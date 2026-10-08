"""AI로 보낼 수 있는 파일의 선별 규칙: 링크·바이너리·생성 파일·미지원 언어·크기·의심 비밀.

판정 결과는 계약의 Limitation 값이며, 한 파일에는 가장 먼저 해당하는 사유 하나만 붙는다.
정책 버전(POLICY_VERSION)이 바뀌면 input 해시도 달라져야 하므로 규칙을 바꿀 때 함께 올린다.
"""
import posixpath
import re

from app.schemas.ai import Limitation

POLICY_VERSION = 'code-transfer-v1'
MAX_FILE_BYTES = 200_000

SYMLINK_MODE = '120000'
GITLINK_MODE = '160000'

LANGUAGES = {
    '.py': 'python', '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
    '.ts': 'typescript', '.tsx': 'typescript', '.java': 'java', '.kt': 'kotlin', '.go': 'go', '.rs': 'rust',
    '.rb': 'ruby', '.php': 'php', '.c': 'c', '.h': 'c', '.cc': 'cpp', '.cpp': 'cpp', '.hpp': 'cpp',
    '.cs': 'csharp', '.swift': 'swift', '.scala': 'scala', '.sh': 'shell', '.sql': 'sql',
    '.html': 'html', '.css': 'css', '.scss': 'scss', '.vue': 'vue',
    '.yml': 'yaml', '.yaml': 'yaml', '.toml': 'toml', '.json': 'json', '.md': 'markdown',
}

_GENERATED_NAMES = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'cargo.lock',
                    'go.sum', 'composer.lock', 'gemfile.lock', 'pipfile.lock'}
_GENERATED_SUFFIXES = ('.min.js', '.min.css', '.map', '.pb.go', '_pb2.py', '_pb2_grpc.py', '.generated.ts',
                       '.designer.cs')
_GENERATED_DIRS = {'node_modules', 'dist', 'build', 'vendor', '__pycache__', '.next'}
_GENERATED_MARKER = re.compile(r'@generated|DO NOT EDIT|Code generated .* by ', re.IGNORECASE)

_SECRET_NAMES = {'.env', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', 'credentials', '.npmrc', '.pypirc',
                 '.netrc', 'secrets.json', 'secrets.yml', 'secrets.yaml'}
_SECRET_SUFFIXES = ('.pem', '.key', '.p12', '.pfx', '.jks', '.keystore')
_SECRET_PATTERNS = [
    re.compile(r'-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{40,}\b'),
    re.compile(r'\bxox[abprs]-[A-Za-z0-9-]{10,}'),
    re.compile(r'\bsk-[A-Za-z0-9]{32,}\b'),
    re.compile(r'(?i)\b(?:api[_-]?key|secret|passwd|password|access[_-]?token|auth[_-]?token)\b'
               r'\s*[:=]\s*[\'"][^\'"\s]{12,}[\'"]'),
]


def language_for(path: str) -> str | None:
    return LANGUAGES.get(posixpath.splitext(path.lower())[1])


def path_exclusion(path: str, *, old_path: str | None = None) -> Limitation | None:
    """경로만으로 알 수 있는 제외 사유(내용을 읽기 전)."""
    for candidate in (path, old_path):
        if not candidate:
            continue
        lower = candidate.lower()
        name = posixpath.basename(lower)
        if name in _GENERATED_NAMES or lower.endswith(_GENERATED_SUFFIXES) \
                or _GENERATED_DIRS & set(lower.split('/')[:-1]):
            return Limitation.generated_file
    for candidate in (path, old_path):
        if candidate:
            name = posixpath.basename(candidate.lower())
            if name in _SECRET_NAMES or name.startswith('.env.') or name.endswith(_SECRET_SUFFIXES):
                return Limitation.secret_suspected
    if language_for(path) is None:
        return Limitation.unsupported_language
    return None


def mode_exclusion(old_mode: str | None, new_mode: str | None) -> Limitation | None:
    if SYMLINK_MODE in (old_mode, new_mode) or GITLINK_MODE in (old_mode, new_mode):
        return Limitation.link_or_submodule
    return None


def content_exclusion(text: str, *, byte_count: int) -> Limitation | None:
    """읽은 diff 본문에 대한 제외 사유. 비밀 탐지는 삭제·맥락 줄까지 포함한 전체를 본다."""
    head = '\n'.join(text.splitlines()[:30])
    if _GENERATED_MARKER.search(head):
        return Limitation.generated_file
    if byte_count > MAX_FILE_BYTES:
        return Limitation.too_large
    if any(p.search(text) for p in _SECRET_PATTERNS):
        return Limitation.secret_suspected
    return None
