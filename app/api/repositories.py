import os
import statistics
from datetime import datetime, timezone

import requests
from requests.exceptions import RequestException
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.core.database import get_db
from app.models.repository import Repository
from app.models.analysis_job import AnalysisJob
from app.models.contributor import Contributor
from app.models.commit import Commit
from app.models.pull_request import PullRequest
from app.models.file import File
from app.tasks.analyze_repository import analyze_repository
from app.routers.auth import _get_session_user_id
from app.models.analysis_snapshot import AnalysisSnapshot
from app.services.analysis_snapshots import analysis_parameters, load_snapshot
from app.services.repository_access import (canonical_github_url, github_metadata, user_github_token,
    visible_repository_query, verify_repository_access, remember_access)
from app.services.lightweight_score import compute_lightweight_score
from app.services.job_lifecycle import job_staleness, ACTIVE_STATUSES, LOCK_NAMESPACE

router = APIRouter()

# 확장자 → 표시용 언어 이름 (배지 표시용, 간단한 휴리스틱).
# "대표 언어" 판단은 이 중 실제 프로그래밍 언어(_PRIMARY_LANGUAGE_EXTS)만 후보로 삼는다 —
# 안 그러면 번역 문서가 많은 레포(예: fastapi의 docs/*.md)가 그냥 파일 수만으로
# "Markdown 프로젝트"로 잘못 표시된다.
_EXT_LANGUAGE = {
    "py": "Python", "js": "JavaScript", "jsx": "JavaScript", "mjs": "JavaScript",
    "ts": "TypeScript", "tsx": "TypeScript", "java": "Java", "go": "Go",
    "rb": "Ruby", "php": "PHP", "c": "C", "h": "C", "cpp": "C++", "hpp": "C++",
    "cs": "C#", "rs": "Rust", "kt": "Kotlin", "swift": "Swift", "scala": "Scala",
    "html": "HTML", "css": "CSS", "scss": "SCSS", "vue": "Vue", "sh": "Shell",
    "sql": "SQL", "md": "Markdown", "json": "JSON", "yml": "YAML", "yaml": "YAML",
}
_PRIMARY_LANGUAGE_EXTS = {
    "py", "js", "jsx", "mjs", "ts", "tsx", "java", "go", "rb", "php",
    "c", "h", "cpp", "hpp", "cs", "rs", "kt", "swift", "scala", "vue",
}

def _compute_staleness(job: AnalysisJob) -> dict:
    """시간상 복구 후보를 표시한다. 실제 재시작은 워커 잠금을 확인한 뒤 허용한다."""
    return job_staleness(job)


@router.get("/repositories")
def list_repositories(request: Request, limit: int = Query(20,ge=1,le=100), db: Session = Depends(get_db)):
    """최근 분석 요청된 레포지토리 목록 (홈 화면용)."""
    repos = (
        visible_repository_query(db, _get_session_user_id(request))
        .order_by(Repository.id.desc())
        .limit(limit)
        .all()
    )
    result = []
    for repo in repos:
        try:
            verify_repository_access(db, repo.id, _get_session_user_id(request))
        except HTTPException as exc:
            if exc.status_code in {401, 404, 409}:
                continue
            raise
        latest_job = (
            db.query(AnalysisJob)
            .filter(AnalysisJob.repository_id == repo.id)
            .order_by(AnalysisJob.id.desc())
            .first()
        )
        commit_count = (
            db.query(func.count(Commit.id))
            .filter(Commit.repository_id == repo.id, Commit.on_default_branch.isnot(False))
            .scalar() or 0
        )
        contributor_count = (
            db.query(func.count(Contributor.id))
            .filter(Contributor.repository_id == repo.id)
            .scalar() or 0
        )
        pr_count = (
            db.query(func.count(PullRequest.id))
            .filter(PullRequest.repository_id == repo.id)
            .scalar() or 0
        )
        staleness = _compute_staleness(latest_job) if latest_job else {
            "is_stale": False, "stale_seconds": None, "remaining_seconds": None,
        }
        result.append({
            "id": repo.id,
            "url": repo.url,
            "name": repo.name,
            "status": latest_job.status if latest_job else "unknown",
            "commit_count": commit_count,
            "contributor_count": contributor_count,
            "pr_count": pr_count,
            "last_analyzed_at": repo.last_analyzed_at,
            "created_at": repo.created_at,
            "is_stale": staleness["is_stale"],
            "stale_seconds": staleness["stale_seconds"],
            "remaining_seconds": staleness["remaining_seconds"],
        })
    return result


