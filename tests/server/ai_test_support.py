"""AI 테스트 공용 helper.

- require_test_database(): gitviz_test 전용 DB만 허용하고 app import 전에 환경변수를 설정한다.
- parse_line_maps(): unified diff에서 파일별 좌우 줄 맵을 만든다(fixture 검증용).
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'tests' / 'server' / 'fixtures' / 'ai'
TEST_DB_PREFIX = 'gitviz_test'


def load_fixture(name):
    path = FIXTURES / name
    text = path.read_text(encoding='utf-8')
    return json.loads(text) if path.suffix == '.json' else text


def require_test_database(env=None):
    """GITVIZ_TEST_DATABASE_URL을 검증하고 앱 설정용 환경변수를 지정한다. 반드시 app import 전에 호출."""
    from sqlalchemy.engine import make_url
    env = os.environ if env is None else env
    url = env.get('GITVIZ_TEST_DATABASE_URL')
    if not url:
        raise RuntimeError('GITVIZ_TEST_DATABASE_URL에 격리 PostgreSQL을 지정하세요.')
    if not (make_url(url).database or '').startswith(TEST_DB_PREFIX):
        raise RuntimeError(f'DB 이름은 {TEST_DB_PREFIX}로 시작해야 합니다.')
    env['DATABASE_URL'] = url
    env['DATABASE_URL_ASYNC'] = url.replace('postgresql://', 'postgresql+asyncpg://', 1)
    env.setdefault('SESSION_SECRET_KEY', 'isolated-validation-secret')
    # 앱은 import 때 .env를 읽는다(기존 환경변수는 덮어쓰지 않음). 개발자 .env의 기본 모델 같은 값이 시험 결과를 바꾸지 않도록 고정한다.
    env['AI_PROVIDER_PROFILE'] = 'claude-opus-5-5-v1'
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.core.ai_settings import AISettings
    defaults = AISettings()
    for name, field in {
        'AI_DAILY_JOB_LIMIT': 'daily_job_limit',
        'AI_USER_MONTHLY_BUDGET': 'user_monthly_budget',
        'AI_SERVER_MONTHLY_BUDGET': 'server_monthly_budget',
        'AI_CREDENTIAL_VERIFY_LIMIT_PER_HOUR': 'credential_verify_limit_per_hour',
        'AI_ENABLED': 'enabled',
        'AI_ALLOW_EXTERNAL_TRANSFER': 'allow_external_transfer',
    }.items():
        value = getattr(defaults, field)
        env[name] = str(int(value)) if isinstance(value, bool) else str(value)
    # Not an AISettings field: registry's dynamic local-runtime flag defaults to '0'.
    env['AI_KOBOLDCPP_ENABLED'] = '0'
    return url


_FILE_RE = re.compile(r'^diff --git a/(.+) b/(.+)$')
_HUNK_RE = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def parse_line_maps(diff_text):
    """{path: {change_type, old_path, left, right, right_to_left}} (줄 번호는 1부터).

    left/right는 diff에 나타난 줄, right_to_left는 맥락 줄(양쪽에 존재)의 대응이다.
    """
    result, cur, old_no, new_no = {}, None, 0, 0
    for line in diff_text.splitlines():
        m = _FILE_RE.match(line)
        if m:
            cur = {'change_type': 'modified', 'old_path': None, 'left': [], 'right': [], 'right_to_left': {}}
            result[m.group(2)] = cur
            continue
        if cur is None:
            continue
        if line.startswith('new file mode'):
            cur['change_type'] = 'added'
        elif line.startswith('deleted file mode'):
            cur['change_type'] = 'deleted'
        elif line.startswith('rename from '):
            cur['change_type'] = 'renamed'
            cur['old_path'] = line[len('rename from '):]
        elif line.startswith('---') or line.startswith('+++'):
            continue
        else:
            h = _HUNK_RE.match(line)
            if h:
                old_no, new_no = int(h.group(1)), int(h.group(3))
            elif line.startswith('-'):
                cur['left'].append(old_no)
                old_no += 1
            elif line.startswith('+'):
                cur['right'].append(new_no)
                new_no += 1
            elif line.startswith(' '):
                cur['left'].append(old_no)
                cur['right'].append(new_no)
                cur['right_to_left'][str(new_no)] = old_no
                old_no += 1
                new_no += 1
    return result
