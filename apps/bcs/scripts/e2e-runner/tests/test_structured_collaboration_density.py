"""Offline contract, projection, and honesty tests for Wave-W2-#3."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bcs_e2e.run_state import RunStateError
from bcs_e2e.structured_collaboration_density import (
    FIELD_OBLIGATION_COUNT, PARSED_OBLIGATION_COUNT, PARENT_OBLIGATION_COUNT,
    RESIDUAL_COUNT_GAP, SOURCE_SHA256, ReplayTransport, canonical_replay_observations,
    density_accounting, family_accounting, parsed_obligations,
    run_structured_collaboration_density, structured_collaboration_density_plan,
    validate_density_profile,
)
from bcs_e2e.structured_collaboration_density import contract as density_contract
from bcs_e2e.structured_collaboration_density.registration_proposal import registration_proposal
from bcs_e2e.structured_collaboration_density.reporting import INTEGRATION_TOKEN

SOURCE = ROOT.parent / "e2e-test" / "stories.sh"
PACKAGE = ROOT / "bcs_e2e" / "structured_collaboration_density"


def local_profile() -> dict:
    operations = sorted({row.operation for row in parsed_obligations()})
    return {
        "schema_version": 1,
        "contract": "structured-collaboration-density-profile-v1",
        "environment": {"tier": "local", "id": "owned-local-density", "lifecycle": "local-provision", "deployment": "singlebox"},
        "entrypoint": {"kind": "singlebox"},
        "auth": {"header_env": {"Cookie": "BCS_E2E_HUMAN_COOKIE", "Authorization": "BCS_E2E_DRIVER_TOKEN"}},
        "operation_bindings": {name: {"method": "REPLAY", "path": name} for name in operations},
    }


def preprod_profile() -> dict:
    profile = local_profile()
    profile["environment"] = {"tier": "preprod", "id": "preprod-density", "lifecycle": "remote-attach", "deployment": "preprod"}
    profile["entrypoint"] = {"kind": "bcs-direct"}
    return profile


class StructuredCollaborationRosterTests(unittest.TestCase):
    def test_parsed_accounting_is_17_parents_plus_26_field_atoms(self):
        plan = structured_collaboration_density_plan()
        self.assertEqual(len(plan["obligations"]), 43)
        self.assertEqual(plan["accounting"], {
            "parsed_obligation_count": 43, "parent_obligation_count": 17,
            "field_atomic_count": 26, "residual_count_gap": 0,
            "parsed_coverage": "43/43",
        })
        self.assertEqual(PARSED_OBLIGATION_COUNT, 43)
        self.assertEqual(PARENT_OBLIGATION_COUNT, 17)
        self.assertEqual(FIELD_OBLIGATION_COUNT, 26)
        self.assertEqual(RESIDUAL_COUNT_GAP, 0)

    def test_roster_has_verbatim_method_range_quote_and_parent_projection(self):
        rows = parsed_obligations()
        self.assertEqual([row.obligation_id for row in rows[:17]], [f"STRUCTURED_DENSITY.P{i:02d}" for i in range(1, 18)])
        self.assertEqual([row.obligation_id for row in rows[17:]], [f"STRUCTURED_DENSITY.F{i:02d}" for i in range(1, 27)])
        self.assertEqual(tuple(row.parent_id for row in rows[:17]), tuple(f"STRUCTURED.{name}" for name in (
            "templates-list", "template-detail", "group-create", "group-read", "definition-read", "definition-patch",
            "upgrade-missing", "run-start", "run-read", "graph-read", "node-read", "pending-human-empty",
            "human-response-denied", "run-cancel", "run-terminal", "rerun-aborted-denied", "group-delete")))
        lines = SOURCE.read_text(encoding="utf-8").splitlines()
        for row in rows:
            self.assertTrue(row.line_start <= row.line_end)
            self.assertIn(row.quote, lines[row.line_start - 1], row.obligation_id)
            self.assertEqual(row.as_dict()["source"]["line_start"], row.line_start)
            self.assertEqual(row.as_dict()["source"]["line_end"], row.line_end)
            self.assertTrue(row.method)

    def test_source_sha256_is_recorded_and_live_anchor_drift_fails_closed(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA256)
        drifted = SOURCE.read_text(encoding="utf-8").replace("single-bot-guided-answer", "single-bot-guided-answer-drift", 1)
        with __import__("tempfile").TemporaryDirectory() as temp:
            shadow = Path(temp) / "stories.sh"
            shadow.write_text(drifted, encoding="utf-8")
            original = density_contract.SOURCE_PATH
            density_contract.SOURCE_PATH = shadow
            try:
                with self.assertRaises(RunStateError):
                    structured_collaboration_density_plan()
            finally:
                density_contract.SOURCE_PATH = original

    def test_unlocated_probes_are_disclosed_not_charged(self):
        plan = structured_collaboration_density_plan()
        self.assertEqual(plan["unlocated_count"], 0)
        self.assertEqual(len(plan["unlocated_source_probes"]), 3)
        self.assertEqual(len(plan["projection_exclusions"]), 9)
        self.assertEqual({row["line_start"] for row in plan["projection_exclusions"]}, {870, 871, 885, 888, 925, 952, 953, 964, 965})
        self.assertEqual(plan["accounting"]["residual_count_gap"], 0)
        self.assertIn("conditional alternate", plan["unlocated_source_probes"][1]["reason"])

    def test_line_range_tamper_fails_closed(self):
        rows = list(parsed_obligations())
        rows[0] = replace(rows[0], line_start=825)
        with self.assertRaises(RunStateError):
            density_contract._validate_source_anchors(tuple(rows))


class StructuredCollaborationReplayTests(unittest.TestCase):
    def test_owned_local_replay_passes_all_43_obligations(self):
        transport = ReplayTransport(canonical_replay_observations())
        report = run_structured_collaboration_density(profile=local_profile(), transport=transport)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["execution"]["completed_count"], 43)
        self.assertEqual(len(report["assertions"]), 43)
        self.assertEqual(len(transport.calls), 43)
        self.assertTrue(all(row["status"] == "PASS" for row in report["assertions"]))
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(density_accounting(report)["residual_count_gap"], 0)

    def test_local_without_transport_uses_offline_replay_only(self):
        report = run_structured_collaboration_density(profile=local_profile())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["execution"]["completed_count"], PARSED_OBLIGATION_COUNT)

    def test_preprod_without_transport_is_not_run_and_never_passes(self):
        report = run_structured_collaboration_density(profile=preprod_profile())
        self.assertEqual(report["status"], "BLOCKED_INVENTORIED")
        self.assertEqual(report["execution"]["status"], "NOT_RUN")
        self.assertEqual(report["gap"]["id"], "STRUCTURED_COLLAB_DENSITY_PREPROD_TRANSPORT_GAP")
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(len(report["assertions"]), 43)
        self.assertTrue(all(row["status"] == "NOT_RUN" for row in report["assertions"]))

    def test_bad_observation_fails_and_later_rows_are_not_run(self):
        observations = canonical_replay_observations()
        observations["group.read"] = type(observations["group.read"])(200, {"group_strategy": "chat"})
        report = run_structured_collaboration_density(profile=local_profile(), transport=ReplayTransport(observations))
        by_id = {row["obligation_id"]: row for row in report["assertions"]}
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(by_id["STRUCTURED_DENSITY.F05"]["status"], "FAIL")
        self.assertEqual(by_id["STRUCTURED_DENSITY.F06"]["status"], "NOT_RUN")
        self.assertEqual(by_id["STRUCTURED_DENSITY.F06"]["error_type"], "BLOCKED_AFTER_ASSERTION_FAILURE")

    def test_missing_operation_is_transport_failure_not_pass(self):
        report = run_structured_collaboration_density(profile=local_profile(), transport=ReplayTransport({}))
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["assertions"][0]["status"], "FAIL")
        self.assertEqual(report["assertions"][0]["error_type"], "KeyError")
        self.assertTrue(all(row["status"] == "NOT_RUN" for row in report["assertions"][1:]))


class StructuredCollaborationAdmissionTests(unittest.TestCase):
    def test_profiles_admit_local_and_preprod_shapes(self):
        validate_density_profile(local_profile())
        validate_density_profile(preprod_profile())

    def test_profile_rejects_literal_credentials_and_wrong_env_names(self):
        bad = local_profile()
        bad["auth"]["header_env"]["Cookie"] = "cookie-value-not-a-name"
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = local_profile()
        bad["secrets"] = {"Cookie": "do-not-accept"}
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)

    def test_profile_requires_exact_reviewed_operations(self):
        bad = local_profile()
        bad["operation_bindings"].pop("run.read")
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = local_profile()
        bad["operation_bindings"]["unreviewed.operation"] = {"method": "GET", "path": "/x"}
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)


class StructuredCollaborationIntegrationDisciplineTests(unittest.TestCase):
    def test_describe_only_proposal_reuses_landed_envelope_without_registration_command(self):
        proposal = registration_proposal()
        self.assertEqual(proposal["status"], "DESCRIBE_ONLY")
        self.assertEqual(proposal["token"], INTEGRATION_TOKEN)
        self.assertEqual(proposal["executor_row"], 3)
        self.assertFalse(proposal["new_registration_command"])
        self.assertEqual(proposal["parsed_obligations"], 43)
        self.assertEqual(proposal["residual_count_gap"], 0)
        self.assertIn("executor-migration.json", proposal["red_lines"])

    def test_package_has_no_network_or_credential_value_literals(self):
        text = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py"))
        for forbidden in ("requests", "urllib", "socket", "subprocess", "os.getenv", "os.environ", "Cookie: ", "Bearer ", "-----BEGIN"):
            self.assertNotIn(forbidden, text)
        self.assertIn("BCS_E2E_HUMAN_COOKIE", text)
        self.assertIn("BCS_E2E_DRIVER_TOKEN", text)
        self.assertNotIn("cookie-value", text)
        self.assertNotIn("token-value", text)

    def test_line_budget_and_package_shape(self):
        for path in PACKAGE.glob("*.py"):
            self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 1000, path.name)
        self.assertEqual({path.stem for path in PACKAGE.glob("*.py")}, {
            "__init__", "contract", "defect", "profile", "reporting", "registration_proposal", "run", "transport"})

    def test_family_accounting_is_mechanical(self):
        self.assertEqual(family_accounting(), {"parsed": 43, "parents": 17, "field_atomic": 26, "residual_count_gap": 0})


if __name__ == "__main__":
    unittest.main()
