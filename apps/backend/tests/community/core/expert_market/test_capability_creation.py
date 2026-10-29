"""Capability templates keep the existing Manifest grammar and fixed source."""

import json

import pytest
import yaml

from agentclaw.community.core.expert_market.creation import (
    ExpertCreationConfig,
    ExpertCreationService,
)
from agentclaw.community.core.expert_market.models import ExpertScope
from agentclaw.community.core.expert_market.scanner import ExpertScanner
from agentclaw.community.core.expert_market.service import (
    ExpertCatalogConfig,
    ExpertCatalogService,
)
from tests.community.core.expert_market import test_catalog
from tests.community.core.expert_market.test_catalog import write_expert

catalog = test_catalog.catalog


@pytest.mark.parametrize("skill_mode", ["absent", "empty", "local"])
@pytest.mark.parametrize("mcp_mode", ["absent", "empty", "explicit"])
def test_capability_template_keeps_declared_categories_and_fixed_commit(
    catalog, tmp_path, skill_mode, mcp_mode
):
    folder = write_expert(tmp_path)
    path = folder / "manifest.yaml"
    document = yaml.safe_load(path.read_text())
    manifest = document["manifest"]
    if skill_mode != "absent":
        manifest["skills"] = []
    if skill_mode == "local":
        package = folder / "skills" / "writing-review"
        package.mkdir(parents=True)
        (package / "SKILL.md").write_text(
            "---\nname: writing-review\ndescription: Review a writing draft.\n---\n"
            "Read references/checklist.md and review the draft.\n"
        )
        (package / "references").mkdir()
        (package / "references" / "checklist.md").write_text(
            "Check the intended audience."
        )
        manifest["skills"] = [
            {
                "name": "writing-review",
                "from": "aiworkbench",
                "subpath": "agents/writer/skills/writing-review",
            }
        ]
    if mcp_mode != "absent":
        manifest["mcp"] = []
    if mcp_mode == "explicit":
        manifest["mcp"] = [
            {"server_code": "project-tools", "config": {"endpoint_env": "PRE"}}
        ]
    path.write_text(yaml.safe_dump(document))
    scope = ExpertScope("a", "dev", "aiworkbench")
    result = ExpertScanner(catalog).scan(tmp_path, source_commit="b" * 40, scope=scope)
    assert result.valid == 1
    service = ExpertCreationService(
        ExpertCatalogService(catalog, ExpertCatalogConfig()),
        ExpertCreationConfig("https://example.com/experts.git", "content-reader"),
    )
    compiled = json.loads(
        service.compile(tenant="a", expert_id="writer", engine="openclaw").document
    )
    assert compiled["manifest"] == manifest
    assert compiled["sources"] == {
        "aiworkbench": {
            "protocol": "git",
            "url": "https://example.com/experts.git",
            "ref": "b" * 40,
            "mode": "strict",
            "auth": "content-reader",
        }
    }
    assert "apply_policy" not in compiled
    detail = ExpertCatalogService(catalog, ExpertCatalogConfig()).get(
        tenant="a", expert_id="writer"
    )
    assert detail["mcp"] == (
        [{"server_code": "project-tools"}] if mcp_mode == "explicit" else []
    )
    assert detail["skills"] == (
        [{"name": "writing-review", "description": "Review a writing draft."}]
        if skill_mode == "local"
        else []
    )


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "writing-review"},
        {"name": "writing-review", "content": "Inline text is not a skill package"},
        {"name": "writing-review", "skill_id": "existing-skill"},
        {
            "name": "writing-review",
            "source": {
                "protocol": "git",
                "url": "https://example.com/other.git",
                "ref": "a" * 40,
            },
        },
        {"name": "writing-review", "from": "other", "subpath": "skills/writing-review"},
    ],
)
def test_non_repository_skill_expression_cannot_replace_valid_catalogue(
    catalog, tmp_path, entry
):
    folder = write_expert(tmp_path)
    scope = ExpertScope("a", "dev", "aiworkbench")
    scanner = ExpertScanner(catalog)
    scanner.scan(tmp_path, source_commit="a" * 40, scope=scope)
    path = folder / "manifest.yaml"
    document = yaml.safe_load(path.read_text())
    document["manifest"]["skills"] = [entry]
    path.write_text(yaml.safe_dump(document))
    assert scanner.scan(tmp_path, source_commit="b" * 40, scope=scope).invalid == 1
    assert catalog.get(scope, "writer")["source_commit"] == "a" * 40


@pytest.mark.parametrize(
    "policy", [{}, {"preserve_defaults": False}, {"preserve_defaults": True}, None]
)
def test_policy_input_does_not_replace_last_valid_template(catalog, tmp_path, policy):
    folder = write_expert(tmp_path)
    scope = ExpertScope("a", "dev", "aiworkbench")
    scanner = ExpertScanner(catalog)
    scanner.scan(tmp_path, source_commit="a" * 40, scope=scope)
    path = folder / "manifest.yaml"
    document = yaml.safe_load(path.read_text())
    document["apply_policy"] = policy
    path.write_text(yaml.safe_dump(document))
    assert scanner.scan(tmp_path, source_commit="b" * 40, scope=scope).invalid == 1
    assert catalog.get(scope, "writer")["source_commit"] == "a" * 40
