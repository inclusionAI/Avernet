"""G3 protocol values and durable acquisition, without L4 state transitions."""

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.telemetry_access import (
    TelemetryAccess,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.metrics import (
    MetricDefinition,
    MetricSample,
    Outcome,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.persistence import (
    TelemetryRecordModel,
    TelemetryRepository,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.service import (
    TelemetryService,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.service_api import (
    TelemetryServiceProtocol,
)
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.telemetry import (
    Observation,
    TelemetryContext,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (
    IdentityRef,
    ObjectRef,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    ContextCollabEventModel,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepository,
    InitialEventDraft,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from sqlalchemy import event as sql_event
from sqlalchemy.exc import IntegrityError

from tests.community.core.task.context_collab.creation_facts import (
    create_collet_request,
    creation_facts,
)
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

OWNER = ActorContext(PrincipalRef("human", "owner"), "api")
BOT = ActorContext(PrincipalRef("bot", "worker"), "a2a", OWNER.actor)
OBSERVER = IdentityRef("AGENT", BOT.actor.principal_id, "avernet")
NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def _ref(kind, identifier, version=None):
    return ObjectRef(kind, identifier, version, "avernet")


@pytest.fixture
def runtime(db):
    work = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)
    space = SpaceRef("telemetry-space", "test")
    created = work.create_collet(
        create_collet_request(
            creation_facts(
                space=space,
                principal_ref=OWNER.actor,
                method_kind="Single",
                executor_ref=AgentRef("bot", "worker"),
                objective="Deliver a report",
                resource_refs=(),
                product_kind="team_todo",
                workflow_kind="team_todo_execution",
            ),
            space=space,
            principal_ref=OWNER.actor,
            initial_event=InitialEventDraft("task.created", "Task", OWNER.actor, {}),
        )
    )
    storage = TelemetryRepository(db)
    access = TelemetryAccess(work, StructuralContextCollabAuthorizer())
    return TelemetryService(storage, access), work, storage, created


@pytest.fixture
def context(runtime):
    _, _, _, created = runtime
    return TelemetryContext(
        trace_id="trace-1",
        span_id="span-1",
        protocol_versions={"ACCS": "0.2"},
        collet_ref=_ref("Collet", created.collet.collet_id),
        task_ref=_ref("Task", created.task.task_id),
        agent_ref=_ref("Agent", BOT.actor.principal_id),
        principal_ref=_ref("Principal", "owner"),
    )


@pytest.fixture
def observation(context):
    return Observation(
        observation_id=str(uuid4()),
        observation_type="EVENT",
        observed_at=NOW,
        recorded_at=NOW,
        observer=OBSERVER,
        subject_ref=context.task_ref,
        context=context,
        schema_version="execution_step@1",
        extensions={"step": 1, "detail": "Code written"},
    )


@pytest.fixture
def metric():
    return MetricDefinition(
        metric_id="execution.percent",
        name="Execution progress",
        direction="HIGHER_IS_BETTER",
        unit="percent",
        aggregation="LAST",
        scope="Task",
        owner=OBSERVER,
        schema_version="1.0",
    )


@pytest.fixture
def sample(runtime, context, metric):
    service, _, _, _ = runtime
    ref = service.define_metric(metric, version="7", actor_context=BOT)
    return MetricSample(
        metric_ref=ref,
        subject_ref=context.task_ref,
        value=100,
        measured_at=NOW,
        window=None,
        evidence_refs=(),
        dimensions={"step": "4"},
    )


@pytest.fixture
def outcome(context, sample):
    return Outcome(
        outcome_id=str(uuid4()),
        subject_ref=context.task_ref,
        improvement_objective_refs=(),
        status="SUCCEEDED",
        produced_at=NOW,
        result_refs=(_ref("Artifact", "report"),),
        evidence_refs=(),
        metric_samples=(sample,),
        evaluator_ref=None,
        protocol_versions={"ACCS": "0.2"},
    )


def _count(db):
    with db.orm_session() as session:
        return session.query(TelemetryRecordModel).count()


def test_generic_values_round_trip(observation, metric, sample, outcome):
    for value in (observation.context, observation, metric, sample, outcome):
        assert type(value).from_dict(value.to_dict()) == value


@pytest.mark.parametrize(
    "change",
    [
        {"observation_type": []},
        {"observation_type": "PROGRESS"},
        {"observed_at": datetime(2026, 11, 17)},
        {"recorded_at": "bad"},
        {"schema_version": ""},
        {"confidence": 1.1},
        {"confidence": True},
        {"confidence": float("nan")},
        {"extensions": {"percent": float("nan")}},
        {"extensions": {1: "not a JSON string key"}},
        {"extensions": {"file": object()}},
        {"observer": IdentityRef("BOT", "worker", "avernet")},
        {"subject_ref": None},
    ],
)
def test_observation_rejects_invalid_schema(observation, change):
    with pytest.raises(ContextCollabValidationError):
        replace(observation, **change)


@pytest.mark.parametrize(
    "kind", ["observation", "context", "metric", "sample", "outcome"]
)
def test_unknown_top_level_fields_rejected(
    kind, observation, context, metric, sample, outcome
):
    value = locals()[kind]
    with pytest.raises(ContextCollabValidationError):
        type(value).from_dict({**value.to_dict(), "unexpected": True})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), None, {}, []])
def test_metric_sample_rejects_non_scalar_or_non_finite(sample, value):
    with pytest.raises(ContextCollabValidationError):
        replace(sample, value=value)


def test_record_values_persist_with_audit_but_never_advance_work(
    runtime, db, observation, sample, outcome
):
    service, work, _, created = runtime
    assert isinstance(service, TelemetryServiceProtocol)
    before = work.get_current_status(created.collet.collet_id)
    recorded = service.record_observation(observation, actor_context=BOT)
    assert recorded.recorded_at != observation.recorded_at
    assert recorded.observed_at == observation.observed_at
    assert (
        service.record_metric_sample(
            sample, context=observation.context, actor_context=BOT
        )
        == sample
    )
    assert (
        service.record_outcome(outcome, context=observation.context, actor_context=BOT)
        == outcome
    )
    assert work.get_current_status(created.collet.collet_id) == before
    assert before.task.status == "created"  # 100% and SUCCEEDED do not mean done.
    reloaded = TelemetryService(
        TelemetryRepository(db),
        TelemetryAccess(work, StructuralContextCollabAuthorizer()),
    )
    query = dict(collet_ref=observation.context.collet_ref, actor_context=OWNER)
    assert reloaded.query_observations(observation.subject_ref, **query) == (recorded,)
    assert reloaded.query_metric_samples(sample.subject_ref, **query) == (sample,)
    assert reloaded.query_outcomes(outcome.subject_ref, **query) == (outcome,)
    with db.orm_session() as session:
        rows = session.query(TelemetryRecordModel).all()
        assert len(rows) == 4  # Definition, observation, sample, atomic Outcome.
        assert all('"identity_id": "worker"' in row.recorded_by for row in rows)
        assert all(row.recorded_at is not None for row in rows)
    observation.extensions["detail"] = "mutated by caller"
    assert (
        reloaded.query_observations(observation.subject_ref, **query)[0].extensions[
            "detail"
        ]
        == "Code written"
    )


def test_definition_version_is_immutable_and_not_schema_version(runtime, metric, db):
    service, _, storage, _ = runtime
    ref = service.define_metric(metric, version="7", actor_context=BOT)
    assert ref.version == "7" and storage.get_metric(ref).schema_version == "1.0"
    assert service.define_metric(metric, version="7", actor_context=BOT) == ref
    with pytest.raises(ContextCollabValidationError, match="immutable"):
        service.define_metric(
            replace(metric, aggregation="MEAN"), version="7", actor_context=BOT
        )
    with pytest.raises(ContextCollabValidationError, match="owner"):
        service.define_metric(metric, version="8", actor_context=OWNER)
    assert _count(db) == 1


@pytest.mark.parametrize("principal_type", ["human", "org", "system"])
def test_non_agent_metric_owners_remain_principals(runtime, metric, principal_type):
    actor = ActorContext(PrincipalRef(principal_type, "metric-owner"), "api")
    definition = replace(
        metric, owner=IdentityRef("PRINCIPAL", "metric-owner", "avernet")
    )
    service, _, storage, _ = runtime
    ref = service.define_metric(definition, version="1", actor_context=actor)
    assert storage.get_metric(ref) == definition
    with pytest.raises(ContextCollabValidationError, match="owner"):
        service.define_metric(
            replace(definition, owner=IdentityRef("AGENT", "metric-owner", "avernet")),
            version="2",
            actor_context=actor,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"observer": IdentityRef("AGENT", "someone-else", "avernet")},
        {"subject_ref": _ref("Task", "not-in-this-collet")},
    ],
)
def test_forged_observer_or_subject_denied_before_write(
    runtime, observation, db, change
):
    with pytest.raises(ContextCollabValidationError):
        runtime[0].record_observation(replace(observation, **change), actor_context=BOT)
    assert _count(db) == 0


