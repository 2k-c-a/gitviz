import os
import statistics
from datetime import datetime, timezone

import requests
from requests.exceptions import RequestException
from fastapi import APIRouter, Depends, HTTPException, Request
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
from app.api.analytics import _compute_risk_scores
from app.services.lightweight_score import compute_lightweight_score

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

# rate_limited 상태에서 resume_at을 이만큼(초) 넘겨도 여전히 rate_limited면
# 정상 대기가 아니라 워커가 죽어서 멈춘 것으로 간주한다(폴링/시계 오차 여유분).
_STALE_GRACE_SECONDS = 120
# resume_at 정보가 없는(구버전) 작업은 started_at 이후 이만큼 지나도 안 끝나면 멈춘 것으로 간주.
_STALE_FALLBACK_SECONDS = 6 * 3600


def _compute_staleness(job: AnalysisJob) -> dict:
    """진행 중인 작업의 실시간 대기 상태를 계산한다 (DB에 저장하지 않고 조회 시점에 계산).

    - remaining_seconds: rate_limited 상태일 때 남은 대기 시간(라이브 카운트다운용).
    - is_stale / stale_seconds: 대기가 끝났어야 할 시각을 한참 지났는데도 상태가
      안 바뀌었으면(=워커가 죽어서 멈춤) True. resume_at이 없는 구버전 데이터는
      started_at 기준 장시간 미완료로 폴백 판정한다.
    """
    result = {"remaining_seconds": None, "is_stale": False, "stale_seconds": None}
    if job.status not in ("running", "rate_limited"):
        return result

    now = datetime.now(timezone.utc)
    detail = job.progress_detail or {}
    resume_at_str = detail.get("resume_at")

    if resume_at_str:
        try:
            resume_at = datetime.fromisoformat(resume_at_str)
            overdue = (now - resume_at).total_seconds()
            if job.status == "rate_limited":
                result["remaining_seconds"] = max(0, int(-overdue))
            if overdue > _STALE_GRACE_SECONDS:
                result["is_stale"] = True
                result["stale_seconds"] = int(overdue)
            return result
        except (ValueError, TypeError):
            pass

    if job.started_at:
        elapsed = (now - job.started_at).total_seconds()
        if elapsed > _STALE_FALLBACK_SECONDS:
            result["is_stale"] = True
            result["stale_seconds"] = int(elapsed)

    return result


@router.get("/repositories")
def list_repositories(limit: int = 20, db: Session = Depends(get_db)):
    """최근 분석 요청된 레포지토리 목록 (홈 화면용)."""
    repos = (
        db.query(Repository)
        .order_by(Repository.id.desc())
        .limit(limit)
        .all()
    )
    result = []
    for repo in repos:
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
            SELECT repository_id, MAX(created_at) AS my_last_triggered_at
            FROM analysis_jobs
            WHERE triggered_by_user_id = :uid
            GROUP BY repository_id
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
        if status == "done" and file_count:
            live_ids = {fid for fid, _ in live_files}
            risk_rows = [r for r in _compute_risk_scores(db, repo.id) if r["file_id"] in live_ids]
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


def _trigger_analysis(db: Session, repository_id: int, user_id) -> None:
    """기존 레포의 AnalysisJob을 pending으로 되돌리고 analyze_repository task를 큐잉한다.
    analyze_repository()는 마지막으로 저장된 커밋 이후만 증분 수집하므로 재분석 비용이 적다."""
    job = db.query(AnalysisJob).filter(AnalysisJob.repository_id == repository_id).first()
    if job:
        job.status = "pending"
        job.triggered_by_user_id = user_id
        # 이전 작업의 progress_detail(stage='done' 등)이 남아있으면 새 작업이 시작되기
        # 전 첫 폴링에서 모달이 잠깐 "완료" 상태로 잘못 표시된다 — 큐잉 시점에 초기화한다.
        job.progress_detail = {}
        job.error_message = None
        job.completed_at = None
    else:
        db.add(AnalysisJob(
            repository_id=repository_id, status="pending", triggered_by="api",
            triggered_by_user_id=user_id,
        ))
    db.commit()
    analyze_repository.delay(repository_id, user_id=str(user_id) if user_id else None)


