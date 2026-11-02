"""Add community group chat history. Additive only; rollback keeps the messages."""
from alembic import op

revision = 'd4f6a8c0e2b3'
down_revision = 'c3e5a7b9d1f2'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE TABLE community_chat_messages (\n\tid SERIAL NOT NULL, \n\trepository_id INTEGER NOT NULL, \n\tauthor_id UUID NOT NULL, \n\tbody TEXT NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tdeleted_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(repository_id) REFERENCES repositories (id) ON DELETE CASCADE, \n\tFOREIGN KEY(author_id) REFERENCES users (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_community_chat_room ON community_chat_messages (repository_id, id)')


def downgrade():
    raise RuntimeError('Hide the chat UI instead; chat messages are user content and are preserved.')
