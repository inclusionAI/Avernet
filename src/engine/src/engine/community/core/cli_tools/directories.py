"""Where a bot's command-line tools live.

**The directory belongs to the engine, not the platform** — *「目录常量归引擎」*
(``engine-requirements.zh-CN.md`` §4 A2). There is no copy of it in platform
code, deliberately: a platform-side copy would become a second answer to a
question the engine already answers, and a stale one the first time an engine
moved it.

The placement rule is that a bot's ``cli/`` sits **beside the workspace its
agent actually runs in**, so a skill can name the tool by absolute path today
and PATH injection has a sane target later.

**One resolver serves both community engines**, because on both shipped
deployments they resolve the same workspace:

* **ARCA image** — ``docker/agent/start_claude_code.sh`` exports
  ``CLAUDE_CODE_DEFAULT_CWD`` and ``RELAY_DEFAULT_CWD`` as
  ``/home/admin/.openclaw/workspace``, the same directory an OpenClaw bot
  uses. That path is the image's conventional *bot* workspace, not OpenClaw's
  private tree, despite the name.
* **singlebox / desktop** — BaaS injects ``OPENCLAW_WORKSPACE_DIR`` for
  **every** engine (``_process_manager.py``), built per bot *and* per engine as
  ``…/{bot_id}/{engine}/workspace`` (``_workspace.py``). The variable is the
  generic "this bot's workspace"; only its name says otherwise.

Reading that variable is what keeps bots isolated. A fixed per-engine constant
would give every bot on a singlebox host the same tool directory, and one
bot's whole-set replacement would delete another bot's tools.

Do not confuse the agent's workspace with the *skills* layout root
(``<home>/.claude_code/workspace/skills-pool`` and friends,
``plugins/claude_code/layout_pool.py``). Those are different trees; tools
follow the agent, not the skill pool.

**Resolved lazily, never at import**, because BaaS injects the variable at
spawn — after the engine object is constructed.
"""
from __future__ import annotations

from pathlib import Path

from engine.community.plugin_api.workspace_root import workspace_root_strict


def cli_dir_beside(workspace: Path) -> Path:
    """The rule, stated once: a bot's ``cli/`` is its workspace's sibling."""
    return workspace.parent / "cli"


def bot_cli_dir() -> Path:
    """This bot's tool directory.

    The injected workspace's sibling when BaaS set one — which is per bot and
    per engine, so bots never share — else the ARCA image's conventional
    ``~/.openclaw/cli``.
    """
    configured = workspace_root_strict()
    if configured is not None:
        return cli_dir_beside(configured)
    return Path.home() / ".openclaw" / "cli"


__all__ = [
    "bot_cli_dir",
    "cli_dir_beside",
]
