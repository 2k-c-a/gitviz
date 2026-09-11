"""결과 조회용 Analytics API (DB read 전용).

수집/분석 모듈이 적재한 데이터를 프론트엔드 차트가 바로 쓸 수 있는 형태로 반환한다.
모든 엔드포인트는 repository_id 기반 범용 — 특정 레포(react)에 고정되지 않는다.
"""
import math
import statistics
from datetime import datetime, timezone

import networkx as nx
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.repository import Repository

router = APIRouter(prefix="/repositories/{repository_id}/analytics", tags=["analytics"])


@router.get("/summary")
def get_summary(repository_id: int, db: Session = Depends(get_db)):
    """헤더 + 지표 카드 4개에 필요한 집계값."""
    repo = db.query(Repository).filter(Repository.id == repository_id).first()
    if not repo:
        return {"error": "Repository not found"}

    row = db.execute(
        text(
            """
            SELECT
                COUNT(*)                                              AS total_commits,
                COUNT(*) FILTER (WHERE committed_at >= now() - interval '30 days')  AS commits_30d,
                COUNT(DISTINCT contributor_id)                       AS total_contributors,
                COUNT(DISTINCT contributor_id) FILTER (
                    WHERE committed_at >= now() - interval '90 days') AS active_contributors_90d,
                ROUND(100.0 * COUNT(*) FILTER (WHERE commit_type = 'bugfix')
                      / NULLIF(COUNT(*), 0), 1)                       AS bugfix_pct
            FROM commits
            WHERE repository_id = :rid
            """
        ),
        {"rid": repository_id},
    ).mappings().first()

    total_releases = db.execute(
        text("SELECT COUNT(*) FROM releases WHERE repository_id = :rid"),
        {"rid": repository_id},
    ).scalar()

    latest_release = db.execute(
        text(
            """
            SELECT tag_name FROM releases
            WHERE repository_id = :rid AND published_at IS NOT NULL
            ORDER BY published_at DESC LIMIT 1
            """
        ),
        {"rid": repository_id},
    ).scalar()

    return {
        "repository": {
            "id": repo.id,
            "name": repo.name,
            "url": repo.url,
            "default_branch": repo.default_branch,
            "last_analyzed_at": repo.last_analyzed_at,
        },
        "total_commits": row["total_commits"],
        "commits_30d": row["commits_30d"],
        "total_contributors": row["total_contributors"],
        "active_contributors_90d": row["active_contributors_90d"],
        "total_releases": total_releases,
        "latest_release": latest_release,
        "bugfix_pct": float(row["bugfix_pct"]) if row["bugfix_pct"] is not None else None,
    }


@router.get("/timeline")
def get_timeline(repository_id: int, months: int = 36, db: Session = Depends(get_db)):
    """월별 커밋 수 · 활성 기여자 수 · 버그픽스 비율. months<=0 이면 전체 기간."""
    since_clause = "" if months <= 0 else "AND committed_at >= now() - make_interval(months => :months)"

    rows = db.execute(
        text(
            f"""
            SELECT
                to_char(date_trunc('month', committed_at), 'YYYY-MM')  AS month,
                COUNT(*)                                               AS commits,
                COUNT(DISTINCT contributor_id)                        AS active_contributors,
                ROUND(100.0 * COUNT(*) FILTER (WHERE commit_type = 'bugfix')
                      / NULLIF(COUNT(*), 0), 1)                        AS bugfix_pct
            FROM commits
            WHERE repository_id = :rid
              AND committed_at IS NOT NULL
              {since_clause}
            GROUP BY 1
            ORDER BY 1
            """
        ),
        {"rid": repository_id, "months": months},
    ).mappings().all()

    return {
        "months": [r["month"] for r in rows],
        "commits": [r["commits"] for r in rows],
        "active_contributors": [r["active_contributors"] for r in rows],
        "bugfix_pct": [float(r["bugfix_pct"]) if r["bugfix_pct"] is not None else 0 for r in rows],
    }


@router.get("/commit-types")
def get_commit_types(repository_id: int, db: Session = Depends(get_db)):
    """커밋 유형 분포(도넛). 비율(%) 포함."""
    rows = db.execute(
        text(
            """
            SELECT commit_type, COUNT(*) AS count
            FROM commits
            WHERE repository_id = :rid
            GROUP BY commit_type
            ORDER BY count DESC
            """
        ),
        {"rid": repository_id},
    ).mappings().all()

    total = sum(r["count"] for r in rows) or 1
    return {
        "total": total,
        "types": [
            {
                "type": r["commit_type"] or "other",
                "count": r["count"],
                "pct": round(100.0 * r["count"] / total, 1),
            }
            for r in rows
        ],
    }


@router.get("/releases")
def get_releases(repository_id: int, limit: int = 5, db: Session = Depends(get_db)):
    """릴리즈 히스토리 (최신순)."""
    rows = db.execute(
        text(
            """
            SELECT tag_name, name, release_type, commit_count, published_at
            FROM releases
            WHERE repository_id = :rid
            ORDER BY published_at DESC NULLS LAST
            LIMIT :limit
            """
        ),
        {"rid": repository_id, "limit": limit},
    ).mappings().all()

    return {"releases": [dict(r) for r in rows]}


@router.get("/contributor-heatmap")
def get_contributor_heatmap(
    repository_id: int, weeks: int = 16, limit: int = 8, db: Session = Depends(get_db)
):
    """기여자별 주간 커밋 활동 히트맵. 상위 커밋 수 기여자 limit명 x 최근 weeks주."""
    rows = db.execute(
        text(
            """
            WITH week_series AS (
                SELECT generate_series(
                    date_trunc('week', now()) - make_interval(weeks => :weeks - 1),
                    date_trunc('week', now()),
                    interval '1 week'
                ) AS week_start
            ),
            top_contributors AS (
                SELECT contributor_id, COUNT(*) AS cnt
                FROM commits
                WHERE repository_id = :rid AND contributor_id IS NOT NULL
                GROUP BY contributor_id
                ORDER BY cnt DESC
                LIMIT :limit
            )
            SELECT
                tc.contributor_id,
                tc.cnt          AS total_count,
                co.display_name,
                co.github_login,
                ws.week_start,
                COUNT(c.id)     AS commit_count
            FROM top_contributors tc
            JOIN contributors co ON co.id = tc.contributor_id
            CROSS JOIN week_series ws
            LEFT JOIN commits c
                ON c.contributor_id = tc.contributor_id
               AND c.repository_id = :rid
               AND date_trunc('week', c.committed_at) = ws.week_start
            GROUP BY tc.contributor_id, tc.cnt, co.display_name, co.github_login, ws.week_start
            ORDER BY tc.cnt DESC, tc.contributor_id, ws.week_start
            """
        ),
        {"rid": repository_id, "weeks": weeks, "limit": limit},
    ).mappings().all()

    weeks_list = sorted({r["week_start"].date().isoformat() for r in rows})

    by_contributor = {}
    order = []
    for r in rows:
        cid = r["contributor_id"]
        if cid not in by_contributor:
            by_contributor[cid] = {
                "id": cid,
                "name": r["github_login"] or r["display_name"] or "익명",
                "counts_by_week": {},
            }
            order.append(cid)
        by_contributor[cid]["counts_by_week"][r["week_start"].date().isoformat()] = r["commit_count"]

    contributors = [
        {
            "id": by_contributor[cid]["id"],
            "name": by_contributor[cid]["name"],
            "counts": [by_contributor[cid]["counts_by_week"].get(w, 0) for w in weeks_list],
        }
        for cid in order
    ]

    return {"weeks": weeks_list, "contributors": contributors}


