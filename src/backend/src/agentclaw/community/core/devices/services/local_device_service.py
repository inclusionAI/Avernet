"""Local Device Service — singlebox device service backed by BaaS.

In production (Arca), each bot gets its own sandbox container. ArcaDeviceService
delegates to the Arca SDK for sandbox creation/destruction. This service mirrors
that pattern by delegating to BaaS REST APIs; BaaS internally arranges process
spawn on the local host in singlebox mode.

Architecture:
  ArcaDeviceService → Arca SDK → sandbox container (adapter + openclaw)
  LocalDeviceService → BaasService → BaaS REST (singlebox: BaaS internal local paas)

Follows new architecture conventions:
- Depends on core/devices/models.py for models
- Does not depend on old services/device/ code
"""

import json
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, override


if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.token_vault import TokenVault
    from agentclaw.community.core.devices.protocols import BotQueryProtocol, BotSyncProtocol, McpSyncProtocol
    from agentclaw.community.core.service_bot.services.baas_service import BaasService

from agentclaw.community.core.devices.errors import (
    DeviceNotFoundError,
    DeviceServiceError,
    InvalidDeviceStatusError,
)
from agentclaw.community.core.devices.models import (
    AllocatedDevice,
    DeviceBindingInfo,
    DeviceBindingStatus,
    DeviceConnectionInfo,
    NasMappingInfo,
    OperatorContext,
    SynlinkMappingInfo,
)
from agentclaw.community.core.devices.repository.protocol import (
    DeviceBindingRepository,
    OssToNasRecordRepository,
)
from agentclaw.community.core.devices.services.baas_device_lifecycle_executor import (
    BaasDeviceLifecycleError,
    BaasDeviceLifecycleExecutor,
)
from agentclaw.community.core.devices.services.baas_publish_poller import BaasPublishPoller
from agentclaw.community.core.devices.services.device_service import (
    LOCAL_DEVICE_PROVIDER,
    DeviceService,
)
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE  # noqa: E402
from agentclaw.community.log import get_logger
from agentclaw.community.utils import env_utils
from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant


logger = get_logger()


