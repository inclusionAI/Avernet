"""Where a bot's command-line tools live — **and how to change it.**

> **Tuning this is the point of this module.** The right directory per engine
> is a deployment fact, not a code fact, so nothing here is baked in. Read the
> two paragraphs under *How to change it* and you are done; no other file needs
> touching.

**The directory belongs to the engine, not the platform** — *「目录常量归引擎」*
(``engine-requirements.zh-CN.md`` §4 A2). There is no copy of it in platform
code, deliberately: a platform-side copy would become a second answer to a
question the engine already answers, and a stale one the first time an engine
moved it.

The intended rule is that a bot's ``cli/`` sits beside the workspace its agent
actually runs in, so a skill can name the tool by absolute path today and PATH
injection has a sane target later.

How to change it
----------------

**Without deploying code** — set an environment variable on the container.
Per engine wins over global:

===================================  ==================================
``BOT_CLI_DIR_CLAUDE_CODE=/path``    that engine only
``BOT_CLI_DIR=/path``                every engine on this container
===================================  ==================================

Both are absolute paths used verbatim — no ``cli`` is appended, so what you set
is exactly where tools land.

**In code** — add an entry to :data:`ENGINE_CLI_DIRS` below. One line per
engine, and it is the only table to edit.

If neither is present the default applies: the sibling of the workspace BaaS
injected for this bot (``OPENCLAW_WORKSPACE_DIR`` — despite the name, it is set
for *every* engine, per bot and per engine), falling back to
``~/.openclaw/cli``.

Why the default looks like that
-------------------------------

Reading the injected workspace is what keeps bots isolated: it is built per bot
*and* per engine, so two bots on one singlebox host never share a tool
directory. A fixed constant would give them one, and a whole-set replacement
from either would delete the other's tools.

The ``~/.openclaw/cli`` fallback is the community image's convention, where
``start_claude_code.sh`` points that engine's agent at
``/home/admin/.openclaw/workspace`` too. **That image is not what production
deploys**, so treat the fallback as a safe default rather than a statement about
any particular deployment — and override it per engine once the real layout is
known.

Do not confuse the agent's workspace with the *skills* layout root
(``<home>/.claude_code/workspace/skills-pool`` and friends,
``plugins/claude_code/layout_pool.py``). Those are different trees; tools follow
the agent, not the skill pool.

Resolution is lazy, never at import: BaaS injects the workspace at spawn, after
the engine object is constructed, and an operator may change an override
without a rebuild.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from engine.community.plugin_api.workspace_root import workspace_root_strict

#: Applies to every engine on the container. An absolute path, used verbatim.
CLI_DIR_ENV = "BOT_CLI_DIR"

#: ``BOT_CLI_DIR_<ENGINE>`` — one engine only, and it beats :data:`CLI_DIR_ENV`.
CLI_DIR_ENV_PREFIX = "BOT_CLI_DIR_"

#: **The table to edit.** Per-engine defaults, when an engine's directory is
#: known and is not the workspace sibling. Empty on purpose: today every
#: community engine takes the default, and an entry added here should record
#: *why* that engine differs.
#:
#: Example, once the production layout for Claude Code is settled::
#:
#:     ENGINE_CLI_DIRS = {
#:         "claude_code": lambda: Path("/home/admin/.claude_code/cli"),
#:     }
ENGINE_CLI_DIRS: dict[str, Callable[[], Path]] = {}


def cli_dir_env_var(engine: str) -> str:
    """The per-engine override variable for ``engine``.

    ``cli_dir_env_var("claude_code") == "BOT_CLI_DIR_CLAUDE_CODE"``.
    """
    return f"{CLI_DIR_ENV_PREFIX}{engine.upper()}"


def cli_dir_beside(workspace: Path) -> Path:
    """The default rule, stated once: a bot's ``cli/`` is its workspace's sibling."""
    return workspace.parent / "cli"


def default_cli_dir() -> Path:
    """The workspace-derived default, with no override consulted."""
    configured = workspace_root_strict()
    if configured is not None:
        return cli_dir_beside(configured)
    return Path.home() / ".openclaw" / "cli"


def cli_dir_for(engine: str) -> Path:
    """Where ``engine`` keeps this bot's tools, overrides first.

    Order: ``BOT_CLI_DIR_<ENGINE>``, then ``BOT_CLI_DIR``, then this engine's
    :data:`ENGINE_CLI_DIRS` entry, then :func:`default_cli_dir`.
    """
    for var in (cli_dir_env_var(engine), CLI_DIR_ENV):
        override = os.environ.get(var)
        if override and override.strip():
            return Path(override.strip())
    engine_default = ENGINE_CLI_DIRS.get(engine)
    if engine_default is not None:
        return engine_default()
    return default_cli_dir()


def cli_dir_resolver(engine: str) -> Callable[[], Path]:
    """A late-binding resolver for ``engine``, for the service to hold.

    The service takes a callable rather than a path so an override set after
    the engine object exists is still honoured.
    """
    return lambda: cli_dir_for(engine)


__all__ = [
    "CLI_DIR_ENV",
    "CLI_DIR_ENV_PREFIX",
    "ENGINE_CLI_DIRS",
    "cli_dir_beside",
    "cli_dir_env_var",
    "cli_dir_for",
    "cli_dir_resolver",
    "default_cli_dir",
]
