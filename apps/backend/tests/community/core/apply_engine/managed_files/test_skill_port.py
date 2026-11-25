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
from unittest.mock import Mock

from agentclaw.community.core.bot_config_manifest.sources.source_resolver import DeclaredSourceResolver
from agentclaw.community.core.apply_engine.managed_files import (
    CATEGORY_SKILLS,
    ManagedFileScope,
    ManagedFilesStore,
)
from agentclaw.community.core.apply_engine.managed_files.ports import (
    ManagedSkillOwnerConflict,
    PlatformSkillPackageUpload,
)

from tests.community.core.apply_engine._fakes import (
    FakeActivationService,
    FakeManifestContent,
    FakeObjectCredentials,
    OSS_BUCKET,
    build_skill_zip,
    compiling_skills,
    make_context,
    oss_source,
    real_validator,
    seeded_object_store,
)

from ._fakes import FakeObjectStorage

_BASE = "teclaw/dev/bolt_data"
_SCOPE = ManagedFileScope(entity_type="staff", entity_id="u_owner", bot_id="b_1")
QC_KEY = "skills/quality-check.zip"
QZ = build_skill_zip("quality-check", extra=[("scripts/run.sh", b"echo ok\n")])
QZ_V2 = build_skill_zip("quality-check", extra=[("scripts/run.sh", b"echo v2\n")])


def _run(coro):
    return asyncio.run(coro)


