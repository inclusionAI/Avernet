"""Admission, execution, and CLI tests for the dual structured density case.

Preprod gate tests use bcs-direct/gateway dictionary profiles and fail closed
before any transport is constructed, proving the admission contract without a
socket.  Owned-local tests drive the real 17-request journey against the
loopback station and assert the receipt triple, the shared-origin default, the
exact 43/43 projection, the strict split of separate leg binding faces, and
the honest BLOCKED receipt with its best-effort delete.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.case_command_registration import CASE_COMMANDS
from bcs_e2e.cli import main as cli_main
from bcs_e2e.profile import ConfigError, fingerprint
from bcs_e2e.route_bindings import _EDGE_CASES
from bcs_e2e.structured_density.dual_case_run import (
    BLOCKED_RESOURCE_DISPOSITION, CASE_ID, REPORT_CONTRACT,
    SUCCESS_RESOURCE_DISPOSITION, dual_structured_density_case_run,
)
from bcs_e2e.structured_density.profile import (
    DENSITY_STRUCTURED_OPERATIONS, DENSITY_TEMPLATES_OPERATIONS,
)
from tests.test_structured_density_live_transport import (
    GROUP_ID, RUN_ID, _loopback_env, loopback_density_profile,
)

GATE_DRIVER = "20261031_gatedriver:39464"
GATE_MEMBER = "20261031_gatemember:39464"
GATE_HUMAN = "39464"
RUN_ID_GATE = "20261031-density-gate"
LOCAL_RUN_ID = "20261031-density-loopback"
DENSITY_COMMAND = "dual-structured-density-run"


def _gate_binding(operation: str, api_face: str, response: str) -> dict:
    method, path, auth = _EDGE_CASES[operation]
    binding = {"method": method, "path": path, "api_face": api_face, "auth": auth,
               "response": response, "traceparent": "disabled"}
    if auth == "authenticated_bot":
        binding["caller_actor_id"] = GATE_DRIVER
    return binding


def _gate_base(entrypoint: dict, environment_id: str) -> dict:
    return {
        "schema_version": 1,
        "environment": {"tier": "preprod", "id": environment_id, "lifecycle": "remote-attach",
                        "deployment": "preprod", "provider": "station"},
        "entrypoint": entrypoint,
        "auth": {"header_env": {"Cookie": "BCS_E2E_HUMAN_COOKIE",
                                "Authorization": "BCS_E2E_DRIVER_TOKEN"}},
        "human": {"user_id": GATE_HUMAN},
        "actors": {"driver": {"bot_id": GATE_DRIVER, "owner_id": GATE_HUMAN, "dedicated": True},
                   "member": {"bot_id": GATE_MEMBER, "owner_id": GATE_HUMAN, "dedicated": True}},
        "policy": {"fixture_pool": "structured-density-gate-pool"},
        "edge_case_bindings": {},
    }


def gate_structured_profile(bindings=None, member: str = GATE_MEMBER) -> dict:
    operations = DENSITY_STRUCTURED_OPERATIONS if bindings is None else bindings
    profile = _gate_base({"kind": "bcs-direct", "origin": "https://bcs-pre.alipay.com",
                          "api_face": "public-v1", "manifest_face": "legacy"},
                         "bcs-pre-density-gate")
    profile["actors"]["member"]["bot_id"] = member
    profile["edge_case_bindings"] = {
        operation: _gate_binding(operation, "internal-v1", "plain-json")
        for operation in operations}
    return profile


def gate_templates_profile(bindings=None, member: str = GATE_MEMBER) -> dict:
    operations = DENSITY_TEMPLATES_OPERATIONS if bindings is None else bindings
    profile = _gate_base({"kind": "gateway", "origin": "https://teamclawgw-pre.alipay.com",
                          "api_face": "public-v1", "manifest_face": "internal-v1"},
                         "teamclawgw-pre-density-gate")
    profile["browser_origin"] = "https://teamclaw-pre.alipay.com"
    profile["actors"]["member"]["bot_id"] = member
    profile["edge_case_bindings"] = {
        operation: _gate_binding(operation, "public-v1", "public-envelope")
        for operation in operations}
    return profile


class DualStructuredDensityGateTests(unittest.TestCase):
    """Every preprod gate fails closed before any socket is opened."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.structured_path = self.root / "gate-structured.json"
        self.templates_path = self.root / "gate-templates.json"
        self.structured_path.write_text(json.dumps(gate_structured_profile()), encoding="utf-8")
        self.templates_path.write_text(json.dumps(gate_templates_profile()), encoding="utf-8")
        self.state = self.root / "gate-state"

    def _run(self, **kwargs):
        dual_structured_density_case_run(
            profile_path=self.structured_path, templates_profile_path=self.templates_path,
            state_dir=self.state, run_id=RUN_ID_GATE, allow_write=True,
            confirm_run_id=RUN_ID_GATE, **kwargs)

    def test_write_permission_and_exact_confirmation_are_gated(self):
        with self.assertRaises(ConfigError):
            dual_structured_density_case_run(
                profile_path=self.structured_path, state_dir=self.state, run_id=RUN_ID_GATE,
                allow_write=False, confirm_run_id=RUN_ID_GATE)
        with patch.dict(os.environ, _loopback_env(), clear=True):
            with self.assertRaises(ConfigError):
                dual_structured_density_case_run(
                    profile_path=self.structured_path, state_dir=self.state, run_id=RUN_ID_GATE,
                    allow_write=True, confirm_run_id="not-the-run-id",
                    templates_profile_path=self.templates_path)

    def test_preprod_structured_face_binds_exactly_its_14_operations(self):
        for wrong in (set(DENSITY_STRUCTURED_OPERATIONS) - {"structured.group.delete"},
                      set(DENSITY_STRUCTURED_OPERATIONS) | {"structured_templates.list"}):
            path = self.root / "gate-structured-wrong.json"
            path.write_text(json.dumps(gate_structured_profile(bindings=wrong)), encoding="utf-8")
            with patch.dict(os.environ, _loopback_env(), clear=True):
                with self.assertRaises(ConfigError):
                    dual_structured_density_case_run(
                        profile_path=path, templates_profile_path=self.templates_path,
                        state_dir=self.root / "never", run_id=RUN_ID_GATE,
                        allow_write=True, confirm_run_id=RUN_ID_GATE)

    def test_preprod_requires_the_explicit_gateway_templates_profile(self):
        with patch.dict(os.environ, _loopback_env(), clear=True):
            with self.assertRaises(ConfigError):
                dual_structured_density_case_run(
                    profile_path=self.structured_path, state_dir=self.root / "never",
                    run_id=RUN_ID_GATE, allow_write=True, confirm_run_id=RUN_ID_GATE)

    def test_preprod_templates_face_binds_exactly_its_2_operations(self):
        for wrong in (set(DENSITY_TEMPLATES_OPERATIONS) - {"structured_templates.detail"},
                      set(DENSITY_TEMPLATES_OPERATIONS) | {"structured.group.read"}):
            path = self.root / "gate-templates-wrong.json"
            path.write_text(json.dumps(gate_templates_profile(bindings=wrong)), encoding="utf-8")
            with patch.dict(os.environ, _loopback_env(), clear=True):
                with self.assertRaises(ConfigError):
                    dual_structured_density_case_run(
                        profile_path=self.structured_path, templates_profile_path=path,
                        state_dir=self.root / "never", run_id=RUN_ID_GATE,
                        allow_write=True, confirm_run_id=RUN_ID_GATE)

    def test_preprod_profiles_must_declare_identical_identities(self):
        templates = gate_templates_profile(member="20261031_othermember:39464")
        path = self.root / "gate-templates-other-member.json"
        path.write_text(json.dumps(templates), encoding="utf-8")
        with patch.dict(os.environ, _loopback_env(), clear=True):
            with self.assertRaises(ConfigError):
                dual_structured_density_case_run(
                    profile_path=self.structured_path, templates_profile_path=path,
                    state_dir=self.root / "never", run_id=RUN_ID_GATE,
                    allow_write=True, confirm_run_id=RUN_ID_GATE)

    def test_shared_local_profile_must_also_bind_the_catalog_operations(self):
        shared = loopback_density_profile(21099)
        shared["edge_case_bindings"] = {
            operation: binding for operation, binding in shared["edge_case_bindings"].items()
            if operation in DENSITY_STRUCTURED_OPERATIONS}
        path = self.root / "local-shared-strip.json"
        path.write_text(json.dumps(shared), encoding="utf-8")
        with self.assertRaises(ConfigError):
            dual_structured_density_case_run(
                profile_path=path, state_dir=self.root / "never", run_id=LOCAL_RUN_ID,
                allow_write=True, confirm_run_id=LOCAL_RUN_ID)

    def test_local_separate_legs_bind_exactly_their_split_faces(self):
        structured = loopback_density_profile(21099)
        structured["edge_case_bindings"] = {
            operation: binding for operation, binding in structured["edge_case_bindings"].items()
            if operation in DENSITY_STRUCTURED_OPERATIONS}
        for wrong in (structured,):
            wrong["edge_case_bindings"]["structured_templates.list"] = \
                loopback_density_profile(21099)["edge_case_bindings"]["structured_templates.list"]
            path = self.root / "local-structured-extra.json"
            path.write_text(json.dumps(wrong), encoding="utf-8")
            with self.assertRaises(ConfigError):
                dual_structured_density_case_run(
                    profile_path=path, templates_profile_path=Path("/nonexistent-templates.json"),
                    state_dir=self.root / "never", run_id=LOCAL_RUN_ID,
                    allow_write=True, confirm_run_id=LOCAL_RUN_ID)


