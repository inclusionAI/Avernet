"""Shared device selection helpers for bot resolver services.

Provides consistent hashing ring-based and random device selection
from a list of device records. Used by both the WSS and HTTP
bot resolver services.

Design decisions (per design.md):
- D-01: Extracted from bot_wss_resolver_service.py into shared module
- D-02: Same consistent hashing logic reused across all bot resolvers
"""

import hashlib
import random
from typing import TYPE_CHECKING

from secbaas.logger import get_logger

if TYPE_CHECKING:
    from secbaas.core.repository.device import DeviceRecord

logger = get_logger("core-service")

# Virtual nodes per physical device for even hash distribution
_VIRTUAL_NODES = 100


def build_ring(
    active_devices: list["DeviceRecord"],
) -> list[tuple[int, "DeviceRecord"]]:
    """Build a consistent hash ring from active devices.

    Each physical device is placed on the ring _VIRTUAL_NODES times
    using different hash seeds (``{device_uuid}:{vnode_index}``) for
    even distribution, especially with small device counts.

    Args:
        active_devices: List of ACTIVE device records

    Returns:
        Sorted list of (hash_value, DeviceRecord) tuples forming the ring
    """
    ring: list[tuple[int, DeviceRecord]] = []
    for d in active_devices:
        for vnode in range(_VIRTUAL_NODES):
            seed = f"{d.device_uuid}:{vnode}"
            h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
            ring.append((h, d))
    ring.sort(key=lambda x: x[0])
    return ring


def select_by_affinity(
    ring: list[tuple[int, "DeviceRecord"]],
    device_affinity: str,
) -> "DeviceRecord":
    """Select a device from the consistent hash ring using the affinity key.

    Computes the MD5 hash of the affinity key and finds the first device
    clockwise on the ring (wrapping around if beyond the last entry).

    Args:
        ring: Sorted list of (hash_value, DeviceRecord) from build_ring()
        device_affinity: Affinity key string

    Returns:
        The DeviceRecord nearest clockwise on the ring
    """
    key = int(hashlib.md5(device_affinity.encode()).hexdigest(), 16)
    unique_devices = len(set(d.device_uuid for _, d in ring))
    for h, d in ring:
        if key <= h:
            logger.info(
                f"Affinity selection: key_hash={key}, "
                f"matched_device_uuid={d.device_uuid}, "
                f"ring_size={len(ring)}, "
                f"unique_devices={unique_devices}, "
                f"device_affinity={device_affinity!r}"
            )
            return d
    logger.info(
        f"Affinity key wrapping around ring: key_hash={key}, "
        f"ring_size={len(ring)}, unique_devices={unique_devices}, "
        f"selected_first={ring[0][1].device_uuid}"
    )
    return ring[0][1]


def select_active_device(
    devices: list["DeviceRecord"],
    device_affinity: str | None = None,
) -> "DeviceRecord | None":
    """Select an ACTIVE device from the list.

    When ``device_affinity`` is provided, uses consistent hashing to
    deterministically select a device — same input always maps to the same
    ``device_uuid`` across all service instances (stateless).

    When ``device_affinity`` is ``None`` (default), falls back to random
    selection for simple load balancing.

    Args:
        devices: List of device records
        device_affinity: Optional affinity key for sticky device selection

    Returns:
        A selected ACTIVE device, or None if no ACTIVE devices
    """
    active_devices = [d for d in devices if d.status == "ACTIVE"]
    if not active_devices:
        return None
    if device_affinity is not None:
        ring = build_ring(active_devices)
        return select_by_affinity(ring, device_affinity)
    return random.choice(active_devices)


def select_available_device(
    devices: list["DeviceRecord"],
    device_affinity: str | None = None,
) -> "DeviceRecord | None":
    """Select an available device from the list with a relaxed status filter.

    Unlike :func:`select_active_device` which only considers ``ACTIVE`` devices,
    this function accepts devices in ``ACTIVE``, ``PENDING``, ``UPDATING``,
    or ``OFFLINE`` status — covering bot creation/publish flows where devices
    are not yet fully active, including transient OFFLINE windows caused by
    mng daemon reconnection during startup.

    **Status whitelist:** ``"ACTIVE"``, ``"PENDING"``, ``"UPDATING"``, ``"OFFLINE"``

    **Excluded states** (return ``None`` when all devices are in these)::

        ``"RELEASED"``, ``"FAILED"``, ``"STOPPED"``

    Rationale: RELEASED and FAILED are terminal states with no in-progress
    startup. STOPPED means the container has already been destroyed and the
    mng daemon is unreachable. OFFLINE is included because it represents a
    transient state where the device is reachable but its daemon/service is
    temporarily unavailable (e.g., during mng daemon reconnection).

    Selection strategy mirrors :func:`select_active_device` exactly:

    * ``device_affinity`` provided → consistent hashing via
      :func:`build_ring` / :func:`select_by_affinity` (deterministic,
      same key → same device across instances).
    * ``device_affinity`` is ``None`` → random choice for simple load
      balancing.

    Args:
        devices: List of device records to select from.
        device_affinity: Optional affinity key for sticky device selection.

    Returns:
        A selected available device, or ``None`` if no device matches the
        accepted statuses.
    """
    available_devices = [
        d for d in devices if d.status in ("ACTIVE", "PENDING", "UPDATING", "OFFLINE")
    ]
    if not available_devices:
        return None
    if device_affinity is not None:
        ring = build_ring(available_devices)
        return select_by_affinity(ring, device_affinity)
    return random.choice(available_devices)
