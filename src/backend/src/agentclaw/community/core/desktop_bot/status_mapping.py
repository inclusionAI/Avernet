"""Pure-function status mapping: BaaS response → local StatusDecision.

No side effects, no external dependencies. Testable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StatusDecision:
    target_status: str | None = None
    release_reason: str | None = None
    soft_delete: bool = False
    log_context: dict[str, str] = field(default_factory=dict)


def map_baas_to_local(
    *,
    baas_response: dict | None,
    current_local_status: str,
    confirmed_orphan: bool,
) -> StatusDecision:
    """Map BaaS device-status response to a local status decision.

    Priority (first match wins):
    1. confirmed_orphan + PENDING → FAILED
    2. confirmed_orphan + other  → RELEASED + soft_delete
    3. bot_status == RELEASED    → RELEASED + soft_delete
    4. bot_status == FAILED      → FAILED
    5. bot_status == DESTROYING  → RELEASING
    6. bot_status == ACTIVE + local PENDING + ALL_OFFLINE → None (重启过渡保护)
    7. bot_status == ACTIVE + device_status mapping
    8. bot_status == PENDING     → None (no change)
    9. unrecognized              → None + warning log
    """
    # Orphan paths
    if confirmed_orphan:
        if current_local_status == "PENDING":
            return StatusDecision(
                target_status="FAILED",
                release_reason="baas_orphan_404",
                log_context={"reason": "orphan_pending_to_failed"},
            )
        return StatusDecision(
            target_status="RELEASED",
            release_reason="baas_orphan_404",
            soft_delete=True,
            log_context={"reason": "orphan_to_released"},
        )

    # BaaS responded normally
    if baas_response is None:
        return StatusDecision(log_context={"warning": "no_baas_response_not_orphan"})

    bot_status = (baas_response.get("bot_status") or "").upper()
    device_status = (baas_response.get("device_status") or "").upper()

    # Terminal: RELEASED
    if bot_status == "RELEASED":
        return StatusDecision(
            target_status="RELEASED",
            soft_delete=True,
            log_context={"reason": "baas_released"},
        )

    # Terminal: FAILED
    if bot_status == "FAILED":
        return StatusDecision(
            target_status="FAILED",
            log_context={"reason": "baas_failed"},
        )

    # Intermediate: DESTROYING → RELEASING
    if bot_status == "DESTROYING":
        return StatusDecision(
            target_status="RELEASING",
            log_context={"reason": "baas_destroying_to_releasing"},
        )

    # ACTIVE with device status
    if bot_status == "ACTIVE":
        # 重启过渡态保护:本地仍是 PENDING(重启刚发起)而设备 ALL_OFFLINE,
        # 是容器已起、进程还没连回来的正常过渡,不应被对账成 OFFLINE。
        # 保持 PENDING,等设备连回(ALL_ONLINE→ACTIVE)或由扫描层的超时兜底处理。
        if current_local_status == "PENDING" and device_status == "ALL_OFFLINE":
            return StatusDecision(
                target_status=None,
                log_context={"pending_transition": "true"},
            )
        device_map = {
            "ALL_ONLINE": "ACTIVE",
            "PARTIAL_ONLINE": "ACTIVE",
            "ALL_OFFLINE": "OFFLINE",
        }
        target = device_map.get(device_status)
        ctx: dict[str, str] = {}
        if device_status == "PARTIAL_ONLINE":
            ctx["partial_online"] = "true"
        if target:
            return StatusDecision(target_status=target, log_context=ctx)
        return StatusDecision(
            log_context={"unrecognized": f"device_status={device_status}"},
        )

    # PENDING — no change
    if bot_status == "PENDING":
        ctx = {}
        if current_local_status != "PENDING":
            ctx["unexpected_baas_pending"] = "true"
        return StatusDecision(target_status=None, log_context=ctx)

    # Unrecognized
    return StatusDecision(
        log_context={"unrecognized": f"bot_status={bot_status},device={device_status}"},
    )


# Statuses BaaS reports authoritatively enough to SHOW in the by-owner list.
_DISPLAY_DEVICE_MAP = {
    "ALL_ONLINE": "ACTIVE",
    "PARTIAL_ONLINE": "ACTIVE",
    "ALL_OFFLINE": "OFFLINE",
}
_DISPLAY_TERMINAL = {"RELEASED", "FAILED"}


def map_baas_to_display(baas_response: dict | None) -> str | None:
    """Map a BaaS device-status response to the status string to SHOW in the list.

    The by-owner list directly consumes BaaS live state (the DB status lags).
    This is a *read-only* mapping, distinct from :func:`map_baas_to_local`
    (the scan's write-decision): there is NO PENDING-transition protection and
    NO orphan handling — the list shows what BaaS reports right now.

    Returns the status to display, or ``None`` to leave the DB value untouched
    (BaaS still PENDING/DESTROYING, unknown device_status, or empty response).
    """
    if not baas_response:
        return None

    bot_status = (baas_response.get("bot_status") or "").upper()
    device_status = (baas_response.get("device_status") or "").upper()

    # Terminal bot states are authoritative regardless of device_status.
    if bot_status in _DISPLAY_TERMINAL:
        return bot_status

    # ACTIVE: derive from the live device status.
    if bot_status == "ACTIVE":
        return _DISPLAY_DEVICE_MAP.get(device_status)

    # PENDING / DESTROYING / unrecognized: no authoritative live status to show.
    return None
