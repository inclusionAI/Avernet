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
    with pytest.raises(ValueError, match="consume"):
        service.compile(tenant="a", expert_id="writer", engine="teclaw")
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
