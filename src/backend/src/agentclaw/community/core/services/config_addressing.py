"""Engine-config address composition — the per-engine ``path_mapper`` builder for
the ``config/`` namespace on arca / baas.

The engine-config consumer flow (the publish-stage engine-config read) addresses the
file by the logical namespace ``config/<file>`` and stays provider-agnostic. The
provider-specific composition of that logical path into the real container/host
address lives **here** and is injected into the device-filesystem plugins by the
dispatcher (:meth:`DeviceFilesystemDispatcher.dispatch_addressed`) — never in the
service.

- **arca / baas**: the engine config is kept in the OSS host-dir layout at
  ``{bot_engine_dir}/openclaw.json`` (``config.json`` for ``claude_code``) — the same
  file the bot-level engine-config GET reads via
  ``ArcaDeviceAccessor.get_engine_config_path``. The **real filename is derived from
  ``engine_type``**, not from the logical leaf the caller passes; the mapper only
  validates the ``config/`` prefix (so a host path or a foreign namespace fails loudly
  rather than silently passing through). This is intentional: teclaw requires the
  canonical logical address ``config/teclaw.json`` (its ``to_engine_relative`` maps it
  to ``/config/teclaw.json``), so every provider is addressed with that one logical
  path and arca/baas resolve their own concrete file from the engine.
- **teclaw**: not built here — teclaw keeps its own ``to_engine_relative`` mapper
  (``config/<file>`` → ``/config/<file>``), wired in the dispatcher.

Lives in ``core`` (imports only ``core.config_compose`` + ``core.workspace``); the
dispatcher in ``di`` calls it. The service does not import this module, so it carries
no provider/engine path knowledge. Mirrors ``identity_addressing.py``.
"""
from __future__ import annotations

from typing import Callable

from agentclaw.community.core.config_compose.teclaw_paths import CONFIG_NS
from agentclaw.community.core.workspace.path_factory import get_bot_engine_dir


def build_arca_config_mapper(
    entity_type: str,
    entity_id: str,
    bot_id: str,
    engine_type: str,
) -> Callable[[str], str]:
    """Build the ``config/<file>`` → engine-address mapper for arca / baas.

    The returned callable maps any ``config/<file>`` logical path to the per-bot OSS
    host path the container's ``/api/file/*`` API expects — ``{bot_engine_dir}/config.json``
    for ``claude_code``, ``{bot_engine_dir}/openclaw.json`` otherwise (matching
    ``ArcaDeviceAccessor.get_engine_config_path``). The concrete filename comes from
    ``engine_type``; the logical leaf is ignored. The config flow always passes a
    ``config/<file>`` path, so a non-namespace input is a programming error and fails
    loudly rather than silently passing through.
    """
    prefix = f"{CONFIG_NS}/"

    def _map(logical: str) -> str:
        if not logical.startswith(prefix):
            raise ValueError(
                f"config mapper expected a {prefix!r}-prefixed path, got {logical!r}"
            )
        base = get_bot_engine_dir(entity_id, bot_id, engine_type, entity_type)
        filename = "config.json" if engine_type == "claude_code" else "openclaw.json"
        return str(base / filename)

    return _map


__all__ = ["build_arca_config_mapper"]
