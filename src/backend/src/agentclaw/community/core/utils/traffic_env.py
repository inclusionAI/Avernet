"""Traffic environment normalization for outbound header injection.

The outbound header value is intentionally constrained to the public contract:
``draft`` / ``pre`` / ``prod`` / ``eval``.

This module only maps explicit traffic-env values and service-bot publish
stages. It must not infer from runtime/deploy env (dev/pre/prod), because the
header describes business traffic stage rather than the Backend runtime.
"""
from __future__ import annotations

from typing import Any


TRAFFIC_ENV_ENV_KEY = "AGENTCLAW_TRAFFIC_ENV"
TRAFFIC_ENV_HEADER_KEY = "x-agentclaw-traffic-env"
TRAFFIC_ENV_VALUES = {"draft", "pre", "prod", "eval"}
PUBLISH_STAGE_TO_TRAFFIC_ENV = {
    "draft": "draft",
    "verify": "pre",
    "eval": "eval",
    "online": "prod",
}


def normalize_traffic_env(value: str | None = None) -> str:
    """Normalize explicit traffic env / PublishStage to header value."""
    normalized = str(value or "").strip().lower()
    if normalized in TRAFFIC_ENV_VALUES:
        return normalized
    if normalized in PUBLISH_STAGE_TO_TRAFFIC_ENV:
        return PUBLISH_STAGE_TO_TRAFFIC_ENV[normalized]
    return "draft"


def resolve_traffic_env(
    *,
    explicit: str | None = None,
    extra_envs: dict[str, Any] | None = None,
    stage: str | None = None,
) -> str:
    """Resolve traffic env without consulting runtime/deploy env.

    Priority: explicit argument > backend-controlled env/header aliases >
    PublishStage > draft.
    """
    if explicit:
        return normalize_traffic_env(explicit)
    if isinstance(extra_envs, dict):
        for key in (TRAFFIC_ENV_ENV_KEY, TRAFFIC_ENV_HEADER_KEY):
            value = extra_envs.get(key)
            if value:
                return normalize_traffic_env(str(value))
    if stage:
        return normalize_traffic_env(stage)
    return "draft"
