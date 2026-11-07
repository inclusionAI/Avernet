"""Manager-sync triggers for ordinary collaborator changes (Task 7).

Every ordinary mutation path — add / update / remove / leave and the four
public Editors faces — funnels through
``CollaboratorService.on_collaboration_changed``. Task 7 wires the unified
manager-sync side-channel into that one callback, so these tests evolve the
``test_editor_service`` dependency doubles into the real service plus:

- a ``RecordingNotifier`` (a ``BotManagerSyncProtocol`` double that records
  the ``(SyncKey, SyncReason)`` of every admission and can be told to raise),
- a repository whose ``insert`` commits through a session the tests can
  probe, mirroring the production ``CollaboratorRepository`` (the write is
  not durable until the owning session's commit exits).

Safety rules pinned here, one test each:

- Notification submits ONLY after the business commit succeeded — never
  before it, never after a rollback; the commit-exit evidence is the probe
  around the session commit, never ``insert()`` call order.
- A notifier failure never changes the business result — the record is
  still returned, the passport sync still runs.
- One notify per real change; a refused mutation or a failed delete stays
  silent even though the old roster fan-out semantics are untouched.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, Mock

import pytest

from tests.community.bot_address_fakes import support_legacy_locator

from agentclaw.community.core.bot_collaborator.errors import (
    CollaboratorNotFoundError,
    PermissionDeniedError,
)
from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRecord,
    CollaboratorRole,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SubmissionResult,
    SyncKey,
    SyncReason,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

OWNER = "owner-1"
MEMBER = "member-1"
BOT_PK = 10

#: Outside any request scope every test runs on the default tenant.
TENANT = get_current_avernet_tenant()

#: The one admission every real change here is expected to submit.
EXPECTED_KEY = SyncKey(avernet_tenant=TENANT, env="dev", bot_pk=BOT_PK)


class RecordingNotifier:
    """A ``BotManagerSyncProtocol`` double recording ``(SyncKey, SyncReason)``.

    ``request_sync`` is a mock, so tests can keep using the plain mock
    idioms — ``side_effect`` to make the notifier fail,
    ``assert_called_once`` for admission counts — while ``calls`` records
    the exact ``(SyncKey, SyncReason)`` pairs the business code submitted.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[SyncKey, SyncReason]] = []
        spy = self

        def _record_request(
            *,
            bot_pk: int,
            env: str,
            avernet_tenant: str,
            reason: SyncReason,
        ) -> SubmissionResult:
            spy.calls.append(
                (
                    SyncKey(
                        avernet_tenant=avernet_tenant, env=env, bot_pk=bot_pk
                    ),
                    reason,
                )
            )
            return SubmissionResult.QUEUED

        self.request_sync = MagicMock(side_effect=_record_request)
        self.request_space_sync = MagicMock(
            return_value=SubmissionResult.DISABLED
        )


class RepositorySession:
    """The fake session behind the fixture repository's write path.

    Shaped like the production repository: ``insert`` runs inside the
    owning session scope and the change only becomes durable when that
    session's ``commit`` exits — so the commit-exit test can probe the
    exact point the business commit completes instead of pretending the
    ``insert()`` call order is commit evidence.
    """

    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def _bot() -> dict:
    return {
        "id": BOT_PK,
        "bot_id": "bot-1",
        "owner_id": OWNER,
        "bot_type": "service",
        "space_id": None,
    }


def _record(**overrides) -> CollaboratorRecord:
    now = datetime(2026, 8, 19, 10, 0, 0)
    values = {
        "id": 7,
        "bot_pk": BOT_PK,
        "bot_id": "bot-1",
        "owner_id": OWNER,
        "user_id": MEMBER,
        "role": CollaboratorRole.MEMBER,
        "operator_id": OWNER,
        "env": "dev",
        "gmt_create": now,
        "gmt_modified": now,
        **overrides,
    }
    return CollaboratorRecord(**values)


@pytest.fixture
def repository_session() -> RepositorySession:
    return RepositorySession()


