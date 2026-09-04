"""Platform-managed CLI tools for a bot (W9, issue #1477).

Two halves, and the split is the design:

* **The record** — ``ac_bot_cli_tool`` holds what the platform asked for: the
  pinned ``digest``, the selected ``subpath``, the delivered ``md5``, the
  version, and the object key where the platform kept the bytes.
* **The bytes** — ``store.py`` keeps the platform's own copy in the
  ``bot-data`` object store, because a teclaw artifact composed for a live
  update or a manifest apply has to reference the tool *now* and the engine is
  not the side that has it.
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
from agentclaw.community.core.bot_config_manifest.cli_tools.context import (
    CliToolContext,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.declarations import (
    CliToolDecl,
    CliToolDrift,
    CliToolStatus,
    CliToolOutcome,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.delivery_port import (
    CliToolDeliveryError,
    CliToolDeliveryPort,
    CliToolPlacementError,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.service import (
    CliToolService,
)
from agentclaw.community.core.bot_config_manifest.cli_tools.store import (
    BOT_DATA_STORE,
    CliToolScope,
    CliToolStore,
    CliToolStoreError,
    StoredCliTool,
)

# The two family ports are deliberately NOT re-exported here. Importing
# ``arca_port`` pulls the adapter-transport Plugin API, and a package that
# re-exported both would make every reader of a record pay for both families'
# dependencies. The strategy that binds one imports it by module.

__all__ = [
    "BOT_DATA_STORE",
    "INSTALLED_BY_MANIFEST",
    "BotCliToolModel",
    "BotCliToolRecord",
    "CliToolContext",
    "CliToolDecl",
    "CliToolDeliveryError",
    "CliToolDeliveryPort",
    "CliToolDrift",
    "CliToolStatus",
    "CliToolOutcome",
    "CliToolPlacementError",
    "CliToolScope",
    "CliToolService",
    "CliToolStore",
    "CliToolStoreError",
    "StoredCliTool",
]
