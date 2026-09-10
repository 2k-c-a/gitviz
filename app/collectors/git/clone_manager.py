from git import Repo
import os
from urllib.parse import urlparse, urlunparse


def _auth_url(github_url: str, token: str) -> str:
    """https GitHub URL에 토큰을 심어 private repo 클론을 인증한다.
    https·github.com 이 아니면 원본 URL을 그대로 반환한다."""
    parsed = urlparse(github_url)
    if parsed.scheme != "https" or "github.com" not in parsed.netloc:
        return github_url
    netloc = parsed.netloc.split("@")[-1]  # 기존 자격증명 제거
    return urlunparse(parsed._replace(netloc=f"x-access-token:{token}@{netloc}"))


def clone_repository(github_url: str, repo_name: str, token: str = None):

    repo_path = f"storage/repos/{repo_name}"

    print("CHECK PATH:", repo_path)

    if os.path.exists(repo_path):
        print("REPO ALREADY EXISTS")
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
