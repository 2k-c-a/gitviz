"""Community request bodies. Exported to contracts/desktop/v1/community-bodies.json for the desktop allowlist."""
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    """The host shares one local file by name only; its content never goes to the server."""
    model_config = ConfigDict(extra='forbid')
    file_name: str = Field(min_length=1, max_length=200, pattern=r'^[^\x00-\x1f\x7f/\\]*$')
    language: str = Field(default='plain', pattern=_LANGUAGE)


class CollabSeatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    seat_id: str = Field(pattern=_SESSION)


class CollabRelayRequest(BaseModel):
    """One Yjs message: a document update, a sync request/reply (state vector / diff) or a cursor (awareness) update."""
    model_config = ConfigDict(extra='forbid')
    seat_id: str = Field(pattern=_SESSION)
    to: str | None = Field(default=None, pattern=_SESSION)
    kind: str = Field(pattern=r'^(update|sync-request|sync|awareness)$')
    data: str = Field(min_length=1, max_length=200000, pattern=r'^[A-Za-z0-9+/]+={0,2}$')
