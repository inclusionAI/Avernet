"""Device Service — 设备业务逻辑层.

This module implements the core business logic for device management.
It consolidates the template method pattern from the former BaseDeviceService,
with unified imports from core/devices/ and core/devices/repository/.

根据 README.md 分层规范:
- 只通过 core/devices/repository/protocol.py 中的 Protocol 访问数据
- 不直接 import plugins
- 业务异常用 core/devices/errors.py 中的异常类
"""
from __future__ import annotations

import base64
import json
import secrets
import threading
import uuid
from typing import Callable, Literal, Optional, TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.data_init_service import DataInitService
    from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
    from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient

from agentclaw.community.core.devices.repository.protocol import (
    DeviceBindingRepository,
    OssToNasRecordRepository,
)
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.models import (
    AllocatedDevice,
    DeviceBindingInfo,
    DeviceBindingStatus,
    DeviceConnectionInfo,
    EntityType,
    NasMappingInfo,
    OperatorContext,
    SynlinkMappingInfo,
)
from agentclaw.community.core.devices.errors import (
    DeviceServiceError,
    DeviceNotFoundError,
    InvalidDeviceStatusError,
)
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.devices.protocols import (
    BotQueryProtocol,
    BotSyncProtocol,
    McpSyncProtocol,
)
from agentclaw.community.log import get_logger

logger = get_logger()

from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE  # noqa: E402

# Provider constants — single source of truth
LOCAL_DEVICE_PROVIDER = "local"
ARCA_DEVICE_PROVIDER = "arca"
BAAS_DEVICE_PROVIDER = "baas"

T = TypeVar("T")


