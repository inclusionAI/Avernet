"""Expert discovery through the scanner and persistent catalogue seam."""

import asyncio
from pathlib import Path

import pytest

from agentclaw.community.core.expert_market.models import ExpertScope
from agentclaw.community.core.expert_market.repository import ExpertCatalogRepository
from agentclaw.community.core.expert_market.scanner import ExpertScanner
from agentclaw.community.plugins.local.database import SqliteDB, reset_for_tests


@pytest.fixture
def catalog():
    reset_for_tests()
    db = SqliteDB()
    asyncio.run(db.bootstrap())
    yield ExpertCatalogRepository(db)
    reset_for_tests()


def write_expert(root: Path, name="writer", text="原文正文"):
    folder = root / "agents" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "expert.yaml").write_text(
        f"id: {name}\nname: 写作专家\ndescription: English Writing\ncategory: 内容\ntags: [PRD, 中文]\nmanifest: manifest.yaml\n"
    )
    (folder / "manifest.yaml").write_text(
        f"schema_version: 1\nmanifest:\n  identity:\n    - type: AGENTS.md\n      from: aiworkbench\n      subpath: agents/{name}/AGENTS.md\n"
    )
    (folder / "AGENTS.md").write_text(text)
    return folder


def test_valid_identity_only_expert_is_discoverable_with_real_content(
    catalog, tmp_path
):
    write_expert(tmp_path)
    scope = ExpertScope("tenant-a", "dev", "aiworkbench")
    result = ExpertScanner(catalog).scan(tmp_path, source_commit="a" * 40, scope=scope)
    assert result.valid == 1
    total, items = catalog.search(
        scope,
        keyword=" writing ",
        category="内容",
        sort="hot",
        page_num=1,
        page_size=20,
    )
    assert total == 1
    assert items[0]["creation_count"] == 0
    detail = catalog.get(scope, "writer")
    assert detail["identity"] == [{"type": "AGENTS.md", "content": "原文正文"}]
    assert detail["skills"] == []
    assert detail["source_commit"] == "a" * 40
    assert catalog.search(
        ExpertScope("tenant-b", "dev", "aiworkbench"),
        keyword="",
        category=None,
        sort="hot",
        page_num=1,
        page_size=20,
    ) == (0, [])


def test_invalid_update_preserves_effective_content_and_first_added_then_removal(
    catalog, tmp_path
):
    scope = ExpertScope("tenant-a", "dev", "aiworkbench")
    scanner = ExpertScanner(catalog)
    folder = write_expert(tmp_path)
    scanner.scan(tmp_path, source_commit="a" * 40, scope=scope)
    initial = catalog.get(scope, "writer")
    (folder / "AGENTS.md").write_text("第二版")
    scanner.scan(tmp_path, source_commit="b" * 40, scope=scope)
    assert catalog.get(scope, "writer")["first_added_at"] == initial["first_added_at"]
    (folder / "expert.yaml").write_text("id: writer\nid: writer\n")
    assert scanner.scan(tmp_path, source_commit="c" * 40, scope=scope).invalid == 1
    assert catalog.get(scope, "writer")["identity"][0]["content"] == "第二版"
    assert catalog.get(scope, "writer")["source_commit"] == "b" * 40
    (folder / "expert.yaml").unlink()
    scanner.scan(tmp_path, source_commit="d" * 40, scope=scope)
    assert catalog.get(scope, "writer") is None
    write_expert(tmp_path)
    scanner.scan(tmp_path, source_commit="e" * 40, scope=scope)
    assert catalog.get(scope, "writer")["first_added_at"] == initial["first_added_at"]


def test_literal_search_matches_each_tag_and_never_json_punctuation(catalog, tmp_path):
    scope = ExpertScope("tenant-a", "dev", "aiworkbench")
    write_expert(tmp_path)
    ExpertScanner(catalog).scan(tmp_path, source_commit="a" * 40, scope=scope)

    def search(keyword):
        return catalog.search(
            scope, keyword=keyword, category=None, sort="hot", page_num=1, page_size=20
        )[0]

    assert search(" prd ") == 1
    assert search("中文") == 1
    assert search("%") == 0
    assert search("_") == 0
    assert search("[") == 0
    assert search('PRD", "中文') == 0


