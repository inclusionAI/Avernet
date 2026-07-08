"""SkillCenter 开放 API — 本地 mock 实现。

本地模式下不连接真实 SkillCenter，所有发布直接通过。
"""

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam

logger = get_logger()


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="no I/O",
)
class LocalSkillCenterClient(MockSeam, SkillCenterClient):
    """Mock 客户端：发布立即成功，用于本地开发调试。"""

    def upload_and_publish(self, payload: dict) -> dict:
        skill_code = payload.get("skillCode", "local-mock")
        logger.info("[LocalMock] upload_and_publish: %s", skill_code)
        return {
            "success": True,
            "data": {
                "skillCode": skill_code,
                "name": payload.get("skillName", ""),
                "status": "PUBLISHED",
                "statusDesc": "已发布(mock)",
                "source": "local",
                "version": payload.get("versionNumber", "1"),
            },
        }

    def query_publish_status(self, skill_code: str) -> dict:
        logger.info("[LocalMock] query_publish_status: %s", skill_code)
        return {
            "success": True,
            "data": {
                "skillCode": skill_code,
                "status": "PUBLISHED",
                "statusDesc": "已发布(mock)",
                "isCompleted": True,
                "isSuccess": True,
            },
        }

    def list_versions(self, skill_code: str) -> list[dict]:
        logger.info("[LocalMock] list_versions: %s", skill_code)
        return []

    def search_market_skills(
        self, keyword: str = "", tag: str = "", page: int = 1, page_size: int = 20,
        team_id: str | None = None,
    ) -> dict:
        logger.info("[LocalMock] search_market_skills: keyword=%s tag=%s team_id=%s", keyword, tag, team_id)
        return {"success": True, "data": [], "total": 0}

    def get_market_tags(self) -> list[dict]:
        logger.info("[LocalMock] get_market_tags")
        return []

    def get_download_url(self, skill_code: str, version_number: str) -> dict:
        logger.info("[LocalMock] get_download_url: %s v%s", skill_code, version_number)
        return {"success": True, "data": {"downloadUrl": "file:///local-mock/skill.zip"}}

    def delete_skill(self, skill_code: str) -> dict:
        logger.info("[LocalMock] delete_skill: %s", skill_code)
        return {"success": True, "message": "deleted(mock)"}

    def get_file_structure(self, skill_code: str, version: str = "") -> dict:
        logger.info("[LocalMock] get_file_structure: %s v%s", skill_code, version)
        return {
            "success": True,
            "data": {
                "name": skill_code,
                "type": "DIR",
                "description": None,
                "children": [
                    {"name": "SKILL.md", "type": "FILE", "description": "技能描述文件", "children": None},
                    {"name": "src", "type": "DIR", "description": None, "children": [
                        {"name": "main.py", "type": "FILE", "description": "主入口文件", "children": None},
                    ]},
                ],
            },
        }

    def get_file_content(self, skill_code: str, file_path: str, version: str = "") -> dict:
        logger.info("[LocalMock] get_file_content: %s %s v%s", skill_code, file_path, version)
        return {
            "success": True,
            "data": {
                "path": file_path,
                "name": file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path,
                "type": "FILE",
                "content": "# mock file content",
                "description": "",
            },
        }