@pytest.fixture
def collaborator_case(repository_session):
    """The ``test_editor_service.dependencies`` doubles, evolved for Task 7.

    The service is real (``on_collaboration_changed`` is never mocked
    away), the notifier is a RecordingNotifier, and the repository's
    ``insert`` commits through a session the tests can wrap. Bot PK=10,
    owner=owner-1; returns ``(service, repository, notifier, passport)``.
    """
    repository = Mock()
    repository.get_by_bot_and_user.return_value = None
    repository.get_user_role.return_value = None
    repository.list_by_bot.return_value = []

    def _insert(payload):
        # Mirror the production CollaboratorRepository: the write commits
        # inside the write, at the session-scope exit — not after the
        # callback returns.
        repository_session.commit()
        return _record(
            user_id=payload["user_id"],
            role=payload.get("role") or CollaboratorRole.ADMIN.value,
            operator_id=payload.get("operator_id"),
        )

    repository.insert.side_effect = _insert

    bot_repo = support_legacy_locator(Mock())
    bot_repo.get_by_id_and_owner.return_value = _bot()
    passport = Mock()
    notifier = RecordingNotifier()
    service = CollaboratorService(
        collaborator_repo=repository,
        bot_repo=bot_repo,
        passport_plugin=passport,
        credentials_admins_writer=Mock(),
        space_access_service=Mock(),
        manager_sync=notifier,
    )
    return service, repository, notifier, passport


def test_member_add_commits_and_requests_sync_even_if_notifier_fails(collaborator_case):
    # collaborator_case 在本文件按现有 test_editor_service.dependencies 构造，
    # 提供 service/repository/notifier/passport；Bot PK=10，owner=owner-1。
    service, repository, notifier, passport = collaborator_case
    notifier.request_sync.side_effect = RuntimeError("notification unavailable")
    record = service.add_collaborator("bot-1", "owner-1", "member-1", "owner-1",
                                      role="member", env="dev")
    assert record.user_id == "member-1"
    repository.insert.assert_called_once()
    notifier.request_sync.assert_called_once()
    assert passport.update_passport.call_args.kwargs["admins"] == []


def test_no_notification_before_commit_or_after_rollback(
    collaborator_case, repository_session
):
    """The only commit evidence is the session commit's own exit point.

    The probe wraps the repository session's ``commit``: while the commit
    runs (and right after it exits, before ``insert`` returns) the
    notifier must be silent; exactly one admission may exist after the
    whole change completes. A commit that raises must propagate to the
    caller and leave the notifier untouched — a rolled-back write never
    reaches the side-channel. Counting ``insert()`` call order would not
    be commit evidence and is deliberately not used.
    """
    service, repository, notifier, _ = collaborator_case
    observed = {}

    real_commit = repository_session.commit

    def _probing_commit():
        assert notifier.request_sync.call_count == 0
        real_commit()
        observed["after_commit_exit"] = notifier.request_sync.call_count

    repository_session.commit = _probing_commit

    record = service.add_collaborator(
        "bot-1", OWNER, "member-2", OWNER, role="member", env="dev"
    )

    assert record.user_id == "member-2"
    assert observed["after_commit_exit"] == 0
    assert notifier.request_sync.call_count == 1
    assert notifier.calls == [
        (EXPECTED_KEY, SyncReason.COLLABORATOR_CHANGED)
    ]

    # A commit that raises rolls the write back: the exception propagates
    # out of the service (nothing may catch around the repository write)
    # and no further admission happens.
    repository_session.commit = Mock(side_effect=RuntimeError("commit failed"))
    with pytest.raises(RuntimeError, match="commit failed"):
        service.add_collaborator(
            "bot-1", OWNER, "member-3", OWNER, role="member", env="dev"
        )
    assert notifier.request_sync.call_count == 1