@router.get("/repositories/mine")
def list_my_repositories(request: Request, db: Session = Depends(get_db)):
    """로그인한 사용자가 직접 분석 요청(신규/재분석)한 레포지토리 목록 — "내 분석
    프로젝트" 페이지용. 레포는 소유 개념이 없는 전역 리소스라, analysis_jobs.
    triggered_by_user_id(이 사용자가 트리거한 job)로 "내가 분석한 레포"를 역산한다.
    정렬은 내가 마지막으로 그 레포 분석을 요청한 시각 기준(최근순)이다."""
    user_id = _get_session_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    my_repos = db.execute(
        text(
            """
            SELECT a.repository_id, COALESCE(MAX(j.created_at), MAX(a.created_at)) AS my_last_triggered_at
            FROM repository_access a
            LEFT JOIN analysis_jobs j ON j.repository_id=a.repository_id AND j.triggered_by_user_id=a.user_id
            WHERE a.user_id = :uid
            GROUP BY a.repository_id
            ORDER BY my_last_triggered_at DESC
            """
        ),
        {"uid": str(user_id)},
    ).mappings().all()

    result = []
    for row in my_repos:
        repo = db.query(Repository).filter(Repository.id == row["repository_id"]).first()
        if not repo:
            continue
        try:
            verify_repository_access(db, repo.id, user_id)
        except HTTPException as exc:
            if exc.status_code in {401, 404, 409}:
                continue
            raise

        latest_job = (
            db.query(AnalysisJob)
            .filter(AnalysisJob.repository_id == repo.id)
            .order_by(AnalysisJob.id.desc())
            .first()
        )
        status = latest_job.status if latest_job else "unknown"

        commit_count = db.query(func.count(Commit.id)).filter(
            Commit.repository_id == repo.id, Commit.on_default_branch.isnot(False)
        ).scalar() or 0
        contributor_count = db.query(func.count(Contributor.id)).filter(Contributor.repository_id == repo.id).scalar() or 0

        live_files = (
            db.query(File.id, File.extension)
            .filter(File.repository_id == repo.id, func.coalesce(File.is_deleted, False) == False)
            .all()
        )
        file_count = len(live_files)

        high_risk_count = bf1_count = 0
        median_bus_factor = None
        if status == "done":
            snapshot = db.query(AnalysisSnapshot).filter_by(repository_id=repo.id).order_by(AnalysisSnapshot.id.desc()).first()
            if snapshot:
                risk_rows = snapshot.payload["tree"]["files"]
                file_count = len(risk_rows)
                high_risk_count = sum(1 for r in risk_rows if r["risk_score"] is not None and r["risk_score"] >= 0.7)
                bf1_count = sum(1 for r in risk_rows if r["is_bus_factor_risk"])
                bf_values = [r["bus_factor"] for r in risk_rows if r["bus_factor"] is not None]
                median_bus_factor = round(statistics.median(bf_values), 1) if bf_values else None

        ext_counts: dict[str, int] = {}
        for _, ext in live_files:
            ext = (ext or "").lower()
            if ext in _PRIMARY_LANGUAGE_EXTS:
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
        top_ext = max(ext_counts, key=ext_counts.get) if ext_counts else None
        language = _EXT_LANGUAGE.get(top_ext) if top_ext else None

        staleness = _compute_staleness(latest_job) if latest_job else {
            "is_stale": False, "stale_seconds": None, "remaining_seconds": None,
        }

        result.append({
            "id": repo.id,
            "url": repo.url,
            "name": repo.name,
            "default_branch": repo.default_branch,
            "language": language,
            "status": status,
            "error_message": latest_job.error_message if latest_job else None,
            "progress_detail": latest_job.progress_detail if latest_job else None,
            "started_at": latest_job.started_at if latest_job else None,
            "my_last_triggered_at": row["my_last_triggered_at"],
            "last_analyzed_at": repo.last_analyzed_at,
            "commit_count": commit_count,
            "contributor_count": contributor_count,
            "file_count": file_count,
            "high_risk_count": high_risk_count,
            "bus_factor_1_count": bf1_count,
            "median_bus_factor": median_bus_factor,
            "is_stale": staleness["is_stale"],
            "stale_seconds": staleness["stale_seconds"],
            "remaining_seconds": staleness["remaining_seconds"],
        })

    return result


