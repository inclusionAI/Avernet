"""Identity-file address composition — the per-engine ``path_mapper`` builders.

The identity HTTP/consumer flow addresses every file by the logical namespace
``identity/<file>`` and stays provider-agnostic. The provider-specific composition
of that logical path into the real engine/container address lives **here** and is
injected into the device-filesystem plugins by the dispatcher
(:class:`DeviceFilesystemDispatcher.dispatch_addressed`) — never in
``IdentityService``.

- **arca / baas** (container reached via the engine's ``/api/file/*`` API, which
  remaps the prefix in ``_convert_path``): compose the same address the router did
  before consolidation — coding runtimes pin CLI-owned files to CLI homes, while
  every other engine uses the per-bot OSS host path (``path_factory``). The engine
  does the final remap.
- **teclaw**: not built here — teclaw keeps its own ``to_engine_relative`` mapper
  (``identity/<file>`` → ``/identity/<file>``), wired in the dispatcher.

Lives in ``core`` (imports only ``core.workspace`` + ``core.config_compose``); the
dispatcher in ``di`` calls it. ``IdentityService`` does not import this module, so
the service carries no provider/engine path knowledge.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from agentclaw.community.core.config_compose.teclaw_paths import IDENTITY_NS
from agentclaw.community.core.workspace.path_factory import get_bot_engine_dir

# claude_code Arca 容器内 workspace 路径 (relocated from the identity router).
_CLAUDE_CODE_ARCA_WORKSPACE_ROOT = "/home/admin/.claude_code/workspace/.claude"

# Coding runtime files live in the CLI's own container-level home, not the
# per-bot workspace. These paths are stable engine image contracts.
_AICODING_RUNTIME_IDENTITY_PATHS: dict[str, Path] = {
    "CLAUDE.md": Path("/home/admin/.claude/CLAUDE.md"),
    "AGENTS.md": Path("/home/admin/.codex/AGENTS.md"),
}


def build_arca_identity_mapper(
    entity_type: str,
    entity_id: str,
    bot_id: str,
    engine_type: str,
) -> Callable[[str], str]:
    """Build the ``identity/<file>`` → engine-address mapper for arca / baas.

    The returned callable maps a logical ``identity/<file>`` path to the address the
    container's ``/api/file/*`` API expects (coding runtime CLI home, claude_code
    container path, or the per-bot OSS host path the engine then remaps). The identity
    flow always passes a ``identity/<file>`` path, so a non-namespace input is a
    programming error and fails loudly rather than silently passing through.
    """
    prefix = f"{IDENTITY_NS}/"

    def _map(logical: str) -> str:
        if not logical.startswith(prefix):
            raise ValueError(
                f"identity mapper expected a {prefix!r}-prefixed path, got {logical!r}"
            )
        name = logical[len(prefix) :]
        if engine_type == "aicoding" and name in _AICODING_RUNTIME_IDENTITY_PATHS:
            return str(_AICODING_RUNTIME_IDENTITY_PATHS[name])
        if engine_type == "claude_code":
            return f"{_CLAUDE_CODE_ARCA_WORKSPACE_ROOT}/{name}"
        base = get_bot_engine_dir(entity_id, bot_id, engine_type, entity_type)
        # openclaw keeps identity files under the workspace subdir (router parity).
        if engine_type == "openclaw":
            base = base / "workspace"
        return str(base / name)

    return _map


__all__ = ["build_arca_identity_mapper"]
