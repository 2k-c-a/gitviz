"""팀 API DTO(S13). 계약은 docs/AI_TEAM_ACCESS.md, docs/AI_INTERFACE_CONTRACT.md 18절."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TeamRole = Literal["admin", "member", "viewer"]
ShareType = Literal["review_result", "handover_target"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TeamCreate(_Strict):
    name: str = Field(min_length=1, max_length=80)


class TeamPatch(_Strict):
    name: str = Field(min_length=1, max_length=80)


class TeamView(_Strict):
    team_id: UUID
    name: str
    role: TeamRole
    status: Literal["active", "invited"]
    visibility_epoch: int
    member_count: int
    created_at: datetime


class TeamList(_Strict):
    teams: list[TeamView]


class InviteRequest(_Strict):
    github_login: str = Field(min_length=1, max_length=255)
    role: TeamRole = "member"


class RolePatch(_Strict):
    role: TeamRole


class MemberView(_Strict):
    user_id: UUID
    github_login: str
    display_name: str | None = None
    role: TeamRole
    status: Literal["active", "invited"]
    created_at: datetime


class MemberList(_Strict):
    members: list[MemberView]


class RepositoryAdd(_Strict):
    repository_id: int = Field(ge=1)


class TeamRepositoryView(_Strict):
    repository_id: int
    name: str | None = None
    url: str | None = None
    created_at: datetime


class TeamRepositoryList(_Strict):
    repositories: list[TeamRepositoryView]


class ShareCreate(_Strict):
    resource_type: ShareType
    resource_id: UUID


class ShareView(_Strict):
    share_id: UUID
    team_id: UUID
    repository_id: int
    resource_type: ShareType
    resource_id: UUID
    shared_by: UUID
    shared_by_login: str | None = None
    status: Literal["active", "outdated", "expired"]
    created_at: datetime


class ShareList(_Strict):
    shares: list[ShareView]


class ShareContent(_Strict):
    share_id: UUID
    resource_type: ShareType
    repository_id: int
    content: dict[str, Any]


class ReportCreate(_Strict):
    repository_ids: list[int] = Field(min_length=1, max_length=20)


class ReportListItem(_Strict):
    report_id: UUID
    team_id: UUID
    repository_ids: list[int]
    created_by: UUID | None = None
    visibility_epoch: int
    visibility_current: bool          # 팀의 현재 공개 범위 세대와 같다(조회할 때 원본 공유와 저장소 접근을 다시 확인한다)
    created_at: datetime


class ReportList(_Strict):
    reports: list[ReportListItem]


class ReportView(_Strict):
    report_id: UUID
    team_id: UUID
    repository_ids: list[int]
    created_by: UUID | None = None
    visibility_epoch: int
    template_version: str
    source_manifest: dict[str, Any]
    payload: dict[str, Any]
    markdown: str
    stale_reasons: list[str] = Field(default_factory=list)
    created_at: datetime
