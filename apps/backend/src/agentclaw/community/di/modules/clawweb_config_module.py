"""DI provider for ``ClawwebConfig``.

Lives OUT of ``di/modules/config_module.py`` (which sits at zero headroom under
the 1000-line architecture cap) so the clawweb host config provider does not
push that monolith over the threshold. Mirrors the
``TaskTrajectoryConfigModule`` pattern: a small ``Module`` with one
``@singleton @provider`` that reads the ``clawweb`` block of ``user_config``
(via ``config_module.read_user_config``) and returns a ``ClawwebConfig``.
Registered in ``di/container.py`` alongside ``ConfigModule``.

YAML shape under ``user_config.clawweb``::

    clawweb:
      base_url: "https://clawweb.example.test"   # prod clawweb host (env overlay sets it)
      # pre overlay sets the pre clawweb host; simulator the in-cluster service; singlebox ""

The shipped source carries no real clawweb host (``test_shipped_config_no_corp_identifiers``
guards that) — each env overlay supplies its own value via the ``clawweb`` yaml block.

The ``clawweb_http_client`` HttpClient binding (in
``di/modules/http_client_module.py``) injects ``ClawwebConfig`` and reads
``base_url``; this module is the only place the yaml block is parsed.
"""
from __future__ import annotations

from typing import Any

from injector import Module, provider, singleton

from agentclaw.community.di.clawweb_config import ClawwebConfig
from agentclaw.community.di.modules import config_module


def _block() -> dict[str, Any]:
    """Pull the ``clawweb`` block out of ``user_config`` (``{}`` if missing),
    mirroring the other config modules' ``_block`` helpers."""
    raw = config_module.read_user_config().get("clawweb") or {}
    return dict(raw) if isinstance(raw, dict) else {}


class ClawwebConfigModule(Module):
    """Bind ``ClawwebConfig`` to its deployment config (the clawweb host)."""

    @singleton
    @provider
    def clawweb(self) -> ClawwebConfig:
        block = _block()
        defaults = ClawwebConfig()
        return ClawwebConfig(
            base_url=block.get("base_url", defaults.base_url),
        )
