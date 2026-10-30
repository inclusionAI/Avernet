"""Contract tests for the unified owned-local custom-collaboration case."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bcs_e2e.local_cli import LocalCliError
from bcs_e2e.local_custom_collaboration_case import (
    CASE_ID,
    DENSITY_REQUIREMENTS,
    DENSITY_SPECS,
    REQUIREMENTS,
    custom_collaboration_case_adaptability,
    custom_collaboration_case_plan,
    run_custom_collaboration_case,
)
from bcs_e2e.run_state import write


class LocalCustomCollaborationCaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state_dir = self.root / "state"
        self.state_dir.mkdir(mode=0o700)
        write(self.state_dir / "run.json", {
            "run_id": "custom-collab",
            "ledger_path": str(self.state_dir / "run.sqlite"),
        })
        self.runtime = Mock(run_id="custom-collab")
        self.binary = self.root / "bcs-cli"
        self.yaml = self.state_dir / "custom-collaboration.yaml"
        self.yaml.write_text("name: Owned-local collaboration\n")
        self.driver = "bot-driver"
        self.reviewer = "bot-reviewer"
        self.human = "human_1001"
        self.token = "owned-local-driver-token"

    def test_case_plan_covers_all_three_parent_obligations(self):
        plan = custom_collaboration_case_plan()
        self.assertEqual(plan["case_id"], CASE_ID)
        self.assertEqual(plan["legacy_id"], "story_cli_operator_creates_custom_collaboration")
        self.assertEqual(plan["requirement_ids"],
                         ["CUSTOM-COLLABORATION.validate-yaml",
                          "CUSTOM-COLLABORATION.create-persistent-group",
                          "CUSTOM-COLLABORATION.trial-one-shot-lifecycle"]
                         + list(DENSITY_REQUIREMENTS))
        self.assertEqual(plan["parent_requirement_count"], 37)
        adaptability = custom_collaboration_case_adaptability()
        self.assertEqual(adaptability["local_executor_status"], "LOCAL_FULL_PARENT_EXECUTOR")
        self.assertEqual(adaptability["remote_validation"], "NOT_RUN")
        self.assertFalse(adaptability["grants_release_credit"])
        self.assertIn("preprod", adaptability["blocker"])

    def test_success_executes_validate_trial_create_then_exact_cleanup(self):
        calls = []
        def density_atoms(*methods):
            return {f"CUSTOM-COLLABORATION.{name}": True
                    for name, method, _first, _last in DENSITY_SPECS if method in methods}
        children = {
            "validate": {"status": "LOCAL_CUSTOM_COLLABORATION_VALIDATE_PASS",
                         "density_observations": density_atoms("validate_yaml")},
            "trial": {"status": "LOCAL_CUSTOM_COLLABORATION_TRIAL_PASS",
                      "density_observations": density_atoms(
                          "trial_create_group", "trial_add_member", "trial_permission",
                          "trial_start_run", "trial_query_graph", "trial_query_pending",
                          "trial_respond_guard", "trial_messages", "cancel_trial_run",
                          "read_trial_session", "delete_trial_group")},
            "create": {
                "status": "LOCAL_CUSTOM_COLLABORATION_CREATE_GROUP_PASS",
                "group_id": "custom-participants-group",
                "session_id": "custom-participants-group:initial",
                "density_observations": density_atoms(
                    "create_persistent_group", "read_persistent_group"),
            },
        }
        def validate(*args, **kwargs):
            calls.append("validate")
            self.assertEqual(kwargs["case_id"], CASE_ID)
            return children["validate"]
        def trial(*args, **kwargs):
            calls.append("trial")
            self.assertEqual(kwargs["case_id"], CASE_ID)
            return children["trial"]
        def create(*args, **kwargs):
            calls.append("create")
            self.assertEqual(kwargs["case_id"], CASE_ID)
            return children["create"]
        def cleanup(runtime, method, path, body, token, expected_status):
            calls.append(("cleanup", runtime, method, path, body, token))
            self.assertEqual((runtime, method, body, token, expected_status),
                             (self.runtime, "DELETE", None, self.token, 200))
            return {"deleted": True, "id": "custom-participants-group"}
        with patch("bcs_e2e.local_custom_collaboration_case.validate_collaboration_yaml", side_effect=validate), \
             patch("bcs_e2e.local_custom_collaboration_case.run_custom_collaboration_trial", side_effect=trial), \
             patch("bcs_e2e.local_custom_collaboration_case.create_custom_collaboration_group", side_effect=create), \
             patch("bcs_e2e.local_custom_collaboration_case._request_trial_json", side_effect=cleanup):
            result = run_custom_collaboration_case(
                self.runtime, state_dir=self.state_dir, binary=self.binary,
                binary_sha256="a" * 64, yaml_path=self.yaml,
                driver_id=self.driver, reviewer_id=self.reviewer,
                human_id=self.human, driver_token=self.token,
            )
        self.assertEqual(calls, ["validate", "trial", "create", ("cleanup", self.runtime, "DELETE",
                         "/groups/custom-participants-group?bot_id=bot-driver", None, self.token)])
        self.assertEqual(calls[3][3], "/groups/custom-participants-group?bot_id=bot-driver")
        self.assertEqual(result["status"], "LOCAL_CUSTOM_COLLABORATION_LIFECYCLE_PASS")
        self.assertEqual(result["case_id"], CASE_ID)
        self.assertEqual(result["assertions"],
                         [{"id": name, "status": "PASS"} for name in REQUIREMENTS])
        self.assertEqual(len(result["density_observations"]), len(DENSITY_SPECS))
        self.assertTrue(all(result["density_observations"].values()))
        self.assertEqual(result["persistent_group_cleanup"], "DELETED")
        self.assertEqual(result["full_parent_coverage"], "LOCAL_PARENT_REQUIREMENTS_ONLY")
        self.assertEqual(result["remote_validation"], "NOT_RUN")
        self.assertFalse(result["grants_release_credit"])
        report = self.state_dir / "local-custom-collaboration.json"
        self.assertTrue(report.is_file())
        self.assertEqual(json.loads(report.read_text())["status"], result["status"])
        self.assertNotIn(self.token, json.dumps(result))

    def test_failed_persistent_group_cleanup_does_not_claim_parent_pass(self):
        report_path = self.state_dir / "local-custom-collaboration.json"
        with patch("bcs_e2e.local_custom_collaboration_case.validate_collaboration_yaml",
                   return_value={"status": "VALIDATE_PASS"}), \
             patch("bcs_e2e.local_custom_collaboration_case.run_custom_collaboration_trial",
                   return_value={"status": "TRIAL_PASS"}), \
             patch("bcs_e2e.local_custom_collaboration_case.create_custom_collaboration_group",
                   return_value={"status": "CREATE_PASS", "group_id": "custom-participants-group"}), \
             patch("bcs_e2e.local_custom_collaboration_case._request_trial_json",
                   return_value={"deleted": False, "id": "custom-participants-group"}), \
             self.assertRaisesRegex(LocalCliError, "persistent Group cleanup differs"):
            run_custom_collaboration_case(
                self.runtime, state_dir=self.state_dir, binary=self.binary,
                binary_sha256="a" * 64, yaml_path=self.yaml,
                driver_id=self.driver, reviewer_id=self.reviewer,
                human_id=self.human, driver_token=self.token,
            )
        self.assertFalse(report_path.exists())


if __name__ == "__main__":
    unittest.main()
