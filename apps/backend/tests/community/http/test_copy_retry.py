"""Handler-unit tests for POST /openapi/v1/bots/copy/{bot_id}/retry.

Idempotency contract: FE shows the retry button for every copy_state except
CONVERGED, and may click it freely. The service throttles (60s CAS), refuses
CONVERGED, and otherwise re-hands-off to create_job via manifest_seam.start_job
(Route B: manifest document, no state_ref). The create_job idempotency key
(UNIQUE on active_idempotency_key) dedupes if a task is still live, so retry is
safe to fire freely — the 60s CAS is the spam guard.

The path's ``bot_id`` is the dst bot (the one FE is polling copy_state on); the
backend resolves it to the operation ledger row via get_by_dst_bot_id.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.openapi_v1.bots.copy import (
    retry_copy,
    RetryResult,
)
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.apply_engine.triggers import (
    CREATE_ON_CONTAINER_TRIGGER,
    CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.bot_copy.types import BotCopyRow, BotCopyStatus

ENV = "dev"
TENANT = "teamclaw"


def _apply(trigger, status) -> SimpleNamespace:
    return SimpleNamespace(trigger=trigger, status=status)


def _row(**over) -> BotCopyRow:
    base = dict(
        operation_id="op_1",
        source_bot_id="src_1",
        source_owner_id="u1",
        dst_bot_id="dst_1",
        dst_owner_id="u1",
        operator_id="u1",
        status=BotCopyStatus.INITIAL,
        env=ENV,
        avernet_tenant=TENANT,
    )
    base.update(over)
    return BotCopyRow(**base)


def _mocks(applies=None, bot_status="PENDING", cas_retry_ok=True):
    bot_copy_repo = MagicMock()
    bot_copy_repo.get_by_dst_bot_id.return_value = _row()
    bot_copy_repo.cas_retry.return_value = cas_retry_ok
    apply_repo = MagicMock()
    apply_repo.recent.return_value = applies or []
    bot_repo = MagicMock()
    bot_repo.get_by_id.return_value = {"status": bot_status}
    manifest_seam = MagicMock()
    # retry_copy re-resolves the dst owner's space for the canonical creation
    # spec (space_kind is a strict key the deserializer reads). The stub returns
    # a personal space whose numeric_id is None (the synthetic personal fallback
    # shape), matching submit_copy's default for a personal-cloud copy where the
    # owner has no real personal Space record.
    space_context = MagicMock()
    space_context.resolve_current.return_value = SimpleNamespace(
        kind="personal", numeric_id=None
    )
    return bot_copy_repo, apply_repo, bot_repo, manifest_seam, space_context


def _retry(mocks, now=datetime(2026, 11, 2, 10, 0, 0), bot_id="dst_1"):
    bot_copy_repo, apply_repo, bot_repo, manifest_seam, space_context = mocks
    return retry_copy(
        bot_id=bot_id,
        operator_id="u1",
        bot_copy_repo=bot_copy_repo,
        apply_repo=apply_repo,
        bot_repo=bot_repo,
        manifest_seam=manifest_seam,
        space_context=space_context,
        now=now,
    )


def test_retry_first_call_re_hands_off_to_create_job():
    mocks = _mocks()
    _, _, _, manifest_seam, _ = mocks
    result = _retry(mocks)
    assert result is RetryResult.RE_ENQUEUED
    mocks[0].get_by_dst_bot_id.assert_called_once()
    mocks[0].cas_retry.assert_called_once()
    start_kwargs = manifest_seam.start_job.call_args.kwargs
    assert start_kwargs["bot_id"] == "dst_1"
    assert start_kwargs["spec"]["source_bot_id"] == "src_1"
    assert "state_ref" not in start_kwargs["spec"]  # Route B: manifest doc, not state_ref
    assert start_kwargs["iframe_url"] is None
    assert start_kwargs["redirect_url"] is None


def test_retry_throttled_within_60s_returns_still_running():
    mocks = _mocks()
    _retry(mocks, now=datetime(2026, 11, 2, 10, 0, 0))
    # 30s later the CAS throttle refuses
    mocks[0].cas_retry.return_value = False
    result = _retry(mocks, now=datetime(2026, 11, 2, 10, 0, 30))
    assert result is RetryResult.STILL_RUNNING
    mocks[3].start_job.assert_called_once()  # only the first call enqueued


def test_retry_converged_returns_terminal_no_enqueue():
    mocks = _mocks(
        applies=[
            _apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED.value),
            _apply(CREATE_ON_CONTAINER_TRIGGER, ApplyStatus.SUCCEEDED.value),
        ],
        bot_status="ACTIVE",
    )
    _, _, _, manifest_seam, _ = mocks
    result = _retry(mocks)
    assert result is RetryResult.TERMINAL
    manifest_seam.start_job.assert_not_called()
    mocks[0].cas_retry.assert_not_called()


def test_retry_unknown_bot_raises_404():
    bot_copy_repo = MagicMock()
    bot_copy_repo.get_by_dst_bot_id.return_value = None
    with pytest.raises(HTTPException) as exc_info:
        retry_copy(
            bot_id="missing",
            operator_id="u1",
            bot_copy_repo=bot_copy_repo,
            apply_repo=MagicMock(),
            bot_repo=MagicMock(),
            manifest_seam=MagicMock(),
            space_context=MagicMock(),
            now=datetime(2026, 11, 2, 10, 0, 0),
        )
    assert exc_info.value.status_code == 404


def test_retry_failed_state_still_re_enqueues_not_terminal():
    # pre_container FAILED → derive FAILED → but user rule: only CONVERGED
    # refuses; FAILED still retries.
    mocks = _mocks(
        applies=[_apply(CREATE_PRE_CONTAINER_TRIGGER, ApplyStatus.FAILED.value)],
        bot_status="PENDING",
    )
    _, _, _, manifest_seam, _ = mocks
    result = _retry(mocks)
    assert result is RetryResult.RE_ENQUEUED
    manifest_seam.start_job.assert_called_once()


def test_retry_team_dst_space_id_resolved_via_numeric_id_not_raw_dst_row():
    # Regression: the spec's space_id MUST come from retry_space.numeric_id, not
    # dst.get("space_id"). For a team dst (dst.space_id = int 200) both routes
    # agree, but the contract that matters is the personal case (see next test).
    # Here we pin that a non-None numeric_id wins over the raw dst value: stub
    # numeric_id=200 and assert the spec carries 200 regardless of dst.space_id.
    mocks = _mocks()
    _bot_copy_repo, _apply_repo, bot_repo, manifest_seam, space_context = mocks
    bot_repo.get_by_id.return_value = {
        "status": "PENDING", "space_id": 200, "owner_name": "Alice"
    }
    space_context.resolve_current.return_value = SimpleNamespace(
        kind="team", numeric_id=200
    )
    assert _retry(mocks) is RetryResult.RE_ENQUEUED
    # header_space_id was stringified from the dst's int (mirror of submit_copy).
    assert space_context.resolve_current.call_args.kwargs["header_space_id"] == "200"
    spec = manifest_seam.start_job.call_args.kwargs["spec"]
    assert spec["space_id"] == 200
    assert spec["space_kind"] == "team"
    # nick_name is read back from the dst's already-written owner_name, so a
    # retry re-stamps the same real name instead of the 工号 worker fallback.
    assert spec["nick_name"] == "Alice"


def test_retry_null_dst_space_id_promoted_to_resolved_personal_numeric_id():
    # The fix this guards: a personal-bot retry on a dst row whose space_id is
    # NULL must NOT send space_id=None into the durable spec. resolve_current
    # turns NULL into the owner's real personal Space, so numeric_id is the
    # real personal id (stub 300 here) -- not None. Sending None (the old
    # dst.get("space_id") behaviour) would make guard_add count only
    # ``space_id IS NULL`` personal bots, under-counting those already promoted
    # to a numeric personal space id, and diverge from submit_copy's first write.
    mocks = _mocks()
    _bot_copy_repo, _apply_repo, bot_repo, manifest_seam, space_context = mocks
    # dst row has NO space_id (NULL -- the legacy personal-bot shape).
    bot_repo.get_by_id.return_value = {"status": "PENDING"}
    # resolve_current promotes NULL → owner's real personal Space, numeric 300.
    space_context.resolve_current.return_value = SimpleNamespace(
        kind="personal", numeric_id=300
    )
    assert _retry(mocks) is RetryResult.RE_ENQUEUED
    assert space_context.resolve_current.call_args.kwargs["header_space_id"] is None
    spec = manifest_seam.start_job.call_args.kwargs["spec"]
    # promoted: None dst → real personal numeric id 300, not None.
    assert spec["space_id"] == 300
    assert spec["space_kind"] == "personal"
    # nick_name fallback: dst row has no owner_name yet (first submit failed
    # before the bot row was written), so nick_name falls back to the dst owner
    # id -- same fallback shape the worker applies at create_flow.py:735.
    assert spec["nick_name"] == "u1"


def test_retry_nick_name_falls_back_to_owner_id_when_dst_row_lacks_owner_name():
    # A partial dst row (no owner_name column value) must not send nick_name=None
    # into the spec -- that would let the worker's ``spec.get("nick_name",
    # user_id)`` resolve to user_id anyway, but explicitly falling back to the
    # dst owner id here keeps retry self-consistent regardless of the worker.
    mocks = _mocks()
    _bot_copy_repo, _apply_repo, bot_repo, manifest_seam, space_context = mocks
    bot_repo.get_by_id.return_value = {"status": "PENDING", "owner_name": None}
    space_context.resolve_current.return_value = SimpleNamespace(
        kind="personal", numeric_id=None
    )
    assert _retry(mocks) is RetryResult.RE_ENQUEUED
    spec = manifest_seam.start_job.call_args.kwargs["spec"]
    assert spec["nick_name"] == "u1"   # row.dst_owner_id
