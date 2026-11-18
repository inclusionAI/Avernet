"""Conformance suite for ``PolicyRetrievalPort``.

Documented invariants:
  - ``retrieve`` returns a list for any ``experience_type`` (including ``None``)
  - implementations never raise when the upstream is unavailable — empty
    list is the disabled / no-match signal (corporate impls log ``reason``
    outside this contract; community no-op returns ``[]`` immediately)
"""

from __future__ import annotations

from typing import Callable

from agentclaw.community.core.task.task_experience.protocols import (
    PolicyRetrievalPort,
)

__all__ = ["assert_policy_retrieval_port_invariants", "PolicyPortFactory"]

PolicyPortFactory = Callable[[], PolicyRetrievalPort]


async def assert_policy_retrieval_port_invariants(
    make_port: PolicyPortFactory,
) -> None:
    """Assert ``PolicyRetrievalPort`` invariants on the freshly-built instance."""
    port = make_port()
    # With no filter set, the result must still be a list — never raise.
    results_no_filter = await port.retrieve("q", bot_id="b")
    assert isinstance(results_no_filter, list)

    # With a specific experience_type the contract still holds.
    results_filtered = await port.retrieve(
        "q", bot_id="b", experience_type="multibot_workflow", active_only=True,
    )
    assert isinstance(results_filtered, list)

    # Repeated calls must continue to succeed (no per-instance caching state
    # that gates subsequent retrievals).
    results_repeat = await port.retrieve("q", bot_id="b")
    assert isinstance(results_repeat, list)
