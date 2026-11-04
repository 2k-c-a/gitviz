"""AI 리뷰를 앱 전용 BYOK로 바꾸며 서버에 저장했던 사용자 API key와 코드 원문을 지운다(AD-33).

- 사용자 key(ai_credential_versions·ai_credentials)를 삭제한다. 작업이 고정했던 key 버전 연결(ai_jobs.credential_version_id)은
  먼저 비운다. 그래서 그 열을 불변 열 목록에서 뺀다(나머지 고정 설정은 그대로 불변).
- 입력 파일의 암호화된 코드 원문(encrypted_payload)을 비운다. 서버 암호화 키 설정이 없어져 더는 열 수 없다.
  manifest·파일별 해시·줄 지도와 리뷰 결과·피드백은 그대로 둔다.
- 더는 쓰지 않는 작업 발행 대기열(task_outbox)의 행을 지운다.
표·열은 지우지 않는다(데이터 보존 원칙과 기존 리비전의 downgrade 경로를 유지한다).
"""
from alembic import op

revision = '39b676ca36f2'
down_revision = 'e7a9c1d3f5b4'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
DROP TRIGGER ai_jobs_identity ON ai_jobs;
CREATE TRIGGER ai_jobs_identity BEFORE UPDATE ON ai_jobs
    FOR EACH ROW EXECUTE FUNCTION gitviz_ai_immutable_columns(
        'owner_user_id', 'repository_id', 'input_id', 'kind', 'fingerprint', 'generation', 'created_at',
        'provider', 'model', 'provider_profile', 'profile_fingerprint', 'consent_manifest_hash');
UPDATE ai_jobs SET credential_version_id = NULL WHERE credential_version_id IS NOT NULL;
DELETE FROM ai_credential_versions;
DELETE FROM ai_credentials;
UPDATE ai_input_files SET encrypted_payload = NULL, key_version = NULL WHERE encrypted_payload IS NOT NULL;
DELETE FROM task_outbox;
""")


def downgrade():
    raise RuntimeError('Deleted API keys and code payloads cannot be restored; AI review is app-only BYOK (AD-33).')