def test_history_counts_soft_deleted_and_failed_bots_before_global_pagination(
    catalog, tmp_path
):
    import json
    from agentclaw.community.plugin_api.models import BotModel
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    scope = ExpertScope("tenant-a", "dev", "aiworkbench")
    write_expert(tmp_path, "a-expert")
    write_expert(tmp_path, "z-expert")
    ExpertScanner(catalog).scan(tmp_path, source_commit="a" * 40, scope=scope)
    with avernet_tenant_scope("tenant-a"):
        # Persisted history is the fixture; assertions stay at catalogue boundary.
        with catalog._db.orm_session() as db:
            for i, (expert, deleted, state, kind) in enumerate(
                [
                    ("a-expert", 0, "READY", "personal"),
                    ("z-expert", 1, "FAILED", "personal"),
                    ("z-expert", 0, "DRAFT", "service"),
                ]
            ):
                db.add(
                    BotModel(
                        bot_id=f"asset-{i}",
                        entity_id=f"entity-{i}",
                        entity_type="user",
                        creator_id="u",
                        owner_id="u",
                        status=state,
                        bot_type=kind,
                        is_delete=deleted,
                        env="dev",
                        ext=json.dumps(
                            {
                                "avatar": "unchanged",
                                "expert_origin": {
                                    "expert_id": expert,
                                    "source_repo_id": "aiworkbench",
                                },
                            }
                        ),
                    )
                )
        total, items = catalog.search(
            scope, keyword="", category=None, sort="hot", page_num=1, page_size=1
        )
        assert total == 2
        assert [(v["expert_id"], v["creation_count"]) for v in items] == [
            ("z-expert", 2)
        ]
        assert catalog.get(scope, "a-expert")["creation_count"] == 1


def test_full_enumeration_failure_never_deletes_and_other_valid_expert_advances(
    catalog, tmp_path
):
    scope = ExpertScope("tenant-a", "dev", "aiworkbench")
    scanner = ExpertScanner(catalog)
    bad = write_expert(tmp_path, "bad")
    good = write_expert(tmp_path, "good")
    scanner.scan(tmp_path, source_commit="a" * 40, scope=scope)
    (bad / "manifest.yaml").write_text("schema_version: false\nmanifest: {}\n")
    (good / "AGENTS.md").write_text("new good")
    result = scanner.scan(tmp_path, source_commit="b" * 40, scope=scope)
    assert (result.valid, result.invalid) == (1, 1)
    assert catalog.get(scope, "bad")["source_commit"] == "a" * 40
    assert catalog.get(scope, "good")["identity"][0]["content"] == "new good"
    with pytest.raises(ValueError, match="unavailable"):
        scanner.scan(tmp_path / "missing", source_commit="c" * 40, scope=scope)
    assert catalog.get(scope, "good") is not None


@pytest.mark.parametrize(
    "change",
    [
        "name: duplicate\nname: duplicate\n",
        "id: writer\nengine: openclaw\n",
    ],
)
def test_new_bad_experts_are_not_discoverable(catalog, tmp_path, change):
    folder = write_expert(tmp_path)
    (folder / "expert.yaml").write_text(change)
    scope = ExpertScope("tenant-a", "dev", "aiworkbench")
    assert (
        ExpertScanner(catalog)
        .scan(tmp_path, source_commit="a" * 40, scope=scope)
        .invalid
        == 1
    )
    assert catalog.categories(scope) == []


def test_standard_skill_metadata_is_persisted_and_invalid_package_preserves_old_input(
    catalog, tmp_path
):
    scope = ExpertScope("tenant-a", "dev", "aiworkbench")
    scanner = ExpertScanner(catalog)
    expert = write_expert(tmp_path)
    other = write_expert(tmp_path, "other")
    package = expert / "skills" / "writing-review"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: writing-review\ndescription: 检查写作质量与逻辑。\n---\n\n# 写作评审\n\n先检查事实，再评审结构。\n"
    )
    manifest = expert / "manifest.yaml"
    manifest.write_text(
        manifest.read_text()
        + "  skills:\n    - name: writing-review\n      from: aiworkbench\n      subpath: agents/writer/skills/writing-review\n"
    )
    result = scanner.scan(tmp_path, source_commit="a" * 40, scope=scope)
    assert (result.valid, result.invalid) == (2, 0)
    detail = catalog.get(scope, "writer")
    assert detail["skills"] == [
        {"name": "writing-review", "description": "检查写作质量与逻辑。"}
    ]
    assert any(
        reference["path"].endswith("/writing-review/SKILL.md")
        for reference in detail["references"]
    )
    (package / "SKILL.md").write_text(
        "---\nname: wrong-name\ndescription: 不能冒充原声明。\n---\n"
    )
    (other / "AGENTS.md").write_text("其他合法专家继续更新")
    result = scanner.scan(tmp_path, source_commit="b" * 40, scope=scope)
    assert (result.valid, result.invalid) == (1, 1)
    assert catalog.get(scope, "writer")["skills"] == detail["skills"]
    assert catalog.get(scope, "writer")["source_commit"] == "a" * 40
    assert (
        catalog.get(scope, "other")["identity"][0]["content"] == "其他合法专家继续更新"
    )
