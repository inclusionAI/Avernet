"""Service API Protocol for system configuration (re-exported from plugin_api).

The Protocol now lives in ``plugin_api/system_config.py`` so ``core/`` layer
consumers (e.g. ``task_dispatch.claim_join_gate``) can depend on it without
importing the ``api/`` layer. This module re-exports the same class object so
every existing router/DI binding (``Injected(SystemConfigServiceProtocol)`` and
``binder.bind(SystemConfigServiceProtocol, ...)``) keeps working unchanged.
"""
from __future__ import annotations

from agentclaw.community.plugin_api.system_config import SystemConfigServiceProtocol

__all__ = ["SystemConfigServiceProtocol"]