def _trigger_analysis(db: Session, repository_id: int, user_id, parameters=None):
    # 요청마다 새 작업을 만든다. 같은 저장소의 진행 중인 작업은 중복 큐잉하지 않는다.
    db.query(Repository).filter_by(id=repository_id).with_for_update().one()
    active_jobs = db.query(AnalysisJob).filter(AnalysisJob.repository_id == repository_id,
        AnalysisJob.status.in_(ACTIVE_STATUSES)).order_by(AnalysisJob.id.desc()).all()
    if active_jobs:
        if any(not job_staleness(active)["is_stale"] for active in active_jobs):
            raise HTTPException(409, "진행 중인 분석이 있습니다. 완료 후 원하는 기간으로 다시 요청해 주세요.")
        # 워커가 장시간 정상 실행 중이면 나이와 무관하게 교체하지 않는다.
        # 수집 워커의 session advisory lock과 같은 키를 transaction 범위로 획득한다.
        unlocked = db.execute(text("SELECT pg_try_advisory_xact_lock(:namespace, :rid)"),
                              {"namespace": LOCK_NAMESPACE, "rid": repository_id}).scalar()
        if not unlocked:
            raise HTTPException(409, "수집 워커가 아직 실행 중입니다. 종료 후 다시 시도해 주세요.")
        for active in active_jobs:
            active.status = "cancelled"
            active.completed_at = datetime.now(timezone.utc)
            active.error_message = "응답이 없는 작업을 새 분석 요청으로 교체했습니다."
    job = AnalysisJob(repository_id=repository_id, status="pending", triggered_by="api",
                      triggered_by_user_id=user_id, parameters=parameters or analysis_parameters())
    db.add(job)
    db.commit()
    try:
        analyze_repository.delay(repository_id, user_id=str(user_id), job_id=job.id)
    except Exception:
        job.status = "failed"
        job.error_message = "분석 작업을 큐에 등록하지 못했습니다. 다시 시도해 주세요."
        db.commit()
        raise HTTPException(503, job.error_message)
    return job.id


def _normalize_github_url(url: str) -> str:
    return canonical_github_url(url)


@router.post("/repositories")
def create_repository(github_url: str, request: Request, analysis_start: str | None = None,
                      analysis_end: str | None = None, db: Session = Depends(get_db)):
    user_id = _get_session_user_id(request)
    if not user_id:
        raise HTTPException(401, "분석을 요청하려면 로그인해 주세요.")
    try:
        github_url = canonical_github_url(github_url)
        parameters = analysis_parameters(analysis_start, analysis_end)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    personal = user_github_token(db, user_id)
    metadata = github_metadata(github_url, personal)
    if metadata["private"] and not personal:
        raise HTTPException(404, "저장소를 찾을 수 없거나 접근 권한이 없습니다.")
    # 주소가 재사용돼도 이전 저장소의 수집 이력/결과에 새 ID를 덮어쓰지 않는다.
    repository = db.query(Repository).filter(Repository.github_id == metadata["id"]).first()
    if repository is None:
        repository = Repository(url=metadata["canonical_url"],name=metadata["name"],platform="github",
                                github_id=metadata["id"], is_private=metadata["private"])
        db.add(repository)
        from sqlalchemy.exc import IntegrityError
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            repository = db.query(Repository).filter_by(github_id=metadata["id"]).one()
    remember_access(db,repository,metadata,user_id)
    job_id = _trigger_analysis(db,repository.id,user_id,parameters)
    return {"message":"Repository analysis queued", "repository_id":repository.id,"job_id":job_id}


_GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
_SEARCH_TIMEOUT_SECONDS = 8
_SEARCH_SORT_VALUES = {"stars", "forks", "help-wanted-issues", "updated"}