@pytest.mark.parametrize(
    "change",
    [
        {"collet_ref": None},
        {"task_ref": _ref("Task", "other-task")},
        {"agent_ref": _ref("Agent", "other-bot")},
        {"principal_ref": _ref("Principal", "other-owner")},
        {"space_ref": _ref("Space", "other-space")},
        {"activity_ref": _ref("Activity", "invented")},
        {"method_binding_ref": _ref("MethodBinding", "unrelated")},
        {"product_profile_ref": _ref("ProductProfile", "unrelated", "1")},
        {"collet_ref": ObjectRef("Collet", "foreign", None, "foreign-issuer")},
    ],
)
def test_wrong_telemetry_associations_fail_closed(runtime, observation, db, change):
    changed = replace(observation, context=replace(observation.context, **change))
    with pytest.raises(ContextCollabValidationError):
        runtime[0].record_observation(changed, actor_context=BOT)
    assert _count(db) == 0


@pytest.mark.parametrize(
    "query_name", ["query_observations", "query_metric_samples", "query_outcomes"]
)
def test_unrelated_actor_cannot_read_telemetry(runtime, context, query_name):
    outsider = ActorContext(PrincipalRef("bot", "outsider"), "a2a")
    with pytest.raises(ContextCollabValidationError, match="principal or executor"):
        getattr(runtime[0], query_name)(
            context.task_ref, collet_ref=context.collet_ref, actor_context=outsider
        )


