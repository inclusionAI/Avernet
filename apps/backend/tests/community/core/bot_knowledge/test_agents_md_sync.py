"""AGENTS.md projection: fence discipline, idempotence, degradation."""

import pytest

from agentclaw.community.core.bot_knowledge.agents_md_sync import (
    KNOWLEDGE_SECTION_END_MARKER,
    KNOWLEDGE_SECTION_START_MARKER,
    BotKnowledgeAgentsMdSync,
    apply_knowledge_section,
    build_knowledge_section,
)
from agentclaw.community.kernel.knowledge import BotKnowledgeScope


# ---------- pure section helpers ----------


def test_build_section_with_and_without_names():
    section = build_knowledge_section([("kb1", "Team Library"), ("kb2", None)])
    assert section.startswith(KNOWLEDGE_SECTION_START_MARKER)
    assert section.endswith(KNOWLEDGE_SECTION_END_MARKER)
    assert "- `kb1` — Team Library" in section
    assert "- `kb2`" in section
    assert "`search_knowledge`" in section
    assert "`read_document`" in section


def test_apply_appends_when_absent():
    assert apply_knowledge_section("# 手写", "SECTION") == "# 手写\n\nSECTION\n"


def test_apply_creates_heading_for_empty_file():
    assert apply_knowledge_section("", "SECTION") == "# AGENTS\n\nSECTION\n"


def test_apply_replaces_in_place_keeping_surroundings():
    old_section = build_knowledge_section([("kb1", None)])
    content = f"# 手写开头\n\n{old_section}\n\n尾部手写\n"
    new_section = build_knowledge_section([("kb2", "New Library")])
    out = apply_knowledge_section(content, new_section)
    assert out.startswith("# 手写开头\n\n")
    assert out.endswith("\n\n尾部手写\n")
    assert "- `kb2` — New Library" in out
    assert "`kb1`" not in out


def test_apply_idempotent_round_trip():
    section = build_knowledge_section([("kb1", None)])
    once = apply_knowledge_section("# 头\n", section)
    assert apply_knowledge_section(once, section) == once


def test_apply_remove():
    section = build_knowledge_section([("kb1", None)])
    content = f"# 手写\n\n{section}\n\n尾部\n"
    assert apply_knowledge_section(content, None) == "# 手写\n\n尾部\n"


def test_apply_remove_without_marker_is_noop():
    assert apply_knowledge_section("# 手写\n", None) == "# 手写\n"


def test_apply_broken_fence_returns_none():
    content = f"# 手写\n\n{KNOWLEDGE_SECTION_START_MARKER}\n孤儿段落"
    assert apply_knowledge_section(content, "SECTION") is None
    assert apply_knowledge_section(content, None) is None


# ---------- fakes ----------


class FakeIdentity:
    def __init__(self, files=None):
        self.files = dict(files or {})
        self.writes = []

    async def read_identity_file(
        self, entity_type, entity_id, bot_id, file_type, owner_id, *, engine_type=None, stage="draft"
    ):
        return self.files.get(file_type, "")

    async def write_identity_file(
        self, entity_type, entity_id, bot_id, file_type, content, owner_id, *, engine_type=None
    ):
        self.files[file_type] = content
        self.writes.append(file_type)


class ExplodingIdentity(FakeIdentity):
    async def write_identity_file(self, *args, **kwargs):
        raise RuntimeError("device down")


class FakeDetailsCatalog:
    def __init__(self, names=None, fail=False):
        self.names = dict(names or {})
        self.fail = fail
        self.calls = []

    def details(self, *, space_id, kb_ids, actor_id):
        self.calls.append(list(kb_ids))
        if self.fail:
            raise RuntimeError("catalog down")
        return [
            {"kb_id": kb, "name": self.names.get(kb), "space_id": str(space_id)}
            for kb in kb_ids
        ]


# ---------- component behaviour over the real repository ----------


def make_sync(repo, catalog, identity):
    return BotKnowledgeAgentsMdSync(repo, catalog, identity)


def enable(service, scope, kb_id="kb", *, enabled=True, revision=0):
    return service.set_enabled(
        scope=scope, actor_id="owner", kb_id=kb_id, enabled=enabled, expected_revision=revision
    )


@pytest.mark.asyncio
async def test_sync_projects_enabled_selection_with_names(knowledge):
    _, repo, service, scope = knowledge
    enable(service, scope)
    identity = FakeIdentity({"AGENTS.md": "# 手写\n"})
    catalog = FakeDetailsCatalog(names={"kb": "Team Knowledge Base"})
    await make_sync(repo, catalog, identity).sync(scope=scope, actor_id="owner")
    content = identity.files["AGENTS.md"]
    assert content.startswith("# 手写\n\n")
    assert "- `kb` — Team Knowledge Base" in content
    assert catalog.calls == [["kb"]]


