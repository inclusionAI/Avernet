"""``ManagedFilesStore`` and its compose reader (W8)."""
from __future__ import annotations

import pytest

from agentclaw.community.core.bot_config_manifest.managed_files import (
    CATEGORY_IDENTITY,
    CATEGORY_RESOURCES,
    CATEGORY_SKILLS,
    ManagedFileScope,
    ManagedFilesComposeReader,
    ManagedFilesStore,
    ManagedFilesStoreError,
    digest_of,
)
from agentclaw.community.core.config_compose.models import ComposeRequest

from ._fakes import FakeObjectStorage, sqlite_repository

_SCOPE = ManagedFileScope(env="dev", entity_type="staff", entity_id="u1", bot_id="bot7")
_BASE = "teclaw/dev/bolt_data"


def _store(oss=None):
    oss = oss or FakeObjectStorage()
    return ManagedFilesStore(object_storage=oss, repository=sqlite_repository(), store_base=lambda: _BASE), oss


def test_put_writes_the_object_then_the_row_under_the_promotion_layout() -> None:
    store, oss = _store()
    f = store.put(
        _SCOPE, category=CATEGORY_IDENTITY, name="RULES.md",
        rel_path="identity/RULES.md", content=b"# rules\n", apply_id="ap_1",
    )
    assert f.store_key == f"{_BASE}/staff_u1/bot7_manifest/teclaw/identity/RULES.md"
    assert f.ref_path == "staff_u1/bot7_manifest/teclaw/identity/RULES.md"
    assert f.digest == digest_of(b"# rules\n") and f.size_bytes == 8
    assert oss.objects[f.store_key] == b"# rules\n"
    assert store.get(_SCOPE, category=CATEGORY_IDENTITY, rel_path="identity/RULES.md") == f
    assert store.read(f) == b"# rules\n"


def test_a_failed_object_put_leaves_no_row() -> None:
    store, _ = _store(FakeObjectStorage(fail_puts=True))
    with pytest.raises(ManagedFilesStoreError):
        store.put(_SCOPE, category=CATEGORY_IDENTITY, name="x", rel_path="identity/x", content=b"1", apply_id=None)
    assert store.list(_SCOPE, category=CATEGORY_IDENTITY) == []


def test_a_failed_object_delete_keeps_the_row_so_the_bytes_stay_reachable() -> None:
    from agentclaw.community.core.bot_config_manifest.managed_files import ManagedFilesStoreError

    store, oss = _store()
    store.put(_SCOPE, category=CATEGORY_IDENTITY, name="a", rel_path="identity/a", content=b"1", apply_id=None)
    key = store.list(_SCOPE, category=CATEGORY_IDENTITY)[0].store_key
    oss.delete_object = lambda k: False  # type: ignore[method-assign]
    with pytest.raises(ManagedFilesStoreError):
        store.delete(_SCOPE, category=CATEGORY_IDENTITY, rel_path="identity/a")
    assert [r.store_key for r in store.list(_SCOPE, category=CATEGORY_IDENTITY)] == [key]
    with pytest.raises(ManagedFilesStoreError):
        store.purge(_SCOPE)
    assert len(store.list(_SCOPE, category=CATEGORY_IDENTITY)) == 1
    # Once the store answers again, the same row lets the delete land.
    oss.delete_object = lambda k: oss.objects.pop(k, None) is not None  # type: ignore[method-assign]
    assert store.delete(_SCOPE, category=CATEGORY_IDENTITY, rel_path="identity/a")
    assert key not in oss.objects and store.list(_SCOPE, category=CATEGORY_IDENTITY) == []


