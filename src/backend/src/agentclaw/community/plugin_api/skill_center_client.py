"""Legacy SkillCenter Client and the Q5 SkillCenter Gateway Plugin APIs."""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


class SkillCenterTeamCreateError(RuntimeError):
    """Raised when Skill Center rejects or cannot complete team creation."""


@dataclass(frozen=True)
class SkillCenterTeamCreateRequest:
    """Transport-neutral data required to mirror one OCB Space to SC."""

    team_code: str
    team_name: str
    ref_source_id: str
    ref_source_platform: str
    description: str | None = None
    icon: str | None = None


@dataclass(frozen=True)
class SkillCenterTeamCreateResult:
    """Confirmed SC team identity returned after a successful creation."""

    team_id: int
    team_code: str
    team_name: str
    ref_source_platform: str
    ref_source_id: str


@dataclass(frozen=True)
class SkillCenterTeamSkillPage:
    """One page of the official SC Team Skill listing."""

    items: list[dict]
    total: int


@runtime_checkable
class SkillCenterClient(Plugin, Protocol):
    """SkillCenter 开放 API 客户端。"""

    def create_team(
        self, request: SkillCenterTeamCreateRequest
    ) -> SkillCenterTeamCreateResult:
        """Create the SC team corresponding to an OCB team Space.

        Implementations obtain endpoint, appKey and source from deployment
        configuration. A failed or rejected creation raises
        :class:`SkillCenterTeamCreateError`; it must not return a false-success
        result.
        """
        ...

    def upload_and_publish(self, payload: dict) -> dict:
        """上传并发布技能（异步，返回后需轮询状态）。

        Args:
            payload: 包含 skillCode, packageUrl, versionNumber, skillName 等字段。
        Returns:
            SkillCenter 响应 dict，含 success + data（skillCode, status, ...）。
        """
        ...

    def query_publish_status(self, skill_code: str) -> dict:
        """查询技能发布状态。

        Args:
            skill_code: 技能编码（userProvidedSkillId）。
        Returns:
            SkillCenter 响应 dict，含 data.status / isCompleted / isSuccess 等。
        """
        ...

    def list_versions(self, skill_code: str) -> list[dict]:
        """获取技能所有已发布版本。

        Args:
            skill_code: 技能编码。
        Returns:
            版本列表，每项含 versionId, versionNumber, releasedAt, note, sha256。
        """
        ...

    def search_market_skills(
        self,
        keyword: str = "",
        tag: str = "",
        page: int = 1,
        page_size: int = 20,
        team_id: str | None = None,
    ) -> dict:
        """搜索公开市场技能。"""
        ...

    def get_market_tags(self) -> list[dict]:
        """获取市场标签列表。"""
        ...

    def get_download_url(self, skill_code: str, version_number: str) -> dict:
        """获取指定版本的下载链接。"""
        ...

    def delete_skill(self, skill_code: str) -> dict:
        """删除技能（逻辑删除）。

        Returns:
            SkillCenter 响应 dict，含 success + message。
        """
        ...

    def get_file_structure(self, skill_code: str, version: str = "") -> dict:
        """获取技能文件结构树。

        Args:
            skill_code: 技能编码。
            version: 版本号（可选，默认最新版本）。
        Returns:
            SkillCenter 响应 dict，含 data（文件树结构）。
        """
        ...

    def get_file_content(
        self, skill_code: str, file_path: str, version: str = ""
    ) -> dict:
        """获取技能指定文件内容。

        Args:
            skill_code: 技能编码。
            file_path: 文件相对路径，如 ``src/main.py``。
            version: 版本号（可选，默认最新版本）。
        Returns:
            SkillCenter 响应 dict，含 data.path / data.content 等。
        """
        ...


class SkillCenterGatewayErrorCode(str, Enum):
    """Stable errors a Q5 gateway exposes without leaking an SC SDK or HTTP."""

    BUSINESS = "business_error"
    TIMEOUT = "timeout"
    UNKNOWN_RESPONSE = "unknown_response"
    PROTOCOL = "protocol_error"
    UNAVAILABLE = "unavailable"


class SkillCenterGatewayError(RuntimeError):
    """Normalized Q5 gateway error; retry and Attempt decisions stay upstream."""

    def __init__(self, code: SkillCenterGatewayErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class SkillCenterGateway(Plugin, Protocol):
    """Team-scoped Q5 API for the new Space/Center lifecycle.

    Each operation carrying a Skill identity requires its resolved SC ``team_id``
    as a request argument. This Protocol never selects a default Team and never
    retries a POST; its caller owns Attempt and ``RESULT_UNKNOWN`` handling.
    """

    def create_team(self, request: SkillCenterTeamCreateRequest) -> SkillCenterTeamCreateResult:
        """Create a Team using the official SC Team-create request DTO."""
        ...

    def get_team_by_ref_source(
        self, *, ref_source_platform: str, ref_source_id: str
    ) -> SkillCenterTeamCreateResult:
        """Confirm a TC→SC Team mapping after an unknown create outcome."""
        ...

    def list_team_skills(
        self,
        *,
        team_id: int,
        keyword: str = "",
        page_num: int = 1,
        page_size: int = 20,
    ) -> SkillCenterTeamSkillPage:
        """List one Team's Skills; callers must provide the explicit SC teamId."""
        ...

    def upload_and_publish(self, payload: dict, *, team_id: str) -> dict:
        """Submit exactly one publish POST; this method never retries it."""
        ...

    def query_publish_status(self, skill_code: str, *, team_id: str) -> dict:
        """Read the status for one immutable SC skill identity."""
        ...

    def get_skill_detail(self, skill_code: str, *, team_id: str) -> dict:
        """Read metadata by immutable ``skillCode``."""
        ...

    def list_versions(self, skill_code: str, *, team_id: str) -> list[dict]:
        """List versions for one immutable SC skill identity."""
        ...

    def get_download_url(
        self, skill_code: str, version_number: str, *, team_id: str
    ) -> dict:
        """Resolve one exact ``skillCode + versionNumber`` download."""
        ...

    def search_market_skills(
        self, keyword: str = "", tag: str = "", page: int = 1, page_size: int = 20
    ) -> dict:
        """Search the public market; it is not Space-Team scoped."""
        ...

    def get_market_tags(self) -> list[dict]:
        """List public-market tags."""
        ...