def _digest_of(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


class FakeSkillRepository:
    """The ``SkillRepository`` calls the port makes, over a dict."""

    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.creates: list[dict[str, Any]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.delete_preflights: list[str] = []
        self.delete_blocker: Exception | None = None
        self._next = 100

    def list_bot_local_by_name(self, *, bot_id: str, name: str) -> list[dict]:
        return [
            dict(r)
            for r in self.rows.values()
            if r["bolt_id"] == bot_id
            and r["name"] == name
            and str(r["git_path"]).startswith("local://")
        ]

    def get_by_id(self, skill_id: str) -> dict | None:
        row = self.rows.get(int(skill_id))
        return dict(row) if row is not None else None

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

    def delete_bot_local_skill(self, *, skill_id: str, owner_id: str, bot_id: str):
        row = self.rows.get(int(skill_id))
        if row is None or row.get("user_id") != owner_id or row.get("bolt_id") != bot_id:
            return False
        del self.rows[int(skill_id)]
        return True

    def require_unreferenced_for_delete(self, skill_id: str) -> None:
        self.delete_preflights.append(skill_id)
        if self.delete_blocker is not None:
            raise self.delete_blocker


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

    def local_skill_assets(self, *, bot_id: str, owner_id: str, bot=None):
        return tuple(
            SimpleNamespace(
                skill_id=row["id"], name=row["name"], git_path=row["git_path"],
                mcp_dependencies=(),
            )
            for row in self._skills.rows.values()
            if row.get("bolt_id") == bot_id
            and row.get("user_id") == owner_id
            and str(row.get("git_path") or "").startswith("local://")
        )


def _rig(packages: dict[str, bytes]):
    """``packages`` is object key → package bytes, seeded into the bucket the
    declared ``oss`` source reads."""
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
        security_hook=Mock(),
    )
    objects = seeded_object_store(packages)
    content = FakeManifestContent()
    pipeline = DeclaredSourceResolver(content, FakeObjectCredentials(), objects)
    materialiser = compiling_skills(
        port, activation, LiveCapabilityReader(skills, activation), real_validator(), pipeline, content
    )
    return materialiser, store, oss, skills, activation, objects


async def _apply(materialiser, ctx, entries):
    resolved = await materialiser.resolve(ctx, entries)
    assert resolved.ok, resolved.failures
    plan = await materialiser.plan(ctx, resolved.intents)
    written = await materialiser.write(ctx, plan)
    return plan, written


def _ctx():
    return make_context(engine_type="teclaw", owner_id="u_owner")


def _declared(key: str = QC_KEY, body: bytes = QZ):
    return {
        "name": "quality-check",
        "source": oss_source(key),
        "digest": _digest_of(body),
    }


def _indexed(store) -> list[tuple[str, str]]:
    return sorted((r.name, r.rel_path) for r in store.list(_SCOPE, category=CATEGORY_SKILLS))


# ── first apply: unpacked, indexed, recorded, activated ────────────────────


def test_a_declared_skill_is_unpacked_into_the_store_and_recorded() -> None:
    materialiser, store, oss, skills, activation, _ = _rig({QC_KEY: QZ})

    plan, written = _run(_apply(materialiser, _ctx(), [_declared()]))

    assert [e.outcome.value for e in written.entries] == ["created"]
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


def test_the_second_apply_fully_replaces_the_package() -> None:
    materialiser, store, oss, skills, activation, _ = _rig({QC_KEY: QZ})
    _run(_apply(materialiser, _ctx(), [_declared()]))
    plan, written = _run(_apply(materialiser, _ctx(), [_declared()]))

    assert [e.outcome.value for e in written.entries] == ["updated"]
    assert len(skills.updates) == 1 and len(skills.creates) == 1
    assert activation.skill_activations == [
        skills.creates[0]["id"], skills.creates[0]["id"]
    ]


def test_a_changed_package_is_replaced_in_place() -> None:
    materialiser, store, oss, skills, activation, objects = _rig({QC_KEY: QZ})
    _run(_apply(materialiser, _ctx(), [_declared()]))
    created_id = skills.creates[0]["id"]

    # The same URL now serves a new package (a moved pin).
    objects.put(OSS_BUCKET, QC_KEY, QZ_V2)
    plan, written = _run(_apply(materialiser, _ctx(), [_declared(body=QZ_V2)]))

    assert [e.outcome.value for e in written.entries] == ["updated"]
    rows = {r.rel_path: r for r in store.list(_SCOPE, category=CATEGORY_SKILLS)}
    assert oss.objects[rows["workspace/skills-local/quality-check/scripts/run.sh"].store_key] == b"echo v2\n"
    # The same row, updated in place — no second create; Direct is reasserted.
    assert len(skills.creates) == 1
    assert [sid for sid, _ in skills.updates] == [str(created_id)]
    assert activation.skill_activations == [created_id, created_id]


def test_a_stale_member_of_a_replaced_package_is_dropped_from_the_store() -> None:
    _, store, oss, skills, _, _ = _rig({})
    port = PlatformSkillPackageUpload(
        store, validator=real_validator(), skill_repository=skills,
        security_hook=Mock(),
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


def test_removal_deactivates_and_physically_deletes_the_local_asset() -> None:
    materialiser, store, oss, skills, activation, _ = _rig({QC_KEY: QZ})
    _run(_apply(materialiser, _ctx(), [_declared()]))

    plan, written = _run(_apply(materialiser, _ctx(), []))

    assert plan.removals == ("quality-check",)
    assert activation.skill_deactivations == [skills.creates[0]["id"]]
    assert _indexed(store) == []
    assert skills.rows == {}


def test_removal_checks_references_before_deleting_store_objects() -> None:
    import pytest

    _, store, _oss, skills, _, _ = _rig({})
    port = PlatformSkillPackageUpload(
        store, validator=real_validator(), skill_repository=skills,
        security_hook=Mock(),
    )
    uploaded = _run(
        port.upload_local_skill(
            bot_id="b_1", owner_id="u_owner", actor_id="u_actor", package=QZ
        )
    )
    skill_id = str(uploaded["skill"]["id"])
    indexed_before = _indexed(store)
    skills.delete_blocker = RuntimeError("SKILL_ASSET_IN_USE")

    with pytest.raises(RuntimeError, match="SKILL_ASSET_IN_USE"):
        _run(
            port.delete_local_skill(
                skill_id=skill_id,
                name="quality-check",
                bot_id="b_1",
                owner_id="u_owner",
                actor_id="u_actor",
            )
        )

    assert skills.delete_preflights == [skill_id]
    assert _indexed(store) == indexed_before
    assert int(skill_id) in skills.rows


def test_platform_delete_records_offline_sync_after_asset_delete() -> None:
    _, store, _oss, skills, _, _ = _rig({})
    hook = Mock()
    port = PlatformSkillPackageUpload(
        store, validator=real_validator(), skill_repository=skills,
        security_hook=hook,
    )
    uploaded = _run(port.upload_local_skill(
        bot_id="b_1", owner_id="u_owner", actor_id="u_actor", package=QZ,
    ))
    skill_id = str(uploaded["skill"]["id"])
    hook.record_deletion.side_effect = RuntimeError("observer unavailable")

    _run(port.delete_local_skill(
        skill_id=skill_id, name="quality-check", bot_id="b_1",
        owner_id="u_owner", actor_id="u_actor",
    ))

    assert int(skill_id) not in skills.rows
    hook.record_deletion.assert_called_once_with(
        bot_id="b_1", owner_id="u_owner", actor_id="u_actor",
        skill_id=int(skill_id),
    )


# ── the port's own answers ─────────────────────────────────────────────────


def test_manifest_upload_port_has_no_installed_digest_probe() -> None:
    _, store, oss, skills, _, _ = _rig({})
    port = PlatformSkillPackageUpload(
        store, validator=real_validator(), skill_repository=skills,
        security_hook=Mock(),
    )

    assert not hasattr(port, "installed_package_digest")


def test_a_same_name_row_owned_by_someone_else_is_never_replaced() -> None:
    import pytest

    _, store, oss, skills, _, _ = _rig({})
    # A collaborator's row, and a legacy row with no owner at all.
    skills.create({"name": "quality-check", "git_path": "local://skills-local/quality-check", "user_id": "u_other", "bolt_id": "b_1"})
    port = PlatformSkillPackageUpload(
        store, validator=real_validator(), skill_repository=skills,
        security_hook=Mock(),
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


# ── ensure_dst_local_skill_asset (bot-file-synced member registration) ──────
#
# The port stores its package through BOT_FILE_SYNC and registers the dst
# ``ac_skill`` row separately. The three branches below exercise every line
# of ``_ensure_dst_local_skill_asset`` through the public async wrapper:
# ``asyncio.to_thread`` runs the sync body in a worker, so the test asserts
# the wrapper's awaitable return, not the sync result directly.


def _ensure_port(skills: FakeSkillRepository) -> PlatformSkillPackageUpload:
    # ``_ensure_dst_local_skill_asset`` only touches ``self._skills``: neither
    # the store, the validator nor the security hook are exercised. Real
    # construction (real store/validator) keeps the test honest about the
    # port's own wiring, but a Mock store is enough — the seam never reads it.
    return PlatformSkillPackageUpload(
        Mock(), validator=Mock(), skill_repository=skills, security_hook=Mock(),
    )


def _src_row(*, description: str = "synced payload") -> dict[str, Any]:
    # A source-bot local-skill row reverse-looked-up by name+owner.
    return {
        "id": 41, "name": "quality-check", "description": description,
        "git_path": "local://skills-local/quality-check",
        "user_id": "u_src", "bolt_id": "src-bot",
    }


def test_ensure_dst_local_skill_asset_creates_row_when_no_existing_match() -> None:
    import pytest  # noqa: F401  # kept for parity with the suite's import style

    skills = FakeSkillRepository()
    skills.rows[41] = _src_row()
    port = _ensure_port(skills)

    result = _run(port.ensure_dst_local_skill_asset(
        bot_id="b_1", owner_id="u_owner", actor_id="u_actor",
        source_bot_id="src-bot", source_owner_id="u_src", name="quality-check",
    ))

    # Created on a fresh locator with the source description propagated; the
    # operation matches the materialiser's expected ``created`` string and the
    # inactive ``active=False`` state the prepare-only path requires.
    assert result["operation"] == "created"
    assert result["actor_id"] == "u_actor"
    assert result["skill"]["name"] == "quality-check"
    assert result["skill"]["description"] == "synced payload"
    assert result["skill"]["git_path"] == "local://skills-local/quality-check"
    assert result["skill"]["user_id"] == "u_owner"
    assert result["skill"]["bolt_id"] == "b_1"
    assert result["skill"]["active"] is False
    assert len(skills.creates) == 1
    created = skills.creates[0]
    assert created["name"] == "quality-check"
    assert created["source_type"] == "upload"
    assert created["git_path"] == "local://skills-local/quality-check"
    assert skills.updates == []


def test_ensure_dst_local_skill_asset_replaces_existing_match_in_place() -> None:
    import pytest  # noqa: F401

    skills = FakeSkillRepository()
    # Source bot owns the row reverse-looked up by name + source_owner.
    skills.rows[41] = _src_row(description="fresh description")
    # The dst bot already owns the same-name row that file-sync owns the
    # package under: ``_own_row`` returns it, the replace branch runs.
    existing_id = 101
    skills.rows[existing_id] = {
        "id": existing_id, "name": "quality-check",
        "description": "stale", "git_path": "local://skills-local/quality-check",
        "user_id": "u_owner", "bolt_id": "b_1",
    }
    port = _ensure_port(skills)

    result = _run(port.ensure_dst_local_skill_asset(
        bot_id="b_1", owner_id="u_owner", actor_id="u_actor",
        source_bot_id="src-bot", source_owner_id="u_src", name="quality-check",
    ))

    # ``replaced`` (not ``updated`` — the port deliberately diverges from the
    # upload road here); the locator is reasserted, the description is refreshed
    # from the source row, and no new create ran.
    assert result["operation"] == "replaced"
    assert result["skill"]["id"] == existing_id
    assert result["skill"]["description"] == "fresh description"
    assert result["skill"]["git_path"] == "local://skills-local/quality-check"
    assert result["skill"]["active"] is False
    assert skills.creates == []
    assert len(skills.updates) == 1
    sid, values = skills.updates[0]
    assert sid == str(existing_id)
    assert values == {
        "description": "fresh description",
        "git_path": "local://skills-local/quality-check",
    }


def test_ensure_dst_local_skill_asset_raises_not_found_when_source_has_no_owned_row() -> None:
    import pytest
    from agentclaw.community.core.skill_center.errors import (
        LocalSkillNotFoundError,
    )

    skills = FakeSkillRepository()
    # Source bot has a legacy unowned row for this name: ``src_owned`` filters
    # to zero rows and the manifest-vs-source disagreement surfaces as NotFound.
    skills.rows[41] = {
        "id": 41, "name": "quality-check", "description": "x",
        "git_path": "local://skills-local/quality-check",
        "user_id": "somebody-else", "bolt_id": "src-bot",
    }
    port = _ensure_port(skills)

    with pytest.raises(LocalSkillNotFoundError):
        _run(port.ensure_dst_local_skill_asset(
            bot_id="b_1", owner_id="u_owner", actor_id="u_actor",
            source_bot_id="src-bot", source_owner_id="u_src", name="quality-check",
        ))

    # Nothing on the dst bot was written.
    assert skills.creates == [] and skills.updates == []


def test_ensure_dst_local_skill_asset_raises_not_found_when_source_has_several_owned_rows() -> None:
    import pytest
    from agentclaw.community.core.skill_center.errors import (
        LocalSkillNotFoundError,
    )

    skills = FakeSkillRepository()
    # Two source rows owned by the same owner — the manifest and source bot
    # disagree, the same NotFound currency the upload road uses.
    skills.rows[41] = _src_row(description="a")
    skills.rows[42] = _src_row(description="b")
    port = _ensure_port(skills)

    with pytest.raises(LocalSkillNotFoundError):
        _run(port.ensure_dst_local_skill_asset(
            bot_id="b_1", owner_id="u_owner", actor_id="u_actor",
            source_bot_id="src-bot", source_owner_id="u_src", name="quality-check",
        ))

    assert skills.creates == [] and skills.updates == []


def test_ensure_dst_local_skill_asset_uses_empty_description_when_source_row_omits_it() -> None:
    # ``description = src.get("description") or ""`` — a missing/None description
    # on the source row must surface as the empty string, never ``None``.
    skills = FakeSkillRepository()
    skills.rows[41] = {
        "id": 41, "name": "quality-check", "description": None,
        "git_path": "local://skills-local/quality-check",
        "user_id": "u_src", "bolt_id": "src-bot",
    }
    port = _ensure_port(skills)

    result = _run(port.ensure_dst_local_skill_asset(
        bot_id="b_1", owner_id="u_owner", actor_id="u_actor",
        source_bot_id="src-bot", source_owner_id="u_src", name="quality-check",
    ))

    assert result["operation"] == "created"
    assert result["skill"]["description"] == ""
    assert skills.creates[0]["description"] == ""


def test_ensure_dst_local_skill_asset_replaced_path_treats_update_returning_none_as_existing() -> None:
    # The branch ``or existing`` falls back to the existing row when the repo's
    # ``update`` returns ``None`` (e.g. a concurrently deleted row) — the
    # registration still succeeds with the row the materialiser was about to
    # write, never with ``None``.
    skills = FakeSkillRepository()
    skills.rows[41] = _src_row(description="fresh")
    existing_id = 101
    skills.rows[existing_id] = {
        "id": existing_id, "name": "quality-check", "description": "stale",
        "git_path": "local://skills-local/quality-check",
        "user_id": "u_owner", "bolt_id": "b_1",
    }
    # Force ``update`` to return None: a stub class wrapping the real fake.
    class _UpdateReturnsNone(FakeSkillRepository):
        def update(self, skill_id, skill_data):  # type: ignore[override]
            super().update(skill_id, skill_data)
            return None
    skills = _UpdateReturnsNone()
    skills.rows[41] = _src_row(description="fresh")
    skills.rows[existing_id] = {
        "id": existing_id, "name": "quality-check", "description": "stale",
        "git_path": "local://skills-local/quality-check",
        "user_id": "u_owner", "bolt_id": "b_1",
    }
    port = _ensure_port(skills)

    result = _run(port.ensure_dst_local_skill_asset(
        bot_id="b_1", owner_id="u_owner", actor_id="u_actor",
        source_bot_id="src-bot", source_owner_id="u_src", name="quality-check",
    ))

    assert result["operation"] == "replaced"
    # The fallback keeps the existing description untouched and the same id.
    assert result["skill"]["id"] == existing_id
    assert result["skill"]["description"] == "stale"
