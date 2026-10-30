"""Offline tests for the Workspace field dual-environment case runner.

Every test is socket-free: the local leg replays the frozen 63-atom oracle
through the owned fake transport, and the preprod leg never reaches the
admission seam that would open a socket.  The suite covers the local PASS
receipt triple with digest-only profile persistence, the named preprod
BLOCKED_INVENTORIED gap receipt, bounded profile admission (missing/extra
keys, illegal tier, auth environment-name references only), the fresh private
state directory gates, confirm/write rc semantics, the 43 = 17 + 26
obligation accounting, and the 63-atom oracle-only reference invariant.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.case_command_registration import CASE_COMMANDS
from bcs_e2e.group_workspace_density import load_group_workspace_density_contract
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.run_state import RunStateError
from bcs_e2e.workspace_field import dual_case_run as runner
from bcs_e2e.workspace_field.dual_case_run import (
    BASELINE_COUNT,
    BINDING_FILE,
    CASE_ID,
    DUAL_WORKSPACE_FIELD_COMMAND,
    FIELD_COUNT,
    GAP_FILE,
    LOCAL_WORKSPACE_FIELD_ASSERTION_FAILURE,
    LOCAL_WORKSPACE_FIELD_FUSE_ERROR_BRANCH_NOT_EXERCISED,
    LOCAL_WORKSPACE_FIELD_UNACKNOWLEDGED_NOT_RUN,
    OBLIGATION_COUNT,
    PREPROD_GROUP_WORKSPACE_FIELD_FACES_NOT_INVENTORIED,
    PROFILE_DIGEST_FILE,
    RAW_PROFILE_FILE,
    REPORT_CONTRACT,
    REPORT_FILE,
    SCHEMA_VERSION,
    dual_workspace_field_case_run,
    dual_workspace_field_command_handler,
)
from bcs_e2e.workspace_field.group_workspace_field_obligations import (
    FakeWorkspaceTransport,
    load_workspace_field_contract,
    project_workspace_obligations,
    validate_workspace_field_contract,
)
from bcs_e2e.workspace_field.profile import (
    ALLOWED_ENV_REFERENCES,
    admit_workspace_field_profile,
    default_workspace_field_profile,
    load_workspace_field_profile,
)

LOCAL_RUN_ID = "20261031-workspace-field-local"
ATOM_COUNT_EXPECTED = 63
FUSE_OBLIGATION_ID = "WORKSPACE.cli.fuse.error-payload-shape"
PREPROD_RUN_ID = "20261031-workspace-field-preprod"
RECEIPT_FILES = (BINDING_FILE, REPORT_FILE, GAP_FILE, PROFILE_DIGEST_FILE)


def _projection_with_obligations() -> tuple[dict, dict, dict]:
    profile = default_workspace_field_profile(tier="local")
    contract = validate_workspace_field_contract(load_workspace_field_contract())
    receipt = FakeWorkspaceTransport(profile).execute()
    projection = project_workspace_obligations(contract, receipt["density"], profile=profile)
    return contract, receipt["density"], projection


class WorkspaceFieldDualCaseRunTests(unittest.TestCase):
    """Local PASS, preprod BLOCKED_INVENTORIED, and the receipt triple."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.local_profile_path = self.root / "workspace-field-local.json"
        self.local_profile_path.write_text(
            json.dumps(default_workspace_field_profile(tier="local")), encoding="utf-8")
        self.preprod_profile_path = self.root / "workspace-field-preprod.json"
        self.preprod_profile_path.write_text(
            json.dumps(default_workspace_field_profile(tier="preprod")), encoding="utf-8")

    def _local_run(self, run_id: str = LOCAL_RUN_ID, state: Path | None = None):
        state = state or self.root / "state-local"
        profile = default_workspace_field_profile(tier="local")
        path = self.root / "local-template.json"
        path.write_text(json.dumps(profile), encoding="utf-8")
        report = dual_workspace_field_case_run(
            profile_path=path, state_dir=state, run_id=run_id,
            allow_write=True, confirm_run_id=run_id)
        return report, state

    def _preprod_run(self, run_id: str = PREPROD_RUN_ID):
        state = self.root / "state-preprod"
        report = dual_workspace_field_case_run(
            profile_path=self.preprod_profile_path, state_dir=state, run_id=run_id,
            allow_write=True, confirm_run_id=run_id)
        return report, state

    def test_local_pass_writes_receipt_triple_and_persists_profile_digest_only(self):
        with patch("socket.socket", side_effect=AssertionError("local leg opened a socket")):
            report, state = self._local_run()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], CASE_ID)
        self.assertEqual(report["contract"], REPORT_CONTRACT)
        self.assertEqual(report["schema_version"], SCHEMA_VERSION)
        self.assertEqual(report["parent_id"], "story_user_operates_group_workspace")
        self.assertEqual(report["run_id"], LOCAL_RUN_ID)
        written = sorted(path.name for path in state.iterdir())
        self.assertEqual(written, sorted(RECEIPT_FILES))
        self.assertFalse((state / RAW_PROFILE_FILE).exists())
        binding = json.loads((state / BINDING_FILE).read_text(encoding="utf-8"))
        self.assertEqual(binding["contract"], REPORT_CONTRACT)
        self.assertEqual(binding["case_id"], CASE_ID)
        self.assertEqual(binding["run_id"], LOCAL_RUN_ID)
        self.assertEqual(binding["profile_digest"], fingerprint(
            json.loads(self.local_profile_path.read_text(encoding="utf-8"))))
        self.assertTrue(binding["executor_digest"])
        self.assertEqual(binding["credential_refs"], ["BCS_E2E_DRIVER_TOKEN"])
        digest_artifact = json.loads((state / PROFILE_DIGEST_FILE).read_text(encoding="utf-8"))
        self.assertEqual(digest_artifact["profile_digest"], binding["profile_digest"])
        self.assertFalse(digest_artifact["raw_profile_persisted"])
        self.assertEqual(digest_artifact["credential_refs"], ["BCS_E2E_DRIVER_TOKEN"])

    def test_local_pass_criterion_counts_42_pass_with_the_named_fuse_gap(self):
        report, state = self._local_run()
        execution = report["execution"]
        self.assertEqual(execution["status"], "PASS")
        self.assertEqual(execution["counts"], {"PASS": 42, "FAIL": 0, "NOT_RUN": 1})
        self.assertEqual(execution["assertion_projection"]["baseline_counts"],
                         {"PASS": 17, "FAIL": 0, "NOT_RUN": 0})
        self.assertEqual(execution["assertion_projection"]["field_counts"],
                         {"PASS": 25, "FAIL": 0, "NOT_RUN": 1})
        acknowledged = execution["assertion_projection"]["isomorphism"]
        self.assertTrue(acknowledged["verified"])
        self.assertTrue(acknowledged["oracle_only"])
        self.assertEqual(acknowledged["referenced_atom_count"], 63)
        self.assertEqual(report["documented_gaps"],
                         [LOCAL_WORKSPACE_FIELD_FUSE_ERROR_BRANCH_NOT_EXERCISED])
        gap_descriptor = report["gap_descriptors"][0]
        self.assertEqual(gap_descriptor["gap_id"], LOCAL_WORKSPACE_FIELD_FUSE_ERROR_BRANCH_NOT_EXERCISED)
        self.assertEqual(gap_descriptor["status"], "NOT_RUN")
        self.assertEqual(gap_descriptor["obligation_ids"],
                         ["WORKSPACE.cli.fuse.error-payload-shape"])
        self.assertEqual(gap_descriptor["network_requests"], 0)
        self.assertFalse(gap_descriptor["grants_release_credit"])
        self.assertEqual(report["network_requests"], 0)
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertFalse(report["raw_profile_persisted"])
        gaps = json.loads((state / GAP_FILE).read_text(encoding="utf-8"))
        self.assertEqual(gaps["documented_gaps"], [LOCAL_WORKSPACE_FIELD_FUSE_ERROR_BRANCH_NOT_EXERCISED])
        self.assertEqual(gaps["counts"], {"PASS": 42, "FAIL": 0, "NOT_RUN": 1})
        self.assertEqual(gaps["not_run_obligation_count"], 1)

    def test_local_execution_opens_no_socket(self):
        with patch("socket.socket", side_effect=AssertionError("offline runner opened a socket")):
            report, _ = self._local_run()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["execution"]["result_evidence"]["atom_counts"],
                         {"PASS": 63, "FAIL": 0, "NOT_RUN": 0})
        self.assertEqual(report["execution"]["result_evidence"]["transport"], "fake-offline")

    def test_local_case_run_fail_closes_on_result_assertion_divergence(self):
        real_projector = runner.project_workspace_obligations

        def drifted(contract, density, *, profile):
            projection = real_projector(contract, density, profile=profile)
            projection["obligations"][3]["status"] = "FAIL"
            return projection

        state = self.root / "state-drift"
        with patch.object(runner, "project_workspace_obligations", drifted):
            with self.assertRaises(RunStateError):
                dual_workspace_field_case_run(
                    profile_path=self.local_profile_path, state_dir=state,
                    run_id=LOCAL_RUN_ID + "-drift", allow_write=True,
                    confirm_run_id=LOCAL_RUN_ID + "-drift")
        # Only the admission artifacts exist; no report or gap receipt is
        # persisted from a projection that lost result/assertion isomorphism.
        self.assertTrue((state / BINDING_FILE).is_file())
        self.assertFalse((state / REPORT_FILE).exists())
        self.assertFalse((state / GAP_FILE).exists())

    def test_isomorphism_verifier_units(self):
        contract, density, projection = _projection_with_obligations()
        verified = runner.verify_result_assertion_isomorphism(contract, density, projection)
        self.assertTrue(verified["verified"])
        self.assertTrue(verified["oracle_only"])
        self.assertEqual(verified["atom_count"], 63)

        thin_density = copy.deepcopy(density)
        thin_density["requirements"] = thin_density["requirements"][:-1]
        with self.assertRaises(RunStateError):
            runner.verify_result_assertion_isomorphism(contract, thin_density, projection)

        invented = copy.deepcopy(contract)
        invented["obligations"][0]["atom_ids"] = ["WORKSPACE.invented.atom"]
        with self.assertRaises(RunStateError):
            runner.verify_result_assertion_isomorphism(invented, density, projection)

        miscounted = copy.deepcopy(projection)
        miscounted["counts"]["PASS"] = OBLIGATION_COUNT
        with self.assertRaises(RunStateError):
            runner.verify_result_assertion_isomorphism(contract, density, miscounted)

        flipped = copy.deepcopy(projection)
        flipped["obligations"][0]["status"] = "FAIL"
        flipped["counts"] = {"PASS": 41, "FAIL": 1, "NOT_RUN": 1}
        with self.assertRaises(RunStateError):
            runner.verify_result_assertion_isomorphism(contract, density, flipped)

    def test_local_verdict_units(self):
        contract, density, projection = _projection_with_obligations()
        self.assertEqual(runner._local_verdict(projection, "WORKSPACE.cli.fuse.error-payload-shape")[0], "PASS")
        failed = copy.deepcopy(projection)
        failed["obligations"][0]["status"] = "FAIL"
        failed["counts"] = {"PASS": 41, "FAIL": 1, "NOT_RUN": 1}
        status, gaps = runner._local_verdict(failed, "WORKSPACE.cli.fuse.error-payload-shape")
        self.assertEqual(status, "BLOCKED")
        self.assertEqual(gaps[0]["gap_id"], LOCAL_WORKSPACE_FIELD_ASSERTION_FAILURE)
        self.assertEqual(gaps[0]["obligation_ids"], [failed["obligations"][0]["obligation_id"]])
        unexplained = copy.deepcopy(projection)
        unexplained["obligations"][1]["status"] = "NOT_RUN"
        unexplained["counts"] = {"PASS": 41, "FAIL": 0, "NOT_RUN": 2}
        status, gaps = runner._local_verdict(unexplained, "WORKSPACE.cli.fuse.error-payload-shape")
        self.assertEqual(status, "BLOCKED")
        self.assertEqual(gaps[-1]["gap_id"], LOCAL_WORKSPACE_FIELD_UNACKNOWLEDGED_NOT_RUN)
        clean = copy.deepcopy(projection)
        fuse_index = next(index for index, row in enumerate(clean["obligations"])
                          if row["obligation_id"] == FUSE_OBLIGATION_ID)
        clean["obligations"][fuse_index]["status"] = "PASS"
        clean["counts"] = {"PASS": 43, "FAIL": 0, "NOT_RUN": 0}
        status, gaps = runner._local_verdict(clean, FUSE_OBLIGATION_ID)
        self.assertEqual((status, gaps), ("PASS", []))

    def test_preprod_leg_is_blocked_inventoried_with_the_named_face_gap(self):
        with patch("socket.socket", side_effect=AssertionError("preprod leg opened a socket")):
            report, state = self._preprod_run()
        self.assertEqual(report["status"], "BLOCKED_INVENTORIED")
        self.assertEqual(report["preprod_verdict"], "NOT_RUN")
        self.assertEqual(report["execution"]["status"], "NOT_RUN")
        self.assertEqual(report["execution"]["mode"], "PREPROD_FAIL_CLOSED_NOT_INVENTORIED")
        self.assertEqual(report["execution"]["reason_class"],
                         PREPROD_GROUP_WORKSPACE_FIELD_FACES_NOT_INVENTORIED)
        self.assertEqual(report["execution"]["counts"],
                         {"PASS": 0, "FAIL": 0, "NOT_RUN": OBLIGATION_COUNT})
        self.assertEqual(report["documented_gaps"], [PREPROD_GROUP_WORKSPACE_FIELD_FACES_NOT_INVENTORIED])
        self.assertEqual(report["network_requests"], 0)
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        self.assertEqual(report["credential_refs"],
                         ["BCS_E2E_DRIVER_TOKEN", "BCS_E2E_HUMAN_COOKIE"])
        self.assertEqual(report["resource_disposition"], "NO_LIVE_RESOURCE_NO_SOCKET_NO_REMOTE_EXECUTION")
        written = sorted(path.name for path in state.iterdir())
        self.assertEqual(written, sorted(RECEIPT_FILES))

    def test_preprod_gap_receipt_persists_the_honest_not_run_receipt(self):
        report, state = self._preprod_run()
        gaps = json.loads((state / GAP_FILE).read_text(encoding="utf-8"))
        self.assertEqual(gaps["contract"], REPORT_CONTRACT + "-gap-v1")
        self.assertEqual(gaps["status"], "BLOCKED_INVENTORIED")
        self.assertEqual(gaps["preprod_verdict"], "NOT_RUN")
        self.assertEqual(gaps["counts"], {"PASS": 0, "FAIL": 0, "NOT_RUN": OBLIGATION_COUNT})
        self.assertEqual(gaps["not_run_obligation_count"], OBLIGATION_COUNT)
        self.assertEqual(gaps["obligation_count"], OBLIGATION_COUNT)
        self.assertEqual(gaps["network_requests"], 0)
        self.assertFalse(gaps["grants_release_credit"])
        self.assertEqual(gaps["documented_gaps"], [PREPROD_GROUP_WORKSPACE_FIELD_FACES_NOT_INVENTORIED])
        descriptor = gaps["gaps"][0]
        self.assertEqual(descriptor["gap_id"], PREPROD_GROUP_WORKSPACE_FIELD_FACES_NOT_INVENTORIED)
        self.assertEqual(descriptor["status"], "NOT_RUN")
        self.assertEqual(descriptor["obligation_count"], OBLIGATION_COUNT)
        obligation_ids = [row["obligation_id"]
                          for row in validate_workspace_field_contract(
                              load_workspace_field_contract())["obligations"]]
        self.assertEqual(descriptor["obligation_ids"], obligation_ids)
        self.assertEqual(descriptor["network_requests"], 0)
        self.assertFalse(descriptor["grants_release_credit"])
        obligation_receipt = report["execution"]["obligation_receipt"]
        self.assertEqual(obligation_receipt["status"], "NOT_RUN")
        self.assertEqual(obligation_receipt["counts"]["NOT_RUN"], OBLIGATION_COUNT)
        self.assertFalse(obligation_receipt["grants_release_credit"])

    def test_preprod_leg_never_forges_a_remote_pass(self):
        report, state = self._preprod_run()
        self.assertEqual(report["execution"]["completed_count"], 0)
        self.assertEqual(report["execution"]["counts"]["PASS"], 0)
        self.assertEqual(report["execution"]["obligation_receipt"]["counts"]["PASS"], 0)
        self.assertIsNone(report["execution"]["assertion_projection"])
        self.assertIsNone(report["execution"]["result_evidence"])
        gaps = json.loads((state / GAP_FILE).read_text(encoding="utf-8"))
        self.assertEqual(gaps["counts"]["PASS"], 0)
        self.assertEqual(gaps["gaps"][0]["status"], "NOT_RUN")
        self.assertEqual(gaps["gaps"][0]["obligation_count"], OBLIGATION_COUNT)
        raw = self.preprod_profile_path.read_bytes()
        for path in state.iterdir():
            self.assertNotIn(raw, path.read_bytes())
            self.assertNotIn(b'"auth"', path.read_bytes())

    def test_handler_rc_semantics_local_pass_preprod_blocked_and_mismatch(self):
        def handler(profile: Path, state: Path, run_id: str, confirm: str) -> tuple[int, str]:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                rc = dual_workspace_field_command_handler(Namespace(
                    profile=str(profile), state_dir=str(state), run_id=run_id,
                    allow_write=True, confirm_run_id=confirm))
            return rc, buffer.getvalue()

        rc, _ = handler(self.local_profile_path, self.root / "state-h-local", LOCAL_RUN_ID, LOCAL_RUN_ID)
        self.assertEqual(rc, 0)
        rc, _ = handler(self.preprod_profile_path, self.root / "state-h-pre", PREPROD_RUN_ID, PREPROD_RUN_ID)
        self.assertEqual(rc, 2)
        rc, printed = handler(self.local_profile_path, self.root / "state-h-mm", LOCAL_RUN_ID, "mismatch")
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(printed)["status"], "BLOCKED")


