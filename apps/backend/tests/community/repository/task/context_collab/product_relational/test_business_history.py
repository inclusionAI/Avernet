from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.row_codec import (
    _collet_from_row,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.task_storage import (
    load_tasks,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.product_relational_models import (
    ColletRow,
    ColletRolloutRow,
    StageRolloutRow,
    EventRow,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.runtime_event_writer import (
    feedback_event_id,
)
from agentclaw.community.utils.avernet_tenant import (
    get_current_avernet_tenant,
    avernet_tenant_scope,
)
from agentclaw.community.utils.env_utils import get_current_env


def test_create_chain_atomic_physical_ids_and_pure_consistent_reads(online):
    h = online
    history = h.repo.create_collet(h.profile.id, "implement feature", h.owner)
    cid, rid = history.collet.id, history.current_rollout_id
    assert len(history.rollouts) == 1
    stages = history.rollouts[0].stages
    assert [s.name for s in stages] == ["z_design", "a_build"]
    assert [s.status.value for s in stages] == ["CREATE", "CREATE"]
    assert all(s.session_id == "" and s.input == "implement feature" for s in stages)
    with h.db.orm_session() as session:
        root = session.get(ColletRow, cid)
        assert root.collab_context_id == f"product-v2:{cid}"
        assert root.current_rollout_id == rid and root.scope == "null"
        assert root.current_snapshot is root.root_task_ref is None
        assert root.creation_facts == "{}" and root.context_version == 1
        audit = session.query(EventRow).one()
        assert audit.collet_rollout_id == rid and audit.collet_stage_rollout_id is None
        assert (
            audit.visibility == "private"
            and audit.event_kind == "product_runtime.collet.created"
        )
        assert (
            audit.primitive_type
            is audit.parent_event
            is audit.context_version_after
            is audit.task_ref
            is None
        )
    statements = []

    def capture(_conn, _cursor, statement, *_):
        statements.append(statement)

    event.listen(h.engine, "before_cursor_execute", capture)
    try:
        assert h.repo.get_collet(cid, h.owner) == history
        assert h.repo.locate(rid, "rollout", h.owner) == history
        assert h.repo.locate(stages[0].stage_rollout_id, "stage", h.owner) == history
    finally:
        event.remove(h.engine, "before_cursor_execute", capture)
    assert sum(sql == "BEGIN" for sql in statements) == 3
    assert not any(
        sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for sql in statements
    )


def test_stage_order_uses_yaml_not_insertion_order_or_id(online):
    h = online
    original = h.repo.create_collet(h.profile.id, "work", h.owner)
    with h.db.orm_session() as session:
        session.query(StageRolloutRow).delete()
        axes = h.repo._axes()
        for sid, name in [(60, "z_design"), (1, "a_build")]:
            session.add(
                StageRolloutRow(
                    **axes,
                    id=sid,
                    name=name,
                    collet_rollout_id=original.current_rollout_id,
                    status="CREATE",
                    input="work",
                    artifacts="[]",
                    acceptances="[]",
                    error_msg="",
                )
            )
    ordered = h.repo.get_collet(original.collet.id, h.owner).rollouts[0].stages
    assert [s.stage_rollout_id for s in ordered] == [60, 1]


@pytest.mark.parametrize(
    "column,value",
    [
        ("name", "unknown"),
        ("env", "wrong-env"),
        ("status", "STOPPED"),
        ("acceptances", "{}"),
        ("artifacts", "null"),
    ],
)
def test_incomplete_or_corrupt_child_fails_closed(online, column, value):
    h = online
    history = h.repo.create_collet(h.profile.id, "work", h.owner)
    with h.db.orm_session() as session:
        stage = session.query(StageRolloutRow).order_by(StageRolloutRow.id).first()
        setattr(stage, column, value)
    with pytest.raises(Invalid):
        h.repo.get_collet(history.collet.id, h.owner)


def test_event_failure_rolls_back_root_children_current_pointer(online):
    h = online

    def fail_event(_conn, _cursor, sql, *_):
        if sql.startswith("INSERT INTO ac_context_collab_event"):
            raise RuntimeError("simulated audit failure")

    event.listen(h.engine, "before_cursor_execute", fail_event)
    try:
        with pytest.raises(RuntimeError, match="audit failure"):
            h.repo.create_collet(h.profile.id, "work", h.owner)
    finally:
        event.remove(h.engine, "before_cursor_execute", fail_event)
    with h.db.orm_session() as session:
        assert all(
            session.query(model).count() == 0
            for model in [ColletRow, ColletRolloutRow, StageRolloutRow, EventRow]
        )


def test_current_pointer_cannot_reference_another_collet_rollout(online):
    h = online
    first = h.repo.create_collet(h.profile.id, "a", h.owner)
    second = h.repo.create_collet(h.profile.id, "b", h.owner)
    with pytest.raises(IntegrityError):
        with h.db.orm_session() as session:
            session.query(ColletRow).filter_by(id=first.collet.id).update(
                {"current_rollout_id": second.current_rollout_id}
            )
    assert h.repo.get_collet(first.collet.id, h.owner) == first


def test_predecessor_cannot_reference_another_collet(online):
    h = online
    first = h.repo.create_collet(h.profile.id, "a", h.owner)
    second = h.repo.create_collet(h.profile.id, "b", h.owner)
    with pytest.raises(IntegrityError):
        with h.db.orm_session() as session:
            session.query(ColletRolloutRow).filter_by(
                id=first.current_rollout_id
            ).update({"predecessor_id": second.current_rollout_id})


def test_event_stage_must_belong_to_selected_rollout(online):
    h = online
    a = h.repo.create_collet(h.profile.id, "a", h.owner)
    b = h.repo.create_collet(h.profile.id, "b", h.owner)
    with pytest.raises(IntegrityError):
        with h.db.orm_session() as session:
            audit = (
                session.query(EventRow)
                .filter_by(collet_rollout_id=a.current_rollout_id)
                .one()
            )
            audit.collet_stage_rollout_id = b.rollouts[0].stages[0].stage_rollout_id


def test_legacy_l4_reader_and_task_paths_reject_product_rows(online):
    h = online
    history = h.repo.create_collet(h.profile.id, "work", h.owner)
    with h.db.orm_session() as session:
        root = session.get(ColletRow, history.collet.id)
        with pytest.raises(Invalid, match="legacy L4"):
            _collet_from_row(root)
        with pytest.raises(Invalid, match="legacy Task"):
            load_tasks(session, root)


def test_multi_connection_concurrent_creates_are_independent(online):
    h = online
    tenant = get_current_avernet_tenant()

    def create(_):
        with avernet_tenant_scope(tenant):
            return h.repo.create_collet(h.profile.id, "same request", h.owner)

    with ThreadPoolExecutor(max_workers=4) as workers:
        histories = list(workers.map(create, range(4)))
    assert len({r.collet.id for r in histories}) == 4
    assert len({r.current_rollout_id for r in histories}) == 4
    assert all(len(r.rollouts[0].stages) == 2 for r in histories)
    with h.db.orm_session() as session:
        assert session.query(EventRow).count() == 4


def test_feedback_id_is_scoped_but_existing_index_not_cross_context_arbitration(online):
    h = online
    axes = {"avernet_tenant": get_current_avernet_tenant(), "env": get_current_env()}
    key = feedback_event_id(axes, h.owner, "Opaque-UUID")
    assert len(key) == 84 and key.isascii()
    assert key == feedback_event_id(axes, h.owner, "Opaque-UUID")
    assert key != feedback_event_id(axes, h.owner, "opaque-uuid")
    assert key != feedback_event_id(
        {**axes, "env": "different"}, h.owner, "Opaque-UUID"
    )
    assert key != feedback_event_id(
        {**axes, "avernet_tenant": "different"}, h.owner, "Opaque-UUID"
    )
    with pytest.raises(Invalid):
        feedback_event_id(axes, h.owner, "字" * 43)
    a, b = [h.repo.create_collet(h.profile.id, s, h.owner) for s in ("a", "b")]
    # Counterexample: context-scoped Event uniqueness cannot replace the old
    # actor/UUID receipt index across contexts. No feedback writer is enabled.
    with h.db.orm_session() as session:
        for history in (a, b):
            audit = (
                session.query(EventRow)
                .filter_by(collet_rollout_id=history.current_rollout_id)
                .one()
            )
            audit.event_id = key
    with h.db.orm_session() as session:
        assert session.query(EventRow).filter_by(event_id=key).count() == 2


def test_actual_community_sqlite_plugin_owns_transaction_without_nested_begin(
    business, tmp_path
):
    from agentclaw.community.core.base import Base
    from agentclaw.community.core.task.context_collab.l6_products.persistence.product_relational_models import (
        BUSINESS_TABLES,
    )
    from agentclaw.community.core.task.context_collab.l6_products.persistence.product_relational_repository import (
        SqlProductBusinessRepository,
    )
    from agentclaw.community.plugins.community.sqlite_database import (
        CommunitySqliteDatabase,
    )

    h = business
    db = CommunitySqliteDatabase(
        f"sqlite:///{tmp_path / 'real-plugin.db'}", create_schema=False
    )
    Base.metadata.create_all(db._engine, tables=BUSINESS_TABLES)
    repo = SqlProductBusinessRepository(db, h.publisher, h.grant, h.repo.authorization)
    try:
        profile = repo.create_profile("coding", h.composition, h.stages, h.owner)
        assert repo.get_profile(profile.id) == profile
        with db.orm_session() as session:
            from agentclaw.community.core.task.context_collab.l6_products.persistence.product_relational_models import (
                ProductProfileRow,
            )

            session.query(ProductProfileRow).filter_by(id=profile.id).update(
                {"status": "ONLINE"}
            )
        history = repo.create_collet(profile.id, "work", h.owner)
        assert repo.get_collet(history.collet.id, h.owner) == history
        with db.orm_session() as session:
            # Independent read through the actual plugin, not repository memory.
            assert session.query(EventRow).count() == 1
    finally:
        db._engine.dispose()