@router.get("/search/github-repositories")
def search_github_repositories(
    request: Request, q: str, sort: str = "", per_page: int = 20, db: Session = Depends(get_db),
):
    """GitHub Search API로 레포를 검색해 메타데이터 + is_registered만 반환한다.
    경량 예비 점수(scores)는 Track 02 몫이라 아직 없으면 null로 내려간다 — 프론트
    (search.js)는 scores가 null이어도 안전하게 처리하도록 이미 만들어져 있다.

    기존 GitHubClient.get()은 rate limit에 걸리면 내부에서 블로킹 대기하는 구조라
    화면에서 사용자가 기다리는 이 검색 요청엔 못 쓴다. 여기서는 재시도 없이 1회만
    호출하고, 제한에 걸리면 즉시 429로 응답한다(프론트가 상태 코드만 보고 분기).
    """
    q = q.strip()
    if not q:
        raise HTTPException(status_code=422, detail="검색어(q)가 필요합니다.")

    params = {"q": q, "per_page": max(1, min(per_page, 100))}
    if sort in _SEARCH_SORT_VALUES:
        params["sort"] = sort

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    personal = user_github_token(db, _get_session_user_id(request))
    token = personal or os.getenv("GITHUB_TOKEN")
    if not personal:
        params["q"] = f"{q} is:public"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.get(
            _GITHUB_SEARCH_URL, params=params, headers=headers, timeout=_SEARCH_TIMEOUT_SECONDS,
        )
    except RequestException:
        raise HTTPException(status_code=502, detail="GitHub에 연결할 수 없습니다.")

    if response.status_code in (403, 429):
        raise HTTPException(status_code=429, detail="GitHub 검색 API 한도에 도달했습니다.")
    if not response.ok:
        raise HTTPException(status_code=502, detail=f"GitHub 검색에 실패했습니다 ({response.status_code}).")

    payload = response.json()
    registered_urls = {
        _normalize_github_url(r.url) for r in visible_repository_query(db, _get_session_user_id(request)).all()
    }
    now = datetime.now(timezone.utc)

    results = []
    for item in payload.get("items", []):
        if item.get("private") and not personal:
            continue
        owner = item.get("owner") or {}
        html_url = item.get("html_url", "")
        metadata = {
            "language": item.get("language"),
            "stars": item.get("stargazers_count", 0),
            "open_issues": item.get("open_issues_count", 0),
            "pushed_at": item.get("pushed_at"),
        }
        results.append({
            "owner": owner.get("login"),
            "repo_name": item.get("name"),
            "url": html_url,
            "description": item.get("description"),
            "language": metadata["language"],
            "stars": metadata["stars"],
            "forks": item.get("forks_count", 0),
            "open_issues": metadata["open_issues"],
            "pushed_at": metadata["pushed_at"],
            "is_registered": _normalize_github_url(html_url) in registered_urls,
            "scores": compute_lightweight_score(metadata, now=now),
        })

    return {
        "query": q,
        "total_count": payload.get("total_count", 0),
        "results": results,
    }


@router.post("/repositories/{repository_id}/reanalyze")
def reanalyze_repository(repository_id: int, request: Request, analysis_start: str | None = None,
                         analysis_end: str | None = None, db: Session = Depends(get_db)):
    user_id = _get_session_user_id(request)
    repository, metadata = verify_repository_access(db, repository_id, user_id, write=True)
    try:
        parameters = analysis_parameters(analysis_start,analysis_end)
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    remember_access(db,repository,metadata,user_id)
    job_id = _trigger_analysis(db,repository.id,user_id,parameters)
    return {"message":"Repository re-analysis queued","repository_id":repository.id,"job_id":job_id}


@router.get("/repositories/{repository_id}")
def get_repository(repository_id: int, request: Request, db: Session = Depends(get_db)):
    repository, _ = verify_repository_access(db, repository_id, _get_session_user_id(request))

    latest_job = (
        db.query(AnalysisJob)
        .filter(AnalysisJob.repository_id == repository.id)
        .order_by(AnalysisJob.id.desc())
        .first()
    )

    return {
        "id": repository.id,
        "url": repository.url,
        "name": repository.name,
        "status": latest_job.status if latest_job else None,
        "last_analyzed_at": repository.last_analyzed_at,
    }


@router.get("/repositories/{repository_id}/status")
def get_repository_status(repository_id: int, request: Request, db: Session = Depends(get_db)):
    """프론트엔드 폴링용: 접근 권한을 확인한 저장소 상태만 반환한다."""
    verify_repository_access(db, repository_id, _get_session_user_id(request))
    job = (
        db.query(AnalysisJob)
        .filter(AnalysisJob.repository_id == repository_id)
        .order_by(AnalysisJob.id.desc())
        .first()
    )
    if not job:
        return {"error": "No analysis job found"}

    total_contributors = (
        db.query(func.count(Contributor.id))
        .filter(Contributor.repository_id == repository_id)
        .scalar() or 0
    )
    commit_count = (
        db.query(func.count(Commit.id))
        .filter(Commit.repository_id == repository_id, Commit.on_default_branch.isnot(False))
        .scalar() or 0
    )
    pr_count = (
        db.query(func.count(PullRequest.id))
        .filter(PullRequest.repository_id == repository_id)
        .scalar() or 0
    )

    staleness = _compute_staleness(job)

    return {
        "repository_id": repository_id,
        "job_id": job.id,
        "snapshot_id": (job.parameters or {}).get("snapshot_id"),
        "status": job.status,
        "progress_detail": job.progress_detail or {},
        "error_message": job.error_message,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "commit_count": commit_count,
        "pr_count": pr_count,
        "contributors_total": total_contributors,
        "remaining_seconds": staleness["remaining_seconds"],
        "is_stale": staleness["is_stale"],
        "stale_seconds": staleness["stale_seconds"],
    }
