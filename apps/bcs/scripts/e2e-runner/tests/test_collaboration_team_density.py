from __future__ import annotations

import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bcs_e2e.collaboration_team_density.contract as contract_module
from bcs_e2e.collaboration_team_density import (
    DENSITY_OBLIGATION_COUNT,
    PARSED_OBLIGATION_COUNT,
    PARENT_OBLIGATION_COUNT,
    PROPOSAL_TOKEN,
    RESIDUAL_COUNT_GAP,
    SOURCE_SHA256,
    STATUS_EXECUTION_GAP,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    density_plan,
    describe_only_mainline_fragment,
    mock_observations,
    obligations,
    project,
)
from bcs_e2e.run_state import RunStateError

ROOT = Path(__file__).resolve().parents[2]
CLI_STORIES = ROOT / "e2e-test" / "cli-stories.sh"


class CollaborationTeamDensityContractTests(unittest.TestCase):
    def test_roster_has_eight_parents_twenty_two_atoms_and_zero_residual(self):
        rows = obligations()
        self.assertEqual(len(rows), PARSED_OBLIGATION_COUNT)
        self.assertEqual(sum(row.level == "PARENT" for row in rows), PARENT_OBLIGATION_COUNT)
        self.assertEqual(sum(row.level == "DENSITY" for row in rows), DENSITY_OBLIGATION_COUNT)
        self.assertEqual(PARSED_OBLIGATION_COUNT - PARENT_OBLIGATION_COUNT,
                         DENSITY_OBLIGATION_COUNT)
        self.assertEqual(RESIDUAL_COUNT_GAP, 0)
        self.assertEqual(len({row.obligation_id for row in rows}), PARSED_OBLIGATION_COUNT)
        self.assertEqual([row.obligation_id for row in rows[:8]], [
            "CLI-COLLAB-TEAM.protect-visibility",
            "CLI-COLLAB-TEAM.friend-request-reject",
            "CLI-COLLAB-TEAM.group-create-read",
            "CLI-COLLAB-TEAM.group-add-member",
            "CLI-COLLAB-TEAM.group-list",
            "CLI-COLLAB-TEAM.group-fuse",
            "CLI-COLLAB-TEAM.group-complete",
            "CLI-COLLAB-TEAM.group-terminate",
        ])

    def test_every_row_has_exact_live_source_quote_and_sha256_gate(self):
        raw = CLI_STORIES.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), SOURCE_SHA256)
        lines = raw.decode("utf-8").splitlines()
        for row in obligations():
            self.assertEqual(row.source_file, "apps/bcs/scripts/e2e-test/cli-stories.sh")
            self.assertEqual(row.quote, "\n".join(lines[row.line_start - 1:row.line_end]))
            self.assertTrue(row.quote)
            self.assertTrue(row.located)
            self.assertGreaterEqual(row.line_start, 1)
            self.assertLessEqual(row.line_end, len(lines))

    def test_anchor_drift_fails_closed(self):
        pristine = CLI_STORIES.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            drifted = Path(tmp) / "cli-stories.sh"
            drifted.write_text(pristine.replace("recommendation", "synthetic"), encoding="utf-8")
            with mock.patch.object(contract_module, "SOURCE_PATH", drifted):
                with self.assertRaisesRegex(RunStateError, "SHA-256 drifted"):
                    contract_module.obligations()

    def test_plan_discloses_density_accounting_and_execution_boundary(self):
        plan = density_plan()
        self.assertEqual(plan["parsed_obligation_count"], 30)
        self.assertEqual(plan["parent_obligation_count"], 8)
        self.assertEqual(plan["density_obligation_count"], 22)
        self.assertEqual(plan["residual_count_gap"], 0)
        self.assertEqual(plan["remote_validation"], "NOT_RUN")
        self.assertFalse(plan["grants_release_credit"])
        self.assertIn("formal bot-group API", plan["projection_rule"])
        self.assertEqual(plan["credential_references"],
                         ["BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"])

    def test_no_credential_value_literals(self):
        package_dir = Path(contract_module.__file__).resolve().parent
        uuid_like = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
        for path in sorted(package_dir.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(uuid_like.search(text), path.name)
            self.assertNotIn("Bearer ", text, path.name)
        self.assertNotIn("BCS_E2E_HUMAN_COOKIE=", "\n".join(
            path.read_text(encoding="utf-8") for path in package_dir.glob("*.py")))


class CollaborationTeamDensityReplayTests(unittest.TestCase):
    def test_complete_owned_local_replay_is_thirty_of_thirty_pass(self):
        receipt = project(mock_observations())
        self.assertEqual(receipt["status"], STATUS_PASS)
        self.assertEqual(receipt["parsed_obligation_count"], 30)
        self.assertEqual(receipt["counts"], {
            STATUS_PASS: 30, STATUS_FAIL: 0, STATUS_NOT_RUN: 0,
            STATUS_EXECUTION_GAP: 0,
        })
        self.assertEqual(receipt["residual_count_gap"], 0)
        self.assertEqual(len(receipt["obligations"]), 30)
        self.assertEqual(receipt["remote_validation"], "NOT_RUN")
        self.assertFalse(receipt["grants_release_credit"])

    def test_missing_formal_api_projection_is_execution_gap_not_pass(self):
        observations = mock_observations()
        observations.pop("formal_api")
        receipt = project(observations)
        rows = {row["obligation_id"]: row for row in receipt["obligations"]}
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertEqual(rows["CLI-COLLAB-TEAM.D15"]["status"], STATUS_EXECUTION_GAP)
        self.assertEqual(rows["CLI-COLLAB-TEAM.D16"]["status"], STATUS_EXECUTION_GAP)
        self.assertEqual(rows["CLI-COLLAB-TEAM.D17"]["status"], STATUS_EXECUTION_GAP)

    def test_bad_observation_is_fail_and_missing_observation_is_not_run(self):
        observations = mock_observations()
        observations["fuse"]["recommendation"] = ""
        observations.pop("terminated")
        receipt = project(observations)
        rows = {row["obligation_id"]: row for row in receipt["obligations"]}
        self.assertEqual(rows["CLI-COLLAB-TEAM.D18"]["status"], STATUS_FAIL)
        self.assertEqual(rows["CLI-COLLAB-TEAM.D21"]["status"], STATUS_NOT_RUN)
        self.assertEqual(receipt["status"], STATUS_FAIL)

    def test_describe_only_registration_fragment_is_not_a_write(self):
        fragment = describe_only_mainline_fragment()
        self.assertIn(PROPOSAL_TOKEN, fragment)
        self.assertIn("executor-migration.json", fragment)
        self.assertIn("do not edit", fragment)


if __name__ == "__main__":
    unittest.main()
