"""결과 조회용 Analytics API (DB read 전용).

수집/분석 모듈이 적재한 데이터를 프론트엔드 차트가 바로 쓸 수 있는 형태로 반환한다.
모든 엔드포인트는 repository_id 기반 범용 — 특정 레포(react)에 고정되지 않는다.
"""
import math

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


def _entropy_and_bus_factor(contributor_counts: list[int]) -> tuple[float, int | None]:
    """기여 횟수 리스트 하나(=파일 하나)에서 Ownership Entropy와 Bus Factor를 함께 계산.
    같은 기여 비율 분포에서 파생되므로 한 번에 계산해 재사용한다."""
    total = sum(contributor_counts)
    if total == 0 or not contributor_counts:
        return 0.0, None

    shares = sorted((c / total for c in contributor_counts), reverse=True)

    entropy = -sum(p * math.log2(p) for p in shares if p > 0)

    cum = 0.0
    bus_factor = len(shares)
    for i, p in enumerate(shares, start=1):
        cum += p
        if cum >= 0.8:
            bus_factor = i
            break

    return entropy, bus_factor


def _minmax_norm(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {k: 0.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


@router.get("/risk-scores")
def get_risk_scores(repository_id: int, limit: int = 50, db: Session = Depends(get_db)):
    """파일별 Risk Score — 5-피처(변경빈도·Churn·Entropy·버그비율·Co-change) 가중합.

    설계 노트 — Bus Factor는 의도적으로 이 산식에서 제외했다.
    Bus Factor와 Ownership Entropy는 같은 파일별 기여 비율 분포에서 파생되는 지표라
    (소유권이 집중될수록 BF·Entropy 둘 다 낮아짐) 값 자체는 강하게 상관되지만 "위험"의
    방향은 반대다 — Entropy가 낮다(집중)는 건 결함 위험이 낮다는 뜻이고[Bird et al., 2011],
    BF가 낮다(집중)는 건 승계 위험이 높다는 뜻이다. 같은 산식에 함께 넣으면 같은 원인
    (소유권 집중)에 대해 서로 반대 부호로 상쇄된다. 그래서 Risk Score는 결함/리팩토링
    우선순위라는 단일 차원에 집중하고(Entropy만 사용), Bus Factor는 별도 승계 리스크로
    분리해 고위험 파일에 교차 배지(is_bus_factor_risk)로만 표시한다.
    """
    # 1) 변경빈도 · Churn · 버그비율 (파일별)
    base_rows = db.execute(
        text(
            """
            SELECT
                cfc.file_id,
                f.path,
                COUNT(DISTINCT cfc.commit_id)                                       AS change_freq,
                COALESCE(SUM(cfc.additions + cfc.deletions), 0)                     AS churn,
                COUNT(DISTINCT cfc.commit_id) FILTER (WHERE c.commit_type = 'bugfix') AS bugfix_commits
            FROM commit_file_changes cfc
            JOIN commits c ON c.id = cfc.commit_id
            JOIN files f ON f.id = cfc.file_id
            WHERE c.repository_id = :rid
            GROUP BY cfc.file_id, f.path
            """
        ),
        {"rid": repository_id},
    ).mappings().all()

    if not base_rows:
        return {"files": [], "summary": {"high_risk_count": 0, "avg_risk_score": None,
                                          "hidden_coupling_count": 0, "avg_entropy": None}}

    # 2) 파일별 · 기여자별 커밋 수 (Entropy + Bus Factor 계산용)
    ownership_rows = db.execute(
        text(
            """
            SELECT cfc.file_id, c.contributor_id, COUNT(DISTINCT c.id) AS cnt
            FROM commit_file_changes cfc
            JOIN commits c ON c.id = cfc.commit_id
            WHERE c.repository_id = :rid AND c.contributor_id IS NOT NULL
            GROUP BY cfc.file_id, c.contributor_id
            """
        ),
        {"rid": repository_id},
    ).mappings().all()

    ownership_by_file: dict[int, list[int]] = {}
    for r in ownership_rows:
        ownership_by_file.setdefault(r["file_id"], []).append(r["cnt"])

    # 3) Co-change 집계 (MVP — 대규모 커밋 제외 + threshold pruning만 적용한 단순화 버전.
    #    PR 단위 분석·IDF 보정은 코드영향도(coupling.html) 전용 기능으로 별도 구현 예정)
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

    # 4) 파일별 원시 지표 조립
    raw: dict[int, dict] = {}
    for r in base_rows:
        fid = r["file_id"]
        change_freq = r["change_freq"]
        bugfix = r["bugfix_commits"] or 0
        entropy, bus_factor = _entropy_and_bus_factor(ownership_by_file.get(fid, []))
        raw[fid] = {
            "file_id": fid,
            "path": r["path"],
            "change_freq": change_freq,
            "churn": float(r["churn"]),
            "bug_ratio": (bugfix / change_freq) if change_freq else 0.0,
            "entropy": entropy,
            "co_change": co_change_by_file.get(fid, 0.0),
            "bus_factor": bus_factor,
        }

    # 5) Min-Max 정규화 후 가중합 (w=0.2 균등)
    norm_freq = _minmax_norm({k: v["change_freq"] for k, v in raw.items()})
    norm_churn = _minmax_norm({k: v["churn"] for k, v in raw.items()})
    norm_entropy = _minmax_norm({k: v["entropy"] for k, v in raw.items()})
    norm_bug = _minmax_norm({k: v["bug_ratio"] for k, v in raw.items()})
    norm_co = _minmax_norm({k: v["co_change"] for k, v in raw.items()})

    files = []
    for fid, v in raw.items():
        risk = (
            0.2 * norm_freq.get(fid, 0.0)
            + 0.2 * norm_churn.get(fid, 0.0)
            + 0.2 * norm_entropy.get(fid, 0.0)
            + 0.2 * norm_bug.get(fid, 0.0)
            + 0.2 * norm_co.get(fid, 0.0)
        )
        files.append({
            **v,
            "risk_score": round(risk, 4),
            "is_bus_factor_risk": v["bus_factor"] == 1,
        })

    files.sort(key=lambda x: x["risk_score"], reverse=True)
    top_files = files[:limit]

    entropies = [v["entropy"] for v in raw.values()]
    summary = {
        "high_risk_count": sum(1 for f in files if f["risk_score"] >= 0.7),
        "avg_risk_score": round(sum(f["risk_score"] for f in files) / len(files), 4) if files else None,
        "hidden_coupling_count": sum(1 for v in raw.values() if v["co_change"] >= 0.3),
        "avg_entropy": round(sum(entropies) / len(entropies), 4) if entropies else None,
    }

    return {"files": top_files, "summary": summary}
