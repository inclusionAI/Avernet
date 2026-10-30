"""Offline density, anchor, replay, and integration tests for Wave-W2-#17."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bcs_e2e.group_context_density import (  # noqa: E402
    FIELD_OBLIGATION_COUNT,
    GROUP_CONTEXT_PREPROD_GAP_ID,
    PARENT_OBLIGATION_COUNT,
    PARSED_OBLIGATION_COUNT,
    RESIDUAL_COUNT_GAP,
    SOURCE_SHA256,
    ReplayTransport,
    STATUS_BLOCKED_INVENTORIED,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    canonical_replay_observations,
    density_accounting,
    family_accounting,
    group_context_density_plan,
    parsed_obligations,
    run_group_context_density,
    validate_density_profile,
)
from bcs_e2e.group_context_density import contract as density_contract  # noqa: E402
from bcs_e2e.group_context_density.registration_proposal import (  # noqa: E402
    registration_proposal,
)
from bcs_e2e.group_context_density.reporting import (  # noqa: E402
    INTEGRATION_TOKEN,
    md_table,
    proposed_executor_migration_notes,
)
from bcs_e2e.run_state import RunStateError  # noqa: E402

SOURCE = ROOT.parent / "e2e-test" / "cli-stories.sh"
PACKAGE = ROOT / "bcs_e2e" / "group_context_density"

PARENT_IDS = (
    "CLI-GROUP-CONTEXT.workspace",
    "CLI-GROUP-CONTEXT.add-group-entry",
    "CLI-GROUP-CONTEXT.add-session-entry",
    "CLI-GROUP-CONTEXT.add-round-entry",
    "CLI-GROUP-CONTEXT.get-initial-entry",
    "CLI-GROUP-CONTEXT.update-entry",
    "CLI-GROUP-CONTEXT.list-entries",
    "CLI-GROUP-CONTEXT.get-updated-entry",
    "CLI-GROUP-CONTEXT.cleanup-workspace",
)

ANCHOR_RANGES = ((571, 571), (586, 596), (598, 609), (611, 623),
                 (625, 634), (636, 646), (648, 657), (659, 667),
                 (669, 670), (574, 574), (596, 596), (609, 609),
                 (623, 623), (634, 634), (646, 646), (657, 657),
                 (667, 667), (670, 670))


def local_profile() -> dict:
    operations = sorted({row.operation for row in parsed_obligations()})
    return {
        "schema_version": 1,
        "contract": "group-context-density-profile-v1",
        "environment": {
            "tier": "local", "id": "group-context-density-local",
            "lifecycle": "local-provision", "deployment": "singlebox",
        },
        "entrypoint": {"kind": "singlebox"},
        "auth": {"header_env": {
            "Cookie": "BCS_E2E_HUMAN_COOKIE",
            "Authorization": "BCS_E2E_DRIVER_TOKEN",
        }},
        "operation_bindings": {
            operation: {"method": "REPLAY", "path": operation}
            for operation in operations
        },
    }


def preprod_profile() -> dict:
    profile = local_profile()
    profile["environment"] = {
        "tier": "preprod", "id": "group-context-density-preprod",
        "lifecycle": "remote-attach", "deployment": "preprod",
    }
    profile["entrypoint"] = {"kind": "bcs-direct"}
    return profile


class TestGroupContextRoster(unittest.TestCase):
    def test_parsed_accounting_is_nine_parents_plus_nine_fields(self):
        plan = group_context_density_plan()
        self.assertEqual(plan["accounting"], {
            "parsed_obligation_count": 18,
            "parent_obligation_count": 9,
            "field_atomic_count": 9,
            "residual_count_gap": 0,
            "parsed_coverage": "18/18",
        })
        self.assertEqual(PARSED_OBLIGATION_COUNT, 18)
        self.assertEqual(PARENT_OBLIGATION_COUNT, 9)
        self.assertEqual(FIELD_OBLIGATION_COUNT, 9)
        self.assertEqual(RESIDUAL_COUNT_GAP, 0)

    def test_roster_is_verbatim_and_projects_existing_nine_parents(self):
        rows = parsed_obligations()
        self.assertEqual(len(rows), 18)
        self.assertEqual([row.obligation_id for row in rows],
                         [f"GROUP_CONTEXT_DENSITY.P{i:02d}" for i in range(1, 10)]
                         + [f"GROUP_CONTEXT_DENSITY.F{i:02d}" for i in range(1, 10)])
        self.assertEqual(tuple(row.parent_obligation for row in rows[:9]), PARENT_IDS)
        self.assertEqual(tuple((row.line_start, row.line_end) for row in rows), ANCHOR_RANGES)
        source_lines = SOURCE.read_text(encoding="utf-8").splitlines()
        for row in rows:
            self.assertTrue(row.line_start <= row.line_end)
            self.assertIn(row.quote, source_lines[row.line_start - 1], row.obligation_id)
            self.assertTrue(row.as_dict()["located"])
            self.assertTrue(row.method)

    def test_source_sha256_is_recorded_and_anchor_drift_fails_closed(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA256)
        drifted = SOURCE.read_text(encoding="utf-8").replace(
            "CLI group-context workspace returns a session id",
            "CLI group-context workspace returns a drifted session id", 1)
        with tempfile.TemporaryDirectory() as temp:
            shadow = Path(temp) / "cli-stories.sh"
            shadow.write_text(drifted, encoding="utf-8")
            original = density_contract.SOURCE_PATH
            density_contract.SOURCE_PATH = shadow
            try:
                with self.assertRaises(RunStateError):
                    group_context_density_plan()
            finally:
                density_contract.SOURCE_PATH = original

    def test_projection_exclusions_are_disclosed_and_not_charged(self):
        plan = group_context_density_plan()
        self.assertEqual(plan["unlocated_count"], 0)
        self.assertEqual(plan["unlocated_source_probes"], [])
        self.assertGreaterEqual(len(plan["projection_exclusions"]), 5)
        self.assertEqual(plan["accounting"]["residual_count_gap"], 0)
        self.assertIn("control-flow", " ".join(
            row["reason"] for row in plan["projection_exclusions"]))


class TestGroupContextReplay(unittest.TestCase):
    def test_owned_local_replay_passes_all_eighteen_rows(self):
        transport = ReplayTransport(canonical_replay_observations())
        report = run_group_context_density(
            profile=local_profile(), transport=transport)
        self.assertEqual(report["status"], STATUS_PASS)
        self.assertEqual(report["execution"]["completed_count"], 18)
        self.assertEqual(len(report["assertions"]), 18)
        self.assertEqual(len(transport.calls), 18)
        self.assertTrue(all(row["status"] == STATUS_PASS
                            for row in report["assertions"]))
        self.assertEqual(density_accounting(report)["residual_count_gap"], 0)
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])

    def test_local_without_transport_uses_only_the_canonical_replay(self):
        report = run_group_context_density(profile=local_profile())
        self.assertEqual(report["status"], STATUS_PASS)
        self.assertEqual(report["execution"]["completed_count"], 18)
        self.assertEqual(report["execution"]["total_count"], 18)

    def test_preprod_without_transport_is_blocked_and_not_run(self):
        report = run_group_context_density(profile=preprod_profile())
        self.assertEqual(report["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(report["execution"]["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(report["execution"]["error_type"], "PREPROD_EXECUTION_NOT_RUN")
        self.assertEqual(report["gap"]["id"], GROUP_CONTEXT_PREPROD_GAP_ID)
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(len(report["assertions"]), 18)
        self.assertTrue(all(row["status"] == STATUS_NOT_RUN
                            for row in report["assertions"]))

    def test_bad_owner_metadata_fails_and_cascades_to_later_rows(self):
        observations = canonical_replay_observations()
        observations["group_context.list.entries"] = type(
            observations["group_context.list.entries"])(
                200, {"contexts": [{"name": "cli_e2e_context_density", "permission": "RO"}]})
        report = run_group_context_density(
            profile=local_profile(), transport=ReplayTransport(observations))
        by_id = {row["obligation_id"]: row for row in report["assertions"]}
        self.assertEqual(report["status"], STATUS_FAIL)
        self.assertEqual(by_id["GROUP_CONTEXT_DENSITY.F07"]["status"], STATUS_FAIL)
        self.assertEqual(by_id["GROUP_CONTEXT_DENSITY.F08"]["status"], STATUS_NOT_RUN)
        self.assertEqual(by_id["GROUP_CONTEXT_DENSITY.F09"]["status"], STATUS_NOT_RUN)
        self.assertEqual(by_id["GROUP_CONTEXT_DENSITY.F08"]["error_type"],
                         "BLOCKED_AFTER_ASSERTION_FAILURE")

    def test_missing_observation_is_fail_evidence_not_pass(self):
        report = run_group_context_density(
            profile=local_profile(), transport=ReplayTransport({}))
        self.assertEqual(report["status"], STATUS_FAIL)
        self.assertEqual(report["assertions"][0]["status"], STATUS_FAIL)
        self.assertEqual(report["assertions"][0]["error_type"], "KeyError")
        self.assertTrue(all(row["status"] == STATUS_NOT_RUN
                            for row in report["assertions"][1:]))


class TestGroupContextAdmission(unittest.TestCase):
    def test_profiles_admit_local_and_preprod_shapes(self):
        validate_density_profile(local_profile())
        validate_density_profile(preprod_profile())

    def test_profile_rejects_literal_credentials_and_unknown_operations(self):
        bad = local_profile()
        bad["auth"]["header_env"]["Cookie"] = "BAD_ENV_REFERENCE"
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = local_profile()
        bad["operation_bindings"].pop("group_context.cleanup")
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = local_profile()
        bad["secrets"] = {}
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)


class TestGroupContextIntegrationDiscipline(unittest.TestCase):
    def test_describe_only_proposal_reuses_existing_dual_envelope(self):
        proposal = registration_proposal()
        self.assertEqual(proposal["status"], "DESCRIBE_ONLY")
        self.assertEqual(proposal["token"], INTEGRATION_TOKEN)
        self.assertEqual(proposal["executor_row"], 17)
        self.assertEqual(proposal["envelope_command"], "dual-group-context-run")
        self.assertFalse(proposal["new_registration_command"])
        self.assertEqual(proposal["parsed_obligations"], 18)
        self.assertEqual(proposal["residual_count_gap"], 0)
        self.assertIn("executor-migration.json", proposal["red_lines"])
        self.assertIn("no new command", proposal["notes"])

    def test_reporting_contains_the_requested_mainline_fragment(self):
        notes = proposed_executor_migration_notes()
        self.assertIn(f"DESCRIBE_ONLY token={INTEGRATION_TOKEN}", notes)
        self.assertIn("no new registration command", " ".join(notes))
        self.assertIn("row #17 notes only", " ".join(notes))
        self.assertIn("parsed=18", " ".join(notes))
        self.assertIn("residual_count_gap=0", " ".join(notes))
        report = run_group_context_density(profile=local_profile())
        table = md_table(report)
        self.assertEqual(sum(line.startswith("| GROUP_CONTEXT_DENSITY")
                             for line in table.splitlines()), 18)

    def test_package_has_no_network_process_env_or_credential_value_literals(self):
        text = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py"))
        for forbidden in ("urllib", "socket", "subprocess", "os.getenv", "os.environ",
                          "Bearer ", "Cookie: ", "-----BEGIN"):
            self.assertNotIn(forbidden, text)
        self.assertIn("BCS_E2E_HUMAN_COOKIE", text)
        self.assertIn("BCS_E2E_DRIVER_TOKEN", text)
        self.assertNotIn("token-value", text)

    def test_no_credential_value_literals(self):
        patterns = (
            re.compile(r"Bearer\\s+[A-Za-z0-9._-]{8,}"),
            re.compile(r"sk-[A-Za-z0-9]{16,}"),
            re.compile(r"(?i)(cookie|token|secret)\\s*[:=]\\s*['\"][^'\"]{12,}['\"]"),
        )
        for path in sorted(PACKAGE.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                self.assertIsNone(pattern.search(text), f"{path.name} tripped {pattern.pattern}")

    def test_line_budget_and_subpackage_shape(self):
        self.assertEqual({path.stem for path in PACKAGE.glob("*.py")}, {
            "__init__", "contract", "defect", "profile", "reporting",
            "registration_proposal", "run", "transport",
        })
        for path in PACKAGE.glob("*.py"):
            self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 1000)
        self.assertLessEqual(len(Path(__file__).read_text(encoding="utf-8").splitlines()), 1000)

    def test_family_accounting_is_mechanical(self):
        self.assertEqual(family_accounting(), {
            "parsed": 18, "parents": 9, "field_atomic": 9,
            "residual_count_gap": 0,
        })


if __name__ == "__main__":
    unittest.main()
