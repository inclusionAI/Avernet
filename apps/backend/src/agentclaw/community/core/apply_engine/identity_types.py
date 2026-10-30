"""The identity-file vocabulary the engine and the Manifest grammar share.

Which file types a bot's engine accepts, and which names the engine owns
(so a desired state may never declare them). Defined here because the
``identity`` materialiser re-asks both at apply time and the Manifest
validator asks them at ``PUT``; one definition keeps "accepted" and
"appliable" the same set.
"""

from __future__ import annotations

__all__ = [
    "RESERVED_IDENTITY_FILES",
    "legal_identity_types",
]

#: Engine-generated runtime state. Apply never writes them and never removes
#: them (work-items §3.2), so a document declaring one would be accepted and
#: then never converge. Refused at write time instead, which is what keeps
#: "accepted" and "appliable" the same set.
RESERVED_IDENTITY_FILES: frozenset[str] = frozenset({"MEMORY.md"})


def legal_identity_types(engine_type: str) -> frozenset[str]:
    """The identity files this engine accepts.

    Imported lazily, and that is not style: ``core/services/identity.py`` pulls
    in the device dispatcher and a DI module at import time, so a module-level
    import would drag the injector into a pure validator and close a cycle. The
    constants stay defined there — one definition, per that module's own
    docstring — and ``harness/services/bot_profile.py`` reaches them the same
    way for the same reason.
    """
    from agentclaw.community.core.services.identity import (
        CLAUDE_CODE_IDENTITY_FILES,
        VALID_IDENTITY_FILES,
    )

    if engine_type == "claude_code":
        return frozenset(CLAUDE_CODE_IDENTITY_FILES)
    return frozenset(VALID_IDENTITY_FILES)
