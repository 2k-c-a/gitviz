from git import Repo
import os
from urllib.parse import urlparse, urlunparse
import subprocess


def get_default_branch(repo_path: str) -> str | None:
    """로컬 clone은 원격 저장소의 기본 브랜치를 checkout한 상태로 시작하므로
    HEAD가 가리키는 브랜치명이 곧 기본 브랜치(main/master/그 외)다.
    GitHub REST 호출 없이 이미 clone된 저장소에서 바로 알아낼 수 있다."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        branch = result.stdout.strip()
        return branch if branch and branch != "HEAD" else None
    except Exception:
        return None


def _auth_url(github_url: str, token: str) -> str:
    """https GitHub URL에 토큰을 심어 private repo 클론을 인증한다.
    https·github.com 이 아니면 원본 URL을 그대로 반환한다."""
    parsed = urlparse(github_url)
    if parsed.scheme != "https" or "github.com" not in parsed.netloc:
        return github_url
    netloc = parsed.netloc.split("@")[-1]  # 기존 자격증명 제거
    return urlunparse(parsed._replace(netloc=f"x-access-token:{token}@{netloc}"))


def update_repository(repo_path: str, github_url: str, token: str = None) -> None:
    """이미 clone된 저장소를 원격 최신 상태로 맞춘다.

    - 모든 원격 브랜치를 refs/remotes/origin/* 로 fetch(--prune: 원격에서 지운 브랜치 정리)
    - checkout된 기본 브랜치를 origin/<branch>로 fast-forward(reset --hard)
      → collect_current_tree_paths / 기본 브랜치 커밋 판정이 최신 HEAD 기준이 된다.
    이 clone은 수집 전용이라 로컬 변경이 없으므로 reset --hard가 안전하다.
    실패해도 기존 clone으로 수집은 계속할 수 있으니 예외를 올리지 않고 경고만 남긴다.
    """
    # 토큰은 .git/config에 저장하지 않고 이번 fetch에만 인증 URL로 넘긴다.
    fetch_url = _auth_url(github_url, token) if token else "origin"
    try:
        subprocess.run(
            ["git", "fetch", "--prune", fetch_url, "+refs/heads/*:refs/remotes/origin/*"],
            cwd=repo_path, capture_output=True, text=True, check=True,
        )
        branch = get_default_branch(repo_path)
        if branch:
            subprocess.run(
                ["git", "reset", "--hard", f"origin/{branch}"],
                cwd=repo_path, capture_output=True, text=True, check=True,
            )
        print("FETCHED latest from origin")
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or str(e)).strip()
        if token:
            msg = msg.replace(token, "***").replace(fetch_url, github_url)
        print("⚠️ git fetch 실패, 기존 clone으로 계속 진행:", msg)


def clone_repository(github_url: str, repo_name: str, token: str = None):

    repo_path = f"storage/repos/{repo_name}"

    print("CHECK PATH:", repo_path)

    if os.path.exists(repo_path):
        print("REPO ALREADY EXISTS")
        update_repository(repo_path, github_url, token)
        return repo_path

    print("START CLONE")

    # 토큰이 있으면 인증 URL로 클론(private 지원). URL 자체는 토큰이 들어있으니 절대 출력 금지.
    clone_url = _auth_url(github_url, token) if token else github_url
    try:
        repo = Repo.clone_from(clone_url, repo_path)
    except Exception as e:
        # 실패 메시지에 토큰이 섞여 로그/DB(error_message)에 남지 않도록 정화 후 재발생
        msg = str(e)
        if token:
            msg = msg.replace(token, "***").replace(clone_url, github_url)
        raise RuntimeError(f"clone 실패: {msg}") from None

    # 토큰이 .git/config(remote origin)에 영구 저장되지 않도록 원래 URL로 되돌린다.
    if token:
        try:
            repo.remotes.origin.set_url(github_url)
        except Exception:
            pass

    print("END CLONE")

    return repo_path
