"""agentswarm-owned error markers — domain-level, zero dependencies.

The D17 isolation gate bans bot_management imports inside
``core/agentswarm/**``, so the retry-classification marker lives here
while the translation from platform quota errors stays at the plugin
edge (``plugins/community/agentswarm/host.py``).
"""
from __future__ import annotations


class NonRetryableProvisioningError(RuntimeError):
    """agentswarm-owned marker: provisioning failed in a way that will
    deterministically recur (e.g. a space quota ceiling), so retrying is
    futile.

    ``bot_id``/``bot_state`` are additive, optional context for the
    authorization gate's terminal verdicts (openspec D3): when a human
    rejection or an elapsed authorization window fails the attempt, the
    runtime records that terminal BotState on the agent row so the run
    surface stops serving a stale consent URL. Doubles that pre-date the
    attributes keep working — both stay empty.
    """

    def __init__(self, message: str = "", *, bot_id: str = "",
                 bot_state: str = "") -> None:
        super().__init__(message)
        self.bot_id = bot_id
        self.bot_state = bot_state
