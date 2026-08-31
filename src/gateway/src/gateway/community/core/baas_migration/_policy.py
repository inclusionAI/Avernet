"""Reading secbaas's ``policy`` column the way secbaas reads it.

A deliberate re-implementation of
``src/baas/src/secbaas/community/api/api_gateway/_policy.py``, not an import:
the gateway does not depend on the ``secbaas`` package, and it must not start
doing so for a migration that is meant to be deleted. The *semantics* are what
have to match, and they are these — every one of them fail-closed:

* ``NULL`` / blank / unparseable / not an object / no ``allowed_bots`` key
  → no bots.
* the legacy ``"NONE"`` sentinel is filtered out, so a lone ``["NONE"]``
  → no bots.
* ``"*"`` anywhere wins outright and collapses the list to allow-all.

Reading this more permissively than secbaas does would hand a migrated app
access secbaas never granted it, which is the one mistake a credential
migration must not make. Reading it *less* permissively only under-grants, and
under-granting is visible; that asymmetry is why every ambiguous form above
resolves to "no bots" rather than to an error.
"""

from __future__ import annotations

import json

#: secbaas's allow-all sentinel.
WILDCARD = "*"

#: secbaas's legacy deny sentinel, filtered out wherever it appears.
_NONE_SENTINEL = "NONE"


def parse_allowed_bots(policy: str | None) -> list[str]:
    """Return the bot references a secbaas policy allows.

    ``[WILDCARD]`` means allow-all; ``[]`` means deny-all. Both are real answers
    — the caller must not read an empty list as "unknown".
    """
    if not policy or not policy.strip():
        return []
    try:
        parsed = json.loads(policy)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []
    raw = parsed.get("allowed_bots")
    if not isinstance(raw, list):
        return []
    bots = [b for b in raw if isinstance(b, str) and b != _NONE_SENTINEL]
    if WILDCARD in bots:
        return [WILDCARD]
    return bots


def split_bot_reference(reference: str) -> tuple[str, str] | None:
    """Split ``{real_bot_id}:{entity_id}`` into its two halves.

    Returns ``None`` when the reference is not that shape — the same judgement
    secbaas's ``parse_bot_entity_id`` makes, extended to reject an empty
    ``real_bot_id`` as well. A grant needs both halves: one names the bot, the
    other names the person whose access is being lent.
    """
    if ":" not in reference:
        return None
    real_bot_id, entity_id = reference.split(":", 1)
    if not real_bot_id or not entity_id:
        return None
    return real_bot_id, entity_id