@router.get("/top-contributors")
def get_top_contributors(repository_id: int, limit: int = 5, db: Session = Depends(get_db)):
    """커밋 수 기준 상위 기여자."""
    rows = db.execute(
        text(
            """
            SELECT
                co.id,
                co.display_name,
                co.github_login,
                COUNT(c.id)        AS commit_count,
                MAX(c.committed_at) AS last_commit_at
            FROM contributors co
            JOIN commits c ON c.contributor_id = co.id
            WHERE co.repository_id = :rid
            GROUP BY co.id, co.display_name, co.github_login
            ORDER BY commit_count DESC
            LIMIT :limit
            """
        ),
        {"rid": repository_id, "limit": limit},
    ).mappings().all()

    return {"contributors": [dict(r) for r in rows]}


def _ownership_analysis(owners: list[dict]) -> dict:
    """파일 하나의 기여자 목록([{contributor_id, name, cnt}, ...])에서 Ownership Entropy·
    Bus Factor·기여자별 지분(shares, 내림차순)을 한 번에 계산한다. 셋 다 같은 분포에서
    파생되므로 한 번에 계산해 재사용한다(Risk Score용 entropy/BF, Bus Factor 전용
    엔드포인트의 소유권 상세·독점 파일 집계 모두 이 결과를 쓴다)."""
    total = sum(o["cnt"] for o in owners)
    if total == 0 or not owners:
        return {"entropy": 0.0, "bus_factor": None, "shares": []}

    sorted_owners = sorted(owners, key=lambda o: o["cnt"], reverse=True)
    shares = [
        {"contributor_id": o["contributor_id"], "name": o["name"], "share": o["cnt"] / total}
        for o in sorted_owners
    ]

    entropy = -sum(s["share"] * math.log2(s["share"]) for s in shares if s["share"] > 0)

    cum = 0.0
    bus_factor = len(shares)
    for i, s in enumerate(shares, start=1):
        cum += s["share"]
        if cum >= 0.8:
            bus_factor = i
            break

    return {"entropy": entropy, "bus_factor": bus_factor, "shares": shares}


def _fetch_recent_ownership(db, repository_id: int) -> dict[int, list[dict]]:
    """파일별 '최근'(가장 최근 refactor 커밋 이후, 없으면 첫 커밋 이후) 구간의 기여자별
    커밋 수 + 이름. Risk Score의 Entropy/Bus Factor와 Bus Factor 전용 엔드포인트가
    공유하는 단일 소스 — 두 곳에서 같은 파일에 다른 값이 나오는 걸 방지한다."""
    rows = db.execute(
        text(
            """
            WITH file_windows AS (
                SELECT cfc.file_id,
                       MIN(c.committed_at) AS first_commit_at,
                       MAX(c.committed_at) FILTER (WHERE c.commit_type = 'refactor') AS last_refactor_at
                FROM commit_file_changes cfc
                JOIN commits c ON c.id = cfc.commit_id
                WHERE c.repository_id = :rid
                GROUP BY cfc.file_id
            )
            SELECT cfc.file_id, c.contributor_id, co.display_name, co.github_login,
                   COUNT(DISTINCT c.id) AS cnt
            FROM commit_file_changes cfc
            JOIN commits c ON c.id = cfc.commit_id
            JOIN contributors co ON co.id = c.contributor_id
            JOIN file_windows fw ON fw.file_id = cfc.file_id
            WHERE c.repository_id = :rid AND c.contributor_id IS NOT NULL
              AND c.committed_at >= COALESCE(fw.last_refactor_at, fw.first_commit_at)
            GROUP BY cfc.file_id, c.contributor_id, co.display_name, co.github_login
            """
        ),
        {"rid": repository_id},
    ).mappings().all()

    by_file: dict[int, list[dict]] = {}
    for r in rows:
        by_file.setdefault(r["file_id"], []).append({
            "contributor_id": r["contributor_id"],
            "name": r["github_login"] or r["display_name"] or "익명",
            "cnt": r["cnt"],
        })
    return by_file


