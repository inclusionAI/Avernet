"""SQLite VM work bounds, not timing or production/OceanBase benchmarks."""

import json

from sqlalchemy import event

from agentclaw.community.core.bot_knowledge.repository.store import (
    BotKnowledgeRepository,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel,
    PublishStatus,
)
from agentclaw.community.kernel.knowledge import BotKnowledgeScope
from tests.community.repository.publishing.test_knowledge_selection_lifecycle import (
    Binding,
    _start_online,
    publication as publication_fixture,
)
from tests.community.repository.publishing.test_knowledge_observed_completion import (
    space,
)
from tests.community.repository.publishing.test_knowledge_short_transaction import (
    finish,
)

publication = publication_fixture


def measure(engine, action):
    steps = [0]
    statements = []

    def tick():
        steps[0] += 1
        return 0

    def checkout(connection, *_args):
        connection.set_progress_handler(tick, 1)

    def checkin(connection, *_args):
        if connection is not None:
            connection.set_progress_handler(None, 0)

    def capture(_connection, _cursor, sql, params, _ctx, many):
        statements.append((sql, params, many))

    event.listen(engine.pool, "checkout", checkout)
    event.listen(engine.pool, "checkin", checkin)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = action()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        event.remove(engine.pool, "checkout", checkout)
        event.remove(engine.pool, "checkin", checkin)
    plans = []
    with engine.connect() as connection:
        for sql, params, many in statements:
            if sql.startswith(("SELECT", "UPDATE")):
                plans.append(
                    dict(
                        sql=sql,
                        batch_size=len(params) if many else 1,
                        plan=[
                            tuple(row)
                            for row in connection.exec_driver_sql(
                                "EXPLAIN QUERY PLAN " + sql,
                                params[0] if many else params,
                            )
                        ],
                    )
                )
    return result, dict(vm_steps=steps[0], statements=len(statements), plans=plans)


def test_current_work_is_bounded_as_publish_and_disabled_binding_history_grows(
    publication, tmp_path
):
    db, repo, record = publication
    space(db)
    engine = db.factory.kw["bind"]
    finish(repo, _start_online(repo, record))
    reader = BotKnowledgeRepository(db)
    scope = BotKnowledgeScope("service", "owner", "dev")
    evidence = []
    for size in (1, 1000, 10000):
        with db.orm_session() as session:
            session.execute(
                BotPublishModel.__table__.insert(),
                [
                    dict(
                        source_bot_pk=1,
                        source_bot_id="service",
                        publish_bot_id="service.pub",
                        owner_id="owner",
                        permission_owner="owner",
                        name="History",
                        env="dev",
                        version=-i,
                        status=PublishStatus.UPGRADED,
                        ext=json.dumps(
                            {
                                "knowledge_selection_result": {
                                    "status": "applied",
                                    "deployment_id": i,
                                }
                            }
                        ),
                    )
                    for i in range(size)
                ],
            )
            session.execute(
                Binding.__table__.insert(),
                [
                    dict(
                        bot_pk=1,
                        env="dev",
                        space_id=9,
                        kb_id=f"disabled-{size}-{i}",
                        enabled=False,
                        online_enabled=False,
                        revision=1,
                        updated_by="owner",
                    )
                    for i in range(size)
                ],
            )
            session.query(Binding).filter(Binding.kb_id.in_(["A", "B"])).update(
                {"enabled": False}
            )
            session.query(Binding).filter_by(kb_id="A" if size != 1000 else "B").update(
                {"enabled": True}
            )
        current = repo.insert(
            dict(
                source_bot_pk=1,
                source_bot_id="service",
                publish_bot_id="service.pub",
                owner_id="owner",
                permission_owner="owner",
                name="Current",
                env="dev",
                version=size + 2,
                status=PublishStatus.DRAFT,
            )
        )
        online = _start_online(repo, current)
        _, reads = measure(
            engine, lambda: reader.list_enabled(scope, selection_stage="online")
        )
        _, page = measure(
            engine,
            lambda: reader.list_bindings(
                scope=scope,
                actor_id="owner",
                selection_stage="online",
            ),
        )
        completed, completion = measure(
            engine,
            lambda: repo.compare_and_set_status_with_ext(
                publish_id=current.id,
                source_status=PublishStatus.ONLINE_PUB,
                target_status=PublishStatus.SUCCESS,
                expected_ext=online.ext,
                ext=online.ext,
                observed_workflow_id=size + 100,
            ),
        )
        assert completed.status == PublishStatus.SUCCESS
        evidence.append(
            dict(added_history=size, reader=reads, page=page, completion=completion)
        )
    (tmp_path / "current-summary-cost.json").write_text(json.dumps(evidence, indent=2))
    for path in ("reader", "page", "completion"):
        work = [row[path]["vm_steps"] for row in evidence]
        # Generous fixed VM allowance allows planner/row-id variance, but detects
        # both old O(H) JSON scans and O(B) clear UPDATE (90k+ at this volume).
        assert max(work) < 12000, (path, work)
        assert max(work) - min(work) < 2500, (path, work)
        for row in evidence:
            for plan in row[path]["plans"]:
                if "FROM ac_bot_publish" in plan["sql"]:
                    assert "WHERE ac_bot_publish.id =" in plan["sql"]
                if plan["sql"].startswith("UPDATE ac_bot_knowledge_binding"):
                    assert "WHERE ac_bot_knowledge_binding.id =" in plan["sql"]
    print(
        "CURRENT_SUMMARY_VM_STEPS",
        {
            p: [r[p]["vm_steps"] for r in evidence]
            for p in ("reader", "page", "completion")
        },
    )


