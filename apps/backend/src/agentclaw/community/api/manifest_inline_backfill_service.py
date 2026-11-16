"""Service API Protocol for the Manifest inline-content backfill (transitional).

Re-export only. The Protocol is defined in its owning core module
(``core/bot_config_manifest/inline_backfill_protocol.py``); adapters import
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.bot_config_manifest.inline_backfill_protocol import (
    InlineBackfillEntry,
    InlineBackfillReport,
    InlineBackfillRowNotFound,
    ManifestInlineBackfillProtocol,
)

__all__ = [
    "InlineBackfillEntry",
    "InlineBackfillReport",
    "InlineBackfillRowNotFound",
    "ManifestInlineBackfillProtocol",
]
