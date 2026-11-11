"""The ``task_nav`` handler — fire-and-log clone fan-out.

Three things to prove: the handler hands each declared source row to the clone
Port with the destination taken from the apply context and the constants
(``scope=exact`` / ``dry_run=false``) written in-handler, not read from the
entry; a clone failure is logged and swallowed (the kind stays ``unchanged``,
the category is never aborted — the deliberate divergence from
``render_screen``'s failed-row-abort); and an empty declared list is a no-op,
not a removal (clawweb's clone only adds grants). The Rule-25 consumer case
then drives the real handler — built by the DI registry — through the real
``HttpBotWorkflowPermissionsClonePort`` and the ``clawweb``-qualified
``LocalHttpClient`` it injects.
"""
from __future__ import annotations

import httpx

from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.materialisers.bot_config_task_nav_handler import (
    TaskNavSyncHandler,
)
from agentclaw.community.core.bot_management.bot_workflow_permissions_clone_port import (
    BotWorkflowPermissionsClonePort,
)


def _ctx() -> ApplyContext:
    return ApplyContext(
        bot_id="b_dst", owner_id="o_dst", actor_id="a", entity_id="e",
        env="dev", tenant="", engine_type="openclaw", bot_type="arca",
        bot={}, capabilities=object(),
    )


class _RecordingPort(BotWorkflowPermissionsClonePort):
    """Records every clone call; optionally raises on the Nth."""

    def __init__(self, *, raise_on: int | None = None) -> None:
        self.calls: list[dict] = []
        self._raise_on = raise_on

    def clone(self, *, source_bot_id, source_owner_id, target_bot_id,
              target_owner_id, scope, dry_run) -> None:
        self.calls.append({
            "source_bot_id": source_bot_id, "source_owner_id": source_owner_id,
            "target_bot_id": target_bot_id, "target_owner_id": target_owner_id,
            "scope": scope, "dry_run": dry_run,
        })
        if self._raise_on is not None and len(self.calls) == self._raise_on:
            raise httpx.ConnectError("clawweb down", request=None)


# ── the converge contract ───────────────────────────────────────────────────


def test_handler_clones_each_entry_targeted_at_dst_with_constants_written_in_handler():
    port = _RecordingPort()
    handler = TaskNavSyncHandler(port)
    assert handler.kind == "task_nav"

    outcome = handler.sync_kind(
        ctx=_ctx(),
        entries=[
            {"type": "bot", "source_bot_id": "src_a", "source_owner_id": "os_a"},
            {"type": "bot", "source_bot_id": "src_b", "source_owner_id": "os_b"},
        ],
        is_empty=False,
    )

    # One clone per entry, target = apply context, constants written in-handler.
    assert port.calls == [
        {
            "source_bot_id": "src_a", "source_owner_id": "os_a",
            "target_bot_id": "b_dst", "target_owner_id": "o_dst",
            "scope": "exact", "dry_run": False,
        },
        {
            "source_bot_id": "src_b", "source_owner_id": "os_b",
            "target_bot_id": "b_dst", "target_owner_id": "o_dst",
            "scope": "exact", "dry_run": False,
        },
    ]
    # Fire-and-log: every entry is ``unchanged`` (the handler does not read the
    # response), and the kind is ok — it never aborts the category.
    assert outcome.kind == "task_nav"
    assert outcome.entries == (
        ("src_a", "unchanged", None),
        ("src_b", "unchanged", None),
    )
    assert outcome.ok is True


def test_handler_is_fire_and_log_a_clone_failure_does_not_abort():
    """The divergence from ``render_screen``: a clone failure (transport error
    OR a non-2xx surfaced by ``raise_for_status``) is logged and swallowed.
    The kind stays ``unchanged`` and ``ok`` — no sibling kind is aborted and
    the apply completes."""
    port = _RecordingPort(raise_on=1)
    handler = TaskNavSyncHandler(port)

    outcome = handler.sync_kind(
        ctx=_ctx(),
        entries=[
            {"type": "bot", "source_bot_id": "src_a", "source_owner_id": "os_a"},
            {"type": "bot", "source_bot_id": "src_b", "source_owner_id": "os_b"},
        ],
        is_empty=False,
    )

    # The first clone raised; the handler caught it and still fanned out the
    # second — the apply was not aborted, and both entries are ``unchanged``.
    assert len(port.calls) == 2
    assert outcome.ok is True
    assert outcome.entries == (
        ("src_a", "unchanged", None),
        ("src_b", "unchanged", None),
    )


def test_handler_treats_an_empty_declared_list_as_a_noop_not_a_removal():
    """Clawweb's clone only adds grants and cannot converge to an empty set,
    so a declared ``task_nav: []`` leaves the destination untouched — unlike
    ``render_screen: []`` which clears the destination's screens."""
    port = _RecordingPort()
    handler = TaskNavSyncHandler(port)

    outcome = handler.sync_kind(ctx=_ctx(), entries=[], is_empty=True)

    assert port.calls == []
    assert outcome.entries == ()
    assert outcome.ok is True
