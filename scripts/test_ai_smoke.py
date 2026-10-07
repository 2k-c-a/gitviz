"""smoke 도구(smoke_ai_live.py)를 로컬 가짜 제공자로 검증한다. 실제 제공자·GitHub·key는 쓰지 않는다."""
import contextlib
import io
import json
import os
import socket
import sys
import threading
import unittest
from decimal import Decimal
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_test_support as support  # noqa: E402

support.require_test_database()
sys.path.insert(0, str(support.ROOT))

import ai_fake_provider as fake  # noqa: E402
import requests  # noqa: E402
import smoke_ai_live as smoke  # noqa: E402
from app.services.ai.providers.anthropic_provider import ProviderConfigError  # noqa: E402

KEY = 'sk-ant-api03-SMOKESECRETKEY0123456789abcdef'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Smoke(unittest.TestCase):
    def test_local_live_cli_accepts_no_key_with_zero_budget(self):
        with patch.dict(os.environ, {'AI_OLLAMA_ENABLED': '1', 'AI_OLLAMA_MODEL_DIGEST': 'a' * 64,
                                   'GITVIZ_SMOKE_API_KEY': '', 'AI_TEST_PROVIDER_BASE_URL': ''}), \
             patch.object(smoke, 'provider_smoke', return_value={'ok': True, 'steps': [], 'cost_usd': '0',
                                                               'cost_known': True}) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(smoke.main(['provider', '--profile', 'ollama-local-v1', '--live', '--budget', '0']), 0)
            run.assert_called_once_with(None, 'ollama-local-v1', Decimal('0'))

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        threading.Thread(target=fake.serve, args=(cls.port,), daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.port}'
        for _ in range(50):
            try:
                requests.get(cls.base + '/__stats', timeout=1)
                break
            except requests.RequestException:
                threading.Event().wait(0.1)

    def setUp(self):
        requests.post(self.base + '/__reset', timeout=5)
        self._env = patch.dict(os.environ, {'AI_TEST_PROVIDER_BASE_URL': self.base, 'AI_ENABLED': '0'})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def stats(self):
        return requests.get(self.base + '/__stats', timeout=5).json()

    def mode(self, mode, **extra):
        requests.post(self.base + '/__mode', json={'mode': mode, **extra}, timeout=5)

    def run_provider(self, key=KEY, budget='1.0', profile='claude-opus-5-5-v1'):
        return smoke.provider_smoke(key, profile, Decimal(budget), official_only=False)      # 로컬 가짜 제공자로 도구를 시험할 때만

    def test_provider_stage_passes_and_reports_cost_from_usage_and_profile_price(self):
        report = self.run_provider()
        self.assertTrue(report['ok'], report)
        names = [s['step'] for s in report['steps']]
        self.assertEqual(names[0], 'key 확인(models 조회)')
        for kind in ('summary', 'review'):
            self.assertIn(f'count_tokens({kind})', names)
            self.assertIn(f'생성({kind}) 응답', names)
            self.assertIn(f'출력 검증({kind})', names)
        from app.services.ai import token_budget
        from app.services.ai.providers import registry
        expected = token_budget.cost_of(registry.get_profile('claude-opus-5-5-v1'), 1200, 300) * 2
        self.assertEqual(Decimal(report['cost_usd']), expected)
        st = self.stats()
        self.assertEqual((st['models'], st['count_tokens'], st['messages'], st['keys'], st['last_tools']), (1, 2, 2, [KEY], []))   # 사용자 key만, 도구 없음
        self.assertNotIn(KEY, json.dumps(report))
        self.assertNotIn('SMOKESECRET', smoke.render(report, 't'))

    def test_budget_too_small_stops_before_any_generation(self):
        report = self.run_provider(budget='0.01')
        self.assertFalse(report['ok'])
        self.assertEqual(report['steps'][-1]['step'], '예산 확인')
        st = self.stats()
        self.assertEqual((st['messages'], st['count_tokens']), (0, 2))                          # 비용이 드는 호출은 하지 않았다

    def test_rejected_key_fails_at_the_first_step_without_leaking_it(self):
        report = self.run_provider(key='sk-ant-api03-THISISABADKEY0123456789BADKEY')
        self.assertFalse(report['ok'])
        self.assertEqual(len(report['steps']), 1)
        self.assertIn('auth_failed', report['steps'][0]['detail'])
        self.assertNotIn('BADKEY', json.dumps(report))
        self.assertEqual((self.stats()['count_tokens'], self.stats()['messages']), (0, 0))

    def test_unusable_or_invalid_model_output_fails_the_smoke(self):
        for mode, expected in (('bad_json', '생성(summary) 응답'), ('fake_location', '출력 검증(review)')):
            with self.subTest(mode=mode):
                requests.post(self.base + '/__reset', timeout=5)
                self.mode(mode)
                report = self.run_provider()
                self.assertFalse(report['ok'])
                self.assertEqual(report['steps'][-1]['step'], expected)
                self.assertFalse(report['steps'][-1]['ok'])

    def test_lost_response_marks_the_cost_as_possibly_incurred(self):
        self.mode('drop')
        report = self.run_provider()
        self.assertFalse(report['ok'])
        self.assertTrue(report.get('uncertain_cost_possible'))

    def test_real_smoke_uses_only_the_official_address_and_never_sends_the_key_elsewhere(self):
        report = smoke.provider_smoke(KEY, 'claude-opus-5-5-v1', Decimal('1'))                  # 기본(official_only=True): 로컬 주소 우회 거부
        self.assertFalse(report['ok'])
        self.assertEqual(report['steps'][0]['step'], '제공자 주소')
        self.assertEqual((self.stats()['models'], self.stats()['keys']), (0, []))               # key를 어디에도 보내지 않았다
        self.assertNotIn(KEY, json.dumps(report))
        for url in ('https://api.evil.example', 'http://127.0.0.1@evil.example', 'http://10.1.2.3:80'):
            with self.subTest(url=url), patch.dict(os.environ, {'AI_TEST_PROVIDER_BASE_URL': url}):
                with self.assertRaises(ProviderConfigError):                                    # 도구 시험 모드에서도 어댑터가 비로컬 주소를 거부한다
                    smoke.provider_smoke(KEY, 'claude-opus-5-5-v1', Decimal('1'), official_only=False)
                out = io.StringIO()
                with patch.dict(os.environ, {'GITVIZ_SMOKE_API_KEY': KEY}), contextlib.redirect_stdout(out):
                    self.assertEqual(smoke.main(['provider', '--live', '--budget', '1']), 1)    # 실제 경로는 환경변수가 있으면 실패
                self.assertNotIn(KEY, out.getvalue())
                self.assertEqual(self.stats()['models'], 0)

    def test_local_token_estimate_below_the_provider_count_fails(self):
        self.mode('ok', count=20000)                                                            # 제공자 토큰 수가 로컬 추정보다 크다
        report = self.run_provider()
        self.assertFalse(report['ok'])
        self.assertTrue(report['local_estimate_underestimates'])
        self.assertIn('count_tokens(summary)', report['steps'][-1]['step'])
        self.assertFalse(report['steps'][-1]['ok'])
        self.assertEqual(self.stats()['messages'], 0)                                           # 생성 전에 멈춘다

    def test_missing_usage_is_cost_unverified_not_zero(self):
        self.mode('no_usage')
        report = self.run_provider()
        self.assertFalse(report['ok'])
        self.assertEqual((report['cost_known'], report['cost_usd'], report.get('cost_unverified')), (False, None, True))
        self.assertEqual(report['steps'][-1]['step'], '비용 확인(summary)')
        text = smoke.render(report, 't')
        self.assertIn('미확인', text)
        self.assertNotIn('비용: 0', text)
        self.assertEqual(self.stats()['messages'], 1)                                           # 첫 생성에서 멈춘다(미확인 비용을 더 쌓지 않는다)

    def test_cli_never_calls_without_live_and_never_takes_the_key_as_an_argument(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(smoke.main(['provider']), 0)
            self.assertEqual(smoke.main(['github']), 0)
            self.assertEqual(smoke.main(['provider', '--live']), 2)                              # key·예산 없음
            self.assertEqual(smoke.main(['provider', '--live', '--budget', '1']), 2)             # key는 환경변수로만
            self.assertEqual(smoke.main(['github', '--live']), 2)
        self.assertEqual(self.stats()['models'], 0)
        with self.assertRaises(SystemExit):
            smoke.main(['provider', '--live', '--budget', '1', '--api-key', KEY])               # 인자로는 받지 않는다
        with patch.dict(os.environ, {'GITVIZ_SMOKE_API_KEY': KEY}), contextlib.redirect_stdout(out):
            self.assertEqual(smoke.main(['provider', '--live', '--budget', '1']), 1)                # 실제 경로: 로컬 주소 환경변수가 있으면 거부
        self.assertNotIn(KEY, out.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
