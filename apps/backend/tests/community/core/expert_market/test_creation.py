"""Expert compilation at the catalogue-to-Manifest public seam."""

import asyncio
import json
import pytest

from agentclaw.community.core.expert_market.creation import (
    ExpertCreationService,
    ExpertCreationConfig,
)
from agentclaw.community.core.expert_market.models import ExpertScope
from agentclaw.community.core.expert_market.repository import ExpertCatalogRepository
from agentclaw.community.core.expert_market.scanner import ExpertScanner
from agentclaw.community.core.expert_market.service import (
    ExpertCatalogConfig,
    ExpertCatalogService,
)
from agentclaw.community.plugins.local.database import SqliteDB, reset_for_tests
from tests.community.core.expert_market import test_catalog
from tests.community.core.expert_market.test_catalog import write_expert

catalog = test_catalog.catalog


def test_identity_template_binds_latest_valid_full_commit_and_source(tmp_path):
    reset_for_tests()
    db = SqliteDB()
    asyncio.run(db.bootstrap())
    catalog = ExpertCatalogRepository(db)
    write_expert(tmp_path, text="保持原文\n第二行")
    ExpertScanner(catalog).scan(
        tmp_path, source_commit="a" * 40, scope=ExpertScope("a", "dev", "aiworkbench")
    )
    service = ExpertCreationService(
        ExpertCatalogService(catalog, ExpertCatalogConfig()),
        ExpertCreationConfig("https://example.com/experts.git", "catalog-credential"),
    )
    compiled = service.compile(tenant="a", expert_id="writer", engine="openclaw")
    doc = json.loads(compiled.document)
    assert doc["sources"]["aiworkbench"] == {
        "protocol": "git",
        "url": "https://example.com/experts.git",
        "ref": "a" * 40,
        "mode": "strict",
        "auth": "catalog-credential",
    }
    assert "cli_tools" not in doc["manifest"]
    assert doc["manifest"]["identity"][0]["subpath"] == "agents/writer/AGENTS.md"
    assert compiled.origin.expert_name == "写作专家"
    assert compiled.origin.source_commit == "a" * 40
    assert len(compiled.origin.manifest_digest) == 64
    claude = json.loads(
        service.compile(tenant="a", expert_id="writer", engine="claude_code").document
    )
    assert claude["manifest"]["identity"] == [
        {"type": "CLAUDE.md", "content": "# AGENTS.md\n\n保持原文\n第二行"}
    ]
    teclaw = json.loads(service.compile(tenant="a", expert_id="writer", engine="teclaw").document)
    assert teclaw["manifest"]["identity"] == doc["manifest"]["identity"]
    reset_for_tests()


@pytest.mark.parametrize("policy", [{}, {"preserve_defaults": False}, None])
def test_historical_policy_template_is_refused_without_rewriting(catalog, tmp_path, policy):
    """An old effective record must not silently change its install semantics."""
    scope = ExpertScope("a", "dev", "aiworkbench")
    write_expert(tmp_path)
    ExpertScanner(catalog).scan(tmp_path, source_commit="a" * 40, scope=scope)
    historical = catalog.get(scope, "writer")
    historical["manifest_template"]["apply_policy"] = policy
    catalog.reconcile(scope, "a" * 40, {"writer": historical}, {}, {"writer"})
    service = ExpertCreationService(
        ExpertCatalogService(catalog, ExpertCatalogConfig()),
        ExpertCreationConfig("https://example.com/experts.git"),
    )
    with pytest.raises(ValueError, match="apply_policy.*not supported"):
        service.compile(tenant="a", expert_id="writer", engine="openclaw")
    assert catalog.get(scope, "writer")["manifest_template"] == historical["manifest_template"]


