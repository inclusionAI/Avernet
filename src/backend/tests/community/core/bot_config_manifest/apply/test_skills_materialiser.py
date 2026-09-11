"""Tests for the ``skills`` materialiser (W5).

What these pin, by the work item's acceptance criteria:

- declared skills travel the **manual-upload road**: fetched bytes validated
  by the same ``SkillPackageValidator`` the router path uses, handed to
  ``upload_local_skill``, then direct activation — indistinguishable from a
  user's upload because it is one;
- non-git skills are digest-pinned (validator + fetcher enforce it);
- the package's own front-matter name must equal the entry's ``name``;
- the area is the **active skill set**, narrowed by the Set-governed members
  (never removals, never declarable), tar.gz/subpath/unpack all land, one
  fetch failure aborts the category, re-applies converge with zero writes.
"""
from __future__ import annotations

import asyncio
import importlib
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace


from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetcher,
)
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.apply.source_fetchers import (
    object_receipt_url,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
)

from ._fakes import (
    FakeActivationService,
    FakeCapabilityReader,
    FakeGuardedFetcher,
    FakeManifestContent,
    FakeObjectCredentials,
    FakeSkillUploadService,
    OSS_BUCKET,
    build_skill_tgz,
    build_skill_zip,
    declared_session,
    fetched_object,
    make_context,
    oss_source,
    real_validator,
    seeded_object_store,
    skill_asset,
)

#: The object every skills fixture is declared against. A skills entry names
#: a declared source; the key's own suffix is what ``_archive_kind`` reads.
QC_KEY = "skills/quality-check.zip"
#: The W11 identity bytes read out of the bucket are filed under — what a
#: pre-filed receipt (a keep_last copy, a dry run's leftover) is keyed by.
QC_RECEIPT = object_receipt_url(OSS_BUCKET, QC_KEY)


def _run(coro):
    return asyncio.run(coro)


def _ctx(**kwargs):
    kwargs.setdefault("engine_type", "openclaw")
    kwargs.setdefault("owner_id", "u_owner")
    # Every declared source is resolved against the apply's source session,
    # so a rig that declares one hands the context one.
    kwargs.setdefault("source_session", declared_session())
    return make_context(**kwargs)


