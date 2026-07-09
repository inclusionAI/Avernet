"""BaasConnInfoBuilder — 复用 baas_service.get_ws_info + build_baas_conn_info_for_http。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.device_context import ConnInfoBuildError
from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient


logger = get_logger()

# 跟 BaasDeviceService._compose_device_conn_info 和 arca_device_service.py:838
# 的兜底语义对齐:bot 反查不到 / active_engine 缺失时,默认 openclaw。
# REL20260610 行为等价。
_DEFAULT_ENGINE_TYPE = "openclaw"


class BaasConnInfoBuilder:
    """provider=baas 的 conn_info 计算器。

    底层复用 ``baas_service.get_ws_info`` + ``build_baas_conn_info_for_http``,
    不重写。失败统一包成 :class:`ConnInfoBuildError`。
    """

    def __init__(
        self,
        baas_service,
        bot_repository: BotRepository,
        device_binding_repository: DeviceBindingRepository,
        sandbox_client: "SandboxRuntimeClient | None" = None,
    ):
        self._baas_service = baas_service
        self._bot_repo = bot_repository
        self._device_repo = device_binding_repository
        self._sandbox_client = sandbox_client

    def build(
        self,
        binding: DeviceBindingRecord,
        user_id: str,
        *,
        device_uuid: str | None = None,
    ) -> dict[str, Any]:
        try:
            ws_info = self._baas_service.get_ws_info(
                bind_id=binding.id,
                device_affinity=user_id,
                device_uuid=device_uuid,
            )
        except Exception as e:
            raise ConnInfoBuildError(
                f"BaasConnInfoBuilder: get_ws_info failed for binding={binding.id}: {e}"
            ) from e

        # 反查 bot 记录(为了拿 active_engine + bot_type)。
        #
        # 直接 ``BotRepository.get_by_binding_id`` 对 service bot 查不到:
        # service bot 在 ac_bots 表里只有一条 row 关联到 owner 个人的 ARCA
        # binding,publish 到 BaaS 出来的 binding_id 不在 ac_bots.binding_id
        # 列上。
        #
        # 兼容策略对齐 ``BaasDeviceService._resolve_bot_by_binding_id``(同仓
        # ``core/devices/services/baas_device_service.py``,L732):
        #   Step 1: ``ac_bots.binding_id`` 直接命中(桌面 bot 适用)
        #   Step 2: 反查 ``ac_entity_device_binding.device_props.bolt_id``
        #           (=bot_id),再 by ``(bolt_id, entity_id=owner)`` 查 ac_bots
        #
        # 兜底语义跟 ARCA 链路 ``arca_device_service.py:838`` 一致:查不到 →
        # ``_DEFAULT_ENGINE_TYPE``,不再 raise。REL20260610 行为等价。
        # 现场:trace 0be8ed2217816832272041880e9da7(协作者公开访问 service bot)
        # 跟进:docs/superpowers/baas-refactor-dirty-work.md §7
        bot = self._resolve_bot(binding)
        engine_type = (bot or {}).get("active_engine") or _DEFAULT_ENGINE_TYPE
        bot_type = (bot or {}).get("bot_type") or ""

        if not bot:
            logger.warning(
                "[BaasConnInfoBuilder] bot not resolvable for binding=%s "
                "(device_id=%s, device_provider=%s); falling back to "
                "engine_type=%r, bot_type=''. ac_bots 表对该 binding 没建 row,"
                "见 dirty work §7.",
                binding.id, binding.device_id, binding.device_provider,
                _DEFAULT_ENGINE_TYPE,
            )

        return build_baas_conn_info_for_http(
            bind_id=binding.id,
            ws_info=ws_info,
            engine_type=engine_type,
            bot_type=bot_type,
            user_id=user_id,
            device_uuid=device_uuid,
            sandbox_client=self._sandbox_client,
        )

    def _resolve_bot(self, binding: DeviceBindingRecord) -> dict[str, Any] | None:
        """两步反查策略,对齐 ``BaasDeviceService._resolve_bot_by_binding_id``。"""
        # Step 1: ac_bots.binding_id 直接命中(桌面 bot)
        bot = self._bot_repo.get_by_binding_id(binding.id)
        if bot is not None:
            return bot

        # Step 2: 服务 bot — 通过 binding.device_props.bolt_id 反查
        bolt_id = (binding.device_props or {}).get("bolt_id", "")
        if not bolt_id:
            return None

        return self._bot_repo.get_by_id_and_owner(bolt_id, binding.entity_id)
