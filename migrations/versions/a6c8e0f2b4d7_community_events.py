"""Add the community schedule (events and their participants). Additive only; rollback keeps the content."""
from alembic import op

revision = 'a6c8e0f2b4d7'
down_revision = '39b676ca36f2'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE TABLE community_events (\n\tid SERIAL NOT NULL, \n\trepository_id INTEGER NOT NULL, \n\tauthor_id UUID NOT NULL, \n\ttitle VARCHAR(200) NOT NULL, \n\tdescription TEXT DEFAULT '' NOT NULL, \n\tevent_type VARCHAR(20) NOT NULL, \n\tstatus VARCHAR(12) DEFAULT 'planned' NOT NULL, \n\tstarts_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tends_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tall_day BOOLEAN DEFAULT 'false' NOT NULL, \n\tassignee_id UUID, \n\tlocation VARCHAR(200), \n\tmeeting_url VARCHAR(500), \n\tvisibility VARCHAR(12) DEFAULT 'community' NOT NULL, \n\treminder_minutes INTEGER, \n\trecurrence VARCHAR(8) DEFAULT 'none' NOT NULL, \n\trecurrence_until TIMESTAMP WITH TIME ZONE, \n\tsprint_id INTEGER, \n\tmilestone_id INTEGER, \n\tpost_id INTEGER, \n\tdetails JSONB DEFAULT '{}' NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE, \n\tdeleted_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_community_event_type CHECK (event_type IN ('meeting','team_meeting','tech_share','community_event','other','task','code_review','sprint','milestone','deploy','release')), \n\tCONSTRAINT ck_community_event_status CHECK (status IN ('planned','in_progress','done','cancelled')), \n\tCONSTRAINT ck_community_event_visibility CHECK (visibility IN ('community','participants')), \n\tCONSTRAINT ck_community_event_recurrence CHECK (recurrence IN ('none','daily','weekly','monthly')), \n\tCONSTRAINT ck_community_event_range CHECK (ends_at >= starts_at), \n\tFOREIGN KEY(repository_id) REFERENCES repositories (id) ON DELETE CASCADE, \n\tFOREIGN KEY(author_id) REFERENCES users (id) ON DELETE CASCADE, \n\tFOREIGN KEY(assignee_id) REFERENCES users (id) ON DELETE SET NULL, \n\tFOREIGN KEY(sprint_id) REFERENCES community_events (id) ON DELETE SET NULL, \n\tFOREIGN KEY(milestone_id) REFERENCES community_events (id) ON DELETE SET NULL, \n\tFOREIGN KEY(post_id) REFERENCES community_posts (id) ON DELETE SET NULL\n)")
    op.execute('CREATE INDEX ix_community_events_calendar ON community_events (repository_id, starts_at)')
    op.execute("CREATE TABLE community_event_participants (\n\tevent_id INTEGER NOT NULL, \n\tuser_id UUID NOT NULL, \n\tresponse VARCHAR(10) DEFAULT 'pending' NOT NULL, \n\tresponded_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (event_id, user_id), \n\tCONSTRAINT ck_community_event_response CHECK (response IN ('pending','accepted','declined','tentative')), \n\tFOREIGN KEY(event_id) REFERENCES community_events (id) ON DELETE CASCADE, \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE\n)")
    op.execute('CREATE INDEX ix_community_event_participants_user ON community_event_participants (user_id)')


def downgrade():
    raise RuntimeError('Hide the schedule UI instead; community events and responses are user content and are preserved.')
