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
* local-skill members (``local://``) are skipped — the manifest schema refuses
  a bare local member (Spec §1), so the physical packages ride the copy's
  ``resources: workspace/`` file-sync entry and the dst operator re-links via
  Add-Skill.
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


def test_branch_b_local_skill_member_is_skipped_schema_cannot_express_it():
    # The manifest schema refuses a local skill member whose ``from`` names a
    # ``protocol: bot`` source (``support_matrix.py:170``
    # ``_BOT_SOURCE_IS_RESOURCES_ONLY``), and a bare ``origin: local`` entry
    # fails the SKILLS-category source gate
    # (``validate_skill_entry``→``resolve_source``). So a snapshot that carries
    # ``local://`` members must skip them in the derived segment — the physical
    # packages still ride the copy's ``resources: workspace/`` file-sync entry,
    # and the dst operator can re-link local skills via Add-Skill. Repo/center
    # members of the same Set must still come through.
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
    # The repo member survived; the local member was dropped.
    origins = {skill["origin"] for skill in ordinary_rows[0]["skills"]}
    assert origins == {"repo"}
    names = {skill["name"] for skill in ordinary_rows[0]["skills"]}
    assert "local-only" not in names
    # The derived segment re-validates against the schema (no local-only
    # member that the SKILLS source gate would refuse).
    _assert_skill_sets_validate(segment)


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