class DeviceService:
    """设备管理服务基类.

    Provides business logic for device lifecycle management including:
    - Device application and validation (template method with hooks)
    - Device release
    - Device status queries
    - Device connections
    - Bot status callbacks

    Subclasses override hook methods for provider-specific behavior:
    - _exec_shell: Execute shell commands on the device
    - _setup_directory: Initialize directories for the device
    - _do_allocate: Allocate the actual device resource
    - _do_release: Release the actual device resource
    - _start_service: Start services on the device
    - _query_device_info: Query device information
    - _compose_device_conn_info: Compose connection information

    This service is independent of HTTP concerns and uses the
    DeviceBindingRepository Protocol for data access.
    """

    def __init__(
        self,
        repository: DeviceBindingRepository,
        default_engine: str = DEFAULT_ENGINE_TYPE,
        *,
        bot_query: "BotQueryProtocol",
        bot_sync: "BotSyncProtocol",
        oss_record_repo: "OssToNasRecordRepository",
        mcp_sync: "McpSyncProtocol",
        data_init_service_provider: "Optional[Callable[[], DataInitService]]" = None,
        vault: "Optional[TokenVault]" = None,
        sandbox_client: "Optional[SandboxRuntimeClient]" = None,
        task_queue_service: "TaskQueueService | None" = None,
    ):
        """Initialize device service.

        Args:
            repository: Device repository implementation
            default_engine: Default engine type for device connections
            bot_query: Bot query protocol (required, injected via DI)
            bot_sync: Bot sync protocol (required, injected via DI)
            oss_record_repo: OSS-to-NAS 迁移记录仓库
            mcp_sync: MCP 配置同步协议（required，设备激活时同步 MCP 到设备）
            data_init_service_provider: Lazy factory for ``DataInitService``;
                breaks the DataInitService ↔ DeviceService construction
                cycle. ``None`` is permitted for direct-construction paths
                (unit tests) where data-init callbacks are not exercised.
            vault: ``TokenVault`` 用于 ``apply_device`` 读回 token 时
                解密（密文→明文）。``None`` 时退化为空 key vault（passthrough），
                保证既有直接构造路径（单测）不炸；DI 装配点传真实 vault 单例。
        """
        self._repo = repository
        self._default_engine = default_engine
        self._bot_query = bot_query
        self._bot_sync = bot_sync
        self._oss_record_repo = oss_record_repo
        self._mcp_sync = mcp_sync
        self._data_init_service_provider = data_init_service_provider
        self._vault = vault or TokenVault(master_key="")
        # Sandbox-runtime client for the ARCA-proxy branch of
        # get_device_connection_v2 (proxy base/target). ``None`` on direct-
        # construction paths (unit tests) that never hit that branch.
        self._sandbox_client = sandbox_client
        self._task_queue_service = task_queue_service
        logger.info("[DeviceService] Initialized")

    # =========================================================================
    # Helper methods (from former BaseDeviceService)
    # =========================================================================

    def _effective_binding_status(self, record) -> str:
        """The binding's status for gating/listing — the stored column.

        Every engine (teclaw included) now reads from the stored binding status:
        the TeclawStatusReconciler persists a teclaw container's resolved status
        onto the column post-provision, so there is no per-read baas probe.
        """
        return record.status

    def _get_collaborator_service(self):
        """获取 CollaboratorService 实例。"""
        try:
            from agentclaw.community.di import get_app_injector
            from agentclaw.community.core.bot_collaborator.services.collaborator_service import CollaboratorService
            injector = get_app_injector()
            return injector.get(CollaboratorService)
        except Exception as e:
            logger.warning(f"[_get_collaborator_service] Failed to get CollaboratorService: {e}")
            return None

    @staticmethod
    def safe_b64decode(data: str) -> bytes:
        """Safely decode base64 data, auto-padding if necessary."""
        missing_padding = len(data) % 4
        if missing_padding:
            data += "=" * (4 - missing_padding)
        return base64.b64decode(data)

    @staticmethod
    def _generate_callback_token() -> str:
        """Generate callback token for device."""
        return secrets.token_urlsafe(32)

    # =========================================================================
    # Hook methods — subclasses override these for provider-specific behavior
    # =========================================================================

    def _exec_shell(self, device: AllocatedDevice, shell_cmd: str) -> str:
        """Execute shell command on device (hook — subclasses override)."""
        return f"Command executed on {device.device_id}: {shell_cmd}"

    def _exec_shell_new(self, device: AllocatedDevice, shell_cmd: str):
        """Execute shell command on device and return CommandResult (hook — subclasses override).

        Returns:
            CommandResult object with stdout, stderr, exit_code, etc.
        """
        logger.info(f"[DeviceService._exec_shell_new] Arca _exec_shell_new: {shell_cmd}")
        from agentclaw.community.kernel.device_dto import CommandResult
        return CommandResult(
            stdout=f"Command executed on {device.device_id}: {shell_cmd}",
            stderr="",
            exit_code=0,
            elapsed_time=0,
            status="completed",
            error=None,
        )

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
        """Set up directories for the device (hook — subclasses override).

        Returns:
            List of NAS mappings
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
        """Allocate the actual device resource (hook — subclasses override).

        Default: returns a local device.
        """
        return AllocatedDevice(
            device_id=device_id,
            device_provider=LOCAL_DEVICE_PROVIDER,
            device_props={"client_id": device_id, "bolt_id": bolt_id},
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
        """NAS 模式设备分配（hook — 子类覆盖）。

        与 _do_allocate 的区别：
        - 无 storage_mappings（不需要 OSS 挂载配置）
        - 使用 SDK Storage 对象（NAS 挂载配置）
        - bot_type 即 active_engine（如 "openclaw"/"moltis"），用于 storage_id 隔离
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support NAS storage mode"
        )

    def _do_release(self, *, device: AllocatedDevice) -> bool:
        """Release the actual device resource (hook — subclasses override)."""
        return True

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
        """Start services on the device (hook — subclasses override).

        Args:
            device: The allocated device
            nas_mappings: NAS mapping info list
            engine: Engine type (e.g., "openclaw", "moltis")
            bot_type: Bot type (typically same as engine, used for start_service.sh)
            bot_id: Bot ID
            owner_id: Owner ID
            admins: List of admin user IDs
            codefuse_token: CodeFuse SSO auth_code（base64）。仅 applicationCoding bot
                由 apply_device 透传非空值；provider 在容器就绪初始化里解码后写
                codefuse.json。base 默认实现不处理（Noop）。

        Returns:
            Tuple of (success, message)
        """
        return True, "Service start not implemented for this provider"

    def _after_binding_persisted(
        self,
        *,
        binding_id: int,
        allocated: AllocatedDevice,
        bot_id: str,
        owner_id: str,
        device_props: dict,
    ) -> bool:
        """Provider hook after binding persistence.

        Return True when the provider has taken over lifecycle advancement and
        the generic async _start_service path should be skipped.
        """
        return False

    def _query_device_info(
        self,
        *,
        device: AllocatedDevice,
    ) -> dict:
        """Query device information (hook — subclasses override)."""
        return {}

    def update_device_headers(
        self,
        *,
        device: AllocatedDevice,
        agent_pass_token: str = "",
        agent_code: str = "",
    ) -> bool:
        """Update outbound header rules on a running device (hot-update hook)。

        Subclasses override this to provider-specific implementations.
        Default: no-op.
        """
        return False

    def _compose_device_conn_info(
        self,
        *,
        device: AllocatedDevice,
        port: int | None = None,
        ttl: int | None = None,
    ) -> DeviceConnectionInfo:
        """Compose device connection information (hook — subclasses override).

        Returns:
            Device connection info
        """
        return DeviceConnectionInfo(
            type=device.device_provider,
            target=f"localhost:{port or 20003}",
            token="",
            engine_type=self._default_engine,
        )

    def _reset_openclaw_config(
        self,
        allocated_device: AllocatedDevice,
        *,
        entity_id: str,
        entity_type: str,
    ) -> None:
        """Reset openclaw configuration (hook — subclasses override)."""
        pass

    # =========================================================================
    # Device ID generation helpers (from former BaseDeviceService)
    # =========================================================================

    def _generate_device_id(
        self,
        *,
        entity_id: str,
        entity_type: str,
        is_first: bool = True,
    ) -> tuple[str, str]:
        """Generate device ID.

        Returns:
            (device_id, bolt_id) tuple
        """
        if is_first:
            bolt_id = "default"
        else:
            from datetime import datetime
            timestamp = datetime.now().strftime("%y%m%d%H")
            suffix = secrets.token_hex(2)
            bolt_id = f"{timestamp}_{suffix}"

        device_id = f"{entity_type}_{entity_id}_{bolt_id}"
        return device_id, bolt_id

    def _validate_and_generate_device_id(
        self,
        *,
        entity_id: str,
        entity_type: str,
        env: str,
        bot_id: str = "default",
    ) -> tuple[str, str, DeviceBindingRecord | None]:
        """Validate and generate device ID.

        Generates appropriate device ID and checks for released devices that can be reused.

        Returns:
            (device_id, bolt_id, released_binding) tuple
        """
        uuid_suffix = uuid.uuid4().hex
        device_id = f"{entity_type}_{entity_id}_{bot_id}_{uuid_suffix}"

        has_device = self._repo.exists_device_id(device_id=device_id)

        logger.info(
            f"[_validate_and_generate_device_id] entity_id={entity_id}, "
            f"entity_type={entity_type}, env={env}, bot_id={bot_id}, "
            f"has_device={has_device}"
        )

        released_binding = self._repo.get_released_binding(device_id=device_id)
        if released_binding is not None:
            logger.info(f"[_validate_and_generate_device_id] found released binding for {device_id}, will reuse")
            return device_id, bot_id, released_binding

        if has_device:
            new_uuid_suffix = uuid.uuid4().hex
            device_id = f"{entity_type}_{entity_id}_{bot_id}_{new_uuid_suffix}"
            logger.info(f"[_validate_and_generate_device_id] default device_id exists, generated new: {device_id}")
            released_binding = self._repo.get_released_binding(device_id=device_id)
            if released_binding is not None:
                return device_id, bot_id, released_binding

        return device_id, bot_id, None

    def _resolve_env_dir(self, env: str) -> str:
        """Resolve environment directory prefix."""
        if env == "prod":
            return "aidesktop_prod"
        elif env == "pre":
            return "aidesktop_pre"
        return "aidesktop_dev"

    def _resolve_entity_dir(self, entity_id: str, entity_type: str) -> str:
        """Resolve entity directory name."""
        return f"{entity_type}_{entity_id}"

    def _resolve_data_dir(
        self,
        *,
        aidesktop_root: str,
        entity_id: str,
        entity_type: str,
        bot_id: str,
        engine: Literal["moltis", "openclaw", "aicoding", "claude_code"],
        env: str,
    ) -> tuple:
        """Resolve data and config directories for a device.

        Returns:
            Tuple of (data_dir, conf_dir) as Path objects
        """
        from pathlib import Path
        env_dir = self._resolve_env_dir(env)
        entity_dir = self._resolve_entity_dir(entity_id=entity_id, entity_type=entity_type)
        device_base_dir = Path(aidesktop_root) / env_dir / "bolt_data" / entity_dir / bot_id

        data_dir = device_base_dir / engine
        conf_dir = device_base_dir / f"{engine}_conf"

        return data_dir, conf_dir

    # =========================================================================
    # OSS-to-NAS migration check
    # =========================================================================

    def _get_storage_mode(self, entity_id: str, bot_id: str) -> str:
        """查询当前 bot 的存储模式。

        Returns:
            'oss'：走原有 OSS 流程（无记录或 status=oss）
            'nas'：走 NAS 新流程

        Raises:
            RuntimeError: status 为 'switching' 或 'failed' 时
        """
        try:
            record = self._oss_record_repo.get_record(entity_id, bot_id)
        except Exception as e:
            # 表不存在或查询失败时 fallback 到 OSS 流程
            logger.info(f"[apply_device] Migration status check skipped: {e}")
            return "oss"
        logger.info(f"[apply_device] Migration status check skipped: {record}")

        if record is None or record["storage_status"] == "oss":
            return "oss"

        status = record["storage_status"]
        if status == "nas":
            return "nas"
        elif status == "switching":
            raise RuntimeError(f"Bot {entity_id}/{bot_id} 正在迁移中，请稍后重试")
        elif status == "failed":
            raise RuntimeError(f"Bot {entity_id}/{bot_id} 迁移失败，请联系运维处理")
        return "oss"

    # =========================================================================
    # Core business methods — template method pattern
    # =========================================================================

    def apply_device(
        self,
        *,
        apply_reason: str | None,
        entity_id: str | None,
        entity_type: str | None,
        operator: OperatorContext,
        bot_id: str | None = None,
        engine: str = DEFAULT_ENGINE_TYPE,
        bot_type: str = "",
        owner_id: str | None = None,
        symbol: list[SynlinkMappingInfo] | None = None,
        force_nas: bool = False,
        extra_envs: dict[str, str] | None = None,
        admins: list[str] | None = None,
        template_type: str | None = None,
        template_config: dict | None = None,
    ) -> DeviceBindingRecord | None:
        """Apply for a device — template method with provider hooks.

        Lifecycle:
        1. Resolve parameters
        2. Validate and generate device ID
        3. Set up directories (hook: _setup_directory)
        4. Allocate device (hook: _do_allocate)
        5. Generate callback token
        6. Create/reuse database record
        7. Start services asynchronously (hook: _start_service)

        Args:
            apply_reason: Reason for applying
            entity_id: Entity ID (e.g., user ID)
            entity_type: Entity type (e.g., "staff")
            operator: Operator context
            bot_id: Bot ID
            engine: Engine type (e.g., "openclaw", "moltis")
            bot_type: Bot type from bot object (e.g., "personal", "service", "desktop")
            owner_id: Owner ID
            symbol: Symlink mapping info list
            force_nas: Force NAS storage mode
            extra_envs: Extra environment variables
            admins: List of admin user IDs
            template_type: Template type
            template_config: Template configuration
        """
        # Resolve parameters
        resolved_entity_id = entity_id if entity_id else operator.staff_id
        resolved_entity_type = entity_type if entity_type else EntityType.STAFF.value
        resolved_bot_id = bot_id or "default"
        resolved_engine = engine or DEFAULT_ENGINE_TYPE
        resolved_owner_id = owner_id if owner_id else resolved_entity_id

        from agentclaw.community.utils import env_utils
        env = env_utils.get_current_env()

        # Check OSS-to-NAS migration status
        storage_mode = self._get_storage_mode(resolved_entity_id, resolved_bot_id)
        logger.info(f"[apply_device] Storage mode: {storage_mode}")

        # 0. Validate and generate device ID
        device_id, bolt_id, released_binding = self._validate_and_generate_device_id(
            entity_id=resolved_entity_id,
            entity_type=resolved_entity_type,
            env=env,
            bot_id=resolved_bot_id,
        )

        # 1. Directory setup + device allocation (storage_mode 分支)
        if force_nas or storage_mode == "nas":
            # NAS 流程：跳过 _setup_directory（不需要 OSS 目录初始化）
            nas_mappings: list[NasMappingInfo] = []
            allocated = self._do_allocate_nas(
                entity_id=resolved_entity_id,
                entity_type=resolved_entity_type,
                bolt_id=bolt_id,
                device_id=device_id,
                env=env,
                bot_type=resolved_engine,
                create_bot_type=bot_type,
                owner_id=resolved_owner_id,
                engine=resolved_engine,
                bot_id=resolved_bot_id,
                extra_envs=extra_envs,
                template_type=template_type,
                template_config=template_config,
            )
        else:
            # OSS 流程（原逻辑不变）
            nas_mappings = self._setup_directory(
                operator=operator,
                entity_id=resolved_entity_id,
                entity_type=resolved_entity_type,
                bolt_id=bolt_id,
                env=env,
                engine=resolved_engine,
            )
            allocated = self._do_allocate(
                entity_id=resolved_entity_id,
                entity_type=resolved_entity_type,
                bolt_id=bolt_id,
                device_id=device_id,
                storage_mappings=nas_mappings,
                env=env,
                engine=resolved_engine,
                bot_type=bot_type,
                bot_id=resolved_bot_id,
                owner_id=resolved_owner_id,
                extra_envs=extra_envs,
                template_type=template_type,
                template_config=template_config,
            )

        # 3. Generate callback token
        callback_token = self._generate_callback_token()

        nas_mappings_json = [
            nm.to_dict()
            for nm in nas_mappings
            if nm is not None
        ]
        symbol_json = [
            sm.to_dict()
            for sm in (symbol or [])
            if sm is not None
        ]
        device_props = {
            **allocated.device_props,
            "nas_mappings": json.dumps(nas_mappings_json, ensure_ascii=False) if nas_mappings else "[]",
            "callback_token": callback_token,
            "bolt_id": bolt_id,
            "symbol": json.dumps(symbol_json, ensure_ascii=False) if symbol else "[]",
            "entity_id": resolved_entity_id,
            "entity_type": resolved_entity_type,
        }
        # 4. Process database record
        status = DeviceBindingStatus.PENDING.value

        if released_binding is not None:
            logger.info(f"[apply_device] reusing released device: {device_id}, status={status}")
            self._repo.reuse_binding(
                binding_id=released_binding.id,
                device_props=device_props,
                apply_reason=apply_reason,
                applied_by=operator.staff,
                status=status,
            )
            binding_id = released_binding.id
        else:
            binding_id = self._repo.insert_binding(
                entity_id=resolved_entity_id,
                entity_type=resolved_entity_type,
                device_id=allocated.device_id,
                device_provider=allocated.device_provider,
                env=env,
                device_props=device_props,
                status=status,
                apply_reason=apply_reason,
                applied_by=operator.staff,
            )

        if self._after_binding_persisted(
            binding_id=binding_id,
            allocated=allocated,
            bot_id=resolved_bot_id,
            owner_id=resolved_owner_id,
            device_props=device_props,
        ):
            record = self._repo.get_by_id(binding_id)
            if record is None:
                return None
            return record

        # 5. Start service asynchronously
        # 仅 applicationCoding bot 透传 CodeFuse token（从 ext 读回密文，启动时解密为明文）。
        # 解密在异步闭包内：密文损坏/密钥错配时由 start_service 的 except 承接为 FAILED，
        # 与 token 写入失败同语义（failure-closed），避免同步段抛错导致 binding 已落库但
        # apply_device 返回 500 的状态机不一致。token 全程 in-memory 不进 device_props；
        # get_template_config 不解密 → API 返回密文脱敏。
        _raw_codefuse_token = (
            (template_config or {}).get("token")
            if (template_type or "") == "applicationCoding"
            else None
        )

        def start_service_async():
            try:
                codefuse_token = (
                    self._vault.decrypt_or_passthrough(_raw_codefuse_token)
                    if _raw_codefuse_token
                    else None
                )
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
                    owner_id=resolved_owner_id,
                    admins=admins,
                    codefuse_token=codefuse_token,
                )
                logger.info(f"[apply_device] start service for device {allocated.device_id}: success={success}, message={message}")
                if not success:
                    self._mark_service_start_failed(
                        binding_id=binding_id,
                        error=message or "service start returned failure",
                    )
            except Exception as e:
                logger.exception(f"[apply_device] start service failed for device {allocated.device_id}: {e}")
                self._mark_service_start_failed(binding_id=binding_id, error=str(e))

        thread = threading.Thread(target=start_service_async, daemon=True)
        thread.start()

        record = self._repo.get_by_id(binding_id)
        if record is None:
            return None
        return record

    def release_device(
        self,
        *,
        binding_id: int,
        release_reason: str | None,
        reset: bool = False,
        operator: OperatorContext,
    ) -> DeviceBindingRecord | None:
        """Release a device.

        Args:
            binding_id: Binding ID to release
            release_reason: Reason for release
            reset: Whether to reset device configuration
            operator: Operator context

        Returns:
            Device binding response after release
        """
        current = self._repo.get_by_id(binding_id)
        if current is None:
            raise DeviceNotFoundError(f"binding {binding_id} not found")

        if current.status not in [
            DeviceBindingStatus.ACTIVE.value,
            DeviceBindingStatus.PENDING.value,
            DeviceBindingStatus.FAILED.value,
        ]:
            raise InvalidDeviceStatusError("only ACTIVE/PENDING/FAILED devices can be released")

        entity_id = current.entity_id
        entity_type = current.entity_type
        allocated_device = AllocatedDevice(
            device_id=current.device_id,
            device_provider=current.device_provider,
            device_props=current.device_props,
        )

        # 物理释放与 DB 状态更新解耦：即使物理释放失败，也要更新 DB 状态为 RELEASED，
        # 防止出现孤儿 PENDING/ACTIVE binding 记录
        physical_release_error = None
        try:
            self._do_release(device=allocated_device)
        except Exception as e:
            logger.error(f"[release_device] Physical release failed for binding {binding_id}: {e}")
            physical_release_error = e

        if reset:
            try:
                self._reset_openclaw_config(allocated_device, entity_id=entity_id, entity_type=entity_type)
            except Exception as e:
                logger.warning(f"[release_device] Reset openclaw config failed for binding {binding_id}: {e}")

        self._repo.release_binding(
            binding_id=binding_id,
            release_reason=(
                release_reason
                if not physical_release_error
                else f"{release_reason or ''} (physical release failed: {physical_release_error})"
            ),
            released_by=operator.staff,
        )

        record = self._repo.get_by_id(binding_id)
        if record is None:
            return None
        return record

    def list_devices(
        self,
        *,
        entity_id: str | None,
        entity_type: str | None,
        env: str | None,
        status: str | None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[DeviceBindingRecord]]:
        """List device bindings with filters.

        ``env`` 默认走 ``get_current_env()`` —— repo 层要求 env 必传
        以避免跨环境串数据。caller 显式传 env 时透传。
        """
        from agentclaw.community.utils import env_utils

        resolved_env = env if env is not None else env_utils.get_current_env()
        if env is None:
            logger.debug(
                "[list_bindings] env not provided, defaulted to current_env=%s",
                resolved_env,
            )

        return self._repo.list_bindings(
            entity_id=entity_id,
            entity_type=entity_type,
            env=resolved_env,
            status=status,
            page=page,
            page_size=page_size,
        )

    def get_provider_inventory(
        self,
        *,
        entity_id: str | None,
        entity_type: str | None,
        env: str | None,
        status: str | None,
        page_size: int = 500,
        max_pages: int = 20,
    ) -> dict:
        """Aggregate device bindings by provider for rollout observation.

        This intentionally uses the existing paginated repository API instead
        of adding a DB-specific aggregation method. The result reports whether
        it was truncated so callers know when they are looking at a bounded
        sample rather than the whole table.
        """
        from agentclaw.community.utils import env_utils

        page_size = max(1, min(int(page_size), 1000))
        max_pages = max(1, min(int(max_pages), 100))

        resolved_env = env if env is not None else env_utils.get_current_env()
        if env is None:
            logger.debug(
                "[get_provider_inventory] env not provided, defaulted to current_env=%s",
                resolved_env,
            )

        by_provider: dict[str, dict] = {}
        total = 0
        scanned = 0

        for page in range(1, max_pages + 1):
            total, items = self._repo.list_bindings(
                entity_id=entity_id,
                entity_type=entity_type,
                env=resolved_env,
                status=status,
                page=page,
                page_size=page_size,
            )
            if not items:
                break

            for item in items:
                provider = item.device_provider or "unknown"
                provider_bucket = by_provider.setdefault(
                    provider,
                    {"total": 0, "by_status": {}, "by_env": {}},
                )
                provider_bucket["total"] += 1
                provider_bucket["by_status"][item.status] = (
                    provider_bucket["by_status"].get(item.status, 0) + 1
                )
                provider_bucket["by_env"][item.env] = (
                    provider_bucket["by_env"].get(item.env, 0) + 1
                )
                scanned += 1

            if scanned >= total:
                break

        return {
            "filters": {
                "entity_id": entity_id,
                "entity_type": entity_type,
                "env": resolved_env,
                "status": status,
            },
            "total": total,
            "scanned": scanned,
            "truncated": scanned < total,
            "page_size": page_size,
            "max_pages": max_pages,
            "by_provider": by_provider,
        }

    def get_device(self, *, binding_id: int) -> DeviceBindingRecord:
        """Get device by binding ID."""
        item = self._repo.get_by_id(binding_id)
        if item is None:
            raise DeviceNotFoundError(f"binding {binding_id} not found")
        return item

    def get_device_by_device_id(self, *, device_id: str) -> DeviceBindingRecord:
        """Get device by device ID."""
        item = self._repo.get_by_device_id(device_id)
        if item is None:
            raise DeviceNotFoundError(f"device {device_id} not found")
        return item

    def get_device_connection(
        self,
        *,
        binding_id: int,
        operator: OperatorContext,
        port: int | None = None,
        ttl: int | None = None,
        device_uuid: str | None = None,
    ) -> DeviceConnectionInfo:
        """Get device connection information.

        Validates the binding exists and is not FAILED, checks permission,
        then delegates to _compose_device_conn_info for provider-specific logic.

        ``device_uuid`` (optional) targets a specific instance in the multi-instance
        BaaS provider (see ``BaasDeviceService.get_device_connection``); local /
        non-BaaS providers have a single device and ignore it.
        """
        logger.info(f"[get_device_connection] called with binding_id={binding_id}, port={port}, ttl={ttl}")

        record = self._repo.get_by_id(binding_id)
        if record is None:
            raise DeviceNotFoundError(f"binding {binding_id} not found")

        if self._effective_binding_status(record) == DeviceBindingStatus.FAILED.value:
            raise InvalidDeviceStatusError("cannot get connection for failed device")

        # Permission check: 检查是否是公开 Bot 或协作者
        if record.entity_id != operator.staff_id:
            is_public = False
            is_collaborator = False
            bot = None
            try:
                bot = self._bot_query.get_by_binding_id(binding_id)
                is_public = bot is not None and bot.get("public") == "1"
            except Exception as e:
                logger.warning("[get_device_connection] Failed to check bot visibility: %s", e)

            # 检查协作者权限
            if not is_public and not is_collaborator:
                if bot is None:
                    raise InvalidDeviceStatusError("Bot 信息不存在，无法校验权限")
                bot_id = bot.get("bot_id")
                owner_id = bot.get("owner_id")
                logger.info(
                    "[get_device_connection] Check collaborator: bot_id=%s, owner_id=%s, user_id=%s",
                    bot_id, owner_id, operator.staff_id
                )
                if not bot_id or not owner_id:
                    raise InvalidDeviceStatusError("Bot 信息不完整，无法校验权限")
                collaborator_service = self._get_collaborator_service()
                if not collaborator_service:
                    raise InvalidDeviceStatusError("协作者服务不可用，无法校验权限")
                from agentclaw.community.core.bot_collaborator.models import PermissionLevel
                result = collaborator_service.check_collaborator_permission(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    user_id=operator.staff_id,
                    required_level=PermissionLevel.MEMBER,
                )
                is_collaborator = result.get("has_permission", False)
                if not is_collaborator:
                    raise InvalidDeviceStatusError("非公开Bot只能获取本人或协作者设备的连接信息")

        allocated_device = AllocatedDevice(
            device_id=record.device_id,
            device_provider=record.device_provider,
            device_props=record.device_props,
        )
        resolved_port = port or 20003

        return self._compose_device_conn_info(device=allocated_device, port=resolved_port, ttl=ttl)

    def report_device_alive(
        self, *, device_id: str, token: str, skip_token_check: bool = False
    ) -> DeviceBindingRecord:
        """Report device as alive.

        Supports periodic device reporting. First report changes status from PENDING to ACTIVE,
        subsequent reports update last_alive_at timestamp.

        Args:
            device_id: The device identifier.
            token: The callback token for authentication.
            skip_token_check: If True, bypasses token validation. Used for in-process calls
                from BaaS publish callback where the agentbox binding may not have a
                callback_token configured.
        """
        import time as _time
        _t0 = _time.time()
        logger.info(f"[report_device_alive] device_id={device_id} entry")

        record = self._repo.get_by_device_id(device_id)
        if record is None:
            raise DeviceNotFoundError(f"device {device_id} not found")

        # Validate token (skip if skip_token_check is True)
        if not skip_token_check:
            stored_token = record.device_props.get("callback_token")
            if stored_token != token:
                raise InvalidDeviceStatusError("invalid token for device")

        if record.status == DeviceBindingStatus.RELEASED.value:
            raise InvalidDeviceStatusError("cannot report alive for released device")

        prev_status = record.status
        # Update status
        new_status = (
            DeviceBindingStatus.ACTIVE.value
            if record.status == DeviceBindingStatus.PENDING.value
            else record.status
        )
        logger.info(
            f"[report_device_alive] device_id={device_id} status_transition: "
            f"binding_id={record.id}, prev={prev_status}, new={new_status}, "
            f"is_first_active={prev_status == DeviceBindingStatus.PENDING.value}"
        )

        self._repo.update_status_and_alive_at(binding_id=record.id, status=new_status)

        # If status changed from PENDING to ACTIVE, sync bot status and trigger callbacks
        if record.status == DeviceBindingStatus.PENDING.value:
            logger.info(
                f"[report_device_alive] device_id={device_id} PENDING→ACTIVE callbacks start: "
                f"binding_id={record.id}"
            )
            _t_cb = _time.time()
            self._update_bot_status_on_device_active(binding_id=record.id)
            logger.info(
                f"[report_device_alive] device_id={device_id} update_bot_status done: "
                f"cost_ms={(_time.time() - _t_cb) * 1000:.0f}"
            )
            _t_sync = _time.time()
            self._sync_bot_config_when_device_active(device_id=device_id)
            logger.info(
                f"[report_device_alive] device_id={device_id} sync_bot_config done: "
                f"cost_ms={(_time.time() - _t_sync) * 1000:.0f}"
            )
            # MCP 同步：由注入的 McpSyncProtocol 在后台线程中执行，
            # 不阻塞 alive 回调。失败仅记录日志，不影响主流程。
            self._sync_mcps_when_device_active(record)
            # data-init 触发已移至 report_device_status(status=SUCCEEDED) 回调
            logger.info(
                f"[report_device_alive] device_id={device_id} all_callbacks done: "
                f"total_ms={(_time.time() - _t0) * 1000:.0f}"
            )

            try:
                from agentclaw.community.core.events.bus import get_event_bus
                from agentclaw.community.core.events.types import DeviceActivatedEvent

                event = DeviceActivatedEvent(
                    device_id=device_id,
                    binding_id=record.id,
                    entity_id=record.entity_id,
                    entity_type=record.entity_type,
                    device_provider=record.device_provider,
                    sandbox_id=(record.device_props or {}).get("sandbox_id"),
                )
                get_event_bus().publish(event)
                logger.info(
                    f"[report_device_alive] device_id={device_id} DeviceActivatedEvent published: "
                    f"binding_id={record.id}, sandbox_id={event.sandbox_id}"
                )
            except Exception as e:
                logger.warning(
                    f"[report_device_alive] device_id={device_id} event publish failed: {e}",
                    exc_info=True,
                )

        updated_record = self._repo.get_by_id(record.id)
        if updated_record is None:
            raise DeviceNotFoundError(f"binding {record.id} not found after update")

        return updated_record

    def report_device_status(
        self, *, device_id: str, status: str, message: str | None, token: str
    ) -> DeviceBindingRecord:
        """Report device startup status.

        Saves startup status to ac_bots.ext field.
        If status is FAILED, updates both ac_bots and ac_entity_device_binding status to FAILED.
        """
        logger.info(f"[report_device_status] Received status report: device_id={device_id}, status={status}")

        record = self._repo.get_by_device_id(device_id)
        if record is None:
            raise DeviceNotFoundError(f"device {device_id} not found")

        # Validate token
        stored_token = record.device_props.get("callback_token")
        if stored_token != token:
            raise InvalidDeviceStatusError("invalid token for device")

        if record.status == DeviceBindingStatus.RELEASED.value:
            raise InvalidDeviceStatusError("cannot report status for released device")

        # Update ac_bots.ext field
        self._update_bot_start_status(binding_id=record.id, status=status, message=message)

        # If FAILED, update both ac_bots and ac_entity_device_binding status
        if status == "FAILED":
            logger.info(f"[report_device_status] Status is FAILED, updating bot and device status: device_id={device_id}")
            self._update_bot_status_on_device_failed(binding_id=record.id)
            self._repo.update_status(binding_id=record.id, status=DeviceBindingStatus.FAILED.value)

        elif status == "SUCCEEDED":
            # 设备自报启动成功，前置条件 bot_status=ACTIVE + start_status=SUCCEEDED 均已满足
            logger.info(f"report_device_status device_id={device_id} status=SUCCEEDED triggering_data_init")
            self._trigger_data_init_on_device_ready(device_id=device_id, record=record)

        updated_record = self._repo.get_by_id(record.id)
        if updated_record is None:
            raise DeviceNotFoundError(f"binding {record.id} not found after update")

        return updated_record

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
        """List connectable devices for personal bots.

        查询逻辑：
        1. 先根据 entity_id 查询所有激活的个人 bot（bot_type=personal）
        2. 再根据个人 bot 的 binding_id 查询设备绑定状态
        3. 只返回个人 bot 关联的设备，避免返回服务型 bot 的设备
        """
        if not entity_id:
            return 0, []

        # Step 1: 查询激活的个人 bot (bot_type=personal)
        bots = self._bot_query.list_active_bots_by_entity(
            entity_id=entity_id,
            entity_type=entity_type,
            bot_type="personal",
        )
        if not bots:
            return 0, []

        # Step 2: 提取 binding_ids
        binding_ids = [
            bot.get("binding_id")
            for bot in bots
            if bot.get("binding_id")
        ]
        if not binding_ids:
            return 0, []

        # Step 3: 查询设备绑定
        all_bindings = self._repo.get_by_ids(binding_ids)

        # Step 4: 过滤 ACTIVE 状态。teclaw 绑定的本地列是未维护的标记，按 baas
        # 实时状态判定（_effective_binding_status）；其余绑定用本地列，非 teclaw
        # 零额外开销（resolver 先看 device_provider 即返回）。
        active_bindings = [
            b for b in all_bindings
            if self._effective_binding_status(b) == DeviceBindingStatus.ACTIVE.value
        ]
        if env:
            active_bindings = [
                b for b in active_bindings
                if b.env == env
            ]

        # Step 5: 分页
        total = len(active_bindings)
        start = (page - 1) * page_size
        end = page * page_size
        paginated_bindings = active_bindings[start:end]

        # Step 6: 组装结果
        results: list[DeviceBindingInfo] = []
        for item in paginated_bindings:
            connection = None
            if with_connection:
                try:
                    connection = self.get_device_connection(
                        binding_id=item.id,
                        operator=operator,
                        port=port,
                    )
                except Exception:
                    connection = None
            results.append(DeviceBindingInfo(record=item, connection=connection))
        return total, results

    def exec_shell(self, device_id: str, shell_cmd: str) -> str:
        """Execute shell command on device."""
        logger.info(f"[exec_shell] device_id={device_id}, cmd={shell_cmd}")

        current = self._repo.get_by_device_id(device_id=device_id)
        if current is None:
            raise DeviceNotFoundError(f"device {device_id} not found")

        if current.status not in [
            DeviceBindingStatus.ACTIVE.value,
            DeviceBindingStatus.PENDING.value,
        ]:
            raise InvalidDeviceStatusError("only ACTIVE/PENDING devices can exec_shell")

        allocated_device = AllocatedDevice(
            device_id=current.device_id,
            device_provider=current.device_provider,
            device_props=current.device_props,
        )
        return self._exec_shell(device=allocated_device, shell_cmd=shell_cmd)

    def get_device_connection_by_bot(
        self,
        *,
        bot_id: str,
        operator: OperatorContext,
        port: int | None = None,
        ttl: int | None = None,
        device_uuid: str | None = None,
    ) -> DeviceConnectionInfo:
        """Get connection info by bot_id (hook — router overrides).

        Resolution from bot_id → runtime binding lives in the router; a plain
        provider does not implement it.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support bot_id connection entry"
        )

    def get_instances(
        self,
        *,
        binding_id: int,
        health_check: bool = False,
    ) -> dict:
        """List device instances by binding_id (hook — router overrides).

        Multi-instance is a baas/router concern; a plain provider does not
        implement it. ``DeviceServiceRouter`` overrides this.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support multi-instance listing"
        )

    def get_instances_by_bot(
        self,
        *,
        bot_id: str,
        health_check: bool = False,
    ) -> dict:
        """List device instances by bot_id (hook — router overrides).

        Resolution from bot_id → runtime binding lives in the router.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support multi-instance listing"
        )

    def restart_device(
        self,
        *,
        binding_id: int,
        device_uuid: str,
        operator: OperatorContext,
    ) -> dict:
        """Restart a specific device instance (hook — router overrides).

        Multi-instance restart is a baas/router concern; a plain provider
        does not implement it. ``DeviceServiceRouter`` overrides this.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support multi-instance restart"
        )

    def exec_shell_new(self, device_id: str, shell_cmd: str):
        """Execute shell command on device and return CommandResult.

        Args:
            device_id: 设备 ID
            shell_cmd: 要执行的 shell 命令

        Returns:
            CommandResult 对象，包含 stdout、stderr、exit_code 等字段

        Raises:
            DeviceNotFoundError: 设备未找到
            InvalidDeviceStatusError: 设备状态不允许执行命令
        """
        logger.info(f"[exec_shell_new] device_id={device_id}, cmd={shell_cmd}")

        current = self._repo.get_by_device_id(device_id=device_id)
        if current is None:
            raise DeviceNotFoundError(f"device {device_id} not found")

        if current.status not in [
            DeviceBindingStatus.ACTIVE.value,
            DeviceBindingStatus.PENDING.value,
        ]:
            raise InvalidDeviceStatusError("only ACTIVE/PENDING devices can exec_shell_new")

        allocated_device = AllocatedDevice(
            device_id=current.device_id,
            device_provider=current.device_provider,
            device_props=current.device_props,
        )
        return self._exec_shell_new(device=allocated_device, shell_cmd=shell_cmd)

    def batch_set_env(self, *, binding_ids: list[int], env: str) -> tuple[int, list[int]]:
        """Batch update environment for multiple bindings."""
        count = self._repo.batch_update_env(binding_ids=binding_ids, env=env)
        updated_records = self._repo.get_by_ids(binding_ids)
        updated_ids = [
            r.id for r in updated_records
            if r.env == env
        ]
        return count, updated_ids

    # =========================================================================
    # Bot status callback methods (aligned with old code)
    # =========================================================================

    def _update_bot_start_status(self, *, binding_id: int, status: str, message: str | None) -> None:
        """Update bot ext field with startup status."""
        logger.info(f"[_update_bot_start_status] binding_id={binding_id}, status={status}")
        try:
            self._repo.update_bot_start_status(binding_id=binding_id, status=status, message=message)
        except Exception as e:
            logger.warning(f"[_update_bot_start_status] Failed: {e}", exc_info=True)

    def _update_bot_status_on_device_active(self, *, binding_id: int) -> None:
        """Update associated bot status when device becomes ACTIVE."""
        logger.info(f"[_update_bot_status_on_device_active] binding_id={binding_id}")
        try:
            self._repo.update_bot_status_on_device_active(binding_id=binding_id)
        except Exception as e:
            logger.warning(f"[_update_bot_status_on_device_active] Failed: {e}", exc_info=True)

    def _update_bot_status_on_device_failed(self, *, binding_id: int) -> None:
        """Update associated bot status when device fails."""
        logger.info(f"[_update_bot_status_on_device_failed] binding_id={binding_id}")
        try:
            self._repo.update_bot_status_on_device_failed(binding_id=binding_id)
        except Exception as e:
            logger.warning(f"[_update_bot_status_on_device_failed] Failed: {e}", exc_info=True)

    def _mark_service_start_failed(self, *, binding_id: int, error: str) -> None:
        """Mark binding + bot as FAILED and persist the error when _start_service fails.

        Mirrors the FAILED branch of report_device_status so the async service-start
        path leaves consistent state (otherwise the bot would stay PENDING forever
        with no error surfaced).
        """
        logger.error(f"[_mark_service_start_failed] binding_id={binding_id}, error={error}")
        self._update_bot_start_status(binding_id=binding_id, status="FAILED", message=error)
        self._update_bot_status_on_device_failed(binding_id=binding_id)
        try:
            self._repo.update_status(binding_id=binding_id, status=DeviceBindingStatus.FAILED.value)
        except Exception as e:
            logger.warning(f"[_mark_service_start_failed] update binding status failed: {e}", exc_info=True)

    def _sync_bot_config_when_device_active(self, *, device_id: str) -> None:
        """Sync bot config to device when it becomes ACTIVE.

        对齐老代码 services/device/service.py::_sync_bot_config_when_device_active。
        通过 Protocol 接口访问 Bot 系统，不再直接 import 旧代码。
        """
        logger.info(f"[_sync_bot_config_when_device_active] device_id={device_id}")
        try:
            record = self._repo.get_by_device_id(device_id)
            if record is None:
                logger.info(f"[_sync_bot_config_when_device_active] Device not found: device_id={device_id}")
                return

            bot = self._bot_query.get_by_binding_id(record.id)
            if bot is None:
                logger.info(f"[_sync_bot_config_when_device_active] No bot found for binding_id={record.id}")
                return

            bot_id = bot.get("bot_id")
            public = bot.get("public") or ""
            ext = bot.get("ext") or {}
            permission_owner = ext.get("permission_owner") or ""
            owner_id = bot.get("owner_id") or ""

            # Task 2.1: 入参精简,binding 由 resolver 内部查,
            # nick_name 死参由 plugin 兜底。
            result = self._bot_sync.sync_bot_config_to_device(
                bot_id=bot_id,
                user_id=owner_id,
                public=public,
                permission_owner=permission_owner,
            )
            logger.info(f"[_sync_bot_config_when_device_active] Sync completed: device_id={device_id}, bot_id={bot_id}, success={result.get('success')}")
        except Exception as e:
            logger.warning(f"[_sync_bot_config_when_device_active] Failed: {e}", exc_info=True)

    def _sync_mcps_when_device_active(self, record) -> None:
        """设备变为 ACTIVE 后，后台触发 MCP 全量同步。

        顺序：先刷新白名单与许可证，再推送全部 MCP 配置。
        失败仅记录日志，不影响 alive 回调的成功响应。
        """

        def _run() -> None:
            import asyncio

            try:
                # 通过 binding_id 查 bot 记录取 bot_id 和 engine_type
                bot = self._bot_query.get_by_binding_id(record.id)
                if not bot:
                    logger.error(
                        "[_sync_mcps_when_device_active] No bot found for binding_id=%s",
                        record.id,
                    )
                    return
                bot_id = bot.get("bot_id", "")
                engine_type = bot.get("active_engine")

                async def _do_sync() -> tuple[dict, dict | None]:
                    # 1. 先声明白名单（scope），失败则直接返回，不必继续推送详细配置
                    scope_result = await self._mcp_sync.refresh_mcp_scope(
                        user_id=record.entity_id,
                        entity_id=record.entity_id,
                        bot_id=bot_id,
                        entity_type=record.entity_type,
                        engine_type=engine_type,
                    )
                    if not scope_result.get("success"):
                        return scope_result, None

                    # 2. 再推送详细配置
                    detail_result = await self._mcp_sync.sync_mcp_details(
                        user_id=record.entity_id,
                        entity_id=record.entity_id,
                        bot_id=bot_id,
                        entity_type=record.entity_type,
                        engine_type=engine_type,
                    )
                    return scope_result, detail_result

                scope_result, detail_result = asyncio.run(_do_sync())
                if not scope_result.get("success"):
                    logger.error(
                        "[report_device_alive] MCP scope 声明失败: device=%s, engine_type=%s, error=%s",
                        record.device_id,
                        engine_type,
                        scope_result.get("error"),
                    )
                elif not detail_result.get("success"):
                    logger.error(
                        "[report_device_alive] MCP 详细配置推送失败: device=%s, engine_type=%s, error=%s",
                        record.device_id,
                        engine_type,
                        detail_result.get("error"),
                    )
                else:
                    logger.info(
                        "[report_device_alive] MCP 同步成功: device=%s, engine_type=%s",
                        record.device_id, engine_type,
                    )
            except Exception as sync_error:
                logger.error(
                    "[report_device_alive] MCP 同步失败: device=%s, error=%s",
                    record.device_id, sync_error,
                )

        threading.Thread(target=_run, daemon=True).start()

    def _trigger_data_init_on_device_ready(self, *, device_id: str, record) -> None:
        """当设备自报 SUCCEEDED 时触发 data-init。

        由 report_device_status(status=SUCCEEDED) 调用。
        前置条件已满足：bot_status=ACTIVE（alive 回调已设）+
        start_status=SUCCEEDED（当前回调刚写入）。
        无需轮询 engine health 或等待 warmup。
        """
        try:
            bot = self._bot_query.get_by_binding_id(record.id)
            if bot is None:
                logger.info(
                    f"bot_id=unknown data_init_trigger skipped bot_not_found binding_id={record.id}"
                )
                return

            bot_id = bot.get("bot_id")
            owner_id = bot.get("owner_id") or ""
            entity_id = bot.get("entity_id") or getattr(record, "entity_id", "") or ""
            entity_type = bot.get("entity_type") or getattr(record, "entity_type", "staff") or "staff"

            import json as _json
            ext = bot.get("ext") or {}
            if isinstance(ext, str):
                try:
                    ext = _json.loads(ext)
                except _json.JSONDecodeError:
                    ext = {}
            data_init_status = ext.get("data_init_status")

            logger.info(
                f"bot_id={bot_id} data_init_trigger device_ready "
                f"data_init_status={data_init_status} "
                f"owner_id={owner_id} entity_id={entity_id} entity_type={entity_type}"
            )

            # 仅在 data_init_status 为 pending_init / failed 时触发
            # null（存量 Bot，未启用 data-init）/ completed / in_progress 跳过
            if data_init_status not in ("pending_init", "failed"):
                logger.info(
                    f"bot_id={bot_id} data_init_trigger skipped "
                    f"data_init_status={data_init_status}"
                )
                return

            # 兜底校验：Bot 状态必须是 ACTIVE，防止 alive 回调晚于 SUCCEEDED 到达
            bot_status = bot.get("status", "UNKNOWN")
            if bot_status != "ACTIVE":
                logger.warning(
                    f"bot_id={bot_id} data_init_trigger skipped "
                    f"bot_status={bot_status} (expected ACTIVE, alive callback may be delayed)"
                )
                return

            import asyncio
            import threading
            import time as _time

            # Cycle-breaker: ``DataInitService.__init__`` takes
            # DeviceService; resolving DataInitService eagerly would close
            # the construction graph. The Callable[[], T] factory injected
            # via __init__ defers the lookup until this callback fires.
            if self._data_init_service_provider is None:
                logger.warning(
                    f"bot_id={bot_id} data_init_trigger skipped: "
                    "no data_init_service_provider configured"
                )
                return
            data_init_service = self._data_init_service_provider()

            # report_device_status 是同步调用链，当前线程没有可挂靠的事件循环，
            # 使用独立线程 + asyncio.run() 在线程内创建事件循环执行 trigger_init。
            def _run_init():
                _t_start = _time.time()
                logger.info(f"bot_id={bot_id} data_init_trigger thread_started")
                try:
                    asyncio.run(
                        data_init_service.trigger_init(
                            bot_id=bot_id,
                            owner_id=owner_id,
                            entity_id=entity_id,
                            entity_type=entity_type,
                        )
                    )
                    logger.info(
                        f"bot_id={bot_id} data_init_trigger thread_finished "
                        f"total_ms={(_time.time() - _t_start) * 1000:.0f}"
                    )
                except Exception as run_exc:
                    logger.error(
                        f"bot_id={bot_id} data_init_trigger thread_failed exc={run_exc} "
                        f"total_ms={(_time.time() - _t_start) * 1000:.0f}",
                        exc_info=True,
                    )

            thread = threading.Thread(target=_run_init, daemon=True, name=f"data-init-{bot_id}")
            thread.start()

            logger.info(f"bot_id={bot_id} data_init_trigger dispatched source=status_succeeded thread={thread.name}")

        except Exception as e:
            logger.warning(f"bot_id=unknown data_init_trigger failed device_id={device_id} exc={e}", exc_info=True)

    # =========================================================================
    # get_device_connection_v2 — 代理/直连组装（公共方法，多个上层模块共用）
    # =========================================================================

    def get_device_connection_v2(
        self,
        user_id: str,
        nick_name: str,
        binding_id: int,
        operator_tenant_id: str = "default",
    ) -> dict:
        """获取设备连接信息（支持新旧架构），自动判断代理/直连模式。

        新架构（ARCA）: 使用 sandbox_id 通过代理访问
        旧架构（直连）: 使用 IP 直接访问

        此方法供上层模块（cron_relay、bot_service、skill_set_service、mcp_service、
        expert_chat 等）统一调用，避免每个调用方重复实现代理/直连判断逻辑。

        Args:
            user_id: 用户 ID（用作 operator.staff_id）
            nick_name: 用户花名（用作 operator.operator_name）
            binding_id: 设备绑定 ID
            operator_tenant_id: 租户 ID（默认 "default"）

        Returns:
            dict 包含:
            - url: 完整的请求 URL（已包含 path）
            - headers: 请求头（包含认证 token 等）
            - use_proxy: 是否使用代理（新架构为 True）
            - sandbox_id: 新设备的 sandbox_id（如果有）
            - target: 原始 target（兼容旧代码）
            - token: 原始 token（兼容旧代码）
            - engine_type: 引擎类型
        """
        # 1. 获取设备详情，检查 sandbox_id 和 device_provider（单源事实）
        try:
            device_result = self.get_device(binding_id=binding_id)
            device_props = getattr(device_result, 'device_props', {}) or {}
            sandbox_id = device_props.get('sandbox_id')
            device_provider = device_result.device_provider
        except Exception as e:
            logger.warning(f"[get_device_connection_v2] Failed to get device props: {e}")
            sandbox_id = None
            device_provider = None

        # 2. 构建 operator 并获取连接信息
        operator = OperatorContext(
            staff_id=user_id,
            staff=user_id,
            nick_name=nick_name,
            operator_name=nick_name,
            tenant_id=operator_tenant_id,
        )

        try:
            result = self.get_device_connection(
                binding_id=binding_id,
                operator=operator,
            )

            target = result.target
            token = result.token
            engine_type = result.engine_type
            connection_type = result.type  # 'local', 'remote', 'baas', etc.

            # 3a. BaaS invoke-http 代理（desktop / local 均走此路径）
            if connection_type in ('desktop', 'local'):
                invoke_base = (
                    f"{result.baas_base_url}/api/v1/bots/{result.tenant}/{result.bot_uuid}"
                    f"/invoke-http/{result.engine_port}"
                )
                logger.info(
                    f"[get_device_connection_v2] Using BaaS invoke-http for binding {binding_id}, "
                    f"bot_uuid={result.bot_uuid}, engine_port={result.engine_port}"
                )
                return {
                    "url": invoke_base,
                    "headers": {"x-proxypass-token": token} if token else {},
                    "use_proxy": True,
                    "sandbox_id": None,
                    "target": target,
                    "token": token,
                    "engine_type": engine_type,
                    "type": connection_type,
                    "bot_uuid": result.bot_uuid,
                    "baas_base_url": result.baas_base_url,
                    "tenant": result.tenant,
                    "engine_port": result.engine_port,
                    # 透出 binding_id + entity_id 给 transport 用 BaasService.get_http_info
                    # 拿到每个请求的真实 engine URL,对齐 LocalDeviceFileSystem 的 per-request 姿势。
                    # singlebox 模式下 baas /http-info 返直连 url (跳过 invoke-http 代理的
                    # ARCA stub bug);线上 prod 走 ARCA 沙箱时 baas /http-info 返代理 url,
                    # 两者透明走同一条路径。
                    "binding_id": binding_id,
                    "device_affinity": user_id,
                }

            # 3b. 判断是否需要使用 ARCA 代理（服务 bot）
            # 单源:binding.device_provider 是全仓权威事实源
            # （resolver 治理后,device_provider == "arca" 即等价于
            #  原 bool(sandbox_id) or connection_type=='baas' or target.startswith('ARCA_')）
            is_arca_device = device_provider == "arca"

            if is_arca_device:
                # 新架构：使用设备运行时代理（vendor 细节在 SandboxRuntimeClient）
                proxy_base = self._sandbox_client.proxy_base_url()

                # 对于 BaaS 设备，target 已经是 ARCA_xxx@alt:port 格式，直接使用
                # 对于普通 ARCA 设备，需要通过 client 构造 target
                if connection_type == 'baas' or target.startswith('ARCA_'):
                    target_path = target
                else:
                    target_path = self._sandbox_client.proxy_target(sandbox_id)

                proxy_url = f"{proxy_base}/proxypass/{target_path}"

                logger.info(
                    f"[get_device_connection_v2] Using ARCA proxy for binding {binding_id}, "
                    f"sandbox_id={sandbox_id}, connection_type={connection_type}"
                )

                return {
                    "url": proxy_url,
                    "headers": {"x-proxypass-token": token} if token else {},
                    "use_proxy": True,
                    "sandbox_id": sandbox_id,
                    "target": target,
                    "token": token,
                    "engine_type": engine_type,
                    "type": connection_type,
                }
            elif not result.available:
                # Device offline — propagate unavailable status
                logger.info(
                    f"[get_device_connection_v2] Device unavailable for binding {binding_id}"
                )
                return {
                    "url": "",
                    "headers": {},
                    "use_proxy": False,
                    "sandbox_id": None,
                    "target": "",
                    "token": "",
                    "engine_type": engine_type,
                    "type": connection_type,
                    "available": False,
                    "message": result.message,
                }
            else:
                # 旧架构：直接访问
                logger.info(
                    f"[get_device_connection_v2] Using direct connection for binding {binding_id}, "
                    f"target={target}"
                )

                # plan-01: 优先用 DeviceConnectionInfo.url（BaaS http-info 提供）；
                # 缺失时 fallback `f"http://{target}"`（保旧 desktop / dev 测试兼容）。
                direct_url = result.url or f"http://{target}"

                return {
                    "url": direct_url,
                    "headers": {},
                    "use_proxy": False,
                    "sandbox_id": None,
                    "target": target,
                    "token": token,
                    "engine_type": engine_type,
                    "type": connection_type,
                }

        except Exception as e:
            logger.error(f"[get_device_connection_v2] Failed to get device connection for {binding_id}: {e}")
            raise DeviceServiceError(f"Failed to get device connection: {e}")
