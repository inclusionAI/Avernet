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
from agentclaw.community.core.bot_config_manifest.managed_files.store import (
    category_of,
    name_of,
)
from agentclaw.community.core.config_compose.models import ComposeOccasion, ComposeRequest

from ._fakes import FakeObjectStorage

_SCOPE = ManagedFileScope(entity_type="staff", entity_id="u1", bot_id="bot7")
_BASE = "teclaw/dev/bolt_data"
_ROOT = f"{_BASE}/staff_u1/bot7_manifest/teclaw"


def _store(oss=None):
    oss = oss or FakeObjectStorage()
    return ManagedFilesStore(object_storage=oss, store_base=lambda: _BASE), oss


def test_put_writes_the_object_under_the_promotion_layout() -> None:
    store, oss = _store()
    f = store.put(
        _SCOPE, category=CATEGORY_IDENTITY, name="RULES.md",
        rel_path="identity/RULES.md", content=b"# rules\n",
    )
    assert f.store_key == f"{_ROOT}/identity/RULES.md"
    assert f.ref_path == "staff_u1/bot7_manifest/teclaw/identity/RULES.md"
    assert f.digest == digest_of(b"# rules\n") and f.size_bytes == 8
    assert oss.objects[f.store_key] == b"# rules\n"
    # ``get`` reads the object back with the same digest and size.
    assert store.get(_SCOPE, category=CATEGORY_IDENTITY, rel_path="identity/RULES.md") == f
    assert store.get(_SCOPE, category=CATEGORY_RESOURCES, rel_path="identity/RULES.md") is None
    assert store.get(_SCOPE, category=CATEGORY_IDENTITY, rel_path="identity/nope") is None
    assert store.read(f) == b"# rules\n"
    assert store.read_at(_SCOPE, "identity/RULES.md") == b"# rules\n"


def test_a_failed_object_put_raises_and_stores_nothing() -> None:
    store, _ = _store(FakeObjectStorage(fail_puts=True))
    with pytest.raises(ManagedFilesStoreError):
        store.put(_SCOPE, category=CATEGORY_IDENTITY, name="x", rel_path="identity/x", content=b"1")
    assert store.list(_SCOPE, category=CATEGORY_IDENTITY) == []


def test_the_layout_decides_the_category_and_the_name() -> None:
    assert category_of("identity/RULES.md") == CATEGORY_IDENTITY
    assert category_of("workspace/kb/a.md") == CATEGORY_RESOURCES
    assert category_of("workspace/skills-local/order-lookup/SKILL.md") == CATEGORY_SKILLS
    assert category_of("workspace/skills-local/order-lookup/scripts/run.py") == CATEGORY_SKILLS
    # A package needs a name and a member; the bare directory is nothing.
    assert category_of("workspace/skills-local/") is None
    assert category_of("workspace/skills-local/order-lookup") is None
    assert category_of("identity/") is None
    assert category_of("workspace/") is None
    assert category_of("elsewhere/x") is None
    assert name_of(CATEGORY_IDENTITY, "identity/RULES.md") == "RULES.md"
    assert name_of(CATEGORY_RESOURCES, "workspace/kb/a.md") == "kb/a.md"
    assert name_of(CATEGORY_SKILLS, "workspace/skills-local/order-lookup/scripts/run.py") == "order-lookup"


def test_a_write_whose_path_would_read_back_as_another_category_is_refused() -> None:
    """A resource declared under ``skills-local/`` would come back as a skill
    member; the store refuses it rather than misfiling it."""
    store, oss = _store()
    with pytest.raises(ValueError):
        store.put(
            _SCOPE, category=CATEGORY_RESOURCES, name="skills-local/x/SKILL.md",
            rel_path="workspace/skills-local/x/SKILL.md", content=b"1",
        )
    with pytest.raises(ValueError):
        store.put(_SCOPE, category=CATEGORY_IDENTITY, name="x", rel_path="workspace/x", content=b"1")
    assert oss.objects == {}