class LocalDeviceAllocateError(DeviceServiceError):
    """本地设备分配错误."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LocalDeviceReleaseError(DeviceServiceError):
    """本地设备释放错误."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LocalDeviceService(DeviceService):
    """Singlebox device service backed by BaaS.

    Each bot is allocated/released via BaaS create_bot/destroy_bot.
    Bot lifecycle status is driven by BaaS publish progress, polled by
    BaasPublishPoller in a background daemon thread.

    binding_id propagation contract
    --------------------------------
    ``_compose_device_conn_info`` and ``_start_service`` rely on
    ``device.device_props["binding_id"]`` being populated. Since the DB row's
    primary key (binding_id) is not persisted in ``device_props`` by default,
    any code path that materializes an :class:`AllocatedDevice` for these
    methods MUST back-fill ``device_props["binding_id"] = record.id`` first.

    Canonical examples:
      - :meth:`apply_device` — back-fills binding_id immediately after
        ``insert_binding`` / ``reuse_binding`` and before invoking
        ``_start_service`` in the async thread.
      - :meth:`get_device_connection` — back-fills binding_id after loading
        the persisted record and before invoking ``_compose_device_conn_info``.

    Spec reference: §2.5 — binding_id is the canonical key used to look up
    BaaS ws_info / publish state.
    """

    def __init__(
        self,
        repository: DeviceBindingRepository,
        baas_service: "BaasService",
        publish_poller: "BaasPublishPoller",
        default_engine: str = DEFAULT_ENGINE_TYPE,
        config: dict[str, Any] | None = None,
        *,
        bot_query: "BotQueryProtocol",
        bot_sync: "BotSyncProtocol",
        oss_record_repo: "OssToNasRecordRepository",
        mcp_sync: "McpSyncProtocol",
        lifecycle_executor: BaasDeviceLifecycleExecutor | None = None,
        vault: "TokenVault | None" = None,
    ):
        super().__init__(
            repository, default_engine,
            bot_query=bot_query, bot_sync=bot_sync,
            oss_record_repo=oss_record_repo,
            mcp_sync=mcp_sync,
            vault=vault,
        )
        self._config = config or {}
        self._baas_service = baas_service
        self._lifecycle_executor = lifecycle_executor or BaasDeviceLifecycleExecutor(
            baas_service
        )
        self._publish_poller = publish_poller
        logger.info("[LocalDeviceService] Initialized with BaaS backend")

    def _get_aidesktop_root(self) -> str:
        return self._config.get("aidesktop_root", "/aidesktop")

    def _resolve_env_dir(self, env: str) -> str:
        if env == "prod":
            return "aidesktop_prod"
        elif env == "pre":
            return "aidesktop_pre"
        return "aidesktop_dev"

    def _resolve_entity_dir(self, entity_id: str, entity_type: str) -> str:
        return f"{entity_type}_{entity_id}"

    # ──────────────────────────────────────────────────────────────────────
    # Directory setup
    # ──────────────────────────────────────────────────────────────────────

    def _setup_directory(
        self,
        operator: OperatorContext,
        *,
        entity_id: str,
        entity_type: str,
        bolt_id: str,
        env: str,
        engine: str = DEFAULT_ENGINE_TYPE,
    ) -> list[NasMappingInfo]:
        """Create the bot's isolated workspace directory.

        Returns:
            Empty list — local mode doesn't need NasMapping
        """
        aidesktop_root = self._get_aidesktop_root()
        env_dir = self._resolve_env_dir(env)
        entity_dir = self._resolve_entity_dir(entity_id, entity_type)
        device_base_dir = Path(aidesktop_root) / env_dir / "bolt_data" / entity_dir / bolt_id

        engine_dir = device_base_dir / engine

        try:
            device_base_dir.mkdir(parents=True, exist_ok=True)

            if engine_dir.is_symlink():
                engine_dir.unlink()
                engine_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"[setup_directory] Removed {engine} symlink, created independent dir: {engine_dir}")
            elif not engine_dir.exists():
                engine_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"[setup_directory] Created independent {engine} dir: {engine_dir}")

            workspace_dir = engine_dir / "workspace"
            skills_dir = workspace_dir / "skills"
            skills_local_dir = skills_dir / "skills-local"
            skills_repo_dir = skills_dir / "skills-repo"
            active_dir = skills_dir / "active"

            workspace_dir.mkdir(parents=True, exist_ok=True)
            skills_dir.mkdir(parents=True, exist_ok=True)
            skills_local_dir.mkdir(parents=True, exist_ok=True)
            skills_repo_dir.mkdir(parents=True, exist_ok=True)
            active_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"[setup_directory] Created {engine} directories for bot: {device_base_dir}")
        except Exception as e:
            logger.error(f"[setup_directory] Failed to create local device directories: {e}")
            raise LocalDeviceAllocateError(f"创建设备目录失败: {str(e)}") from e

        return []

    # ──────────────────────────────────────────────────────────────────────
    # Device allocation
    # ──────────────────────────────────────────────────────────────────────

    def _do_allocate(
        self,
        *,
        entity_id: str,
        entity_type: str,
        bolt_id: str,
        device_id: str,
        storage_mappings: list[NasMappingInfo],
        env: str,
        engine: str = DEFAULT_ENGINE_TYPE,
        bot_type: str = "",
        bot_id: str | None = None,
        owner_id: str | None = None,
        extra_envs: dict[str, str] | None = None,
        template_type: str | None = None,
        template_config: dict | None = None,
    ) -> AllocatedDevice:
        """singlebox：通过 BaaS create_bot + approve_publish 创建 bot。

        [device_id 历史兼容性说明]
        本接口生成的 device_id 直接采用 BaaS 返回的 bot_uuid，与 LocalDeviceService
        旧版的 ``{entity_type}_{entity_id}_{bolt_id}_{uuid}`` 不兼容。
          1. 当期 singlebox 全部增量数据，不影响上线
          2. 未来涉及历史数据时（如线上 binding 旧记录无 bot_uuid），
             Backend 这边要做兼容
          3. 本接口本身不兼容历史数据
        """
        bot_dict = {
            "bot_id": bolt_id,
            "bot_name": bolt_id,
            "entity_id": entity_id,
            "entity_type": entity_type,
            "active_engine": engine,
            "bot_type": bot_type or "personal",
        }
        effective_owner_id = owner_id or entity_id
        request_id = uuid.uuid4().hex

        logger.info(
            f"[_do_allocate] BaaS create_bot start: bolt_id={bolt_id}, "
            f"entity_id={entity_id}, owner_id={effective_owner_id}, "
            f"engine={engine}, bot_type={bot_dict['bot_type']}, request_id={request_id}"
        )

        payload = self._baas_service._build_create_bot_payload(
            bot=bot_dict,
            owner_id=effective_owner_id,
            request_id=request_id,
            device_count=1,
            migration_path="",
            auto_approve_publish=True,
        )

        try:
            lifecycle_result = self._lifecycle_executor.create_bot_from_payload(
                payload=payload,
                owner_id=effective_owner_id,
                request_id=request_id,
                action="singlebox_local_device_create",
                approve_comment="自动审批",
            )
        except BaasDeviceLifecycleError as e:
            raise LocalDeviceAllocateError(str(e)) from e

        bot_uuid = lifecycle_result.bot_uuid
        publish_id = lifecycle_result.publish_id

        logger.info(
            f"[_do_allocate] Allocated singlebox bot via BaaS: "
            f"bot_uuid={bot_uuid}, publish_id={publish_id}"
        )

        # device_provider 的过渡决策（2026-06-05 singlebox BaaS 改造）：
        # 实质上 device 已经由 BaaS（内部 LocalPaasService）管理，**正确语义应写
        # BAAS_DEVICE_PROVIDER ("baas")**。当前仍写 LOCAL_DEVICE_PROVIDER ("local") 的原因：
        #   1. 本轮 PR 范围是 singlebox 复用 local 路径接 BaaS，未承诺重整 device_provider 语义；
        #   2. 改成 "baas" 需要联动改 5+ 处下游硬编码分支判断（见下面 caller 清单），
        #      会扩大本次 PR 影响面，单独 PR 收口更稳。
        #
        # 下游影响清单（改成 "baas" 时要一起改）：
        #   - di/modules/testing_devices_module.py: providers dict key "local" → "baas"
        #     + default_provider_key 同步（DeviceServiceRouter 用 binding 列值去 providers
        #     dict 路由，两者必须一致）
        #   - local_device_service.py 内 _compose_device_conn_info / _query_device_info
        #     入口的 `if device.device_provider != LOCAL_DEVICE_PROVIDER` 校验
        #   - core/workspace/path_factory.py: `device_provider == "baas"` 分支会把
        #     singlebox bot 当作"线上 BaaS"误选 OSS-view 路径
        #   - core/devices/services/engine_health.py: `b.device_provider == "arca"` 过滤
        #   - core/devices/services/readiness_service.py: 同上
        #   - plugins/prod/health_probe.py: 同上
        #
        # FUTURE: BaaS 真正接管 singlebox 进程生命周期、Prod/Local BaaS 接口统一后，
        # 走方案 A（device_provider 改为 "baas"），同时收拾上面 5+ 处下游分支。
        # 在此之前 device_provider="local" 语义偏差但功能正确（DI 路由 dict key 和
        # binding 列值同为 "local"，路由能跑通）。
        return AllocatedDevice(
            device_id=bot_uuid,
            device_provider=LOCAL_DEVICE_PROVIDER,
            device_props={
                "bot_uuid": bot_uuid,
                "publish_id": publish_id,
                "bolt_id": bolt_id,
                "entity_id": entity_id,
                "entity_type": entity_type,
                "engine": engine,
            }
        )

    # ──────────────────────────────────────────────────────────────────────
    # Service startup
    # ──────────────────────────────────────────────────────────────────────

    def _start_service(
        self,
        *,
        device: AllocatedDevice,
        nas_mappings: list[NasMappingInfo] | None = None,
        engine: str = DEFAULT_ENGINE_TYPE,
        bot_type: str = "",
        bot_id: str | None = None,
        owner_id: str | None = None,
        admins: list[str] | None = None,
        codefuse_token: str | None = None,
    ) -> tuple[bool, str]:
        """触发后台 BaaS publish 轮询；状态由 poller 在 SUCCESS 时翻转。

        [Q3 决议] Backend 仍返 PENDING；后台 thread 轮询 BaaS publish。
        [Q6 决议] 废弃 callback_token；轮询完成后直接 skip_token_check=True。
        """
        publish_id = device.device_props.get("publish_id")
        bot_uuid = device.device_props.get("bot_uuid", device.device_id)

        # binding_id 由父类模板方法在 device_props 填回（apply_device 流程）
        binding_id = device.device_props.get("binding_id")

        if not publish_id:
            # BaaS create_bot 返回成功但缺失 publish_id —— 这是 BaaS 契约违反，
            # 不能默默标记 ACTIVE（会发出一个无 publish 撑腰的坏 bot）。
            # 直接 ERROR + 返回失败，由上层 _mark_service_start_failed 暴露问题。
            logger.error(
                f"[_start_service] BaaS create_bot returned no publish_id for "
                f"bot_uuid={bot_uuid}; cannot drive PENDING→ACTIVE state machine"
            )
            return False, (
                f"missing publish_id from BaaS create_bot response for bot_uuid={bot_uuid}; "
                f"check BaaS contract"
            )

        if binding_id is None:
            return False, (
                f"missing binding_id in device_props for {bot_uuid}; "
                "cannot start polling"
            )

        self._publish_poller.start(
            publish_id=publish_id,
            device_id=device.device_id,
            binding_id=binding_id,
        )
        return True, f"polling publish_id={publish_id}, bot_uuid={bot_uuid}"

    def report_device_alive(self, *, device_id, token, skip_token_check=False):
        """父类翻 PENDING→ACTIVE 后回填 adapter_port 到 binding.device_props。

        singlebox: _do_allocate 落地的 binding 缺 adapter_port → dispatcher 判
        "未接 BaaS" 走 pathlib 本机。ACTIVE 后从 ws-info 拿真 engine_port 回填，
        skills upload/sync 即走 BaaS http-info。best-effort，失败不阻塞 ACTIVE。
        """
        record = super().report_device_alive(
            device_id=device_id, token=token, skip_token_check=skip_token_check
        )
        try:
            props = dict(record.device_props or {})
            if "adapter_port" not in props:
                ws = self._baas_service.get_ws_info(bind_id=record.id)
                props["adapter_port"] = ws.engine_port
                self._repo.update_device_props(binding_id=record.id, props=props)
                logger.info("[report_device_alive] 回填 adapter_port=%s bind=%s", ws.engine_port, record.id)
        except Exception as e:
            logger.warning("[report_device_alive] adapter_port 回填失败 bind=%s: %s", getattr(record, "id", "?"), e)
        return record

    # ──────────────────────────────────────────────────────────────────────
    # Device release
    # ──────────────────────────────────────────────────────────────────────

    def _do_release(self, *, device: AllocatedDevice) -> bool:
        """通过 BaaS destroy_bot + approve_publish 释放 bot。

        - destroy_bot 失败 → raise（与 ArcaDeviceService 一致）
        - approve_publish 失败 → 仅 warning，不阻塞本地清理（与 desktop delete 对齐）
        """
        bot_uuid = device.device_props.get("bot_uuid") or device.device_id
        operator = device.device_props.get("entity_id", "")
        request_id = uuid.uuid4().hex

        logger.info(
            f"[_do_release] BaaS destroy_bot: bot_uuid={bot_uuid}, "
            f"request_id={request_id}"
        )

        try:
            self._lifecycle_executor.destroy_bot(
                bot_uuid=bot_uuid,
                operator=operator,
                request_id=request_id,
            )
        except BaasDeviceLifecycleError as e:
            raise LocalDeviceReleaseError(str(e)) from e

        logger.info(f"[_do_release] Released singlebox bot: bot_uuid={bot_uuid}")
        return True

    # ──────────────────────────────────────────────────────────────────────
    # Connection info
    # ──────────────────────────────────────────────────────────────────────

    def _compose_device_conn_info(
        self,
        *,
        device: AllocatedDevice,
        port: int | None = None,
        ttl: int | None = None,
        ws_conn_mode: str | None = None,
    ) -> DeviceConnectionInfo:
        """通过 BaaS get_ws_info 获取容器连接信息。"""
        if device.device_provider != LOCAL_DEVICE_PROVIDER:
            raise ValueError(
                f"device provider('{device.device_provider}') "
                f"is not '{LOCAL_DEVICE_PROVIDER}'"
            )

        # binding_id 的语义：等于 ac_entity_device_binding.id，也等于 ac_bots.binding_id
        # （ac_bots.binding_id 是外键指向 ac_entity_device_binding.id），三处其实是同一个值。
        #
        # 为什么走 device_props 传而非显式入参：
        # _compose_device_conn_info 的签名要与父类 DeviceService 抽象保持一致
        # （Arca / Baas / Local 三个子类共用），父类只规定通过 AllocatedDevice 传递上下文。
        # AllocatedDevice 只有 device_id / device_provider / device_props 三个字段，binding_id
        # 不在 schema 里，只能塞进 device_props dict。
        #
        # 为什么 device_props 里不持久化 binding_id：
        # ac_entity_device_binding.device_props 是 Text JSON 列，原则上只存 provider-specific
        # 字段（sandbox_id 等）。binding_id 是 row PK，自指存储是冗余设计，所以 caller 必须
        # 在 in-memory 构造 AllocatedDevice 时做 back-fill。canonical pattern 见
        # apply_device 和 get_device_connection。
        #
        # FUTURE: 更干净的方案是父类抽象调整为显式传 binding_id 参数（涉及 3 个子类 + 父类
        # 模板方法），消除这处隐式契约。当前不做，spec §2.5 接受该 trade-off。
        binding_id = device.device_props.get("binding_id")
        if binding_id is None:
            raise ValueError(
                f"No binding_id in device_props for {device.device_id}. "
                f"LocalDeviceService consumers MUST back-fill device_props['binding_id'] = record.id "
                f"before invoking _compose_device_conn_info. See apply_device / get_device_connection "
                f"for the canonical pattern."
            )

        # 过渡：BaaS ws-info 不可达（CI 无网 / 设备离线）时不抛 500，返回
        # available=False 的连接信息——下游 connectable/connection 据此显示离线，
        # 而非整链 500。BaaS 接通后正常返回真 target/token。
        try:
            ws_info = self._baas_service.get_ws_info(
                bind_id=binding_id,
                device_affinity=device.device_props.get("entity_id"),
                ws_conn_mode=ws_conn_mode,
            )
        except Exception as e:
            logger.warning(
                "[_compose_device_conn_info] ws-info unavailable bind=%s: %s",
                binding_id, e,
            )
            return DeviceConnectionInfo(
                type=LOCAL_DEVICE_PROVIDER,
                target="",
                token="",
                engine_type=self._default_engine,
                available=False,
                message=f"device unavailable: {e}",
            )

        # plan-01 新增：通过 BaaS get_http_info 拿 url + token，写入 DeviceConnectionInfo.url。
        # 业务 plugin (plan-02~04 改造) 主要走 HTTP；caller (expert_chat 等) 已有
        # `conn.get("url") or f"http://{conn['target']}"` 模式优先取 url。
        # 兜底：http-info 不可达 → 走 ws-info-only 路径（保留 ws.target / ws.token）；
        # url 为空时 caller 自动 fallback 到 target 拼 URL。Task 8 会加 http-info 单独
        # 失败时的 available=False 分支测试，并允许 ws 成功 / http 失败这种部分可达态。
        adapter_port = device.device_props.get("adapter_port", ws_info.engine_port)
        try:
            http_info = self._baas_service.get_http_info(
                bind_id=binding_id,
                port=adapter_port,
                device_affinity=device.device_props.get("entity_id"),
                ws_conn_mode=ws_conn_mode,
            )
            url = http_info.http_url
            token = http_info.token
            # http-info 不带过期时间，而返回的 token 是它的——ws-info 的
            # expires_at 描述的是另一个 token，填上就是错的。
            expires_at = ""
            # http_info 返回的 target 是 3 段格式 LOCAL_{dev}@{tpl}:{port}，
            # 与 token（JWT 中的 target claim）对齐。
            target = http_info.target
        except Exception as e:
            logger.warning(
                "[_compose_device_conn_info] http-info unavailable bind=%s: %s "
                "(ws-info ok, returning ws-only conn info)",
                binding_id, e,
            )
            url = ""
            token = ws_info.token
            # 回落到 ws-info 的 token，所以 ws-info 的过期时间此刻是对的。
            expires_at = ws_info.expires_at
            # 回退到 WS 链路的 target，与 ws_info.token 对齐。
            target = ws_info.target
        return DeviceConnectionInfo(
            type=LOCAL_DEVICE_PROVIDER,
            target=target,
            token=token,
            expires_at=expires_at,
            # HTTP 与 WS 的 target/token 都来自各自同一次签发，避免代理校验时
            # 把一条链路的 JWT 与另一条链路的 target 错配。
            ws_target=ws_info.target,
            ws_token=ws_info.token,
            ws_expires_at=ws_info.expires_at,
            engine_type=device.device_props.get("engine", DEFAULT_ENGINE_TYPE),
            baas_base_url=ws_info.baas_base_url,
            bot_uuid=ws_info.bot_uuid,
            tenant=ws_info.tenant,
            engine_port=ws_info.engine_port,
            url=url,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Device info query
    # ──────────────────────────────────────────────────────────────────────

    def _query_device_info(self, *, device: AllocatedDevice) -> dict[str, Any]:
        """通过 BaaS list_devices_by_bot_uuid 查询设备健康度。"""
        if device.device_provider != LOCAL_DEVICE_PROVIDER:
            raise ValueError(
                f"device provider('{device.device_provider}') "
                f"is not '{LOCAL_DEVICE_PROVIDER}'"
            )

        bot_uuid = device.device_props.get("bot_uuid") or device.device_id

        try:
            devices = self._baas_service.list_devices_by_bot_uuid(bot_uuid)
        except Exception as e:
            logger.warning(
                f"[_query_device_info] BaaS list_devices_by_bot_uuid failed: "
                f"bot_uuid={bot_uuid} error={e}"
            )
            return {"healthy": False, "device_ip": None}

        if not devices:
            return {"healthy": False, "device_ip": None}

        dev = devices[0]  # singlebox device_count=1
        return {
            "device_ip": dev.get("ip"),
            "healthy": dev.get("status") == "ACTIVE",
        }

    def _exec_shell(self, device: AllocatedDevice, shell_cmd: str) -> str:
        """Not implemented for local devices."""
        return "LocalDeviceService does not support _exec_shell"

    # ──────────────────────────────────────────────────────────────────────
    # Device ID generation
    # ──────────────────────────────────────────────────────────────────────

    def _generate_device_id(
        self,
        *,
        entity_id: str,
        entity_type: str,
        bot_id: str = "default",
    ) -> tuple[str, str]:
        """Generate device ID. Format: {entity_type}_{entity_id}_{bot_id}_{uuid}"""
        uuid_suffix = uuid.uuid4().hex
        device_id = f"{entity_type}_{entity_id}_{bot_id}_{uuid_suffix}"
        return device_id, bot_id

    # ──────────────────────────────────────────────────────────────────────
    # apply_device override — PENDING instead of ACTIVE
    # ──────────────────────────────────────────────────────────────────────

    @override
    def apply_device(
        self,
        *,
        apply_reason: str | None,
        entity_id: str,
        entity_type: str,
        operator: OperatorContext,
        bot_id: str | None = None,
        engine: str | None = None,
        bot_type: str = "",
        owner_id: str | None = None,
        symbol: list[SynlinkMappingInfo] | None = None,
        force_nas: bool = False,
        extra_envs: dict[str, str] | None = None,
        admins: list[str] | None = None,
        template_type: str | None = None,
        template_config: dict | None = None,
    ):
        """Apply for a device — singlebox mode backed by BaaS.

        Same flow as the parent template method, but local-specific:
        - Starts as PENDING (BaaS publish not yet SUCCESS)
        - _start_service hands publish_id to BaasPublishPoller; the poller
          calls report_device_alive(skip_token_check=True) on SUCCESS to
          drive PENDING → ACTIVE.
        """
        resolved_bot_id = bot_id or "default"
        resolved_engine = engine or self._default_engine
        env = env_utils.get_current_env()

        # Generate device_id
        device_id, bolt_id = self._generate_device_id(
            entity_id=entity_id,
            entity_type=entity_type,
            bot_id=resolved_bot_id,
        )

        # Check for released binding to reuse
        released_binding = self._repo.get_released_binding(device_id=device_id)

        # TODO Remove this. No need to set up.
        # Initialize directories
        # nas_mappings = self._setup_directory(
        #     operator=operator,
        #     entity_id=entity_id,
        #     entity_type=entity_type,
        #     bolt_id=bolt_id,
        #     env=env,
        # )
        nas_mappings = []

        # Allocate device (ports + config)
        allocated = self._do_allocate(
            entity_id=entity_id,
            entity_type=entity_type,
            bolt_id=bolt_id,
            device_id=device_id,
            storage_mappings=nas_mappings,
            env=env,
            engine=resolved_engine,
            bot_type=bot_type,
            owner_id=owner_id,
            extra_envs=extra_envs,
            template_type=template_type,
            template_config=template_config,
        )

        # Build device properties
        device_props = {
            **allocated.device_props,
            "nas_mappings": json.dumps([], ensure_ascii=False),
            "symbol": json.dumps([s.to_dict() for s in symbol]) if symbol else "[]",
        }

        # Start as PENDING — transitions to ACTIVE when BaasPublishPoller
        # observes publish SUCCESS and calls report_device_alive(skip_token_check=True)
        status = DeviceBindingStatus.PENDING.value

        if released_binding is not None:
            logger.info(f"[apply_device] Reusing released local device: {device_id}")
            self._repo.reuse_binding(
                binding_id=released_binding.id,
                device_props=device_props,
                apply_reason=apply_reason,
                applied_by=operator.staff,
                status=status,
            )
            result = self._repo.get_by_id(released_binding.id)
        else:
            binding_id = self._repo.insert_binding(
                entity_id=entity_id,
                entity_type=entity_type,
                device_id=allocated.device_id,
                device_provider=LOCAL_DEVICE_PROVIDER,
                env=env,
                device_props=device_props,
                status=status,
                apply_reason=apply_reason,
                applied_by=operator.staff,
            )
            result = self._repo.get_by_id(binding_id)

        if result is None:
            raise LocalDeviceAllocateError("Failed to create device binding")

        # Back-fill binding_id into in-memory device_props.
        # binding_id 即 result.id（= ac_entity_device_binding.id，也即 ac_bots.binding_id），
        # 三处同值。它不在 DB 持久化的 device_props JSON 里（PK 自指存储是冗余），
        # 但下游 _start_service(→ BaasPublishPoller) 与 _compose_device_conn_info 都需要它
        # 来调 BaaS（get_publish_progress 经 binding/poller、get_ws_info(bind_id=...)）。
        # 这里只塞进 in-memory dict，不写回 DB——契约见 _compose_device_conn_info 顶部注释。
        resolved_binding_id = result.id
        device_props["binding_id"] = resolved_binding_id

        # Start service asynchronously (kick off BaasPublishPoller in background thread).
        # 失败路径必须调用 _mark_service_start_failed（与基类对齐），否则 BaaS publish
        # 拉起失败时 bot 会永远停在 PENDING + error_message=null。
        def start_service_async():
            try:
                success, message = self._start_service(
                    device=AllocatedDevice(
                        device_id=allocated.device_id,
                        device_provider=allocated.device_provider,
                        device_props=device_props,
                    ),
                    nas_mappings=nas_mappings,
                    engine=resolved_engine,
                    bot_type=bot_type,
                    bot_id=resolved_bot_id,
                    owner_id=owner_id or entity_id,
                    admins=admins,
                )
                logger.info(f"[apply_device] Start service: success={success}, message={message}")
                if not success:
                    self._mark_service_start_failed(
                        binding_id=resolved_binding_id,
                        error=message or "service start returned failure",
                    )
            except Exception as e:
                logger.exception(f"[apply_device] Start service failed: {e}")
                self._mark_service_start_failed(
                    binding_id=resolved_binding_id,
                    error=str(e),
                )

        thread = threading.Thread(
            target=bind_current_avernet_tenant(start_service_async), daemon=True
        )
        thread.start()

        return result

    # ──────────────────────────────────────────────────────────────────────
    # get_device_connection override
    # ──────────────────────────────────────────────────────────────────────

    @override
    def get_device_connection(
        self,
        *,
        binding_id: int,
        operator: OperatorContext,
        port: int | None = None,
        ttl: int | None = None,
        device_uuid: str | None = None,
        ws_conn_mode: str | None = None,
        path: str | None = None,
    ) -> DeviceConnectionInfo:
        """Get device connection info.

        ``device_uuid`` targets a specific instance for multi-instance BaaS bots;
        local devices are single-instance and ignore it.

        ``path`` is ignored here: this provider returns a bare routing target
        (and an HTTP base URL), never a finished WebSocket URL, so the caller
        appends the path itself. Accepted to keep the provider signatures equal.
        """
        record = self._repo.get_by_id(binding_id)
        if record is None:
            raise DeviceNotFoundError(f"binding {binding_id} not found")

        if record.status == DeviceBindingStatus.FAILED.value:
            raise InvalidDeviceStatusError("cannot get connection for failed device")

        # Permission check: allow public bots
        if record.entity_id != operator.staff_id:
            is_public = False
            try:
                bot = self._bot_query.get_by_binding_id(binding_id)
                is_public = bot is not None and bot.get("public") == "1"
            except Exception as e:
                logger.warning("[get_device_connection] Failed to check bot visibility: %s", e)
            if not is_public:
                raise InvalidDeviceStatusError(
                    "非公开Bot只能获取本人设备的连接信息"
                )

        # Back-fill binding_id into device_props.
        # caller 传入的 binding_id 参数 == record.id == 我们要塞的值（三者同源：
        # ac_entity_device_binding.id），看似冗余但必须 back-fill，因为
        # _compose_device_conn_info 的签名（受父类抽象约束）只能通过 AllocatedDevice
        # 传递 binding 上下文，无法显式接 binding_id 参数。详见 _compose_device_conn_info
        # 顶部的契约说明。
        # 注意 dict(...) 拷贝一份再 mutate，避免污染 record.device_props 原对象（repo 缓存安全）。
        device_props = dict(record.device_props or {})
        device_props["binding_id"] = record.id

        allocated_device = AllocatedDevice(
            device_id=record.device_id,
            device_provider=record.device_provider,
            device_props=device_props,
        )

        return self._compose_device_conn_info(
            device=allocated_device,
            port=port,
            ttl=ttl,
            ws_conn_mode=ws_conn_mode,
        )

    # ──────────────────────────────────────────────────────────────────────
    # list_connectable_devices override
    # ──────────────────────────────────────────────────────────────────────

    @override
    def list_connectable_devices(
        self,
        *,
        entity_id: str | None,
        entity_type: str | None,
        env: str | None,
        page: int = 1,
        page_size: int = 20,
        with_connection: bool = False,
        port: int | None = None,
        operator: OperatorContext | None = None,
    ) -> tuple[int, list[DeviceBindingInfo]]:
        """List connectable (ACTIVE) devices with optional connection info."""
        resolved_env = env if env is not None else env_utils.get_current_env()
        if env is None:
            logger.debug(
                "[list_connectable_devices] env not provided, defaulted to current_env=%s",
                resolved_env,
            )

        total, items = self._repo.list_bindings(
            entity_id=entity_id,
            entity_type=entity_type,
            env=resolved_env,
            status=DeviceBindingStatus.ACTIVE.value,
            page=page,
            page_size=page_size,
        )

        if not with_connection:
            return total, [DeviceBindingInfo(record=item) for item in items]

        results = []
        for item in items:
            connection = None
            try:
                if operator is not None:
                    connection = self.get_device_connection(
                        binding_id=item.id,
                        operator=operator,
                        port=port,
                    )
            except Exception as e:
                logger.warning(f"[list_connectable_devices] Failed to get connection for {item.id}: {e}")
            results.append(DeviceBindingInfo(record=item, connection=connection))
        return total, results
