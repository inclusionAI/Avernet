"""Platform-managed CLI tools for a bot (W9, issue #1477).

Two halves, and the split is the design:

* **The record** — ``ac_bot_cli_tool`` holds what the platform asked for: the
  pinned ``digest``, the selected ``subpath``, the delivered ``md5``, the
  version, and the object key where the platform kept the bytes.
* **The delivery** — the engine installs a tool *by name*. No container path
  crosses that boundary in either direction: the engine chooses the directory,
  sets the executable bit and exposes the tool to the agent, all inside one
  ``install`` call.

Re-exported here so callers bind to the package rather than to a module path,
which keeps a later file split from breaking every call site.
"""
from agentclaw.community.core.bot_config_manifest.cli_tools.models import (
    INSTALLED_BY_MANIFEST,
    BotCliToolModel,
    BotCliToolRecord,
)

__all__ = [
    "INSTALLED_BY_MANIFEST",
    "BotCliToolModel",
    "BotCliToolRecord",
]
