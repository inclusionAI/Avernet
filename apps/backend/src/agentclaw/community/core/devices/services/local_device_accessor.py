"""LocalDeviceAccessor -- local-mode DeviceAccessor implementation.

In local mode, there is no remote device; all operations happen on the local
machine. This class is the neutral ``DeviceAccessor`` contract only
(``get_connection_info`` / ``get_engine_config_path``); the singlebox process
Lifecycle (boot symlink restore, orphan reallocation, process shutdown) lives in
the ``plugins/local`` participant ``LocalDeviceLifecycle`` (B9 split — the
Lifecycle drives other ``plugins/local`` components and so cannot live in
``core``).
"""

from typing import Any

from injector import inject

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.log import get_logger
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor


logger = get_logger()


class LocalDeviceAccessor(DeviceAccessor):
    """DeviceAccessor for local mode -- always returns 'local' provider, no remote connection."""

    @inject
    def __init__(
        self,
        path_factory: WorkspacePathFactory,
        bot_repository: BotRepository,
        binding_repo: DeviceBindingRepository,
        baas_service: BaasService,
    ) -> None:
        # path_factory: resolves the standard per-bot engine directory.
        # bot_repository: maps (bot_id, owner_id) → binding_id.
        # binding_repo: read binding metadata used to resolve BaaS connection info.
        # baas_service: used by ``get_connection_info`` (plan-03 Part B)
        # to resolve a binding to a BaaS-issued HTTP endpoint for the
        # MCP sync upper layer (ProdDeviceMCPSyncPlugin._get_base_url).
        self._path_factory = path_factory
        self._bot_repository = bot_repository
        self._binding_repo = binding_repo
        self._baas_service = baas_service

    def get_connection_info(
        self, bot_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """plan-03 Part B: resolve (bot → binding → BaaS http_info) and
        return a dict shaped for ``ProdDeviceMCPSyncPlugin._get_base_url``
        consumption (which inspects ``url`` / ``headers`` / ``device_provider``).

        Returns ``None`` (caller-safe sentinel meaning "no syncable device")
        when any link of the chain is missing or BaaS is unreachable.

        Note: ``device_provider`` is set to ``"local"`` (not ``"baas"``)
        because that triggers ``ProdDeviceMCPSyncPlugin._get_base_url`` to
        use the literal ``conn_info["url"]`` rather than the invoke-http
        tunnel shape — our plan-01 ``get_http_info`` already gives us the
        direct container URL.
        """
        try:
            bot = self._bot_repository.get_by_id_and_owner(bot_id, user_id)
        except Exception as e:
            logger.warning(
                "[LocalDeviceAccessor.get_connection_info] bot_repo failed: %s", e
            )
            return None
        if not bot:
            return None
        binding_id = (
            bot.get("binding_id") if isinstance(bot, dict)
            else getattr(bot, "binding_id", None)
        )
        if not binding_id:
            return None
        try:
            binding = self._binding_repo.get_by_id(binding_id)
        except Exception as e:
            logger.warning(
                "[LocalDeviceAccessor.get_connection_info] binding_repo failed: %s", e
            )
            return None
        if binding is None:
            return None
        props = binding.device_props or {}
        if "adapter_port" not in props:
            return None
        try:
            info = self._baas_service.get_http_info(
                bind_id=binding.id,
                port=props["adapter_port"],
                path="/api/mcp",  # MCPSync caller signature; for audit only
                device_affinity=binding.entity_id,
                tenant=props.get("tenant") or None,
            )
        except Exception as e:
            logger.warning(
                "[LocalDeviceAccessor.get_connection_info] get_http_info failed "
                "binding=%s: %s", binding.id, e,
            )
            return None
        return {
            "url": info.http_url,
            "token": info.token,
            "device_provider": "local",
            "headers": {"openclawToken": info.token},
            "target": "",
            "use_proxy": False,
        }

    def get_engine_config_path(
        self,
        bot_id: str,
        owner_id: str,
        *,
        entity_id: str,
        engine_type: str,
        entity_type: str = "staff",
    ) -> str:
        bot_dir = self._path_factory.get_bot_engine_dir(
            entity_id, bot_id, engine_type, entity_type
        )
        return f"{bot_dir}/openclaw.json"
