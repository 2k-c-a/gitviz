"""Add project communities (members, URL invites, posts, comments). Additive only; rollback keeps the content."""
from alembic import op

revision = 'c3e5a7b9d1f2'
down_revision = 'b7e1d9c3a502'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE TABLE community_members (\n\tid SERIAL NOT NULL, \n\trepository_id INTEGER NOT NULL, \n\tuser_id UUID NOT NULL, \n\trole VARCHAR(12) NOT NULL, \n\tstatus VARCHAR(12) DEFAULT 'active' NOT NULL, \n\tjoined_via VARCHAR(12) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_community_member UNIQUE (repository_id, user_id), \n\tCONSTRAINT ck_community_member_role CHECK (role IN ('admin','member')), \n\tCONSTRAINT ck_community_member_status CHECK (status IN ('active','removed')), \n\tCONSTRAINT ck_community_member_via CHECK (joined_via IN ('analyzer','contributor','invite')), \n\tFOREIGN KEY(repository_id) REFERENCES repositories (id) ON DELETE CASCADE, \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE\n)")
    op.execute("CREATE UNIQUE INDEX uq_community_admin ON community_members (repository_id) WHERE role = 'admin'")
    op.execute("CREATE TABLE community_invites (\n\tid UUID NOT NULL, \n\trepository_id INTEGER NOT NULL, \n\ttoken_hash VARCHAR(64) NOT NULL, \n\tcreated_by UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tmax_uses INTEGER, \n\tuse_count INTEGER DEFAULT '0' NOT NULL, \n\trevoked_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(repository_id) REFERENCES repositories (id) ON DELETE CASCADE, \n\tUNIQUE (token_hash), \n\tFOREIGN KEY(created_by) REFERENCES users (id) ON DELETE CASCADE\n)")
    op.execute('CREATE INDEX ix_community_invites_repository_id ON community_invites (repository_id)')
    op.execute('CREATE TABLE community_posts (\n\tid SERIAL NOT NULL, \n\trepository_id INTEGER NOT NULL, \n\tauthor_id UUID NOT NULL, \n\ttitle VARCHAR(200) NOT NULL, \n\tbody TEXT NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE, \n\tdeleted_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(repository_id) REFERENCES repositories (id) ON DELETE CASCADE, \n\tFOREIGN KEY(author_id) REFERENCES users (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_community_posts_feed ON community_posts (repository_id, id)')
    op.execute('CREATE TABLE community_comments (\n\tid SERIAL NOT NULL, \n\tpost_id INTEGER NOT NULL, \n\tauthor_id UUID NOT NULL, \n\tbody TEXT NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE, \n\tdeleted_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(post_id) REFERENCES community_posts (id) ON DELETE CASCADE, \n\tFOREIGN KEY(author_id) REFERENCES users (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_community_comments_post ON community_comments (post_id, id)')


def downgrade():
    raise RuntimeError('Hide the community UI instead; community posts and members are user content and are preserved.')