class DualStructuredDensityLoopbackTests(unittest.TestCase):
    """Owned-local Singlebox runs the whole journey twice: shared and split."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.station = _new_station()
        self.addCleanup(self.station.stop)
        self.state = self.root / "density-state"
        self.shared_path = self._write("density-shared.json",
                                       loopback_density_profile(self.station.port))

    def _write(self, name: str, payload: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _run(self, *, templates_path: Path | None = None, run_id: str = LOCAL_RUN_ID,
             state: Path | None = None):
        with patch.dict(os.environ, _loopback_env(), clear=True):
            return dual_structured_density_case_run(
                profile_path=self.shared_path, templates_profile_path=templates_path,
                state_dir=state or self.state, run_id=run_id, allow_write=True,
                confirm_run_id=run_id)

    def test_shared_local_journey_passes_the_full_receipt_triple(self):
        report = self._run()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], CASE_ID)
        self.assertEqual(report["contract"], REPORT_CONTRACT)
        self.assertEqual(report["credential_kind"], "human_cookie_and_driver_bot_token")
        self.assertTrue(report["profile_digest"])
        self.assertEqual(report["templates_profile_digest"], report["profile_digest"])
        self.assertTrue(report["executor_digest"])
        self.assertEqual(report["case_scope"]["live_journey_steps"], 17)
        self.assertEqual(len(report["case_scope"]["structured_operations"]), 14)
        self.assertEqual(len(report["case_scope"]["templates_operations"]), 2)
        self.assertTrue(report["environment"]["shared_origin"])
        self.assertEqual(report["environment"]["structured"]["entrypoint_kind"], "singlebox")
        self.assertEqual(report["environment"]["templates"]["origin"],
                         report["environment"]["structured"]["origin"])
        self.assertEqual(report["execution"]["status"], "PASS")
        self.assertEqual(report["execution"]["execution"]["completed_count"], 43)
        self.assertEqual(report["resource_disposition"], SUCCESS_RESOURCE_DISPOSITION)
        self.assertIsNone(report["cleanup_outcome"])

        binding = json.loads((self.state / "structured-density-binding.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["case_id"], CASE_ID)
        self.assertEqual(binding["contract"], REPORT_CONTRACT)
        self.assertEqual(binding["run_id"], LOCAL_RUN_ID)
        self.assertEqual(binding["profile_digest"], fingerprint(json.loads(
            self.shared_path.read_text(encoding="utf-8"))))
        self.assertEqual(binding["planned_resource_disposition"], SUCCESS_RESOURCE_DISPOSITION)
        self.assertTrue((self.state / "structured-density-profile.json").is_file())
        self.assertTrue((self.state / "structured-density-run.json").is_file())
        self.assertFalse((self.state / "structured-density-templates-profile.json").exists())

        self.assertEqual(len(report["journey"]), 17)
        self.assertEqual(report["journey"][0]["operation"], "templates.list")
        self.assertEqual(report["journey"][-1]["operation"], "group.delete")
        mutations = sorted(entry["operation"] for entry in report["journey"] if entry["mutation"])
        self.assertEqual(mutations, sorted([
            "group.create", "definition.patch", "definition.upgrade", "run.start",
            "run.human-response", "run.cancel", "run.rerun", "group.delete"]))
        self.assertEqual(len(self.station.server.hits), 17)
        self.assertEqual(self.station.count("GET", "/api/v1/collaboration/templates"), 1)
        self.assertEqual(
            self.station.count("GET", f"/api/v1/state-machine-runs/{RUN_ID}"), 2)
        self.assertEqual(self.station.count("DELETE", f"/api/v1/groups/{GROUP_ID}"), 1)

    def test_local_separate_templates_profile_splits_the_binding_faces(self):
        shared = loopback_density_profile(self.station.port)
        structured = copy.deepcopy(shared)
        structured["edge_case_bindings"] = {
            operation: binding for operation, binding in shared["edge_case_bindings"].items()
            if operation in DENSITY_STRUCTURED_OPERATIONS}
        catalog = copy.deepcopy(shared)
        catalog["edge_case_bindings"] = {
            operation: binding for operation, binding in shared["edge_case_bindings"].items()
            if operation in DENSITY_TEMPLATES_OPERATIONS}
        structured_path = self._write("density-split-structured.json", structured)
        templates_path = self._write("density-split-templates.json", catalog)
        state = self.root / "density-split-state"
        with patch.dict(os.environ, _loopback_env(), clear=True):
            report = dual_structured_density_case_run(
                profile_path=structured_path, templates_profile_path=templates_path,
                state_dir=state, run_id=LOCAL_RUN_ID + "-split", allow_write=True,
                confirm_run_id=LOCAL_RUN_ID + "-split")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["execution"]["execution"]["completed_count"], 43)
        self.assertFalse(report["environment"]["shared_origin"])
        self.assertTrue((state / "structured-density-profile.json").is_file())
        self.assertTrue((state / "structured-density-templates-profile.json").is_file())
        self.assertTrue((state / "structured-density-run.json").is_file())
        self.assertEqual(len(self.station.server.hits), 17)

    def test_failing_run_start_blocks_with_best_effort_delete(self):
        station = _new_station(fail_run_start=True)
        self.addCleanup(station.stop)
        path = self._write("density-fail.json",
                           loopback_density_profile(station.port))
        state = self.root / "density-fail-state"
        run_id = LOCAL_RUN_ID + "-fail"
        with patch.dict(os.environ, _loopback_env(), clear=True):
            report = dual_structured_density_case_run(
                profile_path=path, state_dir=state, run_id=run_id, allow_write=True,
                confirm_run_id=run_id)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["resource_disposition"], BLOCKED_RESOURCE_DISPOSITION)
        self.assertEqual(report["execution"]["status"], "FAIL")
        self.assertEqual(report["cleanup_outcome"]["status"], "ATTEMPTED")
        self.assertEqual(report["cleanup_outcome"]["http_status"], 200)
        self.assertTrue(report["cleanup_outcome"]["request_id_echoed"])
        self.assertEqual(station.count("DELETE", f"/api/v1/groups/{GROUP_ID}"), 1)
        self.assertEqual(len(report["journey"]), 7)
        self.assertEqual(report["journey"][-1]["operation"], "definition.upgrade")
        rows = report["execution"]["assertions"]
        first_fail = next(index for index, row in enumerate(rows) if row["status"] == "FAIL")
        self.assertEqual(rows[first_fail]["source"]["operation"], "run.start")
        self.assertTrue(all(row["status"] == "NOT_RUN" for row in rows[first_fail + 1:]))


class DualStructuredDensityCliTests(unittest.TestCase):
    def test_command_is_registered_and_help_exits_zero(self):
        self.assertIn(DENSITY_COMMAND, CASE_COMMANDS)
        self.assertIn("dual-structured-templates-run", CASE_COMMANDS)
        with patch.dict(os.environ, _loopback_env(), clear=True):
            for command in (DENSITY_COMMAND, "dual-structured-templates-run"):
                with self.assertRaises(SystemExit) as caught:
                    cli_main([command, "--help"])
                self.assertEqual(caught.exception.code, 0)

    def test_main_fails_closed_on_confirm_mismatch_with_rc2(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rmtree(root))
        station = _new_station()
        self.addCleanup(station.stop)
        profile = root / "density-main.json"
        profile.write_text(json.dumps(loopback_density_profile(station.port)), encoding="utf-8")
        argv = [DENSITY_COMMAND, "--profile", str(profile),
                "--templates-profile", str(profile),
                "--state-dir", str(root / "unused-state"),
                "--run-id", LOCAL_RUN_ID, "--confirm-run-id", "different",
                "--allow-write"]
        with patch.dict(os.environ, _loopback_env(), clear=True):
            self.assertEqual(cli_main(argv), 2)


def _new_station(fail_run_start: bool = False):
    from tests.test_structured_density_live_transport import DensityStation
    station = DensityStation(fail_run_start=fail_run_start)
    station.start()
    return station


def _rmtree(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
