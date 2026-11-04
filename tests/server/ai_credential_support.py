"""BYOK 테스트 공용 helper: 실행 요청에 붙일 사용자 key와 작업에 고정할 PinnedConfig 만들기.

AI 리뷰는 앱 전용 BYOK다(AD-33): 서버는 key를 저장하지 않고, 앱이 실행 요청마다 X-GitViz-AI-Key 헤더로 보낸다.
테스트 DB(GITVIZ_TEST_DATABASE_URL)를 쓰는 테스트가 support.require_test_database() 뒤에 import한다.
"""
TEST_KEY = 'sk-ant-api03-TESTKEY0123456789abcdefABCDEF'
KEY_HEADER = 'X-GitViz-AI-Key'
OPUS = 'claude-opus-5-5-v1'
SONNET = 'claude-sonnet-5-5-v1'


def key_headers(key: str = TEST_KEY) -> dict:
    """앱 main이 실행 요청에 붙이는 헤더."""
    return {KEY_HEADER: key}


def pinned_for(db, owner_id, repository_id, input_id, *, profile_id=OPUS, manifest_hash='a' * 64,
               policy_version='code-transfer-v1'):
    """동의를 거치지 않고 작업에 고정할 설정을 직접 만든다(작업 처리 계층 테스트용)."""
    from app.services.ai.consent import PinnedConfig
    from app.services.ai.providers.registry import get_profile
    profile = get_profile(profile_id)
    return PinnedConfig(provider=profile.provider, profile=profile, profile_fingerprint=profile.fingerprint(),
                        input_id=input_id, repository_id=repository_id,
                        manifest_hash=manifest_hash, policy_version=policy_version)
