"""Add desktop authentication only. Rollback preserves issued-session records."""
from alembic import op
revision = 'b7e1d9c3a502'
down_revision = 'a9d2f4c6e8b0'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE TABLE desktop_auth_transaction (\n\tid UUID NOT NULL, \n\tchallenge VARCHAR(64) NOT NULL, \n\tpoll_secret_hash VARCHAR(64) NOT NULL, \n\tdisplay_code VARCHAR(12) NOT NULL, \n\tdevice_label VARCHAR(80) NOT NULL, \n\tissuer_hash VARCHAR(64) NOT NULL, \n\tuser_id UUID, \n\tbrowser_session_binding_hash VARCHAR(64), \n\tstate VARCHAR(12) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tnext_poll_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texchange_key_hash VARCHAR(64), \n\tissuance_response_ciphertext BYTEA, \n\tresponse_expires_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_desktop_flow_state CHECK (state IN ('pending','authorized','exchanged','expired','denied')), \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE\n)")
    op.execute('CREATE INDEX ix_desktop_flow_expiry ON desktop_auth_transaction (expires_at, state)')
    op.execute('CREATE INDEX ix_desktop_flow_issuer ON desktop_auth_transaction (issuer_hash, created_at)')
    op.execute('CREATE TABLE desktop_session (\n\tid UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\ttoken_hash VARCHAR(64) NOT NULL, \n\tdevice_label VARCHAR(80) NOT NULL, \n\tcapabilities JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trevoked_at TIMESTAMP WITH TIME ZONE, \n\tlast_used_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, \n\tUNIQUE (token_hash)\n)')
    op.execute('CREATE INDEX ix_desktop_session_user ON desktop_session (user_id, revoked_at)')


def downgrade():
    raise RuntimeError('Disable desktop issuance and revoke sessions; preserve authentication tables.')
