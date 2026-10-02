"""저장소 ID별 수집 디렉토리와 프로세스 한정 Git 인증."""
import base64
import os
import subprocess
from pathlib import Path
from git import Repo
from app.services.repository_access import canonical_github_url

REPO_ROOT = Path(__file__).resolve().parents[3] / 'storage' / 'repos'


def repository_path(repository_id: int) -> Path:
    if isinstance(repository_id, bool) or not isinstance(repository_id, int) or repository_id <= 0:
        raise ValueError('유효한 repository_id가 필요합니다.')
    root = REPO_ROOT.resolve()
    path = (root / str(repository_id)).resolve()
    if path.parent != root:
        raise ValueError('저장소 경로가 수집 디렉토리를 벗어납니다.')
    return path


def git_environment(token=None):
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GCM_INTERACTIVE'] = 'never'
    if token:
        encoded = base64.b64encode(f'x-access-token:{token}'.encode()).decode()
        env.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
                   GIT_CONFIG_VALUE_0=f'AUTHORIZATION: basic {encoded}')
    return env


def get_default_branch(repo_path):
    repo = Repo(repo_path)
    try:
        return repo.git.symbolic_ref('refs/remotes/origin/HEAD').removeprefix('refs/remotes/origin/')
    except Exception:
        return repo.active_branch.name if not repo.head.is_detached else None


def clone_repository(github_url: str, repository_id: int, token=None):
    url = canonical_github_url(github_url)
    path = repository_path(repository_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    env = git_environment(token)
    try:
        if path.exists():
            repo = Repo(path)
            if canonical_github_url(repo.remotes.origin.url) != url:
                raise ValueError('수집 디렉토리의 원격 저장소가 일치하지 않습니다.')
            subprocess.run(['git', 'fetch', '--prune', 'origin', '+refs/heads/*:refs/remotes/origin/*'],
                           cwd=path, env=env, check=True, capture_output=True)
            subprocess.run(['git', 'remote', 'set-head', 'origin', '--auto'], cwd=path,
                           env=env, check=True, capture_output=True)
            branch = get_default_branch(str(path))
            if not branch:
                raise ValueError('기본 브랜치를 확인할 수 없습니다.')
            # 위에서 수집 작업공간 내부의 ID별 경로임을 검증한 클론에만 수행한다.
            subprocess.run(['git', 'reset', '--hard', f'origin/{branch}'], cwd=path,
                           env=env, check=True, capture_output=True)
        else:
            Repo.clone_from(url, str(path), env=env)
    except Exception:
        raise RuntimeError('저장소 복제/갱신에 실패했습니다. 접근 권한과 네트워크를 확인해 주세요.') from None
    return str(path)


def snapshot_target(repo_path, as_of=None):
    repo = Repo(repo_path)
    if as_of is None:
        sha = repo.head.commit.hexsha
    else:
        candidate = next((c for c in repo.iter_commits('HEAD', before=as_of.isoformat())
                          if c.committed_datetime < as_of), None)
        if candidate is None:
            raise ValueError('분석 종료 시각 이전의 기본 브랜치 커밋이 없습니다.')
        sha = candidate.hexsha
    paths = repo.git.ls_tree('-r', '-z', '--name-only', sha).split('\0')
    paths = [p for p in paths if p]
    reachable = repo.git.rev_list(sha).splitlines()
    return sha, paths, reachable
