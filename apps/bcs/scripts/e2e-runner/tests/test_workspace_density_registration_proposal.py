"""Registration locks for the row #7 workspace density closure proposal.

The proposal module is describe-only: these tests pin the frozen calibers,
the exact notes-only executor-face integration, the inventory fail-close
closure, and the credential hygiene of every published text surface.
"""
from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.inventory import (
    EXECUTOR_MIGRATION_NOTES,
    _EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS,
    load_executor_migration,
)
from bcs_e2e.workspace_density import frozen as ws_frozen
from bcs_e2e.workspace_density.registration_proposal import (
    INTEGRATION_PLEDGE,
    MD_ROW,
    NOTE,
    NOTE_TOKEN,
    RUNNER_COMMIT,
    SEAM_COMMIT,
    SPLIT_COMMIT,
    WORKSPACE_FIELD_COMMAND,
    proposal_facts,
)

CLI_TEXT = (ROOT / "bcs_e2e" / "cli.py").read_text(encoding="utf-8")
COMMAND_REGISTRATION_TEXT = (
    ROOT / "bcs_e2e" / "case_command_registration.py").read_text(encoding="utf-8")
EXECUTOR_FACE = json.loads(
    (ROOT / "executor-migration.json").read_text(encoding="utf-8"))
FACE_BY_ID = {row["id"]: row["executor_migration"]
              for row in EXECUTOR_FACE["rows"]}


