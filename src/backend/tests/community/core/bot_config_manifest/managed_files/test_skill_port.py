"""The store-backed skill package port drives the real ``SkillsMaterialiser`` (W8).

No device anywhere: the package is validated by the production validator,
unpacked into the index under ``workspace/skills-local/<name>/``, recorded as
a skill row with the teclaw layout's own ``local://skills-local/<name>``
locator, and activated record-only. The second apply of the same document
plans ``unchanged`` from the index and writes nothing.
"""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from typing import Any

from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import EntryFetcher
from agentclaw.community.core.bot_config_manifest.apply.materialisers.skills import (
    SkillsMaterialiser,
)
from agentclaw.community.core.bot_config_manifest.managed_files import (
    CATEGORY_SKILLS,
    ManagedFileScope,
    ManagedFilesStore,
)
from agentclaw.community.core.bot_config_manifest.managed_files.ports import (
    ManagedSkillOwnerConflict,
    PlatformSkillPackageUpload,
)

from tests.community.core.bot_config_manifest.apply._fakes import (
    FakeActivationService,
    FakeCredentials,
    FakeGuardedFetcher,
    FakeManifestContent,
    build_skill_zip,
    fetched_object,
    make_context,
    real_validator,
)

from ._fakes import FakeObjectStorage

_BASE = "teclaw/dev/bolt_data"
_SCOPE = ManagedFileScope(entity_type="staff", entity_id="u_owner", bot_id="b_1")
QC_URL = "https://example.test/skills/quality-check.zip"
QZ = build_skill_zip("quality-check", extra=[("scripts/run.sh", b"echo ok\n")])
QZ_V2 = build_skill_zip("quality-check", extra=[("scripts/run.sh", b"echo v2\n")])


def _run(coro):
    return asyncio.run(coro)


