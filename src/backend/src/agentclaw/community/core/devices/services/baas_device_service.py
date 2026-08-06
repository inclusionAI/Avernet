"""BaaS Device Service — BaaS 层设备生命周期和连接信息服务。"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any, Protocol, override

from agentclaw.community.core.bot_management.engines import (
    build_extra_properties_fail_open,
    extract_runtime_token_fail_open,
    should_encrypt_template_token_fail_open,
)
from agentclaw.community.core.devices.errors import DeviceServiceError
from agentclaw.community.core.devices.models import (
    AllocatedDevice,
    DeviceConnectionInfo,
    DeviceBindingStatus,
    NasMappingInfo,
    OperatorContext,
)
from agentclaw.community.core.devices.repository.protocol import (
    DeviceBindingRepository,
    OssToNasRecordRepository,
)
from agentclaw.community.core.devices.services.baas_device_lifecycle_executor import (
    BaasDeviceLifecycleError,
    BaasDeviceLifecycleExecutor,
)
from agentclaw.community.core.devices.services.baas_device_header_updater import (
    BaasDeviceHeaderUpdateError,
    BaasDeviceHeaderUpdater,
)
from agentclaw.community.core.devices.services.baas_container_init import (
    BaasContainerInitializer,
    _deserialize_symbol,
)
from agentclaw.community.core.devices.services.baas_exec_shell import execute_baas_shell_command
from agentclaw.community.core.devices.services import baas_publish_lifecycle
from agentclaw.community.core.devices.services.baas_template_resolver import (
    BaasTemplateResolveError,
    BaasTemplateResolverProtocol,
)
from agentclaw.community.core.devices.services.device_service import (
    BAAS_DEVICE_PROVIDER,
    DEFAULT_ENGINE_TYPE,
    DeviceService,
)
from agentclaw.community.log import get_logger


if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.token_vault import TokenVault
    from agentclaw.community.core.devices.protocols import BotQueryProtocol, BotSyncProtocol, McpSyncProtocol
    from agentclaw.community.core.service_bot.services.baas_service import BaasService
    from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService

logger = get_logger()


def _generate_request_id(
    *, bot_id: str, entity_id: str, entity_type: str, env: str, action: str
) -> str:
    """Idempotent BaaS request_id. Matches DesktopBotService._generate_request_id
    so a device create and a desktop-bot create using the same inputs do
    not collide.
    """
    raw = f"{entity_id}_{entity_type}_{bot_id}_{env}_{action}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class BaasDeviceServiceError(DeviceServiceError):
    """BaaS 设备服务错误。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class TemplateConfigReader(Protocol):
    """Minimal template service surface needed by BaaS create-init tasks."""

    def get_template_config(self, bot_id: str) -> dict[str, Any] | None: ...