def _digest_of(body: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(body).hexdigest()


async def _apply(materialiser, ctx, entries):
    resolved = await materialiser.resolve(ctx, entries)
    if not resolved.ok:
        return resolved, None, None
    plan = await materialiser.plan(ctx, resolved.intents)
    written = await materialiser.write(ctx, plan)
    return resolved, plan, written


def skill_rig(
    *,
    packages: dict[str, bytes] | None = None,
    assets: list | None = None,
    member_ids: set[int] | None = None,
) -> tuple:
    """(materialiser, uploads, activation, reader, objects, content).

    ``packages`` seeds the bucket a declared ``oss`` source reads: object key
    → the package's bytes. A source that has died is modelled on the store
    (``objects.make_unavailable``), which is the road a skills entry takes.
    """
    from agentclaw.community.core.bot_config_manifest.apply.materialisers.skills import (
        SkillsMaterialiser,
    )

    uploads = FakeSkillUploadService()
    activation = FakeActivationService()
    reader = FakeCapabilityReader(assets=assets, member_ids=member_ids)
    objects = seeded_object_store(packages)
    content = FakeManifestContent()
    pipeline = EntryFetcher(
        FakeGuardedFetcher(responses={}),
        content,
        FakeObjectCredentials(),
        objects,
    )
    materialiser = SkillsMaterialiser(
        uploads, activation, reader, real_validator(), pipeline
    )
    return materialiser, uploads, activation, reader, objects, content


QZ = build_skill_zip("quality-check")


def _declared(name: str = "quality-check", key: str = QC_KEY, digest: str | None = None):
    entry = {
        "name": name,
        "source": oss_source(key),
        "digest": digest or _digest_of(QZ),
    }
    return entry


# ── declarations upload and activate ────────────────────────────────────────


def test_a_declared_skill_uploads_the_validated_package_and_activates():
    materialiser, uploads, activation, _, objects, _ = skill_rig(
        packages={QC_KEY: QZ}
    )
    result, plan, written = _run(
        _apply(materialiser, _ctx(), [_declared()])
    )

    assert result.ok
    assert [e.outcome.value for e in written] == ["created"]
    # One upload, of a package the real validator accepts, named by its own
    # front matter — the same entry point the raw-zip router path takes.
    assert [call["name"] for call in uploads.uploads] == ["quality-check"]
    real_validator().validate_zip(uploads.uploads[0]["package"])
    assert [call["actor_id"] for call in uploads.uploads] == ["u_actor"]
    # And one activation, of the id the upload created.
    activated_id = activation.skill_activations[0]
    assert activated_id == uploads.rows["quality-check"]["id"]


def test_the_second_apply_of_an_unchanged_document_performs_no_writes():
    materialiser, uploads, activation, reader, objects, content = skill_rig(
        packages={QC_KEY: QZ}
    )
    entries = [_declared()]
    _run(_apply(materialiser, _ctx(), entries))
    assert len(uploads.uploads) == 1

    # World reflow between the applies: the upload is registered and active,
    # the receipt from the first apply is filed — what the next apply reads.
    qc_id = uploads.rows["quality-check"]["id"]
    reader.assets = (skill_asset(qc_id, "quality-check"),)

    _, plan, second = _run(_apply(materialiser, _ctx(), entries))
    assert [e.outcome.value for e in second] == ["unchanged"]
    assert len(uploads.uploads) == 1  # zero further uploads
    assert activation.writes == 1  # and zero further activations
    assert len(objects.calls) == 1  # only the first apply hit the store;
    # the second was answered by the platform's own copy of the pinned bytes


def test_an_inactive_same_name_skill_is_reinstated_as_created():
    """A deactivated local skill the manifest now declares: the area (the
    ACTIVE set) does not contain it, so it is classed for creation — the
    upload service's same-name replace handles the row, the activation makes
    it active."""
    materialiser, uploads, activation, _, _, _ = skill_rig(packages={QC_KEY: QZ})
    result, plan, written = _run(_apply(materialiser, _ctx(), [_declared()]))
    assert [e.outcome.value for e in written] == ["created"]
    assert uploads.uploads and activation.skill_activations


def test_one_failed_fetch_aborts_the_whole_category_no_writes():
    other_key = "skills/order-lookup.zip"
    other = build_skill_zip("order-lookup")
    materialiser, uploads, activation, _, objects, _ = skill_rig(
        packages={QC_KEY: QZ, other_key: other},
    )
    # The second entry's source is a bucket the store cannot reach — the
    # transport failure, not a refusal, and nothing is filed to fall back to.
    objects.make_unavailable("down-bucket", "source transport failed")
    resolved = _run(
        materialiser.resolve(
            _ctx(),
            [
                _declared(),
                {
                    "name": "order-lookup",
                    "source": oss_source(other_key, bucket="down-bucket"),
                    "digest": _digest_of(other),
                },
            ],
        )
    )
    assert not resolved.ok
    failures = {f.identity: f.reason for f in resolved.failures}
    assert "source transport failed" in failures["order-lookup"]
    assert uploads.uploads == []
    assert activation.writes == 0


def test_a_digest_that_the_source_no_longer_matches_fails_the_entry():
    stale_pin = "sha256:" + "1" * 64
    materialiser, uploads, _, _, _, _ = skill_rig(packages={QC_KEY: QZ})
    resolved = _run(
        materialiser.resolve(_ctx(), [_declared(digest=stale_pin)])
    )
    assert not resolved.ok  # the pin disagrees with what is actually served
    assert stale_pin in resolved.failures[0].reason
    assert uploads.uploads == []


def test_the_packages_own_name_must_equal_the_declared_name():
    materialiser, uploads, _, _, _, _ = skill_rig(packages={QC_KEY: QZ})
    resolved = _run(
        materialiser.resolve(_ctx(), [_declared(name="different-name")])
    )
    assert not resolved.ok
    reason = resolved.failures[0].reason
    assert "quality-check" in reason and "different-name" in reason
    assert uploads.uploads == []


def test_inline_content_is_refused_by_the_materialiser_too():
    # The validator refuses it at PUT; the belt keeps a hand-built apply
    # honest — there is no package to build from inline text.
    materialiser, uploads, _, _, _, _ = skill_rig()
    resolved = _run(
        materialiser.resolve(_ctx(), [{"name": "qc", "content": "not a package"}])
    )
    assert not resolved.ok
    assert uploads.uploads == []


# ── archives: tar.gz, subpath, unpack override, detection ──────────────────


def test_a_tar_gz_source_with_a_subpath_flattens_to_the_skill():
    archive = build_skill_tgz(
        [
            ("pkg/quality-check/SKILL.md", _manifest_bytes("quality-check")),
            ("pkg/README", b"wrapper readme the subtree must drop"),
        ]
    )
    key = "skills/qc.tar.gz"
    materialiser, uploads, activation, _, _, _ = skill_rig(packages={key: archive})
    result, plan, written = _run(
        _apply(
            materialiser,
            _ctx(),
            [
                {
                    "name": "quality-check",
                    "source": oss_source(key),
                    "digest": _digest_of(archive),
                    "subpath": "pkg/quality-check",
                }
            ],
        )
    )
    assert result.ok
    assert [e.outcome.value for e in written] == ["created"]
    assert uploads.uploads[0]["name"] == "quality-check"
    # The canonical package came from the subtree: wrapper files are gone.
    with zipfile.ZipFile(io.BytesIO(uploads.uploads[0]["package"])) as ziparchive:
        assert ziparchive.namelist() == ["SKILL.md"]


def test_a_declared_unpack_overrides_an_unhelpful_key():
    key = "skills/get-package"
    materialiser, uploads, _, _, _, _ = skill_rig(packages={key: QZ})
    resolved = _run(
        materialiser.resolve(
            _ctx(),
            [
                {
                    "name": "quality-check",
                    "source": oss_source(key),
                    "digest": _digest_of(QZ),
                    "unpack": "zip",
                }
            ],
        )
    )
    assert resolved.ok
    assert resolved.intents[0].identity == "quality-check"


def test_a_zip_key_is_detected_from_its_suffix_without_unpack():
    key = "skills/anything.zip"
    materialiser, _, _, _, _, _ = skill_rig(packages={key: QZ})
    resolved = _run(
        materialiser.resolve(_ctx(), [_declared(key=key)])
    )
    assert resolved.ok


def test_an_undetectable_archive_kind_fails_with_a_readable_reason():
    # Nothing names the shape: the key carries no archive suffix, the object
    # road serves no content type, and the entry declares no ``unpack``.
    key = "skills/get-package"
    materialiser, uploads, _, _, _, _ = skill_rig(packages={key: QZ})
    resolved = _run(
        materialiser.resolve(_ctx(), [_declared(key=key)])
    )
    assert not resolved.ok
    assert "unpack" in resolved.failures[0].reason
    assert uploads.uploads == []


def test_a_subpath_that_selects_nothing_fails():
    archive = build_skill_tgz([("other/SKILL.md", _manifest_bytes("other"))])
    key = "skills/qc.tar.gz"
    materialiser, _, _, _, _, _ = skill_rig(packages={key: archive})
    resolved = _run(
        materialiser.resolve(
            _ctx(),
            [
                {
                    "name": "quality-check",
                    "source": oss_source(key),
                    "digest": _digest_of(archive),
                    "subpath": "pkg/quality-check",
                }
            ],
        )
    )
    assert not resolved.ok
    assert "subpath" in resolved.failures[0].reason


def test_an_oversized_package_fails_at_resolve_not_mid_write():
    huge = build_skill_zip(
        "quality-check",
        extra=[("big.bin", b"x" * (10 * 1024 * 1024 + 1))],
    )
    key = "skills/qc.zip"
    materialiser, uploads, activation, _, _, _ = skill_rig(packages={key: huge})
    resolved = _run(
        materialiser.resolve(_ctx(), [_declared(key=key, digest=_digest_of(huge))])
    )
    assert not resolved.ok  # the package limit is the upload's, asked up front
    assert uploads.uploads == []
    assert activation.writes == 0


# ── the area: overwrite, governance narrowing, declared-empty ──────────────


def test_skills_empty_removes_every_directly_active_skill():
    materialiser, uploads, activation, reader, _, _ = skill_rig(
        assets=[
            skill_asset(11, "alpha"),
            skill_asset(12, "beta"),
        ],
    )
    _, plan, written = _run(_apply(materialiser, _ctx(), []))
    assert plan.removals == ("alpha", "beta")
    assert activation.skill_deactivations == [11, 12]
    assert uploads.uploads == []


def test_set_governed_skills_are_never_removals():
    # A skill one of the bot's Sets supplies is not the manifest's to remove:
    # the write would refuse it, so the plan refuses to plan it.
    materialiser, _, activation, reader, _, _ = skill_rig(
        assets=[
            skill_asset(11, "alpha"),
            skill_asset(12, "set-supplied"),
        ],
        member_ids={12},
    )
    _, plan, _ = _run(_apply(materialiser, _ctx(), []))
    assert plan.removals == ("alpha",)
    assert activation.skill_deactivations == [11]


def test_a_declared_name_matching_a_set_governed_skill_fails_at_resolve():
    materialiser, uploads, activation, reader, _, _ = skill_rig(
        assets=[skill_asset(12, "quality-check")],
        member_ids={12},
        packages={QC_KEY: QZ},
    )
    resolved = _run(materialiser.resolve(_ctx(), [_declared()]))
    assert not resolved.ok
    assert "skill set" in resolved.failures[0].reason
    assert uploads.uploads == []
    assert activation.writes == 0
    assert reader.asset_reads  # the conflict was asked before the fetch…


def test_a_declared_name_matching_a_non_local_active_skill_fails():
    # git:// skills are shared assets: a runtime name collision is refused
    # before any bytes spend, because install would refuse it mid-write.
    materialiser, uploads, _, _, _, _ = skill_rig(
        assets=[skill_asset(9, "quality-check", git_path="git://default/x")],
        packages={QC_KEY: QZ},
    )
    resolved = _run(materialiser.resolve(_ctx(), [_declared()]))
    assert not resolved.ok
    assert "non-local" in resolved.failures[0].reason
    assert uploads.uploads == []


def test_undeclared_members_of_the_area_are_removed_by_name():
    # A first apply over an area holding one declared and one other skill:
    # the declaration uploads its package (no receipt existed yet), and the
    # other, undeclared member leaves the area.
    materialiser, uploads, activation, reader, _, _ = skill_rig(
        assets=[
            skill_asset(11, "some-other-skill"),
            skill_asset(12, "quality-check"),
        ],
        packages={QC_KEY: QZ},
    )
    _, plan, written = _run(_apply(materialiser, _ctx(), [_declared()]))
    assert [e.outcome.value for e in written] == ["updated"]
    assert plan.removals == ("some-other-skill",)
    assert activation.skill_deactivations == [11]
    assert uploads.uploads[0]["name"] == "quality-check"
    assert activation.skill_activations == []  # already active: not re-activated


# ── parity with the upload path, by construction ───────────────────────────


def test_the_module_writes_through_the_upload_service_only():
    """AST import guard: this module may not reach the storage, repository or
    package-packing internals of the upload flow — one service, one
    activation service, the reader, and the validator. A side door would
    install unregistered files (the D3 lesson)."""
    import ast

    module = importlib.import_module(
        "agentclaw.community.core.bot_config_manifest.apply.materialisers.skills"
    )
    source = Path(module.__file__).read_text()
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for module_name in imported:
        assert "local_skill_upload" not in module_name or "protocol" in module_name
        assert "skill_repo" not in module_name
        assert "storage" not in module_name
        assert "skills_pool" not in module_name
        assert "restart" not in module_name


def test_manifest_bytes_for_build_skill_zip():
    # Guard for the test helper itself: the zips these tests feed the fakes
    # must be valid packages by the production validator.
    validated = real_validator().validate_zip(QZ)
    assert validated.name == "quality-check"


def _manifest_bytes(name: str) -> bytes:
    return (
        f"---\nname: {name}\ndescription: {name} test skill.\n---\n# {name}\n"
    ).encode()


def test_an_unpinned_skill_source_defaults_to_keep_last():
    """Omitted ``on_fetch_failure`` means ``keep_last`` (schema §2): a source
    that has since died still delivers the platform's own copy, and the
    entry materialises from it."""
    materialiser, uploads, activation, reader, objects, content = skill_rig(
        packages={QC_KEY: QZ}
    )
    # First apply with an unpinned entry (no digest declared on identity-style
    # freedom is not allowed for skills — the validator pins declared sources
    # — so pin it and file the receipt, then kill the source).
    entries = [_declared()]
    _run(_apply(materialiser, _ctx(), entries))
    objects.make_unavailable(OSS_BUCKET, "source transport failed")
    qc_id = uploads.rows["quality-check"]["id"]
    reader.assets = (skill_asset(qc_id, "quality-check"),)

    _, _, second = _run(_apply(materialiser, _ctx(), entries))
    assert [e.outcome.value for e in second] == ["unchanged"]
    assert len(uploads.uploads) == 1  # the store's copy answered, no re-upload


def test_the_receipts_link_the_apply_and_the_entry():
    materialiser, uploads, activation, reader, objects, content = skill_rig(
        packages={QC_KEY: QZ}
    )
    _run(_apply(materialiser, _ctx(apply_id="apply-7"), [_declared()]))
    call = content.store_calls[0]
    assert call["apply_id"] == "apply-7"
    assert call["category"] == "skills"
    assert call["entry_identity"] == "quality-check"


def test_a_dry_run_receipt_is_not_installation_evidence():
    """The P0 audit's scenario: the document's pin moved from (S1, D1) to
    (S2, D2); a dry run fetched and filed D2's receipt (dry runs fetch);
    the name is still active **with D1's package installed**. The real apply
    must class the entry for a WRITE — a receipt proves the platform
    fetched the content, never that it was installed, and reporting
    UNCHANGED here would mean D2 silently never lands while the report says
    SUCCEEDED."""
    old_zip = build_skill_zip("quality-check", extra=[("v1.txt", b"old")])
    materialiser, uploads, activation, reader, objects, content = skill_rig(
        packages={QC_KEY: QZ}
    )
    # World: installed and active is D1 (the OLD package).
    uploads.rows["quality-check"] = {"id": 12, "name": "quality-check"}
    uploads.installed["quality-check"] = old_zip
    reader.assets = (skill_asset(12, "quality-check"),)
    # The dry run's fetch of D2 filed its receipt…
    content.store(
        fetched_object(QZ, url=QC_RECEIPT, content_type="application/zip"),
        scope=None,
        source_url=QC_RECEIPT,
    )

    _, plan, written = _run(_apply(materialiser, _ctx(), [_declared()]))
    assert [e.outcome.value for e in written] == ["updated"]
    # What got written is D2's canonical repack (the passthrough road).
    assert (
        uploads.uploads[0]["package"]
        == real_validator().validate_zip(QZ).canonical_zip
    )
    assert activation.skill_activations == []  # already active: replaced only


def test_a_receipt_left_behind_by_an_aborted_write_is_not_evidence():
    """Apply #1's resolve filed this entry's receipt, its write stage then
    aborted (the category's other half failed): apply #2 must still WRITE,
    not read the leftover receipt as 'already installed'."""
    materialiser, uploads, activation, reader, objects, content = skill_rig(
        packages={QC_KEY: QZ}
    )
    # The leftover receipt from the aborted apply…
    content.store(
        fetched_object(QZ, url=QC_RECEIPT, content_type="application/zip"),
        scope=None,
        source_url=QC_RECEIPT,
    )
    # …an active name (installed with the *older* package, because abort #1
    # never reached this entry's write)…
    uploads.rows["quality-check"] = {"id": 12, "name": "quality-check"}
    uploads.installed["quality-check"] = build_skill_zip(
        "quality-check", extra=[("v1.txt", b"pre-abort")]
    )
    reader.assets = (skill_asset(12, "quality-check"),)

    _, plan, written = _run(_apply(materialiser, _ctx(), [_declared()]))
    assert [e.outcome.value for e in written] == ["updated"]


def test_an_unreadable_installed_package_is_treated_as_unknown():
    """Unreadable ≠ equal: a name whose installed package cannot be read back
    (or holds bytes that no longer form a package) must be classed for a
    full write, never guessed into UNCHANGED."""
    materialiser, uploads, activation, reader, objects, content = skill_rig(
        packages={QC_KEY: QZ}
    )
    uploads.rows["quality-check"] = {"id": 12, "name": "quality-check"}
    # Installed bytes that are NOT this package: digest differs on purpose —
    # "unreadable" is modeled by the real service returning None; here the
    # neighbor case (stale content) proves the same verdict.
    uploads.installed["quality-check"] = b"not-a-zip"
    reader.assets = (skill_asset(12, "quality-check"),)

    _, plan, written = _run(_apply(materialiser, _ctx(), [_declared()]))
    assert [e.outcome.value for e in written] == ["updated"]


def test_a_deactivation_conflict_mid_write_reports_partially_written():
    """The skills `partially_written` corner: removals run after uploads, so
    a governed skill that slipped the plan's narrowing (its membership
    landed between plan and write) aborts the category with honest
    partially-written semantics rather than pretending the area is whole.
    The engine's write-raise path classifies it; this drives it through the
    materialiser with W2's conflict shape the fake models."""
    materialiser, uploads, activation, reader, objects, content = skill_rig(
        packages={QC_KEY: QZ},
    )
    # The area holds one direct skill the document does not declare…
    reader.assets = (skill_asset(31, "stale-skill"),)
    dev = _run  # keep names local
    resolved = dev(materialiser.resolve(_ctx(), [_declared()]))
    assert resolved.ok

    plan = dev(materialiser.plan(_ctx(), resolved.intents))
    assert plan.removals == ("stale-skill",)

    # …and its governance lands between plan and write (member_skill_ids
    # narrows plan but write re-asks the area, not the narrowing).
    reader.assets = (skill_asset(31, "stale-skill"),)

    async def _conflicting_deactivate(**kwargs):
        raise RuntimeError(
            "RESOURCE_MANAGED_BY_SKILL_SET"
        )

    activation.deactivate_skill = _conflicting_deactivate
    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="RESOURCE_MANAGED"):
        dev(materialiser.write(_ctx(), plan))
    # The upload for the declared skill DID land — the honest
    # partially-written shape (the engine's write-raise classifier
    # classifies it, this documents the drive).
    assert uploads.uploads


