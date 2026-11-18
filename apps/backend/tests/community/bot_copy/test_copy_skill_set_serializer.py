"""S1 unit tests for ``copy._serialize_skill_sets`` (Spec §6 snapshot-derived).

The serializer is a pure function: it takes the source bot's
``SkillSetApplySnapshot`` and returns the ``manifest.skill_sets`` list to write
into the dst manifest document, or ``None`` to omit the segment (the dst reads
as a brand-new bot: default enabled, no ordinary sets — Spec §0). The segment
is derived solely from the snapshot — the apply-engine source of truth that
covers manifest-managed and legacy Genome bots alike.

Tests round-trip the output through ``validate_document`` — the same validator
``PUT`` and the manifest-creation preflight call — which internally invokes
``validate_capabilities`` (``skill_sets.py``). The validator is the schema
gate; the full ``ManifestCompiler`` path is exercised end-to-end by the S3
endpoint tests (Spec §4 separation), which feed a complete document with
``sources``/``resources`` through the apply engine.

Scenarios map to Spec §4 S1:
* ④  snapshot with a default set + one ordinary set → derived segment
  re-validates.
* ⑤  empty snapshot → ``None`` (omit the segment).
* local-skill members (``local://``) are serialized as
  ``{name, origin: local, from: from_bot}`` — wave-1 opened the
  ``(SKILLS, BOT)`` ``support_matrix`` cell so the declaration validates, and
  wave-2's ``BotSyncedLocalSkill`` resolve reverse-looks-up the source bot's
  ``ac_skill`` row and registers the dst row without uploading (the physical
  packages ride the copy's ``resources: workspace/`` file-sync entry).
* ``None`` snapshot → omit segment.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.adapters.http.openapi_v1.bots.copy import (
    _serialize_skill_sets,
)
from agentclaw.community.core.apply_engine.capabilities import resolve_capabilities
from agentclaw.community.core.bot_config_manifest.schema.validator import (
    validate_document,
)
from agentclaw.community.core.skill_center.skill_set_apply import (
    BotCapabilityScope,
    SkillSetApplySnapshot,
    SkillSetSnapshot,
)


_CAPS = resolve_capabilities(
    active_engine="openclaw", bot_type="personal", is_teclaw=lambda _: False
)


def _assert_skill_sets_validate(
    segment: list[dict[str, Any]] | None,
    *,
    sources: dict[str, Any] | None = None,
) -> None:
    """Re-validate the segment through ``validate_document``.

    A manifest document with ``schema_version: 1`` + the segment under
    ``manifest.skill_sets`` is the minimal document the schema gate accepts;
    ``validate_capabilities`` runs inside it. When members reference a named
    source via ``from:``, the caller passes the matching ``sources`` block so
    that ``resolve_source`` can resolve it — otherwise the schema would report
    an undeclared-source violation that the segment's own vocabulary did not
    cause.
    """
    import yaml as _yaml

    manifest: dict[str, Any] = {"resources": []}
    if segment is not None:
        manifest["skill_sets"] = segment
    document_obj: dict[str, Any] = {"schema_version": 1, "manifest": manifest}
    if sources:
        document_obj["sources"] = sources
    document = _yaml.safe_dump(
        document_obj,
        sort_keys=False,
        allow_unicode=True,
    )
    # ``validate_document`` raises on any violation; absence of raise == pass.
    validate_document(document, _CAPS)


def _default_enabled(segment: list[dict[str, Any]] | None) -> bool:
    """Mirror ``capabilities_resolve.py:27-29`` — default switch by row presence.

    Returning True when the segment is ``None`` matches the resolver's
    ``is None`` branch: omitting the segment enables default, the brand-new
    baseline.
    """
    if segment is None:
        return True
    return any(
        isinstance(row, dict) and row.get("name") == "default" for row in segment
    )


# ---------------------------------------------------------------------------
# Snapshot-derived segment (the sole path — manifest-managed and legacy bots
# both surface their state through ``SkillSetApplySnapshot``).
# ---------------------------------------------------------------------------


def _snapshot(
    *, sets: tuple[SkillSetSnapshot, ...], skills: tuple[dict[str, Any], ...] = ()
) -> SkillSetApplySnapshot:
    return SkillSetApplySnapshot(
        scope=BotCapabilityScope(
            bot_id="src_1",
            owner_id="u1",
            engine_type="openclaw",
            default_engine_types=("openclaw",),
            tenant="teamclaw",
            env="dev",
        ),
        sets=sets,
        skill_exclusions=frozenset(),
        mcp_exclusions=frozenset(),
        installed_skill_ids=frozenset(),
        installed_mcp_codes=frozenset(),
        platform_default_codes=frozenset(),
        skills=skills,
    )


def test_scenario_4_snapshot_with_default_plus_ordinary_set_derives_segment():
    # The ordinary Set's members cover center and repo origins: those are the
    # branches that validate standalone against the schema (no `from:`
    # declaration needed). The local-skill branch carries the extra S1/S2 wiring
    # that needs a declared file-sync source; the S2 integration test exercises
    # that path, this S1 test stays within the snapshot vocabulary.
    ordinary_skills = (
        # A repo-path skill.
        {
            "id": 101,
            "name": "repo-tool",
            "git_path": "tools/repo-tool",
            "skill_uuid": None,
        },
        # A center skill referenced by uuid.
        {
            "id": 102,
            "name": "center-tool",
            "git_path": "center://center-fixture",
            "skill_uuid": "33333333-3333-3333-3333-333333333333",
        },
    )
    default_set = SkillSetSnapshot(
        id=1,
        name="default",
        description=None,
        is_default=True,
        is_active=True,
        skill_ids=(1,),
        mcp_codes=("platform-mcp",),
        skill_references=(),
    )
    ordinary_set = SkillSetSnapshot(
        id=2,
        name="my-tools",
        description="team shared",
        is_default=False,
        is_active=True,
        skill_ids=(101, 102),
        mcp_codes=("gh",),
        skill_references=(),
    )
    snapshot = _snapshot(
        sets=(default_set, ordinary_set), skills=ordinary_skills
    )

    segment = _serialize_skill_sets(snapshot=snapshot)
    assert segment is not None
    assert _default_enabled(segment) is True
    ordinary = [row for row in segment if row.get("name") == "my-tools"]
    assert len(ordinary) == 1
    assert ordinary[0]["is_active"] is True
    assert ordinary[0]["description"] == "team shared"
    assert {skill["origin"] for skill in ordinary[0]["skills"]} == {
        "repo",
        "center",
    }
    # Center skill carries its uuid; repo skill carries its path; MCP members
    # are emitted as ``{server_code}`` rows (Spec §0 / §1).
    center_skill = next(s for s in ordinary[0]["skills"] if s["origin"] == "center")
    assert center_skill["skill_uuid"] == "33333333-3333-3333-3333-333333333333"
    repo_skill = next(s for s in ordinary[0]["skills"] if s["origin"] == "repo")
    assert repo_skill["repo_path"] == "tools/repo-tool"
    assert ordinary[0]["mcp"] == [{"server_code": "gh"}]
    _assert_skill_sets_validate(segment)


def test_scenario_5_snapshot_without_sets_returns_none():
    snapshot = _snapshot(sets=(), skills=())
    segment = _serialize_skill_sets(snapshot=snapshot)
    assert segment is None
    assert _default_enabled(segment) is True


def test_branch_b_local_skill_member_serialized_as_from_from_bot():
    # Wave-1 opened the ``(SKILLS, BOT)`` ``support_matrix`` cell so a local
    # member can name the file-sync ``protocol: bot`` source, and wave-2's
    # ``BotSyncedLocalSkill`` resolve reverse-looks-up the source bot's
    # ``ac_skill`` row + registers the dst row without uploading. So a
    # snapshot carrying a ``local://`` member now emits
    # ``{name, origin: local, from: from_bot}`` referencing the top-level
    # ``sources.from_bot`` named source ``_build_manifest_document`` emits —
    # no longer dropped. The physical package rides the copy's
    # ``resources: workspace/`` file-sync entry; repo/center members of the
    # same Set still come through unchanged.
    members = (
        {
            "id": 101,
            "name": "repo-tool",
            "git_path": "tools/repo-tool",
            "skill_uuid": None,
        },
        {
            "id": 102,
            "name": "local-only",
            "git_path": "local://some/local/pkg",
            "skill_uuid": None,
        },
    )
    ordinary = SkillSetSnapshot(
        id=2,
        name="mixed",
        description=None,
        is_default=False,
        is_active=True,
        skill_ids=(101, 102),
        mcp_codes=(),
        skill_references=(),
    )
    snapshot = _snapshot(sets=(ordinary,), skills=members)

    segment = _serialize_skill_sets(snapshot=snapshot)
    assert segment is not None
    ordinary_rows = [row for row in segment if row.get("name") == "mixed"]
    assert len(ordinary_rows) == 1
    # Both the repo and the local member survive; the local one carries
    # ``from: from_bot`` referencing the file-sync source.
    skills_out = ordinary_rows[0]["skills"]
    origins = {skill["origin"] for skill in skills_out}
    assert origins == {"repo", "local"}
    local_skill = next(s for s in skills_out if s["origin"] == "local")
    assert local_skill["name"] == "local-only"
    assert local_skill["from"] == "from_bot"
    repo_skill = next(s for s in skills_out if s["origin"] == "repo")
    assert repo_skill["name"] == "repo-tool"
    assert repo_skill["repo_path"] == "tools/repo-tool"
    # The derived segment re-validates against the schema (the
    # ``sources.from_bot`` declaration must be provided for the ``from:``
    # reference to resolve).
    _assert_skill_sets_validate(
        segment,
        sources={
            "from_bot": {
                "protocol": "bot",
                "bot_id": "src_1",
                "owner_id": "u1",
            }
        },
    )


@pytest.mark.parametrize("snapshot", [None])
def test_branch_b_snapshot_none_returns_none(snapshot):
    """A ``None`` snapshot (legacy path with nothing to read) → omit segment."""
    segment = _serialize_skill_sets(snapshot=snapshot)
    assert segment is None


def test_build_manifest_document_omits_skill_sets_when_none():
    """``_build_manifest_document`` does not emit the segment when ``None``."""
    from agentclaw.community.adapters.http.openapi_v1.bots.copy import (
        _build_manifest_document,
    )

    document = _build_manifest_document(
        script_body="echo hi",
        source_bot_id="src_1",
        source_owner_id="u1",
        skill_sets=None,
    )
    assert "skill_sets" not in document


def test_build_manifest_document_emits_skill_sets_when_provided():
    from agentclaw.community.adapters.http.openapi_v1.bots.copy import (
        _build_manifest_document,
    )

    segment: list[dict[str, Any]] = [{"name": "default"}]
    document = _build_manifest_document(
        script_body="echo hi",
        source_bot_id="src_1",
        source_owner_id="u1",
        skill_sets=segment,
    )
    assert "skill_sets" in document
    # The builder's output must pass the same validator.
    validate_document(document, _CAPS)


def test_default_plus_ordinary_set_with_local_member_end_to_end_round_trip():
    """Spec §1.4 / 波3 — source snapshot carries a default Set + an ordinary
    Set with a repo/center/local member → ``_serialize_skill_sets`` emits the
    ordinary Set with the local member as ``{name, origin: local,
    from: from_bot}``; ``_build_manifest_document`` embeds the segment with the
    matching top-level ``sources.from_bot`` declaration; ``validate_document``
    re-accepts the full document (wave-1 opened the ``(SKILLS, BOT)`` cell, so
    the local member's ``from: from_bot`` reference resolves against
    ``sources.from_bot``)."""
    from agentclaw.community.adapters.http.openapi_v1.bots.copy import (
        _build_manifest_document,
    )

    ordinary_skills = (
        {
            "id": 201,
            "name": "repo-tool",
            "git_path": "tools/repo-tool",
            "skill_uuid": None,
        },
        {
            "id": 202,
            "name": "center-tool",
            "git_path": "center://center-fixture",
            "skill_uuid": "44444444-4444-4444-4444-444444444444",
        },
        {
            "id": 203,
            "name": "local-tool",
            "git_path": "local://some/local/pkg",
            "skill_uuid": None,
        },
    )
    default_set = SkillSetSnapshot(
        id=1,
        name="default",
        description=None,
        is_default=True,
        is_active=True,
        skill_ids=(),
        mcp_codes=(),
        skill_references=(),
    )
    ordinary_set = SkillSetSnapshot(
        id=2,
        name="full-mixed",
        description=None,
        is_default=False,
        is_active=True,
        skill_ids=(201, 202, 203),
        mcp_codes=("gh",),
        skill_references=(),
    )
    snapshot = _snapshot(
        sets=(default_set, ordinary_set), skills=ordinary_skills
    )

    segment = _serialize_skill_sets(snapshot=snapshot)
    assert segment is not None
    assert _default_enabled(segment) is True
    ordinary = next(row for row in segment if row.get("name") == "full-mixed")
    by_origin = {skill["origin"]: skill for skill in ordinary["skills"]}
    assert set(by_origin) == {"repo", "center", "local"}
    assert by_origin["local"]["name"] == "local-tool"
    assert by_origin["local"]["from"] == "from_bot"
    assert by_origin["repo"]["repo_path"] == "tools/repo-tool"
    assert by_origin["center"]["skill_uuid"] == "44444444-4444-4444-4444-444444444444"
    assert ordinary["mcp"] == [{"server_code": "gh"}]

    # Build the full manifest document and re-validate — the local member's
    # ``from: from_bot`` resolves against the document's top-level
    # ``sources.from_bot`` (declared by ``_build_manifest_document`` for the
    # file-sync entry); wave-1 opened the support_matrix cell so
    # ``validate_document`` accepts the declaration.
    document = _build_manifest_document(
        script_body="echo hi",
        source_bot_id="src_1",
        source_owner_id="u1",
        skill_sets=segment,
    )
    validate_document(document, _CAPS)
