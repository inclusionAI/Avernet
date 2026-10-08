"""SkillCenter 开放 API 客户端协议。

对接 SkillCenter 的核心接口：
- POST /api/v1/skills/upload/publish   （上传发布）
- GET  /skillcenter/api/v1/skills/upload/status/{skillCode} （查询状态）
- GET  /api/v1/skills/{skillCode}/versions      （版本列表）
- GET  /api/v1/skills/market/search             （市场搜索）
- GET  /api/v1/skills/market/tags               （市场标签）
- GET  /api/v1/skills/{skillCode}/versions/{ver}/download （版本下载）
"""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


class SkillCenterTeamCreateError(RuntimeError):
    """Raised when Skill Center rejects or cannot complete team creation."""


@dataclass(frozen=True)
class SkillCenterTeamCreateRequest:
    """Transport-neutral data required to mirror one OCB Space to SC."""

    team_code: str
    team_name: str
    ref_source_id: str
    description: str | None = None
    icon: str | None = None
    ref_source_platform: str | None = None


@dataclass(frozen=True)
class SkillCenterTeamCreateResult:
    """Confirmed SC team identity returned after a successful creation."""

    team_id: str


class SkillCenterTeamQueryError(RuntimeError):
    """Raised when an SC team lookup fails or returns invalid data."""


@dataclass(frozen=True)
class SkillCenterTeamQueryRequest:
    """Transport-neutral identity used to find an SC team mapping."""

    source: str
    ref_source_id: str


@dataclass(frozen=True)
class SkillCenterTeamQueryResult:
    """SC team identity resolved from ``source`` and ``ref_source_id``."""

    team_id: str


class SkillCenterMarketSearchError(RuntimeError):
    """Raised when Skill Center market search fails or returns invalid data."""


class SkillCenterPublishStatusError(RuntimeError):
    """Raised when Skill Center publish-status lookup fails or is malformed."""


@dataclass(frozen=True)
class SkillCenterMarketSearchRequest:
    """Transport-neutral Skill Center market search request.

    ``appKey`` and ``source`` are deployment configuration and deliberately do
    not appear here. ``team_id`` is retained for trusted internal callers; the
    public OPEN adapter always sends ``None`` and forces ``PUBLIC`` access.
    """

    keyword: str | None = None
    page_num: int = 1
    page_size: int = 20
    is_official: bool | None = None
    is_recommended: bool | None = None
    tag_list: tuple[str, ...] = ()
    sort_by: str | None = None
    creator_name: str | None = None
    creator_work_no: str | None = None
    team_id: str | None = None
    access_level: str | None = None
    belong_to: str | None = None


@dataclass(frozen=True)
class SkillCenterMarketSearchResult:
    """Validated page returned by Skill Center market search."""

    total: int
    items: tuple[dict[str, Any], ...]


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

    def get_team_by_ref_source(
        self, request: SkillCenterTeamQueryRequest
    ) -> SkillCenterTeamQueryResult | None:
        """Find an SC team by its external source identity.

        ``None`` means that SC has no matching team. Upstream failures or
        malformed responses raise :class:`SkillCenterTeamQueryError`.
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
        The implementation calls ``GET /skillcenter/api/v1/skills/upload/status/{skillCode}``
        and injects ``code``/``source`` from deployment configuration.

        Returns:
            SkillCenter response envelope containing ``data`` with the publish
            status fields. Upstream failures or malformed responses raise
            :class:`SkillCenterPublishStatusError`.
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
        self, request: SkillCenterMarketSearchRequest
    ) -> SkillCenterMarketSearchResult:
        """Search Skill Center and return a validated, transport-neutral page.

        Implementations inject ``appKey`` and ``source`` from deployment
        configuration. Rejected, unavailable, or malformed upstream responses
        raise :class:`SkillCenterMarketSearchError`; they must not be converted
        into an empty successful page.
        """
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
