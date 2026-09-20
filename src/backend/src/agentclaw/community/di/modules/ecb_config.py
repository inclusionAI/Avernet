"""Small ECB-specific config helpers kept outside the large config module."""

from __future__ import annotations

import math
from typing import Any, Callable

from agentclaw.community.di import config as cfg


def resource_ready_settings(
    block: dict[str, Any],
    defaults: cfg.EcbConfig,
    *,
    coerce: Callable[..., Any],
    as_int: Callable[[Any], int],
) -> dict[str, Any]:
    """Parse bounded resource-ready sidecar settings with safe fallbacks."""
    return {
        "resource_ready_timeout_seconds": coerce(
            block,
            "resource_ready_timeout_seconds",
            float,
            defaults.resource_ready_timeout_seconds,
            "ecb",
            valid=lambda value: value > 0 and math.isfinite(value),
        ),
        "resource_ready_worker_threads": coerce(
            block,
            "resource_ready_worker_threads",
            as_int,
            defaults.resource_ready_worker_threads,
            "ecb",
            valid=lambda value: value >= 1,
        ),
        "resource_ready_max_in_flight": coerce(
            block,
            "resource_ready_max_in_flight",
            as_int,
            defaults.resource_ready_max_in_flight,
            "ecb",
            valid=lambda value: value >= 1,
        ),
        "resource_ready_dedupe_ttl_seconds": coerce(
            block,
            "resource_ready_dedupe_ttl_seconds",
            float,
            defaults.resource_ready_dedupe_ttl_seconds,
            "ecb",
            valid=lambda value: value > 0 and math.isfinite(value),
        ),
        "resource_ready_dedupe_max_entries": coerce(
            block,
            "resource_ready_dedupe_max_entries",
            as_int,
            defaults.resource_ready_dedupe_max_entries,
            "ecb",
            valid=lambda value: value >= 1,
        ),
    }
