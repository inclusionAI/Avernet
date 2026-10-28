"""Singlebox-only ``ManagerSyncStack`` override — the memory-mode stack.

SINGLEBOX ONLY. The test column installs
``TestBotManagerSyncModule`` (Recording delivery + in-process
coordination doubles, wired for the conformance suites), and this module
is appended **after** it in the singlebox column so the last binding
wins: an explicitly enabled singlebox ``bot_manager_sync`` block gets
the single-replica downgrade stack — the SAME real
trusted-App HTTP delivery through the local Gateway
(:class:`HttpBcsManagersPlugin`, per-tenant credential resolved through
the ``SecretResolver`` exactly like production) with the in-process
:class:`MemoryManagerSyncCoordinator` carrying the configured
generation sentinel value. No Redis service, no ``redis_url``, no
sentinel bootstrap — the whole-stack restart that singlebox always
performs is the dev stack's single-writer guarantee.

The declared ``coordination_mode`` is honored, never overridden: this
override exists for the memory downgrade alone, so a singlebox block
that declares the default ``redis`` mode gets the SAME availability
verdict the community column produces without shared Redis
(``shared-redis-required``) — it must never silently deliver in-process
coordination under a redis declaration.

Invariants kept identical to the community column:

- disabled stays disabled (``unavailable("disabled")``) — this module
  binds a stack, it never turns the feature on by itself;
- the SAME credential availability checks run in memory mode, so a
  missing local app credential makes the feature unavailable with a
  diagnostic instead of fabricating an empty-string credential;
- the plugin-key doubles below ``TestBotManagerSyncModule`` (the
  ``BcsManagersPlugin`` / ``ManagerSyncCoordinator`` DI keys the
  conformance suites resolve) stay untouched — this module adds exactly
  one provider key.
"""
from __future__ import annotations

from injector import Module, provider, singleton, inject

from agentclaw.community.di.bot_manager_sync_config import ManagerSyncConfig
from agentclaw.community.di.modules.bot_manager_sync_module import (
    ManagerSyncStack,
    _resolve_app_credentials,
    build_memory_manager_sync_stack,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

logger = get_logger()


class SingleboxBotManagerSyncModule(Module):
    """singlebox: real HTTP delivery + in-process coordination when enabled."""

    @singleton
    @provider
    @inject
    def manager_sync_stack(
        self,
        config: ManagerSyncConfig,
        secret_resolver: SecretResolver,
    ) -> ManagerSyncStack:
        """The singlebox stack — only stateful when the feature is enabled."""
        if not config.enabled:
            return ManagerSyncStack.unavailable("disabled")
        if config.coordination_mode != "memory":
            # Honor the declared mode: this override exists for the memory
            # downgrade only. A singlebox block declaring the default redis
            # mode surfaces the community column's no-shared-Redis verdict
            # — the dev stack runs no Redis service — never a silent
            # in-process coordinator under a redis declaration.
            return ManagerSyncStack.unavailable("shared-redis-required")
        credentials, missing_reason = _resolve_app_credentials(
            config, secret_resolver
        )
        if missing_reason:
            return ManagerSyncStack.unavailable(missing_reason)
        logger.info(
            "[MANAGER-SYNC] enabled stack resolving (singlebox memory mode "
            "— single-replica dev downgrade, real Gateway delivery): "
            "target=%s env=%s tenants=%d generation=provided",
            config.target_id,
            config.env,
            len(credentials),
        )
        return build_memory_manager_sync_stack(config, credentials)


__all__ = ["SingleboxBotManagerSyncModule"]