class WorkspaceFieldProfileAdmissionTests(unittest.TestCase):
    """Bounded key sets, tier-strict shapes and env-name-only auth."""

    def _admit(self, payload: dict) -> None:
        directory = tempfile.TemporaryDirectory(prefix="wf-profile-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "profile.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        load_workspace_field_profile(path)

    def test_profile_missing_keys_fail_admission(self):
        for key in ("contract", "environment", "executor", "entrypoint", "auth", "remote_validation"):
            payload = default_workspace_field_profile(tier="local")
            del payload[key]
            with self.assertRaises(ConfigError):
                admit_workspace_field_profile(payload)

    def test_profile_extra_keys_fail_admission(self):
        payload = default_workspace_field_profile(tier="local")
        payload["secrets"] = {"driver_token": "literal-secret"}
        with self.assertRaises(ConfigError):
            admit_workspace_field_profile(payload)
        payload = default_workspace_field_profile(tier="local")
        payload["observer"] = {"binding": "extra"}
        with self.assertRaises(ConfigError):
            admit_workspace_field_profile(payload)
        with self.assertRaises(ConfigError):
            self._admit(payload)

    def test_profile_illegal_tier_and_tier_shapes_fail_admission(self):
        staged = default_workspace_field_profile(tier="local")
        staged["environment"]["tier"] = "staging"
        with self.assertRaises(ConfigError):
            admit_workspace_field_profile(staged)
        local_preprod_auth = default_workspace_field_profile(tier="local")
        local_preprod_auth["auth"] = default_workspace_field_profile(tier="preprod")["auth"]
        with self.assertRaises(ConfigError):
            admit_workspace_field_profile(local_preprod_auth)
        preprod_local_auth = default_workspace_field_profile(tier="preprod")
        preprod_local_auth["auth"] = default_workspace_field_profile(tier="local")["auth"]
        with self.assertRaises(ConfigError):
            admit_workspace_field_profile(preprod_local_auth)
        drifted_id = default_workspace_field_profile(tier="local")
        drifted_id["environment"]["id"] = "workspace-field-preprod"
        with self.assertRaises(ConfigError):
            admit_workspace_field_profile(drifted_id)
        gateway_local = default_workspace_field_profile(tier="local")
        gateway_local["entrypoint"]["kind"] = "gateway"
        with self.assertRaises(ConfigError):
            admit_workspace_field_profile(gateway_local)
        drifted_executor = default_workspace_field_profile(tier="preprod")
        drifted_executor["executor"]["transport"] = "http-live"
        with self.assertRaises(ConfigError):
            admit_workspace_field_profile(drifted_executor)
        claimed = default_workspace_field_profile(tier="local")
        claimed["remote_validation"] = "PASS"
        with self.assertRaises(ConfigError):
            admit_workspace_field_profile(claimed)

    def test_profile_auth_admits_environment_names_only(self):
        for tier, env_name in (("local", "BCS_E2E_DRIVER_TOKEN"),
                               ("preprod", "BCS_E2E_HUMAN_COOKIE"),
                               ("preprod", "BCS_E2E_DRIVER_TOKEN")):
            payload = default_workspace_field_profile(tier=tier)
            if tier == "local":
                payload["auth"]["token_env"] = env_name
            else:
                entry = (payload["auth"]["profiles"][0]
                         if env_name == "BCS_E2E_HUMAN_COOKIE" else payload["auth"]["profiles"][1])
                key = "cookie_env" if "cookie_env" in entry else "token_env"
                entry[key] = env_name
            self.assertEqual(admit_workspace_field_profile(payload)["environment"]["tier"], tier)

        bad_refs = ["bcs_e2e_driver_token", "BCS_E2E_UNKNOWN_TOKEN", "driver-token-literal",
                    "BCS_E2E_DRIVER_TOKEN_VALUE"]
        for bad in bad_refs:
            payload = default_workspace_field_profile(tier="local")
            payload["auth"]["token_env"] = bad
            with self.assertRaises(ConfigError):
                admit_workspace_field_profile(payload)

        inline = default_workspace_field_profile(tier="preprod")
        inline["auth"]["profiles"][0]["cookie_env"] = "session-cookie-value-xyz"
        with self.assertRaises(ConfigError):
            admit_workspace_field_profile(inline)

        missing_bot = default_workspace_field_profile(tier="preprod")
        missing_bot["auth"]["profiles"] = missing_bot["auth"]["profiles"][:1]
        with self.assertRaises(ConfigError):
            admit_workspace_field_profile(missing_bot)

        duplicate_kind = default_workspace_field_profile(tier="preprod")
        duplicate_kind["auth"]["profiles"] = [
            dict(duplicate_kind["auth"]["profiles"][0]),
            dict(duplicate_kind["auth"]["profiles"][0])]
        with self.assertRaises(ConfigError):
            admit_workspace_field_profile(duplicate_kind)

        third_entry = default_workspace_field_profile(tier="preprod")
        third_entry["auth"]["profiles"] = third_entry["auth"]["profiles"] + [
            {"kind": "owned_bot", "token_env": "BCS_E2E_DRIVER_TOKEN"}]
        with self.assertRaises(ConfigError):
            admit_workspace_field_profile(third_entry)

        self.assertEqual(sorted(ALLOWED_ENV_REFERENCES),
                         ["BCS_E2E_DRIVER_TOKEN", "BCS_E2E_HUMAN_COOKIE"])


class WorkspaceFieldStateDirGateTests(unittest.TestCase):
    """Fresh, absolute, symlink-free private state directories only."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.profile_path = self.root / "profile.json"
        self.profile_path.write_text(
            json.dumps(default_workspace_field_profile(tier="local")), encoding="utf-8")

    def _attempt(self, state_dir):
        return dual_workspace_field_case_run(
            profile_path=self.profile_path, state_dir=state_dir,
            run_id=LOCAL_RUN_ID, allow_write=True, confirm_run_id=LOCAL_RUN_ID)

    def test_existing_state_dir_is_rejected_without_writes(self):
        existing = self.root / "state-exists"
        existing.mkdir()
        with self.assertRaises(ConfigError):
            self._attempt(existing)
        self.assertEqual(list(existing.iterdir()), [])

    def test_relative_state_dir_is_rejected(self):
        relative = Path("workspace-field-relative-state")
        with self.assertRaises(ConfigError):
            self._attempt(relative)
        self.assertFalse(relative.exists())
        self._cleanup(relative)

    def test_symlinked_state_dir_is_rejected(self):
        target = self.root / "state-target"
        target.mkdir()
        link = self.root / "state-link"
        os.symlink(str(target), link)
        with self.assertRaises(ConfigError):
            self._attempt(link)
        self.assertEqual(list(target.iterdir()), [])

    def test_missing_parent_dir_is_rejected(self):
        nested = self.root / "absent-parent" / "state"
        with self.assertRaises(ConfigError):
            self._attempt(nested)
        self.assertFalse((self.root / "absent-parent").exists())

    def _cleanup(self, path: Path) -> None:
        if path.exists():
            path.rmdir()


class WorkspaceFieldWriteConfirmGateTests(unittest.TestCase):
    """Explicit write permission and exact Run confirmation gate first."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.profile_path = self.root / "profile.json"
        self.profile_path.write_text(
            json.dumps(default_workspace_field_profile(tier="local")), encoding="utf-8")

    def test_allow_write_and_confirm_mismatch_refuse_before_any_write(self):
        state = self.root / "state-gated"
        with self.assertRaises(ConfigError):
            dual_workspace_field_case_run(
                profile_path=self.profile_path, state_dir=state, run_id=LOCAL_RUN_ID,
                allow_write=False, confirm_run_id=LOCAL_RUN_ID)
        with self.assertRaises(ConfigError):
            dual_workspace_field_case_run(
                profile_path=self.profile_path, state_dir=state, run_id=LOCAL_RUN_ID,
                allow_write=True, confirm_run_id="20261031-other")
        self.assertFalse(state.exists())

    def test_invalid_run_id_is_rejected(self):
        state = self.root / "state-id"
        with self.assertRaises(ConfigError):
            dual_workspace_field_case_run(
                profile_path=self.profile_path, state_dir=state,
                run_id="bad run id with spaces", allow_write=True,
                confirm_run_id="bad run id with spaces")
        self.assertFalse(state.exists())


class WorkspaceFieldObligationOracleTests(unittest.TestCase):
    """43 = 17 + 26 accounting and 63 atoms referencing only the oracle."""

    def test_obligation_count_is_43_equals_17_baseline_plus_26_field(self):
        contract = validate_workspace_field_contract(load_workspace_field_contract())
        self.assertEqual(contract["obligation_counts"],
                         {"baseline": BASELINE_COUNT, "field": FIELD_COUNT, "total": OBLIGATION_COUNT})
        self.assertEqual((BASELINE_COUNT, FIELD_COUNT, OBLIGATION_COUNT), (17, 26, 43))
        rows = contract["obligations"]
        self.assertEqual(len(rows), OBLIGATION_COUNT)
        self.assertEqual(sum(1 for row in rows if row["kind"] == "COARSE"), BASELINE_COUNT)
        self.assertEqual(sum(1 for row in rows if row["kind"] == "FIELD"), FIELD_COUNT)
        report, _ = self._local_report()
        self.assertEqual(report["case_scope"]["obligation_count"], OBLIGATION_COUNT)
        self.assertEqual(report["case_scope"]["baseline_obligation_count"], BASELINE_COUNT)
        self.assertEqual(report["case_scope"]["field_obligation_count"], FIELD_COUNT)

    def test_sixty_three_atoms_reference_only_the_oracle(self):
        contract = validate_workspace_field_contract(load_workspace_field_contract())
        oracle = load_group_workspace_density_contract()
        oracle_ids = {row["id"] for row in oracle["density_requirements"]}
        self.assertEqual(len(oracle_ids), 63)
        referenced = {atom for row in contract["obligations"] for atom in row["atom_ids"]}
        self.assertEqual(len(referenced), ATOM_COUNT_EXPECTED)
        self.assertEqual(referenced, oracle_ids)
        rollup = contract["baseline_atom_rollup"]
        self.assertEqual(len(rollup), BASELINE_COUNT)
        partitioned = {atom for atoms in rollup.values() for atom in atoms}
        self.assertEqual(partitioned, oracle_ids)
        for row in contract["obligations"]:
            if row["kind"] == "FIELD":
                self.assertTrue(set(row["atom_ids"]) <= oracle_ids)
        report, _ = self._local_report()
        isomorphism = report["execution"]["assertion_projection"]["isomorphism"]
        self.assertTrue(isomorphism["verified"])
        self.assertTrue(isomorphism["oracle_only"])
        self.assertEqual(len(isomorphism["oracle_atom_ids"]), 63)
        self.assertEqual(isomorphism["referenced_atom_count"], 63)

    def _local_report(self):
        root = Path(tempfile.mkdtemp(prefix="wf-oracle-"))
        self.addCleanup(lambda: _remove_tree(root))
        profile_path = root / "profile.json"
        profile_path.write_text(
            json.dumps(default_workspace_field_profile(tier="local")), encoding="utf-8")
        report = dual_workspace_field_case_run(
            profile_path=profile_path, state_dir=root / "state", run_id=LOCAL_RUN_ID,
            allow_write=True, confirm_run_id=LOCAL_RUN_ID)
        return report, root

    def test_raw_profile_bytes_and_auth_blocks_never_reach_the_state_dir(self):
        report, root = self._local_report()
        raw = (root / "profile.json").read_bytes()
        state = root / "state"
        for path in state.iterdir():
            content = path.read_bytes()
            self.assertNotIn(raw, content)
            self.assertNotIn(b'"auth"', content)
            self.assertNotIn(b"BCS_E2E_HUMAN_COOKIE=", content)
        digest_artifact = json.loads((state / PROFILE_DIGEST_FILE).read_text(encoding="utf-8"))
        self.assertEqual(digest_artifact["profile_digest"],
                         fingerprint(json.loads(raw)))
        self.assertTrue(report["profile_digest_only"])

    def test_local_receipts_are_deterministic_across_runs(self):
        first, first_root = self._local_report()
        second_root = Path(tempfile.mkdtemp(prefix="wf-oracle-2-"))
        self.addCleanup(lambda: _remove_tree(second_root))
        profile_path = second_root / "profile.json"
        profile_path.write_bytes((first_root / "profile.json").read_bytes())
        second = dual_workspace_field_case_run(
            profile_path=profile_path, state_dir=second_root / "state",
            run_id=LOCAL_RUN_ID, allow_write=True, confirm_run_id=LOCAL_RUN_ID)
        self.assertEqual(first["profile_digest"], second["profile_digest"])
        self.assertEqual(first["executor_digest"], second["executor_digest"])
        self.assertEqual(first["execution"], second["execution"])
        self.assertEqual(first["case_scope"], second["case_scope"])
        first_binding = json.loads((first_root / "state" / BINDING_FILE).read_text(encoding="utf-8"))
        second_binding = json.loads((second_root / "state" / BINDING_FILE).read_text(encoding="utf-8"))
        self.assertEqual(
            {key: value for key, value in first_binding.items() if key != "state_dir"},
            {key: value for key, value in second_binding.items() if key != "state_dir"})


class WorkspaceFieldWrapperSeamTests(unittest.TestCase):
    """The wrapper constant and handler exist and are mainline-registered."""

    def test_wrapper_constant_and_handler_are_registered(self):
        self.assertEqual(DUAL_WORKSPACE_FIELD_COMMAND, "dual-workspace-field-run")
        self.assertIn(DUAL_WORKSPACE_FIELD_COMMAND, CASE_COMMANDS)
        self.assertIn("dual-workspace-field-run", CASE_COMMANDS)
        self.assertTrue(callable(dual_workspace_field_command_handler))
        from bcs_e2e.workspace_field import (  # noqa: F401  package re-export
            CASE_ID as exported_case_id,
            DUAL_WORKSPACE_FIELD_COMMAND as exported_command,
            dual_workspace_field_case_run as exported_run,
            dual_workspace_field_command_handler as exported_handler,
            default_workspace_field_profile as exported_template,
        )
        self.assertEqual(exported_case_id, CASE_ID)
        self.assertEqual(exported_command, DUAL_WORKSPACE_FIELD_COMMAND)
        self.assertTrue(callable(exported_run))
        self.assertTrue(callable(exported_handler))
        self.assertTrue(callable(exported_template))



def _remove_tree(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
