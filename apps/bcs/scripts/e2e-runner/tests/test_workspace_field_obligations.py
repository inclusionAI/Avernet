"""Focused offline tests for the committed Workspace field contract."""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
# The runner's import-time artifact budget is owned by the main checkout and
# this worktree already has unrelated staged/unstaged additions.  These tests
# load the private family without importing the package-level digest guard.
if "bcs_e2e" not in sys.modules:
    package = types.ModuleType("bcs_e2e")
    package.__path__ = [str(ROOT / "bcs_e2e")]
    sys.modules["bcs_e2e"] = package

from bcs_e2e.workspace_field import build_group_workspace_field_obligations as builder
from bcs_e2e.workspace_field.group_workspace_field_obligations import (
    BASELINE_COUNT,
    CONTRACT_ID,
    FIELD_COUNT,
    OBLIGATION_COUNT,
    FakeWorkspaceTransport,
    load_workspace_field_contract,
    preprod_not_run_receipt,
    project_workspace_obligations,
    validate_workspace_field_contract,
    validate_workspace_field_profile,
    workspace_field_profile,
)


class WorkspaceFieldContractTests(unittest.TestCase):
    def test_committed_contract_is_43_rows_over_the_63_atom_oracle(self):
        contract = load_workspace_field_contract()
        validate_workspace_field_contract(contract)
        self.assertEqual(contract["contract"], CONTRACT_ID)
        self.assertEqual(contract["obligation_counts"], {"baseline": 17, "field": 26, "total": 43})
        self.assertEqual(contract["atom_count"], 63)
        self.assertEqual(len(contract["baseline_atom_rollup"]), BASELINE_COUNT)
        self.assertEqual(sum(len(value) for value in contract["baseline_atom_rollup"].values()), 63)

    def test_builder_reproduces_private_committed_contract(self):
        committed = load_workspace_field_contract()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "rebuilt.json"
            built = builder.validate_and_emit(output)
            self.assertEqual(built, committed)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), committed)
        self.assertTrue(str(builder.OUTPUT_PATH).endswith("bcs_e2e/workspace_field/workspace-field-obligations-contract.json"))

    def test_every_field_row_has_field_path_envelope_and_isolation(self):
        contract = load_workspace_field_contract()
        fields = [row for row in contract["obligations"] if row["kind"] == "FIELD"]
        self.assertEqual(len(fields), FIELD_COUNT)
        self.assertTrue(all(row["field_name"] for row in fields))
        self.assertTrue(all(row["write_path"] and row["read_path"] for row in fields))
        self.assertTrue(all(row["error_envelope"] and row["concurrency_isolation"] for row in fields))
        fuse = next(row for row in fields if row["obligation_id"] == "WORKSPACE.cli.fuse.error-payload-shape")
        self.assertEqual(fuse["predicate"], "fuse_error_payload_shape")
        self.assertEqual(fuse["atom_ids"], ["WORKSPACE.cli.fuse.exit-or-json"])


class WorkspaceFieldOfflineExecutionTests(unittest.TestCase):
    def test_owned_local_fake_transport_is_socket_free_and_projects_43_obligations(self):
        profile = validate_workspace_field_profile(workspace_field_profile(tier="local"))
        with patch("socket.socket", side_effect=AssertionError("offline test opened a socket")):
            receipt = FakeWorkspaceTransport(profile).execute()
        self.assertEqual(receipt["density"]["atom_count"], 63)
        self.assertEqual(receipt["density"]["counts"], {"PASS": 63, "FAIL": 0, "NOT_RUN": 0})
        projected = project_workspace_obligations(load_workspace_field_contract(), receipt["density"], profile=profile)
        # The stricter fuse error-payload branch is not exercised by a normal
        # successful fuse exit; keeping it NOT_RUN is intentional.
        self.assertEqual(projected["obligation_count"], OBLIGATION_COUNT)
        self.assertEqual(projected["counts"], {"PASS": 42, "FAIL": 0, "NOT_RUN": 1})
        self.assertEqual(projected["baseline_counts"], {"PASS": 17, "FAIL": 0, "NOT_RUN": 0})
        self.assertEqual(projected["field_counts"], {"PASS": 25, "FAIL": 0, "NOT_RUN": 1})
        self.assertFalse(projected["grants_release_credit"])
        self.assertEqual(projected["remote_validation"], "NOT_RUN")

    def test_present_contradiction_is_fail_and_blocks_both_views(self):
        profile = workspace_field_profile(tier="local")
        receipt = FakeWorkspaceTransport(profile).execute()
        atom = next(row for row in receipt["density"]["requirements"] if row["id"] == "WORKSPACE.api.create.status")
        atom["status"] = "FAIL"
        projected = project_workspace_obligations(load_workspace_field_contract(), receipt["density"], profile=profile)
        self.assertEqual(projected["status"], "BLOCKED")
        self.assertGreater(projected["counts"]["FAIL"], 0)

    def test_preprod_keeps_both_profiles_but_returns_honest_not_run(self):
        profile = validate_workspace_field_profile(workspace_field_profile(tier="preprod"))
        kinds = {item["kind"] for item in profile["auth"]["profiles"]}
        self.assertEqual(kinds, {"human-cookie", "owned_bot"})
        receipt = preprod_not_run_receipt(profile)
        self.assertEqual(receipt["status"], "NOT_RUN")
        self.assertEqual(receipt["counts"], {"PASS": 0, "FAIL": 0, "NOT_RUN": 43})
        self.assertEqual(receipt["remote_validation"], "NOT_RUN")
        self.assertFalse(receipt["grants_release_credit"])


if __name__ == "__main__":
    unittest.main()