class WorkspaceDensityRegistrationProposalTests(unittest.TestCase):
    def test_executor_row_face_locked_notes_only(self):
        face = FACE_BY_ID[ws_frozen.PARENT]
        self.assertEqual(face["mode"], "DUAL")
        self.assertEqual(face["preprod_verdict"], "PASS")
        self.assertEqual(face["gates"], [])
        self.assertEqual(face["notes"], [NOTE])

    def test_note_token_and_commit_anchors(self):
        self.assertEqual(
            NOTE_TOKEN, "WORKSPACE_DENSITY_59_PARSED_63_ATOMS_20261101")
        self.assertEqual(RUNNER_COMMIT, "205715228")
        self.assertEqual(SEAM_COMMIT, "6114dbfbc")
        self.assertEqual(SPLIT_COMMIT, "63c2a3a52")
        self.assertEqual(WORKSPACE_FIELD_COMMAND, "dual-workspace-field-run")

    def test_mathematical_closure_both_calibers(self):
        self.assertEqual(ws_frozen.PARSED_COUNT, 59)
        self.assertEqual(ws_frozen.ATOM_COUNT, 63)
        self.assertEqual(ws_frozen.PARENT_COUNT, 17)
        self.assertEqual(ws_frozen.LEAF_COUNT, 42)
        self.assertEqual(ws_frozen.EXCLUSION_COUNT, 21)
        self.assertEqual(ws_frozen.RESIDUAL_COUNT_GAP, 0)
        self.assertEqual(ws_frozen.PARSED_COUNT,
                         ws_frozen.PARENT_COUNT + ws_frozen.LEAF_COUNT)
        self.assertEqual(ws_frozen.ATOM_COUNT,
                         ws_frozen.LEAF_COUNT + ws_frozen.EXCLUSION_COUNT)
        self.assertEqual(sum(ws_frozen.LEAF_FAMILY_COUNTS.values()), 42)
        self.assertEqual(sum(ws_frozen.EXCLUSION_FAMILY_COUNTS.values()), 21)
        self.assertEqual(sum(ws_frozen.ATOM_FAMILY_COUNTS.values()), 63)
        for family in ("api", "cli", "tail"):
            self.assertEqual(
                ws_frozen.LEAF_FAMILY_COUNTS[family]
                + ws_frozen.EXCLUSION_FAMILY_COUNTS[family],
                ws_frozen.ATOM_FAMILY_COUNTS[family])
        self.assertEqual(ws_frozen.LEAF_FAMILY_COUNTS, {"api": 19, "cli": 7, "tail": 16})
        self.assertEqual(ws_frozen.EXCLUSION_FAMILY_COUNTS, {"api": 7, "cli": 4, "tail": 10})
        self.assertEqual(ws_frozen.ATOM_FAMILY_COUNTS, {"api": 26, "cli": 11, "tail": 26})

    def test_note_carries_frozen_anchors_and_commit_evidence(self):
        for needle in (
            "row #7 story_user_operates_group_workspace",
            "register the workspace density closure",
            "59 parsed = 17 retained Workspace.* parents + 42 charged leaves",
            "63 source-anchored atoms = 42 leaves + 21 disclosed exclusions",
            "residual_count_gap=0 under both calibers: 59 = 17 + 42 and 63 = 42 + 21",
            "api26/cli11/tail26", "api19/cli7/tail16", "api7/cli4/tail10",
            "26=19+7", "11=7+4", "26=16+10",
            ws_frozen.GROUP_SOURCE_FILE, ws_frozen.STORIES_SOURCE_FILE,
            "L408-427", "L429-488",
            "6114dbfbc", "205715228", "63c2a3a52",
            "dual-workspace-field-run", "DUAL-WORKSPACE-DENSITY-01",
            "workspace-density-v1", "group-workspace-density-v1",
            "workspace-field-obligations-v1", "migration-contracts-v1",
            "observed_form", "retired_410", "legacy_service_spec",
            "notes-only integration", "dual-workspace-run", "r5/r6 receipts",
            "notes 0 to 1", "fail closed to NOT_RUN",
            "remote_validation=NOT_RUN", "grants_release_credit=false",
        ):
            self.assertIn(needle, NOTE)

    def test_inventory_fail_close_membership_and_counter_closure(self):
        self.assertIn(NOTE, EXECUTOR_MIGRATION_NOTES)
        flattened = [note for status in load_executor_migration().values()
                     for note in status["notes"]]
        self.assertEqual(Counter(flattened),
                         Counter(_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS))
        statuses = load_executor_migration()
        self.assertEqual(statuses[ws_frozen.PARENT]["notes"], [NOTE])

    def test_live_face_matches_json_source_verbatim(self):
        statuses = load_executor_migration()
        for story_id, live in statuses.items():
            source = FACE_BY_ID[story_id]
            self.assertEqual(live, source, story_id)

    def test_executor_face_command_registered_in_cli_text(self):
        self.assertIn("dual-workspace-field-run", CLI_TEXT)
        self.assertIn("dual-workspace-field-run", COMMAND_REGISTRATION_TEXT)
        self.assertIn("dual-workspace-field-run", NOTE)

    def test_md_row_and_pledge_disclose_write_domains(self):
        self.assertIn(ws_frozen.PARENT, MD_ROW)
        self.assertIn("| 7 |", MD_ROW)
        self.assertIn("dual-workspace-field-run", MD_ROW)
        self.assertIn("DUAL-WORKSPACE-DENSITY-01", MD_ROW)
        self.assertIn("remote_validation=NOT_RUN", INTEGRATION_PLEDGE)
        self.assertIn("grants_release_credit=false", INTEGRATION_PLEDGE)
        self.assertIn("no coverage-map overlay", INTEGRATION_PLEDGE)

    def test_proposal_facts_recompute_frozen_calibers(self):
        facts = proposal_facts()
        self.assertEqual(facts["note_token"], NOTE_TOKEN)
        self.assertEqual(facts["parent"], ws_frozen.PARENT)
        self.assertEqual(facts["atom_count"], 63)
        self.assertEqual(facts["leaf_count"], 42)
        self.assertEqual(facts["exclusion_count"], 21)
        self.assertEqual(facts["parent_count"], 17)
        self.assertEqual(facts["parsed_count"], 59)
        self.assertEqual(facts["residual_count_gap"], 0)
        self.assertEqual(facts["story_line_span"], [408, 427])
        self.assertEqual(facts["tail_line_span"], [429, 488])
        self.assertEqual(facts["workspace_field_command"], "dual-workspace-field-run")
        self.assertEqual(facts["runner_commit"], RUNNER_COMMIT)
        self.assertEqual(facts["seam_commit"], SEAM_COMMIT)
        self.assertEqual(facts["split_commit"], SPLIT_COMMIT)
        self.assertIsNone(facts["upstream_issue"])
        self.assertEqual(facts["source_files"], {
            "group.sh": ws_frozen.GROUP_SH_SHA256,
            "stories.sh": ws_frozen.STORIES_SH_SHA256,
        })

    def test_describe_only_texts_carry_no_credential_literals(self):
        for text in (NOTE, MD_ROW, INTEGRATION_PLEDGE):
            self.assertNotIn("Bearer", text)
            self.assertNotIn("Cookie:", text)
            self.assertNotIn("-----BEGIN", text)
