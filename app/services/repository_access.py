"""URL 정규화와 GitHub의 현재 권한 확인. 사용자 토큰은 공용 토큰으로 대체하지 않는다."""
import os
import re
import copy
import hashlib
from collections import OrderedDict
from threading import Lock
from datetime import datetime, timezone
from urllib.parse import urlsplit
import requests
from fastapi import HTTPException
from sqlalchemy import text
from app.models.repository import Repository
from app.models.analysis_snapshot import RepositoryAccess

_metadata_cache = OrderedDict()
_metadata_lock = Lock()


def canonical_github_url(value: str) -> str:
    parsed = urlsplit((value or "").strip())
    if (parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.port is not None
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("https://github.com/owner/repository 형식만 허용됩니다.")
    path = parsed.path.rstrip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    parts = path.strip("/").split("/")
    if len(parts) != 2 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) or p in {".", ".."} for p in parts):
        raise ValueError("GitHub 소유자와 저장소 이름이 필요합니다.")
    return "https://github.com/" + "/".join(parts).lower()


def user_github_token(db, user_id):
    if not user_id:
        return None
    encrypted = db.execute(text("SELECT access_token_encrypted FROM users WHERE id = :uid"),
                           {"uid": user_id}).scalar()
    if not encrypted:
        return None
    from app.services.auth_service import decrypt_token
    try:
        return decrypt_token(encrypted)
    except Exception:
        raise HTTPException(401, "GitHub 인증을 갱신해 주세요.")


def github_metadata(url, token=None, *, public_fallback=False):
    canonical = canonical_github_url(url)
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    cache_key=(canonical,hashlib.sha256((token or '').encode()).hexdigest())
    with _metadata_lock:
        cached=copy.deepcopy(_metadata_cache.get(cache_key))
    if cached and cached[0]:headers['If-None-Match']=cached[0]
    try:
        response = requests.get("https://api.github.com/repos/" + canonical.split("github.com/")[1],
                                headers=headers, timeout=8, allow_redirects=True)
    except requests.RequestException:
        raise HTTPException(502, "GitHub 접근 권한을 확인할 수 없습니다.")
    if response.status_code == 304 and cached:
        return cached[1]  # GitHub가 이번 요청의 인증 및 변경 여부를 확인한 응답만 재사용
    if response.status_code == 404:
        raise HTTPException(404, "저장소를 찾을 수 없거나 접근 권한이 없습니다.")
    if response.status_code == 401:
        raise HTTPException(401, "GitHub 인증을 갱신해 주세요.")
    if response.status_code in {403, 429}:
        if public_fallback and token:
            return github_metadata(url, None)  # 비인증 조회로 공개 여부만 확인. private는 404
        raise HTTPException(429, "GitHub 접근 확인 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.")
    if not response.ok:
        raise HTTPException(502, "GitHub 접근 권한 확인에 실패했습니다.")
    data = response.json()
    if not isinstance(data.get("private"), bool) or not isinstance(data.get("id"), int):
        raise HTTPException(502, "GitHub 저장소 정보를 확인할 수 없습니다.")
    # 응답 URL도 엄격히 검증한다(조직/저장소 이전은 canonical URL로 저장).
    data["canonical_url"] = canonical_github_url(data.get("html_url", canonical))
    with _metadata_lock:
        _metadata_cache[cache_key]=(response.headers.get('ETag'),copy.deepcopy(data))
        if len(_metadata_cache)>1000:_metadata_cache.popitem(last=False)
    return data


def verify_repository_access(db, repository_id, user_id=None, *, write=False, live=None):
    """저장소 접근을 확인한다.

    live=False(쓰기가 아닌 조회의 기본값): 마지막 확인에서 공개(is_private=False)로 저장된 저장소는
    GitHub에 다시 묻지 않고 저장된 값을 믿는다. 그래서 GitHub 장애·호출 한도 중에도 저장된 결과를 볼 수 있다.
    공개 여부는 분석·재분석·등록 때(remember_access) 갱신되므로, 그 사이에 비공개로 바뀐 저장소는
    다음 갱신 전까지 공개로 취급된다(2026-11-03 소유자 결정).
    비공개이거나 아직 확인되지 않은(None) 저장소, 쓰기, live=True 호출은 항상 GitHub에 확인한다.
    """
    repo = db.query(Repository).filter(Repository.id == repository_id).first()
    if not repo:
        raise HTTPException(404, "저장소를 찾을 수 없거나 접근 권한이 없습니다.")
    if repo.github_id is None:
        raise HTTPException(409,"이전 저장소의 GitHub 식별 정보가 확인되지 않았습니다. 저장소 URL을 새로 등록해 주세요.")
    if write and not user_id:
        raise HTTPException(401, "분석을 요청하려면 로그인해 주세요.")
    if live is None:
        live = write
    if not live and repo.is_private is False:
        return repo, {"id": repo.github_id, "private": False, "canonical_url": repo.url,
                      "default_branch": repo.default_branch, "name": repo.name}
    personal = user_github_token(db, user_id) if user_id else None
    # 공용 토큰은 공개 여부 확인만 수행한다. private=true이면 개인 인증으로 재확인한다.
    metadata = github_metadata(repo.url, personal or os.getenv("GITHUB_TOKEN"), public_fallback=not personal)
    if metadata["private"] and not personal:
        raise HTTPException(404, "저장소를 찾을 수 없거나 접근 권한이 없습니다.")
    if repo.github_id is not None and metadata["id"] != repo.github_id:
        raise HTTPException(404, "저장소 식별자가 변경됐습니다. 저장소를 다시 등록해 주세요.")
    return repo, metadata


def visible_repository_query(db, user_id=None):
    query = db.query(Repository)
    if user_id:
        linked = db.query(RepositoryAccess.repository_id).filter(RepositoryAccess.user_id == user_id)
        return query.filter((Repository.is_private.is_(False)) | Repository.id.in_(linked))
    return query.filter(Repository.is_private.is_(False))


def remember_access(db, repo, metadata, user_id):
    repo.github_id = metadata["id"]
    repo.url = metadata["canonical_url"]
    repo.is_private = metadata["private"]
    repo.visibility_checked_at = datetime.now(timezone.utc)
    repo.default_branch = metadata.get("default_branch") or repo.default_branch
    if user_id:
        access = db.query(RepositoryAccess).filter_by(repository_id=repo.id, user_id=user_id).first()
        if not access:
            db.add(RepositoryAccess(repository_id=repo.id, user_id=user_id))
        elif access.hidden_at is not None:
            access.hidden_at = None  # 다시 분석을 요청하면 '내 분석 프로젝트'에 다시 보인다.


def hide_from_my_projects(db, repository_id, user_id):
    """'내 분석 프로젝트' 목록에서만 숨긴다. 접근 기록·분석 결과·커뮤니티는 건드리지 않는다."""
    access = db.query(RepositoryAccess).filter_by(repository_id=repository_id, user_id=user_id).first()
    if not access:
        raise HTTPException(404, "내 분석 프로젝트에 없는 저장소입니다.")
    if access.hidden_at is None:
        access.hidden_at = datetime.now(timezone.utc)
