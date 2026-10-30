"""Focused offline regression for Draft delivery Wave-W2-#12.

The suite only validates source anchoring and in-memory replay.  It does not
launch bcs-cli, open a socket, call preprod, resolve credentials, or write an
executor registration.  The local replay deliberately passes all 32 rows;
the preprod projection stays NOT_RUN and discloses the known 302 identity
redirect defect instead of silently adapting the route.
"""
from __future__ import annotations

import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bcs_e2e.collaboration_draft_density.contract as contract_module
from bcs_e2e.collaboration_draft_density import (
    DENSITY_OBLIGATION_COUNT, PARSED_OBLIGATION_COUNT, PARENT, PARENT_OBLIGATION_COUNT,
    PRODUCT_DEFECT_302_ID, PROPOSAL_TOKEN, RESIDUAL_COUNT_GAP,
    SOURCE_SHA256, STATUS_BLOCKED_INVENTORIED, STATUS_FAIL, STATUS_NOT_RUN, STATUS_PASS,
    density_plan, describe_only_mainline_fragment, mock_observations, obligations, project,
)
from bcs_e2e.run_state import RunStateError

ROOT = Path(__file__).resolve().parents[1]
STORY = ROOT.parent / "e2e-test" / "collaboration_draft_story.sh"
EXPECTED_PARENT_IDS = (
    "DRAFT-DELIVERY.offline-preview", "DRAFT-DELIVERY.create-first-revision",
    "DRAFT-DELIVERY.owner-metadata", "DRAFT-DELIVERY.shared-read-validation",
    "DRAFT-DELIVERY.fixed-revision-cli", "DRAFT-DELIVERY.cas-update-history",
    "DRAFT-DELIVERY.revoke-delete-lifecycle", "DRAFT-DELIVERY.journey-completes",
)


