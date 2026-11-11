"""Community notifications: how far each member has read the chat, a per-community mute, and which schedule invitations
the participant has already been notified about. Columns are added only; existing rows start as already read/notified so
the first poll after the upgrade does not flood anyone. Rollback keeps the data."""
from alembic import op

revision = 'b8d0f2a4c6e9'
down_revision = 'a6c8e0f2b4d7'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE community_members ADD COLUMN chat_read_id INTEGER')
    op.execute("ALTER TABLE community_members ADD COLUMN notifications_muted BOOLEAN DEFAULT 'false' NOT NULL")
    op.execute('UPDATE community_members SET chat_read_id = (SELECT max(c.id) FROM community_chat_messages c '
               'WHERE c.repository_id = community_members.repository_id)')
    op.execute('ALTER TABLE community_event_participants ADD COLUMN notified_at TIMESTAMP WITH TIME ZONE')
    op.execute('UPDATE community_event_participants SET notified_at = now()')


def downgrade():
    raise RuntimeError('Hide the notification UI instead; read positions and mute settings are user state and are preserved.')