def _minmax_norm(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {k: 0.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


_MIN_MONTHS = 1.0  # 관측 기간이 너무 짧으면(리팩토링 직후 등) rate가 튀는 것을 막는 하한


def _months_between(start, end) -> float:
    if start is None:
        return _MIN_MONTHS
    days = (end - start).total_seconds() / 86400
    return max(_MIN_MONTHS, days / 30.44)


def _compute_risk_scores(db: Session, repository_id: int) -> list[dict]:
    """파일별 Risk Score = 0.5·CurrentRisk + 0.5·StructuralRisk. 레포 전체 파일에 대해
    (정렬·limit 없이) 계산한 결과를 그대로 반환한다 — /risk-scores와 /tree가 공유하는
    핵심 로직이라, 정렬·limit·summary 조립은 호출부(엔드포인트)에서 각자 한다.

    정의(결함/유지보수 위험도)는 그대로 두되, 산식은 두 축으로 나눈다.

    CurrentRisk — "리팩토링(있었다면) 이후로 다시 문제가 쌓이고 있는가".
    이 파일의 가장 최근 refactor 커밋 시점(없으면 첫 커밋 시점) 이후 데이터만으로
    5개 지표를 계산한다. 그래야 오래전에 끝난 리팩토링의 diff가 영원히 churn/변경빈도에
    남아 파일을 계속 고위험으로 잡아두는 문제가 없다 — 그 스파이크는애초에 "최근" 구간
    밖에 있으므로 시간이 지나길 기다릴 필요 없이 바로 반영된다.
        CurrentRisk = 0.2·norm(변경빈도_rate) + 0.2·norm(churn_rate) + 0.2·norm(Entropy)
                    + 0.2·norm(버그비율) + 0.2·norm(Co-change)

    StructuralRisk — "이 파일이 역사적으로 얼마나 말썽이었는가" (전체 기간 기준).
    반복적으로 리팩토링해야 했던 파일(=구조가 계속 불안정한 파일)은 CurrentRisk만으로는
    보이지 않는다 — 리팩토링 직후엔 항상 "최근" 구간이 깨끗하게 리셋되기 때문. 이 축이
    그 역사를 담는다.
        StructuralRisk = (1/3)·norm(refactor_rate) + (1/3)·norm(bugfix_rate_전체)
                        + (1/3)·norm(churn_rate_전체)

    변경빈도·churn·refactor·버그수정 "횟수"는 관측 기간이 파일마다 다르므로(오래전에
    리팩토링된 파일 vs 어제 리팩토링된 파일) 그대로 비교하면 안 된다 — 월 단위 rate로
    바꿔서 비교한다. Entropy·버그비율은 원래부터 비율이라 rate 변환이 필요 없다.

    Bus Factor는 여전히 이 산식에 포함하지 않는다. Bus Factor와 Ownership Entropy는
    같은 파일별 기여 비율 분포에서 파생되는 지표라(소유권이 집중될수록 BF·Entropy 둘 다
    낮아짐) 값 자체는 강하게 상관되지만 "위험"의 방향은 반대다 — Entropy가 낮다(집중)는
    건 결함 위험이 낮다는 뜻이고[Bird et al., 2011], BF가 낮다(집중)는 건 승계 위험이
    높다는 뜻이다. 같은 산식에 함께 넣으면 같은 원인(소유권 집중)에 대해 서로 반대
    부호로 상쇄된다. Bus Factor는 별도 승계 리스크로 분리해 고위험 파일에 교차 배지
    (is_bus_factor_risk)로만 표시하며, 이 값도 CurrentRisk와 같은 "최근" 구간 기준으로
    계산해 리팩토링 이후 실제 소유자가 누구인지를 반영한다.
    """
    now = datetime.now(timezone.utc)

    # 1) 파일별 관측 구간(첫 커밋 시점 · 가장 최근 refactor 시점) + 전체 기간 원시 지표
    rows = db.execute(
        text(
            """
            WITH file_windows AS (
                SELECT cfc.file_id,
                       MIN(c.committed_at) AS first_commit_at,
                       MAX(c.committed_at) FILTER (WHERE c.commit_type = 'refactor') AS last_refactor_at
                FROM commit_file_changes cfc
                JOIN commits c ON c.id = cfc.commit_id
                WHERE c.repository_id = :rid
                GROUP BY cfc.file_id
            ),
            alltime AS (
                SELECT cfc.file_id,
                       COUNT(DISTINCT cfc.commit_id)                                          AS change_freq_alltime,
                       COALESCE(SUM(cfc.additions + cfc.deletions), 0)                        AS churn_alltime,
                       COUNT(DISTINCT cfc.commit_id) FILTER (WHERE c.commit_type = 'bugfix')  AS bugfix_alltime,
                       COUNT(DISTINCT cfc.commit_id) FILTER (WHERE c.commit_type = 'refactor') AS refactor_alltime
                FROM commit_file_changes cfc
                JOIN commits c ON c.id = cfc.commit_id
                WHERE c.repository_id = :rid
                GROUP BY cfc.file_id
            ),
            recent AS (
                SELECT cfc.file_id,
                       COUNT(DISTINCT cfc.commit_id)                                         AS change_freq_recent,
                       COALESCE(SUM(cfc.additions + cfc.deletions), 0)                       AS churn_recent,
                       COUNT(DISTINCT cfc.commit_id) FILTER (WHERE c.commit_type = 'bugfix') AS bugfix_recent
                FROM commit_file_changes cfc
                JOIN commits c ON c.id = cfc.commit_id
                JOIN file_windows fw ON fw.file_id = cfc.file_id
                WHERE c.repository_id = :rid
                  AND c.committed_at >= COALESCE(fw.last_refactor_at, fw.first_commit_at)
                GROUP BY cfc.file_id
            )
            SELECT
                fw.file_id, f.path, fw.first_commit_at, fw.last_refactor_at,
                a.change_freq_alltime, a.churn_alltime, a.bugfix_alltime, a.refactor_alltime,
                COALESCE(r.change_freq_recent, 0) AS change_freq_recent,
                COALESCE(r.churn_recent, 0)       AS churn_recent,
                COALESCE(r.bugfix_recent, 0)      AS bugfix_recent
            FROM file_windows fw
            JOIN files f ON f.id = fw.file_id
            JOIN alltime a ON a.file_id = fw.file_id
            LEFT JOIN recent r ON r.file_id = fw.file_id
            """
        ),
        {"rid": repository_id},
    ).mappings().all()

    if not rows:
        return []

    # 2) 파일별 "최근"(리팩토링 이후, 없으면 첫 커밋 이후) 구간 기여자 분포 —
    #    Entropy + Bus Factor에 사용. Bus Factor 전용 엔드포인트와 동일한 소스를 공유한다.
    ownership_by_file = _fetch_recent_ownership(db, repository_id)

    # 3) Co-change 집계 (MVP — 대규모 커밋 제외 + threshold pruning만 적용한 단순화 버전.
    #    PR 단위 분석·IDF 보정은 코드영향도(coupling.html) 전용 기능으로 별도 구현 예정.
    #    구조적 관계 스냅샷이라 "최근" 윈도우로 나누지 않고 기존 방식 그대로 유지한다.)
    coupling_rows = db.execute(
        text(
            """
            WITH file_changes AS (
                SELECT cfc.commit_id, cfc.file_id
                FROM commit_file_changes cfc
                JOIN commits c ON c.id = cfc.commit_id
                WHERE c.repository_id = :rid AND COALESCE(c.is_large_commit, false) = false
            ),
            file_totals AS (
                SELECT file_id, COUNT(DISTINCT commit_id) AS total_changes
                FROM file_changes
                GROUP BY file_id
            ),
            pairs AS (
                SELECT a.file_id AS file_a, b.file_id AS file_b,
                       COUNT(DISTINCT a.commit_id) AS co_count
                FROM file_changes a
                JOIN file_changes b ON a.commit_id = b.commit_id AND a.file_id <> b.file_id
                GROUP BY a.file_id, b.file_id
                HAVING COUNT(DISTINCT a.commit_id) >= 2
            )
            SELECT p.file_a AS file_id,
                   AVG(p.co_count::float / LEAST(fa.total_changes, fb.total_changes)) AS co_change_avg
            FROM pairs p
            JOIN file_totals fa ON fa.file_id = p.file_a
            JOIN file_totals fb ON fb.file_id = p.file_b
            GROUP BY p.file_a
            """
        ),
        {"rid": repository_id},
    ).mappings().all()
    co_change_by_file = {r["file_id"]: float(r["co_change_avg"]) for r in coupling_rows}

    # 4) 파일별 원시 지표 조립 (CurrentRisk 원시값 + StructuralRisk 원시값)
    raw: dict[int, dict] = {}
    for r in rows:
        fid = r["file_id"]
        first_commit_at = r["first_commit_at"]
        last_refactor_at = r["last_refactor_at"]
        recent_start = last_refactor_at or first_commit_at

        months_alltime = _months_between(first_commit_at, now)
        months_recent = _months_between(recent_start, now)

        change_freq_recent = r["change_freq_recent"]
        churn_recent = float(r["churn_recent"])
        bugfix_recent = r["bugfix_recent"]

        oa = _ownership_analysis(ownership_by_file.get(fid, []))
        entropy, bus_factor = oa["entropy"], oa["bus_factor"]

        raw[fid] = {
            "file_id": fid,
            "path": r["path"],
            "last_refactor_at": last_refactor_at.isoformat() if last_refactor_at else None,
            # CurrentRisk 원시값 (리팩토링 이후 구간, rate = 개수 / 관측 개월수)
            "change_freq": change_freq_recent,
            "change_freq_rate": change_freq_recent / months_recent,
            "churn": churn_recent,
            "churn_rate": churn_recent / months_recent,
            "entropy": entropy,
            "bug_ratio": (bugfix_recent / change_freq_recent) if change_freq_recent else 0.0,
            "co_change": co_change_by_file.get(fid, 0.0),
            "bus_factor": bus_factor,
            # StructuralRisk 원시값 (전체 기간, rate = 개수 / 파일 생애 개월수)
            "refactor_rate": r["refactor_alltime"] / months_alltime,
            "bugfix_rate_alltime": r["bugfix_alltime"] / months_alltime,
            "longterm_churn_rate": float(r["churn_alltime"]) / months_alltime,
        }

    # 5) Min-Max 정규화
    def norm(key):
        return _minmax_norm({k: v[key] for k, v in raw.items()})

    n_freq_rate = norm("change_freq_rate")
    n_churn_rate = norm("churn_rate")
    n_entropy = norm("entropy")
    n_bug = norm("bug_ratio")
    n_co = norm("co_change")
    n_refactor_rate = norm("refactor_rate")
    n_bugfix_rate = norm("bugfix_rate_alltime")
    n_longterm_churn = norm("longterm_churn_rate")

    files = []
    for fid, v in raw.items():
        current_risk = (
            0.2 * n_freq_rate.get(fid, 0.0)
            + 0.2 * n_churn_rate.get(fid, 0.0)
            + 0.2 * n_entropy.get(fid, 0.0)
            + 0.2 * n_bug.get(fid, 0.0)
            + 0.2 * n_co.get(fid, 0.0)
        )
        structural_risk = (
            (1 / 3) * n_refactor_rate.get(fid, 0.0)
            + (1 / 3) * n_bugfix_rate.get(fid, 0.0)
            + (1 / 3) * n_longterm_churn.get(fid, 0.0)
        )
        risk = 0.5 * current_risk + 0.5 * structural_risk
        files.append({
            **v,
            "current_risk": round(current_risk, 4),
            "structural_risk": round(structural_risk, 4),
            "risk_score": round(risk, 4),
            "is_bus_factor_risk": v["bus_factor"] == 1,
        })

    return files


@router.get("/risk-scores")
def get_risk_scores(repository_id: int, limit: int = 50, db: Session = Depends(get_db)):
    """레포 전체 파일의 Risk Score를 계산해(_compute_risk_scores) 상위 limit개만 반환한다.
    산식 설명은 _compute_risk_scores 문서 참고."""
    files = _compute_risk_scores(db, repository_id)
    if not files:
        return {"files": [], "summary": {"high_risk_count": 0, "avg_risk_score": None,
                                          "hidden_coupling_count": 0, "avg_entropy": None}}

    files.sort(key=lambda x: x["risk_score"], reverse=True)
    top_files = files[:limit]

    entropies = [f["entropy"] for f in files]
    summary = {
        "high_risk_count": sum(1 for f in files if f["risk_score"] >= 0.7),
        "avg_risk_score": round(sum(f["risk_score"] for f in files) / len(files), 4) if files else None,
        "hidden_coupling_count": sum(1 for f in files if f["co_change"] >= 0.3),
        "avg_entropy": round(sum(entropies) / len(entropies), 4) if entropies else None,
    }

    return {"files": top_files, "summary": summary}


def _module_of(path: str) -> str:
    """파일 경로에서 모듈(디렉토리 상위 2단계)을 뽑는다. 최상위 파일은 (root)."""
    parts = path.split("/")
    if len(parts) <= 1:
        return "(root)"
    return "/".join(parts[:2])


@router.get("/bus-factor")
def get_bus_factor_analysis(repository_id: int, limit: int = 50, db: Session = Depends(get_db)):
    """Bus Factor 전용 분석 — busfactor.html 데이터 계약(HANDOFF.md 4-4) 대응.

    Risk Score 배지에 쓰는 BF/Entropy와 동일한 소스(_fetch_recent_ownership — 가장 최근
    refactor 커밋 이후, 없으면 첫 커밋 이후 구간)를 재사용해 두 화면의 값이 어긋나지
    않게 한다. 기여자 식별(Identity Resolution)이 끝나지 않아 contributor_id가 없는
    파일은 BF를 정의할 수 없으므로 집계에서 제외한다.
    """
    file_rows = db.execute(
        text(
            "SELECT id AS file_id, path FROM files "
            "WHERE repository_id = :rid AND COALESCE(is_deleted, false) = false"
        ),
        {"rid": repository_id},
    ).mappings().all()

    empty = {
        "summary": {"project_bus_factor": None, "high_risk_file_count": 0,
                    "avg_entropy": None, "risky_module_count": 0},
        "distribution": [], "top_owners": [], "risky_modules": [], "files": [],
    }
    if not file_rows:
        return empty

    paths_by_file = {r["file_id"]: r["path"] for r in file_rows}
    ownership_by_file = _fetch_recent_ownership(db, repository_id)

    analysis_by_file: dict[int, dict] = {}
    for fid, path in paths_by_file.items():
        oa = _ownership_analysis(ownership_by_file.get(fid, []))
        if oa["bus_factor"] is None:
            continue
        analysis_by_file[fid] = {"path": path, **oa}

    if not analysis_by_file:
        return empty

    # 1) BF 분포 (5 이상은 "5+"로 묶음)
    dist_counter: dict[int, int] = {}
    for v in analysis_by_file.values():
        bucket = min(v["bus_factor"], 5)
        dist_counter[bucket] = dist_counter.get(bucket, 0) + 1
    distribution = [
        {"bus_factor": (str(k) if k < 5 else "5+"), "file_count": dist_counter[k]}
        for k in sorted(dist_counter)
    ]

    # 2) 기여자별 독점 파일 수 — BF=1인 파일에서 혼자 80%를 넘긴 그 기여자
    exclusive_counter: dict[int, dict] = {}
    for v in analysis_by_file.values():
        if v["bus_factor"] == 1 and v["shares"]:
            owner = v["shares"][0]
            cid = owner["contributor_id"]
            entry = exclusive_counter.setdefault(
                cid, {"contributor_id": cid, "name": owner["name"], "exclusive_file_count": 0}
            )
            entry["exclusive_file_count"] += 1
    top_owners = sorted(
        exclusive_counter.values(), key=lambda o: o["exclusive_file_count"], reverse=True
    )[:limit]

    # 3) 위험 모듈 — 디렉토리 상위 2단계 기준, BF=1 비율 내림차순
    module_stats: dict[str, dict] = {}
    for v in analysis_by_file.values():
        mod = _module_of(v["path"])
        s = module_stats.setdefault(mod, {"module": mod, "file_count": 0, "bf1_count": 0})
        s["file_count"] += 1
        if v["bus_factor"] == 1:
            s["bf1_count"] += 1
    for s in module_stats.values():
        s["bf1_ratio"] = round(s["bf1_count"] / s["file_count"], 4)
    risky_modules = sorted(
        module_stats.values(), key=lambda s: (s["bf1_ratio"], s["bf1_count"]), reverse=True
    )[:limit]

    # 4) 파일 소유권 상세 — BF 오름차순(가장 위험한 파일 먼저) 상위 limit개
    files_detail = [
        {
            "file_id": fid,
            "path": v["path"],
            "bus_factor": v["bus_factor"],
            "entropy": round(v["entropy"], 4),
            "owners": [
                {"contributor_id": s["contributor_id"], "name": s["name"], "share": round(s["share"], 4)}
                for s in v["shares"]
            ],
        }
        for fid, v in analysis_by_file.items()
    ]
    files_detail.sort(key=lambda f: (f["bus_factor"], -f["entropy"]))
    files_detail = files_detail[:limit]

    # 5) 요약
    bf_values = sorted(v["bus_factor"] for v in analysis_by_file.values())
    entropies = [v["entropy"] for v in analysis_by_file.values()]
    summary = {
        "project_bus_factor": statistics.median(bf_values),
        "high_risk_file_count": sum(1 for v in analysis_by_file.values() if v["bus_factor"] == 1),
        "avg_entropy": round(sum(entropies) / len(entropies), 4) if entropies else None,
        "risky_module_count": sum(1 for s in module_stats.values() if s["bf1_ratio"] >= 0.3),
    }

    return {
        "summary": summary,
        "distribution": distribution,
        "top_owners": top_owners,
        "risky_modules": risky_modules,
        "files": files_detail,
    }


_PR_PAIRS_SQL = """
    WITH file_changes AS (
        SELECT prf.pull_request_id AS unit_id, prf.file_id
        FROM pull_request_files prf
        JOIN pull_requests pr ON pr.id = prf.pull_request_id
        WHERE pr.repository_id = :rid AND COALESCE(pr.is_large_pr, false) = false
          AND prf.file_id IS NOT NULL
    ),
    file_totals AS (
        SELECT file_id, COUNT(DISTINCT unit_id) AS total_units
        FROM file_changes GROUP BY file_id
    ),
    pairs AS (
        SELECT a.file_id AS file_a, b.file_id AS file_b, COUNT(DISTINCT a.unit_id) AS co_count
        FROM file_changes a
        JOIN file_changes b ON a.unit_id = b.unit_id AND a.file_id < b.file_id
        GROUP BY a.file_id, b.file_id
        HAVING COUNT(DISTINCT a.unit_id) >= 2
    )
    SELECT p.file_a, p.file_b, p.co_count, fa.total_units AS total_a, fb.total_units AS total_b
    FROM pairs p
    JOIN file_totals fa ON fa.file_id = p.file_a
    JOIN file_totals fb ON fb.file_id = p.file_b
"""

_COMMIT_PAIRS_SQL = """
    WITH file_changes AS (
        SELECT cfc.commit_id AS unit_id, cfc.file_id
        FROM commit_file_changes cfc
        JOIN commits c ON c.id = cfc.commit_id
        WHERE c.repository_id = :rid AND COALESCE(c.is_large_commit, false) = false
    ),
    file_totals AS (
        SELECT file_id, COUNT(DISTINCT unit_id) AS total_units
        FROM file_changes GROUP BY file_id
    ),
    pairs AS (
        SELECT a.file_id AS file_a, b.file_id AS file_b, COUNT(DISTINCT a.unit_id) AS co_count
        FROM file_changes a
        JOIN file_changes b ON a.unit_id = b.unit_id AND a.file_id < b.file_id
        GROUP BY a.file_id, b.file_id
        HAVING COUNT(DISTINCT a.unit_id) >= 2
    )
    SELECT p.file_a, p.file_b, p.co_count, fa.total_units AS total_a, fb.total_units AS total_b
    FROM pairs p
    JOIN file_totals fa ON fa.file_id = p.file_a
    JOIN file_totals fb ON fb.file_id = p.file_b
"""


def _compute_coupling(db, repository_id: int) -> dict:
    """PR 단위(없으면 커밋 단위 fallback) co-change 쌍 + IDF 보정.

    paper 설계 그대로: PR은 논리적 작업 단위라 신뢰도가 높아 기본으로 쓰고, PR이
    하나도 없는 레포(직접 push만 하는 레포)는 커밋 단위로 대체한다. 대규모
    PR/커밋(파일 10% 또는 15개 이상)은 이미 수집 단계에서 계산된 is_large_pr /
    is_large_commit 플래그로 제외 — 리팩토링·자동화성 대량 변경의 노이즈를 막는다.

    coupling_score(A,B) = co_count(A,B) / min(total_A, total_B)   — [0,1] 범위
    idf(f)              = log(total_units / units_containing_f)
    adjusted_score(A,B)  = coupling_score(A,B) * idf(A) * idf(B)   — utils.py처럼
    전역적으로 자주 바뀌는 파일의 false positive를 억제. idf는 배율이라
    adjusted_score는 1을 넘을 수 있다 — "강결합(≥0.7)" 판정은 배율 영향이 없는
    coupling_score 기준, 정렬은 신호 대 잡음비가 더 좋은 adjusted_score 기준.

    한계: import 그래프(Python AST 분석)가 아직 없어 "import 관계는 없는데
    coupling만 높다"는 진짜 Hidden Coupling 검증은 불가능하다. 여기서 나가는
    coupling/adjusted score는 그 후보 신호일 뿐, 확정 판정이 아니다.
    """
    pr_count = db.execute(
        text("SELECT COUNT(*) FROM pull_requests WHERE repository_id = :rid"),
        {"rid": repository_id},
    ).scalar()
    unit = "pr" if pr_count else "commit"

    if unit == "pr":
        pair_sql = _PR_PAIRS_SQL
        total_units = db.execute(
            text("""
                SELECT COUNT(DISTINCT prf.pull_request_id)
                FROM pull_request_files prf
                JOIN pull_requests pr ON pr.id = prf.pull_request_id
                WHERE pr.repository_id = :rid AND COALESCE(pr.is_large_pr, false) = false
            """),
            {"rid": repository_id},
        ).scalar() or 0
        excluded_units = db.execute(
            text("SELECT COUNT(*) FROM pull_requests WHERE repository_id = :rid AND is_large_pr = true"),
            {"rid": repository_id},
        ).scalar() or 0
    else:
        pair_sql = _COMMIT_PAIRS_SQL
        total_units = db.execute(
            text("""
                SELECT COUNT(DISTINCT cfc.commit_id)
                FROM commit_file_changes cfc
                JOIN commits c ON c.id = cfc.commit_id
                WHERE c.repository_id = :rid AND COALESCE(c.is_large_commit, false) = false
            """),
            {"rid": repository_id},
        ).scalar() or 0
        excluded_units = db.execute(
            text("SELECT COUNT(*) FROM commits WHERE repository_id = :rid AND is_large_commit = true"),
            {"rid": repository_id},
        ).scalar() or 0

    rows = db.execute(text(pair_sql), {"rid": repository_id}).mappings().all()

    paths = {
        r["id"]: r["path"]
        for r in db.execute(
            text("SELECT id, path FROM files WHERE repository_id = :rid"), {"rid": repository_id}
        ).mappings().all()
    }

    def idf(count: int) -> float:
        if not total_units or not count:
            return 0.0
        return math.log(total_units / count) if count < total_units else 0.0

    pairs = []
    for r in rows:
        coupling_score = r["co_count"] / min(r["total_a"], r["total_b"])
        adjusted_score = coupling_score * idf(r["total_a"]) * idf(r["total_b"])
        pairs.append({
            "file_a_id": r["file_a"], "file_a_path": paths.get(r["file_a"], "?"),
            "file_b_id": r["file_b"], "file_b_path": paths.get(r["file_b"], "?"),
            "co_change_count": r["co_count"],
            "coupling_score": round(coupling_score, 4),
            "adjusted_score": round(adjusted_score, 4),
        })
    pairs.sort(key=lambda p: p["adjusted_score"], reverse=True)

    return {
        "co_change_unit": unit,
        "total_units": total_units,
        "excluded_unit_count": excluded_units,
        "pairs": pairs,
    }


def _per_file_coupling_summary(coupling_result: dict) -> dict[int, dict]:
    """_compute_coupling() 결과를 파일 하나당 스칼라 두 개로 압축한다 (트리 뷰처럼 파일별로
    "영향도 순" 정렬·색상 인코딩을 해야 하는데 쌍(pair) 단위 리스트는 그대로 쓸 수 없는 곳용).

        max_coupling_score  — 이 파일과 가장 강하게 결합된 파트너의 coupling_score([0,1])
        coupled_file_count  — coupling_score >= 0.7(강결합 기준, /coupling의 high_coupling_count와 동일 임계값)인 파트너 수
    """
    summary: dict[int, dict] = {}
    for p in coupling_result["pairs"]:
        for fid in (p["file_a_id"], p["file_b_id"]):
            s = summary.setdefault(fid, {"max_coupling_score": 0.0, "coupled_file_count": 0})
            s["max_coupling_score"] = max(s["max_coupling_score"], p["coupling_score"])
            if p["coupling_score"] >= 0.7:
                s["coupled_file_count"] += 1
    return summary


@router.get("/coupling")
def get_coupling(repository_id: int, limit: int = 100, min_score: float = 0.0, db: Session = Depends(get_db)):
    """레포 전체 파일 결합도(Co-change) 목록. adjusted_score 내림차순."""
    result = _compute_coupling(db, repository_id)
    pairs = [p for p in result["pairs"] if p["coupling_score"] >= min_score]

    meta = {
        "co_change_unit": result["co_change_unit"],
        "total_units": result["total_units"],
        "excluded_unit_count": result["excluded_unit_count"],
        "total_pairs": len(result["pairs"]),
        "high_coupling_count": sum(1 for p in result["pairs"] if p["coupling_score"] >= 0.7),
        "avg_adjusted_score": round(sum(p["adjusted_score"] for p in result["pairs"]) / len(result["pairs"]), 4)
        if result["pairs"] else None,
    }
    return {"meta": meta, "pairs": pairs[:limit]}


def _coupled_files_for(result: dict, file_id: int) -> list[dict]:
    """_compute_coupling() 결과에서 file_id가 걸린 쌍만 뽑아 상대 파일 기준으로 펼치고
    adjusted_score 내림차순 정렬한다. /coupling/{file_id}와 파일 상세 팝업(files/{file_id}/detail)
    양쪽에서 공유하는 로직 — 계산 결과가 어긋나지 않게 한 곳에 모아둔다."""
    coupled = []
    for p in result["pairs"]:
        if p["file_a_id"] == file_id:
            coupled.append({"file_id": p["file_b_id"], "path": p["file_b_path"],
                             "co_change_count": p["co_change_count"],
                             "coupling_score": p["coupling_score"], "adjusted_score": p["adjusted_score"]})
        elif p["file_b_id"] == file_id:
            coupled.append({"file_id": p["file_a_id"], "path": p["file_a_path"],
                             "co_change_count": p["co_change_count"],
                             "coupling_score": p["coupling_score"], "adjusted_score": p["adjusted_score"]})
    coupled.sort(key=lambda c: c["adjusted_score"], reverse=True)
    return coupled


@router.get("/coupling/{file_id}")
def get_file_coupling(repository_id: int, file_id: int, limit: int = 20, db: Session = Depends(get_db)):
    """특정 파일과 함께 자주 변경되는 파일 목록 (영향도 그래프 drill-down용)."""
    result = _compute_coupling(db, repository_id)

    file_row = db.execute(
        text("SELECT id, path FROM files WHERE repository_id = :rid AND id = :fid"),
        {"rid": repository_id, "fid": file_id},
    ).mappings().first()
    if not file_row:
        return {"error": "File not found"}

    coupled = _coupled_files_for(result, file_id)
    return {
        "file": {"id": file_row["id"], "path": file_row["path"]},
        "co_change_unit": result["co_change_unit"],
        "coupled_files": coupled[:limit],
    }


def _fetch_network_edges(db, repository_id: int) -> dict:
    """co_edit(무방향, PR+파일 공동수정)과 review(유방향, 리뷰어→작성자)를 결합한
    방향성 협업 엣지를 계산한다 (paper 설계 그대로).

        edge_weight(A→B) = 0.6 * (co_edit_raw / 2) + 0.4 * review_directed(A→B)

    co_edit는 무방향 신호라 양방향(A→B, B→A)에 절반씩 똑같이 배분해서 단방향
    엣지가 왜곡되지 않게 한다. review는 원래부터 방향이 있어(누가 누구 PR을
    리뷰했는지) 그대로 한쪽에만 더한다.

    한계: co_edit는 PR·PR파일·PR커밋 데이터가 있어야 계산되므로 PR이 없는
    레포(직접 push만 하는 레포)는 co_edit가 전부 0이 된다 — Co-change와 달리
    커밋 단위 fallback은 두지 않았다(커밋 하나엔 작성자가 한 명뿐이라 "같은
    커밋을 함께 수정"이 의미가 없기 때문). review는 PR 존재 여부와 무관하게
    계산된다.
    """
    total_prs = db.execute(
        text(
            "SELECT COUNT(*) FROM pull_requests "
            "WHERE repository_id = :rid AND COALESCE(is_large_pr, false) = false"
        ),
        {"rid": repository_id},
    ).scalar() or 0

    co_edit_rows = []
    if total_prs:
        co_edit_rows = db.execute(
            text(
                """
                WITH pr_file_contributors AS (
                    SELECT DISTINCT prf.pull_request_id, prf.file_id, c.contributor_id
                    FROM pull_request_files prf
                    JOIN pull_requests pr ON pr.id = prf.pull_request_id
                    JOIN pr_commits pc ON pc.pull_request_id = prf.pull_request_id
                    JOIN commits c ON c.id = pc.commit_id
                    WHERE pr.repository_id = :rid AND COALESCE(pr.is_large_pr, false) = false
                      AND c.contributor_id IS NOT NULL
                )
                SELECT a.contributor_id AS contributor_a, b.contributor_id AS contributor_b,
                       COUNT(*) AS co_edit_count
                FROM pr_file_contributors a
                JOIN pr_file_contributors b
                  ON a.pull_request_id = b.pull_request_id AND a.file_id = b.file_id
                 AND a.contributor_id < b.contributor_id
                GROUP BY a.contributor_id, b.contributor_id
                """
            ),
            {"rid": repository_id},
        ).mappings().all()

    total_reviews = db.execute(
        text(
            "SELECT COUNT(*) FROM pr_reviews r JOIN pull_requests pr ON pr.id = r.pull_request_id "
            "WHERE pr.repository_id = :rid"
        ),
        {"rid": repository_id},
    ).scalar() or 0

    review_rows = []
    if total_reviews:
        review_rows = db.execute(
            text(
                """
                SELECT r.reviewer_contributor_id AS reviewer_id, pr.author_contributor_id AS author_id,
                       COUNT(*) AS review_count
                FROM pr_reviews r
                JOIN pull_requests pr ON pr.id = r.pull_request_id
                WHERE pr.repository_id = :rid
                  AND r.reviewer_contributor_id IS NOT NULL AND pr.author_contributor_id IS NOT NULL
                  AND r.reviewer_contributor_id <> pr.author_contributor_id
                GROUP BY r.reviewer_contributor_id, pr.author_contributor_id
                """
            ),
            {"rid": repository_id},
        ).mappings().all()

    raw: dict[tuple, dict] = {}

    def slot(a, b):
        return raw.setdefault((a, b), {"co_edit_raw": 0.0, "review": 0.0})

    for r in co_edit_rows:
        v = r["co_edit_count"] / total_prs
        slot(r["contributor_a"], r["contributor_b"])["co_edit_raw"] = v
        slot(r["contributor_b"], r["contributor_a"])["co_edit_raw"] = v

    for r in review_rows:
        slot(r["reviewer_id"], r["author_id"])["review"] = r["review_count"] / total_reviews

    edges = []
    for (a, b), v in raw.items():
        weight = 0.6 * (v["co_edit_raw"] / 2) + 0.4 * v["review"]
        if weight <= 0:
            continue
        edges.append({
            "source": a, "target": b,
            "co_edit_raw": round(v["co_edit_raw"], 4),
            "review_directed": round(v["review"], 4),
            "edge_weight": round(weight, 4),
        })

    return {"edges": edges, "total_prs": total_prs, "total_reviews": total_reviews}


def _compute_network_metrics(edges: list[dict], contributor_ids: list[int]) -> dict:
    """Betweenness Centrality(방향성 그래프) + Louvain 군집(무방향 그래프로 변환해 계산)."""
    G = nx.DiGraph()
    G.add_nodes_from(contributor_ids)
    for e in edges:
        G.add_edge(e["source"], e["target"], weight=e["edge_weight"])

    betweenness = nx.betweenness_centrality(G, weight="weight") if G.number_of_edges() else {n: 0.0 for n in G.nodes}

    UG = nx.Graph()
    UG.add_nodes_from(contributor_ids)
    for e in edges:
        a, b, w = e["source"], e["target"], e["edge_weight"]
        if UG.has_edge(a, b):
            UG[a][b]["weight"] += w
        else:
            UG.add_edge(a, b, weight=w)

    if UG.number_of_edges():
        communities = nx.algorithms.community.louvain_communities(UG, weight="weight", seed=42)
    else:
        communities = [{n} for n in UG.nodes]

    cluster_of = {}
    for idx, comm in enumerate(communities):
        for n in comm:
            cluster_of[n] = idx

    cluster_sizes: dict[int, int] = {}
    for cid in cluster_of.values():
        cluster_sizes[cid] = cluster_sizes.get(cid, 0) + 1
    max_cluster_id = max(cluster_sizes, key=cluster_sizes.get) if cluster_sizes else None

    isolated = sum(1 for n in contributor_ids if G.degree(n) == 0)
    total = len(contributor_ids)

    return {
        "betweenness": betweenness,
        "cluster_of": cluster_of,
        "cluster_count": len(communities),
        "isolated_contributor_count": isolated,
        "isolated_ratio": round(isolated / total, 4) if total else None,
        "network_density": round(nx.density(G), 4) if total > 1 else 0.0,
        "max_cluster_size": max(cluster_sizes.values()) if cluster_sizes else 0,
        "max_cluster_label": f"cluster-{max_cluster_id}" if max_cluster_id is not None else None,
    }


@router.get("/network")
def get_network(
    repository_id: int, limit_nodes: int = 50, min_edge_weight: float = 0.0, db: Session = Depends(get_db)
):
    """협업 그래프 (방향성) — 노드 크기는 커밋 수, 색은 Louvain 군집."""
    contributors = [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT co.id, co.display_name, co.github_login, COUNT(c.id) AS commit_count
                FROM contributors co
                LEFT JOIN commits c ON c.contributor_id = co.id AND c.repository_id = co.repository_id
                WHERE co.repository_id = :rid
                GROUP BY co.id, co.display_name, co.github_login
                """
            ),
            {"rid": repository_id},
        ).mappings().all()
    ]

    if not contributors:
        return {
            "nodes": [], "edges": [],
            "metrics": {"total_contributor_count": 0, "total_edge_count": 0, "network_density": None,
                        "cluster_count": 0, "isolated_contributor_count": 0, "isolated_ratio": None,
                        "avg_edge_weight": None, "max_cluster_size": 0, "max_cluster_label": None},
        }

    contributor_ids = [c["id"] for c in contributors]
    edge_data = _fetch_network_edges(db, repository_id)
    metrics_raw = _compute_network_metrics(edge_data["edges"], contributor_ids)

    top_ids = {
        c["id"] for c in sorted(contributors, key=lambda c: c["commit_count"], reverse=True)[:limit_nodes]
    }

    nodes = [
        {
            "id": c["id"],
            "name": c["github_login"] or c["display_name"] or "익명",
            "commit_count": c["commit_count"],
            "betweenness_centrality": round(metrics_raw["betweenness"].get(c["id"], 0.0), 4),
            "cluster": metrics_raw["cluster_of"].get(c["id"]),
        }
        for c in contributors if c["id"] in top_ids
    ]

    edges = sorted(
        (e for e in edge_data["edges"]
         if e["edge_weight"] >= min_edge_weight and e["source"] in top_ids and e["target"] in top_ids),
        key=lambda e: e["edge_weight"], reverse=True,
    )

    all_edges = edge_data["edges"]
    metrics = {
        "total_contributor_count": len(contributors),
        "total_edge_count": len(all_edges),
        "network_density": metrics_raw["network_density"],
        "cluster_count": metrics_raw["cluster_count"],
        "isolated_contributor_count": metrics_raw["isolated_contributor_count"],
        "isolated_ratio": metrics_raw["isolated_ratio"],
        "avg_edge_weight": round(sum(e["edge_weight"] for e in all_edges) / len(all_edges), 4) if all_edges else None,
        "max_cluster_size": metrics_raw["max_cluster_size"],
        "max_cluster_label": metrics_raw["max_cluster_label"],
    }

    return {"nodes": nodes, "edges": edges, "metrics": metrics}


@router.get("/network/top-pairs")
def get_network_top_pairs(repository_id: int, limit: int = 5, db: Session = Depends(get_db)):
    """상위 협업 쌍 — 양방향 edge_weight 합산 기준."""
    contributors = {
        r["id"]: (r["github_login"] or r["display_name"] or "익명")
        for r in db.execute(
            text("SELECT id, display_name, github_login FROM contributors WHERE repository_id = :rid"),
            {"rid": repository_id},
        ).mappings().all()
    }
    edge_data = _fetch_network_edges(db, repository_id)

    pair_map: dict[tuple, dict] = {}
    for e in edge_data["edges"]:
        a, b = e["source"], e["target"]
        key = (a, b) if a < b else (b, a)
        entry = pair_map.setdefault(
            key, {"co_edit_raw": 0.0, "review_a_to_b": 0.0, "review_b_to_a": 0.0, "edge_weight_sum": 0.0}
        )
        entry["co_edit_raw"] = e["co_edit_raw"]
        entry["edge_weight_sum"] += e["edge_weight"]
        if a == key[0]:
            entry["review_a_to_b"] = e["review_directed"]
        else:
            entry["review_b_to_a"] = e["review_directed"]

    pairs = [
        {
            "contributor_a": {"id": a, "name": contributors.get(a, "익명")},
            "contributor_b": {"id": b, "name": contributors.get(b, "익명")},
            "edge_weight": round(v["edge_weight_sum"], 4),
            "co_edit_raw": round(v["co_edit_raw"], 4),
            "review_directed_a_to_b": round(v["review_a_to_b"], 4),
            "review_directed_b_to_a": round(v["review_b_to_a"], 4),
        }
        for (a, b), v in pair_map.items()
    ]
    pairs.sort(key=lambda p: p["edge_weight"], reverse=True)
    return pairs[:limit]


def _contributor_name_row(db, repository_id: int, contributor_id: int):
    return db.execute(
        text("SELECT id, display_name, github_login FROM contributors WHERE repository_id = :rid AND id = :cid"),
        {"rid": repository_id, "cid": contributor_id},
    ).mappings().first()


@router.get("/contributors/{contributor_id}/owned-files")
def get_contributor_owned_files(
    repository_id: int, contributor_id: int, limit: int = 20, db: Session = Depends(get_db)
):
    """특정 기여자의 지분(share) 내림차순 소유 파일 목록 — 타임라인 상위 기여자 클릭 팝업(1번)용.
    Bus Factor 전용 엔드포인트와 동일 소스(_fetch_recent_ownership)를 재사용해 값이 어긋나지 않는다.
    """
    contributor_row = _contributor_name_row(db, repository_id, contributor_id)
    if not contributor_row:
        return {"error": "Contributor not found"}

    paths_by_file = {
        r["id"]: r["path"]
        for r in db.execute(
            text("SELECT id, path FROM files WHERE repository_id = :rid AND COALESCE(is_deleted, false) = false"),
            {"rid": repository_id},
        ).mappings().all()
    }
    ownership_by_file = _fetch_recent_ownership(db, repository_id)

    owned = []
    for fid, owners in ownership_by_file.items():
        if fid not in paths_by_file:
            continue
        oa = _ownership_analysis(owners)
        mine = next((s for s in oa["shares"] if s["contributor_id"] == contributor_id), None)
        if not mine:
            continue
        owned.append({
            "file_id": fid,
            "path": paths_by_file[fid],
            "share": round(mine["share"], 4),
            "bus_factor": oa["bus_factor"],
            "is_bus_factor_risk": oa["bus_factor"] == 1,
        })
    owned.sort(key=lambda f: f["share"], reverse=True)

    return {
        "contributor": {
            "id": contributor_id,
            "name": contributor_row["github_login"] or contributor_row["display_name"] or "익명",
        },
        "owned_file_count": len(owned),
        "files": owned[:limit],
    }


@router.get("/contributors/{contributor_id}/collaborators")
def get_contributor_collaborators(
    repository_id: int, contributor_id: int, limit: int = 20, db: Session = Depends(get_db)
):
    """특정 기여자와 협업 강도(edge_weight)가 높은 순 상대방 목록 — 타임라인 상위 기여자
    클릭 팝업(1번)용. /network가 쓰는 _fetch_network_edges를 그대로 재사용해 한 사람 기준으로
    필터링만 한 것이라 /network/top-pairs 값과 어긋나지 않는다."""
    contributor_row = _contributor_name_row(db, repository_id, contributor_id)
    if not contributor_row:
        return {"error": "Contributor not found"}

    names = {
        r["id"]: (r["github_login"] or r["display_name"] or "익명")
        for r in db.execute(
            text("SELECT id, display_name, github_login FROM contributors WHERE repository_id = :rid"),
            {"rid": repository_id},
        ).mappings().all()
    }
    edge_data = _fetch_network_edges(db, repository_id)

    agg: dict[int, dict] = {}
    for e in edge_data["edges"]:
        if e["source"] == contributor_id:
            other, out_review, in_review = e["target"], e["review_directed"], 0.0
        elif e["target"] == contributor_id:
            other, out_review, in_review = e["source"], 0.0, e["review_directed"]
        else:
            continue
        entry = agg.setdefault(
            other, {"co_edit_raw": 0.0, "review_out": 0.0, "review_in": 0.0, "edge_weight_sum": 0.0}
        )
        # co_edit_raw는 양방향 엣지에 동일하게 배분돼 있어 더하지 않고 그대로 사용
        entry["co_edit_raw"] = e["co_edit_raw"]
        entry["review_out"] += out_review
        entry["review_in"] += in_review
        entry["edge_weight_sum"] += e["edge_weight"]

    collaborators = [
        {
            "contributor": {"id": cid, "name": names.get(cid, "익명")},
            "edge_weight": round(v["edge_weight_sum"], 4),
            "co_edit_raw": round(v["co_edit_raw"], 4),
            "review_directed_out": round(v["review_out"], 4),
            "review_directed_in": round(v["review_in"], 4),
        }
        for cid, v in agg.items()
    ]
    collaborators.sort(key=lambda c: c["edge_weight"], reverse=True)

    return {
        "contributor": {
            "id": contributor_id,
            "name": contributor_row["github_login"] or contributor_row["display_name"] or "익명",
        },
        "collaborators": collaborators[:limit],
    }


@router.get("/files/{file_id}/detail")
def get_file_detail(repository_id: int, file_id: int, limit: int = 20, db: Session = Depends(get_db)):
    """위험도 탭 파일 클릭 팝업(2번)용 — 이 파일을 수정한 사람(editors) + 이 파일이 영향을
    끼칠 수 있는 파일(coupled_files)을 한 번에 묶어서 반환한다. Bus Factor·Coupling과 같은
    소스(_fetch_recent_ownership, _compute_coupling)를 재사용해 값이 어긋나지 않는다."""
    file_row = db.execute(
        text("SELECT id, path FROM files WHERE repository_id = :rid AND id = :fid"),
        {"rid": repository_id, "fid": file_id},
    ).mappings().first()
    if not file_row:
        return {"error": "File not found"}

    ownership_by_file = _fetch_recent_ownership(db, repository_id)
    oa = _ownership_analysis(ownership_by_file.get(file_id, []))

    coupling_result = _compute_coupling(db, repository_id)
    coupled = _coupled_files_for(coupling_result, file_id)

    return {
        "file": {"id": file_row["id"], "path": file_row["path"]},
        "entropy": round(oa["entropy"], 4),
        "bus_factor": oa["bus_factor"],
        "is_bus_factor_risk": oa["bus_factor"] == 1,
        "editors": [
            {"contributor_id": s["contributor_id"], "name": s["name"], "share": round(s["share"], 4)}
            for s in oa["shares"]
        ],
        "co_change_unit": coupling_result["co_change_unit"],
        "coupled_files": coupled[:limit],
    }


def _build_tree(files: list[dict]) -> dict:
    """path 문자열(예: "app/api/analytics.py")들을 "/" 기준으로 중첩 디렉토리 트리로 접는다.
    디렉토리 노드는 {name, children}, 파일(leaf) 노드는 {name, children 없음, ...metrics}.
    프론트 트리맵(treemap.js의 MOCK_TREE)과 동일한 모양(name/children/file_id/value/
    bus_factor/entropy)에 risk_score·coupling_score를 추가한 형태 — 디렉토리 집계(평균 BF 등)는
    지금처럼 프론트에서 계산하는 쪽을 그대로 둔다(트리는 원본 값만 실어준다)."""
    root: dict = {"name": "root", "children": []}
    for f in files:
        parts = f["path"].split("/")
        cur = root
        for part in parts[:-1]:
            nxt = next((c for c in cur["children"] if c["name"] == part and "children" in c), None)
            if nxt is None:
                nxt = {"name": part, "children": []}
                cur["children"].append(nxt)
            cur = nxt
        cur["children"].append({
            "name": parts[-1],
            "file_id": f["file_id"],
            "path": f["path"],
            "value": 1,
            "risk_score": f["risk_score"],
            "bus_factor": f["bus_factor"],
            "entropy": f["entropy"],
            "is_bus_factor_risk": f["is_bus_factor_risk"],
            "coupling_score": f["coupling_score"],
            "coupled_file_count": f["coupled_file_count"],
        })
    return root


@router.get("/tree")
def get_repo_tree(repository_id: int, db: Session = Depends(get_db)):
    """코드분석 탭(트리맵) 데이터 계약 — 디렉토리 트리의 파일(leaf)마다 Risk Score·Bus
    Factor·코드 영향도(코드 결합도) 3개 지표를 한 번에 실어서 반환한다. 프론트가 트리
    렌더링 + 위험도/영향도/BF 정렬 토글을 API 한 번으로 전부 처리할 수 있게 하는 게 목적
    (treemap.html의 warn-box에 있던 "전체 파일 목록 + 중첩 트리" 요청에 대응).

    세 지표는 각각 기존 엔드포인트와 완전히 같은 계산 로직을 공유한다 — 같은 파일이라도
    화면마다 값이 어긋나는 일이 없도록 새로 계산하지 않고 전부 재사용:
        risk_score  → _compute_risk_scores (=/risk-scores와 동일)
        bus_factor  → 위와 동일 소스(_fetch_recent_ownership) 재사용
        coupling    → _compute_coupling (=/coupling와 동일) → _per_file_coupling_summary로 파일당 1개 값으로 압축

    파일 수가 적은(커밋을 아직 안 남긴) 레포는 risk_score/bus_factor가 없는 파일도
    trees에 포함될 수 있다 — 그런 경우 해당 필드는 null.
    """
    file_rows = db.execute(
        text(
            "SELECT id AS file_id, path FROM files "
            "WHERE repository_id = :rid AND COALESCE(is_deleted, false) = false"
        ),
        {"rid": repository_id},
    ).mappings().all()

    if not file_rows:
        return {"tree": {"name": "root", "children": []}, "files": [], "file_count": 0}

    risk_by_file = {f["file_id"]: f for f in _compute_risk_scores(db, repository_id)}
    coupling_result = _compute_coupling(db, repository_id)
    coupling_by_file = _per_file_coupling_summary(coupling_result)

    files = []
    for r in file_rows:
        fid, path = r["file_id"], r["path"]
        risk = risk_by_file.get(fid)
        coup = coupling_by_file.get(fid, {"max_coupling_score": 0.0, "coupled_file_count": 0})
        files.append({
            "file_id": fid,
            "path": path,
            "risk_score": risk["risk_score"] if risk else None,
            "bus_factor": risk["bus_factor"] if risk else None,
            "entropy": round(risk["entropy"], 4) if risk else None,
            "is_bus_factor_risk": risk["is_bus_factor_risk"] if risk else False,
            "coupling_score": round(coup["max_coupling_score"], 4),
            "coupled_file_count": coup["coupled_file_count"],
        })

    # 종합 리스트(위험도순 기본) — 프론트가 트리 아래 랭킹에 바로 쓸 수 있게 정렬해서 같이 내려준다.
    # 영향도순/BF순 재정렬은 프론트에서 이 배열을 그대로 다시 sort하면 됨(추가 호출 불필요).
    files.sort(key=lambda f: f["risk_score"] if f["risk_score"] is not None else -1, reverse=True)

    return {
        "tree": _build_tree(files),
        "files": files,
        "file_count": len(files),
        "co_change_unit": coupling_result["co_change_unit"],
    }
