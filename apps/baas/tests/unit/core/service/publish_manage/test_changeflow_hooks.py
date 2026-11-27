# mypy: disable-error-code="arg-type,attr-defined"
"""Unit tests for the ChangeFlow observe-only hooks in DefaultPublishService.

Covers the P1 W2 community-side helpers:
- ``_cf_read_change_id`` / ``_cf_persist`` (extra_config nested cf dict)
- ``_cf_build_stages`` (frozen release plan from batch + record rows)
- ``_cf_on_register`` (register hook + change_id persistence)
- ``_cf_report_terminal`` (terminal hook + outcome mapping)
- ``_cf_report_batch_result`` (batch-result hook + per-device states)
- ``_cf_fire_pre_check`` / ``_cf_pre_check_safe`` (detached pre-check)
- register trigger condition inside ``create_publish`` (online + CREATE/UPDATE)

The ``create_publish`` integration path is heavy (it drives the full publish
state machine). To keep these tests focused on ChangeFlow behavior we:
  * drive ``create_publish`` only for the register trigger-condition matrix
    (point 1) with ``_cf_on_register`` / ``_cf_build_stages`` patched out;
  * unit-test every other helper directly (points 2-8).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.publish_manage import (
    BatchStatus,
    PublishConfig,
    PublishStatus,
    PublishType,
    PublishRecordResult,
)
from secbaas.community.core.service.publish_manage import DefaultPublishService

TENANT = "test_tenant"
ENV = "default"
OPERATOR = "test-op"
BOT_ID = 1
PUBLISH_ID = 100


# ---------------------------------------------------------------------------
# Service construction
# ---------------------------------------------------------------------------
def _make_service(cf_observer: object | None = None) -> DefaultPublishService:
    """Build a DefaultPublishService with mock repos and an injected observer.

    Mirrors the ``_make_service`` pattern in test_handle_device_callback.py.
    The cf_observer is passed through directly (no DI) so each test controls
    the observer's behavior.
    """
    return DefaultPublishService(
        bot_repo=MagicMock(),
        device_repo=MagicMock(),
        rel_repo=MagicMock(),
        session_repo=MagicMock(),
        publish_repo=MagicMock(),
        batch_repo=MagicMock(),
        publish_record_repo=MagicMock(),
        template_service=MagicMock(),
        bot_service=MagicMock(),
        device_service=MagicMock(),
        cf_observer=cf_observer,
    )


def _publish_record(*, extra_config: dict | None = None, **kw) -> MagicMock:
    """A minimal publish record stub for helper-level tests."""
    rec = MagicMock()
    rec.id = kw.get("id", PUBLISH_ID)
    rec.bot_id = kw.get("bot_id", BOT_ID)
    rec.env = kw.get("env", ENV)
    rec.name = kw.get("name", "bot-name")
    rec.creator = kw.get("creator", OPERATOR)
    rec.last_publish_id = kw.get("last_publish_id", None)
    rec.replica_desired = kw.get("replica_desired", 0)
    rec.extra_config = extra_config
    return rec


# ---------------------------------------------------------------------------
# 8. _cf_read_change_id
# ---------------------------------------------------------------------------
class TestCfReadChangeId:
    def test_none_when_extra_config_is_none(self):
        svc = _make_service()
        assert svc._cf_read_change_id(None) is None

    def test_none_when_no_cf_key(self):
        svc = _make_service()
        assert svc._cf_read_change_id({"other": 1}) is None

    def test_none_when_cf_is_not_dict(self):
        svc = _make_service()
        assert svc._cf_read_change_id({"cf": "not-a-dict"}) is None

    def test_none_when_change_id_missing(self):
        svc = _make_service()
        assert svc._cf_read_change_id({"cf": {}}) is None

    def test_returns_change_id_when_present(self):
        svc = _make_service()
        assert svc._cf_read_change_id({"cf": {"change_id": "extp-x"}}) == "extp-x"

    def test_returns_none_for_falsy_change_id(self):
        svc = _make_service()
        assert svc._cf_read_change_id({"cf": {"change_id": ""}}) is None


# ---------------------------------------------------------------------------
# 7. _cf_build_stages
# ---------------------------------------------------------------------------
class TestCfBuildStages:
    def test_two_batches_ordered_by_index_with_targets(self):
        """Two batches → stages ordered 第N批 by batch_index, targets from records."""
        svc = _make_service()

        batch_a = SimpleNamespace(id=11, batch_index=2)
        batch_b = SimpleNamespace(id=10, batch_index=1)
        svc._publish_batch_repo.list_by_publish_id.return_value = [batch_a, batch_b]

        rec_b1 = SimpleNamespace(extra_config={"device_uuid": "dev-1"})
        rec_b2 = SimpleNamespace(extra_config={"device_uuid": "dev-2"})

        def _list_records(_pub_id, batch_id, _t, _e):
            return {11: [rec_b2], 10: [rec_b1]}[batch_id]

        svc._publish_record_repo.list_by_publish_id_and_batch_id.side_effect = (
            _list_records
        )

        stages = svc._cf_build_stages(
            tenant=TENANT, env=ENV, publish_id=PUBLISH_ID
        )

        assert stages == [
            {"name": "第1批", "batches": [{"index": 1, "targets": ["dev-1"]}]},
            {"name": "第2批", "batches": [{"index": 2, "targets": ["dev-2"]}]},
        ]

    def test_filters_records_without_device_uuid(self):
        svc = _make_service()
        batch = SimpleNamespace(id=10, batch_index=1)
        svc._publish_batch_repo.list_by_publish_id.return_value = [batch]
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
            SimpleNamespace(extra_config={"device_uuid": "dev-1"}),
            SimpleNamespace(extra_config={}),
            SimpleNamespace(extra_config=None),
            SimpleNamespace(extra_config={"device_uuid": "dev-2"}),
        ]

        stages = svc._cf_build_stages(tenant=TENANT, env=ENV, publish_id=PUBLISH_ID)
        assert stages[0]["batches"][0]["targets"] == ["dev-1", "dev-2"]

    def test_empty_when_no_batches(self):
        svc = _make_service()
        svc._publish_batch_repo.list_by_publish_id.return_value = []
        assert svc._cf_build_stages(tenant=TENANT, env=ENV, publish_id=PUBLISH_ID) == []

    def test_returns_empty_on_repo_failure(self):
        """Best-effort: a repo raise must not propagate out of _cf_build_stages."""
        svc = _make_service()
        svc._publish_batch_repo.list_by_publish_id.side_effect = RuntimeError("db down")
        assert svc._cf_build_stages(tenant=TENANT, env=ENV, publish_id=PUBLISH_ID) == []


# ---------------------------------------------------------------------------
# 2. _cf_on_register: cf nested write on success
# ---------------------------------------------------------------------------
class TestCfOnRegisterPersist:
    @pytest.mark.asyncio
    async def test_persists_cf_nested_dict_on_success(self):
        """observer.on_register returns change_id → cf nested into extra_config."""
        observer = MagicMock()
        observer.on_register = AsyncMock(return_value="extp-x")
        svc = _make_service(cf_observer=observer)

        original_extra = {"publish_stage": "online"}
        pub_rec = _publish_record(extra_config=original_extra)

        await svc._cf_on_register(
            tenant=TENANT,
            env=ENV,
            publish_record=pub_rec,
            publish_type_str="CREATE",
            stages=[{"name": "第1批", "batches": [{"index": 1, "targets": ["d1"]}]}],
            operator=OPERATOR,
        )

        # observer called once with a PublishCtx carrying the right fields
        observer.on_register.assert_awaited_once()
        ctx = observer.on_register.call_args.args[0]
        assert ctx.publish_id == PUBLISH_ID
        assert ctx.publish_type == "CREATE"
        assert ctx.bot_id == BOT_ID
        assert ctx.stages == [
            {"name": "第1批", "batches": [{"index": 1, "targets": ["d1"]}]}
        ]
        assert ctx.candidate_version == f"v{PUBLISH_ID}"
        # previous_version empty when last_publish_id is None
        assert ctx.previous_version == ""

        # cf sub-dict written into extra_config (nested, not flattened)
        update_call = svc._publish_repo.update_publish.call_args
        written_ec = update_call.kwargs["extra_config"]
        assert written_ec["cf"]["change_id"] == "extp-x"
        assert "registered_at" in written_ec["cf"]
        assert written_ec["cf"]["terminal_reported"] is False
        assert written_ec["cf"]["batches"] == {}
        # original keys preserved
        assert written_ec["publish_stage"] == "online"
        # modifier is the operator
        assert update_call.kwargs["modifier"] == OPERATOR

    @pytest.mark.asyncio
    async def test_previous_version_derived_from_last_publish_id(self):
        observer = MagicMock()
        observer.on_register = AsyncMock(return_value="extp-y")
        svc = _make_service(cf_observer=observer)
        pub_rec = _publish_record(
            extra_config={"publish_stage": "online"}, last_publish_id=77
        )

        await svc._cf_on_register(
            tenant=TENANT,
            env=ENV,
            publish_record=pub_rec,
            publish_type_str="UPDATE",
            stages=[],
            operator=OPERATOR,
        )

        ctx = observer.on_register.call_args.args[0]
        assert ctx.previous_version == "v77"

    @pytest.mark.asyncio
    async def test_no_persist_when_observer_returns_none(self):
        """observer returns None → no cf.change_id written, hooks later skip."""
        observer = MagicMock()
        observer.on_register = AsyncMock(return_value=None)
        svc = _make_service(cf_observer=observer)
        pub_rec = _publish_record(extra_config={"publish_stage": "online"})

        await svc._cf_on_register(
            tenant=TENANT,
            env=ENV,
            publish_record=pub_rec,
            publish_type_str="CREATE",
            stages=[],
            operator=OPERATOR,
        )

        observer.on_register.assert_awaited_once()
        svc._publish_repo.update_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_persist_when_observer_returns_empty_string(self):
        observer = MagicMock()
        observer.on_register = AsyncMock(return_value="")
        svc = _make_service(cf_observer=observer)
        pub_rec = _publish_record(extra_config={})

        await svc._cf_on_register(
            tenant=TENANT,
            env=ENV,
            publish_record=pub_rec,
            publish_type_str="CREATE",
            stages=[],
            operator=OPERATOR,
        )

        svc._publish_repo.update_publish.assert_not_called()


# ---------------------------------------------------------------------------
# 4. non-blocking: observer raises → swallowed, publish proceeds
# ---------------------------------------------------------------------------
class TestCfNonBlocking:
    @pytest.mark.asyncio
    async def test_register_observer_raise_does_not_propagate(self):
        observer = MagicMock()
        observer.on_register = AsyncMock(side_effect=RuntimeError("cf down"))
        svc = _make_service(cf_observer=observer)
        pub_rec = _publish_record(extra_config={})

        # Must not raise
        await svc._cf_on_register(
            tenant=TENANT,
            env=ENV,
            publish_record=pub_rec,
            publish_type_str="CREATE",
            stages=[],
            operator=OPERATOR,
        )
        # No persist on failure
        svc._publish_repo.update_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_terminal_observer_raise_does_not_propagate(self):
        observer = MagicMock()
        observer.on_publish_terminal = AsyncMock(side_effect=RuntimeError("cf down"))
        svc = _make_service(cf_observer=observer)
        pub_rec = _publish_record(
            extra_config={"cf": {"change_id": "extp-t", "terminal_reported": False}}
        )

        await svc._cf_report_terminal(
            tenant=TENANT,
            env=ENV,
            publish_id=PUBLISH_ID,
            status=PublishStatus.SUCCESS.value,
            publish_record=pub_rec,
        )
        # terminal_reported must NOT be written when observer raised
        svc._publish_repo.update_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_result_observer_raise_does_not_propagate(self):
        observer = MagicMock()
        observer.on_batch_result = AsyncMock(side_effect=RuntimeError("cf down"))
        svc = _make_service(cf_observer=observer)
        pub_rec = _publish_record(
            extra_config={"cf": {"change_id": "extp-b"}}
        )
        svc._publish_repo.get_by_id.return_value = pub_rec
        batch = SimpleNamespace(id=5, batch_index=1, gmt_complete=None)
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = []

        # Must not raise
        await svc._cf_report_batch_result(
            tenant=TENANT,
            env=ENV,
            publish_id=PUBLISH_ID,
            batch=batch,
            batch_status=BatchStatus.COMPLETED.value,
            counts={},
        )

    @pytest.mark.asyncio
    async def test_pre_check_safe_swallows_observer_raise(self):
        observer = MagicMock()
        observer.on_pre_check = AsyncMock(side_effect=RuntimeError("cf down"))
        svc = _make_service(cf_observer=observer)
        from secbaas.community.spi.changeflow import BatchCtx

        ctx = BatchCtx(
            publish_id=PUBLISH_ID,
            change_id="extp-p",
            stage="第1批",
            batch_index=1,
            targets=[],
            candidate_version=f"v{PUBLISH_ID}",
        )
        await svc._cf_pre_check_safe(ctx)
        observer.on_pre_check.assert_awaited_once()


# ---------------------------------------------------------------------------
# 3. change_id None → batch/terminal/pre-check hooks skip
# ---------------------------------------------------------------------------
class TestCfSkipWhenNoChangeId:
    @pytest.mark.asyncio
    async def test_terminal_skipped_when_no_change_id(self):
        observer = MagicMock()
        observer.on_publish_terminal = AsyncMock()
        svc = _make_service(cf_observer=observer)
        pub_rec = _publish_record(extra_config={})  # no cf

        await svc._cf_report_terminal(
            tenant=TENANT,
            env=ENV,
            publish_id=PUBLISH_ID,
            status=PublishStatus.SUCCESS.value,
            publish_record=pub_rec,
        )
        observer.on_publish_terminal.assert_not_called()
        svc._publish_repo.update_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_result_skipped_when_no_change_id(self):
        observer = MagicMock()
        observer.on_batch_result = AsyncMock()
        svc = _make_service(cf_observer=observer)
        svc._publish_repo.get_by_id.return_value = _publish_record(extra_config={})
        batch = SimpleNamespace(id=5, batch_index=1, gmt_complete=None)

        await svc._cf_report_batch_result(
            tenant=TENANT,
            env=ENV,
            publish_id=PUBLISH_ID,
            batch=batch,
            batch_status=BatchStatus.COMPLETED.value,
            counts={},
        )
        observer.on_batch_result.assert_not_called()

    def test_pre_check_skipped_when_no_change_id(self):
        observer = MagicMock()
        observer.on_pre_check = AsyncMock()
        svc = _make_service(cf_observer=observer)
        pub_rec = _publish_record(extra_config={})
        batch = SimpleNamespace(id=5, batch_index=1)

        svc._cf_fire_pre_check(
            tenant=TENANT,
            env=ENV,
            publish_id=PUBLISH_ID,
            publish_record=pub_rec,
            batch=batch,
        )
        observer.on_pre_check.assert_not_called()


# ---------------------------------------------------------------------------
# 5. _cf_report_terminal: outcome mapping + terminal_reported write
# ---------------------------------------------------------------------------
class TestCfReportTerminal:
    @pytest.mark.parametrize(
        "status,expected_outcome",
        [
            (PublishStatus.SUCCESS.value, "success"),
            (PublishStatus.FAILED.value, "failed"),
            (PublishStatus.REJECTED.value, "cancelled"),
            (PublishStatus.REVOKED.value, "cancelled"),
        ],
    )
    @pytest.mark.asyncio
    async def test_outcome_mapping_and_terminal_reported_write(
        self, status, expected_outcome
    ):
        observer = MagicMock()
        observer.on_publish_terminal = AsyncMock()
        svc = _make_service(cf_observer=observer)
        pub_rec = _publish_record(
            extra_config={
                "cf": {"change_id": "extp-t", "terminal_reported": False}
            }
        )

        await svc._cf_report_terminal(
            tenant=TENANT,
            env=ENV,
            publish_id=PUBLISH_ID,
            status=status,
            reason="some reason",
            operator=OPERATOR,
            publish_record=pub_rec,
        )

        observer.on_publish_terminal.assert_awaited_once()
        ctx = observer.on_publish_terminal.call_args.args[0]
        assert ctx.outcome == expected_outcome
        assert ctx.change_id == "extp-t"
        assert ctx.reason == "some reason"
        assert ctx.publish_id == PUBLISH_ID

        # terminal_reported=True written back
        update_call = svc._publish_repo.update_publish.call_args
        written_ec = update_call.kwargs["extra_config"]
        assert written_ec["cf"]["terminal_reported"] is True
        assert update_call.kwargs["modifier"] == OPERATOR

    @pytest.mark.asyncio
    async def test_fetches_publish_record_when_not_provided(self):
        """When publish_record is None the helper fetches from publish_repo."""
        observer = MagicMock()
        observer.on_publish_terminal = AsyncMock()
        svc = _make_service(cf_observer=observer)
        pub_rec = _publish_record(
            extra_config={"cf": {"change_id": "extp-t"}}
        )
        svc._publish_repo.get_by_id.return_value = pub_rec

        await svc._cf_report_terminal(
            tenant=TENANT,
            env=ENV,
            publish_id=PUBLISH_ID,
            status=PublishStatus.SUCCESS.value,
        )
        svc._publish_repo.get_by_id.assert_called_once_with(PUBLISH_ID, TENANT, ENV)
        observer.on_publish_terminal.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_publish_record_missing(self):
        observer = MagicMock()
        observer.on_publish_terminal = AsyncMock()
        svc = _make_service(cf_observer=observer)
        svc._publish_repo.get_by_id.return_value = None

        await svc._cf_report_terminal(
            tenant=TENANT,
            env=ENV,
            publish_id=PUBLISH_ID,
            status=PublishStatus.SUCCESS.value,
        )
        observer.on_publish_terminal.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_already_terminal_reported_via_no_change_id(self):
        """If change_id is absent (register never happened) the hook skips."""
        observer = MagicMock()
        observer.on_publish_terminal = AsyncMock()
        svc = _make_service(cf_observer=observer)
        pub_rec = _publish_record(extra_config={"cf": {"terminal_reported": True}})

        await svc._cf_report_terminal(
            tenant=TENANT,
            env=ENV,
            publish_id=PUBLISH_ID,
            status=PublishStatus.SUCCESS.value,
            publish_record=pub_rec,
        )
        observer.on_publish_terminal.assert_not_called()


# ---------------------------------------------------------------------------
# 6. _cf_report_batch_result: outcome + per-device states
# ---------------------------------------------------------------------------
def _make_batch_record(
    *, device_uuid: str, result_status: str, result_message: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        extra_config={"device_uuid": device_uuid},
        result_status=result_status,
        result_message=result_message,
    )


class TestCfReportBatchResult:
    @pytest.mark.asyncio
    async def test_completed_outcome_succeeded(self):
        observer = MagicMock()
        observer.on_batch_result = AsyncMock()
        svc = _make_service(cf_observer=observer)
        svc._publish_repo.get_by_id.return_value = _publish_record(
            extra_config={"cf": {"change_id": "extp-b"}}
        )
        batch = SimpleNamespace(id=5, batch_index=1, gmt_complete=None)
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
            _make_batch_record(
                device_uuid="dev-1",
                result_status=PublishRecordResult.SUCCESS.value,
            ),
            _make_batch_record(
                device_uuid="dev-2",
                result_status=PublishRecordResult.SUCCESS.value,
            ),
        ]

        await svc._cf_report_batch_result(
            tenant=TENANT,
            env=ENV,
            publish_id=PUBLISH_ID,
            batch=batch,
            batch_status=BatchStatus.COMPLETED.value,
            counts={"SUCCESS": 2, "FAILED": 0},
        )

        observer.on_batch_result.assert_awaited_once()
        ctx = observer.on_batch_result.call_args.args[0]
        assert ctx.outcome == "succeeded"
        assert ctx.device_states == [
            {"device_uuid": "dev-1", "state": "succeeded"},
            {"device_uuid": "dev-2", "state": "succeeded"},
        ]
        assert ctx.targets == ["dev-1", "dev-2"]
        assert ctx.error is None

    @pytest.mark.asyncio
    async def test_failed_outcome_with_failed_device_states(self):
        observer = MagicMock()
        observer.on_batch_result = AsyncMock()
        svc = _make_service(cf_observer=observer)
        svc._publish_repo.get_by_id.return_value = _publish_record(
            extra_config={"cf": {"change_id": "extp-b"}}
        )
        batch = SimpleNamespace(id=5, batch_index=2, gmt_complete=None)
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
            _make_batch_record(
                device_uuid="dev-1",
                result_status=PublishRecordResult.SUCCESS.value,
            ),
            _make_batch_record(
                device_uuid="dev-2",
                result_status=PublishRecordResult.FAILED.value,
                result_message="boom",
            ),
            _make_batch_record(
                device_uuid="dev-3",
                result_status=PublishRecordResult.PROCESSING.value,
            ),
        ]

        await svc._cf_report_batch_result(
            tenant=TENANT,
            env=ENV,
            publish_id=PUBLISH_ID,
            batch=batch,
            batch_status=BatchStatus.FAILED.value,
            counts={"SUCCESS": 1, "FAILED": 1, "PROCESSING": 1},
        )

        ctx = observer.on_batch_result.call_args.args[0]
        assert ctx.outcome == "failed"
        assert ctx.device_states == [
            {"device_uuid": "dev-1", "state": "succeeded"},
            {"device_uuid": "dev-2", "state": "failed"},
            {"device_uuid": "dev-3", "state": "unknown"},
        ]
        # error is the first failed record's message, capped at 500 chars
        assert ctx.error == "boom"


# ---------------------------------------------------------------------------
# _cf_fire_pre_check: dispatch path (change_id present)
# ---------------------------------------------------------------------------
class TestCfFirePreCheck:
    def test_dispatch_creates_task_and_calls_observer(self):
        """When change_id is present _cf_fire_pre_check schedules on_pre_check."""
        observer = MagicMock()
        observer.on_pre_check = AsyncMock()
        svc = _make_service(cf_observer=observer)
        pub_rec = _publish_record(
            extra_config={"cf": {"change_id": "extp-p"}}
        )
        batch = SimpleNamespace(id=5, batch_index=1)
        svc._publish_record_repo.list_by_publish_id_and_batch_id.return_value = [
            _make_batch_record(
                device_uuid="dev-1",
                result_status=PublishRecordResult.PENDING.value,
            ),
        ]

        # Run inside an event loop so create_task has a loop to attach to
        import asyncio

        async def _drive():
            svc._cf_fire_pre_check(
                tenant=TENANT,
                env=ENV,
                publish_id=PUBLISH_ID,
                publish_record=pub_rec,
                batch=batch,
            )
            # Yield to allow the detached task to run
            await asyncio.sleep(0)

        asyncio.run(_drive())

        observer.on_pre_check.assert_awaited_once()
        ctx = observer.on_pre_check.call_args.args[0]
        assert ctx.change_id == "extp-p"
        assert ctx.batch_index == 1
        assert ctx.targets == ["dev-1"]


# ===========================================================================
# 1. Register trigger condition inside create_publish
#
# create_publish is heavy; we patch out _cf_on_register / _cf_build_stages so
# only the *trigger condition* (publish_stage == "online" and publish_type in
# CREATE/UPDATE) is exercised. The negative cases assert _cf_on_register is
# NOT awaited.
# ===========================================================================
def _bot_stub(*, bot_uuid: str = "BOT-1", replica_desired: int = 1) -> MagicMock:
    bot = MagicMock()
    bot.id = BOT_ID
    bot.bot_uuid = bot_uuid
    bot.domain = "default"
    bot.replica_desired = replica_desired
    bot.config = None
    return bot


def _publish_stub(*, extra_config: dict | None) -> MagicMock:
    pub = MagicMock()
    pub.id = PUBLISH_ID
    pub.bot_id = BOT_ID
    pub.publish_type = "CREATE"
    pub.status = PublishStatus.PENDING.value
    pub.extra_config = extra_config
    pub.creator = OPERATOR
    pub.modifier = OPERATOR
    pub.gmt_create = datetime.now()
    pub.gmt_modified = datetime.now()
    pub.name = "bot-name"
    pub.last_publish_id = None
    pub.replica_desired = 1
    return pub


def _wire_create_publish_min(
    svc: DefaultPublishService,
    *,
    bot: MagicMock,
    publish: MagicMock,
    publish_type: PublishType,
) -> None:
    """Wire the mock repos just enough for create_publish to reach Step 6.

    Reuses the pattern from test_publish_service.py::TestPublishCreation.
    """
    svc._bot_service.get_bot = AsyncMock(return_value=bot)

    pub_repo = MagicMock()
    pub_repo.now.return_value = datetime.now()
    pub_repo.list_active_by_bot_ids.return_value = []
    pub_repo.insert_publish.return_value = publish.id
    pub_repo.get_by_id.return_value = publish
    svc._publish_repo = pub_repo

    bot_repo = MagicMock()
    bot_repo.list_by_bot_uuid.return_value = [bot]
    bot_repo.list_by_bot_uuid_including_deleted.return_value = [bot]
    bot_repo.reconcile_unclaimed_pending_bots.return_value = 0
    # UPDATE-only but harmless to wire here
    bot_repo.try_insert_pending_bot.return_value = BOT_ID + 1
    svc._bot_repo = bot_repo

    batch_record = MagicMock()
    batch_record.batch_index = 0
    batch_record.id = 1
    batch_record.batch_capacity = 1
    batch_repo = MagicMock()
    batch_repo.insert_batch.return_value = 1
    batch_repo.get_by_id.return_value = batch_record
    batch_repo.list_by_publish_id.return_value = []
    svc._publish_batch_repo = batch_repo

    # device_repo: CREATE needs a PENDING device; RESTART needs ACTIVE
    device = MagicMock()
    device.id = 10
    if publish_type == PublishType.CREATE:
        device.status = "PENDING"
    else:
        device.status = "ACTIVE"
    device.device_uuid = "dev-1"
    device.provider_device_id = "paas-1"
    dev_repo = MagicMock()
    dev_repo.list_by_bot_id.return_value = [device]
    svc._device_repo = dev_repo

    svc._publish_record_repo = MagicMock()


class TestRegisterTriggerCondition:
    """Point 1: register hook fires only for online + CREATE/UPDATE."""

    @pytest.mark.asyncio
    async def test_online_create_calls_on_register(self):
        svc = _make_service()
        on_register_mock = AsyncMock()
        # Patch the helper so we only assert the trigger condition, not the
        # full register flow (which is covered by TestCfOnRegisterPersist).
        with patch.object(
            svc, "_cf_on_register", on_register_mock
        ) as patched_register, patch.object(
            svc, "_cf_build_stages", MagicMock(return_value=[])
        ) as _stages:
            pub = _publish_stub(extra_config={"publish_stage": "online"})
            _wire_create_publish_min(
                svc, bot=_bot_stub(), publish=pub, publish_type=PublishType.CREATE
            )

            await svc.create_publish(
                tenant=TENANT,
                bot_id=BOT_ID,
                publish_type=PublishType.CREATE,
                operator=OPERATOR,
                request_id="req-trigger-create",
            )

            patched_register.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_online_update_calls_on_register(self):
        svc = _make_service()
        on_register_mock = AsyncMock()
        with patch.object(
            svc, "_cf_on_register", on_register_mock
        ) as patched_register, patch.object(
            svc, "_cf_build_stages", MagicMock(return_value=[])
        ):
            bot = _bot_stub(replica_desired=1)
            # UPDATE needs a config with deploy_config for the clone step.
            from secbaas.community.api.bot_manage import BotConfig
            from secbaas.community.api.device_manage import DeployConfig

            bot.config = BotConfig(
                entity_id="e-1",
                deploy_config=DeployConfig(docker_image="img:1", envs={}),
            )
            pub = _publish_stub(extra_config={"publish_stage": "online"})
            pub.publish_type = "UPDATE"
            _wire_create_publish_min(
                svc, bot=bot, publish=pub, publish_type=PublishType.UPDATE
            )

            await svc.create_publish(
                tenant=TENANT,
                bot_id=BOT_ID,
                publish_type=PublishType.UPDATE,
                operator=OPERATOR,
                request_id="req-trigger-update",
            )

            patched_register.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_online_restart_does_not_call_on_register(self):
        svc = _make_service()
        on_register_mock = AsyncMock()
        with patch.object(
            svc, "_cf_on_register", on_register_mock
        ) as patched_register, patch.object(
            svc, "_cf_build_stages", MagicMock(return_value=[])
        ):
            pub = _publish_stub(extra_config={"publish_stage": "online"})
            _wire_create_publish_min(
                svc, bot=_bot_stub(), publish=pub, publish_type=PublishType.RESTART
            )

            await svc.create_publish(
                tenant=TENANT,
                bot_id=BOT_ID,
                publish_type=PublishType.RESTART,
                operator=OPERATOR,
                request_id="req-trigger-restart",
            )

            patched_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_create_does_not_call_on_register(self):
        svc = _make_service()
        on_register_mock = AsyncMock()
        with patch.object(
            svc, "_cf_on_register", on_register_mock
        ) as patched_register, patch.object(
            svc, "_cf_build_stages", MagicMock(return_value=[])
        ):
            pub = _publish_stub(extra_config={"publish_stage": "verify"})
            _wire_create_publish_min(
                svc, bot=_bot_stub(), publish=pub, publish_type=PublishType.CREATE
            )

            await svc.create_publish(
                tenant=TENANT,
                bot_id=BOT_ID,
                publish_type=PublishType.CREATE,
                operator=OPERATOR,
                request_id="req-trigger-verify",
            )

            patched_register.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_publish_stage_create_does_not_call_on_register(self):
        """publish_stage absent (None) → register hook does not fire."""
        svc = _make_service()
        on_register_mock = AsyncMock()
        with patch.object(
            svc, "_cf_on_register", on_register_mock
        ) as patched_register, patch.object(
            svc, "_cf_build_stages", MagicMock(return_value=[])
        ):
            # extra_config missing publish_stage key entirely
            pub = _publish_stub(extra_config={"replica_desired": 1})
            _wire_create_publish_min(
                svc, bot=_bot_stub(), publish=pub, publish_type=PublishType.CREATE
            )

            await svc.create_publish(
                tenant=TENANT,
                bot_id=BOT_ID,
                publish_type=PublishType.CREATE,
                operator=OPERATOR,
                request_id="req-trigger-none",
            )

            patched_register.assert_not_called()
