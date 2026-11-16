"""Registration, catalog and conformance behavior from 03-strategy.zh-CN.md."""

from __future__ import annotations

import pytest

from agentclaw.community.core.evolution_strategy import (
    DEFAULT_CATALOG,
    Engine,
    InMemoryStrategyRegistry,
    Runtime,
    RuntimeKind,
    StrategyRegistration,
    VersionExists,
    check_allowed_genes,
    check_engine,
    check_params,
    derive_engines,
    registration_from_json,
    validate_registration,
)
from agentclaw.community.core.bot_genome.patch_models import GenomePatch


def registration(**kwargs) -> StrategyRegistration:
    values = {
        "id": "acme/correction-fixer",
        "version": "1.0.0",
        "runtime": Runtime(RuntimeKind.JOB_WORKER, image="registry/acme@sha256:" + "a" * 64),
        "needs": {"experience.sessions@1": {}},
    }
    values.update(kwargs)
    return StrategyRegistration(**values)


def test_catalog_has_versioned_entries_and_always_granted() -> None:
    assert DEFAULT_CATALOG.get("candidates@1").always_granted is True
    assert DEFAULT_CATALOG.provider("experience.sessions@1", Engine.OPENCLAW)
    assert DEFAULT_CATALOG.provider("experience.sessions@1", Engine.HERMES) is None
    assert set(DEFAULT_CATALOG.granted_for({"experience.sessions@1": {}})) >= {"candidates@1", "models@1"}


def test_runtime_requires_correct_deployment_field() -> None:
    assert Runtime(RuntimeKind.IN_PROCESS, package="acme.strategy").problems() == []
    assert Runtime(RuntimeKind.JOB_WORKER, image="registry/acme:latest").problems()
    assert Runtime(RuntimeKind.IN_PROCESS).problems()


def test_registration_rejects_unknown_capability_and_invalid_agent_args() -> None:
    problems = validate_registration(registration(needs={"unknown@1": {}}))
    assert any("unknown capability" in problem for problem in problems)
    problems = validate_registration(registration(needs={"agents@1": {}}))
    assert any("definitions" in problem for problem in problems)


def test_registration_requires_uploaded_openclaw_definition() -> None:
    record = registration(needs={"agents@1": {"definitions": {
        "tune": {"engine": "openclaw", "path": "agents/tune"}
    }}})
    problems = validate_registration(record, definitions={"tune": {"README.md": b"x"}})
    assert any("SKILL.md" in problem for problem in problems)


def test_registration_accepts_definition_and_derives_engine() -> None:
    record = registration(needs={"agents@1": {"definitions": {
        "tune": {"engine": "openclaw", "path": "agents/tune"}
    }}})
    definitions = {"tune": {"SKILL.md": b"# tune\n"}}
    assert validate_registration(record, definitions=definitions) == []
    assert derive_engines(record) == (Engine.OPENCLAW,)
    assert check_engine(record, Engine.OPENCLAW) == []
    assert check_engine(record, Engine.HERMES)


def test_params_schema_is_checked_when_binding_is_written() -> None:
    record = registration(params_schema={
        "type": "object",
        "required": ["window_days"],
        "properties": {"window_days": {"type": "integer", "minimum": 1, "maximum": 90}},
        "additionalProperties": False,
    })
    assert check_params(record, {"window_days": 7}) == []
    assert check_params(record, {"window_days": 400})
    assert check_params(record, {})


def test_allowed_genes_reject_pins_and_out_of_scope_ops() -> None:
    patch = GenomePatch.model_validate({
        "patch_schema": 1,
        "base": "sha256:" + "a" * 64,
        "ops": [{"op": "file.edit", "target": "persona/SOUL.md",
                 "edits": [{"kind": "diff", "diff": "@@ -1 +1 @@\n-a\n+b\n"}]}],
        "rationale": "fix", "evidence": ["episode:ep_1"],
    })
    assert check_allowed_genes(patch, ["persona/"], ["persona/SAFETY.md"]) == []
    assert check_allowed_genes(patch, ["skills/"], [])
    assert check_allowed_genes(patch, ["persona/"], ["persona/SOUL.md"])


def test_json_registration_round_trip() -> None:
    record = registration_from_json({
        "id": "acme/correction-fixer",
        "version": "1.0.0",
        "runtime": {"kind": "job_worker", "image": "registry/acme@sha256:" + "a" * 64},
        "needs": {"experience.sessions@1": {}},
    })
    assert record.runtime.kind is RuntimeKind.JOB_WORKER
    assert record.id == "acme/correction-fixer"


@pytest.mark.asyncio
async def test_registry_registers_immutable_definition_digest() -> None:
    registry = InMemoryStrategyRegistry()
    record = registration(needs={"agents@1": {"definitions": {
        "tune": {"engine": "openclaw", "path": "agents/tune"}
    }}})
    definitions = {"tune": {"SKILL.md": b"# tune\n"}}
    stored = await registry.register(record, definitions)
    assert stored.engines == (Engine.OPENCLAW,)
    assert stored.definition("tune").digest.startswith("sha256:")
    assert (await registry.get(record.id, record.version)).registration.needs["agents@1"]["definitions"]["tune"]["digest"]

    assert await registry.register(record, definitions) == stored
    with pytest.raises(VersionExists):
        await registry.register(record, {"tune": {"SKILL.md": b"# different\n"}})


@pytest.mark.asyncio
async def test_registry_lists_by_engine_and_can_disable() -> None:
    registry = InMemoryStrategyRegistry()
    record = registration()
    await registry.register(record, {})
    assert len(await registry.list(engine=Engine.OPENCLAW)) == 1
    assert await registry.list(engine=Engine.HERMES) == []
    await registry.set_enabled(record.id, False, reason="kill switch")
    assert (await registry.get(record.id, record.version)).enabled is False
