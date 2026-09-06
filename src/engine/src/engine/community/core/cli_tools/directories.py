"""Where each engine keeps its bot's command-line tools.

**The directory belongs to the engine, not the platform** — *「目录常量归引擎」*
(``engine-requirements.zh-CN.md`` §4 A2). There is no copy of it in platform
code, deliberately: a platform-side copy would become a second answer to a
question the engine already answers, and a stale one the first time an engine
moved it.

What every engine *does* share is the placement rule — a bot's ``cli/`` sits
beside its workspace — so that lives once in :func:`cli_dir_beside`, and each
engine supplies only its own workspace root. The workspaces genuinely differ:

============  ==========================================================
engine        workspace root
============  ==========================================================
OpenClaw      ``$OPENCLAW_WORKSPACE_DIR``, else ``~/.openclaw/workspace``
Claude Code   ``<home>/.claude_code/workspace`` (no env override)
============  ==========================================================

so a single shared resolver would have placed Claude Code's tools under
OpenClaw's tree. A third ARCA engine adds one more resolver here and touches
nothing else.

**Resolved lazily, never at import.** OpenClaw's reads an environment variable
BaaS injects at spawn time. On the ARCA deployment this yields
``/home/admin/.openclaw/cli`` — the value the contract names — while on
singlebox it stays per-bot, which a hardcoded constant would break.
"""
from __future__ import annotations

from pathlib import Path

from engine.community.plugin_api.workspace_root import workspace_root_strict

#: Claude Code has no workspace env override, so its root is derived from the
#: container's home the way its own layout code does
#: (``plugins/claude_code/layout_pool.py``).
DEFAULT_HOME = Path("/home/admin")


def cli_dir_beside(workspace: Path) -> Path:
    """The rule, stated once: a bot's ``cli/`` is its workspace's sibling."""
    return workspace.parent / "cli"


def openclaw_cli_dir() -> Path:
    """OpenClaw's tool directory.

    ``$OPENCLAW_WORKSPACE_DIR``'s sibling when BaaS injected one (singlebox,
    desktop — per-bot), else ``~/.openclaw/cli`` (the shared ARCA layout).
    """
    configured = workspace_root_strict()
    if configured is not None:
        return cli_dir_beside(configured)
    return Path.home() / ".openclaw" / "cli"


def claude_code_cli_dir(home: Path = DEFAULT_HOME) -> Path:
    """Claude Code's tool directory: ``<home>/.claude_code/cli``."""
    return cli_dir_beside(home / ".claude_code" / "workspace")


__all__ = [
    "DEFAULT_HOME",
    "claude_code_cli_dir",
    "cli_dir_beside",
    "openclaw_cli_dir",
]
