"""Ownership hints follow the real committed handoff, never credential work."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import event

from agentclaw.community.core.bot_management.owner_handoff.contracts import (
    OwnerHandoffPolicy,
)
from agentclaw.community.core.bot_management.ownership_sync.notifications import (
    DisabledBotOwnershipSync,
)
from agentclaw.community.kernel.bot_manager_sync import SubmissionResult, SyncReason
from agentclaw.community.plugin_api.models import BotModel
from tests.community.core.bot_management.test_owner_handoff import begin, drain
from tests.community.core.bot_management.test_owner_handoff import world as world
from tests.community.core.bot_management.test_resource_handoff import persisted  # noqa: F401


def _committing(world, op):
    for _ in range(5):
        assert world.service().step(op) is False
    assert world.repo.get(op)["phase"] == "COMMITTING_OWNER"


def _credential_counts(world):
    assert (
        len(world.passport.issues)
        == len(world.runtime.calls)
        == len(world.passport.retired)
        == 1
    )


def test_notification_observes_committed_success_and_space_in_new_sessions(world):
    source = world.spaces.initialize_personal(user_id="A", create_sc_team=False)[0]
    with world.db.orm_session() as db:
        db.get(BotModel, world.bot["id"]).space_id = source.id
    op = begin(world)
    target = world.spaces_repo.get_personal_space(user_id="B", env="dev")
    observed = []

    def on_submit(**kwargs):
        # Both repository calls open new sessions, outside finish's transaction.
        world.manager_sync.request_sync.assert_called_once()
        row = world.repo.get(op)
        bot = world.bots.get_by_pk(world.bot["id"])
        observed.append((row["phase"], bot["owner_id"], bot["space_id"], kwargs))

    world.ownership_sync.request_ownership_sync.side_effect = on_submit
    drain(world, op)
    assert observed == [
        (
            "SUCCEEDED",
            "B",
            target.id,
            {
                "bot_pk": world.bot["id"],
                "env": "dev",
                "avernet_tenant": "teamclaw",
                "reason": SyncReason.OWNER_HANDOFF,
            },
        )
    ]
    world.manager_sync.request_sync.assert_called_once()
    _credential_counts(world)


def test_begin_wait_and_cancel_do_not_notify(world):
    op = begin(world)
    world.ownership_sync.request_ownership_sync.assert_not_called()
    world.service().cancel(operation_id=op, actor_id="A")
    assert world.service().step(op) is True
    world.ownership_sync.request_ownership_sync.assert_not_called()
    op = world.service().create(
        bot_pk=world.bot["id"], actor_id="A", new_owner_id="B", request_key="wait"
    )["operation_id"]
    world.passport.status = "PENDING"
    for _ in range(5):
        assert world.service().step(op) is False
    assert world.repo.get(op)["phase"] == "WAITING_AUTHORIZATION"
    world.ownership_sync.request_ownership_sync.assert_not_called()
    world.manager_sync.request_sync.assert_not_called()


def test_terminal_query_replay_retry_and_redrive_do_not_resubmit(world):
    op = begin(world)
    drain(world, op)
    assert world.service().get(operation_id=op, actor_id="B")["phase"] == "SUCCEEDED"
    assert begin(world) == op
    assert world.service().retry(operation_id=op, actor_id="B")["phase"] == "SUCCEEDED"
    world.service().reconcile()
    assert world.service().step(op) is True
    world.ownership_sync.request_ownership_sync.assert_called_once()
    world.manager_sync.request_sync.assert_called_once()
    _credential_counts(world)


def test_recovery_completing_emits_without_reentering_credentials(world):
    op = begin(world)
    _committing(world, op)
    service = world.service(OwnerHandoffPolicy(recovery_enabled=True))
    service.reconcile()
    assert service.step(op) is True
    assert world.repo.get(op)["phase"] == "SUCCEEDED"
    world.ownership_sync.request_ownership_sync.assert_called_once()
    _credential_counts(world)


@pytest.mark.parametrize("failure", ["finish", "commit"])
def test_finish_and_commit_failures_remain_errors_without_notifications(
    world, monkeypatch, failure
):
    op = begin(world)
    _committing(world, op)
    original = world.repo._unfreeze

    def fail(*args):
        raise RuntimeError("write failed secret-payload")

    def fail_commit(db, row, bot):
        original(db, row, bot)

        def before_commit(session):
            session.flush()
            assert bot.owner_id == "B"
            raise RuntimeError("commit failed secret-payload")

        event.listen(db, "before_commit", before_commit, once=True)

    if failure == "finish":
        monkeypatch.setattr(world.repo, "finish", fail)
    else:
        monkeypatch.setattr(world.repo, "_unfreeze", fail_commit)
    assert world.service().step(op) is False
    row = world.repo.get(op)
    assert (row["phase"], row["error_code"]) == (
        "COMMITTING_OWNER",
        "HANDOFF_EXTERNAL_ERROR",
    )
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "A"
    world.ownership_sync.request_ownership_sync.assert_not_called()
    world.manager_sync.request_sync.assert_not_called()
    _credential_counts(world)


@pytest.mark.parametrize(
    "outcome", ["throws", "disabled", "rejected", "manager_throws"]
)
def test_notification_outcomes_are_independent_of_business_success(world, outcome):
    op = begin(world)
    _committing(world, op)
    world.passport.update_passport = MagicMock()
    if outcome == "throws":
        world.ownership_sync.request_ownership_sync.side_effect = RuntimeError(
            "secret-payload"
        )
    elif outcome == "disabled":
        world.ownership_sync.request_ownership_sync.side_effect = (
            DisabledBotOwnershipSync().request_ownership_sync
        )
    elif outcome == "rejected":
        world.ownership_sync.request_ownership_sync.return_value = (
            SubmissionResult.REJECTED
        )
    else:
        world.manager_sync.request_sync.side_effect = RuntimeError("secret-payload")
    assert world.service().step(op) is True
    assert world.repo.get(op)["phase"] == "SUCCEEDED"
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "B"
    world.manager_sync.request_sync.assert_called_once()
    world.ownership_sync.request_ownership_sync.assert_called_once()
    world.passport.update_passport.assert_not_called()
    assert world.service().step(op) is True
    world.ownership_sync.request_ownership_sync.assert_called_once()
    _credential_counts(world)
