"""技能发布服务 — 状态机流转 + 打包上传 + SkillCenter 交互。

状态流转：
  DEVELOPING → PENDING → PUBLISHED / REJECTED
  REJECTED   → PENDING (重试发布)
  PUBLISHED  → OFFLINE (新版本发布成功时旧版本自动下线)
  OFFLINE    终态

升级发布流程：
  1. publish_upgrade: 只新建 v2 DEVELOPING，v1 保持 PUBLISHED（线上不断服）
  2. 用户编辑 v2 后调 publish: v2 → PENDING
  3. query_status 检测 v2 PUBLISHED → 自动把同 skill_uuid 的旧 PUBLISHED 行设为 OFFLINE
"""

import io
import time
import zipfile
from pathlib import Path

from injector import inject

from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.services.skill_propagation_service import (
    SkillPropagationService,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient

logger = get_logger()

# ── 状态机定义 ──────────────────────────────────────────────────────

VALID_TRANSITIONS: dict[str, set[str]] = {
    "DEVELOPING": {"PENDING"},
    "PENDING": {"PUBLISHED", "REJECTED"},
    "PUBLISHED": {"OFFLINE"},
    "REJECTED": {"PENDING"},
}

# SkillCenter 远程状态 → OCB 本地状态（None = 继续轮询）
SC_STATUS_MAP: dict[str, str | None] = {
    "PRE_RELEASING": None,
    "SECURITY_SCANNING": None,
    "SECURITY_CHECK_PASSED": None,
    "STANDARD_CHECK_PASSED": None,
    "PUBLISHED": "PUBLISHED",
    "SECURITY_CHECK_FAILED": "REJECTED",
    "STANDARD_CHECK_FAILED": "REJECTED",
}

# OSS 路径前缀
_OSS_PUBLISH_PREFIX = "skill-publish"
_SIGN_URL_EXPIRES = 7200  # 2 小时


class InvalidTransitionError(Exception):
    """非法状态转移。"""


class SkillPublishService:
    """技能发布核心业务。"""

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        skill_center_client: SkillCenterClient,
        oss_client: ObjectStoragePlugin,
        propagation_service: SkillPropagationService,
    ) -> None:
        """
        Args:
            skill_repo: SkillRepository Protocol 实现
            skill_center_client: SkillCenterClient Protocol 实现
            oss_client: ObjectStoragePlugin（在 DI 下始终绑定，prod 走 OSS，test 走 mock）
            propagation_service: 发布成功后通知运行时
        """
        self._skill_repo = skill_repo
        self._sc_client = skill_center_client
        self._oss = oss_client
        self._propagation = propagation_service

    # ── 公开方法 ────────────────────────────────────────────────────

    def publish(self, skill_id: str, user_id: str | None = None, zip_path: str | None = None, package_url: str | None = None, nick_name: str | None = None) -> dict:
        """触发发布（developing / rejected → pending）。

        流程：校验状态 → 优先使用已存 package_url，否则回退 ZIP 上传 / 本地打包 → 调用 SkillCenter → status=PENDING
        """
        skill = self._get_skill_or_raise(skill_id)
        current = (skill.get("status") or "DEVELOPING").upper()
        self._assert_transition(current, "PENDING")

        skill_code = skill.get("name", "")
        raw_version = str(skill.get("version") or "1")
        version = raw_version if "." in raw_version else f"{raw_version}.0.0"

        final_package_url = package_url or skill.get("package_url")
        if final_package_url:
            package_url = self._resign_if_oss_path(final_package_url)
        elif zip_path:
            package_url = self._upload_zip_file(skill, zip_path)
        else:
            package_url = self._pack_and_upload(skill)

        payload = {
            "skillCode": skill_code,
            "packageUrl": package_url,
            "versionNumber": version,
            "skillName": skill.get("name", ""),
            "description": skill.get("description", ""),
            "visibility": "INTERNAL",
        }
        if nick_name:
            payload["creatorNickName"] = nick_name
        if user_id:
            payload["creatorWorkNo"] = user_id
        result = self._sc_client.upload_and_publish(payload)

        if not result.get("success"):
            logger.error("SkillCenter upload_and_publish failed: %s", result)
            # 优先取已转换的 message（_transform_auth_fail 会改写 message 字段）
            msg = result.get("message") or result.get("errorMsg") or result.get("errorMessage") or result.get("error") or "发布请求失败"
            return {"success": False, "message": msg, "data": result}

        self._skill_repo.update(skill_id, {"status": "PENDING"})
        logger.info("Skill %s published → PENDING (skill_code=%s)", skill_id, skill_code)

        response_data = result.get("data", {}) or {}
        response_data.update({
            "package_url": skill.get("package_url") or package_url,
            "zip_url": skill.get("zip_url"),
        })
        return {
            "success": True,
            "message": "发布请求已提交，请轮询状态",
            "data": response_data,
        }

    def query_status(self, skill_id: str) -> dict:
        """轮询发布状态（仅 pending 可调用）。

        读取 skill_code → GET /upload/status → 映射状态 → 更新本地 status。
        """
        skill = self._get_skill_or_raise(skill_id)
        current = (skill.get("status") or "DEVELOPING").upper()
        if current != "PENDING":
            return {
                "success": False,
                "message": f"当前状态为 {current}，仅 PENDING 状态可轮询",
                "data": {"status": current},
            }

        skill_code = skill.get("name", "")
        result = self._sc_client.query_publish_status(skill_code)

        if not result.get("success"):
            logger.warning("query_publish_status failed for %s: %s", skill_code, result)
            return {"success": False, "message": "查询状态失败", "data": result}

        data = result.get("data", {})
        remote_status = data.get("status", "")
        local_status = SC_STATUS_MAP.get(remote_status)

        if local_status is not None:
            self._assert_transition(current, local_status)
            update_fields = {"status": local_status}
            if local_status == "PUBLISHED":
                skill_uuid = skill.get("skill_uuid")
                if skill_uuid:
                    update_fields["git_path"] = f"center://{skill_uuid}"
            self._skill_repo.update(skill_id, update_fields)
            logger.info("Skill %s status: %s → %s (remote=%s)", skill_id, current, local_status, remote_status)

            if local_status == "PUBLISHED":
                self._on_publish_success(skill)

        return {
            "success": True,
            "message": "已完成" if data.get("isCompleted") else "处理中",
            "data": {
                "skill_id": skill_id,
                "local_status": local_status or current,
                "remote_status": remote_status,
                "is_completed": data.get("isCompleted", False),
                "is_success": data.get("isSuccess", False),
                "error_msg": data.get("errorMsg"),
            },
        }

    def publish_upgrade(self, skill_id: str, user_id: str | None = None) -> dict:
        """创建升级草稿：保持 v1 PUBLISHED 不动，新建 v2 DEVELOPING。

        v1 → OFFLINE 延迟到 v2 发布成功时（_offline_old_versions）自动处理，
        避免用户只点了编辑却没发布导致线上断服。
        """
        skill = self._get_skill_or_raise(skill_id)
        current = (skill.get("status") or "DEVELOPING").upper()
        if current != "PUBLISHED":
            return {
                "success": False,
                "message": f"仅 PUBLISHED 状态可升级发布，当前: {current}",
            }

        skill_uuid = skill.get("skill_uuid")
        old_version = skill.get("version") or 1

        # 幂等校验：同 skill_uuid 下不允许存在多个非 PUBLISHED/OFFLINE 版本
        if skill_uuid:
            all_versions = self._skill_repo.list_skills()
            active_drafts = [
                s for s in all_versions
                if s.get("skill_uuid") == skill_uuid
                and str(s.get("id")) != str(skill.get("id"))
                and s.get("status", "DEVELOPING").upper() not in ("PUBLISHED", "OFFLINE", "DELETED")
            ]
            if active_drafts:
                draft = active_drafts[0]
                return {
                    "success": False,
                    "message": (
                        f"已存在未发布的升级版本 (id={draft.get('id')}, "
                        f"status={draft.get('status')}, version={draft.get('version')})，"
                        f"请先处理该版本"
                    ),
                }

            # 版本号：取同 skill_uuid 下最大 version + 1，避免重复
            existing_versions = [
                (s.get("version") or 1) for s in all_versions
                if s.get("skill_uuid") == skill_uuid
            ]
            new_version = (max(existing_versions) if existing_versions else old_version) + 1
        else:
            new_version = old_version + 1

        # git_path：PUBLISHED 版本已被改为 center://<uuid>，新 DEVELOPING 版本不应使用
        # center:// 指向 SkillCenter 同步产物，而 DEVELOPING 版本需要指向本地可编辑目录
        git_path = skill.get("git_path", "")
        if git_path.startswith("center://"):
            git_path = ""

        new_skill_data = {
            "name": skill.get("name"),
            "description": skill.get("description"),
            "git_path": git_path,
            "link_name": skill.get("link_name"),
            "category": skill.get("category"),
            "tags": skill.get("tags"),
            "input_schema": skill.get("input_schema"),
            "output_schema": skill.get("output_schema"),
            "is_public": skill.get("is_public", False),
            "is_builtin": skill.get("is_builtin", False),
            "user_id": skill.get("user_id"),
            "risk_tags": skill.get("risk_tags"),
            "mcp_dependencies": skill.get("mcp_dependencies"),
            "bolt_id": skill.get("bolt_id", "default"),
            "status": "DEVELOPING",
            "version": new_version,
            "skill_uuid": skill_uuid,
            "source_type": skill.get("source_type"),
            "category_path": skill.get("category_path"),
            "package_url": skill.get("package_url"),
            "zip_url": skill.get("zip_url"),
        }

        new_skill = self._skill_repo.create(new_skill_data)
        if not new_skill:
            return {"success": False, "message": "创建新版本行失败"}

        new_id = new_skill.get("id")
        logger.info(
            "Skill upgrade draft: %s (v%s stays PUBLISHED) → %s (v%s DEVELOPING), skill_uuid=%s",
            skill_id, old_version, new_id, new_version, skill_uuid,
        )

        return {
            "success": True,
            "message": f"升级草稿已创建 v{new_version}，旧版本 v{old_version} 保持在线",
            "data": {
                "old_skill_id": skill_id,
                "old_status": "PUBLISHED",
                "new_skill_id": str(new_id),
                "new_version": new_version,
                "new_status": "DEVELOPING",
            },
        }

    def list_versions(self, skill_id: str) -> list[dict]:
        """获取技能在 SkillCenter 的版本列表。"""
        skill = self._get_skill_or_raise(skill_id)
        skill_code = skill.get("name", "")
        return self._sc_client.list_versions(skill_code)

    # ── 内部方法 ────────────────────────────────────────────────────

    def _on_publish_success(self, skill: dict) -> None:
        """发布成功后的后处理：下线旧版本 + 传播通知。

        当 version > 1（升级发布）时：
        1. 把同 skill_uuid 下其他 PUBLISHED 行设为 OFFLINE
        2. 调用 propagate_on_upgrade 通知运行时热更新
        """
        version = skill.get("version") or 1
        skill_uuid = skill.get("skill_uuid")
        skill_id = str(skill.get("id"))

        if version > 1 and skill_uuid:
            self._offline_old_versions(skill_id, skill_uuid, skill.get("bolt_id"))

        if not self._propagation:
            return

        if not skill_uuid:
            logger.warning(
                "Skill %s missing skill_uuid, skip propagation", skill_id,
            )
            return

        env = self._get_env()
        try:
            result = self._propagation.propagate_on_upgrade(
                skill_uuid=skill_uuid,
                env=env,
                new_version=str(version),
            )
            logger.info(
                "propagate_on_upgrade done: skill_uuid=%s env=%s v=%s "
                "affected=%d success=%d failed=%s log_id=%s",
                skill_uuid, env, version,
                result.affected_bot_count, result.success_bot_count,
                result.failed_bot_ids, result.propagation_log_id,
            )
        except Exception as exc:
            logger.exception(
                "propagate_on_upgrade failed (non-blocking): skill_uuid=%s exc=%s",
                skill_uuid, exc,
            )

    def _offline_old_versions(
        self, current_skill_id: str, skill_uuid: str, bolt_id: str | None,
    ) -> None:
        """把同 skill_uuid 下其他 PUBLISHED 行设为 OFFLINE。"""
        try:
            all_skills = self._skill_repo.list_skills(bolt_id=bolt_id or "default")
            for s in all_skills:
                if (
                    str(s.get("id")) != current_skill_id
                    and s.get("skill_uuid") == skill_uuid
                    and (s.get("status") or "").upper() == "PUBLISHED"
                ):
                    self._skill_repo.update(str(s["id"]), {"status": "OFFLINE"})
                    logger.info(
                        "Old version offlined: id=%s v=%s (replaced by %s)",
                        s["id"], s.get("version"), current_skill_id,
                    )
        except Exception as exc:
            logger.exception(
                "Failed to offline old versions (non-blocking): skill_uuid=%s exc=%s",
                skill_uuid, exc,
            )

    def propagate_on_removal(self, skill_uuid: str) -> None:
        """技能删除后通知运行时清理。调用方需传入 skill_uuid（DB 删除后无法再查）。"""
        if not self._propagation:
            return

        if not skill_uuid:
            logger.warning("propagate_on_removal called with empty skill_uuid, skip")
            return

        env = self._get_env()
        try:
            result = self._propagation.propagate_on_removal(
                skill_uuid=skill_uuid,
                env=env,
            )
            logger.info(
                "propagate_on_removal done: skill_uuid=%s env=%s "
                "affected=%d success=%d failed=%s log_id=%s",
                skill_uuid, env,
                result.affected_bot_count, result.success_bot_count,
                result.failed_bot_ids, result.propagation_log_id,
            )
        except Exception as exc:
            logger.exception(
                "propagate_on_removal failed (non-blocking): skill_uuid=%s exc=%s",
                skill_uuid, exc,
            )

    @staticmethod
    def _get_env() -> str:
        """读取当前运行环境标识。"""
        from agentclaw.community.utils.env_utils import get_current_env
        return get_current_env()

    def _get_skill_or_raise(self, skill_id: str) -> dict:
        skill = self._skill_repo.get_by_id(skill_id)
        if not skill:
            raise ValueError(f"Skill {skill_id} not found")
        return skill

    def _assert_transition(self, current: str, target: str) -> None:
        allowed = VALID_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidTransitionError(f"非法状态转移: {current} → {target}")

    def _resign_if_oss_path(self, url: str) -> str:
        """如果是 OSS 相对路径则重新签名，已是完整 URL 则直接返回。"""
        if url.startswith(("http://", "https://", "file://")):
            return url
        signed = self._oss.sign_url(url, _SIGN_URL_EXPIRES)
        logger.info("Re-signed OSS path: %s → %s", url, signed[:80])
        return signed

    @staticmethod
    def _resolve_skill_dir(git_path: str) -> Path:
        """将 git_path 解析为实际磁盘路径。

        协议格式：
          git://category/sub/skill  → {skills-repo}/category/sub/skill
          local://skill-name        → {skills-local}/skill-name  (仅 local 模式)
          /absolute/path            → 直接使用
          空                        → Path("")
        """
        from agentclaw.community.core.workspace.path_factory import get_global_skills_repo_dir

        if not git_path:
            return Path("")
        if git_path.startswith("git://"):
            candidate = get_global_skills_repo_dir() / git_path[6:]
            if candidate.is_dir():
                return candidate
            return Path(git_path[6:])
        if git_path.startswith("local://"):
            remainder = git_path[8:]
            if remainder.startswith("/"):
                return Path(remainder)
            local_dir = Path.home() / ".openclaw" / "workspace" / "skills" / "skills-local" / remainder
            if local_dir.is_dir():
                return local_dir
            return Path(remainder)
        return Path(git_path)

    def _upload_zip_file(self, skill: dict, zip_path: str) -> str:
        """读取前端传来的 ZIP 文件 → 上传 OSS → 返回签名 URL。"""
        path = Path(zip_path)
        if not path.is_file() or path.suffix != ".zip":
            raise FileNotFoundError(f"ZIP 文件不存在或格式错误: {zip_path}")

        skill_name = skill.get("name") or skill.get("link_name") or "unknown"
        raw_ver = str(skill.get("version") or "1")
        version = raw_ver if "." in raw_ver else f"{raw_ver}.0.0"
        ts = str(int(time.time()))
        oss_path = f"{_OSS_PUBLISH_PREFIX}/{skill_name}/{ts}/{version}.zip"

        zip_bytes = path.read_bytes()
        self._oss.put_object(oss_path, zip_bytes)
        signed_url = self._oss.sign_url(oss_path, _SIGN_URL_EXPIRES)

        logger.info("Skill %s zip uploaded from %s: %s (%d bytes)", skill_name, zip_path, oss_path, len(zip_bytes))
        return signed_url

    def _pack_and_upload(self, skill: dict) -> str:
        """打包技能目录为 ZIP → 上传 OSS → 返回签名 URL。"""
        skill_name = skill.get("name") or skill.get("link_name") or "unknown"
        raw_ver = str(skill.get("version") or "1")
        version = raw_ver if "." in raw_ver else f"{raw_ver}.0.0"
        ts = str(int(time.time()))
        oss_path = f"{_OSS_PUBLISH_PREFIX}/{skill_name}/{ts}/{version}.zip"

        git_path = skill.get("git_path", "")
        # ``_resolve_skill_dir`` maps an empty ``git_path`` to ``Path("")``, which
        # normalises to ``PosixPath('.')`` — the process CWD. ``is_dir()`` says True
        # for it, so the fail-fast guard below waves it through and the zip loop
        # packs the entire working directory. A skill with no ``git_path`` has
        # nothing to publish; reject it before resolving.
        if not git_path:
            raise FileNotFoundError("技能目录不存在: git_path 为空，无法打包发布")

        base = self._resolve_skill_dir(git_path)

        if not base.is_dir():
            raise FileNotFoundError(
                f"技能目录不存在: {base} (git_path={git_path})，无法打包发布"
            )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in base.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(base))

        zip_bytes = buf.getvalue()
        self._oss.put_object(oss_path, zip_bytes)
        signed_url = self._oss.sign_url(oss_path, _SIGN_URL_EXPIRES)

        logger.info("Skill %s packed and uploaded: %s (%d bytes)", skill_name, oss_path, len(zip_bytes))
        return signed_url
