"""브라우저로 직접 확인하기 위해 통합 검증과 같은 프로세스 묶음(app·dispatcher·worker·가짜 제공자)을 띄워 두는 도구.

    python scripts/ai_integration_stack.py      # GITVIZ_TEST_DATABASE_URL·GITVIZ_TEST_REDIS_URL 필요

시작하면 접속 주소와 세션 쿠키(테스트 사용자)를 JSON 한 줄로 출력하고 종료 신호(Ctrl+C)까지 기다린다.
GitHub만 로컬 git fixture로 대체돼 있고 모델 호출은 가짜 제공자가 응답한다(실제 제공자·외부 네트워크 없음).
"""
import base64
import json
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_ai_mvp_integration as v  # noqa: E402


class Live(v.Stack):
    def runTest(self):
        pass


def main():
    Live.setUpClass()
    case = Live()
    case.setUp()
    uid, repo = case.make_user(), case.make_repo()
    data = base64.b64encode(json.dumps({'user_id': str(uid)}).encode())
    cookie = v.TimestampSigner(v.SECRET).sign(data).decode()
    print(json.dumps({'base': Live.base, 'cookie': cookie, 'repo': repo, 'pr': 7, 'provider': f'http://127.0.0.1:{Live.provider_port}'}), flush=True)
    stop = []
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    while not stop:
        time.sleep(0.5)
    Live.tearDownClass()


if __name__ == '__main__':
    main()