def test_hundred_id_validation_clear_set_and_unchanged_completion_use_key_seeks(
    publication, tmp_path
):
    db, repo, record = publication
    engine = db.factory.kw["bind"]
    with db.orm_session() as session:
        session.query(Binding).delete()
        session.add_all(
            [
                Binding(
                    bot_pk=1,
                    space_id=9,
                    env="dev",
                    kb_id=f"{group}-{i:03d}",
                    enabled=group == "left",
                    online_enabled=False,
                    revision=1,
                    updated_by="owner",
                )
                for group in ("left", "right")
                for i in range(100)
            ]
        )
    finish(repo, _start_online(repo, record))
    evidence = []
    counter = 2
    for noise in (1, 1000, 10000):
        with db.orm_session() as session:
            session.execute(
                Binding.__table__.insert(),
                [
                    dict(
                        bot_pk=1,
                        space_id=9,
                        env="dev",
                        kb_id=f"noise-{noise}-{i}",
                        enabled=False,
                        online_enabled=False,
                        revision=1,
                        updated_by="owner",
                    )
                    for i in range(noise)
                ],
            )
            session.query(Binding).update({"enabled": False})
            group = "left" if noise == 1000 else "right"
            session.query(Binding).filter(Binding.kb_id.like(group + "-%")).update(
                {"enabled": True}
            )
        stage = dict(added_disabled=noise)
        for mode in ("replacement", "unchanged"):
            current = repo.insert(
                dict(
                    source_bot_pk=1,
                    source_bot_id="service",
                    publish_bot_id="service.pub",
                    owner_id="owner",
                    permission_owner="owner",
                    name="Current",
                    env="dev",
                    version=counter,
                    status=PublishStatus.DRAFT,
                )
            )
            online = _start_online(repo, current)
            completed, work = measure(
                engine,
                lambda: repo.compare_and_set_status_with_ext(
                    publish_id=current.id,
                    source_status=PublishStatus.ONLINE_PUB,
                    target_status=PublishStatus.SUCCESS,
                    expected_ext=online.ext,
                    ext=online.ext,
                    observed_workflow_id=counter + 100,
                ),
            )
            assert completed.status == PublishStatus.SUCCESS
            counter += 1
            stage[mode] = work
            with db.orm_session() as session:
                active = {
                    r[0]
                    for r in session.query(Binding.kb_id).filter(
                        Binding.online_enabled.is_(True)
                    )
                }
            assert active == {f"{group}-{i:03d}" for i in range(100)}
            updates = [
                p
                for p in work["plans"]
                if p["sql"].startswith("UPDATE ac_bot_knowledge_binding")
            ]
            if mode == "unchanged":
                assert updates == []
            else:
                assert len(updates) == 2
                for p in updates:
                    assert p["batch_size"] == 100
                    assert all("INTEGER PRIMARY KEY" in row[-1] for row in p["plan"])
        evidence.append(stage)
    (tmp_path / "hundred-id-cost.json").write_text(json.dumps(evidence, indent=2))
    for mode in ("replacement", "unchanged"):
        work = [r[mode]["vm_steps"] for r in evidence]
        assert max(work) < 50000, (mode, work)
        assert max(work) - min(work) < 5000, (mode, work)
    print(
        "HUNDRED_ID_VM_STEPS",
        {m: [r[m]["vm_steps"] for r in evidence] for m in ("replacement", "unchanged")},
    )
