"""Core-level contract for the parts of this module other core code depends on.

Separate from ``api/bot_app_grant_service.py``, and the duplication is required
rather than accidental. Two architecture rules meet here:

- *Composition roots are the approved place to select concrete implementations*
  — so a core service must not name ``BotAppGrantService`` in its constructor.
- *core/ may only depend on plugin_api/ and the same core/ layer* — enforced by
  ``tests/community/architecture``, so core cannot reach for the Service API
  Protocol either, which is the adapter-facing contract.

The resolution is a protocol that lives **in core**, which is the same shape
``core/bot_collaborator/protocols.py`` already uses for the same reason.

Deliberately narrow: it declares the one operation another core module actually
needs, not the whole service. A dependency should describe what the caller uses,
so widening it is a decision someone has to make rather than something inherited.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BotAppGrantSweepProtocol(Protocol):
    """Withdrawing every authorization standing against a bot.

    Consumed by ``BotService.delete_bot``: deleting a bot has to withdraw the
    grants on it, or applications keep reaching something that no longer exists.
    """

    def revoke_all_for_bot(self, *, bot_id: str, owner_id: str) -> int:
        """Withdraw every authorization on this bot. Returns how many."""
