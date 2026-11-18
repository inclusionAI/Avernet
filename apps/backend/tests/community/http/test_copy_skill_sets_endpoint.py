"""S3 endpoint-level integration tests for copy → skill_sets sync (Spec §4).

 submit_copy is exercised through its public handler entrypoint signature (the
 same wiring the FastAPI route uses), with every DI dependency mocked. The
 serialized manifest document is captured from the ``manifest_seam.persist``
 call and re-validated through ``validate_document`` — the same schema gate
 PUT and the manifest-creation preflight call. These tests cover acceptance
 criteria AC-1 / AC-2 / AC-2b / AC-3 (endpoint level) and the Spec §4 S3
 five-scenario matrix:

 * ① source snapshot carries a full ``skill_sets`` segment (default + ordinary
   Set + repo/center skill + mcp member) → segment derived from the snapshot
   → dst manifest carries it, validate_document re-accepts it.
 * ② source snapshot has no default set → dst segment carries no ``default``
   row → ``default_enabled`` reads False.
 * ③ source snapshot is empty → ``_serialize_skill_sets`` returns None → dst
   manifest document omits the segment (AC-2b / Spec §2 brand-new baseline).
 * ④ (AC-3) source engine is not openclaw → submit_copy refuses before any
   manifest side effect (HTTPException 409, no persist).
 * ⑤ source snapshot carries a default Set and an ordinary Set → dst segment
   derived from the snapshot, validate_document re-accepts it.

Local-package members (``local://``) are skipped at the declaration layer
(Spec §1): the manifest schema refuses a bare local SkillSet member. Their
physical packages still arrive via the copy's ``resources: workspace/``
file-sync entry, and the operator can re-link them through Add-Skill. So no
endpoint scenario asserts a local member riding the declaration.

The manifest's ``skill_sets`` segment under test lives at
``manifest.seam.persist.call_args.kwargs['document']`` (the YAML body
submit_copy hands to the manifest-creation seam); assertions are made by
parsing that body and by feeding it back to ``validate_document``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi import HTTPException

from agentclaw.community.adapters.http.openapi_v1.bots.copy import (
    BotCopyRequest,
    submit_copy,
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
from tests.community.http.test_copy_route import (
    _OWNER,
    _SOURCE_BOT_ID,
    _empty_snapshot,
    _mocks,
    _source_bot,
)


_CAPS = resolve_capabilities(
    active_engine="openclaw", bot_type="personal", is_teclaw=lambda _: False
)


def _persisted_document(manifest_seam: MagicMock) -> dict[str, Any]:
    """The YAML body submit_copy handed to ``manifest_seam.persist``."""
    document = manifest_seam.persist.call_args.kwargs["document"]
    return yaml.safe_load(document)


def _set_snapshot(mocks: Any, snapshot: SkillSetApplySnapshot) -> None:  # type: ignore[name-defined]
    """Wire the skill_set_service to yield ``snapshot`` for the source."""
    mocks[-1].read_apply_snapshot.return_value = snapshot


def _call_submit_copy(mocks: Any, source: Any = None) -> Any:  # type: ignore[name-defined]
    (bot_repo, bot_copy_repo, bot_script_service, manifest_seam, passport,
     space_context, render_screen_service, bot_service,
     skill_set_service) = mocks
    # Default to the openclaw source; callers that need a different engine
    # (scenario ④'s non-openclaw refusal) pass their own source and skip the
    # reset so the precondition they set on ``bot_repo`` survives.
    bot_repo.get_by_id.return_value = source if source is not None else _source_bot()
    passport.apply_agent_passport.return_value = {"token": "tok_1"}
    return submit_copy(
        body=BotCopyRequest(source_bot_id=_SOURCE_BOT_ID, bot_name="copied"),
        owner_id=_OWNER,
        owner_name="owner",
        bot_repo=bot_repo,
        bot_copy_repo=bot_copy_repo,
        bot_script_service=bot_script_service,
        manifest_seam=manifest_seam,
        passport_plugin=passport,
        space_context=space_context,
        render_screen_service=render_screen_service,
        bot_service=bot_service,
        skill_set_service=skill_set_service,
    )


def _snapshot(
    *,
    sets: tuple[SkillSetSnapshot, ...],
    skills: tuple[dict[str, Any], ...] = (),
) -> SkillSetApplySnapshot:
    """Build a snapshot for the source bot with the given sets/members."""
    return SkillSetApplySnapshot(
        scope=BotCapabilityScope(
            bot_id=_SOURCE_BOT_ID,
            owner_id=_OWNER,
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


# ---------------------------------------------------------------------------
# Scenario ① — snapshot with a full skill_sets segment → derived on dst
# ---------------------------------------------------------------------------


def test_scenario_1_source_manifest_full_segment_passthrough_to_dst():
    """AC-1 endpoint coverage — source N ordinary Set + default enabled →
    dst manifest carries the full segment derived from the snapshot,
    validate_document re-accepts it. Local-package members cannot be
    expressed in the schema (Spec §1) and are skipped; center/repo members
    + mcp still derive."""
    ordinary_skills = (
        {
            "id": 101,
            "name": "center-tool",
            "git_path": "center://center-fixture",
            "skill_uuid": "11111111-1111-1111-1111-111111111111",
        },
        {
            "id": 102,
            "name": "repo-tool",
            "git_path": "tools/repo-tool",
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
        name="shared-tools",
        description="team shared tools",
        is_default=False,
        is_active=True,
        skill_ids=(101, 102),
        mcp_codes=("gh",),
        skill_references=(),
    )
    snapshot = _snapshot(
        sets=(default_set, ordinary_set), skills=ordinary_skills
    )

    mocks = _mocks(script_body="echo hi")
    _set_snapshot(mocks, snapshot)
    resp = _call_submit_copy(mocks)
    assert resp.type == "success"
    manifest_seam = mocks[3]
    doc = _persisted_document(manifest_seam)
    segment = doc["manifest"]["skill_sets"]
    # default switch is on (row present) and the ordinary Set came through with
    # every member type the schema accepts.
    assert any(row.get("name") == "default" for row in segment)
    ordinary = next(row for row in segment if row.get("name") == "shared-tools")
    assert ordinary["description"] == "team shared tools"
    origins = {
        skill.get("origin")
        for skill in ordinary["skills"]
        if "origin" in skill
    }
    assert origins == {"center", "repo"}
    assert ordinary["mcp"] == [{"server_code": "gh"}]
    # Re-validate the full persisted document against the validator Preflight
    # uses; an unrepresentable segment would be a 4xx on submit, not here.
    validate_document(manifest_seam.persist.call_args.kwargs["document"], _CAPS)


# ---------------------------------------------------------------------------
# Scenario ② — source segment without a default row → default disabled
# ---------------------------------------------------------------------------


def test_scenario_2_source_segment_without_default_row_disables_default():
    """AC-2 — source closes default → dst segment carries no default row and
    ``default_enabled`` reads False. The snapshot carries only the ordinary
    Set; the serializer derives no default row."""
    ordinary_skills = (
        {
            "id": 201,
            "name": "center-tool",
            "git_path": "center://center-fixture",
            "skill_uuid": "22222222-2222-2222-2222-222222222222",
        },
    )
    ordinary_set = SkillSetSnapshot(
        id=2,
        name="no-default-tools",
        description=None,
        is_default=False,
        is_active=True,
        skill_ids=(201,),
        mcp_codes=(),
        skill_references=(),
    )
    snapshot = _snapshot(sets=(ordinary_set,), skills=ordinary_skills)

    mocks = _mocks(script_body="echo hi")
    _set_snapshot(mocks, snapshot)
    resp = _call_submit_copy(mocks)
    assert resp.type == "success"
    doc = _persisted_document(mocks[3])
    segment = doc["manifest"]["skill_sets"]
    assert not any(row.get("name") == "default" for row in segment)
    # The ordinary Set still rides through.
    assert [row["name"] for row in segment] == ["no-default-tools"]
    validate_document(mocks[3].persist.call_args.kwargs["document"], _CAPS)


# ---------------------------------------------------------------------------
# Scenario ③ — source has no skill_sets declaration (brand-new) → omitted
# ---------------------------------------------------------------------------


def test_scenario_3_source_without_skill_sets_segment_omits_it_on_dst():
    """AC-2b — fresh source baseline (empty snapshot) → dst manifest omits the
    segment entirely; that is the default-enabled brand-new baseline
    (Spec §0/§2)."""
    mocks = _mocks(script_body="echo hi")
    # The default _mocks() already wires an empty snapshot, but pin it
    # explicitly so the scenario's intent is self-contained.
    _set_snapshot(mocks, _empty_snapshot())
    resp = _call_submit_copy(mocks)
    assert resp.type == "success"
    doc = _persisted_document(mocks[3])
    assert "skill_sets" not in doc["manifest"]
    validate_document(mocks[3].persist.call_args.kwargs["document"], _CAPS)


def test_scenario_3b_brand_new_source_no_manifest_record_empty_snapshot_omits():
    """AC-2b (legacy side) — empty snapshot → the serializer returns None and
    the dst manifest omits the segment."""
    mocks = _mocks(script_body="echo hi")
    _set_snapshot(mocks, _empty_snapshot())
    resp = _call_submit_copy(mocks)
    assert resp.type == "success"
    doc = _persisted_document(mocks[3])
    assert "skill_sets" not in doc["manifest"]
    validate_document(mocks[3].persist.call_args.kwargs["document"], _CAPS)


# ---------------------------------------------------------------------------
# Scenario ④ — non-openclaw source → AC-3 4xx before any side effect
# ---------------------------------------------------------------------------


def test_scenario_4_non_openclaw_source_refused_before_persist():
    """AC-3 — non-openclaw source engine → HTTPException 409, no manifest
    persist, no operation ledger row, no create_job hand-off."""
    mocks = _mocks()
    bot_repo = mocks[0]
    # Source is teclaw-equivalent: a non-openclaw engine.
    bot_repo.get_by_id.return_value = _source_bot(active_engine="teclaw")
    with pytest.raises(HTTPException) as exc:
        _call_submit_copy(mocks, source=_source_bot(active_engine="teclaw"))
    assert exc.value.status_code == 409
    manifest_seam = mocks[3]
    bot_copy_repo = mocks[1]
    assert manifest_seam.persist.call_count == 0
    assert manifest_seam.start_job.call_count == 0
    assert bot_copy_repo.insert.call_count == 0


# ---------------------------------------------------------------------------
# Scenario ⑤ — snapshot-derived segment (default + ordinary Set)
# ---------------------------------------------------------------------------


def _snapshot_with_default_and_ordinary() -> SkillSetApplySnapshot:
    """A snapshot a legacy Genome bot would surface: one default set + one
    ordinary Set with center and repo members (local members skipped per the
    S1-fix; the spec's §1 local representation is refused by the schema, so
    the serializer drops them and the physical packages ride file-sync)."""
    ordinary_skills = (
        {
            "id": 101,
            "name": "repo-tool",
            "git_path": "tools/repo-tool",
            "skill_uuid": None,
        },
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
        mcp_codes=(),
        skill_references=(),
    )
    ordinary = SkillSetSnapshot(
        id=2,
        name="my-tools",
        description="derived",
        is_default=False,
        is_active=True,
        skill_ids=(101, 102),
        mcp_codes=("gh",),
        skill_references=(),
    )
    return _snapshot(sets=(default_set, ordinary), skills=ordinary_skills)


def test_scenario_5_branch_b_snapshot_derives_skill_sets_on_dst():
    """Snapshot-derived (Spec §6) — ``_serialize_skill_sets`` derives a segment
    from the snapshot; default row present + ordinary Set with repo/center
    members re-validates against ``validate_document``."""
    mocks = _mocks(script_body="echo hi")
    _set_snapshot(mocks, _snapshot_with_default_and_ordinary())
    resp = _call_submit_copy(mocks)
    assert resp.type == "success"
    doc = _persisted_document(mocks[3])
    segment = doc["manifest"]["skill_sets"]
    # default row derived from the snapshot's default set
    assert any(row.get("name") == "default" for row in segment)
    ordinary = next(row for row in segment if row.get("name") == "my-tools")
    assert ordinary["is_active"] is True
    assert ordinary["description"] == "derived"
    origins = {skill["origin"] for skill in ordinary["skills"]}
    assert origins == {"repo", "center"}
    assert ordinary["mcp"] == [{"server_code": "gh"}]
    validate_document(mocks[3].persist.call_args.kwargs["document"], _CAPS)