def test_a_purge_that_could_not_delete_every_object_keeps_only_those_rows() -> None:
    """A row goes as soon as its object is confirmed gone: a later failure in
    the same purge must not leave the index remembering bytes that are not
    there any more."""
    from agentclaw.community.core.bot_config_manifest.managed_files import ManagedFilesStoreError

    store, oss = _store()
    store.put(_SCOPE, category=CATEGORY_IDENTITY, name="a", rel_path="identity/a", content=b"1", apply_id=None)
    store.put(_SCOPE, category=CATEGORY_RESOURCES, name="b", rel_path="workspace/b", content=b"2", apply_id=None)
    stuck = store.list(_SCOPE, category=CATEGORY_RESOURCES)[0].store_key
    real_delete = oss.delete_object
    oss.delete_object = lambda k: False if k == stuck else real_delete(k)  # type: ignore[method-assign]
    with pytest.raises(ManagedFilesStoreError):
        store.purge(_SCOPE)
    assert store.list(_SCOPE, category=CATEGORY_IDENTITY) == [], "its object is gone; so is the row"
    assert [r.store_key for r in store.list(_SCOPE, category=CATEGORY_RESOURCES)] == [stuck]
    assert stuck in oss.objects
    oss.delete_object = real_delete  # type: ignore[method-assign]
    assert store.purge(_SCOPE) == 1
    assert oss.objects == {}


def test_delete_removes_object_then_row_and_purge_removes_everything() -> None:
    store, oss = _store()
    store.put(_SCOPE, category=CATEGORY_IDENTITY, name="a", rel_path="identity/a", content=b"1", apply_id=None)
    store.put(_SCOPE, category=CATEGORY_RESOURCES, name="b", rel_path="workspace/b", content=b"2", apply_id=None)
    assert store.delete(_SCOPE, category=CATEGORY_IDENTITY, rel_path="identity/a")
    assert not store.delete(_SCOPE, category=CATEGORY_IDENTITY, rel_path="identity/a")
    assert f"{_BASE}/staff_u1/bot7_manifest/teclaw/identity/a" not in oss.objects
    assert store.purge(_SCOPE) == 1
    assert oss.objects == {}
    assert store.list(_SCOPE, category=CATEGORY_RESOURCES) == []


def test_re_putting_identical_bytes_keeps_one_row() -> None:
    store, oss = _store()
    store.put(_SCOPE, category=CATEGORY_RESOURCES, name="b", rel_path="workspace/b", content=b"2", apply_id="ap_1")
    store.put(_SCOPE, category=CATEGORY_RESOURCES, name="b", rel_path="workspace/b", content=b"2", apply_id="ap_2")
    rows = store.list(_SCOPE, category=CATEGORY_RESOURCES)
    assert len(rows) == 1 and len(oss.objects) == 1


# ── the compose reader ──────────────────────────────────────────────────


class _Manifests:
    def __init__(self, document):
        self._document = document

    def get(self, *, entity_id, bot_id):
        if self._document is None:
            return None
        return type("R", (), {"document": self._document})()


_REQ = ComposeRequest(entity_id="u1", bot_id="bot7", user_id="u1", engine_type="teclaw", entity_type="staff")


def _reader(store, document, switch=True):
    return ManagedFilesComposeReader(
        store=store, manifest_service_provider=lambda: _Manifests(document), platform_managed=lambda: switch
    )


def test_platform_managed_is_the_declared_file_categories_when_the_switch_is_on(monkeypatch) -> None:
    monkeypatch.setattr("agentclaw.community.utils.env_utils.get_current_env", lambda: "dev")
    store, _ = _store()
    doc = "schema_version: 1\nmanifest:\n  identity: []\n  resources:\n    - path: kb/a.md\n      content: hi\n  mcp: []\n"
    assert _reader(store, doc).platform_managed(_REQ) == frozenset({"identity_files", "resources"})
    assert _reader(store, doc, switch=False).platform_managed(_REQ) == frozenset()
    assert _reader(store, None).platform_managed(_REQ) == frozenset()
    assert _reader(store, "schema_version: 1\n").platform_managed(_REQ) == frozenset()
    assert _reader(store, ": not yaml [").platform_managed(_REQ) == frozenset()


