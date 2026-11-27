"""Lifecycle-hook tests for the observe-only ChangeFlow side channel."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from secbaas.community.core.service.publish_manage import DefaultPublishService


class RecordingObserver:
    def __init__(self, change_id: str | None = "chg-1") -> None:
        self.change_id = change_id
        self.registers = []
        self.pre_checks = []
        self.batch_results = []
        self.terminals = []

    async def on_register(self, ctx):
        self.registers.append(ctx)
        return self.change_id

    async def on_pre_check(self, ctx):
        self.pre_checks.append(ctx)

    async def on_batch_result(self, ctx):
        self.batch_results.append(ctx)

    async def on_publish_terminal(self, ctx):
        self.terminals.append(ctx)


def _service(observer, *, rec_repo=None):
    from unittest.mock import MagicMock

    return DefaultPublishService(
        bot_repo=MagicMock(),
        device_repo=MagicMock(),
        rel_repo=MagicMock(),
        session_repo=MagicMock(),
        publish_repo=MagicMock(),
        batch_repo=MagicMock(),
        publish_record_repo=rec_repo or MagicMock(),
        template_service=MagicMock(),
        bot_service=MagicMock(),
        device_service=MagicMock(),
        cf_observer=observer,
    )


def _publish(**overrides):
    values = {
        "id": 42,
        "tenant": "t1",
        "publish_type": "CREATE",
        "config_version": None,
        "creator": "alice",
        "extra_config": {"publish_stage": "online"},
        "bot_id": 7,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _batch(**overrides):
    values = {
        "id": 3,
        "publish_id": 42,
        "stage": "PROD_FIRST_BATCH",
        "batch_index": 0,
        "error_message": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _rec_repo():
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.list_by_publish_id_and_batch_id.return_value = [
        SimpleNamespace(device_uuid="dev-1", result_status="SUCCESS")
    ]
    return repo


@pytest.mark.asyncio
async def test_register_only_for_online_publish_and_persists_change_id():
    observer = RecordingObserver()
    service = _service(observer, rec_repo=_rec_repo())
    publish = _publish()
    bot = SimpleNamespace(id=7, name="svc-bot", replica_desired=1, extra_config={})

    await service._cf_register(publish, bot, [_batch()])

    assert len(observer.registers) == 1
    service._publish_repo.update_publish.assert_called_once()
    assert (
        service._publish_repo.update_publish.call_args.kwargs["extra_config"][
            "cf_change_id"
        ]
        == "chg-1"
    )


@pytest.mark.asyncio
async def test_register_skips_verify_stage():
    observer = RecordingObserver()
    service = _service(observer, rec_repo=_rec_repo())
    publish = _publish(extra_config={"publish_stage": "verify"})
    bot = SimpleNamespace(id=7, name="svc-bot", replica_desired=1, extra_config={})

    await service._cf_register(publish, bot, [_batch()])

    assert observer.registers == []
    service._publish_repo.update_publish.assert_not_called()


@pytest.mark.asyncio
async def test_pre_check_and_batch_result_only_cover_prod_stages():
    observer = RecordingObserver()
    service = _service(observer, rec_repo=_rec_repo())
    publish = _publish(
        extra_config={"publish_stage": "online", "cf_change_id": "chg-1"}
    )

    service._cf_pre_check(publish, _batch(stage="PREPUB"))
    service._cf_pre_check(publish, _batch(stage="PROD_FIRST_BATCH"))
    service._cf_batch_result(
        publish, _batch(stage="PROD_FIRST_BATCH", status="COMPLETED")
    )
    await asyncio.gather(*service._cf_tasks)

    assert len(observer.pre_checks) == 1
    assert observer.pre_checks[0].stage == "PROD_FIRST_BATCH"
    assert len(observer.batch_results) == 1
    assert observer.batch_results[0].outcome == "succeeded"
    assert observer.batch_results[0].targets[0].target == "dev-1"


@pytest.mark.asyncio
async def test_terminal_notifies_once_per_call_with_change_id():
    observer = RecordingObserver()
    service = _service(observer)
    publish = _publish(
        extra_config={"publish_stage": "online", "cf_change_id": "chg-1"}
    )

    service._cf_terminal(publish, "SUCCESS")
    await asyncio.gather(*service._cf_tasks)

    assert len(observer.terminals) == 1
    assert observer.terminals[0].outcome == "success"
    assert observer.terminals[0].change_id == "chg-1"


@pytest.mark.asyncio
async def test_terminal_skipped_without_change_id():
    observer = RecordingObserver()
    service = _service(observer)
    publish = _publish(extra_config={"publish_stage": "online"})

    service._cf_terminal(publish, "FAILED")
    await asyncio.gather(*service._cf_tasks)

    assert observer.terminals == []
