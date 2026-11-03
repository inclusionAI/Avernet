"""Expert identity through real Apply, compose and Teclaw draft provisioning.

This is a code contract test. The external Teclaw runtime is not executed here.
"""
from __future__ import annotations

import json

import pytest
import yaml

from agentclaw.community.core.apply_engine.capabilities import Category, resolve_capabilities
from agentclaw.community.core.apply_engine.order import ApplyPhase
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import MANIFEST_STATE
from agentclaw.community.core.bot_config_manifest.creation import CREATE_PRE_CONTAINER_TRIGGER, preflight_creation_manifest
from agentclaw.community.core.bot_config_manifest.schema import validate_document
from agentclaw.community.core.expert_market.creation import ExpertCreationConfig, ExpertCreationService
from agentclaw.community.core.expert_market.models import ExpertScope
from agentclaw.community.core.expert_market.scanner import ExpertScanner
from agentclaw.community.core.expert_market.service import ExpertCatalogConfig, ExpertCatalogService
from tests.community.core.bot_config_manifest.creation import test_first_artifact as first
from tests.community.core.expert_market import test_catalog
from tests.community.core.expert_market.test_catalog import write_expert

world = first.world
catalog = test_catalog.catalog


@pytest.mark.parametrize("bot_type", ["personal", "service"])
@pytest.mark.parametrize("profile", ["我是产品专家。\n", ""])
def test_native_expert_identity_is_present_in_the_first_teclaw_draft_artifact(
    catalog, world, tmp_path, monkeypatch, bot_type, profile
):
    bodies = {
        "AGENTS.md": "# 约束\n保持原样\n",
        "SOUL.md": "# 人设\r\n包含原始换行\r\n",
        "IDENTITY.md": profile,
        "TOOLS.md": "工具由既有平台管理。\n",
    }
    folder = write_expert(tmp_path)
    (folder / "manifest.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "manifest": {"identity": [{"type": name, "content": content} for name, content in bodies.items()]},
    }, allow_unicode=True))
    scope = ExpertScope("a", "dev", "aiworkbench")
    assert ExpertScanner(catalog).scan(tmp_path, source_commit="a" * 40, scope=scope).valid == 1
    compiled = ExpertCreationService(
        ExpertCatalogService(catalog, ExpertCatalogConfig()),
        ExpertCreationConfig("https://example.com/experts.git"),
    ).compile(tenant="a", expert_id="writer", engine="teclaw")

    def validate(*, document, active_engine, bot_type):
        return validate_document(document, resolve_capabilities(
            active_engine=active_engine, bot_type=bot_type, is_teclaw=lambda engine: engine == "teclaw"
        ))

    preflight_creation_manifest(
        document=compiled.document, engine_type="teclaw", bot_type=bot_type,
        validate=validate, materialised=frozenset({Category.IDENTITY}),
    )
    monkeypatch.setattr(first, "_DOCUMENT", compiled.document)
    monkeypatch.setitem(first._RECORD, "bot_type", bot_type)
    applies, provisioner, baas, store = first._build(world)
    accepted = applies.start_apply(
        entity_id=first._ENTITY, bot_id=first._BOT, bot=dict(first._RECORD),
        owner_id=first._OWNER, actor_id=first._OWNER, state=MANIFEST_STATE,
        trigger=CREATE_PRE_CONTAINER_TRIGGER, phase=ApplyPhase.PRE_CONTAINER,
    )
    report = applies.last_apply(entity_id=first._ENTITY, bot_id=first._BOT)
    assert report is not None and report.apply_id == accepted.apply_id
    assert report.status is ApplyStatus.SUCCEEDED
    provisioner.provision(bot=dict(first._RECORD), owner_id=first._OWNER)
    artifact = baas.create_teclaw_bot.call_args.kwargs["config_artifact"]
    assert artifact["engine_type"] == "teclaw"
    assert artifact["version"] is None
    assert artifact["engine_ext"]["stage"] == "draft"
    assert artifact["ownership"]["identity_files"] == "platform"
    assert {ref["name"] for ref in artifact["identity_files"]} == {name for name, content in bodies.items() if content}
    assert all(ref["name"] != "MEMORY.md" for ref in artifact["identity_files"])
    for ref in artifact["identity_files"]:
        assert ref["store"] == "bot-data"
        assert ref["path"] == f"{first._REF_ROOT}/identity/{ref['name']}"
        assert store._oss.get_object(f"{first._BASE}/{ref['path']}") == bodies[ref["name"]].encode("utf-8")
    assert json.loads(compiled.document)["manifest"]["identity"][2]["type"] == "IDENTITY.md"