class CollaborationDraftDensityContractTests(unittest.TestCase):
    def test_roster_accounting_is_eight_parents_plus_24_atoms(self):
        rows = obligations()
        self.assertEqual(len(rows), PARSED_OBLIGATION_COUNT)
        self.assertEqual(sum(row.level == "PARENT" for row in rows), PARENT_OBLIGATION_COUNT)
        self.assertEqual(sum(row.level == "DENSITY" for row in rows), DENSITY_OBLIGATION_COUNT)
        self.assertEqual(PARSED_OBLIGATION_COUNT - PARENT_OBLIGATION_COUNT,
                         DENSITY_OBLIGATION_COUNT)
        self.assertEqual(RESIDUAL_COUNT_GAP, 0)
        self.assertEqual([row.obligation_id for row in rows[:8]], list(EXPECTED_PARENT_IDS))
        self.assertEqual(len({row.obligation_id for row in rows}), PARSED_OBLIGATION_COUNT)
        self.assertTrue(all(row.parent_obligation in EXPECTED_PARENT_IDS for row in rows))

    def test_every_obligation_has_verbatim_anchor_and_sha256_gate(self):
        raw = STORY.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), SOURCE_SHA256)
        lines = raw.decode("utf-8").splitlines()
        for row in obligations():
            self.assertEqual(row.source_file,
                             "apps/bcs/scripts/e2e-test/collaboration_draft_story.sh")
            self.assertEqual(row.quote,
                             "\n".join(lines[row.line_start - 1:row.line_end]), row.obligation_id)
            self.assertTrue(row.quote)
            self.assertTrue(row.located)
            self.assertGreaterEqual(row.line_start, 1)
            self.assertLessEqual(row.line_end, len(lines))

    def test_anchor_drift_fails_closed_before_projection(self):
        pristine = STORY.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            drifted = Path(tmp) / STORY.name
            drifted.write_text(pristine.replace("no-store", "public"), encoding="utf-8")
            with mock.patch.object(contract_module, "SOURCE_PATH", drifted):
                with self.assertRaisesRegex(RunStateError, "SHA-256 drifted"):
                    contract_module.obligations()

    def test_plan_discloses_denominator_honesty_and_named_credentials_only(self):
        plan = density_plan()
        self.assertEqual(plan["parent"], PARENT)
        self.assertEqual(plan["parsed_obligation_count"], 32)
        self.assertEqual(plan["parent_obligation_count"], 8)
        self.assertEqual(plan["density_obligation_count"], 24)
        self.assertEqual(plan["residual_count_gap"], 0)
        self.assertEqual(plan["credential_references"],
                         ["BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"])
        self.assertEqual(plan["remote_validation"], "NOT_RUN")
        self.assertFalse(plan["grants_release_credit"])
        self.assertIn("302", plan["projection_rule"])

    def test_no_credential_value_literals_or_network_runner_in_package(self):
        package_dir = Path(contract_module.__file__).resolve().parent
        uuid_like = re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
        for path in sorted(package_dir.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(uuid_like.search(text), path.name)
            self.assertIsNone(re.search(r"Bearer\s+[A-Za-z0-9._~-]{12,}", text), path.name)
            self.assertNotIn("BCS_E2E_HUMAN_COOKIE=", text, path.name)
            self.assertNotIn("BCS_E2E_DRIVER_TOKEN=", text, path.name)
            self.assertNotIn("http.client", text, path.name)
            self.assertNotIn("import socket", text, path.name)


class CollaborationDraftDensityReplayTests(unittest.TestCase):
    def test_owned_local_replay_passes_all_32_rows(self):
        receipt = project(mock_observations())
        self.assertEqual(receipt["status"], STATUS_PASS)
        self.assertEqual(receipt["counts"], {STATUS_PASS: 32, STATUS_FAIL: 0, STATUS_NOT_RUN: 0})
        self.assertEqual(receipt["parsed_obligation_count"], 32)
        self.assertEqual(receipt["density_obligation_count"], 24)
        self.assertEqual(receipt["residual_count_gap"], 0)
        self.assertEqual(receipt["remote_validation"], "NOT_RUN")
        self.assertFalse(receipt["grants_release_credit"])

    def test_missing_observation_is_not_run_and_never_pass(self):
        observations = mock_observations()
        observations["fields"].pop("DRAFT-DENSITY.D18")
        receipt = project(observations)
        rows = {row["obligation_id"]: row for row in receipt["obligations"]}
        self.assertEqual(rows["DRAFT-DENSITY.D18"]["status"], STATUS_NOT_RUN)
        self.assertEqual(receipt["status"], STATUS_BLOCKED_INVENTORIED)

    def test_bad_observation_is_fail_not_not_run(self):
        observations = mock_observations()
        observations["values"]["stale_status"] = 200
        receipt = project(observations)
        rows = {row["obligation_id"]: row for row in receipt["obligations"]}
        self.assertEqual(rows["DRAFT-DENSITY.D19"]["status"], STATUS_FAIL)
        self.assertEqual(receipt["status"], STATUS_FAIL)

    def test_preprod_without_observations_is_named_not_run_defect_fail_close(self):
        receipt = project(None, tier="preprod")
        self.assertEqual(receipt["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(receipt["counts"], {STATUS_PASS: 0, STATUS_FAIL: 0, STATUS_NOT_RUN: 32})
        self.assertEqual(receipt["upstream_defects"][PRODUCT_DEFECT_302_ID]["status"],
                         "PRODUCT_DEFECT_ON_FILE")
        self.assertIn("fail-close", receipt["upstream_defects"][PRODUCT_DEFECT_302_ID]["disposition"])
        self.assertFalse(receipt["grants_release_credit"])

    def test_describe_only_mainline_fragment_has_token_and_no_registration_command(self):
        fragment = describe_only_mainline_fragment()
        self.assertIn(PROPOSAL_TOKEN, fragment)
        self.assertIn("executor-migration.json", fragment)
        self.assertIn("no new registration command", fragment)
        self.assertIn("302", fragment)


if __name__ == "__main__":
    unittest.main()
