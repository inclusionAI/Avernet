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
from tests.community.core.expert_market.test_catalog import write_expert


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
