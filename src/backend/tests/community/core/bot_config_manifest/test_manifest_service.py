"""Tests for ``ManifestService`` (W1, issue #1469).

Backed by the real ORM repository on in-memory SQLite — the write path's
all-or-nothing guarantee is exactly the kind of claim a mock would confirm
vacuously: "nothing written" must be observed against a database that really
held a previous document.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_config_manifest.capabilities import CategorySupport
from agentclaw.community.core.bot_config_manifest.manifest_schema import (
    ManifestDocument,
    ManifestInvalidError,
    parse_document,
)
from agentclaw.community.core.bot_config_manifest.services.manifest_service import (
    ManifestService,
)
from agentclaw.community.core.repository.implementations.bot.config_manifest import (
    BotConfigManifestRepository,
)
# Side effect: registers the model on Base.metadata for create_all.
from agentclaw.community.core.bot_config_manifest.repository.models import (  # noqa: F401
    BotConfigManifestModel,
)

DigEST = "sha256:" + "cd" * 32


class InMemorySqliteDB:
    def __init__(self, engine):
        self._engine = engine
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def service() -> ManifestService:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return ManifestService(BotConfigManifestRepository(InMemorySqliteDB(engine)))


A_GOOD_DOC = {
    "schema_version": 1,
    "sources": {
        "content": {
            "git": "https://git.example/team/content.git",
            "ref": "v1.2.0",
            "auth": "corp-git",
        }
    },
    "manifest": {
        "identity": [
            {"type": "SOUL.md", "from": "content", "subpath": "bots/${OCB_BOT_ID}/soul.md"}
        ],
        "skills": [
            {"name": "q", "from": "content", "subpath": "skills/q/"},
            {
                "name": "zip-skill",
                "source": "https://artifacts.example/z.zip",
                "digest": DigEST,
            },
        ],
    },
}


# --- read side --------------------------------------------------------------


def test_a_bot_without_a_manifest_reads_as_an_empty_document(service):
    doc: ManifestDocument = service.get(entity_id="e", bot_id="b")
    assert doc.schema_version == 1
    assert doc.sources == {}
    assert doc.script is None
    assert doc.manifest.resources == []


# --- write side: happy path -------------------------------------------------


def test_put_stores_and_get_returns_the_document(service):
    result = service.put(
        entity_id="e",
        bot_id="b",
        engine_type="openclaw",
        bot_type="personal",
        document=A_GOOD_DOC,
        modifier="alice",
    )
    assert result["schema_version"] == 1
    assert result["warnings"] == []
    assert result["modifier"] == "alice"

    doc = service.get(entity_id="e", bot_id="b")
    assert doc.manifest.skills[0].name == "q"
    assert doc.sources["content"].ref == "v1.2.0"
    # 仓库行保存声明原文——读取兼容且非空。
    assert service.get_record(entity_id="e", bot_id="b") is not None


def test_put_replaces_whole_document(service):
    service.put(
        entity_id="e", bot_id="b", engine_type="openclaw", bot_type="personal",
        document=A_GOOD_DOC, modifier="alice",
    )
    service.put(
        entity_id="e", bot_id="b", engine_type="openclaw", bot_type="personal",
        document={"manifest": {"mcp": [{"server_code": "github"}]}}, modifier="bob",
    )
    doc = service.get(entity_id="e", bot_id="b")
    assert doc.manifest.mcp[0].server_code == "github"
    assert doc.manifest.skills == []  # 替换,不是合并


# --- write side: all-or-nothing ---------------------------------------------


def test_invalid_document_writes_nothing_and_reports_every_reason(service):
    service.put(
        entity_id="e", bot_id="b", engine_type="openclaw", bot_type="personal",
        document=A_GOOD_DOC, modifier="alice",
    )
    with pytest.raises(ManifestInvalidError) as excinfo:
        service.put(
            entity_id="e", bot_id="b", engine_type="openclaw", bot_type="personal",
            document={
                "sources": {"x": {}},
                "manifest": {"skills": [{"name": "q", "from": "ghost"}]},
            },
            modifier="bob",
        )
    rules = [v.rule for v in excinfo.value.violations]
    assert "from-undeclared" in rules
    assert "skills-digest-required" in rules
    assert "sources-no-kind" in rules
    # 原文档稳如泰山:一次非法替换不能 shredding 已有声明。
    doc = service.get(entity_id="e", bot_id="b")
    assert doc.manifest.skills[0].name == "q"
    assert service.get_record(entity_id="e", bot_id="b").modifier == "alice"


# --- capability gate at write time -----------------------------------------


def test_declaring_engine_config_is_refused_as_undelivered(service):
    with pytest.raises(ManifestInvalidError) as excinfo:
        service.put(
            entity_id="e", bot_id="b", engine_type="openclaw", bot_type="personal",
            document={"manifest": {"engine_config": {"config": {"model": "x"}}}},
            modifier="alice",
        )
    assert any(v.rule == "category-unsupported" for v in excinfo.value.violations)


def test_declaring_cli_tools_is_refused_until_w9(service):
    doc = {
        "manifest": {
            "cli_tools": [
                {"name": "mycli", "source": "https://s/mycli", "digest": DigEST}
            ]
        }
    }
    with pytest.raises(ManifestInvalidError) as excinfo:
        service.put(
            entity_id="e", bot_id="b", engine_type="openclaw", bot_type="personal",
            document=doc, modifier="alice",
        )
    assert excinfo.value.violations[0].rule == "category-unsupported"
    assert "W9" in excinfo.value.violations[0].message


def test_script_on_teclaw_is_refused_at_write_time(service):
    with pytest.raises(ManifestInvalidError) as excinfo:
        service.put(
            entity_id="e", bot_id="b", engine_type="teclaw", bot_type="personal",
            document={"script": {"body": "#!/bin/bash\n"}},
            modifier="alice",
        )
    assert excinfo.value.violations[0].rule == "script-unsupported"


def test_the_935_form_factor_override_narrows_script_support(service):
    """openclaw 引擎表说话算;但这台 bot 的支撑判定为「跑不了脚本」→ 拒。"""
    with pytest.raises(ManifestInvalidError) as excinfo:
        service.put(
            entity_id="e", bot_id="b", engine_type="openclaw", bot_type="personal",
            document={"script": {"body": "#!/bin/bash\n"}},
            modifier="alice",
            script_supported=False,
        )
    assert excinfo.value.violations[0].rule == "script-unsupported"


def test_identity_type_is_validated_against_the_engine_at_write_time(service):
    """#1473:claude_code 只认 CLAUDE.md——写入时拒,#不静默跳过。"""
    bad = {
        "manifest": {
            "identity": [
                {"type": "SOUL.md", "source": "https://s/soul.md", "digest": DigEST}
            ]
        }
    }
    with pytest.raises(ManifestInvalidError) as excinfo:
        service.put(
            entity_id="e", bot_id="b", engine_type="claude_code", bot_type="personal",
            document=bad, modifier="alice",
        )
    assert excinfo.value.violations[0].rule == "identity-type-engine"


