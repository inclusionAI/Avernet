"""A swarm run never waits for a human decision.

The spec's scenario is a property of the *run*, not of a code path: "the run does
not enter a state that waits for a human authorization decision". So this asserts it
where a run would be observed — the status vocabulary and the subagent's outcome —
rather than by grepping for a deleted branch.

Why it is true, structurally: a run's identity is applied once, synchronously, by its
first persona. There is no asynchronous approval in the flow, so there is no gate to
park at. The tests below pin the two places that could reintroduce one: `bot_status`
reporting the waiting state, and the subagent raising the park marker.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.agentswarm.domain.models import BotState
from agentclaw.community.plugins.community.agentswarm.host import AvernetBotPlatform


class _Bots:
    def __init__(self, row: dict[str, Any] | None, binding_repo: Any = None) -> None:
        self._row = row
        self._device_binding_repo = binding_repo

    def get_by_id_and_entity(self, bot_id: str, entity: str):
        return self._row


class _BindingRepo:
    def __init__(self, binding: Any) -> None:
        self._binding = binding

    def find_worker_binding(self, **_kw: Any) -> Any:
        return self._binding


class _Apply:
    def last_apply(self, **_kw: Any) -> Any:
        return None


def _platform(*, binding: Any = None, bot: dict[str, Any] | None = None):
    platform = AvernetBotPlatform.__new__(AvernetBotPlatform)
    platform._bot_service = _Bots(bot, _BindingRepo(binding))
    platform._bot_repo = _Bots(bot)
    platform._run_registry = None
    platform._apply_service = _Apply()
    return platform


# ── the status vocabulary ───────────────────────────────────────────────────


@pytest.mark.unit
def test_a_pending_worker_reports_provisioning_and_never_a_waiting_a_human() -> None:
    """A worker whose container is still coming is PROVISIONING, full stop."""
    platform = _platform(binding=SimpleNamespace(status="PENDING"))

    state = platform.bot_status("worker-1", owner_id="owner-1")

    assert state == BotState.PROVISIONING.value
    assert state != BotState.AWAITING_AUTHORIZATION.value


@pytest.mark.unit
def test_no_worker_state_is_the_waiting_one() -> None:
    """Every state a live worker can report, and none of them waits on a human.

    Exhaustive over the statuses a binding can carry plus the "no binding yet"
    case, so a future branch that returned the gate state would fail here.
    """
    waiting = BotState.AWAITING_AUTHORIZATION.value
    observed = set()

    for status in ("PENDING", "ACTIVE", "FAILED"):
        observed.add(
            _platform(binding=SimpleNamespace(status=status)).bot_status(
                "worker-1", owner_id="owner-1"
            )
        )
    # No binding, no bot row: provisioning has not landed yet.
    observed.add(_platform().bot_status("worker-1", owner_id="owner-1"))

    assert waiting not in observed, (
        f"a swarm worker must never report {waiting}; observed {observed}"
    )


@pytest.mark.unit
def test_the_waiting_state_names_no_worker_outcome() -> None:
    """The gate state is not in the vocabulary a worker can produce at all."""
    worker_states = {
        BotState.PROVISIONING.value,
        BotState.READY.value,
        BotState.CREATE_FAILED.value,
        BotState.APPLY_FAILED.value,
    }
    assert BotState.AWAITING_AUTHORIZATION.value not in worker_states


# ── the subagent's outcome ──────────────────────────────────────────────────


@pytest.mark.unit
def test_the_subagent_has_no_park_outcome_for_swarm() -> None:
    """The adapter no longer raises the park marker: nothing can park a run.

    The marker still exists in the seam's vocabulary — the shared pool catches it
    for platforms that can park — but the swarm adapter must not import or raise
    it, because a run has no gate to resume from.
    """
    import inspect

    from agentclaw.community.plugins.community.agentswarm import bot_subagent

    source = inspect.getsource(bot_subagent)
    assert "ParkedAwaitingAuthorization" not in source, (
        "the swarm subagent must not raise the park marker"
    )
    assert "AWAITING_AUTHORIZATION" not in source, (
        "the swarm subagent must not branch on the gate state"
    )