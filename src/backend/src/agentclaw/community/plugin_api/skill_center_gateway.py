"""Plugin API for the team-scoped Skill Center integration.

This consumer-owned Plugin API lives with the Avernet domains that call it;
the real HTTP implementation, private configuration, and credentials live in
OCB and are selected only by its composition root. Community code never imports
``agentclaw.corp``. The boundary is intentionally independent from
``SkillCenterClient``, whose legacy callers and untyped wire contract remain
unchanged. Gateway request objects contain no endpoint, credential, tenant, or
environment settings: deployment adapters obtain those from their
composition-root configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


def _require(value: str, field: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} is required")


def _validate_page(page_num: int, page_size: int) -> None:
    if page_num < 1:
        raise ValueError("page_num must be at least 1")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")


class SkillCenterGatewayErrorCode(str, Enum):
    """Stable failure categories exposed without leaking HTTP or an SC SDK."""

    BUSINESS = "business_error"
    TIMEOUT = "timeout"
    UNKNOWN_RESPONSE = "unknown_response"
    PROTOCOL = "protocol_error"
    UNAVAILABLE = "unavailable"


class SkillCenterGatewayError(RuntimeError):
    """Normalized boundary failure; retry and Attempt policy stay upstream."""

    def __init__(
        self,
        code: SkillCenterGatewayErrorCode,
        message: str,
        *,
        upstream_code: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.upstream_code = upstream_code
        self.trace_id = trace_id


class SkillCenterPublishSubmissionState(str, Enum):
    """Normalized outcome of the one-shot publish submission call."""

    ACCEPTED = "ACCEPTED"


class SkillCenterPublishState(str, Enum):
    """Normalized state returned by publish-status queries."""

    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class SkillCenterSortOrder(str, Enum):
    """Documented Skill Center catalogue ordering values."""

    LATEST = "latest"
    OLDEST = "oldest"
    HEAT = "heat"
    DOWNLOAD = "download"
    FAVORITE = "favorite"


class SkillCenterAccessLevel(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    PRIVATE = "PRIVATE"


class SkillCenterBelongTo(str, Enum):
    PERSONAL = "PERSONAL"
    TEAM = "TEAM"


class SkillCenterVisibility(str, Enum):
    """Visibility values accepted by the SC publish wire."""

    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"


class SkillCenterReadScope(str, Enum):
    """Explicit trust scope for version metadata and package reads."""

    PUBLIC = "PUBLIC"
    TEAM = "TEAM"


@dataclass(frozen=True)
class SkillCenterTeamCreateRequest:
    team_code: str
    team_name: str
    ref_source: str
    ref_source_id: str
    description: str | None = None
    icon: str | None = None

    def __post_init__(self) -> None:
        _require(self.team_code, "team_code")
        _require(self.team_name, "team_name")
        _require(self.ref_source, "ref_source")
        _require(self.ref_source_id, "ref_source_id")


@dataclass(frozen=True)
class SkillCenterTeamLookupRequest:
    ref_source: str
    ref_source_id: str

    def __post_init__(self) -> None:
        _require(self.ref_source, "ref_source")
        _require(self.ref_source_id, "ref_source_id")


@dataclass(frozen=True)
class SkillCenterTeam:
    team_id: str
    team_code: str
    team_name: str
    ref_source: str
    ref_source_id: str

    def __post_init__(self) -> None:
        _require(self.team_id, "team_id")


@dataclass(frozen=True)
class SkillCenterPublicSkillSearchRequest:
    keyword: str | None = None
    page_num: int = 1
    page_size: int = 20
    tags: tuple[str, ...] = ()
    official_only: bool | None = None
    recommended_only: bool | None = None
    sort_by: SkillCenterSortOrder = SkillCenterSortOrder.LATEST
    creator_name: str | None = None
    creator_work_no: str | None = None

    def __post_init__(self) -> None:
        _validate_page(self.page_num, self.page_size)


@dataclass(frozen=True)
class SkillCenterPublicSkillDetailRequest:
    skill_code: str

    def __post_init__(self) -> None:
        _require(self.skill_code, "skill_code")


@dataclass(frozen=True)
class SkillCenterTeamSkillListRequest:
    team_id: str
    keyword: str | None = None
    page_num: int = 1
    page_size: int = 20

    def __post_init__(self) -> None:
        _require(self.team_id, "team_id")
        _validate_page(self.page_num, self.page_size)


@dataclass(frozen=True)
class SkillCenterTeamSkillDetailRequest:
    team_id: str
    skill_code: str

    def __post_init__(self) -> None:
        _require(self.team_id, "team_id")
        _require(self.skill_code, "skill_code")


@dataclass(frozen=True)
class SkillCenterTag:
    tag_id: str
    name: str
    description: str | None = None
    icon_url: str | None = None
    parent_id: str | None = None
    level: int = 1
    children: tuple["SkillCenterTag", ...] = ()


@dataclass(frozen=True)
class SkillCenterSkill:
    skill_code: str
    skill_name: str
    access_level: SkillCenterAccessLevel
    description: str | None = None
    skill_id: str | None = None
    creator_id: str | None = None
    creator_work_no: str | None = None
    creator_name: str | None = None
    latest_version_number: str | None = None
    official_version_number: str | None = None
    updated_at: str | None = None
    icon_url: str | None = None
    belong_to: SkillCenterBelongTo | None = None
    owner_name: str | None = None
    homepage_url: str | None = None
    office_download_url: str | None = None
    intranet_download_url: str | None = None
    sha256: str | None = None
    tags: tuple[str, ...] = ()
    favorite_count: int | None = None
    download_count: int | None = None
    is_official: bool | None = None
    is_recommended: bool | None = None
    is_test: bool | None = None
    network_types: tuple[str, ...] = ()
    antcode_url: str | None = None


@dataclass(frozen=True, kw_only=True)
class SkillCenterTeamSkill(SkillCenterSkill):
    """Team-scoped Skill result whose tenant identity is never inferred."""

    team_id: str
    skill_status: str | None = None

    def __post_init__(self) -> None:
        _require(self.team_id, "team_id")


@dataclass(frozen=True)
class SkillCenterSkillPage:
    items: tuple[SkillCenterSkill, ...]
    total: int
    page_num: int
    page_size: int


@dataclass(frozen=True)
class SkillCenterTeamSkillPage:
    items: tuple[SkillCenterTeamSkill, ...]
    total: int
    page_num: int
    page_size: int


@dataclass(frozen=True)
class SkillCenterPublishSubmitRequest:
    team_id: str
    skill_code: str
    skill_name: str
    version_number: str
    package_url: str
    description: str | None = None
    icon_url: str | None = None
    tags: tuple[str, ...] = ()
    visibility: SkillCenterVisibility = SkillCenterVisibility.PRIVATE
    creator_name: str | None = None
    creator_work_no: str | None = None

    def __post_init__(self) -> None:
        _require(self.team_id, "team_id")
        _require(self.skill_code, "skill_code")
        _require(self.skill_name, "skill_name")
        _require(self.version_number, "version_number")
        _require(self.package_url, "package_url")
        if len(self.tags) > 1:
            raise ValueError("Skill Center publish accepts at most one tag")


@dataclass(frozen=True)
class SkillCenterPublishSubmission:
    skill_code: str
    version_number: str
    status: SkillCenterPublishSubmissionState
    external_request_id: str | None = None


@dataclass(frozen=True)
class SkillCenterPublishStatusRequest:
    team_id: str
    skill_code: str
    version_number: str

    def __post_init__(self) -> None:
        _require(self.team_id, "team_id")
        _require(self.skill_code, "skill_code")
        _require(self.version_number, "version_number")


@dataclass(frozen=True)
class SkillCenterCheckFinding:
    """One stable finding translated from an SC validation report."""

    name: str
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class SkillCenterStandardCheckResult:
    findings: tuple[SkillCenterCheckFinding, ...] = ()


@dataclass(frozen=True)
class SkillCenterSecurityCheckReport:
    risk_level: str | None = None
    findings: tuple[SkillCenterCheckFinding, ...] = ()


@dataclass(frozen=True)
class SkillCenterPublishStatus:
    skill_code: str
    version_number: str
    status: SkillCenterPublishState
    is_completed: bool
    is_success: bool
    message: str | None = None
    skill_name: str | None = None
    upstream_status: str | None = None
    status_description: str | None = None
    source: str | None = None
    released_at: str | None = None
    error_message: str | None = None
    standard_check_result: SkillCenterStandardCheckResult | None = None
    security_check_report: SkillCenterSecurityCheckReport | None = None

    @property
    def completed(self) -> bool:
        return self.is_completed

    @property
    def succeeded(self) -> bool:
        return self.is_success


@dataclass(frozen=True)
class SkillCenterVersionListRequest:
    skill_code: str
    scope: SkillCenterReadScope
    team_id: str | None = None

    def __post_init__(self) -> None:
        _require(self.skill_code, "skill_code")
        if self.scope is SkillCenterReadScope.TEAM:
            _require(self.team_id or "", "team_id")
        elif self.team_id is not None:
            raise ValueError("team_id must be omitted for PUBLIC reads")
        if self.team_id is not None:
            _require(self.team_id, "team_id")


@dataclass(frozen=True)
class SkillCenterVersion:
    version_number: str
    version_id: str | None = None
    sha256: str | None = None
    released_at: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        _require(self.version_number, "version_number")


@dataclass(frozen=True)
class SkillCenterMcpService:
    server_code: str
    name: str | None = None
    icon_url: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class SkillCenterExactDownloadRequest:
    skill_code: str
    version_number: str
    scope: SkillCenterReadScope
    team_id: str | None = None

    def __post_init__(self) -> None:
        _require(self.skill_code, "skill_code")
        _require(self.version_number, "version_number")
        if self.scope is SkillCenterReadScope.TEAM:
            _require(self.team_id or "", "team_id")
        elif self.team_id is not None:
            raise ValueError("team_id must be omitted for PUBLIC reads")
        if self.team_id is not None:
            _require(self.team_id, "team_id")


@dataclass(frozen=True)
class SkillCenterExactDownload:
    skill_code: str
    version_number: str
    download_url: str
    sha256: str
    office_download_url: str | None = None
    intranet_download_url: str | None = None
    mcp_services: tuple[SkillCenterMcpService, ...] = ()

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-fA-F]{64}", self.sha256) is None:
            raise ValueError("sha256 must be a 64-character hexadecimal digest")


@runtime_checkable
class SkillCenterGateway(Plugin, Protocol):
    """Transport/config/auth adapter boundary for the new SC lifecycle.

    Team catalogue and publication operations require a non-empty request-level
    ``team_id``. Version listing and exact download require an explicit
    ``PUBLIC`` or ``TEAM`` scope; Public Reference reads omit Team because
    ``skill_code`` is globally unique. Public market operations deliberately
    have no Team. Implementations issue at most one publish submission; this
    contract never chooses retry, Attempt, ``RESULT_UNKNOWN``, Version creation,
    or materialization policy.
    """

    def create_team(self, request: SkillCenterTeamCreateRequest) -> SkillCenterTeam: ...

    def get_team_by_ref(
        self, request: SkillCenterTeamLookupRequest
    ) -> SkillCenterTeam: ...

    def search_public_skills(
        self, request: SkillCenterPublicSkillSearchRequest
    ) -> SkillCenterSkillPage: ...

    def get_public_skill(
        self, request: SkillCenterPublicSkillDetailRequest
    ) -> SkillCenterSkill | None: ...

    def list_public_tags(self) -> tuple[SkillCenterTag, ...]: ...

    def list_team_skills(
        self, request: SkillCenterTeamSkillListRequest
    ) -> SkillCenterTeamSkillPage: ...

    def get_team_skill(
        self, request: SkillCenterTeamSkillDetailRequest
    ) -> SkillCenterTeamSkill | None: ...

    def submit_publish(
        self, request: SkillCenterPublishSubmitRequest
    ) -> SkillCenterPublishSubmission: ...

    def get_publish_status(
        self, request: SkillCenterPublishStatusRequest
    ) -> SkillCenterPublishStatus: ...

    def list_versions(
        self, request: SkillCenterVersionListRequest
    ) -> tuple[SkillCenterVersion, ...]: ...

    def get_exact_download(
        self, request: SkillCenterExactDownloadRequest
    ) -> SkillCenterExactDownload: ...
