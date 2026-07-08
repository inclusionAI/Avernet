"""Policy parsing utilities for API Gateway."""

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

    allowed_bots: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(
            {"allowed_bots": self.allowed_bots},
            ensure_ascii=False,
            separators=(",", ":"),
        )


def parse_policy(policy: str | None) -> APIKeyPolicy:
    """Parse a policy JSON string into an APIKeyPolicy.

    Args:
        policy: JSON-format policy string, may be None.

    Returns:
        Parsed APIKeyPolicy; empty default policy on parse failure or
        None input.
    """
    if not policy:
        return APIKeyPolicy()
    try:
        result = json.loads(policy)
        if not isinstance(result, dict):
            return APIKeyPolicy()
        return APIKeyPolicy(
            allowed_bots=result.get("allowed_bots", []) or [],
        )
    except (json.JSONDecodeError, TypeError):
        return APIKeyPolicy()
