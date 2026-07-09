"""软链验证 API — 通过 Arca 远程验证容器内 symlink 状态。

GET /api/v1/skill-center/verify-symlinks
Query params:
  - bot_id: str (required)
  - entity_type: str (default: "staff")
  - env: str (default: from request context)

Response: VerifyReport JSON
"""

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.skill_center.services.repositories import SkillRepository
from agentclaw.community.core.skill_center.services.skill_symlink_verify_service import (
    SkillSymlinkVerifyService,
)
from agentclaw.community.di import Injected
from agentclaw.community.di.modules.skill_center_module import DeviceFilesystemDispatcher
from agentclaw.community.log import get_logger


logger = get_logger()
router = APIRouter(prefix="/api/v1/skill-center", tags=["skill-center"])


class VerifySymlinksResponse(BaseModel):
    bot_id: str
    env: str
    total: int
    passed: int
    failed: int
    failures: list[dict]


@router.get("/verify-symlinks", response_model=VerifySymlinksResponse)
async def verify_symlinks(
    bot_id: str = Query(..., description="Bot ID"),
    entity_type: str = Query("staff", description="Entity type (staff/proj/team)"),
    env: str = Query(None, description="环境，不传则自动从服务器读取 (dev/pre/prod)"),
    request: Request = None,
    skill_repo: SkillRepository = Injected(SkillRepository),
    bot_repo: BotRepository = Injected(BotRepository),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(DeviceFilesystemDispatcher),
):
    """验证指定 bot 的所有 active skill 软链状态。

    通过设备文件系统 seam 远程检查容器内 symlink 名称存在 + NAS 目标存在，
    返回验证报告 JSON。

    验证失败不自动修复，只报告问题。
    """
    from agentclaw.community.utils.env_utils import get_current_env

    env = env or get_current_env()
    owner_id = getattr(request.state, "user_id", None) or "default"

    logger.info("[verify_symlinks] bot_id=%s entity_type=%s env=%s", bot_id, entity_type, env)

    svc = SkillSymlinkVerifyService(
        skill_repo=skill_repo, device_fs_dispatcher=device_fs_dispatcher, bot_repo=bot_repo
    )
    report = await svc.verify_bot(bot_id=bot_id, entity_type=entity_type, owner_id=owner_id, env=env)

    logger.info(
        "[verify_symlinks] done: bot_id=%s total=%d passed=%d failed=%d",
        bot_id, report.total, report.passed, report.failed,
    )

    return VerifySymlinksResponse(
        bot_id=report.bot_id,
        env=report.env,
        total=report.total,
        passed=report.passed,
        failed=report.failed,
        failures=[
            f if isinstance(f, dict) else {
                "skill_id": f.skill_id,
                "skill_name": f.skill_name,
                "git_path": f.git_path,
                "link_name": f.link_name,
                "reason": f.reason,
                "expected": f.expected,
                "actual": f.actual,
            }
            for f in report.failures
        ],
    )
