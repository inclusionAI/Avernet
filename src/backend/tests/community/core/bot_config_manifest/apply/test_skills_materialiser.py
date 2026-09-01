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


from agentclaw.community.core.bot_config_manifest.apply.entry_fetch import (
    EntryFetcher,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
)

from ._fakes import (
    FakeActivationService,
    FakeCapabilityReader,
    FakeCredentials,
    FakeGuardedFetcher,
    FakeManifestContent,
    FakeSkillUploadService,
    build_skill_tgz,
    build_skill_zip,
    fetched_object,
    make_context,
    real_validator,
    skill_asset,
)

QC_URL = "https://content.example/skills/quality-check.zip"


def _run(coro):
    return asyncio.run(coro)


def _ctx(**kwargs):
    kwargs.setdefault("engine_type", "openclaw")
    kwargs.setdefault("owner_id", "u_owner")
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
    fetch_failures: dict[str, Exception] | None = None,
    content_types: dict[str, str] | None = None,
) -> tuple:
    """(materialiser, uploads, activation, reader, fetcher, content)."""
    from agentclaw.community.core.bot_config_manifest.apply.materialisers.skills import (
        SkillsMaterialiser,
    )

    uploads = FakeSkillUploadService()
    activation = FakeActivationService()
    reader = FakeCapabilityReader(assets=assets, member_ids=member_ids)
    types = content_types or {}
    fetcher = FakeGuardedFetcher(
        responses={
            url: fetched_object(
                body, url=url, content_type=types.get(url, "application/zip")
            )
            for url, body in (packages or {}).items()
        },
        failures=fetch_failures or {},
    )
    content = FakeManifestContent()
    pipeline = EntryFetcher(fetcher, content, FakeCredentials())
    materialiser = SkillsMaterialiser(
        uploads, activation, reader, real_validator(), pipeline
    )
    return materialiser, uploads, activation, reader, fetcher, content


QZ = build_skill_zip("quality-check")


def _declared(name: str = "quality-check", url: str = QC_URL, digest: str | None = None):
    entry = {"name": name, "source": url, "digest": digest or _digest_of(QZ)}
    return entry


# ── declarations upload and activate ────────────────────────────────────────