def _drive_real_change(service, repository, op: str):
    """Run one ordinary real change; each branch sets only what it needs."""
    if op in ("add_collaborator", "add_for_others"):
        # /add_for_others 只有 operator 取 owner 的差别，其余调用与 /add
        # 完全一致（既有 endpoint test 覆盖路由层）；两种送法都应通知。
        return service.add_collaborator(
            "bot-1", OWNER, "member-1", OWNER, role="member", env="dev"
        )
    if op == "update_collaborator_user_id":
        repository.get_by_id.return_value = _record()
        repository.update.return_value = _record(user_id="member-2")
        return service.update_collaborator(7, OWNER, user_id="member-2", env="dev")
    if op == "update_collaborator_role":
        repository.get_by_id.return_value = _record()
        repository.update.return_value = _record(role=CollaboratorRole.ADMIN)
        return service.update_collaborator(7, OWNER, role="admin", env="dev")
    if op == "remove_collaborator":
        repository.get_by_id.return_value = _record()
        repository.delete.return_value = True
        return service.remove_collaborator(7, OWNER, env="dev")
    if op == "leave_collaboration":
        repository.get_by_bot_and_user.return_value = _record()
        repository.delete.return_value = True
        return service.leave_collaboration("bot-1", OWNER, MEMBER, env="dev")
    if op == "add_editor":
        return service.add_editor("bot-1", OWNER, "member-1", OWNER, env="dev")
    if op == "update_editor":
        repository.get_by_id.return_value = _record()
        repository.update.return_value = _record(role=CollaboratorRole.ADMIN)
        return service.update_editor("bot-1", OWNER, 7, OWNER, "admin", env="dev")
    if op == "remove_editor":
        repository.get_by_id.return_value = _record()
        repository.delete.return_value = True
        return service.remove_editor("bot-1", OWNER, 7, OWNER, env="dev")
    if op == "leave_editors":
        repository.get_user_role.return_value = CollaboratorRole.MEMBER
        repository.get_by_bot_and_user.return_value = _record()
        repository.delete.return_value = True
        return service.leave_editors("bot-1", OWNER, MEMBER, env="dev")
    raise AssertionError(f"unknown op {op!r}")


@pytest.mark.parametrize(
    "op",
    [
        "add_collaborator",
        "add_for_others",
        "update_collaborator_user_id",
        "update_collaborator_role",
        "remove_collaborator",
        "leave_collaboration",
        "add_editor",
        "update_editor",
        "remove_editor",
        "leave_editors",
    ],
)
def test_each_real_change_submits_exactly_one_sync(collaborator_case, op):
    """每次真实变更恰好一次 BCS 同步 —— key 与 reason 与变更来源一致。"""
    service, repository, notifier, _ = collaborator_case

    _drive_real_change(service, repository, op)

    notifier.request_sync.assert_called_once()
    assert notifier.calls == [(EXPECTED_KEY, SyncReason.COLLABORATOR_CHANGED)]


def test_failed_delete_stays_silent(collaborator_case):
    """remove keeps raising, leave keeps returning False — neither syncs.

    失败的删除不是真实变更：remove 保持抛 CollaboratorNotFoundError、
    leave 保持返回 False（原语义），且都不写成功同步；例行 roster 投影
    （Passport/credentials）也无需为无变更的路径强制远端操作。
    """
    service, repository, notifier, _ = collaborator_case
    repository.get_by_id.return_value = _record()
    repository.delete.return_value = False

    with pytest.raises(CollaboratorNotFoundError):
        service.remove_collaborator(7, OWNER, env="dev")

    repository.get_by_bot_and_user.return_value = _record()
    assert service.leave_collaboration("bot-1", OWNER, MEMBER, env="dev") is False

    notifier.request_sync.assert_not_called()
    assert notifier.calls == []


def test_denied_mutation_stays_silent(collaborator_case):
    """A member below the ADMIN bar mutates nothing and notifies nothing."""
    service, repository, notifier, _ = collaborator_case
    repository.get_user_role.return_value = CollaboratorRole.MEMBER

    with pytest.raises(PermissionDeniedError):
        service.add_editor("bot-1", OWNER, "member-2", MEMBER, env="dev")

    repository.insert.assert_not_called()
    notifier.request_sync.assert_not_called()
    assert notifier.calls == []