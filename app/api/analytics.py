"""결과 조회용 Analytics API (DB read 전용).

수집/분석 모듈이 적재한 데이터를 프론트엔드 차트가 바로 쓸 수 있는 형태로 반환한다.
모든 엔드포인트는 repository_id 기반 범용 — 특정 레포(react)에 고정되지 않는다.
"""
import math
import statistics
from datetime import datetime, timezone, timedelta
from app.services.metric_policy import percentile_rank, DEFAULT_WINDOW_DAYS

import networkx as nx
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.repository import Repository
from app.api.snapshot_route import SnapshotAnalyticsRoute

router = APIRouter(prefix="/repositories/{repository_id}/analytics", tags=["analytics"], route_class=SnapshotAnalyticsRoute)


@router.get("/snapshot")
def get_snapshot_metadata(repository_id: int):
    """HTTP 라우트는 저장된 최신 또는 지정 스냅샷 메타데이터를 반환한다."""
    # 실제 응답은 SnapshotAnalyticsRoute가 처리한다.
    raise RuntimeError("HTTP snapshot route only")


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
                -- 커밋 수는 GitHub 표시값과 맞추려 기본 브랜치 커밋만 (NULL = 판정 전 → 포함)
                COUNT(*) FILTER (WHERE on_default_branch IS NOT FALSE)  AS total_commits,
                COUNT(*) FILTER (WHERE on_default_branch IS NOT FALSE
                                   AND committed_at >= now() - interval '30 days')  AS commits_30d,
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
    변경 기여 기반 BF 추정치·기여자별 지분(shares, 내림차순)을 한 번에 계산한다.
    BF는 변경 커밋 수의 누적 80%를 차지하는 최소 인원이며 실제 지식 인원이 아니다.
    같은 분포에서 한 번에 계산해 재사용하며 우선순위 점수에는 가산하지 않는다."""
    total = sum(o["cnt"] for o in owners)
    if total == 0 or not owners:
        return {"entropy": None, "entropy_normalized": None, "bus_factor": None, "shares": [],
                "top_owner_share": None, "contributor_count": 0, "observation_count": 0, "low_support": True}

    sorted_owners = sorted(owners, key=lambda o: o["cnt"], reverse=True)
    shares = [
        {"contributor_id": o["contributor_id"], "name": o["name"], "share": o["cnt"] / total}
        for o in sorted_owners
    ]

    entropy = -sum(s["share"] * math.log2(s["share"]) for s in shares if s["share"] > 0)

    cumulative_count = 0
    bus_factor = len(shares)
    for i, owner in enumerate(sorted_owners, start=1):
        cumulative_count += owner["cnt"]
        # 정수 비교로 정확히 80%인 경우의 부동소수점 누적 오차를 피한다.
        if cumulative_count * 5 >= total * 4:
            bus_factor = i
            break

    return {"entropy": entropy, "entropy_normalized": entropy / math.log2(len(shares)) if len(shares)>1 else 0.0,
            "bus_factor": bus_factor, "shares": shares, "top_owner_share": shares[0]["share"],
            "contributor_count":len(shares), "observation_count":total, "low_support":total<5}


def _metric_window(db):
    info = getattr(db, "info", {})
    end = info.get("analysis_as_of") or datetime.now(timezone.utc)
    return info.get("analysis_window_start") or end - timedelta(days=DEFAULT_WINDOW_DAYS), end


def _fetch_recent_ownership(db, repository_id: int) -> dict[int, list[dict]]:
    """Fixed observation window, no reset at a refactor message."""
    start, end = _metric_window(db)
    rows = db.execute(text("""
        SELECT cfc.file_id, c.contributor_id, co.display_name, co.github_login,
               COUNT(DISTINCT c.id) AS cnt
        FROM commit_file_changes cfc JOIN commits c ON c.id=cfc.commit_id
        JOIN contributors co ON co.id=c.contributor_id
        WHERE c.repository_id=:rid AND c.contributor_id IS NOT NULL
          AND c.committed_at >= :start AND c.committed_at < :end
        GROUP BY cfc.file_id,c.contributor_id,co.display_name,co.github_login
    """), {"rid":repository_id,"start":start,"end":end}).mappings().all()
    by_file = {}
    for r in rows:
        by_file.setdefault(r["file_id"], []).append({
            "contributor_id":r["contributor_id"],
            "name":r["github_login"] or r["display_name"] or "익명", "cnt":r["cnt"]})
    return by_file


def _compute_risk_scores(db: Session, repository_id: int) -> list[dict]:
    """Equal-weight percentile ranks of frequency/churn/weak bugfix counts.

    Current live files with observed changes form the comparison population.
    Ownership and coupling are explanatory metrics, not score components.
    risk_score remains a compatibility alias for maintenance_priority.
    """
    start, end = _metric_window(db)
    rows = db.execute(text("""
        SELECT f.id AS file_id,f.path,MIN(c.committed_at) AS first_observed_at,
               MAX(c.committed_at) FILTER (WHERE c.commit_type='refactor') AS last_refactor_at,
               COUNT(DISTINCT c.id) AS change_freq,
               CASE WHEN COUNT(*) FILTER (WHERE cfc.additions IS NULL OR cfc.deletions IS NULL)=0
                    THEN SUM(cfc.additions+cfc.deletions) ELSE NULL END AS churn,
               COUNT(DISTINCT c.id) FILTER (WHERE c.commit_type='bugfix') AS bugfix_count,
               COUNT(DISTINCT c.id) FILTER (WHERE c.contributor_id IS NULL) AS unidentified_count
        FROM files f JOIN commit_file_changes cfc ON cfc.file_id=f.id
        JOIN commits c ON c.id=cfc.commit_id
        WHERE f.repository_id=:rid AND COALESCE(f.is_deleted,false)=false
          AND c.repository_id=:rid AND c.committed_at >= :start AND c.committed_at < :end
        GROUP BY f.id,f.path
    """), {"rid":repository_id,"start":start,"end":end}).mappings().all()
    ownership = _fetch_recent_ownership(db,repository_id) if rows else {}
    components = {key:percentile_rank({r["file_id"]:r[key] for r in rows})
                  for key in ("change_freq","churn","bugfix_count")}
    files = []
    for r in rows:
        fid = r["file_id"]
        oa = _ownership_analysis(ownership.get(fid,[]))
        ranked = {key:components[key][fid] for key in components}
        priority = sum(ranked.values()) / 3 if all(v is not None for v in ranked.values()) else None
        files.append({
            "file_id":fid,"path":r["path"],"change_freq":r["change_freq"],
            "churn":float(r["churn"]) if r["churn"] is not None else None,"bugfix_count":r["bugfix_count"],
            "bug_ratio":r["bugfix_count"]/r["change_freq"],
            "last_refactor_at":r["last_refactor_at"].isoformat() if r["last_refactor_at"] else None,
            "first_observed_at":r["first_observed_at"].isoformat(),
            "window_start":start.isoformat(),"window_end":end.isoformat(),
            "priority_components":ranked,"maintenance_priority":round(priority,4) if priority is not None else None,
            "risk_score":round(priority,4) if priority is not None else None,"current_risk":None,"structural_risk":None,
            "entropy":oa["entropy"],"entropy_normalized":oa["entropy_normalized"],
            "bus_factor":oa["bus_factor"],"bf80":oa["bus_factor"],
            "top_owner_share":oa["top_owner_share"],"contributor_count":oa["contributor_count"],
            "identified_change_count":oa["observation_count"],
            "unidentified_share":r["unidentified_count"]/r["change_freq"],
            "ownership_low_support":oa["low_support"],
            "is_bus_factor_risk":oa["bus_factor"]==1,
            "evidence_status":"metrics_missing" if priority is None else "low_support" if r["change_freq"]<5 else "observed",
        })
    return files


@router.get("/risk-scores")
def get_risk_scores(repository_id: int, limit: int = 50, db: Session = Depends(get_db)):
    """레포 전체 파일의 Risk Score를 계산해(_compute_risk_scores) 상위 limit개만 반환한다.
    산식 설명은 _compute_risk_scores 문서 참고."""
    files = _compute_risk_scores(db, repository_id)
    if not files:
        return {"files": [], "summary": {"high_risk_count": 0, "avg_risk_score": None,
                                          "hidden_coupling_count": None, "avg_entropy": None,
                                          "score_kind": "maintenance_priority"}}

    files.sort(key=lambda x: x["risk_score"] if x["risk_score"] is not None else -1, reverse=True)
    top_files = files[:limit]

    entropies = [f["entropy"] for f in files if f["entropy"] is not None]
    scores = [f["risk_score"] for f in files if f["risk_score"] is not None]
    summary = {
        "high_risk_count": sum(1 for score in scores if score >= 0.7),
        "avg_risk_score": round(sum(scores) / len(scores), 4) if scores else None,
        "hidden_coupling_count": None,  # AST relation has not been measured.
        "score_kind": "maintenance_priority",
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
    """Fixed-window contribution concentration, not actual knowledge headcount.

    Files without identified contributions are excluded from the BF distribution.
    """
    file_rows = db.execute(
        text(
            "SELECT id AS file_id, path FROM files "
            "WHERE repository_id = :rid AND COALESCE(is_deleted, false) = false"
        ),
        {"rid": repository_id},
    ).mappings().all()

    empty = {
        "summary": {"project_bus_factor": None, "file_bf80_median": None, "high_risk_file_count": 0,
                    "avg_entropy": None, "risky_module_count": 0},
        "distribution": [], "top_owners": [], "risky_modules": [], "files": [],
    }
    if not file_rows:
        return empty

    paths_by_file = {r["file_id"]: r["path"] for r in file_rows}
    ownership_by_file = _fetch_recent_ownership(db, repository_id)
    observations = {f["file_id"]:f for f in _compute_risk_scores(db,repository_id)}

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

    # 2) 기여 집중 파일 수 — BF=1인 파일에서 변경 기여 80% 이상을 차지한 기여자
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
            "entropy_normalized":v["entropy_normalized"], "bf80":v["bus_factor"],
            "top_owner_share":v["top_owner_share"], "contributor_count":v["contributor_count"],
            "observation_count":v["observation_count"], "low_support":v["low_support"],
            "total_change_count":observations.get(fid,{}).get("change_freq",0),
            "unidentified_share":observations.get(fid,{}).get("unidentified_share"),
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
        # 호환용 필드명. 프로젝트 전체 인력 이탈 시뮬레이션이 아닌 파일별 BF 중앙값.
        "project_bus_factor": statistics.median(bf_values),
        "file_bf80_median": statistics.median(bf_values),
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
    """PR co-change, with explicit commit fallback and observed coverage.

    SnapshotInput recalculates large-change flags using max(15,ceil(0.1*N)).
    N is currently the target tree size, not the historical tree size.

    coupling_score(A,B) = co_count(A,B) / min(total_A, total_B)   — [0,1] 범위
    idf(f)              = log(total_units / units_containing_f)
    adjusted_score(A,B)  = coupling_score(A,B) * idf(A) * idf(B)   — utils.py처럼
    전역적으로 자주 바뀌는 파일의 false positive를 억제. idf는 배율이라
    adjusted_score는 1을 넘을 수 있다 — "강결합(≥0.7)" 판정은 배율 영향이 없는
    coupling_score 기준. 기본 정렬은 결합도·표본 수이며 IDF 정렬은 선택 정책이다.

    한계: import 그래프(Python AST 분석)가 아직 없어 "import 관계는 없는데
    coupling만 높다"는 진짜 Hidden Coupling 검증은 불가능하다. 여기서 나가는
    coupling/adjusted score는 그 후보 신호일 뿐, 확정 판정이 아니다.
    """
    coverage = db.execute(text("""
        SELECT COUNT(*) AS merged_pr_count,
          COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM pull_request_files f WHERE f.pull_request_id=p.id)) AS prs_with_files
        FROM pull_requests p WHERE p.repository_id=:rid AND p.state='merged'
    """), {"rid":repository_id}).mappings().one()
    pr_count = db.execute(
        text("SELECT COUNT(DISTINCT p.id) FROM pull_requests p JOIN pull_request_files f ON f.pull_request_id=p.id WHERE p.repository_id = :rid AND COALESCE(p.is_large_pr,false)=false"),
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
            "change_count_a": r["total_a"],
            "change_count_b": r["total_b"],
            "a_to_b_ratio": round(r["co_count"] / r["total_a"], 4),
            "b_to_a_ratio": round(r["co_count"] / r["total_b"], 4),
            "low_support": r["co_count"] < 5,
            "coupling_score": round(coupling_score, 4),
            "adjusted_score": round(adjusted_score, 4),
        })
    pairs.sort(key=lambda p: (-p["coupling_score"], -p["co_change_count"], p["file_a_id"], p["file_b_id"]))

    return {
        "co_change_unit": unit,
        "fallback_reason": None if unit == "pr" else ("no_merged_prs" if not coverage["merged_pr_count"] else
            "pr_details_missing" if not coverage["prs_with_files"] else "all_collected_prs_filtered"),
        "pr_file_coverage": coverage["prs_with_files"] / coverage["merged_pr_count"] if coverage["merged_pr_count"] else None,
        "merged_pr_count": coverage["merged_pr_count"], "prs_with_files": coverage["prs_with_files"],
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
            s = summary.setdefault(fid, {"max_coupling_score": 0.0, "coupled_file_count": 0, "coupling_support": 0})
            if p["coupling_score"] >= s["max_coupling_score"]:
                s["coupling_support"] = max(s["coupling_support"] if p["coupling_score"] == s["max_coupling_score"] else 0, p["co_change_count"])
            s["max_coupling_score"] = max(s["max_coupling_score"], p["coupling_score"])
            if p["coupling_score"] >= 0.7:
                s["coupled_file_count"] += 1
    return summary


@router.get("/coupling")
def get_coupling(repository_id: int, limit: int = 100, min_score: float = 0.0, db: Session = Depends(get_db), sort_by: str = "coupling"):
    """Default raw coupling/support ordering; IDF is an optional candidate."""
    if sort_by not in {"coupling", "idf"}:
        raise ValueError("sort_by must be coupling or idf")
    result = _compute_coupling(db, repository_id)
    pairs = [p for p in result["pairs"] if p["coupling_score"] >= min_score]
    if sort_by == "idf":
        pairs.sort(key=lambda p: (-p["adjusted_score"], -p["co_change_count"], p["file_a_id"], p["file_b_id"]))

    meta = {
        **{k:result[k] for k in ("fallback_reason","pr_file_coverage","merged_pr_count","prs_with_files")},
        "sort_by":sort_by,
        "co_change_unit": result["co_change_unit"],
        "total_units": result["total_units"],
        "excluded_unit_count": result["excluded_unit_count"],
        "total_pairs": len(result["pairs"]),
        "high_coupling_count": sum(1 for p in result["pairs"] if p["coupling_score"] >= 0.7),
        "avg_adjusted_score": round(sum(p["adjusted_score"] for p in result["pairs"]) / len(result["pairs"]), 4)
        if result["pairs"] else None,
    }
    return {"meta": meta, "pairs": pairs[:limit]}


COUPLED_SORT_KEYS = {"coupling": "coupling_score", "idf": "adjusted_score"}


def _coupled_files_for(result: dict, file_id: int, sort_by: str | None = None) -> list[dict]:
    """_compute_coupling() 결과에서 file_id가 걸린 쌍만 뽑아 상대 파일 기준으로 펼치고
    선택 파일에서 상대 파일로의 과거 동반 비율·표본 수 순으로 정렬한다. /coupling/{file_id}와 파일 상세 팝업(files/{file_id}/detail)
    양쪽에서 공유하는 로직 — 계산 결과가 어긋나지 않게 한 곳에 모아둔다.

    sort_by(coupling | idf)를 주면 /coupling과 같은 기준(점수 → 공동 변경 횟수 → file_id)으로
    **전체 후보를 정렬**한다. 호출부는 이 정렬이 끝난 뒤에 limit으로 잘라야 한다 —
    잘린 목록을 다시 정렬하면 전체 최상위 후보가 빠질 수 있다."""
    if sort_by is not None and sort_by not in COUPLED_SORT_KEYS:
        raise ValueError("sort_by must be coupling or idf")
    coupled = []
    for p in result["pairs"]:
        if p["file_a_id"] == file_id:
            coupled.append({"file_id": p["file_b_id"], "path": p["file_b_path"],
                             "co_change_count": p["co_change_count"],
                             "coupling_score": p["coupling_score"], "adjusted_score": p["adjusted_score"],
                             "selected_change_count": p["change_count_a"], "other_change_count": p["change_count_b"],
                             "selected_to_other_ratio": p["a_to_b_ratio"], "other_to_selected_ratio": p["b_to_a_ratio"],
                             "low_support": p["low_support"]})
        elif p["file_b_id"] == file_id:
            coupled.append({"file_id": p["file_a_id"], "path": p["file_a_path"],
                             "co_change_count": p["co_change_count"],
                             "coupling_score": p["coupling_score"], "adjusted_score": p["adjusted_score"],
                             "selected_change_count": p["change_count_b"], "other_change_count": p["change_count_a"],
                             "selected_to_other_ratio": p["b_to_a_ratio"], "other_to_selected_ratio": p["a_to_b_ratio"],
                             "low_support": p["low_support"]})
    if sort_by is None:
        coupled.sort(key=lambda c: (-c["selected_to_other_ratio"], -c["co_change_count"], c["file_id"]))
    else:
        key = COUPLED_SORT_KEYS[sort_by]
        coupled.sort(key=lambda c: (-c[key], -c["co_change_count"], c["file_id"]))
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
                    JOIN commit_file_changes cfc
                      ON cfc.commit_id = c.id AND cfc.file_id = prf.file_id
                    WHERE pr.repository_id = :rid AND COALESCE(pr.is_large_pr, false) = false
                      AND c.repository_id = :rid
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
            "co_edit_raw": v["co_edit_raw"],
            "review_directed": v["review"],
            # 작은 양의 강도가 0으로 반올림되면 거리 계산과 고립 판정이 깨진다.
            "edge_weight": weight,
        })

    return {"edges": edges, "total_prs": total_prs, "total_reviews": total_reviews}


def _compute_network_metrics(edges: list[dict], contributor_ids: list[int]) -> dict:
    """협업 강도의 역수를 거리로 사용한 매개 중심성 + 강도 기반 Louvain 군집.

    NetworkX의 최단 경로 weight는 거리다. 강도와 거리를 별도로 저장하여
    협업이 강할수록 가까워지게 한다. 양수가 아닌 엣지는 관계에서 제외한다.
    """
    G = nx.DiGraph()
    G.add_nodes_from(sorted(contributor_ids))
    for e in sorted(edges, key=lambda e: (e["source"], e["target"])):
        strength = e["edge_weight"]
        if strength <= 0:
            continue
        G.add_edge(e["source"], e["target"], weight=strength, distance=1.0 / strength)

    betweenness = nx.betweenness_centrality(G, weight="distance") if G.number_of_edges() else {n: 0.0 for n in G.nodes}

    UG = nx.Graph()
    UG.add_nodes_from(contributor_ids)
    for e in edges:
        a, b, w = e["source"], e["target"], e["edge_weight"]
        if w <= 0:
            continue
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
def get_file_detail(repository_id: int, file_id: int, limit: int = 20, db: Session = Depends(get_db), sort_by: str | None = None):
    """파일 클릭 팝업용 — 수정한 사람(editors) + 공동 변경 후보(coupled_files).
    공동 변경 신호는 실제 의존성 또는 영향 관계의 확정 판정이 아니다. BF·Coupling과 같은
    소스(_fetch_recent_ownership, _compute_coupling)를 재사용해 값이 어긋나지 않는다.
    sort_by를 주면 전체 후보를 그 기준으로 정렬한 뒤 limit으로 자르고, coupled_total에 전체 후보 수를 싣는다."""
    file_row = db.execute(
        text("SELECT id, path FROM files WHERE repository_id = :rid AND id = :fid"),
        {"rid": repository_id, "fid": file_id},
    ).mappings().first()
    if not file_row:
        return {"error": "File not found"}

    ownership_by_file = _fetch_recent_ownership(db, repository_id)
    oa = _ownership_analysis(ownership_by_file.get(file_id, []))

    coupling_result = _compute_coupling(db, repository_id)
    coupled = _coupled_files_for(coupling_result, file_id, sort_by)

    return {
        "file": {"id": file_row["id"], "path": file_row["path"]},
        "entropy": round(oa["entropy"], 4) if oa["entropy"] is not None else None,
        "entropy_normalized":oa["entropy_normalized"],"top_owner_share":oa["top_owner_share"],
        "contributor_count":oa["contributor_count"],"observation_count":oa["observation_count"],
        "low_support":oa["low_support"],"bf80":oa["bus_factor"],
        "bus_factor": oa["bus_factor"],
        "is_bus_factor_risk": oa["bus_factor"] == 1,
        "editors": [
            {"contributor_id": s["contributor_id"], "name": s["name"], "share": round(s["share"], 4)}
            for s in oa["shares"]
        ],
        "co_change_unit": coupling_result["co_change_unit"],
        "sort_by": sort_by,
        "coupled_total": len(coupled),
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
            "coupling_support": f["coupling_support"],
            "observation_count": f["observation_count"],
            "evidence_status": f["evidence_status"],
            **{k:f[k] for k in ("maintenance_priority","entropy_normalized","top_owner_share",
                               "contributor_count","unidentified_share","ownership_low_support")},
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
            "entropy": round(risk["entropy"], 4) if risk and risk["entropy"] is not None else None,
            "is_bus_factor_risk": risk["is_bus_factor_risk"] if risk else False,
            "coupling_score": round(coup["max_coupling_score"], 4),
            "coupled_file_count": coup["coupled_file_count"],
            "coupling_support": coup.get("coupling_support", 0),
            "maintenance_priority":risk["maintenance_priority"] if risk else None,
            "entropy_normalized":risk["entropy_normalized"] if risk else None,
            "top_owner_share":risk["top_owner_share"] if risk else None,
            "contributor_count":risk["contributor_count"] if risk else 0,
            "unidentified_share":risk["unidentified_share"] if risk else None,
            "ownership_low_support":risk["ownership_low_support"] if risk else True,
            "observation_count": risk["change_freq"] if risk else 0,
            "evidence_status":risk["evidence_status"] if risk else "no_history",
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
