"""Real journal/aggregate transactions with an external protocol fixture."""

from types import SimpleNamespace
from unittest.mock import MagicMock

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
from agentclaw.community.core.bot_management.owner_handoff.preflight import OwnerHandoffPreflight
from agentclaw.community.core.repository.implementations.devices.device import DeviceRepository
from agentclaw.community.core.repository.implementations.publishing.bot_publish import BotPublishRepository
from agentclaw.community.core.service_bot.repository.config_artifact_offload import ConfigArtifactOffloader
from agentclaw.community.core.repository.implementations.bot.owner_handoff import (
    OwnerHandoffRepository,
)
from agentclaw.community.kernel.bot_manager_sync import SyncReason
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

    def query_handoff_passport(self, **kwargs):
        return None

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
    devices = DeviceRepository(db)
    publishes = BotPublishRepository(db, ConfigArtifactOffloader(MagicMock()))
    binding_id = devices.insert_binding(
        entity_id="A", entity_type="staff", device_id="draft-device",
        device_provider="arca", env="dev", device_props={}, status="ACTIVE",
        apply_reason="test handoff", applied_by="A",
    )
    bots.update_by_owner(bot["bot_id"], "A", {"binding_id": binding_id})
    bot = bots.get_by_pk(bot["id"])
    repo, passport, runtime, queue = (
        OwnerHandoffRepository(db),
        Passport(),
        Runtime(),
        Queue(),
    )
    manager_sync = MagicMock()
    ownership_sync = MagicMock()
    from agentclaw.community.core.repository.implementations.spaces.space import SpaceRepository
    from agentclaw.community.core.repository.implementations.spaces.persistence_effects import NoKnowledgeSpacePersistenceEffects
    from agentclaw.community.core.spaces.services.space_access_service import SpaceAccessService
    from agentclaw.community.core.spaces.services.space_service import SpaceService
    spaces_repo = SpaceRepository(db, NoKnowledgeSpacePersistenceEffects())
    space_staff = MagicMock()
    space_staff.get_profile_by_work_no.return_value.nick_name = None
    space_client = MagicMock()
    spaces = SpaceService(spaces_repo, space_client, space_staff)
    space_access = SpaceAccessService(spaces_repo)

    def service(
        policy=OwnerHandoffPolicy(enabled=True, recovery_enabled=True),
        notifier=None,
    ):
        return OwnerHandoffService(
            repo,
            passport,
            runtime,
            Staff(),
            bots,
            queue,
            policy,
            notifier if notifier is not None else manager_sync,
            spaces,
            space_access,
            ownership_sync=ownership_sync,
            preflight=OwnerHandoffPreflight(passport, devices, publishes),
        )

    return SimpleNamespace(
        db=db,
        bots=bots,
        bot=bot,
        repo=repo,
        passport=passport,
        runtime=runtime,
        queue=queue,
        manager_sync=manager_sync,
        ownership_sync=ownership_sync,
        spaces=spaces,
        spaces_repo=spaces_repo,
        space_access=space_access,
        space_client=space_client,
        service=service,
        devices=devices,
        publishes=publishes,
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


def _reach_phase(world, op, phase):
    for _ in range(8):
        if world.repo.get(op)["phase"] == phase:
            return
        world.service().step(op)
    pytest.fail(str(world.repo.get(op)))


def _verified_renewal(world, credential_id):
    previous = world.passport.current
    world.passport.current = {
        **previous,
        "entity_id": "A",
        "credential_id": credential_id,
        "handoff_ancestor_credential_ids": [
            previous["credential_id"],
            *previous.get("handoff_ancestor_credential_ids", []),
        ],
    }


@pytest.mark.parametrize("phase", [
    "WAITING_AUTHORIZATION", "UPDATING_RUNTIME",
    "RETIRING_OLD_CREDENTIALS", "COMMITTING_OWNER",
])
def test_verified_renewal_always_replays_authorization_and_runtime(world, phase):
    op = begin(world)
    _reach_phase(world, op, phase)
    old_runtime_calls = len(world.runtime.calls)
    _verified_renewal(world, "renewed-twice")
    _verified_renewal(world, "latest-renewal")
    world.passport.query_auth_status = lambda *args, **kwargs: {
        "status": "ISSUED", "token": "test-renew",
    }
    assert not world.service().step(op)
    row = world.repo.get(op)
    assert row["credential_id"] == "latest-renewal"
    assert row["phase"] == "WAITING_AUTHORIZATION"
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "A"
    drain(world, op)
    assert len(world.runtime.calls) == old_runtime_calls + 1
    assert world.runtime.calls[-1]["token"] == "test-renew"
    assert world.passport.retired[-1]["keep_credential_id"] == "latest-renewal"
    assert len(world.passport.issues) == 1
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "B"
    assert_bot_mutable(world.bots.get_by_pk(world.bot["id"]))


def test_runtime_failure_then_daily_renewal_recovers_without_reissuing(world):
    op = begin(world)
    _reach_phase(world, op, "UPDATING_RUNTIME")
    world.runtime.fail = True
    world.service().step(op)
    assert world.repo.get(op)["error_code"] == "HANDOFF_EXTERNAL_ERROR"
    _verified_renewal(world, "daily-renewal")
    world.runtime.fail = False
    drain(world, op)
    assert world.repo.get(op)["credential_id"] == "daily-renewal"
    assert len(world.passport.issues) == 1


def test_adopted_renewal_waits_for_its_own_authorization(world):
    op = begin(world)
    _reach_phase(world, op, "UPDATING_RUNTIME")
    _verified_renewal(world, "pending-renewal")
    world.passport.status = "PENDING"
    world.service().step(op)
    world.service().step(op)
    assert world.repo.get(op)["phase"] == "WAITING_AUTHORIZATION"
    assert world.repo.get(op)["error_code"] == "PASSPORT_AUTHORIZATION_PENDING"
    assert not world.runtime.calls
    assert not world.passport.retired
    world.passport.status = "ISSUED"
    drain(world, op)


def test_failed_renewal_selection_write_cannot_update_runtime_or_owner(world, monkeypatch):
    op = begin(world)
    _reach_phase(world, op, "UPDATING_RUNTIME")
    _verified_renewal(world, "new-selection")
    advance = world.repo.advance

    def reject_selection(*args, **kwargs):
        if kwargs.get("credential_id") == "new-selection":
            raise RuntimeError("journal unavailable")
        return advance(*args, **kwargs)

    monkeypatch.setattr(world.repo, "advance", reject_selection)
    assert not world.service().step(op)
    assert world.repo.get(op)["credential_id"] == "selected"
    assert world.repo.get(op)["error_code"] == "HANDOFF_EXTERNAL_ERROR"
    assert not world.runtime.calls
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "A"
    monkeypatch.setattr(world.repo, "advance", advance)
    drain(world, op)


@pytest.mark.parametrize("invalid", [
    {"handoff_operation_id": "other-operation"},
    {"execution_workno": "other-person"},
    {"agent_id": "other-agent"},
    {"entity_id": "other-entity"},
    {"handoff_ancestor_credential_ids": None},
    {"handoff_ancestor_credential_ids": []},
    {"handoff_ancestor_credential_ids": ["unrelated"]},
    {"handoff_ancestor_credential_ids": "selected"},
    {"handoff_ancestor_credential_ids": ["selected", None]},
])
def test_same_executor_without_valid_provenance_cannot_be_adopted(world, invalid):
    op = begin(world)
    _reach_phase(world, op, "UPDATING_RUNTIME")
    _verified_renewal(world, "unproven-renewal")
    world.passport.current.update(invalid)
    assert not world.service().step(op)
    row = world.repo.get(op)
    assert row["credential_id"] == "selected"
    assert row["error_code"] == "PASSPORT_GENERATION_CONFLICT"
    assert not world.runtime.calls
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "A"


def test_renewal_during_runtime_update_cannot_reuse_previous_ack(world):
    op = begin(world)
    _reach_phase(world, op, "UPDATING_RUNTIME")
    update = world.runtime.hot_update_passport_token_to_device

    def renew_during_update(**kwargs):
        result = update(**kwargs)
        _verified_renewal(world, "concurrent-renewal")
        return result

    world.runtime.hot_update_passport_token_to_device = renew_during_update
    assert not world.service().step(op)
    assert world.repo.get(op)["phase"] == "UPDATING_RUNTIME"
    assert not world.passport.retired
    world.runtime.hot_update_passport_token_to_device = update
    drain(world, op)
    assert len(world.runtime.calls) == 2


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


def test_committed_owner_submits_manager_sync_with_journal_facts(world):
    op = begin(world)
    drain(world, op)
    world.manager_sync.request_sync.assert_called_once()
    kwargs = world.manager_sync.request_sync.call_args.kwargs
    assert kwargs["reason"] is SyncReason.OWNER_HANDOFF
    assert kwargs["bot_pk"] == world.bot["id"]
    assert kwargs["env"] == "dev"
    assert kwargs["avernet_tenant"] == "teamclaw"
    # The finishing step owns the only submit; a re-drive of a terminal
    # journal never notifies twice.
    assert world.service().step(op) is True
    assert world.manager_sync.request_sync.call_count == 1


def test_handoff_still_succeeds_when_sync_submit_fails(world):
    op = begin(world)
    for _ in range(5):
        world.service().step(op)
    assert world.repo.get(op)["phase"] == "COMMITTING_OWNER"
    failing = MagicMock()
    failing.request_sync.side_effect = RuntimeError("queue closed")

    assert world.service(notifier=failing).step(op) is True
    assert world.repo.get(op)["phase"] == "SUCCEEDED"
    failing.request_sync.assert_called_once()


def test_sync_submit_failure_does_not_reenter_passport_admin_sync(world):
    op = begin(world)
    for _ in range(5):
        world.service().step(op)
    assert world.repo.get(op)["phase"] == "COMMITTING_OWNER"
    failing = MagicMock()
    failing.request_sync.side_effect = RuntimeError("queue closed")
    # The commit is durable before the notifier runs; the new logic must not
    # compensate through the usual Passport admin-sync path.
    world.passport.update_passport = MagicMock()

    assert world.service(notifier=failing).step(op) is True
    assert world.repo.get(op)["phase"] == "SUCCEEDED"
    world.passport.update_passport.assert_not_called()
    # And the handoff's own remote footprint is unchanged by the failure.
    assert len(world.passport.issues) == 1
    assert len(world.passport.retired) == 1


def test_finish_failure_does_not_submit_manager_sync(world, monkeypatch):
    op = begin(world)
    for _ in range(5):
        world.service().step(op)
    assert world.repo.get(op)["phase"] == "COMMITTING_OWNER"

    def failing(operation_id, worker):
        raise RuntimeError("finish commit failed")

    monkeypatch.setattr(world.repo, "finish", failing)
    assert not world.service().step(op)
    world.manager_sync.request_sync.assert_not_called()


def test_configured_admin_can_handoff_for_absent_owner_and_is_audited(world):
    service = world.service(
        OwnerHandoffPolicy(
            enabled=True,
            recovery_enabled=True,
            administrators=frozenset({"ops"}),
        )
    )
    created = service.create(
        bot_pk=world.bot["id"],
        actor_id="ops",
        new_owner_id="B",
        request_key="admin-handoff",
    )
    op = created["operation_id"]
    for _ in range(12):
        if service.step(op):
            break
    result = service.get(operation_id=op, actor_id="ops")
    assert result["phase"] == "SUCCEEDED"
    assert result["actor_id"] == "ops"
    assert result["old_owner_id"] == "A"
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "B"


@pytest.mark.parametrize("expired", [False, True])
def test_handoff_with_expired_history_preserves_existing_flow(world, monkeypatch, expired):
    snapshot = MagicMock(return_value={"credential_id": "old"})
    monkeypatch.setattr(world.passport, "query_handoff_passport", snapshot)
    original = world.passport.query_agent_passport
    def current(*args, **kwargs):
        if expired and world.passport.current.get("credential_id") == "old":
            return None
        return original(*args, **kwargs)
    monkeypatch.setattr(world.passport, "query_agent_passport", current)
    op = begin(world)
    drain(world, op)
    assert world.repo.get(op)["phase"] == "SUCCEEDED"
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "B"
    assert len(world.passport.issues) == 1
    assert world.passport.issues[0]["expected_credential_id"] == "old"
    if expired:
        assert snapshot.call_count == 2  # Read-only preflight and worker revalidation.
        snapshot.assert_called_with(bot_id="service", owner_workno="A", entity_id="A")
    else:
        snapshot.assert_not_called()


def test_missing_history_stays_queued_without_issuing_or_changing_owner(world, monkeypatch):
    op = begin(world)  # History can disappear after preflight or on an old task.
    monkeypatch.setattr(world.passport, "query_agent_passport", lambda *a, **k: None)
    monkeypatch.setattr(world.passport, "query_handoff_passport", lambda **k: None)
    assert not world.service().step(op)
    row = world.repo.get(op)
    assert row["phase"] == "QUEUED"
    assert row["error_code"] == "CURRENT_PASSPORT_REQUIRED"
    assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "A"
    assert not world.passport.issues


def test_expired_snapshot_failure_does_not_issue_credentials(world, monkeypatch):
    op = begin(world)
    monkeypatch.setattr(world.passport, "query_agent_passport", lambda *a, **k: None)
    def fail(**kwargs):
        raise RuntimeError("snapshot unavailable")
    monkeypatch.setattr(world.passport, "query_handoff_passport", fail)
    assert not world.service().step(op)
    assert world.repo.get(op)["error_code"] == "HANDOFF_EXTERNAL_ERROR"
    assert not world.passport.issues


@pytest.mark.parametrize("initial_arca_ack", [True, False, []])
def test_real_runtime_arca_receipt_completes_handoff_and_releases_lock(
    world, monkeypatch, initial_arca_ack,
):
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.core.service_bot.repository.models import PublishStatus

    online = world.devices.insert_binding(
        entity_id="A", entity_type="staff", device_id="BOT-online",
        device_provider="baas", env="dev", device_props={}, status="ACTIVE",
        apply_reason="published service", applied_by="A",
    )
    world.publishes.insert({
        "source_bot_pk": world.bot["id"], "source_bot_id": "service",
        "publish_bot_id": "service", "owner_id": "A", "name": "service",
        "env": "dev", "status": PublishStatus.SUCCESS.value, "version": 1,
        "permission_owner": "owner", "ext": {"binding": {"online": online}},
    })
    devices = MagicMock()
    devices.get_device.side_effect = lambda **kwargs: world.devices.get_by_id(kwargs["binding_id"])
    receipts = {"arca": initial_arca_ack}
    devices.update_device_headers.side_effect = lambda **kw: (
        receipts["arca"] if kw["device"].device_provider == "arca"
        else [{"device_uuid": "DEVICE-online"}]
    )
    runtime = BotService.__new__(BotService)
    runtime._scheduled_caller_refresh = None
    runtime._repository = world.bots
    runtime._passport_plugin = world.passport
    runtime._device_service_provider = lambda: devices
    runtime._device_binding_repo = world.devices
    runtime._bot_publish_repo = world.publishes
    publication = MagicMock()
    publication.get_device_binding_by_id.side_effect = world.devices.get_by_id
    runtime._bot_publish_provider = lambda: publication
    monkeypatch.setattr(
        world.runtime, "hot_update_passport_token_to_device",
        runtime.hot_update_passport_token_to_device,
    )
    op = begin(world)
    service = world.service()
    for _ in range(3):
        service.step(op)
    assert world.repo.get(op)["phase"] == "UPDATING_RUNTIME"
    service.step(op)
    if initial_arca_ack is not True:
        assert world.repo.get(op)["error_code"] == "RUNTIME_ACK_REQUIRED"
        assert world.bots.get_by_pk(world.bot["id"])["owner_id"] == "A"
        with world.db.orm_session() as db:
            assert db.query(BotCollabLockModel).count() == 1
        receipts["arca"] = True
    drain(world, op)
    assert world.repo.get(op)["phase"] == "SUCCEEDED"
    bot = world.bots.get_by_pk(world.bot["id"])
    assert bot["owner_id"] == "B" and bot["entity_id"] == "A"
    assert not (bot.get("ext") or {}).get("owner_handoff")
    with world.db.orm_session() as db:
        assert db.query(BotCollabLockModel).count() == 0
    assert len(world.passport.issues) == 1
    assert any(call.kwargs["device"].device_provider == "arca" for call in devices.update_device_headers.call_args_list)
    assert any(call.kwargs["device"].device_provider == "baas" for call in devices.update_device_headers.call_args_list)