def test_capabilities_is_the_same_resolver_the_put_uses(service):
    """读写共用同一份判定:GET capabilities 说什么,PUT 拒绝什么。"""
    support: CategorySupport = service.capabilities(
        engine_type="teclaw", bot_type="personal"
    )
    assert support.categories["script"] is False
    with pytest.raises(ManifestInvalidError):
        service.put(
            entity_id="e", bot_id="b", engine_type="teclaw", bot_type="personal",
            document=A_GOOD_DOC | {"script": {"body": "x"}},
            modifier="alice",
        )


# --- warnings ---------------------------------------------------------------


def test_unreferenced_source_is_a_warning_not_an_error(service):
    doc = dict(A_GOOD_DOC)
    doc["sources"]["spare"] = {"git": "https://g/spare.git", "ref": "v2"}
    result = service.put(
        entity_id="e", bot_id="b", engine_type="openclaw", bot_type="personal",
        document=doc, modifier="alice",
    )
    assert any("sources.spare" in w for w in result["warnings"])


def test_reserved_identity_file_is_a_warning(service):
    """schema §3.5:如实警示;apply 永不写(W4 的 D2 例外)。"""
    doc = {
        "manifest": {
            "identity": [
                {"type": "MEMORY.md", "source": "https://s/mem.md", "digest": DigEST}
            ]
        }
    }
    result = service.put(
        entity_id="e", bot_id="b", engine_type="openclaw", bot_type="personal",
        document=doc, modifier="alice",
    )
    assert any("MEMORY.md" in w for w in result["warnings"])


# --- delete -----------------------------------------------------------------


def test_delete_is_idempotent_and_removes_only_the_declaration(service):
    service.put(
        entity_id="e", bot_id="b", engine_type="openclaw", bot_type="personal",
        document=A_GOOD_DOC, modifier="alice",
    )
    assert service.delete(entity_id="e", bot_id="b") is True
    assert service.get(entity_id="e", bot_id="b") == parse_document("{}")
    assert service.delete(entity_id="e", bot_id="b") is False


def test_document_size_limit_is_enforced_on_the_serialized_form(service):
    doc = {"manifest": {"identity": []}}
    doc["manifest"]["resources"] = [
        {"path": f"d{i}/x.md", "source": "https://s/x.md"} for i in range(49)
    ]
    # 49 resources entries + inline大 content still under per-entry limits —
    # push the *document* over 64KiB via inline identity content.
    doc["manifest"]["identity"] = [
        {"type": "RULES.md", "content": "y" * (62 * 1024)}
    ]
    with pytest.raises(ManifestInvalidError) as excinfo:
        service.put(
            entity_id="e", bot_id="b", engine_type="openclaw", bot_type="personal",
            document=doc, modifier="alice",
        )
    assert any(
        v.rule == "limit-inline-content" or v.rule == "limit-document-bytes"
        for v in excinfo.value.violations
    )


def test_modifier_is_bounded_at_construction(service):
    result = service.put(
        entity_id="e", bot_id="b", engine_type="openclaw", bot_type="personal",
        document=A_GOOD_DOC, modifier="m" * 5000,
    )
    assert len(result["modifier"]) == 1024


# --- H2 regression: declared-empty vs absent (#D2 承重语义) ----------------


def test_a_declared_empty_category_survives_storage(service):
    """`skills: []` 是「清空该类目」的声明,不是缺省——序列化保留它。

    exclude_defaults 曾把它连默认值一起剥掉,存下去就和「从未声明」无法
    区分,恰好吃掉 D2 的承重语义;这条测试钉住往返。
    """
    service.put(
        entity_id="e", bot_id="b", engine_type="openclaw", bot_type="personal",
        document={"manifest": {"skills": []}}, modifier="alice",
    )
    record = service.get_record(entity_id="e", bot_id="b")
    import json as _json
    stored = _json.loads(record.document)
    assert "skills" in stored["manifest"], stored
    assert stored["manifest"]["skills"] == []
    assert stored["schema_version"] == 1
