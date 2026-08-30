"""``ManagedDeployConfigComposer`` — the deployment this platform ships today.

The managed bot image lays four scripts under ``/home/admin/bin`` and expects
them chained in order: bootstrap compensation, engine install, the start
service, then the watchdog. Its NAS holds the system directory, the bot's data
directory and the skills repo, and every bot gets a storage volume — the
sessions directory, or the home directory for bots the ``nas_mount`` whitelist
has moved.

None of that is a platform invariant; all of it is this image's layout. The
bodies below are the ones that lived on ``BaasService`` before the composer
seam existed, moved unchanged so the payload every running bot was started with
stays byte-identical (pinned by
``tests/.../test_baas_service_deploy_config_golden.py``).

Two deliberate differences from the pre-move code, both consequences of the
seam rather than changes of behavior:

* ``mount_home_dir_storage`` is a required ``bool`` instead of a tri-state
  ``bool | None``. ``BaasService`` resolves the whitelist once and puts the
  answer in ``BotDeployContext``, so the "``None`` ⇒ go ask the whitelist"
  branches these methods used to carry are gone — along with the two extra
  whitelist reads one payload could trigger.
* ``_setup_directory`` no longer takes ``owner_id``: it read it only to guard
  that whitelist call.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, Optional

from agentclaw.community.core.bot_management.services.engine_resolver import (
    resolve_runtime_engine_for_bot,
)
from agentclaw.community.core.devices.protocols import StoragePathProtocol
from agentclaw.community.core.service_bot.services.deploy.deploy_config_composer import (
    BotDeployContext,
    DeployConfigComposer,
)
from agentclaw.community.core.service_bot.services.deploy.deploy_models import (
    MountPermission,
    MountPointEntry,
    Storage,
    StorageType,
)
from agentclaw.community.core.service_bot.services.deploy.service_skills_manifest import (
    frozen_center_delivery_from_ext,
)
from agentclaw.community.core.service_bot.types import PublishStage, is_editable_bot
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.core.workspace.engine_sandbox import (
    EngineSandboxProvider,
    EngineSandboxRegistry,
)
from agentclaw.community.kernel.deploy_runtime import DeployRuntime
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.bot import BotRepository

logger = get_logger()


class ManagedDeployConfigComposer(DeployConfigComposer):
    """Compose the create-bot payload for the managed bot image."""

    def __init__(
        self,
        *,
        storage_path: StoragePathProtocol,
        sandbox_registry: EngineSandboxRegistry,
        bot_repo: "BotRepository",
    ) -> None:
        self._storage_path = storage_path
        self._sandbox_registry = sandbox_registry
        # Read-only rules are per-bot: the defaults come from the engine's
        # sandbox provider, the overrides from ``ac_bots.ext.read_only_rules``.
        self._bot_repo = bot_repo

    @property
    def name(self) -> DeployRuntime:
        return DeployRuntime.MANAGED

    # ── DeployConfigComposer ────────────────────────────────────────────

    def build_start_command(self, ctx: BotDeployContext) -> str:
        """Chain the managed image's four boot scripts with ``&&``.

        Every step must succeed for the next to run, and the whole chain's exit
        status is what drives the device to ACTIVE or FAILED upstream.
        """
        # 1、Bootstrap 补偿脚本
        bootstrap_cmp = self._get_bootstrap_cmp()

        # 2、安装引擎
        install_engine_cmd = self._get_install_engine_cmd()

        # 3、 启动同步服务
        start_cmd = self._get_start_sandbox_service_cmd(
            ctx.engine,
            ctx.migration_path,
            ctx.bot_type,
            ctx.bot_id,
            ctx.owner_id,
            ctx.entity_id,
            ctx.entity_type,
            ctx.stage,
            ctx.version,
            ctx.mount_home_dir_storage,
            ctx.ext_info,
        )

        # 4、 Start watchdog
        watchdog_cmd = self._get_start_watchdog_cmd()

        return (
            f"{bootstrap_cmp} && ({install_engine_cmd}) && "
            f" {start_cmd} && {watchdog_cmd}"
        )

    def build_mount_points(self, ctx: BotDeployContext) -> list[MountPointEntry]:
        return self._setup_directory(
            entity_id=ctx.entity_id,
            entity_type=ctx.entity_type,
            bot_id=ctx.bot_id,
            engine_type=ctx.engine,
            mount_path=ctx.mount_path,
            mount_home_dir_storage=ctx.mount_home_dir_storage,
            ext_info=ctx.ext_info,
        )

    def build_storage(self, ctx: BotDeployContext) -> Storage | None:
        """Always a volume: every managed bot keeps its state on NAS."""
        return self._setup_bot_storage(
            entity_id=ctx.entity_id,
            entity_type=ctx.entity_type,
            owner_id=ctx.owner_id,
            bot_id=ctx.bot_id,
            engine_type=ctx.engine,
            mount_home_dir_storage=ctx.mount_home_dir_storage,
            bot_type=ctx.bot_type,
            stage=ctx.stage or "",
        )

    # ── the managed image's own composition ─────────────────────────────

    def _get_bootstrap_cmp(self):
        """执行 bootstrap 补偿脚本。"""
        return "su admin -c 'bash /home/admin/bin/bootstrap_minimal.sh'"

    def _get_setup_sync_service_cmd(self, engine: str = ""):
        """执行 setup_supervisor_sync_service.sh 脚本。"""
        setup_cmd = f"bash /home/admin/bin/setup_supervisor_sync_service.sh {engine}"
        logger.info(f"[_get_setup_sync_service_cmd] Executing cmd: {setup_cmd}")
        return setup_cmd

    def _get_install_engine_cmd(self):
        """执行 install_engine.sh 脚本。

        install_engine.sh 是从 start_service.sh 拆分出来的，
        负责安装/更新引擎二进制并落盘 marker 文件，setup_supervisor_sync_service.sh
        会等待该 marker 才继续。BaaS 路径通过 && 串联各步，install_engine
        同步执行即可，无需 nohup；存在性保护用于 backend 先发版而 daas-script
        旧镜像尚无该脚本的窗口。
        """
        script_path = "/home/admin/bin/install_engine.sh"
        log_path = "/home/admin/logs/install_engine.log"
        install_cmd = (
            f"if [ -f {script_path} ]; then "
            f"bash {script_path} >> {log_path} 2>&1; "
            f"else echo '[install_engine] {script_path} not found, skip'; fi"
        )
        logger.info(f"[_get_install_engine_cmd] Executing cmd: {install_cmd}")
        return install_cmd

    def _get_start_sandbox_service_cmd(
        self,
        engine: str,
        migration_path: str,
        bot_type: str,
        bot_id: str | None,
        owner_id: str | None,
        entity_id: str | None,
        entity_type: str | None,
        stage: str | None = PublishStage.ONLINE.value,
        version: str | None = "1",
        mount_home_dir_storage: bool = False,
        ext_info: Optional[Dict[str, Any]] = None,
    ):
        """启动沙箱服务。"""
        # 保留 {token} 和 {client_id} 占位符，供后续替换
        start_service_cmd = (
            f"/home/admin/bin/start_service.sh --token {{token}} --client_id {{client_id}} --bot_type {bot_type} --engine {engine}"
        )

        # 个人 Bot 和服务 Bot 草稿没有迁移目录；此时不传 --source_dir，
        # 避免 start_service.sh 把后面的 --bot_id 当成 source_dir 的值。
        if migration_path:
            start_service_cmd += f" --source_dir {migration_path}"

        if bot_id:
            start_service_cmd += f" --bot_id {bot_id}"
        if owner_id:
            start_service_cmd += f" --owner_id {owner_id}"

        if entity_id and entity_type:
            start_service_cmd += f" --entity_id {entity_id} --entity_type {entity_type}"

        if stage:
            stage_str = stage
            ext_info = ext_info or {}
            if ext_info.get("biz_id"):
                stage_str += f"-{ext_info.get('biz_id')}"
            start_service_cmd += f" --stage {stage_str}"

        if version:
            start_service_cmd += f" --version V{version}"

        # 命中 home 目录挂载白名单时，通知容器内启动脚本使用 NAS home 目录。
        start_service_cmd += f" --useNas {str(mount_home_dir_storage).lower()}"

        read_only_rules = self._get_set_read_only_rule(
            bot_id=bot_id, owner_id=owner_id,
            bot_type=bot_type, stage=stage or PublishStage.ONLINE.value,
        )
        start_service_cmd += read_only_rules

        start_cmd = f"""su admin -c 'nohup {start_service_cmd} >> /home/admin/start.log 2>&1'"""

        logger.info(f"[_get_start_sandbox_service_cmd] Executing cmd: {start_cmd}")
        return start_cmd

    def _resolve_sandbox_provider(
        self,
        bot_id: str = "",
        owner_id: str = "",
        engine: str = "",
    ) -> EngineSandboxProvider:
        """解析引擎对应的 sandbox provider。"""
        engine_type = engine or resolve_runtime_engine_for_bot(bot_id, owner_id, bot_repo=self._bot_repo)
        try:
            return self._sandbox_registry.resolve(engine_type)
        except Exception as e:
            logger.warning(
                "[_resolve_sandbox_provider] resolve failed for engine=%s, fallback to default: %s",
                engine_type,
                e,
            )
            return self._sandbox_registry.resolve(DEFAULT_ENGINE_TYPE)

    def _parse_bot_ext(self, bot: Dict[str, Any] | None) -> Dict[str, Any]:
        if not bot:
            return {}
        ext = bot.get("ext") or {}
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except json.JSONDecodeError:
                return {}
        return ext if isinstance(ext, dict) else {}

    def _materialize_rule_path(self, path: str, base_path: str) -> str:
        if not path:
            return path
        if path.startswith("/"):
            return path
        return f"{base_path.rstrip('/')}/{path}"

    def _materialize_default_rules(
        self,
        provider: EngineSandboxProvider,
    ) -> list[dict[str, str]]:
        base_path = provider.get_base_path()
        result: list[dict[str, str]] = []
        for rule in provider.get_default_read_only_rules():
            result.append({
                "path": self._materialize_rule_path(rule.path, base_path),
                "rule_type": rule.rule_type,
            })
        return result

    def _normalize_custom_read_only_rules(
        self,
        rules: Any,
        *,
        base_path: str,
    ) -> list[dict[str, str]]:
        if not isinstance(rules, list):
            return []

        result: list[dict[str, str]] = []
        for item in rules:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if not path or not isinstance(path, str):
                continue
            rule_type = item.get("rule_type", "file")
            result.append({
                "path": self._materialize_rule_path(path, base_path),
                "rule_type": rule_type,
            })
        return result

    def _dedupe_read_only_rules(self, rules: list[dict[str, str]]) -> list[dict[str, str]]:
        seen: set[tuple[str, str]] = set()
        result: list[dict[str, str]] = []
        for rule in rules:
            key = (rule.get("path", ""), rule.get("rule_type", "file"))
            if key in seen:
                continue
            seen.add(key)
            result.append(rule)
        return result

    def _get_set_read_only_rule(self, bot_id: str = "", owner_id: str = "", engine: str = "",
                                bot_type: str = "service", stage: str = "online"):
        """返回只读规则，拼接为 --set_read_only 参数。

        可编辑(personal / service草稿)不锁:容器内仍需写 mcporter 等配置;
        只有 service 发布 online 才锁。判定收口 is_editable_bot。
        """
        if is_editable_bot(bot_type, stage):
            return ""
        provider = self._resolve_sandbox_provider(bot_id=bot_id, owner_id=owner_id, engine=engine)
        base_path = provider.get_base_path()
        default_rules = self._materialize_default_rules(provider)

        custom_rules: list[dict[str, str]] = []
        if bot_id and owner_id:
            try:
                bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
                ext = self._parse_bot_ext(bot)
                custom_rules = self._normalize_custom_read_only_rules(
                    ext.get("read_only_rules", []),
                    base_path=base_path,
                )
            except Exception as e:
                logger.warning(
                    "[_get_set_read_only_rule] Failed to query custom rules: %s", e
                )

        all_rules = self._dedupe_read_only_rules(default_rules + custom_rules)
        all_paths = [r["path"] for r in all_rules if r.get("path")]
        if not all_paths:
            return ""
        return f" --set_read_only {','.join(all_paths)}"

    def _get_start_watchdog_cmd(
            self,
    ):
        # Start watchdog
        # 保留 {token} 和 {client_id} 占位符，供后续替换
        watchdog_cmd = "/home/admin/bin/starting_watchdog.sh --token {token} --client_id {client_id}"
        exec_watchdog_cmd = (
            f"""su admin -c 'nohup {watchdog_cmd} >> /home/admin/logs/starting_watchdog.log 2>&1'"""
        )
        logger.info(f"[_get_start_watchdog_cmd] Executing cmd: {exec_watchdog_cmd}")
        return exec_watchdog_cmd

    def _get_mkdir_engine_dir_cmd(self, engine: str) -> str:
        """确保引擎目录存在，兼容旧设备缺少引擎级 NAS 挂载的情况。

        对于旧设备，通过 symlink 将 /home/admin/.{engine} 指向
        /home/admin/nfs/bot-data/{engine}（通用 NAS 挂载下的子目录）。
        """
        engine_dir = f"/home/admin/.{engine}"
        nfs_engine_dir = f"/home/admin/nfs/bot-data/{engine}"
        cmd = (
            f"test -d {engine_dir} || "
            f"(mkdir -p {nfs_engine_dir} && ln -sfn {nfs_engine_dir} {engine_dir}) ; "
        )
        logger.info(f"[_ensure_engine_dirs] Executing cmd: {cmd}")
        return cmd

    def _setup_directory(
        self,
        entity_id: str,
        entity_type: str,
        bot_id: str,
        engine_type: str = DEFAULT_ENGINE_TYPE,
        mount_path: Optional[str] = None,
        mount_home_dir_storage: bool = False,
        ext_info: Optional[Dict[str, Any]] = None,
    ) -> list[MountPointEntry]:
        """使用 OSS 创建用户目录结构，返回 Arca MountPoint 配置。

        Args:
            entity_id: 实体 ID
            entity_type: 实体类型
            bot_id: Bot ID
            engine_type: 引擎类型，默认 "openclaw"
            mount_path: 用户自定义 NAS 挂载路径（可选，追加到 mount_points）
            mount_home_dir_storage: 是否挂载 home 目录（``nas_mount`` 白名单结论，
                由 BaasService 在构建 BotDeployContext 时解析完成）
        """
        sp = self._storage_path
        bolt_data = sp.get_bolt_data_path(entity_type=entity_type, entity_id=entity_id, bot_id=bot_id)
        skill_repo = sp.get_skills_repo_path()

        # 引擎感知：通过 EngineSandboxProvider 动态解析 skills 挂载本地路径
        # （base_path/{skill_target_relpath}/skills-repo），避免硬编码 openclaw
        provider = self._resolve_sandbox_provider(engine=engine_type)
        base_path = provider.get_base_path()
        build_plan = provider.get_build_plan()
        skills_local_dir = f"{base_path}/{build_plan.skill_target_relpath}/skills-repo"

        # OSS 挂载点必须位于 /home/admin/nfs/ 下；引擎专用目录
        # （/home/admin/.{engine}、/home/admin/.config/{engine}）由
        # _ensure_engine_dirs() 在沙箱内通过 symlink 指向 bot-data 子目录。

        mount_points = [
            # agentclaw-sys 挂载
            MountPointEntry(
                remote_dir="/agentclaw-sys",
                local_dir="/mnt/sys",
                permission=MountPermission.READ_ONLY,
            ),
        ]

        # skill repo独立挂载
        # bolt 配置数据独立挂载
        if mount_home_dir_storage:
            mount_points.append(
                MountPointEntry(
                    remote_dir=f"/{bolt_data}",
                    local_dir="/opt/nfs/bot-data",
                    permission=MountPermission.READ_WRITE,
                ),
            )
        else:
            mount_points.extend(
                [
                    MountPointEntry(
                        remote_dir=f"/{bolt_data}",
                        local_dir="/home/admin/nfs/bot-data",
                        permission=MountPermission.READ_WRITE,
                    ),
                    MountPointEntry(
                        remote_dir=f"/{skill_repo}",
                        local_dir=skills_local_dir,
                        permission=MountPermission.READ_ONLY,
                    ),
                ]
            )

        center_delivery = frozen_center_delivery_from_ext(
            ext_info,
            {"active_engine": engine_type or DEFAULT_ENGINE_TYPE},
        )
        if center_delivery is not None:
            mount_points.append(
                MountPointEntry(
                    remote_dir=f"/{center_delivery.store_prefix}",
                    local_dir=center_delivery.runtime_path,
                    permission=MountPermission.READ_ONLY,
                )
            )

        # 用户自定义挂载路径
        if mount_path:
            mount_points.append(
                MountPointEntry(
                    remote_dir=mount_path,
                    local_dir=mount_path,
                    permission=MountPermission.READ_WRITE,
                ),
            )
            logger.info(
                f"[ManagedDeployConfigComposer._setup_directory] Added custom mount_path: {mount_path}"
            )

        return mount_points

    def _setup_bot_storage(
            self,
            entity_id: str,
            entity_type: str,
            owner_id: str,
            bot_id: str,
            engine_type: str = DEFAULT_ENGINE_TYPE,
            mount_home_dir_storage: bool = False,
            bot_type: str = "",
            stage: str = "",
    ) -> Storage:
        """按已解析的白名单结论选择 Bot 的 NAS storage 挂载目录。"""
        if mount_home_dir_storage:
            logger.info(
                "[ManagedDeployConfigComposer._setup_bot_storage] Use home dir storage: "
                "owner_id=%s, bot_id=%s, engine_type=%s",
                owner_id,
                bot_id,
                engine_type,
            )
            return self._setup_home_dir_storage(
                entity_id=entity_id,
                entity_type=entity_type,
                bot_id=bot_id,
                engine_type=engine_type,
                device_scoped_home_storage=self._requires_device_scoped_home_storage(
                    bot_type=bot_type,
                    stage=stage,
                ),
            )

        logger.info(
            "[ManagedDeployConfigComposer._setup_bot_storage] Use sessions dir storage: "
            "owner_id=%s, bot_id=%s, engine_type=%s",
            owner_id,
            bot_id,
            engine_type,
        )
        return self._setup_sessions_dir(
            entity_id=entity_id,
            entity_type=entity_type,
            bot_id=bot_id,
            engine_type=engine_type,
        )

    def _setup_sessions_dir(
            self,
            entity_id: str,
            entity_type: str,
            bot_id: str,
            engine_type: str = DEFAULT_ENGINE_TYPE,
    ) -> Storage:

        # sessions NAS 远端路径（{device_uuid} 占位符由 BaaS 层赋值，service bot 多副本按设备隔离）。
        # 重启不丢 session 改由原地 restart 保证（device_uuid 不变），不靠去掉后缀。
        from agentclaw.community.core.workspace.path_factory import get_bot_nas_storage_id
        nas_storage_id = get_bot_nas_storage_id(
            entity_id=entity_id, bot_id=bot_id, engine_type=engine_type, entity_type=entity_type,
        )
        sessions_storage_id = f"{nas_storage_id}_{{device_uuid}}"

        # 引擎感知：sessions 目录由 EngineSandboxProvider 自描述,
        # BaasService 不再拼接引擎相关的子路径约定。
        provider = self._resolve_sandbox_provider(engine=engine_type)
        sessions_dir = provider.get_sessions_dir()

        storage = Storage(
            type=StorageType.NAS,
            storage_id=sessions_storage_id,
            quota="1Gi",
            permission="0777",
            path=sessions_dir,
        )

        return storage

    def _setup_home_dir_storage(
            self,
            entity_id: str,
            entity_type: str,
            bot_id: str,
            engine_type: str = DEFAULT_ENGINE_TYPE,
            device_scoped_home_storage: bool = False,
    ) -> Storage:

        # 个人 Bot / 草稿服务 Bot 只有一个运行态来源，home 目录复用 bot 级 NAS。
        # 预发/生产服务 Bot 支持多实例，每台 BaaS 设备要隔离自己的 home NAS。
        from agentclaw.community.core.workspace.path_factory import get_bot_nas_storage_id
        nas_storage_id = get_bot_nas_storage_id(
            entity_id=entity_id, bot_id=bot_id, engine_type=engine_type, entity_type=entity_type,
        )
        if device_scoped_home_storage:
            nas_storage_id = f"{nas_storage_id}_{{device_uuid}}"

        storage = Storage(
            type=StorageType.NAS,
            storage_id=nas_storage_id,
            quota="1Gi",
            permission="0777",
            path="/home/admin",
        )

        return storage

    @staticmethod
    def _requires_device_scoped_home_storage(*, bot_type: str, stage: str) -> bool:
        """预发/生产服务 Bot 支持多实例，home NAS 需要按 BaaS device_uuid 隔离。"""
        return (
            (bot_type or "").strip().lower() == "service"
            and (stage or "").strip().lower()
            in {PublishStage.VERIFY.value, PublishStage.ONLINE.value, PublishStage.EVAL.value}
        )
