"""Policy parsing utilities for API Gateway.

Policy semantics (read after :func:`parse_policy` normalization):

- ``allowed_bots`` contains ``"*"`` → allow all bots (explicit).
- ``allowed_bots`` is empty → deny all bots (fail-closed; also covers the
  legacy ``"NONE"`` sentinel, which is filtered out — a lone ``["NONE"]``
  therefore normalizes to empty = deny all).
- otherwise → whitelist: only bots listed in ``allowed_bots`` are allowed.

A key whose ``policy`` is ``NULL``/empty or whose JSON object lacks the
``allowed_bots`` key is treated as deny-all (fail-closed). The historical
allow-all behavior for these forms has been removed — all keys must now
carry an explicit, structured policy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class APIKeyPolicy:
    """API Key permission policy.

    Encapsulates structured access to policy JSON, avoiding raw dict
    manipulation by callers.
    """

    NONE: str = "NONE"
    ALL: str = "*"

    allowed_bots: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(
            {"allowed_bots": self.allowed_bots},
            ensure_ascii=False,
            separators=(",", ":"),
        )


def parse_policy(policy: str | None) -> APIKeyPolicy:
    """Parse a policy JSON string into an :class:`APIKeyPolicy`.

    Normalization rules:

    - ``None`` / empty string → ``allowed_bots=[]`` (deny all, fail-closed).
    - dict without ``allowed_bots`` key → ``[]`` (deny all, fail-closed).
    - dict with ``allowed_bots`` containing the ``"NONE"`` sentinel → the
      sentinel is filtered out (a lone ``["NONE"]`` becomes empty = deny all;
      ``["NONE", "bot-1"]`` keeps ``["bot-1"]``).
    - any parse failure (invalid JSON / non-dict / types) → ``[]`` (deny all,
      fail-closed).

    Args:
        policy: JSON-format policy string, may be None.

    Returns:
        Normalized :class:`APIKeyPolicy`.
    """
    if not policy or not policy.strip():
        return APIKeyPolicy()  # fail-closed: deny all
    try:
        result = json.loads(policy)
    except (json.JSONDecodeError, TypeError):
        return APIKeyPolicy()  # fail-closed: deny all
    if not isinstance(result, dict):
        return APIKeyPolicy()  # fail-closed: deny all
    if "allowed_bots" not in result:
        return APIKeyPolicy()  # fail-closed: deny all
    raw = result.get("allowed_bots")
    if not isinstance(raw, list):
        return APIKeyPolicy()  # fail-closed: deny all
    # Filter out legacy "NONE" sentinel; a lone ["NONE"] becomes empty (deny all)
    bots = [b for b in raw if b != APIKeyPolicy.NONE]
    if APIKeyPolicy.ALL in bots:
        return APIKeyPolicy(allowed_bots=[APIKeyPolicy.ALL])  # explicit allow-all wins
    return APIKeyPolicy(allowed_bots=bots)
