"""BaasDeviceAccessor -- prod-mode DeviceAccessor for BAAS-bound bots.

Independent from ArcaDeviceAccessor (which handles legacy
agentclawproxy/proxypass) — see
docs/superpowers/backend-agentbox-arca-split-guide.md §1.

Only handles bots whose ac_entity_device_binding.device_provider == "baas".
Returns None for arca/local bindings.

业务 caller 走 :class:`DeviceContextResolver` +
:class:`BaasConnInfoBuilder` 拿 typed ``DeviceContext``,不再依赖 provider
分流。本 plugin 现仅在 resolver 内部 builder 复用,以及尚未删除的兼容层
(``DeviceFilesystemDispatcher.for_bot`` / ``ProdDeviceSyncPluginSupplier``)
死路径上被引用。
"""
from typing import Any, Callable

from injector import inject

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.log import get_logger
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor
from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient

logger = get_logger()


class BaasDeviceAccessor(DeviceAccessor):
    """DeviceAccessor that only serves BAAS device bindings.

    Does not implement ``Lifecycle``: BAAS-managed bot processes
    survive backend restarts independently, so the backend has no
    boot or shutdown work to do for them.

    Construction uses lazy ``Callable[[], BotService]`` to break injector
    cycles (the prod DI graph routes through this plugin via
    DeviceContextResolver → BaasConnInfoBuilder → BaasDeviceAccessor →
    BotService → SkillSetServiceFactory → DeviceFilesystemDispatcher).
    See ArcaDeviceAccessor docstring for the cycle path.
    """

    @inject
    def __init__(
        self,
        bot_service_provider: Callable[[], BotService],
        baas_service_provider: Callable[[], BaasService],
        path_factory: WorkspacePathFactory,
        sandbox_client: SandboxRuntimeClient,
    ) -> None:
        self._bot_service_provider = bot_service_provider
        self._baas_service_provider = baas_service_provider
        self._path_factory = path_factory
        self._sandbox_client = sandbox_client

    def get_connection_info(
        self, bot_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """Return BAAS conn_info, or None if this isn't a baas binding."""
        if not bot_id or not user_id:
            return None

        try:
            logger.info(
                "[BaasDeviceAccessor.get_connection_info] start: bot_id=%s user_id=%s",
                bot_id, user_id,
            )
            bot = self._bot_service_provider().get_bot(bot_id=bot_id, user_id=user_id)
            device_binding = bot.get("device_binding") or {}
            provider = device_binding.get("device_provider")
            binding_id = device_binding.get("id")
            bot_type = bot.get("bot_type", "")
            logger.info(
                "[BaasDeviceAccessor.get_connection_info] bot found: provider=%s binding_id=%s bot_type=%s",
                provider, binding_id, bot_type,
            )
            if provider != "baas":
                logger.info(
                    "[BaasDeviceAccessor.get_connection_info] skip: provider=%r is not baas", provider,
                )
                return None

            if not binding_id:
                logger.warning(
                    "[BaasDeviceAccessor.get_connection_info] skip: binding_id is empty for bot %s", bot_id,
                )
                return None

            return self._build_baas_conn_info(user_id, binding_id, bot_type=bot_type)
        except Exception as e:
            logger.warning(
                "[BaasDeviceAccessor.get_connection_info] Failed for bot %s: %s",
                bot_id, e, exc_info=True,
            )
            return None

    def get_engine_config_path(
        self,
        bot_id: str,
        owner_id: str,
        *,
        entity_id: str,
        engine_type: str,
        entity_type: str = "staff",
    ) -> str:
        # BaaS reads openclaw.json from the standard engine-dir layout
        # (path_factory in prod returns the device-view path).
        bot_dir = self._path_factory.get_bot_engine_dir(
            entity_id, bot_id, engine_type, entity_type
        )
        return f"{bot_dir}/openclaw.json"

    def _build_baas_conn_info(
        self, user_id: str, binding_id: int, *, bot_type: str = ""
    ) -> dict[str, Any] | None:
        from agentclaw.community.core.devices.services.baas_conn_info import build_baas_conn_info_for_http

        try:
            baas_service = self._baas_service_provider()
            ws_info = baas_service.get_ws_info(
                bind_id=binding_id,
                device_affinity=user_id,
            )
            conn_info = build_baas_conn_info_for_http(
                bind_id=binding_id,
                ws_info=ws_info,
                engine_type="openclaw",
                bot_type=bot_type,
                sandbox_client=self._sandbox_client,
            )
            logger.info(
                "[BaasDeviceAccessor._build_baas_conn_info] "
                "paas_device_id=%s, baas_base_url=%s, engine_port=%s",
                conn_info.get("paas_device_id"),
                conn_info.get("baas_base_url"),
                conn_info.get("engine_port"),
            )
            return conn_info
        except Exception as e:
            logger.warning(
                "[BaasDeviceAccessor._build_baas_conn_info] Failed binding_id=%s: %s",
                binding_id, e,
            )
            return None
