"""Tests for compute_copy_status (derive-on-read from apply records + bot status)."""

from types import SimpleNamespace

from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.apply_engine.triggers import (
    CREATE_ON_CONTAINER_TRIGGER,
    CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.bot_copy.status_derive import compute_copy_status
from agentclaw.community.core.bot_copy.types import BotCopyRow, BotCopyStatus

ENV = "dev"
TENANT = "teamclaw"


def _apply(trigger, status) -> SimpleNamespace:
    """A duck-typed apply record: compute_copy_status reads .trigger + .status only."""
    return SimpleNamespace(trigger=trigger, status=status)


def _copy_row() -> BotCopyRow:
    return BotCopyRow(
        operation_id="op_1",
        source_bot_id="s1",
        source_owner_id="u1",
        dst_bot_id="d1",
        dst_owner_id="u1",
        operator_id="u1",
        status=BotCopyStatus.INITIAL,
        env=ENV,
        avernet_tenant=TENANT,
    )


def test_returns_none_when_no_copy_row_legacy_bot():
    assert compute_copy_status(copy_row=None, dst_apply_records=[], dst_bot_status=None) is None


def test_auth_pending_when_no_apply_records_yet():
    # No pre_container apply → license still pending or create_job has not reached apply.
    assert compute_copy_status(copy_row=_copy_row(), dst_apply_records=[], dst_bot_status=None) == BotCopyStatus.AUTH_PENDING


def test_created_when_pre_container_apply_running():
    recs = [_apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.RUNNING.value)]
    assert compute_copy_status(copy_row=_copy_row(), dst_apply_records=recs, dst_bot_status=None) == BotCopyStatus.CREATED


def test_created_when_pre_terminal_and_device_not_yet_active():
    # pre done, device not yet ACTIVE (on_container not started) → CREATED.
    # File sync is an on_container signal, so a pre-only terminal state is
    # never FILE_SYNCED (no on_container progress to report yet).
    recs = [_apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED.value)]
    assert compute_copy_status(copy_row=_copy_row(), dst_apply_records=recs, dst_bot_status="PENDING") == BotCopyStatus.CREATED


def test_bot_started_when_pre_terminal_and_device_active():
    # pre done, device ACTIVE (on_container apply not started yet) → converging toward on.
    recs = [_apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED.value)]
    assert compute_copy_status(copy_row=_copy_row(), dst_apply_records=recs, dst_bot_status="ACTIVE") == BotCopyStatus.BOT_STARTED


def test_bot_started_when_on_container_running():
    recs = [
        _apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED.value),
        _apply(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.RUNNING.value),
    ]
    assert compute_copy_status(copy_row=_copy_row(), dst_apply_records=recs, dst_bot_status="ACTIVE") == BotCopyStatus.BOT_STARTED


def test_converged_when_on_container_succeeded_and_bot_active():
    recs = [
        _apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED.value),
        _apply(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED.value),
    ]
    assert compute_copy_status(copy_row=_copy_row(), dst_apply_records=recs, dst_bot_status="ACTIVE") == BotCopyStatus.CONVERGED


def test_converged_when_on_container_partial_and_bot_active():
    # on_container PARTIAL is a terminal state where some entries did not land
    # but the phase finished — same convergence semantics as SUCCEEDED: with the
    # dst device ACTIVE the copy itself is converged (the bot is up and usable;
    # the partial remainder converges via the detail-page manifest apply, not
    # the copy retry endpoint). Bot not yet ACTIVE → still BOT_STARTED.
    recs_failed_after = [
        _apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED.value),
        _apply(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.PARTIAL.value),
    ]
    assert compute_copy_status(copy_row=_copy_row(), dst_apply_records=recs_failed_after, dst_bot_status="ACTIVE") == BotCopyStatus.CONVERGED
    assert compute_copy_status(copy_row=_copy_row(), dst_apply_records=recs_failed_after, dst_bot_status="PROVISIONING") == BotCopyStatus.BOT_STARTED


def test_pre_container_failed_is_failed_not_mistaken_for_on_success():  # Review Focus #5
    # pre FAILED, no on → FAILED. The any-FAILED check fires before on states are read.
    recs = [_apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.FAILED.value)]
    assert compute_copy_status(copy_row=_copy_row(), dst_apply_records=recs, dst_bot_status=None) == BotCopyStatus.FAILED


def test_on_container_failed_is_failed_even_if_pre_succeeded():
    recs = [
        _apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED.value),
        _apply(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.FAILED.value),
    ]
    assert compute_copy_status(copy_row=_copy_row(), dst_apply_records=recs, dst_bot_status="ACTIVE") == BotCopyStatus.FAILED


def test_bot_failed_status_is_failed_even_when_applies_succeeded():
    # Device allocation lives in bot_service.create_bot (_do_allocate), outside
    # the apply engine — when it fails the bot row is FAILED but every apply
    # record may have succeeded (pre_container SUCCEEDED, on_container never
    # started). Without the FAILED-status gate the on-None branch would mask
    # this as FILE_SYNCED; the gate short-circuits to FAILED so FE shows
    # "复制失败" + retry rather than a stuck "复制中".
    recs = [_apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED.value)]
    assert compute_copy_status(
        copy_row=_copy_row(), dst_apply_records=recs, dst_bot_status="FAILED"
    ) == BotCopyStatus.FAILED
