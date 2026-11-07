"""Community request bodies. Exported to contracts/desktop/v1/community-bodies.json for the desktop allowlist."""
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# No control characters except line breaks and tabs in multi-line text; titles stay on one line.
_LINE = r'^[^\x00-\x1f\x7f]*$'
_TEXT = r'^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$'


class CreatePostRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200, pattern=_LINE)
    body: str = Field(min_length=1, max_length=20000, pattern=_TEXT)


class UpdatePostRequest(CreatePostRequest):
    pass


class CreateCommentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: str = Field(min_length=1, max_length=5000, pattern=_TEXT)


class UpdateCommentRequest(CreateCommentRequest):
    pass


class CreateInviteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expires_in_hours: int = Field(default=168, ge=1, le=720)
    max_uses: int | None = Field(default=None, ge=1, le=100)


class RedeemInviteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')


class UpdateMemberRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: str = Field(pattern=r'^(active|removed)$')


class TransferAdminRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: UUID


class CreateChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: str = Field(min_length=1, max_length=2000, pattern=_TEXT)


_SESSION = r'^[A-Za-z0-9_-]{22}$'


class VoiceSessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(pattern=_SESSION)


class VoiceSignalRequest(BaseModel):
    """WebRTC signaling relayed to one other seat: an SDP offer/answer, or one ICE candidate."""
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(pattern=_SESSION)
    to: str = Field(pattern=_SESSION)
    kind: str = Field(pattern=r'^(offer|answer|ice)$')
    sdp: str | None = Field(default=None, min_length=1, max_length=16000, pattern=_TEXT)
    candidate: str | None = Field(default=None, max_length=1024, pattern=_LINE)
    sdp_mid: str | None = Field(default=None, max_length=64, pattern=_LINE)
    sdp_mline_index: int | None = Field(default=None, ge=0, le=64)

    @model_validator(mode='after')
    def one_payload(self):
        if self.kind == 'ice':
            if self.candidate is None or self.sdp is not None:
                raise ValueError('ice carries a candidate only')
        elif self.sdp is None or self.candidate is not None or self.sdp_mid is not None or self.sdp_mline_index is not None:
            raise ValueError('offer and answer carry an sdp only')
        return self


_LANGUAGE = r'^(plain|javascript|typescript|jsx|tsx|python|json|markdown|css|html)$'


class CreateCollabSessionRequest(BaseModel):
    """The host shares local files by the start file's name only; their content never goes to the server.
    file_count (AD-34) is how many files the shared set holds, for the session list; absent means one file (AD-29)."""
    model_config = ConfigDict(extra='forbid')
    file_name: str = Field(min_length=1, max_length=200, pattern=r'^[^\x00-\x1f\x7f/\\]*$')
    language: str = Field(default='plain', pattern=_LANGUAGE)
    file_count: int | None = Field(default=None, ge=1, le=60)


class CollabSeatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    seat_id: str = Field(pattern=_SESSION)


class CollabRelayRequest(BaseModel):
    """One Yjs message: a document update, a sync request/reply (state vector / diff) or a cursor (awareness) update.
    In a file set (AD-34) `path` names the file (root-relative, absent for the set's file list), and `asset`/`asset-request`
    carry a read-only image or font in pieces from the host to one seat."""
    model_config = ConfigDict(extra='forbid')
    seat_id: str = Field(pattern=_SESSION)
    to: str | None = Field(default=None, pattern=_SESSION)
    kind: str = Field(pattern=r'^(update|sync-request|sync|awareness|asset|asset-request)$')
    data: str = Field(min_length=1, max_length=200000, pattern=r'^[A-Za-z0-9+/]+={0,2}$')
    path: str | None = Field(default=None, min_length=1, max_length=400, pattern=r'^[^/\\\x00-\x1f:*?"<>|][^\\\x00-\x1f:*?"<>|]*$')

    @field_validator('path')
    @classmethod
    def relative_path(cls, value):
        if value is not None and any(part in ('', '.', '..') for part in value.split('/')):
            raise ValueError('path must be a root-relative file path')
        return value


# Community schedule (2026-11-07). General events and development-process events share one shape; sprints and
# milestones are themselves events that others link to. Issues are kept by number only (no issue collection yet).
EVENT_TYPES = ('meeting', 'team_meeting', 'tech_share', 'community_event', 'other',
               'task', 'code_review', 'sprint', 'milestone', 'deploy', 'release')
_EVENT_TYPE = r'^(' + '|'.join(EVENT_TYPES) + r')$'
_HTTPS = r'^https://[^\s\x00-\x1f\x7f]{1,490}$'


class EventAttachment(BaseModel):
    """A link to a file kept elsewhere (drive, GitHub, ...). The server stores no uploaded files."""
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200, pattern=_LINE)
    url: str = Field(max_length=500, pattern=_HTTPS)


class CreateEventRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200, pattern=_LINE)
    description: str = Field(default='', max_length=10000, pattern=_TEXT)
    event_type: str = Field(pattern=_EVENT_TYPE)
    status: str = Field(default='planned', pattern=r'^(planned|in_progress|done|cancelled)$')
    starts_at: datetime
    ends_at: datetime
    all_day: bool = False
    assignee_id: UUID | None = None
    participant_ids: list[UUID] = Field(default_factory=list, max_length=30)
    location: str | None = Field(default=None, max_length=200, pattern=_LINE)
    meeting_url: str | None = Field(default=None, max_length=500, pattern=_HTTPS)
    visibility: str = Field(default='community', pattern=r'^(community|participants)$')
    reminder_minutes: int | None = Field(default=None, ge=0, le=10080)
    recurrence: str = Field(default='none', pattern=r'^(none|daily|weekly|monthly)$')
    recurrence_until: datetime | None = None
    sprint_id: int | None = Field(default=None, ge=1)
    milestone_id: int | None = Field(default=None, ge=1)
    post_id: int | None = Field(default=None, ge=1)
    issue_numbers: list[int] = Field(default_factory=list, max_length=10)
    pull_numbers: list[int] = Field(default_factory=list, max_length=10)
    release_tag: str | None = Field(default=None, min_length=1, max_length=200, pattern=_LINE)
    goal: str | None = Field(default=None, max_length=2000, pattern=_TEXT)
    version: str | None = Field(default=None, min_length=1, max_length=100, pattern=_LINE)
    environment: str | None = Field(default=None, min_length=1, max_length=100, pattern=_LINE)
    attachments: list[EventAttachment] = Field(default_factory=list, max_length=10)

    @model_validator(mode='after')
    def consistent(self):
        for value in (self.starts_at, self.ends_at, self.recurrence_until):
            if value is not None and value.tzinfo is None:
                raise ValueError('times need a UTC offset')
        if self.ends_at < self.starts_at:
            raise ValueError('ends_at must not be before starts_at')
        if self.ends_at - self.starts_at > timedelta(days=366):
            raise ValueError('an event lasts at most a year')
        if self.recurrence == 'none' and self.recurrence_until is not None:
            raise ValueError('recurrence_until needs a recurrence')
        if self.recurrence_until is not None and self.recurrence_until < self.starts_at:
            raise ValueError('recurrence_until must not be before starts_at')
        if any(not 1 <= n <= 1_000_000_000 for n in [*self.issue_numbers, *self.pull_numbers]):
            raise ValueError('issue and pull request numbers are positive')
        return self


class UpdateEventRequest(CreateEventRequest):
    pass


class EventResponseRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    response: str = Field(pattern=r'^(accepted|declined|tentative)$')