# ── the git road (W7) ───────────────────────────────────────────────────────

_SKILL_MD = (
    "---\nname: demo\ndescription: demo test skill.\n---\n# demo\n"
).encode()

SKILL_GIT_SOURCE = {
    "protocol": "git",
    "url": "https://git.corp/skills.git",
    "ref": "main",
    "subpath": "pkg",
    "auth": None,
}


class _StaticGit:
    """A git client serving one checkout with a fixed tree — or a failure.

    The checkout mirrors ``GitCheckout`` where the git road reaches it:
    ``files(subpath)`` answers the subpath tree's contents (the root holds
    them, not a nested copy), the way the guarded readers do."""

    def __init__(
        self,
        files: list[tuple[str, bytes]] | None = None,
        sha: str = "a" * 40,
        error: Exception | None = None,
    ) -> None:
        self._files = files if files is not None else [("SKILL.md", _SKILL_MD)]
        self.sha = sha
        self.error = error
        self.specs: list = []

    def fetch(self, spec, *, headers=None):
        self.specs.append(spec)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            root=None,
            sha=self.sha,
            url=spec.url,
            ref=spec.ref,
            files=lambda subpath=None, file_limit=None: list(self._files),
            read_file=lambda subpath=None, file_limit=None: b"",
        )


def _git_ctx(git: _StaticGit, *, sources=None, baselines=None):
    """The W7 context: a source session over a scripted git client."""
    session = SourceSession(
        sources=sources or {}, baselines=baselines or {}, git=git
    )
    return _ctx(source_session=session)


