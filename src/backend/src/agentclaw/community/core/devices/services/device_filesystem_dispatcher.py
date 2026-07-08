"""DeviceFilesystemDispatcher — core entry point for per-bot device-filesystem routing.

The dispatcher is provider-agnostic routing logic and therefore lives in
``core/`` (it was previously defined inside ``di/modules/skill_center_module.py``,
which made every core/adapter consumer import it from a DI module — a backwards
dependency). The actual ``(DeviceContext, path_mapper) -> DeviceFileSystem``
construction (which builds the vendor-specific baas / arca / teclaw / local
filesystem plugins) is a profile-dependent concern, so it is **injected** as a
:class:`DeviceFileSystemResolver`:

- corp / test  → ``core.devices.services.device_filesystem_resolver.DefaultDeviceFileSystemResolver``
- community    → no-op (no OSS container runtime — BaaS-team-owned, out of B6 scope)

Core owns the abstraction (DIP); the prod resolver depends on core, so ``core/``
carries no ``arca`` / ``plugins.prod`` symbol. Mirrors ``DeviceSyncDispatcher``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

from agentclaw.community.core.devices.errors import DeviceServiceError
from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.log import get_logger
from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )

logger = get_logger()


@runtime_checkable
class DeviceFileSystemResolver(Protocol):
    """The injected ``(DeviceContext, path_mapper) -> DeviceFileSystem`` fn.

    Given a resolved :class:`DeviceContext` and a concrete ``path_mapper``,
    construct the per-bot :class:`DeviceFileSystem`. Each profile binds its
    own impl; all vendor plugin construction lives in the impl, never here.
    """

    def __call__(
        self, ctx: "DeviceContext", path_mapper: Callable[[str], str]
    ) -> "DeviceFileSystem":
        ...


# ── bot_type × device_provider 合法性校验 ─────────────────────────────────

# 非法组合集合。规则来源：
# docs/superpowers/backend-agentbox-arca-split-guide.md §1.2
_ILLEGAL_BOT_DEVICE_COMBINATIONS: frozenset[tuple[str, str]] = frozenset({
    ("personal", "baas"),
    # service + baas：当前非法，未来服务化 BaaS 就绪后由隐舟重构后开放。
    # TODO(隐舟): 服务化 BaaS 链路就绪后，从此集合移除 ("service", "baas")
    ("service",  "baas"),
    ("desktop",  "arca"),
})


def _validate_bot_device_combination(bot_type: str, device_provider: str) -> None:
    """Raise DeviceServiceError for illegal bot_type × device_provider combinations.

    bot_type 为空时跳过校验（兼容旧数据，旧 conn_info 不含 bot_type），只打 warning。
    See docs/superpowers/backend-agentbox-arca-split-guide.md §1.2.
    """
    if not bot_type:
        logger.warning(
            "[device-dispatch] bot_type 为空，跳过合法性校验 (device_provider=%r)。"
            "若非旧数据，请检查 bot_type 注入链路。",
            device_provider,
        )
        return
    if (bot_type, device_provider) in _ILLEGAL_BOT_DEVICE_COMBINATIONS:
        raise DeviceServiceError(
            f"非法组合：bot_type={bot_type!r} 不支持 device_provider={device_provider!r}。"
            f"请检查 bot 配置是否正确。"
            f"（service+baas 为待支持组合，待隐舟重构后开放）"
        )


# ── Runtime-keyed device dispatchers ──────────────────────────────────────


class DeviceFilesystemDispatcher:
    """Mints a :class:`DeviceFileSystem` for a (bot_id, user_id) pair.

    Routes by ``conn_info["device_provider"]``:

      - ``"baas"`` → :class:`BaasDeviceFileSystem` (BaaS invoke-http tunnel)
      - ``"arca"`` → :class:`ArcaDeviceFileSystem` (agentclawproxy/proxypass)
      - ``"teclaw"`` → :class:`TeclawDeviceFileSystem` (backend writes OSS itself +
        whole-artifact re-compose+deliver; engine only reads its OSS-backed mount)
      - ``"local"`` / None → :class:`LocalDeviceFileSystem`:
          * 找得到 (bot → binding) 链路 → 构造 BaaS 模式 (plan-01 get_http_info
            + httpx direct to container adapter)
          * 找不到（bot 无 binding 等边界场景） → pathlib fallback（不崩）

    See spec: docs/superpowers/specs/2026-06-05-r-class-fs-to-baas-design.md §3.2

    Note: ``"local"`` historically means "ARCA binding without an active
    sandbox" (NOT ``RUNTIME_MODE=local``); the singlebox path also lands
    here because ``LocalDeviceAccessor.get_connection_info`` returns None.
    """

    def __init__(
        self,
        device_plugin: DeviceAccessor,
        resolve: "DeviceFileSystemResolver",
        *,
        resolver_provider: "Callable[[], DeviceContextResolver] | None" = None,
    ) -> None:
        """``resolve`` is the injected ``(ctx, path_mapper) -> DeviceFileSystem``
        construction fn (prod builds the vendor plugins; community is a no-op). All
        the per-provider construction + the BaaS-binding lookup it needs live in
        that impl, so the dispatcher itself is vendor-free.

        ``device_plugin`` is still used by :meth:`engine_config_path` and the
        legacy :meth:`for_bot` conn-info lookup. ``resolver_provider`` is a lazy
        thunk for :class:`DeviceContextResolver` (production injects
        ``lambda: injector.get(DeviceContextResolver)`` to break the
        ``SkillServiceFactory → DeviceFilesystemDispatcher → DeviceContextResolver
        → … → SkillServiceFactory`` cycle; the resolver isn't needed until
        ``for_bot`` runs at request time). ``None`` keeps the legacy
        construct-conn_info path for direct-construction unit tests.
        """
        self._device_plugin = device_plugin
        self._resolve = resolve
        self._resolver_provider = resolver_provider

    def engine_config_path(
        self,
        bot_id: str,
        user_id: str,
        *,
        entity_id: str,
        engine_type: str,
        entity_type: str = "staff",
    ) -> str:
        """Resolve the engine-config path for a bot, dispatched by provider.

        The bound ``DeviceAccessor`` is not provider-aware (it is hard-bound to
        ``ArcaDeviceAccessor``, which — like baas — returns the OSS host-dir
        ``{bot_dir}/openclaw.json``). teclaw instead owns its config inside the
        container and reads/writes it per-file through the engine API at
        ``/config/teclaw.json``, so it must be addressed **namespace-relative**
        (``config/teclaw.json``) — the teclaw filesystem's ``path_mapper``
        (``to_engine_relative``) maps it to the engine path. Returning the host
        ``openclaw.json`` path for a teclaw bot is what produced the
        ``to_engine_relative`` rejection (500).

        Non-teclaw bots keep the bound plugin's host-path resolution unchanged.
        """
        from agentclaw.community.core.bot_management.services.teclaw_provision_service import (
            DEFAULT_TECLAW_ENGINE_TYPES,
        )
        from agentclaw.community.core.config_compose.teclaw_paths import (
            CONFIG_NS,
            TECLAW_ENGINE_CONFIG_FILE,
        )

        if engine_type in DEFAULT_TECLAW_ENGINE_TYPES:
            return f"{CONFIG_NS}/{TECLAW_ENGINE_CONFIG_FILE}"
        return self._device_plugin.get_engine_config_path(
            bot_id,
            user_id,
            entity_id=entity_id,
            engine_type=engine_type,
            entity_type=entity_type,
        )

    @staticmethod
    def _passthrough_mapper(path: str) -> str:
        """Generic mapper: the caller already holds the engine-relative/host path."""
        return path

    @staticmethod
    def _generic_mapper(provider: str) -> Callable[[str], str]:
        """Mapper for the generic (resources/skills/channels) flow.

        teclaw addresses files by its namespace (``workspace/`` · ``identity/``),
        so it always normalizes via ``to_engine_relative``; every other provider
        receives an already-resolved path and passes it through.
        """
        if provider == "teclaw":
            from agentclaw.community.core.config_compose.teclaw_paths import to_engine_relative
            return to_engine_relative
        return DeviceFilesystemDispatcher._passthrough_mapper

    @staticmethod
    def _namespaced_mapper(
        provider: str,
        namespace: str,
        *,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        engine_type: str,
    ) -> Callable[[str], str]:
        """Mapper for a logical-namespace flow: ``<namespace>/<rel>`` → engine address.

        The logical namespace is one of the engine namespaces (``identity/`` ·
        ``workspace/`` · ``config/``). teclaw is namespace-agnostic —
        ``to_engine_relative`` accepts every engine namespace and just slashes it
        (``identity/<f>`` → ``/identity/<f>``, ``workspace/<f>`` → ``/workspace/<f>``,
        ``config/<f>`` → ``/config/<f>``). Every other provider composes the per-bot host
        path for that namespace (the engine then remaps it, or — for local — it is the
        on-disk path). For ``config/`` the arca/baas filename is derived from
        ``engine_type`` (openclaw.json / config.json), not the logical leaf.
        """
        if provider == "teclaw":
            from agentclaw.community.core.config_compose.teclaw_paths import to_engine_relative
            return to_engine_relative
        from agentclaw.community.core.config_compose.teclaw_paths import (
            CONFIG_NS,
            IDENTITY_NS,
            WORKSPACE_NS,
        )
        if namespace == IDENTITY_NS:
            from agentclaw.community.core.services.identity_addressing import (
                build_arca_identity_mapper,
            )
            return build_arca_identity_mapper(entity_type, entity_id, bot_id, engine_type)
        if namespace == WORKSPACE_NS:
            from agentclaw.community.core.services.resource_addressing import (
                build_workspace_mapper,
            )
            return build_workspace_mapper(entity_type, entity_id, bot_id, engine_type)
        if namespace == CONFIG_NS:
            from agentclaw.community.core.services.config_addressing import (
                build_arca_config_mapper,
            )
            return build_arca_config_mapper(entity_type, entity_id, bot_id, engine_type)
        raise ValueError(
            f"_namespaced_mapper: unsupported namespace {namespace!r}"
        )

    def dispatch_addressed(
        self,
        ctx: DeviceContext,
        *,
        namespace: str,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        engine_type: str,
    ) -> DeviceFileSystem:
        """Build the device-fs for a logical-namespace flow from logical coordinates.

        The caller (identity / resources / …) addresses files as ``<namespace>/<rel>``
        and passes these coordinates (never a composed path). The factory builds the
        per-provider ``path_mapper`` for that namespace here — uniformly for every
        provider — and injects it into the plugin, so the caller stays provider-agnostic.
        """
        mapper = self._namespaced_mapper(
            ctx.provider, namespace, entity_type=entity_type, entity_id=entity_id,
            bot_id=bot_id, engine_type=engine_type,
        )
        return self._build(ctx, mapper)

    def dispatch(self, ctx: DeviceContext) -> DeviceFileSystem:
        """按 ``ctx.provider`` 选 DeviceFileSystem 实例 — 纯机械工厂(generic flow)。

        Generic callers (resources / skills / channels) pass already-resolved paths;
        the factory supplies the matching generic ``path_mapper``. The identity flow
        uses :meth:`dispatch_addressed` instead.
        """
        return self._build(ctx, self._generic_mapper(ctx.provider))

    def _build(
        self, ctx: DeviceContext, path_mapper: Callable[[str], str]
    ) -> DeviceFileSystem:
        """Delegate construction to the injected per-profile resolver fn.

        The vendor baas / arca / teclaw / local construction (and the BaaS-binding
        lookup the local branch needs) lives in the resolver impl
        (``plugins.prod`` for corp/test; no-op for community).
        """
        return self._resolve(ctx, path_mapper)

    def for_bot(self, bot_id: str, user_id: str) -> DeviceFileSystem:
        """⚠️ DEPRECATED — 走兼容层,后续删。

        旧入口,内部走 resolver + dispatch(ctx)。新 caller 请直接用 dispatch(ctx)。

        本期 spec(2026-06-15-device-context-resolver-design.md)留兼容层因 20 处
        caller 未迁(channel/identity/skill_center/http adapter 等),plan scope
        gap。详见 docs/superpowers/handoffs/2026-06-15-device-sync-supplier-for-bot-cleanup-handoff.md

        下期 spec 完成 20 caller 迁 dispatch(ctx) 后,此方法可删。

        兼容层细节:有 resolver 注入时走 ``resolve_for_bot → dispatch``;没有
        (老测试直接构造 dispatcher)则保留旧 DeviceAccessor 链路。生产 DI provider
        已注入 resolver,所以线上链路统一走新路径。
        """
        if self._resolver_provider is not None:
            ctx = self._resolver_provider().resolve_for_bot(bot_id, user_id)
            return self.dispatch(ctx)

        # —— Legacy direct-construction path (no resolver injected; used by unit
        # tests that build the dispatcher with just device_plugin). Build a
        # DeviceContext from the plugin's conn_info and route through dispatch →
        # the injected resolver, so construction lives in one place. Unknown /
        # missing provider maps to "local" (the historical fallback).
        conn_info = self._device_plugin.get_connection_info(bot_id, user_id) or {}
        provider = conn_info.get("device_provider")
        bot_type = conn_info.get("bot_type", "")

        _validate_bot_device_combination(bot_type, provider)

        ctx = DeviceContext(
            provider=provider if provider in ("baas", "arca", "teclaw") else "local",
            conn_info=conn_info,
            binding_id=conn_info.get("bind_id", 0) or 0,
            bot_id=bot_id,
            user_id=user_id,
            bot_type=bot_type,
        )
        return self.dispatch(ctx)
