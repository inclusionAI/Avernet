"""SkillCenter 开放 API 客户端协议。

对接 SkillCenter 的核心接口：
- POST /api/v1/skills/upload/publish   （上传发布）
- GET  /api/v1/skills/upload/status/{skillCode} （查询状态）
- GET  /api/v1/skills/{skillCode}/versions      （版本列表）
- GET  /api/v1/skills/market/search             （市场搜索）
- GET  /api/v1/skills/market/tags               （市场标签）
- GET  /api/v1/skills/{skillCode}/versions/{ver}/download （版本下载）
"""

from typing import Protocol, runtime_checkable
from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class SkillCenterClient(Plugin, Protocol):
    """SkillCenter 开放 API 客户端。"""

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
        self, keyword: str = "", tag: str = "", page: int = 1, page_size: int = 20,
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

    def get_file_content(self, skill_code: str, file_path: str, version: str = "") -> dict:
        """获取技能指定文件内容。

        Args:
            skill_code: 技能编码。
            file_path: 文件相对路径，如 ``src/main.py``。
            version: 版本号（可选，默认最新版本）。
        Returns:
            SkillCenter 响应 dict，含 data.path / data.content 等。
        """
        ...
