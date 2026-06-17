"""결과 조회용 Analytics API (DB read 전용).

수집/분석 모듈이 적재한 데이터를 프론트엔드 차트가 바로 쓸 수 있는 형태로 반환한다.
모든 엔드포인트는 repository_id 기반 범용 — 특정 레포(react)에 고정되지 않는다.
"""
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
