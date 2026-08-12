"""Domain errors for the per-bot startup script (issue #926).

Here rather than beside the size-cap errors in ``api/`` because this one is
raised by the **repository**, and ``core`` may not import ``api``. The HTTP
adapter imports it from here directly, the same way it already imports
``BotNotFoundError`` from ``core``.
"""
from __future__ import annotations


class StartupScriptSupersededError(RuntimeError):
    """Raised when a write is for a bot that a later one has already replaced.

    The stored row belongs to a higher ``ac_bots.id`` than the writer's, which
    means the writer's bot was deleted and its identifier handed to a new bot
    while the request was in flight. Overwriting would destroy the current
    owner's script — and would also stamp the row back to the dead incarnation,
    so the stale request's own withdrawal would then delete it.

    Not a client error in any useful sense: the caller did nothing wrong, their
    bot simply stopped existing mid-request. The adapter answers it the same way
    it answers any other "this bot is gone".
    """

    def __init__(self, *, stored_incarnation: int, writing_incarnation: int) -> None:
        super().__init__(
            f"startup script belongs to a newer bot "
            f"(stored={stored_incarnation}, writing={writing_incarnation})"
        )
        self.stored_incarnation = stored_incarnation
        self.writing_incarnation = writing_incarnation
