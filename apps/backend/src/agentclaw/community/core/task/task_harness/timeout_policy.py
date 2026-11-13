"""Effective timeout thresholds for the harness and trajectory RESET gate."""

from __future__ import annotations


DEFAULT_SLA_TIMEOUT = 900.0
DEFAULT_COOP_GROUP_SLA_TIMEOUT = 900.0
DEFAULT_PENDING_TIMEOUT = 180.0


def effective_sla_threshold_ms(
    execution_config: dict | None,
    run_mode: str | None,
) -> int:
    cfg = execution_config or {}
    t = cfg.get("SLA_TIMEOUT")
    if t is None:
        if run_mode == "coop_group":
            return int(DEFAULT_COOP_GROUP_SLA_TIMEOUT * 1000)
        return int(DEFAULT_SLA_TIMEOUT * 1000)
    return int(float(t) * 1000)


def effective_pending_timeout_ms(execution_config: dict | None) -> int:
    cfg = execution_config or {}
    t = cfg.get("PENDING_TIMEOUT")
    if t is None:
        return int(DEFAULT_PENDING_TIMEOUT * 1000)
    return int(float(t) * 1000)
