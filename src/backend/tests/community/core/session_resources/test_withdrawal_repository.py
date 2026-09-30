"""Durable deletion facts: real transactions and independent DB connections."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy import event

from agentclaw.community.core.session_resources.withdrawal_models import (
    ResourceWithdrawalModel,
)
from agentclaw.community.core.session_resources.types import SessionResourceStatus
from agentclaw.community.core.repository.implementations.platform.session_resource import (
    SessionResourceRepository,
)
from agentclaw.community.core.repository.implementations.platform.resource_withdrawal import (
    ResourceWithdrawalRepository,
)
from agentclaw.community.plugins.community.database import CommunityDatabase
from tests.community.core.session_resources.test_repository import _record

SCOPES = ("personal_bot_chat", "friend_bot_chat", "openapi_session")


def delete(repo, **kwargs):
    return repo.soft_delete(
        "sr_001",
        kwargs.get("owner", "owner-1"),
        "bot-1",
        "session-hash",
        withdrawal_scope_types=SCOPES,
    )


@pytest.mark.parametrize(
    "status", [s for s in SessionResourceStatus if s != SessionResourceStatus.DELETED]
)
@pytest.mark.parametrize("scope", SCOPES)
def test_delete_atomically_records_each_single_chat_state(store, status, scope):
    _, repo, outbox = store
    repo.create(replace(_record(), status=status, scope_type=scope))
    assert delete(repo).status == SessionResourceStatus.DELETED
    fact = outbox.get("tc.resource.withdrawn:sr_001")
    assert fact.res_id == "sr_001"
    assert fact.status == "pending"
    assert fact.attempts == 0
    delete(repo)
    assert outbox.get(fact.event_id) == fact


@pytest.mark.parametrize("scope", ["group_chat", "collaboration_session", "unknown"])
def test_other_scopes_remain_local_only(store, scope):
    _, repo, outbox = store
    repo.create(replace(_record(), scope_type=scope))
    assert delete(repo).status == SessionResourceStatus.DELETED
    assert outbox.get("tc.resource.withdrawn:sr_001") is None


def test_no_unauthorized_missing_or_historical_events(store):
    _, repo, outbox = store
    assert delete(repo) is None
    repo.create(_record())
    assert delete(repo, owner="other") is None
    assert outbox.get("tc.resource.withdrawn:sr_001") is None
    repo.soft_delete("sr_001", "owner-1", "bot-1", "session-hash")
    delete(repo)
    assert outbox.get("tc.resource.withdrawn:sr_001") is None


def test_insert_failure_rolls_back_resource_status(store):
    db, repo, outbox = store
    repo.create(_record())

    def reject(*_):
        raise RuntimeError("simulated outbox write failure")

    event.listen(ResourceWithdrawalModel, "before_insert", reject)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            delete(repo)
    finally:
        event.remove(ResourceWithdrawalModel, "before_insert", reject)
    assert (
        repo.get_by_resource_id("sr_001").status
        == SessionResourceStatus.UPLOAD_URL_ISSUED
    )
    assert outbox.get("tc.resource.withdrawn:sr_001") is None


def test_concurrent_delete_and_restart_keep_one_fact(store):
    db, repo, outbox = store
    repo.create(_record())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: delete(SessionResourceRepository(db)), range(8))
        )
    assert all(r.status == SessionResourceStatus.DELETED for r in results)
    with db.orm_session() as session:
        assert session.query(ResourceWithdrawalModel).count() == 1
    restarted = CommunityDatabase(db._url, create_schema=False)
    try:
        assert (
            ResourceWithdrawalRepository(restarted)
            .get("tc.resource.withdrawn:sr_001")
            .status
            == "pending"
        )
    finally:
        restarted._engine.dispose()


def test_claim_is_exclusive_scoped_and_expired_lease_is_fenced(store):
    from datetime import datetime, timedelta

    db, repo, outbox = store
    repo.create(_record())
    delete(repo)
    assert outbox.claim(tenant="other", lease_seconds=60) is None
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(
            pool.map(
                lambda _: outbox.claim(tenant="tenant-1", lease_seconds=60), range(4)
            )
        )
    (first,) = [r for r in claimed if r is not None]
    assert first.attempts == 1
    with db.transactional_orm_session() as session:
        session.query(ResourceWithdrawalModel).update(
            {"lease_until": datetime.now() - timedelta(days=1)}
        )
    second = outbox.claim(tenant="tenant-1", lease_seconds=60)
    assert second.lease_token != first.lease_token
    assert second.attempts == 2
    assert not outbox.finish(first, status="accepted", delay_seconds=0, error_code="")
    assert outbox.finish(second, status="accepted", delay_seconds=0, error_code="")
    assert outbox.claim(tenant="tenant-1", lease_seconds=60) is None
    delete(repo)
    assert outbox.get(first.event_id).status == "accepted"
    assert not outbox.replay(
        event_id=first.event_id,
        tenant="tenant-1",
        expected_attempts=2,
        actor="test-operator",
        reason="test recovery",
    )


def test_retry_block_replay_and_stats(store):
    _, repo, outbox = store
    repo.create(_record())
    delete(repo)
    first = outbox.claim(tenant="tenant-1", lease_seconds=60)
    assert outbox.finish(
        first, status="pending", delay_seconds=100, error_code="http_503"
    )
    assert outbox.claim(tenant="tenant-1", lease_seconds=60) is None
    assert not outbox.replay(
        event_id=first.event_id,
        tenant="tenant-1",
        expected_attempts=1,
        actor="test-operator",
        reason="test recovery",
    )
    # Retain the original age and total attempts while replay resets only the retry budget.
    with store[0].transactional_orm_session() as session:
        session.query(ResourceWithdrawalModel).update({"status": "blocked"})
    stats = outbox.stats(tenant="tenant-1")
    assert stats["blocked"] == 1
    assert stats["oldest_unaccepted_at"] is not None
    assert not outbox.replay(
        event_id=first.event_id,
        tenant="other",
        expected_attempts=1,
        actor="test-operator",
        reason="test recovery",
    )
    assert not outbox.replay(
        event_id=first.event_id,
        tenant="tenant-1",
        expected_attempts=0,
        actor="test-operator",
        reason="test recovery",
    )
    assert outbox.replay(
        event_id=first.event_id,
        tenant="tenant-1",
        expected_attempts=1,
        actor="test-operator",
        reason="test recovery",
    )
    next_try = outbox.claim(tenant="tenant-1", lease_seconds=60)
    assert next_try.attempts == 2
    assert next_try.retry_count == 1
    assert next_try.created_at == first.created_at


def test_two_identical_content_references_have_independent_events(store):
    _, repo, outbox = store
    repo.create(replace(_record(), client_content_hash="same-hash"))
    repo.create(
        replace(_record(), resource_id="sr_002", client_content_hash="same-hash")
    )
    delete(repo)
    assert repo.get_by_resource_id("sr_002").status != SessionResourceStatus.DELETED
    assert outbox.get("tc.resource.withdrawn:sr_002") is None
