"""Registration locks for the row #1 delivery-policy density closure proposal.

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

from bcs_e2e.delivery_density.contract import (
    CASE_ID,
    DENSITY_ASSERTION_COUNT,
    DENSITY_FIELD_COUNT,
    DENSITY_PARENT_COUNT,
    DUAL_DELIVERY_POLICY_DENSITY_COMMAND,
    PROJECTION_CONTRACT,
    REPORT_CONTRACT,
)
from bcs_e2e.delivery_density.obligations import (
    COARSE_PARENT_COUNT,
    FIELD_OBLIGATION_COUNT,
    FIELD_SITE_COUNT,
    PARSED_OBLIGATION_COUNT,
    PARENT,
)
from bcs_e2e.delivery_density.registration_proposal import (
    INTEGRATION_PLEDGE,
    MD_ROW,
    NOTE,
    NOTE_TOKEN,
    RUNNER_COMMIT,
    proposal_facts,
)
from bcs_e2e.inventory import (
    EXECUTOR_MIGRATION_NOTES,
    _EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS,
    load_executor_migration,
)

CLI_TEXT = (ROOT / "bcs_e2e" / "cli.py").read_text(encoding="utf-8")
COMMAND_REGISTRATION_TEXT = (
    ROOT / "bcs_e2e" / "case_command_registration.py").read_text(encoding="utf-8")
EXECUTOR_FACE = json.loads(
    (ROOT / "executor-migration.json").read_text(encoding="utf-8"))
FACE_BY_ID = {row["id"]: row["executor_migration"]
              for row in EXECUTOR_FACE["rows"]}


class DeliveryDensityRegistrationProposalTests(unittest.TestCase):
    def test_executor_row_face_locked_notes_only(self):
        face = FACE_BY_ID[PARENT]
        self.assertEqual(face["mode"], "DUAL")
        self.assertEqual(face["preprod_verdict"], "PASS")
        self.assertEqual(face["gates"], [])
        self.assertEqual(face["notes"], [NOTE])

    def test_note_token_and_commit_anchors(self):
        self.assertEqual(
            NOTE_TOKEN, "DELIVERY_POLICY_DENSITY_26_FIELD_OBLIGATIONS_20261101")
        self.assertEqual(RUNNER_COMMIT, "6be8d9363")
        self.assertEqual(
            DUAL_DELIVERY_POLICY_DENSITY_COMMAND,
            "dual-delivery-policy-density-run")
        self.assertEqual(CASE_ID, "DUAL-DELIVERY-POLICY-DENSITY-01")
        self.assertEqual(PROJECTION_CONTRACT, "delivery-density-projection-v1")
        self.assertEqual(
            REPORT_CONTRACT, "dual-delivery-policy-density-case-run-v1")

    def test_mathematical_closure(self):
        self.assertEqual(COARSE_PARENT_COUNT, 10)
        self.assertEqual(FIELD_SITE_COUNT, 13)
        self.assertEqual(FIELD_OBLIGATION_COUNT, 26)
        self.assertEqual(PARSED_OBLIGATION_COUNT, 36)
        self.assertEqual(DENSITY_PARENT_COUNT, 10)
        self.assertEqual(DENSITY_FIELD_COUNT, 26)
        self.assertEqual(DENSITY_ASSERTION_COUNT, 36)
        self.assertEqual(
            PARSED_OBLIGATION_COUNT, COARSE_PARENT_COUNT + FIELD_OBLIGATION_COUNT)
        self.assertEqual(DENSITY_ASSERTION_COUNT,
                         DENSITY_PARENT_COUNT + DENSITY_FIELD_COUNT)

    def test_note_carries_frozen_anchors_and_commit_evidence(self):
        for needle in (
            "row #1 story_human_manages_delivery_policy",
            "register the 26 field-anchored density obligations",
            "retained DELIVERY.* coarse parent envelope",
            "36-point parsed projection = 10 legacy coarse parents + 26 field atoms",
            "13 anchored upstream field sites in stories.sh",
            "residual_count_gap=0 on the projection caliber",
            "NOT_CHARGED_SOURCE_PROBES",
            "dual-delivery-policy-density-run",
            "DUAL-DELIVERY-POLICY-DENSITY-01",
            "delivery-density-projection-v1",
            "6be8d9363",
            "notes-only integration",
            "dual-delivery-policy-run envelope",
            "r5/r16 PASS receipts",
            "notes 0 to 1",
            "remote_validation=NOT_RUN",
            "grants_release_credit=false",
        ):
            self.assertIn(needle, NOTE)

    def test_inventory_fail_close_membership_and_counter_closure(self):
        self.assertIn(NOTE, EXECUTOR_MIGRATION_NOTES)
        flattened = [note for status in load_executor_migration().values()
                     for note in status["notes"]]
        self.assertEqual(Counter(flattened),
                         Counter(_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS))
        statuses = load_executor_migration()
        self.assertEqual(statuses[PARENT]["notes"], [NOTE])

    def test_live_face_matches_json_source_verbatim(self):
        statuses = load_executor_migration()
        for story_id, live in statuses.items():
            source = FACE_BY_ID[story_id]
            self.assertEqual(live, source, story_id)

    def test_executor_face_command_registered_in_cli_text(self):
        self.assertIn("dual-delivery-policy-density-run", CLI_TEXT)
        self.assertIn("dual-delivery-policy-density-run", COMMAND_REGISTRATION_TEXT)
        self.assertIn("dual-delivery-policy-density-run", NOTE)

    def test_md_row_and_pledge_disclose_write_domains(self):
        self.assertIn(PARENT, MD_ROW)
        self.assertIn("| 1 |", MD_ROW)
        self.assertIn("dual-delivery-policy-density-run", MD_ROW)
        self.assertIn("DUAL-DELIVERY-POLICY-DENSITY-01", MD_ROW)
        self.assertIn("remote_validation=NOT_RUN", INTEGRATION_PLEDGE)
        self.assertIn("grants_release_credit=false", INTEGRATION_PLEDGE)
        self.assertIn("no coverage-map overlay", INTEGRATION_PLEDGE)

    def test_proposal_facts_recompute_frozen_calibers(self):
        facts = proposal_facts()
        self.assertEqual(facts["note_token"], NOTE_TOKEN)
        self.assertEqual(facts["parent"], PARENT)
        self.assertEqual(facts["coarse_parent_count"], 10)
        self.assertEqual(facts["field_site_count"], 13)
        self.assertEqual(facts["field_obligation_count"], 26)
        self.assertEqual(facts["parsed_obligation_count"], 36)
        self.assertEqual(facts["density_parent_count"], 10)
        self.assertEqual(facts["density_field_count"], 26)
        self.assertEqual(facts["case_id"], CASE_ID)
        self.assertEqual(facts["projection_contract"], PROJECTION_CONTRACT)
        self.assertEqual(facts["report_contract"], REPORT_CONTRACT)
        self.assertEqual(facts["dual_density_command"],
                         DUAL_DELIVERY_POLICY_DENSITY_COMMAND)
        self.assertEqual(facts["runner_commit"], RUNNER_COMMIT)
        self.assertIsNone(facts["upstream_issue"])

    def test_describe_only_texts_carry_no_credential_literals(self):
        for text in (NOTE, MD_ROW, INTEGRATION_PLEDGE):
            self.assertNotIn("Bearer", text)
            self.assertNotIn("Cookie:", text)
            self.assertNotIn("-----BEGIN", text)
