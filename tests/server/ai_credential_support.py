"""BYOK 테스트 공용 helper: 사용자 key 등록과 작업에 고정할 PinnedConfig 만들기.

테스트 DB(GITVIZ_TEST_DATABASE_URL)를 쓰는 테스트가 support.require_test_database() 뒤에 import한다.
사용자 API key 암호화 키는 테스트 전용 값을 환경변수에 둔다.
"""
import os

os.environ.setdefault('AI_CREDENTIAL_ENCRYPTION_KEYS', 'c1:' + 'b2' * 32)
os.environ.setdefault('AI_CREDENTIAL_KEY_VERSION', 'c1')

TEST_KEY = 'sk-ant-api03-TESTKEY0123456789abcdefABCDEF'
OPUS = 'claude-opus-5-5-v1'
SONNET = 'claude-sonnet-5-5-v1'


def ensure_credential(db, owner_id, key=None, profile_id=OPUS):
    """사용자 key를 실제 암호화 경로로 등록한다(제공자 확인은 건너뜀). 이미 있으면 그대로 둔다."""
    from app.services.ai import credentials
    found = credentials.get_active(db, owner_id, 'anthropic')
    if found:
        return found[1]
    credentials.register_key(db, owner_id, 'anthropic', key or TEST_KEY, preferred_profile_id=profile_id, verify=False)
    return credentials.get_active(db, owner_id, 'anthropic')[1]


def pinned_for(db, owner_id, repository_id, input_id, *, profile_id=OPUS, manifest_hash='a' * 64,
               policy_version='code-transfer-v1'):
    """동의를 거치지 않고 작업에 고정할 설정을 직접 만든다(작업 처리 계층 테스트용)."""
    from app.services.ai.consent import PinnedConfig
    from app.services.ai.providers.registry import get_profile
    version = ensure_credential(db, owner_id)
    profile = get_profile(profile_id)
    return PinnedConfig(provider=profile.provider, profile=profile, profile_fingerprint=profile.fingerprint(),
                        credential_version_id=version.id, credential_version=version.version,
                        key_hint=version.key_hint, input_id=input_id, repository_id=repository_id,
                        manifest_hash=manifest_hash, policy_version=policy_version)