@pytest.mark.asyncio
async def test_sync_is_idempotent_without_new_writes(knowledge):
    _, repo, service, scope = knowledge
    enable(service, scope)
    identity = FakeIdentity({"AGENTS.md": "# 手写\n"})
    sync = make_sync(repo, FakeDetailsCatalog(names={"kb": "Name"}), identity)
    await sync.sync(scope=scope, actor_id="owner")
    identity.writes.clear()
    await sync.sync(scope=scope, actor_id="owner")
    assert identity.writes == []


@pytest.mark.asyncio
async def test_sync_removes_section_when_selection_empties(knowledge):
    _, repo, service, scope = knowledge
    enable(service, scope)
    identity = FakeIdentity({"AGENTS.md": "# 手写\n"})
    sync = make_sync(repo, FakeDetailsCatalog(names={"kb": "Name"}), identity)
    await sync.sync(scope=scope, actor_id="owner")
    enable(service, scope, enabled=False, revision=1)
    await sync.sync(scope=scope, actor_id="owner")
    assert identity.files["AGENTS.md"] == "# 手写\n"


@pytest.mark.asyncio
async def test_sync_replaces_section_on_selection_change(knowledge):
    _, repo, service, scope = knowledge
    enable(service, scope, kb_id="kb")
    identity = FakeIdentity({"AGENTS.md": "# 手写\n"})
    sync = make_sync(
        repo,
        FakeDetailsCatalog(names={"kb": "Old Library", "kb2": "New Library"}),
        identity,
    )
    await sync.sync(scope=scope, actor_id="owner")
    # The local fixture catalog only authorizes "kb"; widen it before the second
    # enable, as the real platform would after a new grant.
    service.catalog.authorized.add("kb2")
    enable(service, scope, kb_id="kb2")
    await sync.sync(scope=scope, actor_id="owner")
    content = identity.files["AGENTS.md"]
    assert "`kb`" in content and "`kb2`" in content
    assert content.count(KNOWLEDGE_SECTION_START_MARKER) == 1


@pytest.mark.asyncio
async def test_catalog_failure_degrades_to_ids_only(knowledge):
    _, repo, service, scope = knowledge
    enable(service, scope)
    identity = FakeIdentity({"AGENTS.md": "# 手写\n"})
    catalog = FakeDetailsCatalog(fail=True)
    await make_sync(repo, catalog, identity).sync(scope=scope, actor_id="owner")
    content = identity.files["AGENTS.md"]
    assert "- `kb`\n" in content
    assert "—" not in content.split("- `kb`")[1].split("\n")[0]


@pytest.mark.asyncio
async def test_sync_swallows_identity_write_failure(knowledge):
    _, repo, service, scope = knowledge
    enable(service, scope)
    identity = ExplodingIdentity({"AGENTS.md": "# 手写\n"})
    await make_sync(repo, FakeDetailsCatalog(), identity).sync(scope=scope, actor_id="owner")
    assert identity.files["AGENTS.md"] == "# 手写\n"


@pytest.mark.asyncio
async def test_sync_swallows_missing_bot(knowledge):
    _, repo, _, _ = knowledge
    identity = FakeIdentity()
    await make_sync(repo, FakeDetailsCatalog(), identity).sync(
        scope=BotKnowledgeScope("ghost", "owner", "dev"), actor_id="owner"
    )
    assert identity.writes == []


@pytest.mark.asyncio
async def test_sync_skips_broken_fence(knowledge):
    _, repo, service, scope = knowledge
    enable(service, scope)
    broken = f"# 手写\n\n{KNOWLEDGE_SECTION_START_MARKER}\n孤儿段落"
    identity = FakeIdentity({"AGENTS.md": broken})
    await make_sync(repo, FakeDetailsCatalog(), identity).sync(scope=scope, actor_id="owner")
    assert identity.writes == []
    assert identity.files["AGENTS.md"] == broken


def test_resolve_names_batches_by_twenty(knowledge):
    _, repo, _, _ = knowledge
    ids = [f"kb{i}" for i in range(41)]
    names = {kb: f"名{kb}" for kb in ids}
    catalog = FakeDetailsCatalog(names=names)
    sync = make_sync(repo, catalog, FakeIdentity())
    resolved = sync._resolve_names(space_id=1, kb_ids=ids, actor_id="owner")
    assert catalog.calls == [ids[:20], ids[20:40], ids[40:]]
    assert resolved == names
