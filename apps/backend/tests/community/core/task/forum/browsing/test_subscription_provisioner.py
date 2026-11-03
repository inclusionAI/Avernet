"""Unit tests for asynchronous BBS periodic-task provisioning."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agentclaw.community.core.task.forum.browsing.subscription_provisioner import (
    BbsBrowseSubscriptionProvisioner,
)
from agentclaw.community.core.task.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    BROWSE_MODE_OPENCLAW,
    BrowseSubscriptionRecord,
    BrowseSubscriptionUpsertResult,
)

_NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


def _record(*, mode: str = BROWSE_MODE_OPENCLAW, note: str | None = None):
    return BrowseSubscriptionRecord(
        bot_id="bot-a",
        owner_user_id="111111",
        mode=mode,
        note=note,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _FakeCronManager:
    def __init__(self, *, fail: bool = False, block: asyncio.Event | None = None):
        self.fail = fail
        self.block = block
        self.ensures: list[dict[str, str]] = []
        self.removes: list[dict[str, str]] = []

    async def ensure_cron(self, *, bot_id: str, owner_user_id: str):
        self.ensures.append({"bot_id": bot_id, "owner_user_id": owner_user_id})
        if self.block is not None:
            await self.block.wait()
        if self.fail:
            raise RuntimeError("cron relay unavailable")
        return {"success": True}

    async def remove_cron(self, *, bot_id: str, owner_user_id: str):
        self.removes.append({"bot_id": bot_id, "owner_user_id": owner_user_id})
        if self.block is not None:
            await self.block.wait()
        if self.fail:
            raise RuntimeError("cron relay unavailable")
        return {"success": True}


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def push_install_bbs_skills(self, *, bot_id: str, owner_user_id: str):
        self.calls.append(bot_id)
        return {"status": "submitted"}


class _FakeScheduler:
    def __init__(self) -> None:
        self.unregistered: list[str] = []

    def unregister_bot(self, bot_id: str) -> None:
        self.unregistered.append(bot_id)


class _FakeService:
    def __init__(self, initial: BrowseSubscriptionRecord | None) -> None:
        self.initial = initial
        self.upserts: list[dict[str, str | None]] = []

    def get_subscription(self, *, bot_id: str):
        return self.initial

    def upsert_subscription(self, **kwargs):
        self.upserts.append(kwargs)
        self.initial = _record(mode=BROWSE_MODE_OPENCLAW, note=kwargs["note"])
        return BrowseSubscriptionUpsertResult(
            subscription=self.initial,
            created=self.initial.mode == BROWSE_MODE_OPENCLAW,
        )


def _provisioner(service, *, browse_fail=False, digest_fail=False, block=None):
    return BbsBrowseSubscriptionProvisioner(
        service=service,
        browse_cron=_FakeCronManager(fail=browse_fail),
        digest_cron=_FakeCronManager(fail=digest_fail, block=block),
        runner=_FakeRunner(),
        scheduler=_FakeScheduler(),
    )


async def _drain_background() -> None:
    # yield twice: once to start the created task, once for each immediate await
    # step inside the provisioning coroutine.
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_new_subscription_installs_browse_loop_and_retires_digest_loop():
    service = _FakeService(initial=None)
    provisioner = _provisioner(service)

    result = provisioner.join_or_refresh(
        bot_id="bot-a", owner_user_id="111111", note="new"
    )

    assert result.created is True
    assert result.persisted is False
    assert result.subscription.mode == BROWSE_MODE_OPENCLAW
    assert result.subscription.note == "new"
    assert service.upserts == []

    await _drain_background()

    assert service.upserts == [
        {
            "bot_id": "bot-a",
            "owner_user_id": "111111",
            "mode": BROWSE_MODE_OPENCLAW,
            "note": "new",
        }
    ]
    assert len(provisioner._browse_cron.ensures) == 1  # type: ignore[attr-defined]
    assert provisioner._digest_cron.ensures == []  # type: ignore[attr-defined]
    assert len(provisioner._digest_cron.removes) == 1  # type: ignore[attr-defined]
    assert provisioner._runner.calls == ["bot-a"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_digest_cron_cleanup_failure_leaves_no_subscription_row():
    service = _FakeService(initial=None)
    provisioner = _provisioner(service, digest_fail=True)

    result = provisioner.join_or_refresh(
        bot_id="bot-a", owner_user_id="111111", note="will fail"
    )
    assert result.persisted is False

    await _drain_background()

    assert service.upserts == []
    assert service.initial is None
    # The failed attempt is complete and can be retried with a new background task.
    assert provisioner._pending == {}  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_legacy_framework_subscription_is_switched_after_async_assembly():
    service = _FakeService(initial=_record(mode=BROWSE_MODE_FRAMEWORK))
    provisioner = _provisioner(service)

    result = provisioner.join_or_refresh(
        bot_id="bot-a", owner_user_id="111111", note="switch"
    )
    assert result.persisted is False
    assert result.subscription.mode == BROWSE_MODE_OPENCLAW
    assert service.upserts == []

    await _drain_background()

    assert service.upserts[0]["mode"] == BROWSE_MODE_OPENCLAW
    assert len(provisioner._browse_cron.ensures) == 1  # type: ignore[attr-defined]
    assert provisioner._digest_cron.ensures == []  # type: ignore[attr-defined]
    assert len(provisioner._digest_cron.removes) == 1  # type: ignore[attr-defined]
    assert provisioner._scheduler.unregistered == ["bot-a"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_existing_openclaw_subscription_updates_note_and_repairs_async():
    service = _FakeService(initial=_record(note="old"))
    provisioner = _provisioner(service)

    result = provisioner.join_or_refresh(
        bot_id="bot-a", owner_user_id="111111", note="updated"
    )

    assert result.created is False
    assert result.persisted is True
    assert service.upserts == [
        {
            "bot_id": "bot-a",
            "owner_user_id": "111111",
            "mode": BROWSE_MODE_OPENCLAW,
            "note": "updated",
        }
    ]

    await _drain_background()

    assert len(provisioner._browse_cron.ensures) == 1  # type: ignore[attr-defined]
    assert provisioner._digest_cron.ensures == []  # type: ignore[attr-defined]
    assert len(provisioner._digest_cron.removes) == 1  # type: ignore[attr-defined]
    assert provisioner._runner.calls == ["bot-a"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cancelling_pending_subscription_prevents_late_persistence():
    service = _FakeService(initial=None)
    block = asyncio.Event()
    provisioner = _provisioner(service, block=block)

    provisioner.join_or_refresh(
        bot_id="bot-a", owner_user_id="111111", note="cancel me"
    )
    await asyncio.sleep(0)  # background enters the digest manager's wait()
    await provisioner.cancel_pending(bot_id="bot-a")
    block.set()

    await _drain_background()
    assert service.upserts == []
    assert provisioner._pending == {}  # type: ignore[attr-defined]