def test_a_skills_entry_from_a_git_tree_builds_a_package():
    materialiser, uploads, _, _, _, _ = skill_rig()
    git = _StaticGit()
    ctx = _git_ctx(git, sources={"src": SKILL_GIT_SOURCE})
    result, plan, written = _run(
        _apply(materialiser, ctx, [{"name": "demo", "from": "src"}])
    )
    assert result.ok
    assert [e.outcome.value for e in written] == ["created"]
    # The manual-upload road unchanged: an installed skill IS an uploaded
    # one — so the uploaded package must pass the real validator.
    real_validator().validate_zip(uploads.uploads[0]["package"])
    # The declaration's subpath is what the git road read the tree by.
    assert git.specs[0].subpath == "pkg"


def test_the_git_tree_road_files_the_canonical_zip_with_the_store():
    materialiser, _, _, _, _, content = skill_rig()
    git = _StaticGit()
    ctx = _git_ctx(git, sources={"src": SKILL_GIT_SOURCE})
    result, _, _ = _run(_apply(materialiser, ctx, [{"name": "demo", "from": "src"}]))
    assert result.ok
    # The receipt identity is the canonical git URL; the deliverable bytes
    # (the canonical zip) are what the platform keeps a copy of, so a later
    # keep_last falls back to what this entry actually installs.
    call = content.store_calls[-1]
    assert call["source_url"].startswith("git+https://git.corp/skills.git@")
    assert call["source_url"].endswith(":pkg")
    assert content.receipts[-1].content_type == "application/zip"


