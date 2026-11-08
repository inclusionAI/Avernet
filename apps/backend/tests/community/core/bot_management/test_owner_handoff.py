"""Real journal/aggregate transactions with an external protocol fixture."""

from types import SimpleNamespace

import pytest

from agentclaw.community.adapters.http.dependencies import RequestContext
from agentclaw.community.adapters.http.skill_center.skills import _get_path_params
from agentclaw.community.core.bot_collaborator.models import (
    BotCollaboratorModel,
    BotCollabLockModel,
)
from agentclaw.community.core.execution_identity.models import (
    BotExecutionIdentityBindingModel,
)
from agentclaw.community.core.bot_management.owner_handoff.contracts import (
    HandoffError,
    OwnerHandoffPolicy,
)
from agentclaw.community.core.bot_management.owner_handoff.models import (
    OwnerHandoffModel,
)
from agentclaw.community.core.bot_management.owner_handoff.guard import (
    assert_bot_mutable,
)
from agentclaw.community.core.bot_management.owner_handoff.service import (
    OwnerHandoffService,
)
from agentclaw.community.core.repository.implementations.bot.owner_handoff import (
    OwnerHandoffRepository,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderModel,
    WorkOrderApproverModel,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from tests.community.core.bot_management.test_resource_handoff import persisted  # noqa: F401


class Passport:
    """Explicit test contract fixture, never real AAC/revocation evidence."""

    def __init__(self):
        self.current = {"credential_id": "old", "execution_workno": "A"}
        self.issues, self.retired = [], []
        self.status, self.timeout_after_issue, self.retire_fail = "ISSUED", False, False
        self.target_owner, self.next_credential_id = "B", "selected"

    def owner_handoff_supported(self, **kwargs):
        return True

    def query_agent_passport(self, *args, **kwargs):
        return dict(self.current)

    def reissue_agent_credentials(self, **kwargs):
        assert (
            kwargs["entity_id"],
            kwargs["owner_workno"],
            kwargs["expected_credential_id"],
        ) == ("A", self.target_owner, self.current["credential_id"])
        self.issues.append(kwargs)
        self.current = {
            "credential_id": self.next_credential_id,
            "agent_id": "dev|A|service",
            "execution_workno": kwargs["execution_workno"],
            "handoff_operation_id": kwargs["handoff_operation_id"],
            "authorization_id": "aac",
        }
        if self.timeout_after_issue:
            raise RuntimeError("do not expose test-token")
        return {**self.current, "token": "test-token"}

    def query_auth_status(self, *args, **kwargs):
        return {
            "status": self.status,
            "token": "test-token" if self.status == "ISSUED" else None,
        }

    def retire_agent_credentials(self, **kwargs):
        if self.retire_fail:
            raise RuntimeError("retire failed test-token")
        assert kwargs["keep_credential_id"] == self.current["credential_id"]
        self.retired.append(kwargs)


class Runtime:
    def __init__(self):
        self.calls, self.fail = [], False

    def hot_update_passport_token_to_device(self, **kwargs):
        if self.fail:
            raise RuntimeError("runtime rejected test-token")
        self.calls.append(kwargs)
        return {
            "bindings": [{"binding_id": 1, "type": "draft", "device_count": 1}],
            "token_prefix": "test-token",
        }


class Queue:
    def __init__(self):
        self.calls, self.fail = [], False

    def enqueue(self, *args, **kwargs):
        if self.fail:
            raise RuntimeError("storage unavailable")
        self.calls.append((args, kwargs))


class Staff:
    def get_user_by_work_no(self, *, work_no):
        return SimpleNamespace(
            work_no=work_no, display_name="new owner", full_name=None, username=None
        )


@pytest.fixture
def world(persisted):
    db, bots, bot = persisted
    repo, passport, runtime, queue = (
        OwnerHandoffRepository(db),
        Passport(),
        Runtime(),
        Queue(),
    )

    def service(policy=OwnerHandoffPolicy(enabled=True, recovery_enabled=True)):
        return OwnerHandoffService(
            repo, passport, runtime, Staff(), bots, queue, policy
        )

    return SimpleNamespace(
        db=db,
        bots=bots,
        bot=bot,
        repo=repo,
        passport=passport,
        runtime=runtime,
        queue=queue,
        service=service,
    )


def begin(world):
    return world.service().create(
        bot_pk=world.bot["id"], actor_id="A", new_owner_id="B", request_key="request-1"
    )["operation_id"]


def drain(world, operation_id):
    for _ in range(12):
        if world.service().step(operation_id):
            return
    pytest.fail(str(world.repo.get(operation_id)))


def test_full_handoff_preserves_b6_resource_identity_and_other_collaborators(world):
    with world.db.orm_session() as db:
        for user in ["A", "C"]:
            db.add(
                BotCollaboratorModel(
                    bot_pk=world.bot["id"],
                    bot_id="service",
                    owner_id="A",
                    user_id=user,
                    role="admin",
                    operator_id="A",
                    env="dev",
                )
            )
    op = begin(world)
    with pytest.raises(HandoffError, match="IN_PROGRESS"):
        assert_bot_mutable(world.bots.get_by_pk(world.bot["id"]))
    drain(world, op)
    bot = world.bots.get_by_pk(world.bot["id"])
    assert (bot["owner_id"], bot["entity_id"], bot["creator_id"]) == ("B", "A", "A")
    assert_bot_mutable(bot)
    assert world.passport.issues[0]["execution_workno"] == "B"
    assert (
        len(world.passport.issues)
        == len(world.runtime.calls)
        == len(world.passport.retired)
        == 1
    )
    assert world.runtime.calls[0]["user_id"] == "A"
    for locator in ["A", "B"]:
        assert (
            _get_path_params(
                RequestContext(user_id="B", bot_id="service"),
                locator,
                bot_repo=world.bots,
            )[0]
            == "A"
        )
    with world.db.orm_session() as db:
        assert [r.user_id for r in db.query(BotCollaboratorModel).all()] == ["C"]
        assert db.query(BotExecutionIdentityBindingModel).count() == 0
        assert db.query(BotCollabLockModel).count() == 0
    assert "test-token" not in str(world.repo.get(op)) + str(world.queue.calls)
    assert "credential_id" not in world.service().get(operation_id=op, actor_id="B")


def test_independent_staff_executor_and_binding_are_preserved(world):
    with world.db.orm_session() as db:
        row = BotExecutionIdentityBindingModel(
            bot_pk=world.bot["id"],
            execution_workno="D",
            identity_type="STAFF",
            status="ACTIVE",
            modifier_id="A",
            env="dev",
        )
        db.add(row)
        db.flush()
        binding_id = row.id
    op = begin(world)
    drain(world, op)
    assert world.passport.issues[0]["execution_workno"] == "D"
    with world.db.orm_session() as db:
        row = db.query(BotExecutionIdentityBindingModel).one()
        assert (row.id, row.execution_workno, row.credential_id) == (
            binding_id,
            "D",
            "selected",
        )


def test_timeout_after_remote_issue_reconciles_without_second_issue(world):
    world.passport.timeout_after_issue = True
    op = begin(world)
    assert not world.service().step(op)
    assert world.repo.get(op)["phase"] == "REISSUING"
    drain(world, op)
    assert len(world.passport.issues) == 1


def test_unknown_result_never_blindly_reissues_or_changes_owner(world):
    op = begin(world)
    world.service().step(op)
    world.passport.current.pop("handoff_operation_id")
    for _ in range(3):
        assert not world.service().step(op)
    assert world.repo.get(op)["error_code"] == "PASSPORT_RESULT_UNKNOWN"
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "A"
    assert len(world.passport.issues) == 1


def test_pending_aac_is_not_runtime_or_owner_success(world):
    world.passport.status = "PENDING"
    op = begin(world)
    for _ in range(4):
        world.service().step(op)
    assert world.repo.get(op)["phase"] == "WAITING_AUTHORIZATION"
    assert world.runtime.calls == world.passport.retired == []
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "A"
    world.passport.status = "ISSUED"
    drain(world, op)


@pytest.mark.parametrize("boundary", ["runtime", "retire"])
def test_failed_boundary_keeps_old_owner_and_retries_same_generation(world, boundary):
    world.runtime.fail, world.passport.retire_fail = (
        boundary == "runtime",
        boundary == "retire",
    )
    op = begin(world)
    for _ in range(7):
        world.service().step(op)
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "A"
    assert "test-token" not in str(world.repo.get(op))
    world.runtime.fail = world.passport.retire_fail = False
    drain(world, op)
    assert len(world.passport.issues) == 1


def test_idempotency_and_actor_target_conflicts(world):
    op = begin(world)
    assert begin(world) == op
    with pytest.raises(HandoffError, match="IDEMPOTENCY_CONFLICT"):
        world.service().create(
            bot_pk=world.bot["id"],
            actor_id="A",
            new_owner_id="C",
            request_key="request-1",
        )
    with pytest.raises(HandoffError, match="ACCESS_DENIED"):
        world.service().get(operation_id=op, actor_id="outsider")
    drain(world, op)
    assert begin(world) == op


def test_safe_cancel_before_remote_intent(world):
    op = begin(world)
    assert world.service().cancel(operation_id=op, actor_id="A")["phase"] == "CANCELLED"
    assert world.passport.issues == []
    assert_bot_mutable(world.bots.get_by_pk(world.bot["id"]))


def test_cancel_after_remote_intent_is_denied(world):
    op = begin(world)
    world.service().step(op)
    with pytest.raises(HandoffError, match="CANNOT_CANCEL"):
        world.service().cancel(operation_id=op, actor_id="A")


def test_enqueue_failure_recovers_from_durable_journal(world):
    world.queue.fail = True
    with pytest.raises(RuntimeError, match="storage unavailable"):
        begin(world)
    row = world.repo.recoverable()[0]
    world.queue.fail = False
    world.service().reconcile()
    assert world.queue.calls[0][0][1]["operation_id"] == row["operation_id"]
    drain(world, row["operation_id"])


def test_disabled_feature_does_not_read_missing_journal(world):
    OwnerHandoffModel.__table__.drop(world.db.engine)
    service = world.service(OwnerHandoffPolicy())
    service.reconcile()
    assert service.step("not-found") is False
    with pytest.raises(HandoffError, match="DISABLED"):
        service.get(operation_id="not-found", actor_id="A")
    with pytest.raises(HandoffError, match="DISABLED"):
        service.create(bot_pk=1, actor_id="A", new_owner_id="B", request_key="r")


@pytest.mark.parametrize(
    "kind,bot_id", [("personal", "service"), ("service", "default")]
)
def test_personal_and_default_cannot_transfer(world, kind, bot_id):
    with world.db.orm_session() as db:
        row = db.get(BotModel, world.bot["id"])
        row.bot_type, row.bot_id = kind, bot_id
    with pytest.raises(HandoffError, match="SERVICE_BOT_REQUIRED"):
        begin(world)
    assert world.passport.issues == []


def test_active_work_order_blocks_without_rewriting_history(world):
    with world.db.orm_session() as db:
        db.add(
            WorkOrderModel(
                work_order_no="WO1",
                biz_type="BOT_COLLABORATOR",
                biz_id="service",
                applicant_user_id="C",
                status="PENDING",
                env="dev",
            )
        )
    with pytest.raises(HandoffError, match="APPROVAL_PENDING"):
        begin(world)
    with world.db.orm_session() as db:
        assert db.query(WorkOrderModel).one().applicant_user_id == "C"


def test_opaque_friend_order_checks_pending_approver(world):
    with world.db.orm_session() as db:
        order = WorkOrderModel(
            work_order_no="WO1",
            biz_type="BOT_FRIEND",
            biz_id="opaque",
            applicant_user_id="C",
            status="PENDING",
            env="dev",
        )
        db.add(order)
        db.flush()
        db.add(
            WorkOrderApproverModel(
                work_order_id=order.id,
                approver_user_id="A",
                status="PENDING",
                env="dev",
            )
        )
    with pytest.raises(HandoffError, match="APPROVAL_PENDING"):
        begin(world)


def test_operation_tenant_enforced_and_task_carries_scope(world):
    op = begin(world)
    with avernet_tenant_scope("other"):
        with pytest.raises(HandoffError, match="NOT_FOUND"):
            world.repo.get(op)
    assert world.queue.calls[0][0][1]["avernet_tenant"] == "teamclaw"


def test_worker_lease_and_fact_allowlist(world):
    op = begin(world)
    assert world.repo.claim(op, "first")
    assert world.repo.claim(op, "second") is None
    with pytest.raises(HandoffError, match="LEASE_LOST"):
        world.repo.advance(op, "second", "SUCCEEDED")
    with pytest.raises(ValueError, match="non-secret"):
        world.repo.advance(op, "first", "QUEUED", token="test-token")


def test_generation_drift_does_not_inject_or_commit(world):
    op = begin(world)
    world.service().step(op)
    world.service().step(op)
    world.passport.current["credential_id"] = "unexpected-third-generation"
    assert not world.service().step(op)
    assert world.repo.get(op)["error_code"] == "PASSPORT_GENERATION_CONFLICT"
    assert world.runtime.calls == []
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "A"


def test_final_transaction_failure_does_not_report_success(world, monkeypatch):
    op = begin(world)
    for _ in range(5):
        world.service().step(op)
    assert world.repo.get(op)["phase"] == "COMMITTING_OWNER"
    original = world.repo._unfreeze

    def failing(db, row, bot):
        original(db, row, bot)
        raise RuntimeError("transaction failed")

    monkeypatch.setattr(world.repo, "_unfreeze", failing)
    assert not world.service().step(op)
    assert world.repo.get(op)["phase"] == "COMMITTING_OWNER"
    bot = world.bots.get_by_pk(world.bot["id"])
    assert bot["owner_id"] == "A" and bot["ext"]["owner_handoff"] == op
    monkeypatch.setattr(world.repo, "_unfreeze", original)
    drain(world, op)
    assert len(world.passport.issues) == 1


def test_authorization_metadata_is_executor_only_and_never_contains_token(world):
    op = begin(world)
    world.service().step(op)
    world.service().step(op)
    result = world.service().authorization(operation_id=op, actor_id="B")
    assert set(result) == {"status", "redirect_url", "iframe_url"}
    assert "test-token" not in str(result)
    with pytest.raises(HandoffError, match="ACCESS_DENIED"):
        world.service().authorization(operation_id=op, actor_id="A")


def test_unsupported_provider_does_not_freeze_bot(world, monkeypatch):
    monkeypatch.setattr(world.passport, "owner_handoff_supported", lambda **_: False)
    with pytest.raises(HandoffError, match="UNSUPPORTED"):
        begin(world)
    assert_bot_mutable(world.bots.get_by_pk(world.bot["id"]))
    assert world.repo.recoverable() == []


def test_unrelated_actor_cannot_begin(world):
    with pytest.raises(HandoffError, match="OWNER_REQUIRED"):
        world.service().create(
            bot_pk=world.bot["id"], actor_id="C", new_owner_id="B", request_key="r"
        )
    assert world.passport.issues == []


def test_recovery_only_policy_drains_but_cannot_initiate(world):
    op = begin(world)
    service = world.service(OwnerHandoffPolicy(recovery_enabled=True))
    with pytest.raises(HandoffError, match="DISABLED"):
        service.create(
            bot_pk=world.bot["id"], actor_id="A", new_owner_id="B", request_key="new"
        )
    for _ in range(8):
        if service.step(op):
            break
    assert service.get(operation_id=op, actor_id="B")["phase"] == "SUCCEEDED"


@pytest.mark.parametrize(
    "result",
    [
        {"bindings": []},
        {"bindings": [{"device_count": 0}]},
        {"bindings": [{"device_count": 1}], "skipped": True},
    ],
)
def test_runtime_noop_or_empty_ack_cannot_retire_credentials(
    world, monkeypatch, result
):
    monkeypatch.setattr(
        world.runtime, "hot_update_passport_token_to_device", lambda **_: result
    )
    op = begin(world)
    for _ in range(5):
        world.service().step(op)
    assert world.repo.get(op)["error_code"] == "RUNTIME_ACK_REQUIRED"
    assert world.passport.retired == []
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "A"


@pytest.mark.parametrize("action", ["update", "space", "ext", "delete"])
def test_direct_bot_writes_cannot_clear_maintenance_or_delete_bot(world, action):
    begin(world)
    with pytest.raises(HandoffError, match="IN_PROGRESS"):
        if action == "update":
            world.bots.update_by_owner("service", "A", {"ext": {}})
        elif action == "space":
            world.bots.update_space_by_owner(
                bot_id="service", owner_id="A", space_id=12
            )
        elif action == "ext":
            world.bots.compare_and_set_ext(
                bot_id="service",
                owner_id="A",
                expected_ext=world.bots.get_by_pk(world.bot["id"])["ext"],
                ext={},
            )
        else:
            world.bots.soft_delete_by_owner("service", "A")
    assert world.bots.get_by_pk(world.bot["id"])["ext"]["owner_handoff"]


def test_second_handoff_keeps_original_entity_and_default_execution_semantics(world):
    first = begin(world)
    drain(world, first)
    world.passport.target_owner = "C"
    world.passport.next_credential_id = "selected-second"
    second = world.service().create(
        bot_pk=world.bot["id"], actor_id="B", new_owner_id="C", request_key="second"
    )["operation_id"]
    drain(world, second)
    bot = world.bots.get_by_pk(world.bot["id"])
    assert (bot["owner_id"], bot["entity_id"]) == ("C", "A")
    assert world.passport.issues[-1]["execution_workno"] == "C"
    assert world.passport.issues[-1]["expected_credential_id"] == "selected"
    with world.db.orm_session() as db:
        assert db.query(BotExecutionIdentityBindingModel).count() == 0
    for locator in ["A", "C"]:
        assert (
            _get_path_params(
                RequestContext(user_id="C", bot_id="service"),
                locator,
                bot_repo=world.bots,
            )[0]
            == "A"
        )



def test_authorized_retry_uses_private_scope_without_exposing_it(world):
    op = begin(world)
    result = world.service().retry(operation_id=op, actor_id="A")
    assert result["phase"] == "QUEUED" and "avernet_tenant" not in result
    assert len(world.queue.calls) == 2
    assert world.queue.calls[-1][0][1] == {"operation_id": op, "avernet_tenant": "teamclaw"}