def test_hidden_task_evidence_denies_record_and_query(runtime, db, observation):
    service, _, _, created = runtime
    with db.orm_session() as session:
        session.query(ContextCollabEventModel).filter(
            ContextCollabEventModel.event_id == created.initial_event.event_id,
        ).update({"visibility": "private"})
    with pytest.raises(ContextCollabValidationError, match="not visible"):
        service.record_observation(observation, actor_context=BOT)
    with pytest.raises(ContextCollabValidationError, match="not visible"):
        service.query_observations(
            observation.subject_ref,
            collet_ref=observation.context.collet_ref,
            actor_context=BOT,
        )
    assert service.record_observation(
        replace(
            observation,
            observer=IdentityRef("PRINCIPAL", "owner", "avernet"),
            context=replace(observation.context, agent_ref=None),
        ),
        actor_context=OWNER,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"metric_ref": _ref("MetricDefinition", "execution.percent", None)},
        {"metric_ref": _ref("MetricDefinition", "execution.percent", "unknown")},
        {"metric_ref": _ref("MetricDefinition", "missing", "1")},
    ],
)
def test_samples_require_registered_version(runtime, sample, context, db, change):
    with pytest.raises(ContextCollabValidationError):
        runtime[0].record_metric_sample(
            replace(sample, **change), context=context, actor_context=BOT
        )
    assert _count(db) == 1  # Existing definition only.