class BaasDeviceService(DeviceService):
    """BaaS 设备服务 - 与 BaaS 层 API 交互。

    继承 DeviceService，提供:
    - WebSocket 连接信息获取（桌面/服务 bot 已在用）
    - 出站规则热更新（同上）
    - provider=baas 的创建/销毁/查询/状态推进（本期新增）
    """

    def __init__(
        self,
        repository: DeviceBindingRepository,
        baas_service: BaasService,
        default_engine: str = DEFAULT_ENGINE_TYPE,
        *,
        bot_query: BotQueryProtocol,
        bot_sync: BotSyncProtocol,
        oss_record_repo: OssToNasRecordRepository,
        mcp_sync: McpSyncProtocol,
        template_resolver: BaasTemplateResolverProtocol,
        lifecycle_executor: BaasDeviceLifecycleExecutor | None = None,
        vault: "TokenVault | None" = None,
        task_queue_service: "TaskQueueService | None" = None,
        template_service: TemplateConfigReader | None = None,
    ):
        super().__init__(
            repository=repository,
            default_engine=default_engine,
            bot_query=bot_query,
            bot_sync=bot_sync,
            oss_record_repo=oss_record_repo,
            mcp_sync=mcp_sync,
            vault=vault,
            task_queue_service=task_queue_service,
        )
        self._baas_service = baas_service
        self._header_updater = BaasDeviceHeaderUpdater(baas_service)
        self._lifecycle_executor = lifecycle_executor or BaasDeviceLifecycleExecutor(
            baas_service
        )
        self._container_initializer = BaasContainerInitializer(baas_service)
        self._template_resolver = template_resolver
        self._template_service = template_service
        logger.info("[BaasDeviceService] Initialized")

    # ------------------------------------------------------------------
    # provider=baas lifecycle hooks (apply_device template method)
    # ------------------------------------------------------------------

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
        """BaaS-managed devices don't need OCB-side directory prep here —
        the selected BaaS template owns its own mount points and entry point.
        Returning [] keeps the parent apply_device flow happy without writing
        any NAS-related state into device_props.
        """
        return []

    def _do_allocate(
        self,
        *,
        entity_id: str,
        entity_type: str,
        bolt_id: str,
        device_id: str,
        storage_mappings: list[NasMappingInfo],
        env: str,
        agent_pass_token: str = "",
        agent_code: str | None = None,
        engine: str = DEFAULT_ENGINE_TYPE,
        bot_type: str = "",
        bot_id: str | None = None,
        owner_id: str | None = None,
        extra_envs: dict[str, str] | None = None,
        template_type: str | None = None,
        template_config: dict | None = None,
    ) -> AllocatedDevice:
        """Create a BaaS-managed device via ``POST /api/v1/bots`` and
        ``approve_publish``. Returns an ``AllocatedDevice`` whose
        ``device_props`` carry ``bot_uuid`` + ``publish_id`` so
        ``_start_service`` (polling) and ``_do_release`` (destroy) can use them.
        """
        bot_name = bolt_id  # parent uses bot_id for naming; bot_name passed in via extra_envs not available here
        return self._allocate_via_baas(
            entity_id=entity_id,
            entity_type=entity_type,
            bolt_id=bolt_id,
            device_id=device_id,
            env=env,
            engine=engine,
            bot_type=bot_type,
            owner_id=owner_id,
            bot_name=bot_name,
            bot_desc=None,
            extra_envs=extra_envs,
            template_type=template_type,
            template_config=template_config,
        )

    def _do_allocate_nas(
        self,
        *,
        entity_id: str,
        entity_type: str,
        bolt_id: str,
        device_id: str,
        env: str,
        bot_type: str = "",
        create_bot_type: str = "",
        owner_id: str | None = None,
        engine: str = DEFAULT_ENGINE_TYPE,
        agent_pass_token: str = "",
        agent_code: str | None = None,
        bot_id: str | None = None,
        extra_envs: dict[str, str] | None = None,
        template_type: str | None = None,
        template_config: dict | None = None,
    ) -> AllocatedDevice:
        """BaaS-managed devices don't distinguish OSS vs NAS at the OCB
        layer — the selected template owns its storage strategy. Both
        ``_do_allocate`` and ``_do_allocate_nas`` delegate to the same path."""
        return self._allocate_via_baas(
            entity_id=entity_id,
            entity_type=entity_type,
            bolt_id=bolt_id,
            device_id=device_id,
            env=env,
            engine=engine,
            bot_type=create_bot_type or bot_type,
            owner_id=owner_id,
            bot_name=bolt_id,
            bot_desc=None,
            extra_envs=extra_envs,
            template_type=template_type,
            template_config=template_config,
        )

    def enqueue_create_publish_poll(
        self,
        *,
        binding_id: int,
        bot_id: str,
        owner_id: str,
        publish_id: int,
    ) -> bool:
        return baas_publish_lifecycle.enqueue_create_publish_poll(
            self._task_queue_service,
            binding_id=binding_id,
            bot_id=bot_id,
            owner_id=owner_id,
            publish_id=publish_id,
        )

    @override
    def _after_binding_persisted(
        self,
        *,
        binding_id: int,
        allocated: AllocatedDevice,
        bot_id: str,
        owner_id: str,
        device_props: dict,
    ) -> bool:
        return baas_publish_lifecycle.handle_after_binding_persisted(
            task_queue_service=self._task_queue_service,
            mark_service_start_failed=self._mark_service_start_failed,
            binding_id=binding_id,
            allocated=allocated,
            bot_id=bot_id,
            owner_id=owner_id,
            device_props=device_props,
        )

    def _allocate_via_baas(
        self,
        *,
        entity_id: str,
        entity_type: str,
        bolt_id: str,
        device_id: str,
        env: str,
        engine: str,
        bot_type: str,
        owner_id: str | None,
        bot_name: str,
        bot_desc: str | None,
        extra_envs: dict[str, str] | None,
        template_type: str | None,
        template_config: dict | None = None,
    ) -> AllocatedDevice:
        request_id = _generate_request_id(
            bot_id=bolt_id,
            entity_id=entity_id,
            entity_type=entity_type,
            env=env,
            action="create",
        )

        effective_owner_id = owner_id or entity_id
        effective_bot_type = (bot_type or "").strip()
        if not effective_bot_type:
            raise BaasDeviceServiceError(
                "bot_type is required for provider=baas allocation"
            )

        if effective_bot_type not in {"personal", "service"}:
            raise BaasDeviceServiceError(
                f"unsupported bot_type for provider=baas allocation: {effective_bot_type}"
            )

        create_error_prefix = (
            "BaaS service draft create failed"
            if effective_bot_type == "service"
            else "BaaS bot create failed"
        )
        try:
            from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError
            from agentclaw.community.core.service_bot.types import PublishStage

            # template_uid 是上层业务选择 template 的稳定标识；BaaS 创建接口仍使用底层 template_uuid。
            # 这里按 system_config 映射到实际创建用的 template_uuid。
            template_uid, template_uuid = self._resolve_required_baas_template(
                bot_id=bolt_id,
                user_id=effective_owner_id,
                env=env,
                bot_type=effective_bot_type,
                engine_type=engine,
                template_type=template_type,
                template_config=template_config,
            )

            bot = {
                "bot_id": bolt_id,
                "bot_name": bot_name,
                "bot_desc": bot_desc,
                "entity_id": entity_id,
                "entity_type": entity_type,
                "active_engine": engine,
                "bot_type": effective_bot_type,
            }
            extra_properties = build_extra_properties_fail_open(
                bot_id=bolt_id,
                owner_id=effective_owner_id,
                active_engine=engine,
                bot_type=effective_bot_type,
                template_type=template_type,
                template_config=template_config,
                log_context="baas_device_service._allocate_via_baas",
            )
            # 个人 Bot 不向启动脚本传 stage；服务 Bot 草稿才显式传 draft。
            payload_kwargs = {
                "bot": bot,
                "owner_id": effective_owner_id,
                "request_id": request_id,
                "device_count": 1,
                "migration_path": "",
                "template_uuid": template_uuid,
                "auto_approve_publish": True,
                "extra_envs": extra_envs,
                "template_config": template_config,
                "extra_properties": extra_properties,
                # 个人 Bot / 服务 Bot 草稿没有 migration_path，但启动仍按 NAS home 目录运行。
                "mount_home_dir_storage": True,
            }
            if effective_bot_type == "service":
                payload_kwargs["stage"] = PublishStage.DRAFT.value
            payload = self._baas_service._build_create_bot_payload(**payload_kwargs)

            baas_result = self._lifecycle_executor.create_bot_from_payload(
                payload=payload,
                request_id=request_id,
                owner_id=effective_owner_id,
                action="baas_device_create",
                approve_comment="自动审批",
                approve_publish=False,
            )
        except BaasServiceError as e:
            raise BaasDeviceServiceError(f"{create_error_prefix}: {e}") from e
        except BaasDeviceLifecycleError as e:
            raise BaasDeviceServiceError(f"{create_error_prefix}: {e}") from e
        except BaasTemplateResolveError as e:
            raise BaasDeviceServiceError(f"{create_error_prefix}: {e}") from e

        logger.info(
            "[baas_device_create] device_id=%s bot_id=%s bot_uuid=%s publish_id=%s "
            "bot_type=%s engine_type=%s template_type=%s template_uid=%s template_uuid=%s",
            baas_result.bot_uuid,
            bolt_id,
            baas_result.bot_uuid,
            baas_result.publish_id,
            effective_bot_type,
            engine,
            template_type,
            template_uid,
            template_uuid,
        )

        return AllocatedDevice(
            # device_id 落 BaaS 真实 bot_uuid,与 service bot 发布态口径统一:
            # 全系统约定 binding.device_id == BaaS bot_uuid,get_ws_info/http_info
            # 直接拿它查 BaaS,无需 props 兜底。回调由 OCB 自发
            # (report_device_alive(device_id=device.device_id)),落 bot_uuid 后自洽。
            # 原本地拼装 id(staff_<owner>_<bot>_<hex>)留 props.local_device_id 备查。
            device_id=baas_result.bot_uuid,
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={
                "bot_uuid": baas_result.bot_uuid,
                "local_device_id": device_id,
                "publish_id": baas_result.publish_id,
                "device_from": "baas",
                # request_id 留底排障用,update 时复用同一个保持幂等不在本期范围
                "create_request_id": request_id,
                # passthrough envs for diagnostics
                "envs": dict(extra_envs) if extra_envs else {},
                "template_uid": template_uid,
                "template_uuid": template_uuid,
            },
        )

    def _resolve_required_baas_template(
        self,
        *,
        bot_id: str,
        user_id: str,
        env: str,
        bot_type: str,
        engine_type: str | None,
        template_type: str | None,
        template_config: dict | None,
    ) -> tuple[str, str]:
        """校验上层传入的 template_uid，并解析为 BaaS 需要的 template_uuid。"""
        template_uid = self._extract_required_template_uid(template_config)
        template = self._template_resolver.resolve_template(
            bot_id=bot_id,
            user_id=user_id,
            env=env,
            bot_type=bot_type,
            engine_type=engine_type,
            template_type=template_type,
            template_config=template_config,
        )
        return template_uid, template.template_uuid

    @staticmethod
    def _extract_required_template_uid(template_config: dict | None) -> str:
        """从 template_config 中取出并校验上层传入的 template_uid。"""
        if not isinstance(template_config, dict):
            raise BaasTemplateResolveError(
                "provider=baas allocation requires template_config.template_uid"
            )
        resolution_error = template_config.get("_baas_template_uid_resolution_error")
        error_detail = (
            f"; previous template_uid resolution failed: {resolution_error}"
            if isinstance(resolution_error, str) and resolution_error
            else ""
        )
        template_uid = template_config.get("template_uid")
        if not isinstance(template_uid, str) or not template_uid.strip():
            raise BaasTemplateResolveError(
                "provider=baas allocation requires non-empty "
                f"template_config.template_uid{error_detail}"
            )
        return template_uid.strip()

    @override
    def _exec_shell_new(self, device: AllocatedDevice, shell_cmd: str):
        """在 BaaS Bot 容器内执行命令。"""
        return execute_baas_shell_command(
            baas_service=self._baas_service,
            device=device,
            shell_cmd=shell_cmd,
        )

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
        return baas_publish_lifecycle.run_start_service_polling(
            baas_service=self._baas_service,
            device=device,
            run_container_init=self._run_container_init,
            report_device_alive=self.report_device_alive,
            engine=engine,
            bot_type=bot_type,
            bot_id=bot_id,
            owner_id=owner_id,
            admins=admins,
            codefuse_token=codefuse_token,
        )

    def poll_publish_once(self, *, publish_id: int) -> str | None:
        return baas_publish_lifecycle.poll_publish_once(
            baas_service=self._baas_service,
            publish_id=publish_id,
        )

    def refresh_codefuse_token_on_publish_success(
        self,
        *,
        bot_uuid: str | None,
        codefuse_token: str | None,
    ) -> str | None:
        return baas_publish_lifecycle.refresh_codefuse_token_on_publish_success(
            baas_service=self._baas_service,
            vault=self._vault,
            bot_uuid=bot_uuid,
            codefuse_token=codefuse_token,
        )

    def run_create_init_once(
        self,
        *,
        binding_id: int,
        bot_id: str,
        owner_id: str,
        publish_id: int,
    ) -> tuple[bool, str]:
        binding = self._repo.get_by_id(binding_id)
        if binding is None:
            return False, f"binding not found: {binding_id}"
        if binding.status == DeviceBindingStatus.ACTIVE.value:
            return True, f"binding {binding_id} already ACTIVE"

        props = binding.device_props or {}
        current_publish_id = props.get("publish_id")
        if current_publish_id is None or str(current_publish_id) != str(publish_id):
            return False, f"stale publish_id for binding {binding_id}: {publish_id}"

        bot = self._resolve_bot_by_binding_id(binding_id)
        if bot is None:
            return False, f"bot not found for binding_id={binding_id}"

        raw_active_engine = str(bot.get("active_engine") or "").strip() or None
        resolved_engine = raw_active_engine or DEFAULT_ENGINE_TYPE
        resolved_bot_type = str(bot.get("bot_type") or "")
        resolved_bot_id = str(bot.get("bot_id") or bot_id or "")
        resolved_owner_id = str(bot.get("owner_id") or owner_id or "")
        admins = bot.get("admins")
        if not isinstance(admins, list):
            admins = None

        template_type = bot.get("template_type")

        bot_uuid = str(props.get("bot_uuid") or "")
        if not bot_uuid:
            return False, "missing bot_uuid in device_props"
        callback_token = str(props.get("callback_token") or "")
        device = AllocatedDevice(
            device_id=binding.device_id,
            device_provider=binding.device_provider,
            device_props=props,
        )

        # ac_bots only owns template_type; token/model/runtime live in
        # ac_templates.ext.  Create-init runs from a durable task after the
        # synchronous create path, so reload template_config from TemplateService
        # just like the BaaS restart path does.
        if (
            self._template_service is None
            and should_encrypt_template_token_fail_open(
                bot_id=resolved_bot_id,
                owner_id=resolved_owner_id,
                active_engine=raw_active_engine,
                bot_type=resolved_bot_type,
                template_type=template_type,
                template_config=None,
                log_context="baas_device_service.create_init",
            )
        ):
            return False, (
                "template_service required for coding bot create init: "
                f"bot_id={resolved_bot_id}"
            )

        template_config = None
        if self._template_service is not None:
            try:
                template_config = self._template_service.get_template_config(resolved_bot_id)
            except Exception as exc:
                logger.warning(
                    "[run_create_init_once] failed to reload template config "
                    "for bot_id=%s: %s",
                    resolved_bot_id,
                    exc,
                )
        if not isinstance(template_config, dict):
            template_config = {}

        # Resolve the runtime token with the freshly loaded template_config.
        # Engine-specific knowledge lives in the strategy; this caller only
        # passes the reloaded config and gains token resolution fail-soft here
        # (decrypt follows below; a None token means no CodeFuse token to inject).
        raw_codefuse_token = extract_runtime_token_fail_open(
            bot_id=resolved_bot_id,
            owner_id=resolved_owner_id,
            active_engine=raw_active_engine,
            bot_type=resolved_bot_type,
            template_type=template_type,
            template_config=template_config,
            log_context="baas_device_service.create_init_token",
        )
        codefuse_token = (
            self._vault.decrypt_or_passthrough(raw_codefuse_token)
            if raw_codefuse_token
            else None
        )

        try:
            self._run_container_init(
                bot_uuid=bot_uuid,
                device=device,
                engine=resolved_engine,
                bot_type=resolved_bot_type,
                bot_id=resolved_bot_id,
                owner_id=resolved_owner_id,
                callback_token=callback_token,
                admins=admins,
                codefuse_token=codefuse_token,
            )
        except Exception as exc:
            logger.exception(
                "[run_create_init_once] container init failed: binding_id=%s error=%s",
                binding_id,
                exc,
            )
            return False, f"container init failed: {exc}"

        try:
            self.report_device_alive(
                device_id=binding.device_id,
                token=callback_token,
            )
        except Exception as exc:
            logger.exception(
                "[run_create_init_once] report_device_alive failed: "
                "binding_id=%s device_id=%s error=%s",
                binding_id,
                binding.device_id,
                exc,
            )
            return False, f"report_device_alive failed: {exc}"

        return True, "BaaS init done, device active"

    # ------------------------------------------------------------------
    # Container init helpers (aligned with ArcaDeviceService 6-step)
    # ------------------------------------------------------------------

    def _run_container_init(
        self,
        *,
        bot_uuid: str,
        device: AllocatedDevice,
        engine: str,
        bot_type: str,
        bot_id: str | None,
        owner_id: str | None,
        callback_token: str,
        admins: list[str] | None,
        codefuse_token: str | None = None,
    ) -> None:
        """Execute the BaaS post-publish init sequence."""
        time.sleep(2)
        self._container_initializer.run(
            bot_uuid=bot_uuid,
            device=device,
            engine=engine,
            bot_type=bot_type,
            bot_id=bot_id,
            owner_id=owner_id,
            callback_token=callback_token,
            admins=admins,
            codefuse_token=codefuse_token,
        )

    def _write_codefuse_token(self, bot_uuid: str, codefuse_token: str | None) -> None:
        """Compatibility wrapper for focused CodeFuse unit tests."""
        self._container_init()._write_codefuse_token(bot_uuid, codefuse_token)

    def _ensure_baas_engine_dirs(self, bot_uuid: str, engine: str) -> None:
        self._container_init()._ensure_baas_engine_dirs(bot_uuid, engine)

    def _create_baas_skill_symlink_conf(self, bot_uuid: str, symbol: str | None) -> None:
        self._container_init()._create_baas_skill_symlink_conf(bot_uuid, symbol)

    def _start_baas_sandbox_service(self, **kwargs) -> None:
        self._container_init()._start_baas_sandbox_service(**kwargs)

    @staticmethod
    def _deserialize_symbol(symbol: str | None) -> list:
        return _deserialize_symbol(symbol)

    def _container_init(self) -> BaasContainerInitializer:
        initializer = getattr(self, "_container_initializer", None)
        if initializer is None:
            initializer = BaasContainerInitializer(self._baas_service)
            self._container_initializer = initializer
        return initializer

    def _do_release(self, *, device: AllocatedDevice) -> bool:
        """Destroy the BaaS bot. Operator is recovered from ``device_props``
        because the parent ``release_device`` doesn't forward the
        ``OperatorContext`` into this hook.
        """
        bot_uuid = device.device_props.get("bot_uuid") or device.device_id
        operator = device.device_props.get("entity_id", "")
        if not bot_uuid:
            logger.warning(
                "[_do_release] no bot_uuid for device_id=%s, skipping BaaS destroy",
                device.device_id,
            )
            return True

        request_id = _generate_request_id(
            bot_id=device.device_props.get("bolt_id", "") or device.device_id,
            entity_id=operator,
            entity_type=device.device_props.get("entity_type", "staff"),
            env=device.device_props.get("env", ""),
            action="destroy",
        )

        try:
            self._lifecycle_executor.destroy_bot(
                bot_uuid=bot_uuid,
                operator=operator,
                request_id=request_id,
            )
        except BaasDeviceLifecycleError as e:
            logger.error(
                "[_do_release] BaaS destroy_bot failed: device_id=%s bot_uuid=%s error=%s",
                device.device_id, bot_uuid, e,
            )
            raise BaasDeviceServiceError(f"BaaS destroy_bot failed: {e}") from e

        logger.info(
            "[_do_release] BaaS bot destroyed: device_id=%s bot_uuid=%s",
            device.device_id, bot_uuid,
        )
        return True

    def _query_device_info(self, *, device: AllocatedDevice) -> dict:
        """Query underlying BaaS device(s) for diagnostic info.

        provider=baas currently maps one binding to one BaaS bot_uuid; return
        the first row plus the publish_id tracked at create time.
        """
        bot_uuid = device.device_props.get("bot_uuid") or device.device_id

        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        try:
            devices = self._baas_service.list_devices_by_bot_uuid(bot_uuid)
        except BaasServiceError as e:
            logger.warning(
                "[_query_device_info] list_devices failed: bot_uuid=%s error=%s",
                bot_uuid, e,
            )
            devices = []

        first = devices[0] if devices else {}
        return {
            "bot_uuid": bot_uuid,
            "publish_id": device.device_props.get("publish_id", ""),
            "device": first,
            "device_count": len(devices),
        }

    # ------------------------------------------------------------------
    # Pre-existing helpers (desktop / service bot already use them)
    # ------------------------------------------------------------------

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
        """获取设备连接信息。

        覆写父类方法，通过 BaaS 层 API 获取 WebSocket 连接信息。

        Args:
            binding_id: 设备绑定 ID
            operator: 操作者上下文
            port: 端口号（BaaS 层忽略，由服务端决定）
            ttl: TTL（BaaS 层忽略，由服务端决定）
            device_uuid: 多实例场景锁定特定实例（可选）；不传则 BaaS 自动选活跃实例
            ws_conn_mode: WebSocket 连接模式透传（可选）；不传则不覆盖
            path: 目标 in-device 路径（可选，自带前导斜杠）；BaaS 用它拼 ``ws_url``，
                故 relay 下它决定 URL 指向哪个引擎 socket，不传落到默认
                ``/api/openclaw/ws``

        Returns:
            DeviceConnectionInfo: 设备连接信息

        Raises:
            DeviceNotFoundError: 设备绑定不存在
            BaasDeviceServiceError: 获取连接信息失败
        """
        logger.info(f"[BaasDeviceService.get_device_connection] Getting connection for binding_id={binding_id}, device_uuid={device_uuid}")

        # 延迟导入避免循环依赖
        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        try:
            ws_info = self._baas_service.get_ws_info(
                bind_id=binding_id,
                device_affinity=operator.staff_id,
                device_uuid=device_uuid,
                ws_conn_mode=ws_conn_mode,
                # Verbatim, leading slash included. BaaS appends this to the
                # routing target with no separator of its own
                # (``build_proxypass_url`` → ``…/proxypass/{target}{path}``), so
                # stripping the slash published ``…@0:20003api/openclaw/ws`` — a
                # URL whose handshake cannot reach the engine.
                # Only override when asked; None would blank get_ws_info's default.
                **({"path": path} if path else {}),
            )
        except BaasServiceError as e:
            logger.error(f"[BaasDeviceService.get_device_connection] BaaS error: {e}")
            raise BaasDeviceServiceError(f"Failed to get device connection: {e}") from e

        # 判断连接类型：桌面 bot 返回 "desktop"，服务 bot 返回 "baas"
        conn_type = "baas"
        bot = self._resolve_bot_by_binding_id(binding_id)
        if bot and bot.get("bot_type") == "desktop":
            conn_type = "desktop"

        engine_type = (bot.get("active_engine") if bot else None) or self._default_engine

        result = DeviceConnectionInfo(
            type=conn_type,
            target=ws_info.target,
            token=ws_info.token,
            engine_type=engine_type,
            url=ws_info.ws_url if ws_conn_mode == "relay" else "",
            baas_base_url=ws_info.baas_base_url,
            bot_uuid=ws_info.bot_uuid,
            tenant=ws_info.tenant,
            engine_port=ws_info.engine_port,
            # 服务端签发；ttl 入参本层忽略，caller 自行推算必然与真 token 不符。
            # 本链路 token 就是 ws token，故不另填 ws_*（留空即"用 token"）。
            expires_at=ws_info.expires_at,
        )

        logger.info(
            f"[BaasDeviceService.get_device_connection] Got connection: "
            f"binding_id={binding_id}, type={conn_type}, target={result.target}, engine_type={engine_type}"
        )

        return result

    def _resolve_bot_by_binding_id(self, binding_id: int) -> dict[str, Any] | None:
        """根据 binding_id 获取 Bot 信息，兼容桌面 BOT 和服务 BOT。

        优先通过 ac_bots.binding_id 直接查询（桌面 BOT），
        查不到则通过 ac_entity_device_binding 获取 bolt_id + entity_id 再查 ac_bots。
        """
        bot = self._bot_query.get_by_binding_id(binding_id)
        if bot is not None:
            return bot

        binding = self._repo.get_by_id(binding_id)
        if binding is None:
            return None

        bolt_id = binding.device_props.get("bolt_id", "") if binding.device_props else ""
        if not bolt_id:
            return None

        return self._bot_query.get_by_id_and_owner(bolt_id, binding.entity_id)

    def update_device_headers(
        self,
        *,
        device: AllocatedDevice,
        agent_pass_token: str = "",
        agent_code: str = "",
        active_only: bool = False,
    ) -> list[dict]:
        """热更新 BaaS 设备出站 header 规则。"""
        try:
            return self._header_updater.update(
                device=device,
                agent_pass_token=agent_pass_token,
                agent_code=agent_code,
                active_only=active_only,
            )
        except BaasDeviceHeaderUpdateError as e:
            raise BaasDeviceServiceError(str(e)) from e