def _normalize_github_url(url: str) -> str:
    """두 URL이 같은 레포를 가리키는지 비교할 때만 쓰는 정규화값(trim → 끝 슬래시
    제거 → .git 제거 → 소문자 — GitHub URL은 대소문자를 구분하지 않는다).
    저장에는 쓰지 않는다 — 레포는 사용자가 입력한 원래 표기 그대로 저장한다."""
    normalized = (url or "").strip().rstrip("/")
    if normalized.lower().endswith(".git"):
        normalized = normalized[: -len(".git")]
    return normalized.lower()


@router.post("/repositories")
def create_repository(github_url: str, request: Request, db: Session = Depends(get_db)):
    github_url = github_url.strip().rstrip("/").removesuffix(".git")

    # 로그인한 사용자가 요청했으면 그 사용자의 GitHub 토큰으로 수집한다(공용 토큰 대신
    # 개인 rate limit 사용). 비로그인 요청은 기존처럼 공용 GITHUB_TOKEN으로 폴백.
    user_id = _get_session_user_id(request)

    # 대소문자만 다른 URL도 같은 레포로 인식해야 한다 (예: 조직 이전 등으로 owner
    # 표기가 바뀐 경우도 있었음 — fastapi/fastapi 사례).
    existing = (
        db.query(Repository)
        .filter(func.lower(Repository.url) == _normalize_github_url(github_url))
        .order_by(Repository.id)
        .first()
    )
    if existing:
        _trigger_analysis(db, existing.id, user_id)
        return {
            "message": "Repository re-analysis started",
            "repository_id": existing.id,
        }

    repository = Repository(
        url=github_url,
        name=github_url.rstrip("/").split("/")[-1],
        platform="github",
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)

    job = AnalysisJob(
        repository_id=repository.id, status="pending", triggered_by="api",
        triggered_by_user_id=user_id,
    )
    db.add(job)
    db.commit()

    analyze_repository.delay(repository.id, user_id=str(user_id) if user_id else None)

    return {
        "message": "Repository analysis started",
        "repository_id": repository.id,
    }


_GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
_SEARCH_TIMEOUT_SECONDS = 8
_SEARCH_SORT_VALUES = {"stars", "forks", "help-wanted-issues", "updated"}


@router.get("/search/github-repositories")
def search_github_repositories(
    q: str, sort: str = "", per_page: int = 20, db: Session = Depends(get_db),
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
    token = os.getenv("GITHUB_TOKEN")
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
        _normalize_github_url(url) for (url,) in db.query(Repository.url).all()
    }
    now = datetime.now(timezone.utc)

    results = []
    for item in payload.get("items", []):
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
def reanalyze_repository(repository_id: int, request: Request, db: Session = Depends(get_db)):
    """헤더의 '재분석' 버튼용: repository_id를 직접 받아 증분 재분석을 큐잉한다."""
    repository = db.query(Repository).filter(Repository.id == repository_id).first()
    if not repository:
        raise HTTPException(status_code=404, detail="Repository not found")

    user_id = _get_session_user_id(request)
    _trigger_analysis(db, repository.id, user_id)

    return {
        "message": "Repository re-analysis started",
        "repository_id": repository.id,
    }


@router.get("/repositories/{repository_id}")
def get_repository(repository_id: int, db: Session = Depends(get_db)):
    repository = db.query(Repository).filter(Repository.id == repository_id).first()

    if not repository:
        return {"error": "Repository not found"}

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
def get_repository_status(repository_id: int, db: Session = Depends(get_db)):
    """프론트엔드 폴링용: 현재 상태 + 실시간 진행 상황 (업무지시서 3-1)."""
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
