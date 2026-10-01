"""Resource-file address composition — the per-engine ``path_mapper`` builder.

The resources file flow addresses every file by the logical namespace
``workspace/<rel>`` and stays provider-agnostic. The provider-specific composition
of that logical path into the real engine/container address lives **here** and is
injected into the device-filesystem plugins by the dispatcher
(:meth:`DeviceFilesystemDispatcher.dispatch_addressed`) — never in the resources
service or router.

- **arca / baas** (container reached via the engine's ``/api/file/*`` API, which
  remaps the prefix in ``_convert_path``): compose the same host path the router did
  before consolidation — the per-bot workspace dir
  (``get_bot_engine_dir(...)/"workspace"``), **with** ``_maybe_local_translate`` so
  local-mode runs land on the locally-translated path. Byte-identical to today's
  ``get_bot_workspace_dir(path_factory, …)``; the engine does the final remap.
- **local**: same composed (translated) host path — it *is* the on-disk path the
  pathlib-mode ``LocalDeviceFileSystem`` writes to.
- **teclaw**: not built here — teclaw keeps its own ``to_engine_relative`` mapper
  (``workspace/<rel>`` → ``/workspace/<rel>``), wired in the dispatcher.

Mirrors :mod:`agentclaw.community.core.services.identity_addressing`. Lives in ``core``
(imports only ``core.workspace`` + ``core.config_compose``); the dispatcher in ``di``
calls it. The resources service does not import this module, so it carries no
provider/engine path knowledge.
"""
from __future__ import annotations

from typing import Callable

from agentclaw.community.core.config_compose.teclaw_paths import WORKSPACE_NS
from agentclaw.community.core.workspace.path_factory import (
    _maybe_local_translate,
    get_bot_engine_dir,
)


def build_workspace_mapper(
    entity_type: str,
    entity_id: str,
    bot_id: str,
    engine_type: str,
) -> Callable[[str], str]:
    """Build the ``workspace/<rel>`` → host-path mapper for arca / baas / local.

    The returned callable maps a logical ``workspace/<rel>`` path (or the bare
    ``workspace`` namespace, meaning the workspace root) to the host path the
    container's ``/api/file/*`` API expects — i.e. the per-bot workspace dir, kept
    byte-identical to ``get_bot_workspace_dir`` (``get_bot_engine_dir/"workspace"``
    with ``_maybe_local_translate``). The resources flow always passes a
    ``workspace/<rel>`` path, so a non-namespace input is a programming error and
    fails loudly rather than silently passing through.
    """
    prefix = f"{WORKSPACE_NS}/"

    def _map(logical: str) -> str:
        if logical == WORKSPACE_NS:
            rel = ""
        elif logical.startswith(prefix):
            rel = logical[len(prefix):]
        else:
            raise ValueError(
                f"workspace mapper expected a {prefix!r}-prefixed path "
                f"(or bare {WORKSPACE_NS!r}), got {logical!r}"
            )
        base = _maybe_local_translate(
            get_bot_engine_dir(entity_id, bot_id, engine_type, entity_type) / "workspace"
        )
        return str(base / rel) if rel else str(base)

    return _map


__all__ = ["build_workspace_mapper"]
