"""Deterministic test ID generator for integration tests.

Provides stable, human-readable identifiers that avoid collisions
across pytest-xdist parallel workers.
"""

import os
import threading

# Per-thread monotonic counters keyed by entity type.
_counters: dict[str, int] = {}
_lock = threading.Lock()


def _worker_prefix() -> str:
    """Return pytest-xdist worker ID or default."""
    return os.environ.get("PYTEST_XDIST_WORKER", "w0")


def _next_sequence(entity: str) -> int:
    """Return next monotonic sequence number for entity type."""
    with _lock:
        _counters[entity] = _counters.get(entity, 0) + 1
        return _counters[entity]


def make_test_id(entity: str, node_name: str = "", index: int = 0) -> str:
    """Generate a deterministic, human-readable test identifier.

    Args:
        entity: Entity type (e.g., "bot", "device", "publish").
        node_name: Pytest node name for scope isolation.
        index: Optional index for multiple entities of same type.

    Returns:
        A deterministic string like "bot-w0-test_insert_record-00000001".
    """
    worker = _worker_prefix()
    seq = _next_sequence(entity)
    parts = [entity, worker]
    if node_name:
        parts.append(node_name)
    parts.append(f"{seq:08d}")
    if index:
        parts.append(str(index))
    return "-".join(parts)


def reset_counters() -> None:
    """Reset all entity counters. Useful between test sessions."""
    with _lock:
        _counters.clear()