def _digest_of(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


class FakeSkillRepository:
    """The four ``SkillRepository`` calls the port makes, over a dict."""

    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.creates: list[dict[str, Any]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self._next = 100

    def list_bot_local_by_name(self, *, bot_id: str, name: str) -> list[dict]:
        return [
            dict(r)
            for r in self.rows.values()
            if r["bolt_id"] == bot_id
            and r["name"] == name
            and str(r["git_path"]).startswith("local://")
        ]

    def create(self, skill_data: dict) -> dict:
        self._next += 1
        row = {"id": self._next, **skill_data}
        self.rows[self._next] = row
        self.creates.append(dict(row))
        return dict(row)

    def update(self, skill_id: str, skill_data: dict) -> dict | None:
        row = self.rows.get(int(skill_id))
        if row is None:
            return None
        row.update(skill_data)
        self.updates.append((skill_id, dict(skill_data)))
        return dict(row)


class LiveCapabilityReader:
    """The active set as the record-only activation left it."""

    def __init__(self, skills: FakeSkillRepository, activation: FakeActivationService) -> None:
        self._skills = skills
        self._activation = activation

    def active_skill_assets(self, *, bot_id: str, owner_id: str, bot=None):
        return tuple(
            SimpleNamespace(skill_id=row["id"], name=row["name"], git_path=row["git_path"])
            for row in self._skills.rows.values()
            if row["id"] in self._activation.installed_skills
        )

    def member_skill_ids(self, *, bot):
        return frozenset()


def _rig(packages: dict[str, bytes]):
    oss = FakeObjectStorage()
    store = ManagedFilesStore(
        object_storage=oss, store_base=lambda: _BASE
    )
    skills = FakeSkillRepository()
    activation = FakeActivationService()
    port = PlatformSkillPackageUpload(
        store,
        validator=real_validator(),
        skill_repository=skills,
    )
    fetcher = FakeGuardedFetcher(
        responses={
            url: fetched_object(body, url=url, content_type="application/zip")
            for url, body in packages.items()
        }
    )
    pipeline = EntryFetcher(fetcher, FakeManifestContent(), FakeCredentials())
    materialiser = SkillsMaterialiser(
        port, activation, LiveCapabilityReader(skills, activation), real_validator(), pipeline
    )
    return materialiser, store, oss, skills, activation, fetcher


async def _apply(materialiser, ctx, entries):
    resolved = await materialiser.resolve(ctx, entries)
    assert resolved.ok, resolved.failures
    plan = await materialiser.plan(ctx, resolved.intents)
    written = await materialiser.write(ctx, plan)
    return plan, written


def _ctx():
    return make_context(engine_type="teclaw", owner_id="u_owner")


def _declared(url: str = QC_URL, body: bytes = QZ):
    return {"name": "quality-check", "source": url, "digest": _digest_of(body)}


def _indexed(store) -> list[tuple[str, str]]:
    return sorted((r.name, r.rel_path) for r in store.list(_SCOPE, category=CATEGORY_SKILLS))


# ── first apply: unpacked, indexed, recorded, activated ────────────────────


def test_a_declared_skill_is_unpacked_into_the_store_and_recorded() -> None:
    materialiser, store, oss, skills, activation, _ = _rig({QC_URL: QZ})

    plan, written = _run(_apply(materialiser, _ctx(), [_declared()]))

    assert [e.outcome.value for e in written] == ["created"]
    # Every package file, under the teclaw local-skill prefix, named by the skill.
    assert _indexed(store) == [
        ("quality-check", "workspace/skills-local/quality-check/SKILL.md"),
        ("quality-check", "workspace/skills-local/quality-check/scripts/run.sh"),
    ]
    rows = {r.rel_path: r for r in store.list(_SCOPE, category=CATEGORY_SKILLS)}
    assert oss.objects[rows["workspace/skills-local/quality-check/scripts/run.sh"].store_key] == b"echo ok\n"
    # The artifact ref path is the key minus the store base.
    assert rows["workspace/skills-local/quality-check/SKILL.md"].ref_path == (
        "staff_u_owner/b_1_manifest/teclaw/workspace/skills-local/quality-check/SKILL.md"
    )
    # The skill row the real road would write, with the teclaw layout's locator.
    assert len(skills.creates) == 1
    created = skills.creates[0]
    assert created["git_path"] == "local://skills-local/quality-check"
    assert (created["name"], created["user_id"], created["bolt_id"], created["source_type"]) == (
        "quality-check", "u_owner", "b_1", "upload"
    )
    # And activated with the id that row got.
    assert activation.skill_activations == [created["id"]]


# ── convergence from the store ─────────────────────────────────────────────


def test_the_second_apply_is_unchanged_and_writes_nothing() -> None:
    materialiser, store, oss, skills, activation, _ = _rig({QC_URL: QZ})
    _run(_apply(materialiser, _ctx(), [_declared()]))
    puts_before = list(oss.puts)

    plan, written = _run(_apply(materialiser, _ctx(), [_declared()]))

    assert [e.outcome.value for e in written] == ["unchanged"]
    assert oss.puts == puts_before
    assert skills.updates == [] and len(skills.creates) == 1
    assert activation.skill_activations == [skills.creates[0]["id"]]


def test_a_changed_package_is_replaced_in_place() -> None:
    materialiser, store, oss, skills, activation, fetcher = _rig({QC_URL: QZ})
    _run(_apply(materialiser, _ctx(), [_declared()]))
    created_id = skills.creates[0]["id"]

    # The same URL now serves a new package (a moved pin).
    fetcher.responses[QC_URL] = fetched_object(QZ_V2, url=QC_URL, content_type="application/zip")
    plan, written = _run(_apply(materialiser, _ctx(), [_declared(body=QZ_V2)]))

    assert [e.outcome.value for e in written] == ["updated"]
    rows = {r.rel_path: r for r in store.list(_SCOPE, category=CATEGORY_SKILLS)}
    assert oss.objects[rows["workspace/skills-local/quality-check/scripts/run.sh"].store_key] == b"echo v2\n"
    # The same row, updated in place — no second create, no re-activation.
    assert len(skills.creates) == 1
    assert [sid for sid, _ in skills.updates] == [str(created_id)]
    assert activation.skill_activations == [created_id]


def test_a_stale_member_of_a_replaced_package_is_dropped_from_the_store() -> None:
    _, store, oss, skills, _, _ = _rig({})
    port = PlatformSkillPackageUpload(
        store, validator=real_validator(), skill_repository=skills
    )
    with_extra = build_skill_zip("quality-check", extra=[("old/gone.txt", b"x")])
    _run(port.upload_local_skill(bot_id="b_1", owner_id="u_owner", actor_id="u_actor", package=with_extra))
    assert ("quality-check", "workspace/skills-local/quality-check/old/gone.txt") in _indexed(store)

    result = _run(port.upload_local_skill(bot_id="b_1", owner_id="u_owner", actor_id="u_actor", package=QZ))

    assert result["operation"] == "replaced"
    assert _indexed(store) == [
        ("quality-check", "workspace/skills-local/quality-check/SKILL.md"),
        ("quality-check", "workspace/skills-local/quality-check/scripts/run.sh"),
    ]
    assert len(oss.objects) == 2


def test_removal_deactivates_record_only() -> None:
    materialiser, store, oss, skills, activation, _ = _rig({QC_URL: QZ})
    _run(_apply(materialiser, _ctx(), [_declared()]))

    plan, written = _run(_apply(materialiser, _ctx(), []))

    assert plan.removals == ("quality-check",)
    assert activation.skill_deactivations == [skills.creates[0]["id"]]


# ── the port's own answers ─────────────────────────────────────────────────


def test_installed_digest_answers_from_the_store() -> None:
    _, store, oss, skills, _, _ = _rig({})
    port = PlatformSkillPackageUpload(
        store, validator=real_validator(), skill_repository=skills
    )

    def digest(name="quality-check"):
        return _run(
            port.installed_package_digest(bot={}, bot_id="b_1", owner_id="u_owner", name=name)
        )

    # Nothing stored: unknown, never equal.
    assert digest() is None

    _run(port.upload_local_skill(bot_id="b_1", owner_id="u_owner", actor_id="u_actor", package=QZ))
    # The materialiser's own identity of the content: sha256 of the canonical zip.
    assert digest() == _digest_of(real_validator().validate_zip(QZ).canonical_zip)
    assert digest("other") is None

    # A member whose object is gone: the listing is the record, so the
    # package is now a different package — never equal to the declared one,
    # and the next apply writes it again.
    full = digest()
    row = next(r for r in store.list(_SCOPE, category=CATEGORY_SKILLS) if r.rel_path.endswith("SKILL.md"))
    del oss.objects[row.store_key]
    assert digest() != full


def test_a_same_name_row_owned_by_someone_else_is_never_replaced() -> None:
    import pytest

    _, store, oss, skills, _, _ = _rig({})
    # A collaborator's row, and a legacy row with no owner at all.
    skills.create({"name": "quality-check", "git_path": "local://skills-local/quality-check", "user_id": "u_other", "bolt_id": "b_1"})
    port = PlatformSkillPackageUpload(
        store, validator=real_validator(), skill_repository=skills
    )
    with pytest.raises(ManagedSkillOwnerConflict):
        _run(port.upload_local_skill(bot_id="b_1", owner_id="u_owner", actor_id="u_actor", package=QZ))
    assert skills.updates == [] and len(skills.creates) == 1
    skills.rows.clear()
    skills.creates.clear()
    skills.create({"name": "quality-check", "git_path": "local://skills-local/quality-check", "user_id": None, "bolt_id": "b_1"})
    with pytest.raises(ManagedSkillOwnerConflict):
        _run(port.upload_local_skill(bot_id="b_1", owner_id="u_owner", actor_id="u_actor", package=QZ))
    assert skills.updates == []