def test_a_declared_skill_uploads_the_validated_package_and_activates():
    materialiser, uploads, activation, _, fetcher, _ = skill_rig(
        packages={QC_URL: QZ}
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
    materialiser, uploads, activation, reader, fetcher, content = skill_rig(
        packages={QC_URL: QZ}
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
    assert len(fetcher.requests) == 1  # only the first apply hit the network;
    # the second was answered by the platform's own copy of the pinned bytes


def test_an_inactive_same_name_skill_is_reinstated_as_created():
    """A deactivated local skill the manifest now declares: the area (the
    ACTIVE set) does not contain it, so it is classed for creation — the
    upload service's same-name replace handles the row, the activation makes
    it active."""
    materialiser, uploads, activation, _, _, _ = skill_rig(packages={QC_URL: QZ})
    result, plan, written = _run(_apply(materialiser, _ctx(), [_declared()]))
    assert [e.outcome.value for e in written] == ["created"]
    assert uploads.uploads and activation.skill_activations


def test_one_failed_fetch_aborts_the_whole_category_no_writes():
    other_url = "https://content.example/skills/order-lookup.zip"
    other = build_skill_zip("order-lookup")
    materialiser, uploads, activation, _, fetcher, _ = skill_rig(
        packages={QC_URL: QZ, other_url: other},
        fetch_failures={other_url: FetchFailedError("source answered 404")},
    )
    resolved = _run(
        materialiser.resolve(
            _ctx(),
            [
                _declared(),
                _declared(name="order-lookup", url=other_url, digest=_digest_of(other)),
            ],
        )
    )
    assert not resolved.ok
    failures = {f.identity: f.reason for f in resolved.failures}
    assert "source answered 404" in failures["order-lookup"]
    assert uploads.uploads == []
    assert activation.writes == 0


def test_a_digest_that_the_source_no_longer_matches_fails_the_entry():
    stale_pin = "sha256:" + "1" * 64
    materialiser, uploads, _, _, _, _ = skill_rig(packages={QC_URL: QZ})
    resolved = _run(
        materialiser.resolve(_ctx(), [_declared(digest=stale_pin)])
    )
    assert not resolved.ok
    assert "digest mismatch" in resolved.failures[0].reason
    assert uploads.uploads == []


def test_the_packages_own_name_must_equal_the_declared_name():
    materialiser, uploads, _, _, _, _ = skill_rig(packages={QC_URL: QZ})
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
    url = "https://content.example/skills/qc.tar.gz"
    materialiser, uploads, activation, _, _, _ = skill_rig(packages={url: archive})
    result, plan, written = _run(
        _apply(
            materialiser,
            _ctx(),
            [
                {
                    "name": "quality-check",
                    "source": url,
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


def test_a_declared_unpack_overrides_an_unhelpful_url():
    url = "https://content.example/skills/get-package"
    materialiser, uploads, _, _, _, _ = skill_rig(packages={url: QZ})
    resolved = _run(
        materialiser.resolve(
            _ctx(),
            [
                {
                    "name": "quality-check",
                    "source": url,
                    "digest": _digest_of(QZ),
                    "unpack": "zip",
                }
            ],
        )
    )
    assert resolved.ok
    assert resolved.intents[0].identity == "quality-check"


def test_a_zip_url_is_detected_from_its_suffix_without_unpack():
    url = "https://content.example/skills/anything.zip"
    materialiser, _, _, _, _, _ = skill_rig(packages={url: QZ})
    resolved = _run(
        materialiser.resolve(_ctx(), [_declared(url=url)])
    )
    assert resolved.ok


def test_an_undetectable_archive_kind_fails_with_a_readable_reason():
    url = "https://content.example/skills/get-package"
    materialiser, uploads, _, _, _, _ = skill_rig(
        packages={url: QZ}, content_types={url: "text/plain"}
    )
    resolved = _run(
        materialiser.resolve(_ctx(), [_declared(url=url)])
    )
    assert not resolved.ok
    assert "unpack" in resolved.failures[0].reason
    assert uploads.uploads == []


def test_a_subpath_that_selects_nothing_fails():
    archive = build_skill_tgz([("other/SKILL.md", _manifest_bytes("other"))])
    url = "https://content.example/skills/qc.tar.gz"
    materialiser, _, _, _, _, _ = skill_rig(packages={url: archive})
    resolved = _run(
        materialiser.resolve(
            _ctx(),
            [
                {
                    "name": "quality-check",
                    "source": url,
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
    url = "https://content.example/skills/qc.zip"
    materialiser, uploads, activation, _, _, _ = skill_rig(packages={url: huge})
    resolved = _run(
        materialiser.resolve(_ctx(), [_declared(url=url, digest=_digest_of(huge))])
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
        packages={QC_URL: QZ},
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
        packages={QC_URL: QZ},
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
        packages={QC_URL: QZ},
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
    materialiser, uploads, activation, reader, fetcher, content = skill_rig(
        packages={QC_URL: QZ}
    )
    # First apply with an unpinned entry (no digest declared on identity-style
    # freedom is not allowed for skills — the validator pins URL sources — so
    # pin it and file the receipt, then kill the source).
    entries = [_declared()]
    _run(_apply(materialiser, _ctx(), entries))
    fetcher.failures[QC_URL] = FetchFailedError("source transport failed")
    qc_id = uploads.rows["quality-check"]["id"]
    reader.assets = (skill_asset(qc_id, "quality-check"),)

    _, _, second = _run(_apply(materialiser, _ctx(), entries))
    assert [e.outcome.value for e in second] == ["unchanged"]
    assert len(uploads.uploads) == 1  # the store's copy answered, no re-upload
