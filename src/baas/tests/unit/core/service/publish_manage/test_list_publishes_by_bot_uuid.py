"""Unit tests for PublishService.list_publishes_by_bot_uuid.

Exercises the union-across-bot-records + newest-first ordering + unknown-bot
empty behaviour in isolation, with fake repositories (no DB), so the
adopt-by-query differencing contract is pinned without infra.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from secbaas.community.core.service.publish_manage._publish_service import (
    DefaultPublishService,
)


class _FakeBotRepo:
    def __init__(self, by_uuid):
        self._by_uuid = by_uuid

    def list_by_bot_uuid_including_deleted(self, bot_uuid, tenant, env):
        # The endpoint deliberately includes soft-deleted bots so destroy
        # workflows stay adoptable; the fake returns whatever is registered.
        return self._by_uuid.get(bot_uuid, [])


class _FakePublishRepo:
    def __init__(self, by_bot_id):
        self._by_bot_id = by_bot_id

    def list_by_bot_id(self, bot_id, tenant, env):
        return self._by_bot_id.get(bot_id, [])


def _bot(bot_id):
    return SimpleNamespace(id=bot_id)


def _pub(pub_id, bot_id, *, publish_type="UPDATE", status="ACTIVE"):
    return SimpleNamespace(
        id=pub_id,
        bot_id=bot_id,
        publish_type=publish_type,
        status=status,
        gmt_create=datetime(2026, 7, 15),
    )


def _service(bot_by_uuid, pub_by_bot):
    svc = object.__new__(DefaultPublishService)
    svc._bot_repo = _FakeBotRepo(bot_by_uuid)
    svc._publish_repo = _FakePublishRepo(pub_by_bot)
    return svc


@pytest.mark.asyncio
async def test_unknown_bot_returns_empty():
    svc = _service({}, {})
    assert await svc.list_publishes_by_bot_uuid("t", "nope") == []


@pytest.mark.asyncio
async def test_unions_across_bot_records_newest_first():
    # One bot_uuid maps to two bot records (distinct lifecycle statuses),
    # each with its own publishes.
    svc = _service(
        bot_by_uuid={"BOT-1": [_bot(10), _bot(11)]},
        pub_by_bot={
            10: [_pub(100, 10, publish_type="CREATE", status="SUCCESS")],
            11: [_pub(300, 11, status="ACTIVE"), _pub(200, 11, status="FAILED")],
        },
    )
    out = await svc.list_publishes_by_bot_uuid("t", "BOT-1")
    assert [s.id for s in out] == [300, 200, 100]  # newest workflow id first
    assert out[-1].publish_type == "CREATE"
    assert {s.status for s in out} == {"ACTIVE", "FAILED", "SUCCESS"}


@pytest.mark.asyncio
async def test_includes_soft_deleted_bot_destroy_workflow():
    # A destroyed bot is soft-deleted; its DESTROY workflow must still list so a
    # crash-resumed destroy op can adopt it instead of re-issuing.
    svc = _service(
        bot_by_uuid={"BOT-D": [_bot(20)]},  # fake includes-deleted returns it
        pub_by_bot={20: [_pub(400, 20, publish_type="DESTROY", status="SUCCESS")]},
    )
    out = await svc.list_publishes_by_bot_uuid("t", "BOT-D")
    assert [s.id for s in out] == [400]
    assert out[0].publish_type == "DESTROY"
