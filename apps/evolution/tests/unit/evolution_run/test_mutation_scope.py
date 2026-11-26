"""Scope freezing/migration and enforcement before any candidate reservation."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from avernet_genome.mutation_scope import parse_scope
from avernet_genome.patch_models import GenomePatch

from avernet_evolution.core.evolution_run import BindingCheckFailed, EvolutionStore
from avernet_evolution.core.evolution_run.api import _run_json
from avernet_evolution.core.evolution_run.submission import submit_candidate
from avernet_evolution.core.evolution_strategy import Candidate

from .ports_fixture import BOT
from .test_runs_jobs import MANUAL, binding

SCOPE = parse_scope([{"gene": "skills", "origin": "local", "include": "all", "operations": ["update"]}])


def prepare(services):
    services.registry.registration = replace(services.registry.registration, mutation_capability=SCOPE)
    parent = NS(
        revision=NS(id=services.genome.revision_id),
        spec=NS(persona=[], skills=[NS(name="first", origin="local"), NS(name="shared", origin="repo")]),
        policy=NS(mutable_genes=["skills"], pins=["skills/first/scripts"]),
    )
    services.genome.resolve_revision = AsyncMock(return_value=parent)
    return parent


@pytest.mark.asyncio
async def test_scope_freezes_parent_inventory_binding_pins_and_survives_restart(services, tmp_path):
    parent = prepare(services)
    await services.bindings.put_bindings(
        BOT, [binding(allowed_genes=[], mutation_scope=SCOPE)], expected_etag=None, actor="u"
    )
    rid = await services.runs.submit(BOT, "bind_01", idempotency_key="scope", trigger=MANUAL)
    run = await services.runs.get(BOT, rid)
    assert run.allowed_genes == ["skills/first"]
    assert run.mutation_scope.targets == ("skills/first",)
    assert run.mutation_scope.pins == ("skills/first/scripts",)
    parent.spec.skills.append(NS(name="later", origin="local"))
    parent.policy.pins.clear()
    document = await services.bindings.get_bindings(BOT)
    await services.bindings.put_bindings(
        BOT, [binding(allowed_genes=["persona"])], expected_etag=document.etag, actor="u"
    )
    assert await services.runs.submit(BOT, "bind_01", idempotency_key="scope", trigger=MANUAL) == rid
    claimed = await services.jobs.claim("worker", [run.strategy_ref])
    assert claimed.run.mutation_scope == run.mutation_scope
    assert _run_json(run)["mutation_scope"]["targets"] == ["skills/first"]
    # Close/reopen a real SQLite copy, not a round trip through an in-memory codec.
    import sqlite3

    path = tmp_path / "restart.db"
    copy = sqlite3.connect(path)
    services.store.conn.backup(copy)
    copy.close()
    reopened = EvolutionStore(path)
    try:
        assert reopened.get_run(rid).mutation_scope == run.mutation_scope
    finally:
        reopened.conn.close()


@pytest.mark.asyncio
async def test_empty_scope_rejected_before_enqueue_or_budget_reservation(services):
    parent = prepare(services)
    parent.policy.mutable_genes = ["persona"]
    services.budget.reserve = AsyncMock()
    await services.bindings.put_bindings(
        BOT, [binding(allowed_genes=[], mutation_scope=SCOPE)], expected_etag=None, actor="u"
    )
    with pytest.raises(BindingCheckFailed, match="no authorized"):
        await services.runs.submit(BOT, "bind_01", idempotency_key="empty", trigger=MANUAL)
    services.budget.reserve.assert_not_called()
    assert not services.store.conn.execute("SELECT * FROM evolution_run").fetchall()


@pytest.mark.asyncio
async def test_new_selector_cannot_silently_use_legacy_strategy(services):
    with pytest.raises(BindingCheckFailed, match="mutation_capability"):
        await services.bindings.put_bindings(
            BOT, [binding(allowed_genes=[], mutation_scope=SCOPE)], expected_etag=None, actor="u"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "op",
    [
        {"op": "skill.add", "name": "first", "files": {"SKILL.md": "new"}},
        {"op": "skill.remove", "name": "first"},
        {"op": "skill.update", "name": "shared", "files": {"SKILL.md": "overwrite"}},
        {"op": "skill.update", "name": "first", "files": {"SKILL.md": "good", "scripts/a.py": "pinned"}},
    ],
)
async def test_submit_refuses_invalid_ops_before_reserving_candidate(services, op):
    prepare(services)
    await services.bindings.put_bindings(
        BOT, [binding(allowed_genes=[], mutation_scope=SCOPE)], expected_etag=None, actor="u"
    )
    rid = await services.runs.submit(BOT, "bind_01", idempotency_key="bad", trigger=MANUAL)
    run = await services.runs.get(BOT, rid)
    patch = GenomePatch.model_validate(
        {
            "patch_schema": 1,
            "base": run.parent_revision,
            "ops": [op],
            "rationale": "why",
            "evidence": ["episode:e1"],
        }
    )
    with pytest.raises(ValueError, match="frozen mutation_scope"):
        await submit_candidate(
            store=services.store,
            genome=services.genome,
            run=run,
            candidate=Candidate(patch, patch.rationale, tuple(patch.evidence)),
        )
    assert not services.store.conn.execute("SELECT * FROM evolution_run_candidate").fetchall()
    assert not services.genome.committed


@pytest.mark.asyncio
async def test_legacy_database_migration_preserves_null_scope(tmp_path, services):
    path = tmp_path / "legacy.db"
    await services.bindings.put_bindings(BOT, [binding()], expected_etag=None, actor="u")
    rid = await services.runs.submit(BOT, "bind_01", idempotency_key="legacy", trigger=MANUAL)
    before = services.store.get_run(rid)
    store = EvolutionStore(path)
    services.store.conn.backup(store.conn)
    store.conn.execute("ALTER TABLE evolution_run DROP COLUMN mutation_scope")
    store.conn.commit()
    store.conn.close()
    migrated = EvolutionStore(path)
    try:
        assert migrated.get_run(rid) == before
        assert migrated.get_run(rid).mutation_scope is None
        assert "mutation_scope" in {
            row["name"] for row in migrated.conn.execute("PRAGMA table_info(evolution_run)")
        }
    finally:
        migrated.conn.close()