def test_list_classifies_the_bots_keys_and_ignores_what_is_not_in_the_layout() -> None:
    store, oss = _store()
    store.put(_SCOPE, category=CATEGORY_IDENTITY, name="RULES.md", rel_path="identity/RULES.md", content=b"r")
    store.put(_SCOPE, category=CATEGORY_RESOURCES, name="kb/a.md", rel_path="workspace/kb/a.md", content=b"a")
    store.put(_SCOPE, category=CATEGORY_SKILLS, name="ol", rel_path="workspace/skills-local/ol/SKILL.md", content=b"s")
    # Another bot's file, a publish stage of this bot, and a stray key under
    # the prefix that is not in the layout: none of them are this bot's.
    oss.objects[f"{_BASE}/staff_u1/bot8_manifest/teclaw/identity/RULES.md"] = b"other"
    oss.objects[f"{_BASE}/staff_u1/bot7_pub1_draft/teclaw/identity/RULES.md"] = b"stage"
    oss.objects[f"{_ROOT}/elsewhere/x"] = b"stray"
    assert [(f.name, f.rel_path) for f in store.list(_SCOPE, category=CATEGORY_IDENTITY)] == [
        ("RULES.md", "identity/RULES.md")
    ]
    assert [(f.name, f.rel_path) for f in store.list(_SCOPE, category=CATEGORY_RESOURCES)] == [
        ("kb/a.md", "workspace/kb/a.md")
    ]
    assert [(f.name, f.rel_path) for f in store.list(_SCOPE, category=CATEGORY_SKILLS)] == [
        ("ol", "workspace/skills-local/ol/SKILL.md")
    ]
    listed = store.list(_SCOPE, category=CATEGORY_IDENTITY)[0]
    assert listed.digest is None and listed.size_bytes is None, "a listing reads keys only"
    assert listed.store_key == f"{_ROOT}/identity/RULES.md"


def test_a_failed_object_delete_raises_with_the_object_still_there() -> None:
    store, oss = _store()
    store.put(_SCOPE, category=CATEGORY_IDENTITY, name="a", rel_path="identity/a", content=b"1")
    key = store.list(_SCOPE, category=CATEGORY_IDENTITY)[0].store_key
    oss.delete_object = lambda k: False  # type: ignore[method-assign]
    with pytest.raises(ManagedFilesStoreError):
        store.delete(_SCOPE, rel_path="identity/a")
    assert [f.store_key for f in store.list(_SCOPE, category=CATEGORY_IDENTITY)] == [key]
    with pytest.raises(ManagedFilesStoreError):
        store.purge(_SCOPE)
    assert len(store.list(_SCOPE, category=CATEGORY_IDENTITY)) == 1
    # Once the store answers again, the same object lets the delete land.
    oss.delete_object = lambda k: oss.objects.pop(k, None) is not None  # type: ignore[method-assign]
    store.delete(_SCOPE, rel_path="identity/a")
    assert key not in oss.objects and store.list(_SCOPE, category=CATEGORY_IDENTITY) == []


def test_delete_is_issued_without_a_listing_so_a_listing_failure_cannot_skip_it() -> None:
    """The plugin folds a transport failure into an empty listing; a delete
    gated on that would silently leave the object behind."""
    store, oss = _store()
    store.put(_SCOPE, category=CATEGORY_IDENTITY, name="a", rel_path="identity/a", content=b"1")
    oss.list_objects = lambda prefix, max_keys=1000: []  # type: ignore[method-assign]
    store.delete(_SCOPE, rel_path="identity/a")
    assert oss.objects == {}
    # And an absent object is already removed: no error, nothing listed first.
    store.delete(_SCOPE, rel_path="identity/a")


def test_a_purge_that_could_not_delete_every_object_keeps_only_those() -> None:
    store, oss = _store()
    store.put(_SCOPE, category=CATEGORY_IDENTITY, name="a", rel_path="identity/a", content=b"1")
    store.put(_SCOPE, category=CATEGORY_RESOURCES, name="b", rel_path="workspace/b", content=b"2")
    stuck = store.list(_SCOPE, category=CATEGORY_RESOURCES)[0].store_key
    real_delete = oss.delete_object
    oss.delete_object = lambda k: False if k == stuck else real_delete(k)  # type: ignore[method-assign]
    with pytest.raises(ManagedFilesStoreError):
        store.purge(_SCOPE)
    assert store.list(_SCOPE, category=CATEGORY_IDENTITY) == []
    assert [f.store_key for f in store.list(_SCOPE, category=CATEGORY_RESOURCES)] == [stuck]
    assert stuck in oss.objects
    oss.delete_object = real_delete  # type: ignore[method-assign]
    assert store.purge(_SCOPE) == 1
    assert oss.objects == {}


def test_delete_removes_one_object_and_purge_removes_everything_of_the_bot() -> None:
    store, oss = _store()
    store.put(_SCOPE, category=CATEGORY_IDENTITY, name="a", rel_path="identity/a", content=b"1")
    store.put(_SCOPE, category=CATEGORY_IDENTITY, name="a.bak", rel_path="identity/a.bak", content=b"1")
    store.put(_SCOPE, category=CATEGORY_RESOURCES, name="b", rel_path="workspace/b", content=b"2")
    other = f"{_BASE}/staff_u1/bot8_manifest/teclaw/identity/a"
    oss.objects[other] = b"other bot"
    store.delete(_SCOPE, rel_path="identity/a")
    store.delete(_SCOPE, rel_path="identity/a")  # idempotent
    # ``identity/a`` is a prefix of ``identity/a.bak``; only the exact key went.
    assert f"{_ROOT}/identity/a" not in oss.objects and f"{_ROOT}/identity/a.bak" in oss.objects
    assert store.purge(_SCOPE) == 2
    assert oss.objects == {other: b"other bot"}, "another bot's prefix is untouched"
    assert store.list(_SCOPE, category=CATEGORY_RESOURCES) == []


