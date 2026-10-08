"""AI 사용량 예산: 호출마다 예약 → 정산/해제/불명 처리.

- 사용자 예산과 서버 전체 예산을 모두 통과해야 예약된다. 예산 행은 FOR UPDATE로 잠가 동시 요청이
  한도를 우회하지 못한다(잠금 순서는 id 오름차순으로 고정해 교착을 피한다).
- 결과가 불명확한 호출은 예약을 해제하지 않고 uncertain으로 남긴다(운영자가 resolve_uncertain으로 정리).
- 모든 함수는 호출자의 Session에서 동작하며 commit은 호출자가 한다.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.ai_settings import get_ai_settings
from app.models.ai_usage import UsageBudget, UsageReservation
from app.schemas.ai import ErrorCode
from app.services.ai.access import AIError
from app.services.ai.lease import CancelRequested, Lease, LeaseLost

ZERO = Decimal(0)


def period_start(now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    return date(now.year, now.month, 1)


def _budget_id(db: Session, *, owner_id: UUID | None, scope: str, period: date, currency: str, limit: Decimal):
    """없으면 만들고 id를 돌려준다(동시 생성은 ON CONFLICT로 한 행만 남는다)."""
    if scope == 'server':
        db.execute(text("""INSERT INTO usage_budgets (scope, period_start, currency, amount_limit)
                           VALUES ('server', :p, :c, :l) ON CONFLICT (period_start, currency) WHERE scope = 'server'
                           DO NOTHING"""), {'p': period, 'c': currency, 'l': limit})
        return db.execute(select(UsageBudget.id).where(
            UsageBudget.scope == 'server', UsageBudget.period_start == period,
            UsageBudget.currency == currency)).scalar_one()
    db.execute(text("""INSERT INTO usage_budgets (scope, owner_user_id, period_start, currency, amount_limit)
                       VALUES ('user', :o, :p, :c, :l)
                       ON CONFLICT (owner_user_id, period_start, currency) DO NOTHING"""),
               {'o': owner_id, 'p': period, 'c': currency, 'l': limit})
    return db.execute(select(UsageBudget.id).where(
        UsageBudget.owner_user_id == owner_id, UsageBudget.period_start == period,
        UsageBudget.currency == currency)).scalar_one()


def _require_lease(db: Session, lease: Lease, now: datetime) -> None:
    """작업 행을 잠그고 처리권·실행 상태·만료·취소를 확인한다. 이 transaction이 끝날 때까지 복구·취소 처리가
    같은 행을 바꾸지 못하므로 확인 직후 처리권이 사라진 채 예약되는 일이 없다."""
    row = db.execute(text("""SELECT status, lease_until FROM ai_jobs WHERE id = :id AND lease_token = :t
                             FOR UPDATE"""), {'id': lease.job_id, 't': lease.token}).one_or_none()
    if row is None or row.status not in ('running', 'cancel_requested') or row.lease_until is None \
            or row.lease_until <= now:
        raise LeaseLost('처리권을 잃은 작업입니다.')
    if row.status == 'cancel_requested':
        raise CancelRequested('취소가 접수된 작업입니다.')


def reserve_attempt(db: Session, lease: Lease, amount: Decimal, *, now: datetime | None = None) -> list[UUID]:
    """호출 직전에 최대 비용을 예약한다. 한도를 넘으면 BUDGET_LIMIT이며 아무것도 예약하지 않는다."""
    amount = Decimal(amount)
    if amount < 0:
        raise ValueError('예약 금액은 0 이상이어야 합니다.')
    settings = get_ai_settings()
    now = now or datetime.now(timezone.utc)
    period = period_start(now)
    _require_lease(db, lease, now)
    # 사용자 key로 호출하므로 비용은 사용자 몫이다. 개인 지출 상한은 항상 적용하고, 서버 공유 예산은
    # 관리자가 AI_SERVER_MONTHLY_BUDGET을 0보다 크게 지정했을 때만 모든 사용량의 합계 상한으로 적용한다.
    ids = [_budget_id(db, owner_id=lease.owner_user_id, scope='user', period=period,
                      currency=settings.budget_currency, limit=settings.user_monthly_budget)]
    if settings.server_monthly_budget > 0:
        ids.append(_budget_id(db, owner_id=None, scope='server', period=period,
                              currency=settings.budget_currency, limit=settings.server_monthly_budget))
    budgets = db.execute(select(UsageBudget).where(UsageBudget.id.in_(ids)).order_by(UsageBudget.id)
                         .with_for_update()).scalars().all()
    for budget in budgets:
        # Zero-fee local execution cannot increase cloud spend; keep its call ledger.
        if amount > 0 and budget.reserved_amount + budget.spent_amount + amount > budget.amount_limit:
            raise AIError(ErrorCode.BUDGET_LIMIT, '사용 예산 한도에 도달했습니다.',
                          details={'scope': budget.scope})
    if db.execute(select(UsageReservation.id).where(UsageReservation.job_id == lease.job_id,
                                                    UsageReservation.attempt_no == lease.attempt_no)).first():
        raise AIError(ErrorCode.INVALID_STATE, '이 시도는 이미 예산을 예약했습니다.')
    created = []
    for budget in budgets:
        reservation = UsageReservation(job_id=lease.job_id, attempt_no=lease.attempt_no, budget_id=budget.id,
                                       reserved_amount=amount, status='reserved')
        db.add(reservation)
        budget.reserved_amount = budget.reserved_amount + amount
        budget.version += 1
        created.append(reservation)
    db.execute(text("UPDATE ai_attempts SET estimated_cost = :a WHERE job_id = :j AND attempt_no = :n"),
               {'a': amount, 'j': lease.job_id, 'n': lease.attempt_no})
    db.flush()
    return [r.id for r in created]


def _open_reservations(db: Session, job_id: UUID, attempt_no: int, status: str = 'reserved'):
    return db.execute(select(UsageReservation).where(
        UsageReservation.job_id == job_id, UsageReservation.attempt_no == attempt_no,
        UsageReservation.status == status).order_by(UsageReservation.budget_id).with_for_update()).scalars().all()


def _locked_budgets(db: Session, reservations):
    ids = sorted({r.budget_id for r in reservations})
    rows = db.execute(select(UsageBudget).where(UsageBudget.id.in_(ids)).order_by(UsageBudget.id)
                      .with_for_update()).scalars().all() if ids else []
    return {b.id: b for b in rows}


def settle_attempt(db: Session, job_id: UUID, attempt_no: int, actual: Decimal, *,
                   input_tokens: int | None = None, output_tokens: int | None = None,
                   provider_reported: bool = False) -> int:
    """호출 결과 비용을 확정한다. 예약보다 많이 나왔어도 실제 금액을 기록한다. 이미 정산된 행은 건드리지 않는다."""
    actual = Decimal(actual)
    if actual < 0:
        raise ValueError('정산 금액은 0 이상이어야 합니다.')
    reservations = _open_reservations(db, job_id, attempt_no)
    budgets = _locked_budgets(db, reservations)
    for r in reservations:
        budget = budgets[r.budget_id]
        budget.reserved_amount = budget.reserved_amount - r.reserved_amount
        budget.spent_amount = budget.spent_amount + actual
        budget.version += 1
        r.status, r.settled_amount = 'settled', actual
    if reservations:
        cost_column = 'provider_reported_cost' if provider_reported else 'estimated_cost'
        db.execute(text(f"""UPDATE ai_attempts SET {cost_column} = :a, input_tokens = COALESCE(:i, input_tokens),
                            output_tokens = COALESCE(:o, output_tokens) WHERE job_id = :j AND attempt_no = :n"""),
                   {'a': actual, 'i': input_tokens, 'o': output_tokens, 'j': job_id, 'n': attempt_no})
    db.flush()
    return len(reservations)


def release_attempt(db: Session, job_id: UUID, attempt_no: int) -> int:
    """호출이 일어나지 않았음이 확실할 때 예약을 해제한다."""
    reservations = _open_reservations(db, job_id, attempt_no)
    budgets = _locked_budgets(db, reservations)
    for r in reservations:
        budget = budgets[r.budget_id]
        budget.reserved_amount = budget.reserved_amount - r.reserved_amount
        budget.version += 1
        r.status = 'released'
    db.flush()
    return len(reservations)


def mark_uncertain(db: Session, job_id: UUID, attempt_no: int, *, error_code: str = 'OUTCOME_UNCERTAIN') -> int:
    """호출 결과를 알 수 없다. 예약은 해제하지 않고 uncertain으로 남겨 예산을 계속 점유한다."""
    reservations = _open_reservations(db, job_id, attempt_no)
    for r in reservations:
        r.status = 'uncertain'
    db.execute(text("""UPDATE ai_attempts SET status = 'uncertain', outcome_uncertain = true, error_code = :e,
                       finished_at = now() WHERE job_id = :j AND attempt_no = :n
                       AND status NOT IN ('succeeded', 'failed', 'cancelled')"""),
               {'e': error_code, 'j': job_id, 'n': attempt_no})
    db.flush()
    return len(reservations)


def resolve_uncertain(db: Session, reservation_id: UUID, settled_amount: Decimal | None = None) -> bool:
    """운영자가 불명 호출을 정리한다. 금액을 주면 정산, 없으면 해제."""
    row = db.execute(select(UsageReservation).where(UsageReservation.id == reservation_id,
                                                    UsageReservation.status == 'uncertain').with_for_update()
                     ).scalar_one_or_none()
    if row is None:
        return False
    budget = db.execute(select(UsageBudget).where(UsageBudget.id == row.budget_id).with_for_update()).scalar_one()
    budget.reserved_amount = budget.reserved_amount - row.reserved_amount
    budget.version += 1
    if settled_amount is None:
        row.status = 'released'
    else:
        budget.spent_amount = budget.spent_amount + Decimal(settled_amount)
        row.status, row.settled_amount = 'settled', Decimal(settled_amount)
    db.flush()
    return True
