"""SkillSymlinkVerifyService — 远程验证容器内软链状态。

通过 Arca API 验证：
1. 容器内 skills/ 目录下 symlink 名称存在
2. NAS skills-center/<uuid>/ 目标目录存在

不验证 symlink target（用户确认 symlink 名称存在即可）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from agentclaw.community.log import get_logger


logger = get_logger()


@dataclass
class VerifyFailure:
    """单条软链验证失败详情。"""
    skill_id: str
    skill_name: str
    git_path: str
    link_name: str
    reason: str  # symlink_not_found | nas_target_missing | arca_error
    expected: str | None = None
    actual: str | None = None


@dataclass
class VerifyReport:
    """软链验证报告。"""
    bot_id: str
    env: str
    total: int
    passed: int
    failed: int
    failures: list[VerifyFailure] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "bot_id": self.bot_id,
            "env": self.env,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "failures": [asdict(f) for f in self.failures],
        }


class SkillSymlinkVerifyService:
    """通过 Arca API 远程验证容器内软链状态。"""

    SKILLS_DIR_IN_CONTAINER = "/home/admin/.openclaw/workspace/skills"
    SKILLS_CENTER_IN_NAS = "/home/admin/.openclaw/workspace/skills-center"

    def __init__(self, skill_repo, device_fs_dispatcher, bot_repo):  # type: ignore[no-untyped-def]
        self._skill_repo: Any = skill_repo
        self._device_fs_dispatcher: Any = device_fs_dispatcher
        self._bot_repo: Any = bot_repo

    async def verify_bot(
        self,
        bot_id: str,
        entity_type: str,
        owner_id: str,
        env: str,
    ) -> VerifyReport:
        failures: list[VerifyFailure] = []

        # 1. 从 DB 读 active skills
        try:
            skills = self._skill_repo.get_active_skills_by_bot(bot_id, entity_type, owner_id, env)
        except Exception as exc:
            logger.error("[verify] failed to get active skills: bot_id=%s exc=%s", bot_id, exc)
            return VerifyReport(
                bot_id=bot_id, env=env, total=0, passed=0, failed=0,
                failures=[VerifyFailure(
                    skill_id="", skill_name="", git_path="",
                    link_name="", reason="db_error", actual=str(exc)[:100],
                )],
            )

        if not skills:
            return VerifyReport(bot_id=bot_id, env=env, total=0, passed=0, failed=0)

        # 2. 列出容器内 symlinks（经设备文件系统 seam，sandbox 由 dispatcher 内化）
        sandbox_id = await self._get_sandbox_id(bot_id, owner_id)
        if not sandbox_id:
            return VerifyReport(
                bot_id=bot_id, env=env, total=len(skills), passed=0, failed=len(skills),
                failures=[VerifyFailure(
                    skill_id="", skill_name="", git_path="", link_name="",
                    reason="sandbox_not_found",
                )],
            )

        device_fs = self._device_fs_dispatcher.for_bot(bot_id, owner_id)
        symlinks_in_container = await device_fs.list_dir(self.SKILLS_DIR_IN_CONTAINER)
        symlink_names: set[str] = set()
        if symlinks_in_container:
            symlink_names = {f["name"] for f in symlinks_in_container if f.get("is_link") and f.get("name")}
        else:
            logger.warning("[verify] list_arca_directory returned None: bot_id=%s sandbox=%s", bot_id, sandbox_id)

        # 3. 逐条验证
        for skill in skills:
            skill_id = str(skill.get("id", ""))
            skill_name = skill.get("name", "")
            git_path = skill.get("git_path", "")
            link_name = skill.get("link_name", skill_name)

            if link_name not in symlink_names:
                failures.append(VerifyFailure(
                    skill_id=skill_id, skill_name=skill_name, git_path=git_path,
                    link_name=link_name, reason="symlink_not_found", expected=link_name,
                ))
                continue

            if git_path.startswith("center://"):
                # center:// 前缀长度为 9，提取 uuid
                # 软链 source 真实路径：<SKILLS_CENTER_IN_NAS>/<uuid>/current/<skill_name>
                uuid = git_path[9:]
                nas_target = f"{self.SKILLS_CENTER_IN_NAS}/{uuid}/current/{skill_name}"
                exists = await device_fs.exists(nas_target)
                if not exists:
                    failures.append(VerifyFailure(
                        skill_id=skill_id, skill_name=skill_name, git_path=git_path,
                        link_name=link_name, reason="nas_target_missing", expected=nas_target,
                    ))
            elif git_path.startswith("git://"):
                pass  # git:// 目标在容器内，不单独验证 NAS

        passed = len(skills) - len(failures)
        return VerifyReport(
            bot_id=bot_id, env=env, total=len(skills),
            passed=passed, failed=len(failures), failures=failures,
        )

    async def _get_sandbox_id(self, bot_id: str, owner_id: str) -> str | None:
        try:
            from agentclaw.community.core.devices.services import device_info
            provider, sandbox_id = device_info.get_device_info_by_bot_id(bot_id, self._bot_repo)
            return sandbox_id if provider == "arca" else None
        except Exception as exc:
            logger.warning("[verify] get_device_info_by_bot_id failed: bot_id=%s %s", bot_id, exc)
            return None
