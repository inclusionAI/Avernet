"""Exercise the real repository/tenant guard and count SQLite VM work, not rows."""

from pathlib import Path

import pytest
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from sqlalchemy import event, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex

INDEX = "idx_bot_knowledge_candidates"
COLUMNS = ["avernet_tenant", "env", "space_id", "is_delete", "id"]


def _seed(world, count, **scope):
    # Core inserts are fixture setup only; spell out tenant since they bypass
    # the ORM before_insert stamp. Reads below use the unmodified ORM guard.
    values = {
        "owner_id": "owner",
        "entity_id": "owner",
        "entity_type": "user",
        "creator_id": "owner",
        "avernet_tenant": "teamclaw",
        "env": "pre",
        "space_id": 2,
        "is_delete": 0,
    } | scope
    with world.engine.begin() as connection:
        connection.execute(
            BotModel.__table__.insert(),
            [values | {"bot_id": f"noise-{i}"} for i in range(count)],
        )


def _measured_page(world, *, space_id=1, cursor=0, limit=1, keyword=""):
    captured = []

    def capture(_conn, _cursor, statement, parameters, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            captured.append((statement, parameters))

    steps = 0

    def step():
        nonlocal steps
        steps += 1
        return 0

    raw = world.engine.raw_connection()
    raw.driver_connection.set_progress_handler(step, 1)
    event.listen(world.engine, "before_cursor_execute", capture)
    try:
        page = world.source.candidates(
            space_id, cursor=cursor, limit=limit, keyword=keyword
        )
    finally:
        raw.driver_connection.set_progress_handler(None, 0)
        event.remove(world.engine, "before_cursor_execute", capture)
    try:
        # One point lookup for personal-space ownership, then one Team branch
        # or two disjoint Personal branches. Every Bot query is independently
        # seek/limit bounded, including the real injected tenant predicate.
        bot_queries = [
            (sql, params) for sql, params in captured if "FROM ac_bots" in sql
        ]
        assert 1 <= len(bot_queries) <= 2
        assert len(captured) == len(bot_queries) + 1
        plan = []
        for statement, parameters in bot_queries:
            assert "ac_bots.avernet_tenant = ?" in statement
            assert "ac_bots.id > ?" in statement
            assert " LIMIT ?" in statement
            plan.extend(
                raw.driver_connection.execute(
                    "EXPLAIN QUERY PLAN " + statement, parameters
                ).fetchall()
            )
    finally:
        raw.close()
    return page, steps, " ".join(row[3] for row in plan)


def test_candidate_scope_index_schema():
    index = next((i for i in BotModel.__table__.indexes if i.name == INDEX), None)
    assert index is not None, "create_all must create the candidate scope index"
    assert [column.name for column in index.columns] == COLUMNS
    assert not index.unique  # must not replace or tighten Bot identity uniqueness


@pytest.mark.parametrize("count", [1_000, 10_000, 100_000])
def test_empty_candidate_page_does_not_scan_unrelated_spaces(world, count):
    _seed(world, count)
    page, steps, plan = _measured_page(world, space_id=99)
    assert page == {"items": [], "next_cursor": None}
    print(f"candidate-empty noise={count} steps={steps} plan={plan}")
    assert f"SEARCH ac_bots USING INDEX {INDEX}" in plan
    assert "avernet_tenant=? AND env=? AND space_id=? AND is_delete=?" in plan
    assert steps < 500


@pytest.mark.parametrize(
    "scope",
    [
        {"space_id": 1, "avernet_tenant": "other"},
        {"space_id": 1, "env": "prod"},
        {"space_id": 1, "is_delete": 1},
        {"space_id": 2},
    ],
)
def test_sparse_candidate_cursor_and_scope_do_not_scan_noise(world, scope):
    # Also model deployments that already carry the older single-space index.
    with world.engine.begin() as connection:
        connection.exec_driver_sql("CREATE INDEX idx_space_id ON ac_bots (space_id)")
    _seed(world, 10_000, **scope)
    first, steps, plan = _measured_page(world)
    assert [item["owner_id"] for item in first["items"]] == ["owner"]
    assert first["next_cursor"] == 1
    assert steps < 1_000
    assert INDEX in plan
    second, steps, plan = _measured_page(world, cursor=first["next_cursor"])
    assert [item["owner_id"] for item in second["items"]] == ["other-owner"]
    assert second["next_cursor"] is None
    assert steps < 1_000
    assert "USE TEMP B-TREE" not in plan
    print(f"candidate-sparse scope={scope} steps={steps} plan={plan}")


def test_candidate_keyword_and_tenant_guard_keep_the_scope_index(world):
    _seed(world, 10_000)
    page, steps, plan = _measured_page(world, keyword="Existing nickname")
    assert [item["bot_id"] for item in page["items"]] == ["bot"]
    assert page["next_cursor"] is None
    assert steps < 1_000
    assert INDEX in plan
    with avernet_tenant_scope("other"):
        page, steps, plan = _measured_page(world)
    assert page == {"items": [], "next_cursor": None}
    assert steps < 500
    assert INDEX in plan


def test_operator_migration_repairs_existing_table_with_legacy_space_index(world):
    index = next(i for i in BotModel.__table__.indexes if i.name == INDEX)
    index.drop(world.engine)
    with world.engine.begin() as connection:
        connection.exec_driver_sql("CREATE INDEX idx_space_id ON ac_bots (space_id)")
    _seed(world, 10_000, space_id=1, avernet_tenant="other")
    before, before_steps, before_plan = _measured_page(world, cursor=2)
    assert before == {"items": [], "next_cursor": None}
    assert INDEX not in before_plan
    assert before_steps > 20_000

    source = Path(__file__).resolve().parents[3] / "src/agentclaw/community"
    migration = source / "core/knowledge_management/sql/2026_10_28_bot_candidates.sql"
    ddl = (
        "\n".join(
            line
            for line in migration.read_text().splitlines()
            if not line.lstrip().startswith("--")
        )
        .strip()
        .rstrip(";")
    )
    assert " ".join(ddl.split()) == " ".join(
        str(CreateIndex(index).compile(dialect=mysql.dialect())).split()
    )
    with world.engine.begin() as connection:
        connection.exec_driver_sql(ddl)
    actual = next(
        i for i in inspect(world.engine).get_indexes("ac_bots") if i["name"] == INDEX
    )
    assert actual["column_names"] == COLUMNS
    assert not actual["unique"]
    assert inspect(world.engine).get_unique_constraints("ac_bots")[0]["name"] == (
        "uk_bot_id_entity_id_env_tenant"
    )
    after, after_steps, after_plan = _measured_page(world, cursor=2)
    assert after == before
    assert INDEX in after_plan
    assert after_steps < 500
    print(f"candidate-existing-table before={before_steps} after={after_steps}")


@pytest.mark.parametrize("count", [1_000, 10_000, 100_000])
def test_personal_legacy_candidates_do_not_scan_other_spaces(world, count):
    from agentclaw.community.core.spaces.repository.models import SpaceModel

    _seed(world, count)
    with world.db.orm_session() as session:
        space = session.get(SpaceModel, 1)
        space.space_type, space.personal_owner_id = "PERSONAL", "owner"
        session.get(BotModel, 1).space_id = None
        session.get(BotModel, 2).space_id = None  # Another Owner must stay hidden.
    page, steps, plan = _measured_page(world)
    assert [bot["owner_id"] for bot in page["items"]] == ["owner"]
    assert page["next_cursor"] is None
    assert steps < 1_000
    assert plan.count(f"SEARCH ac_bots USING INDEX {INDEX}") == 2
    assert "USE TEMP B-TREE" not in plan
    print(f"candidate-personal-legacy noise={count} steps={steps} plan={plan}")


def test_team_candidates_do_not_scan_unassigned_personal_bots(world):
    _seed(world, 10_000, space_id=None, owner_id="unrelated-owner")
    page, steps, plan = _measured_page(world, limit=20)
    assert [bot["owner_id"] for bot in page["items"]] == ["owner", "other-owner"]
    assert steps < 1_000
    assert INDEX in plan