def test_a_moved_ref_note_survives_into_the_package():
    materialiser, _, _, _, _, _ = skill_rig()
    git = _StaticGit(sha="a" * 40)
    ctx = _git_ctx(
        git,
        sources={"src": SKILL_GIT_SOURCE},
        baselines={"src": "b" * 40},
    )
    result, _, written = _run(_apply(materialiser, ctx, [{"name": "demo", "from": "src"}]))
    assert result.ok
    # Non-strict moves are applied and reported: both SHAs on the row.
    note = written[0].note
    assert note is not None
    assert "b" * 40 in note
    assert "a" * 40 in note


def test_git_keep_last_serves_the_stored_zip_through_the_zip_road():
    materialiser, uploads, _, _, _, content = skill_rig()
    stored = build_skill_zip("demo")
    target = f"git+https://git.corp/skills.git@{'b' * 40}:pkg"
    content.store(
        fetched_object(stored, url=target, content_type="application/zip"),
        scope=None,
        source_url=target,
    )
    git = _StaticGit(error=FetchFailedError("the git fetch failed"))
    ctx = _git_ctx(
        git,
        sources={"src": SKILL_GIT_SOURCE},
        baselines={"src": "b" * 40},
    )
    result, _, written = _run(
        _apply(
            materialiser,
            ctx,
            [{"name": "demo", "from": "src", "on_fetch_failure": "keep_last"}],
        )
    )
    assert result.ok
    assert [e.outcome.value for e in written] == ["created"]
    real_validator().validate_zip(uploads.uploads[0]["package"])

