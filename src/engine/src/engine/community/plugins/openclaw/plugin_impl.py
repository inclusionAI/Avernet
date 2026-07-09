"""OpenClawPluginImpl — thin composition of per-domain mixins.

One impl object satisfies the whole `OpenClawPlugin` facade, sharing a
single default gateway client + a `TokenClientPool`. Two client-resolution
patterns mirror the legacy services exactly:

  - **pooled** (`_pooled_client`): session / chat / cron / relay / approval
    route per-token through the pool.
  - **default** (`_default_client`): models / node use the engine's default
    client (lazily falling back to the module singleton); they ignore the
    token.

Domain methods are split into per-domain mixin files under
`plugins/community/openclaw/`; each mixin is a plain class (no base, no Protocol)
that assumes `self` provides the `_pooled_client` / `_default_client` / `pool`
/ `_model_provider_map` plumbing from `OpenClawPortBase`.

`OpenClawPluginImpl` explicitly inherits `OpenClawPlugin` so static
type-checkers (mypy / pyright) verify full facade conformance.  Importing
`plugin_api` from `plugins` is the allowed DIP edge (leaf → abstraction).
"""
from __future__ import annotations

from engine.community.plugin_api.openclaw.plugin import OpenClawPlugin
from engine.community.plugins.openclaw._approval import _ApprovalPortMixin
from engine.community.plugins.openclaw._base import OpenClawPortBase
from engine.community.plugins.openclaw._chat import _ChatPortMixin
from engine.community.plugins.openclaw._cron import _CronPortMixin
from engine.community.plugins.openclaw._default_config import _DefaultConfigPortMixin
from engine.community.plugins.openclaw._file import _FilePortMixin
from engine.community.plugins.openclaw._mcp import _McpPortMixin
from engine.community.plugins.openclaw._models import _ModelsPortMixin
from engine.community.plugins.openclaw._node import _NodePortMixin
from engine.community.plugins.openclaw._relay import _RelayPortMixin
from engine.community.plugins.openclaw._session import _SessionPortMixin
from engine.community.plugins.openclaw._skills import _SkillsPortMixin
from engine.community.plugins.openclaw.web_shell import _WebShellPortMixin


class OpenClawPluginImpl(
    _SessionPortMixin,
    _ChatPortMixin,
    _CronPortMixin,
    _RelayPortMixin,
    _ApprovalPortMixin,
    _ModelsPortMixin,
    _NodePortMixin,
    _FilePortMixin,
    _DefaultConfigPortMixin,
    _McpPortMixin,
    _SkillsPortMixin,
    _WebShellPortMixin,
    OpenClawPortBase,
    OpenClawPlugin,
):
    """Concrete `OpenClawPlugin` over the real OpenClaw gateway.

    `client` is optional (tests inject; production reuses the shared singleton).
    `pool` is optional (tests inject a mock; production owns a fresh
    `TokenClientPool`).
    """


__all__ = ["OpenClawPluginImpl"]