def test_re_putting_the_same_path_overwrites_in_place() -> None:
    store, oss = _store()
    store.put(_SCOPE, category=CATEGORY_RESOURCES, name="b", rel_path="workspace/b", content=b"2")
    store.put(_SCOPE, category=CATEGORY_RESOURCES, name="b", rel_path="workspace/b", content=b"3")
    files = store.list(_SCOPE, category=CATEGORY_RESOURCES)
    assert len(files) == 1 and len(oss.objects) == 1
    assert store.read(files[0]) == b"3"


def test_a_file_written_under_an_earlier_base_is_not_found_under_the_current_one() -> None:
    """The base is part of the key: the next apply writes the file again."""
    oss = FakeObjectStorage()
    old = ManagedFilesStore(object_storage=oss, store_base=lambda: "teclaw/old/bolt_data")
    old.put(_SCOPE, category=CATEGORY_IDENTITY, name="RULES.md", rel_path="identity/RULES.md", content=b"r")
    current = ManagedFilesStore(object_storage=oss, store_base=lambda: "teclaw/new/bolt_data")
    assert current.list(_SCOPE, category=CATEGORY_IDENTITY) == []
    assert old.list(_SCOPE, category=CATEGORY_IDENTITY)[0].store_key.startswith("teclaw/old/")


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


def _occasion(occasion: ComposeOccasion, engine_type: str = "teclaw") -> ComposeRequest:
    return ComposeRequest(
        entity_id="u1", bot_id="bot7", user_id="u1", engine_type=engine_type, entity_type="staff",
        occasion=occasion,
    )


def test_ownership_follows_the_operation_when_the_switch_is_on() -> None:
    store, _ = _store()
    doc = "schema_version: 1\nmanifest:\n  identity: []\n"
    apply, provision, runtime = (
        _occasion(ComposeOccasion.MANIFEST_APPLY),
        _occasion(ComposeOccasion.PROVISION),
        _occasion(ComposeOccasion.RUNTIME),
    )
    # A manifest apply's redeliver is the platform's, manifest or no manifest
    # (the apply that just ran is what the store holds).
    assert _reader(store, doc).platform_owns(apply) is True
    assert _reader(store, None).platform_owns(apply) is True
    # The first artifact is the platform's only for a bot that carries a manifest.
    assert _reader(store, doc).platform_owns(provision) is True
    assert _reader(store, None).platform_owns(provision) is False
    # A runtime edit is the engine's, manifest or no manifest.
    assert _reader(store, doc).platform_owns(runtime) is False
    # The engine decision is the reader's: another family gets nothing, switch or no switch.
    assert _reader(store, doc).platform_owns(_occasion(ComposeOccasion.MANIFEST_APPLY, "openclaw")) is False
    # And the switch gates everything.
    assert _reader(store, doc, switch=False).platform_owns(apply) is False
    assert _reader(store, doc, switch=False).platform_owns(provision) is False


def test_the_reader_yields_collector_shaped_refs_from_the_store() -> None:
    # The reader scopes by ("staff", req.user_id, req.bot_id) — the same
    # address the write side used, so no patching is needed.
    store, _ = _store()
    store.put(_SCOPE, category=CATEGORY_IDENTITY, name="RULES.md", rel_path="identity/RULES.md", content=b"r")
    store.put(_SCOPE, category=CATEGORY_RESOURCES, name="kb/a.md", rel_path="workspace/kb/a.md", content=b"a")
    store.put(_SCOPE, category=CATEGORY_SKILLS, name="order-lookup", rel_path="workspace/skills-local/order-lookup/SKILL.md", content=b"s")
    store.put(_SCOPE, category=CATEGORY_SKILLS, name="order-lookup", rel_path="workspace/skills-local/order-lookup/scripts/run.py", content=b"p")
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


def test_a_skill_named_like_a_layout_segment_keeps_its_own_prefix() -> None:
    """``teclaw`` and ``workspace`` are legal skill names; the package prefix
    is built from the layout, not found by searching the ref path."""
    store, _ = _store()
    for name in ("workspace", "teclaw"):
        store.put(_SCOPE, category=CATEGORY_SKILLS, name=name, rel_path=f"workspace/skills-local/{name}/SKILL.md", content=b"s")
    skills = _reader(store, None).skills(_REQ)
    assert [(s.name, s.path) for s in skills] == [
        ("teclaw", "staff_u1/bot7_manifest/teclaw/workspace/skills-local/teclaw"),
        ("workspace", "staff_u1/bot7_manifest/teclaw/workspace/skills-local/workspace"),
    ]
