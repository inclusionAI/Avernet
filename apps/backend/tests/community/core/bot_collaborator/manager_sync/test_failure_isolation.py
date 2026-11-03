"""Manager sync remains an isolated side channel to business writes."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import Mock

import httpx
import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher,
    DispatcherPolicy,
)
from agentclaw.community.core.bot_collaborator.manager_sync.service import (
    BotManagerSyncService,
)
from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRecord,
    CollaboratorRole,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.kernel.bot_manager_sync import (
    CollaboratorFact,
    SubmissionResult,
    SyncFacts,
    SyncKey,
    SyncOutcome,
    SyncReason,
)
from agentclaw.community.plugins.community.bcs_managers import HttpBcsManagersPlugin
from agentclaw.community.plugins.local.bcs_managers import RecordingBcsManagersPlugin
from tests.community.bot_address_fakes import support_legacy_locator

OWNER = "owner-1"
MEMBER = "member-1"
BOT_PK = 10


def sync_facts(key: SyncKey) -> SyncFacts:
    return SyncFacts(
        key,
        f"bot-{key.bot_pk}",
        f"entity-{key.bot_pk}",
        OWNER,
        (CollaboratorFact(MEMBER, "admin", True),),
    )


def build_dispatcher(bcs=None, *, policy=None):
    reader = Mock()
    reader.read_facts.side_effect = sync_facts
    bcs = bcs or RecordingBcsManagersPlugin()
    service = BotManagerSyncService(reader, bcs)
    return BotManagerSyncDispatcher(service, reader, policy=policy, env="dev"), bcs


def collaborator_record(
    *, bot_pk: int = BOT_PK, member: str = MEMBER
) -> CollaboratorRecord:
    now = datetime(2026, 10, 27, 10, 0, 0)
    return CollaboratorRecord(
        id=7,
        bot_pk=bot_pk,
        bot_id="bot-1",
        owner_id=OWNER,
        user_id=member,
        role=CollaboratorRole.MEMBER,
        operator_id=OWNER,
        env="dev",
        gmt_create=now,
        gmt_modified=now,
    )


def build_business(
    manager_sync, repository: Mock, *, bot_pk: int = BOT_PK, member: str = MEMBER
) -> CollaboratorService:
    repository.get_by_bot_and_user.return_value = None
    repository.get_user_role.return_value = None
    repository.list_by_bot.return_value = []
    repository.insert.return_value = collaborator_record(bot_pk=bot_pk, member=member)
    bot_repo = support_legacy_locator(Mock())
    bot_repo.get_by_id_and_owner.return_value = {
        "id": bot_pk,
        "bot_id": "bot-1",
        "owner_id": OWNER,
        "bot_type": "service",
        "space_id": None,
    }
    return CollaboratorService(
        collaborator_repo=repository,
        bot_repo=bot_repo,
        passport_plugin=Mock(),
        credentials_admins_writer=Mock(),
        space_access_service=Mock(),
        manager_sync=manager_sync,
    )


@pytest.mark.asyncio
async def test_business_commit_returns_while_real_http_delivery_is_hung():
    entered = asyncio.Event()
    release = asyncio.Event()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(
            200,
            json={
                "code": 20000,
                "request_id": "request-1",
                "data": {"added": 1, "removed": 0, "total": 1},
            },
        )

    client = httpx.AsyncClient(
        base_url="http://bcs.test", transport=httpx.MockTransport(handler)
    )
    plugin = HttpBcsManagersPlugin(
        base_url="http://bcs.test",
        mint_principal=lambda tenant: f"principal-for-{tenant}",
        client=client,
    )
    dispatcher, _ = build_dispatcher(plugin)
    await dispatcher.attach()
    repository = Mock()
    business = build_business(dispatcher, repository)
    try:
        record = await asyncio.to_thread(
            business.add_collaborator,
            "bot-1",
            OWNER,
            MEMBER,
            OWNER,
            role="member",
            env="dev",
        )
        assert record.user_id == MEMBER
        repository.insert.assert_called_once()
        await entered.wait()
        assert len(requests) == 1
        assert dispatcher.stats.entries == 1

        release.set()
        assert await dispatcher.drain(5)
        assert len(requests) == 1
    finally:
        release.set()
        await dispatcher.stop()
        await client.aclose()


def test_notifier_failure_does_not_rollback_successful_business_write():
    notifier = Mock()
    notifier.request_sync.side_effect = RuntimeError("side channel unavailable")
    repository = Mock()
    business = build_business(notifier, repository)

    record = business.add_collaborator(
        "bot-1", OWNER, MEMBER, OWNER, role="member", env="dev"
    )
    assert record.user_id == MEMBER
    repository.insert.assert_called_once()
    notifier.request_sync.assert_called_once()


@pytest.mark.asyncio
async def test_database_write_failure_propagates_and_never_notifies():
    dispatcher, bcs = build_dispatcher()
    await dispatcher.attach()
    repository = Mock()
    repository.insert.side_effect = RuntimeError("db write failed")
    business = build_business(dispatcher, repository)
    try:
        with pytest.raises(RuntimeError, match="db write failed"):
            business.add_collaborator(
                "bot-1", OWNER, MEMBER, OWNER, role="member", env="dev"
            )
        assert dispatcher.stats.entries == 0
        assert bcs.calls == []
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_worker_exception_is_isolated_without_logging_exception_text(caplog):
    class CrashOnce:
        def __init__(self) -> None:
            self.calls = 0

        async def sync_once(self, key):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("secret transport payload")
            return SyncOutcome.CONFIRMED

    service = CrashOnce()
    dispatcher = BotManagerSyncDispatcher(service, Mock(), env="dev")
    await dispatcher.attach()
    try:
        assert (
            dispatcher.request_sync(
                bot_pk=1,
                env="dev",
                avernet_tenant="a",
                reason=SyncReason.REPAIR,
            )
            is SubmissionResult.QUEUED
        )
        assert await dispatcher.drain(5)
        assert (
            dispatcher.request_sync(
                bot_pk=1,
                env="dev",
                avernet_tenant="a",
                reason=SyncReason.REPAIR,
            )
            is SubmissionResult.QUEUED
        )
        assert await dispatcher.drain(5)
        assert service.calls == 2
        messages = "\n".join(record.message for record in caplog.records)
        assert "round_failed" in messages
        assert "secret transport payload" not in messages
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_full_queue_rejection_never_blocks_business_result(monkeypatch):
    dispatcher, bcs = build_dispatcher(
        policy=DispatcherPolicy(max_keys=1, worker_count=1)
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    original = bcs.replace_managers

    async def blocked(snapshot, *, timeout_seconds):
        entered.set()
        await release.wait()
        return await original(snapshot, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(bcs, "replace_managers", blocked)
    await dispatcher.attach()
    first_repo = Mock()
    second_repo = Mock()
    first = build_business(dispatcher, first_repo, bot_pk=10, member=MEMBER)
    second = build_business(dispatcher, second_repo, bot_pk=11, member="member-2")
    try:
        first_record = first.add_collaborator(
            "bot-1", OWNER, MEMBER, OWNER, role="member", env="dev"
        )
        await entered.wait()
        second_record = second.add_collaborator(
            "bot-1", OWNER, "member-2", OWNER, role="member", env="dev"
        )
        assert first_record.user_id == MEMBER
        assert second_record.user_id == "member-2"
        first_repo.insert.assert_called_once()
        second_repo.insert.assert_called_once()
        # The second Bot was rejected from the full queue, but its business
        # write still returned and no caller waited for BCS.
        assert dispatcher.stats.entries == 1
        release.set()
        assert await dispatcher.drain(5)
        assert len(bcs.calls) == 1
    finally:
        release.set()
        await dispatcher.stop()