def test_multifile_identity_keeps_declared_order_and_native_source_bytes(
    catalog, tmp_path
):
    import yaml

    folder = write_expert(tmp_path)
    bodies = {
        "SOUL.md": "灵魂原文\n\n# 内部标题\n末尾\n",
        "IDENTITY.md": "身份原文：不改写。",
        "AGENTS.md": "规则原文\r\n第二行\n",
    }
    entries = []
    for filename, body in bodies.items():
        (folder / filename).write_bytes(body.encode("utf-8"))
        entries.append(
            {
                "type": filename,
                "from": "aiworkbench",
                "subpath": "agents/writer/" + filename,
            }
        )
    (folder / "manifest.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "manifest": {"identity": entries}})
    )
    scope = ExpertScope("a", "dev", "aiworkbench")
    scanned = ExpertScanner(catalog).scan(tmp_path, source_commit="a" * 40, scope=scope)
    assert scanned.valid == 1
    service = ExpertCreationService(
        ExpertCatalogService(catalog, ExpertCatalogConfig()),
        ExpertCreationConfig("https://example.com/experts.git"),
    )
    for engine in ("openclaw", "teclaw"):
        document = json.loads(
            service.compile(tenant="a", expert_id="writer", engine=engine).document
        )
        assert document["manifest"]["identity"] == entries
    claude = json.loads(
        service.compile(tenant="a", expert_id="writer", engine="claude_code").document
    )
    assert claude["manifest"]["identity"] == [
        {
            "type": "CLAUDE.md",
            "content": "# SOUL.md\n\n灵魂原文\n\n# 内部标题\n末尾\n\n\n# IDENTITY.md\n\n身份原文：不改写。\n\n# AGENTS.md\n\n规则原文\r\n第二行\n",
        }
    ]
    assert (
        catalog.get(scope, "writer")["manifest_template"]["manifest"]["identity"]
        == entries
    )


def test_claude_multifile_expansion_obeys_existing_creation_document_limit(
    catalog, tmp_path
):
    from agentclaw.community.core.bot_config_manifest.creation import (
        preflight_creation_manifest,
    )
    from agentclaw.community.core.bot_config_manifest.schema import (
        ManifestTooLargeError,
        validate_document,
    )
    from agentclaw.community.core.apply_engine.capabilities import (
        Category,
        resolve_capabilities,
    )

    folder = write_expert(tmp_path, text="文" * 12000)
    (folder / "SOUL.md").write_text("魂" * 12000)
    with (folder / "manifest.yaml").open("a") as manifest:
        manifest.write(
            "    - type: SOUL.md\n      from: aiworkbench\n      subpath: agents/writer/SOUL.md\n"
        )
    scope = ExpertScope("a", "dev", "aiworkbench")
    assert (
        ExpertScanner(catalog).scan(tmp_path, source_commit="a" * 40, scope=scope).valid
        == 1
    )
    service = ExpertCreationService(
        ExpertCatalogService(catalog, ExpertCatalogConfig()),
        ExpertCreationConfig("https://example.com/experts.git"),
    )
    compiled = service.compile(tenant="a", expert_id="writer", engine="claude_code")

    def validate(*, document, active_engine, bot_type):
        return validate_document(
            document,
            resolve_capabilities(
                active_engine=active_engine,
                bot_type=bot_type,
                is_teclaw=lambda _: False,
            ),
        )

    with pytest.raises(ManifestTooLargeError):
        preflight_creation_manifest(
            document=compiled.document,
            engine_type="claude_code",
            bot_type="personal",
            validate=validate,
            materialised=frozenset({Category.IDENTITY}),
        )


@pytest.mark.parametrize("declaration", [[], [{"name": "review-cli"}]])
def test_compile_refuses_cli_even_if_catalogue_input_bypasses_scanner(catalog, tmp_path, declaration):
    scope = ExpertScope("a", "dev", "aiworkbench")
    write_expert(tmp_path)
    ExpertScanner(catalog).scan(tmp_path, source_commit="a" * 40, scope=scope)
    value = catalog.get(scope, "writer")
    value["manifest_template"]["manifest"]["cli_tools"] = declaration
    catalog.reconcile(scope, "a" * 40, {"writer": value}, {}, {"writer"})
    service = ExpertCreationService(
        ExpertCatalogService(catalog, ExpertCatalogConfig()),
        ExpertCreationConfig("https://example.com/experts.git"),
    )
    with pytest.raises(ValueError, match="cli_tools.*not supported"):
        service.compile(tenant="a", expert_id="writer", engine="openclaw")
    assert catalog.get(scope, "writer")["manifest_template"]["manifest"]["cli_tools"] == declaration
