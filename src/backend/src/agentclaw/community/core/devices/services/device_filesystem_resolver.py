"""``DefaultDeviceFileSystemResolver`` — builds the per-provider device filesystems.

Implements the core
:class:`~agentclaw.community.core.devices.services.device_filesystem_dispatcher.DeviceFileSystemResolver`
seam: the core ``DeviceFilesystemDispatcher`` holds an instance of this and calls
``resolve(ctx, path_mapper)``. All device-filesystem construction (baas / arca /
teclaw / local) + the BaaS-binding lookup the local branch needs live here. Every
impl is a neutral ``core`` filesystem — the ``arca`` branch reaches ARCA only
through the injected ``SandboxRuntimeClient`` seam — so this resolver carries no
vendor coupling and is bound once, neutrally, in the base ``DevicesModule`` (B9).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from injector import inject

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.devices.services.device_context import UnknownProviderError
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.log import get_logger
from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem
from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context import DeviceContext

logger = get_logger()


class DefaultDeviceFileSystemResolver:
    """Neutral ``(DeviceContext, path_mapper) -> DeviceFileSystem`` resolver."""

    @inject
    def __init__(
        self,
        baas_service: BaasService,
        bot_repo: BotRepository,
        binding_repo: DeviceBindingRepository,
        sandbox_client: SandboxRuntimeClient,
    ) -> None:
        self._baas_service = baas_service
        self._bot_repo = bot_repo
        self._binding_repo = binding_repo
        self._sandbox_client = sandbox_client

    def __call__(
        self, ctx: "DeviceContext", path_mapper: Callable[[str], str]
    ) -> "DeviceFileSystem":
        provider = ctx.provider
        conn_info = ctx.conn_info

        if provider == "baas":
            from agentclaw.community.core.devices.services.baas_device_filesystem import (
                BaasDeviceFileSystem,
                DesktopBaasDeviceFileSystem,
            )
            from agentclaw.community.core.devices.services.baas_invoke_transport import (
                BaasInvokeTransport,
                DesktopBaasInvokeTransport,
            )

            bot_type = ctx.bot_type
            engine_port = conn_info["engine_port"]
            tenant = conn_info.get("tenant", "default")

            if bot_type == "desktop":
                desktop_transport = DesktopBaasInvokeTransport(
                    baas_base_url=conn_info["baas_base_url"],
                    tenant=tenant,
                    bot_uuid=conn_info["paas_device_id"],
                    engine_port=engine_port,
                    headers=conn_info.get("headers", {}),
                )
                logger.info(
                    "[DEVICE-PLUGIN-DEBUG] → DesktopBaasDeviceFileSystem(paas_device_id=%s)",
                    conn_info.get("paas_device_id"),
                )
                return DesktopBaasDeviceFileSystem(
                    transport=desktop_transport, conn_info=conn_info,
                    path_mapper=path_mapper,
                )

            cloud_transport = BaasInvokeTransport(
                bind_id=conn_info["bind_id"],
                engine_port=engine_port,
                tenant=tenant,
                baas_service=self._baas_service,
                device_uuid=conn_info.get("device_uuid"),
            )
            logger.info(
                "[DEVICE-PLUGIN-DEBUG] → BaasDeviceFileSystem(paas_device_id=%s, bot_type=%s)",
                conn_info.get("paas_device_id"), bot_type,
            )
            return BaasDeviceFileSystem(
                transport=cloud_transport, conn_info=conn_info,
                path_mapper=path_mapper,
            )

        if provider == "arca":
            # ARCA is corp-only. The base resolver (community / singlebox / test)
            # never routes ``arca`` and raises via the hook; the corp resolver
            # (``CorpDeviceFileSystemResolver``) overrides it to build the ARCA
            # filesystem, which the community build does not ship (B11 T3.4b).
            return self._resolve_arca(conn_info, path_mapper)

        if provider == "teclaw":
            from agentclaw.community.core.devices.services.teclaw_device_filesystem import (
                TeclawDeviceFileSystem,
            )

            logger.info(
                "[DEVICE-PLUGIN-DEBUG] → TeclawDeviceFileSystem(bot=%s)",
                ctx.bot_id,
            )
            # ``path_mapper`` = teclaw engine-relative mapper: device host path →
            # ``/workspace/...`` | ``/identity/...`` (the path the engine knows the
            # file by). teclaw now forwards every read/write per-file to the engine,
            # so it needs neither OSS nor the whole-artifact device-sync redeliver.
            # ``baas_service`` powers those per-file calls via ``invoke_http`` over
            # the agentclawproxy gateway.
            return TeclawDeviceFileSystem(
                conn_info=conn_info,
                path_mapper=path_mapper,
                baas_service=self._baas_service,
            )

        if provider == "local":
            # ``"local"`` provider 经 resolver 出口 → 走 LocalDeviceFileSystem。
            # 本期 resolver 已带 binding_id;但 LocalDeviceFileSystem 的
            # BaaS-mode 需要 ``DeviceBindingContext`` (adapter_port + tenant),
            # 走旧 _build_binding_ctx 路径仍是单一构建源(Step 3 重构再合并)。
            from agentclaw.community.core.devices.services.local_device_filesystem import (
                LocalDeviceFileSystem,
            )

            binding_ctx = self._build_binding_ctx(ctx.bot_id, ctx.user_id)
            logger.info(
                "[DEVICE-PLUGIN-DEBUG] → LocalDeviceFileSystem(provider=%r, baas_mode=%s)",
                provider, binding_ctx is not None,
            )
            return LocalDeviceFileSystem(
                baas_service=self._baas_service if binding_ctx else None,
                binding_ctx=binding_ctx,
                path_mapper=path_mapper,
            )

        raise UnknownProviderError(
            f"DeviceFilesystemDispatcher: unknown provider={provider!r} "
            f"(bot={ctx.bot_id})"
        )

    def _resolve_arca(
        self, conn_info: dict, path_mapper: Callable[[str], str]
    ) -> "DeviceFileSystem":
        """Build the ARCA device filesystem — **corp-only** (overridable hook).

        The base resolver used by the community / singlebox / test profiles has no
        ARCA runtime and never routes ``provider == "arca"``, so it raises. The
        corp profile binds :class:`CorpDeviceFileSystemResolver`, which overrides
        this to construct ``ArcaDeviceFileSystem`` (a corp-only module the
        community distribution does not ship).
        """
        raise UnknownProviderError(
            "arca device filesystem is corp-only; the community DeviceFileSystem "
            "resolver has no ARCA runtime"
        )

    def _build_binding_ctx(self, bot_id: str, user_id: str):
        """从 bot_id+user_id 提取 DeviceBindingContext；找不到返 None（pathlib fallback）.

        Any lookup failure (missing bot, missing binding, DB error, missing
        table in test fixtures) degrades to ``None`` — caller falls back to
        the pathlib mode in :class:`LocalDeviceFileSystem`. Defensive
        try/except avoids bringing down unrelated routes on a binding-lookup
        hiccup.
        """
        from agentclaw.community.core.devices.models import DeviceBindingContext

        try:
            bot = self._bot_repo.get_by_id_and_owner(bot_id, user_id)
        except Exception as e:
            logger.warning(
                "[DeviceFilesystemDispatcher._build_binding_ctx] bot_repo "
                "lookup failed bot=%s owner=%s: %s — pathlib fallback",
                bot_id, user_id, e,
            )
            return None
        if not bot:
            return None
        binding_id = bot.get("binding_id") if isinstance(bot, dict) else getattr(bot, "binding_id", None)
        if not binding_id:
            return None
        try:
            binding = self._binding_repo.get_by_id(binding_id)
        except Exception as e:
            logger.warning(
                "[DeviceFilesystemDispatcher._build_binding_ctx] binding_repo "
                "lookup failed binding_id=%s: %s — pathlib fallback",
                binding_id, e,
            )
            return None
        if binding is None:
            return None
        props = binding.device_props or {}
        # 要求 device_props 显式带 adapter_port 才算 BaaS-managed binding。
        # 现 LocalDeviceService._do_allocate 落地的 binding 都会写入此字段
        # （plan-01 走 BaaS create_bot 后通过 _start_service 注入）；
        # 测试 fixture / 历史 binding 没此字段的视为"未接 BaaS"，
        # 走 pathlib fallback，避免 _baas_request 真去打 BaaS。
        if "adapter_port" not in props:
            return None
        return DeviceBindingContext(
            binding_id=binding.id,
            device_id=binding.device_id,
            entity_id=binding.entity_id,
            adapter_port=props["adapter_port"],
            tenant=props.get("tenant", ""),
        )