def test_definition_scope_and_outcome_subject_cannot_be_mixed(
    runtime, metric, sample, context, outcome, db
):
    service = runtime[0]
    ref = service.define_metric(
        replace(metric, metric_id="collet.percent", scope="Collet"),
        version="1",
        actor_context=BOT,
    )
    with pytest.raises(ContextCollabValidationError, match="scope"):
        service.record_metric_sample(
            replace(sample, metric_ref=ref), context=context, actor_context=BOT
        )
    with pytest.raises(ContextCollabValidationError, match="share its subject"):
        service.record_outcome(
            replace(
                outcome,
                metric_samples=(replace(sample, subject_ref=context.collet_ref),),
            ),
            context=context,
            actor_context=BOT,
        )
    with pytest.raises(ContextCollabValidationError, match="evaluator"):
        service.record_outcome(
            replace(outcome, evaluator_ref=_ref("Principal", "owner")),
            context=context,
            actor_context=BOT,
        )
    assert _count(db) == 2  # Definitions only.


def test_tenant_and_environment_scope_storage_and_authorization(
    runtime, observation, metric, monkeypatch
):
    service, _, storage, _ = runtime
    service.record_observation(observation, actor_context=BOT)
    ref = service.define_metric(metric, version="1", actor_context=BOT)
    with avernet_tenant_scope("other-tenant"):
        assert storage.get_metric(ref) is None
        assert (
            storage.list_records(
                "Observation", observation.context.collet_ref, observation.subject_ref
            )
            == ()
        )
        with pytest.raises(ContextCollabNotFoundError):
            service.record_observation(observation, actor_context=BOT)
        service.define_metric(
            replace(metric, name="Other tenant metric"), version="1", actor_context=BOT
        )
    monkeypatch.setenv("SERVER_ENV", "prod")
    assert storage.get_metric(ref) is None
    assert (
        storage.list_records(
            "Observation", observation.context.collet_ref, observation.subject_ref
        )
        == ()
    )
    with pytest.raises(ContextCollabNotFoundError):
        service.query_observations(
            observation.subject_ref,
            collet_ref=observation.context.collet_ref,
            actor_context=BOT,
        )


@pytest.mark.parametrize("kind", ["metric", "observation", "sample", "outcome"])
def test_sql_failure_propagates_without_telemetry_or_work_partial_write(
    runtime, engine, db, observation, metric, sample, outcome, kind
):
    service, work, _, created = runtime
    before = work.get_current_status(created.collet.collet_id)
    count = _count(db)

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO ac_context_collab_telemetry"):
            raise RuntimeError("telemetry SQL unavailable")

    sql_event.listen(engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="SQL unavailable"):
            if kind == "metric":
                service.define_metric(metric, version="8", actor_context=BOT)
            elif kind == "observation":
                service.record_observation(observation, actor_context=BOT)
            elif kind == "sample":
                service.record_metric_sample(
                    sample, context=observation.context, actor_context=BOT
                )
            else:
                service.record_outcome(
                    outcome, context=observation.context, actor_context=BOT
                )
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail_insert)
    assert _count(db) == count
    assert work.get_current_status(created.collet.collet_id) == before


def test_duplicate_observation_id_fails_without_overwrite(runtime, observation, db):
    service = runtime[0]
    service.record_observation(observation, actor_context=BOT)
    with pytest.raises(IntegrityError):
        service.record_observation(
            replace(observation, extensions={"detail": "overwrite"}), actor_context=BOT
        )
    assert _count(db) == 1