def test_the_reader_yields_collector_shaped_refs_from_the_index(monkeypatch) -> None:
    monkeypatch.setattr(
        "agentclaw.community.core.bot_config_manifest.managed_files.reader._scope",
        lambda req: _SCOPE,
    )
    store, _ = _store()
    store.put(_SCOPE, category=CATEGORY_IDENTITY, name="RULES.md", rel_path="identity/RULES.md", content=b"r", apply_id=None)
    store.put(_SCOPE, category=CATEGORY_RESOURCES, name="kb/a.md", rel_path="workspace/kb/a.md", content=b"a", apply_id=None)
    store.put(_SCOPE, category=CATEGORY_SKILLS, name="order-lookup", rel_path="workspace/skills-local/order-lookup/SKILL.md", content=b"s", apply_id=None)
    store.put(_SCOPE, category=CATEGORY_SKILLS, name="order-lookup", rel_path="workspace/skills-local/order-lookup/scripts/run.py", content=b"p", apply_id=None)
    reader = _reader(store, None)

    identity = reader.identity_files(_REQ)
    assert [(f.name, f.store, f.path) for f in identity] == [
        ("RULES.md", "bot-data", "staff_u1/bot7_manifest/teclaw/identity/RULES.md")
    ]
    resources = reader.resources(_REQ)
    assert [f.path for f in resources] == ["staff_u1/bot7_manifest/teclaw/workspace/kb/a.md"]
    skills = reader.skills(_REQ)
    assert [(s.name, s.scope, s.store, s.path) for s in skills] == [
        ("order-lookup", "user", "bot-data", "staff_u1/bot7_manifest/teclaw/workspace/skills-local/order-lookup")
    ]
    # A package's files, as resources refs, for the names the collector keeps.
    assert [f.path for f in reader.skill_files(_REQ, ["order-lookup"])] == [
        "staff_u1/bot7_manifest/teclaw/workspace/skills-local/order-lookup/SKILL.md",
        "staff_u1/bot7_manifest/teclaw/workspace/skills-local/order-lookup/scripts/run.py",
    ]
    assert reader.skill_files(_REQ, []) == []


def test_a_skill_named_like_a_layout_segment_keeps_its_own_prefix(monkeypatch) -> None:
    """``teclaw`` and ``workspace`` are legal skill names; the package prefix
    is built from the layout, not found by searching the ref path."""
    monkeypatch.setattr(
        "agentclaw.community.core.bot_config_manifest.managed_files.reader._scope",
        lambda req: _SCOPE,
    )
    store, _ = _store()
    for name in ("workspace", "teclaw"):
        store.put(_SCOPE, category=CATEGORY_SKILLS, name=name, rel_path=f"workspace/skills-local/{name}/SKILL.md", content=b"s", apply_id=None)
    skills = _reader(store, None).skills(_REQ)
    assert [(s.name, s.path) for s in skills] == [
        ("teclaw", "staff_u1/bot7_manifest/teclaw/workspace/skills-local/teclaw"),
        ("workspace", "staff_u1/bot7_manifest/teclaw/workspace/skills-local/workspace"),
    ]


def test_a_row_written_under_an_earlier_base_resolves_against_the_current_one() -> None:
    oss = FakeObjectStorage()
    repo = sqlite_repository()
    old = ManagedFilesStore(object_storage=oss, repository=repo, store_base=lambda: "teclaw/old/bolt_data")
    old.put(_SCOPE, category=CATEGORY_IDENTITY, name="RULES.md", rel_path="identity/RULES.md", content=b"r", apply_id=None)
    current = ManagedFilesStore(object_storage=oss, repository=repo, store_base=lambda: "teclaw/new/bolt_data")
    (row,) = current.list(_SCOPE, category=CATEGORY_IDENTITY)
    # The ref is store-relative under the current base, never the stale absolute key.
    assert row.ref_path == "staff_u1/bot7_manifest/teclaw/identity/RULES.md"
    assert row.store_key.startswith("teclaw/old/")
