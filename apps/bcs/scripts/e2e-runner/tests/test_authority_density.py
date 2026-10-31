"""Offline density, anchor, replay, admission, and integration tests for Wave-B #18.

The roster cross-checks the landed authority dual family (read-only imports),
the replay is in-memory only, the preprod leg stays a fail-close NOT_RUN
admission projection with seven named gaps, and the package hygiene scan
mirrors the reference density family byte-for-byte.
"""
from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bcs_e2e.authority.contract import (  # noqa: E402
    BOOTSTRAP_HTTP_CALLS as FAMILY_BOOTSTRAP_HTTP_CALLS,
    BUSINESS_HTTP_CALLS as FAMILY_BUSINESS_HTTP_CALLS,
    ENVIRONMENT as FAMILY_ENVIRONMENT, HTTP_CALL_COUNT as FAMILY_HTTP_CALL_COUNT,
    PRIMARY as FAMILY_PRIMARY, REQUIREMENTS as FAMILY_REQUIREMENTS,
    STORY_CHECKS as FAMILY_STORY_CHECKS,
    STORY_SOURCE as FAMILY_SOURCE_PATH, SOURCE_REVISION as FAMILY_SOURCE_REVISION,
    SOURCE_SHA256 as FAMILY_SOURCE_SHA256,
)
from bcs_e2e.authority.dual_case_run import COMMAND as FAMILY_DUAL_COMMAND  # noqa: E402
from bcs_e2e.authority_density import (  # noqa: E402
    CALL_GROUP_COUNT, CASE_ID, CONTRACT, IDENTITY_PRESEED_OPERATIONS, INTEGRATION_TOKEN,
    PARSED_OBLIGATION_COUNT, PREPROD_COLUMN, PREPROD_GAP_IDS, PRODUCT_DEFECTS,
    RESIDUAL_COUNT_GAP, SHELL_STATUS_ASSERTIONS, SHELL_VALUE_ASSERTIONS, SOURCE_SHA256,
    STATUS_BLOCKED_INVENTORIED, STATUS_FAIL, STATUS_NOT_RUN, STATUS_PASS,
    admission_diagnosis_basis, assert_honesty_ledger, authority_density_plan,
    call_groups, canonical_replay_observations, canonical_replay_transport,
    density_accounting, defect_gap_ledger, family_accounting, gap_attribution, md_table,
    parsed_obligations, preprod_admission_gaps, proposed_executor_migration_notes,
    registration_proposal, run_authority_density, validate_density_profile,
)
from bcs_e2e.authority_density import contract as density_contract  # noqa: E402
from bcs_e2e.authority_density.contract import (  # noqa: E402
    ASSERTION_KINDS, BUSINESS_ENVELOPE_KEYS, ENVIRONMENT_POSTURE, GROUP_PREFIX,
    INITIATED_EXPIRES_AT, REFRESHED_EXPIRES_AT, SOURCE_FILE, STORY_LINE_END,
    STORY_LINE_START, TARGET_USER_ID, TRANSFER_WINDOW_MAX_RENEWAL_MS,
)
from bcs_e2e.authority_density.profile import ALLOWED_ENV_NAMES  # noqa: E402
from bcs_e2e.authority_density.run import (  # noqa: E402
    BLOCKED_AFTER_ASSERTION_FAILURE, ENVELOPE_PRECONDITION_FAILED,
    PREPROD_ADMISSION_BLOCKED,
)
from bcs_e2e.authority_density.transport import (  # noqa: E402
    Observation, ReplayTransport,
)
from bcs_e2e.run_state import RunStateError  # noqa: E402

SOURCE = ROOT.parent / "e2e-test" / "authority.sh"
PACKAGE = ROOT / "bcs_e2e" / "authority_density"

OBLIGATION_IDS = tuple(row.obligation_id for row in parsed_obligations())
IDENTITY_IDS = tuple(row.obligation_id for row in parsed_obligations()
                     if row.operation in IDENTITY_PRESEED_OPERATIONS)


def _operation_bindings() -> dict:
    bindings: dict = {}
    for row in parsed_obligations():
        bindings[row.operation] = {
            "method": row.method, "path_template": row.path_template,
            "actor": row.actor,
            "query": (dict([row.query]) if row.query else None),
        }
    return bindings


def _route_bindings() -> dict:
    bindings: dict = {}
    for operation, binding in _operation_bindings().items():
        face = "public-v1" if operation == "public.mine.read" else "internal-v1"
        bindings[operation] = {
            "method": binding["method"], "path": binding["path_template"],
            "api_face": face, "auth": "gateway-principal-jwt",
            "response": "business-envelope", "traceparent": "w3c",
            "reviewed": False,
        }
    return bindings


def local_profile() -> dict:
    return {
        "schema_version": 1,
        "contract": "authority-density-profile-v1",
        "environment": {"tier": "local", "id": "authority-density-local",
                        "lifecycle": "local-provision", "deployment": "singlebox"},
        "entrypoint": {"kind": "owned-local"},
        "auth": {"kind": "owned-local",
                 "principal_signing_key_env": "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE"},
        "authority": {"target_user_id": TARGET_USER_ID, "mine_user_id": "137"},
        "cli": {"transport": "owned-local-replay",
                "command_family": "collaboration-authority", "api_face": "public-v1"},
        "policy": {"timeout_seconds": 30, "max_response_bytes": 1048576,
                   "failure_retention_hours": 24},
        "operation_bindings": _operation_bindings(),
    }


def preprod_profile() -> dict:
    return {
        "schema_version": 1,
        "contract": "authority-density-profile-v1",
        "environment": {"tier": "preprod", "id": "authority-density-preprod",
                        "expected_bcs_env": "pre", "lifecycle": "remote-attach",
                        "deployment": "preprod"},
        "entrypoint": {"kind": "bcs-direct", "origin": "https://bcs-pre.alipay.com",
                       "api_face": "internal-and-public-v1",
                       "manifest_face": "internal-and-public-v1"},
        "human": {"user_id": "preprod-human-auth-density-1"},
        "auth": {"kind": "preprod-driver-bot-duo",
                 "header_env": {"Authorization": "BCS_E2E_DRIVER_TOKEN",
                                "Cookie": "BCS_E2E_HUMAN_COOKIE"}},
        "authority": {"target_user_id_env": "BCS_E2E_AUTHORITY_STAFF_B",
                      "mine_user_id_env": "BCS_E2E_AUTHORITY_STAFF_MINE"},
        "cli": {"transport": "product-cli-http",
                "command_family": "collaboration-authority", "api_face": "public-v1"},
        "policy": {"timeout_seconds": 30, "max_response_bytes": 1048576,
                   "failure_retention_hours": 24},
        "operation_bindings": _operation_bindings(),
        "route_bindings": _route_bindings(),
    }


class TestAuthorityRoster(unittest.TestCase):
    def test_plan_accounting_freezes_the_42_obligation_roster(self):
        plan = authority_density_plan()
        self.assertEqual(plan["accounting"], {
            "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
            "call_group_count": CALL_GROUP_COUNT,
            "shell_status_assertions": SHELL_STATUS_ASSERTIONS,
            "shell_value_assertions": SHELL_VALUE_ASSERTIONS,
            "residual_count_gap": RESIDUAL_COUNT_GAP,
            "parsed_coverage": "42/42"})
        self.assertEqual(plan["schema_version"], 1)
        self.assertEqual(plan["contract"], CONTRACT)
        self.assertEqual(plan["case_id"], CASE_ID)
        self.assertEqual(plan["parent"], "story_authority_user_journey")
        self.assertEqual(plan["source"]["file"], SOURCE_FILE)
        self.assertEqual(plan["source"]["sha256"], SOURCE_SHA256)
        self.assertEqual(plan["source"]["line_start"], STORY_LINE_START)
        self.assertEqual(plan["source"]["line_end"], STORY_LINE_END)
        self.assertEqual(plan["replay_scope"]["business_operations"], 20)
        self.assertEqual(plan["replay_scope"]["bootstrap_operations_excluded"], 4)
        self.assertEqual(plan["replay_scope"]["family_http_matrix"], 24)
        self.assertEqual(plan["environment_posture"], dict(ENVIRONMENT_POSTURE))
        self.assertTrue(plan["source_read_only"])
        self.assertEqual(plan["unlocated_count"], 0)
        self.assertEqual(plan["unlocated_source_probes"], [])

    def test_roster_ids_and_bindings_match_the_landed_authority_family(self):
        rows = parsed_obligations()
        self.assertEqual(tuple(row.obligation_id for row in rows), FAMILY_REQUIREMENTS)
        for row in rows:
            self.assertEqual((row.method, row.path_template, row.status, row.actor,
                              (dict([row.query]) if row.query else None)),
                             FAMILY_PRIMARY[row.obligation_id[len("AUTHORITY-JOURNEY."):]])
        self.assertEqual(SOURCE_SHA256, FAMILY_SOURCE_SHA256)
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA256)
        self.assertEqual(density_contract.SOURCE_REVISION, FAMILY_SOURCE_REVISION)
        self.assertEqual(density_contract.SOURCE_PATH, FAMILY_SOURCE_PATH)
        self.assertEqual(FAMILY_STORY_CHECKS, PARSED_OBLIGATION_COUNT)
        self.assertEqual(FAMILY_HTTP_CALL_COUNT, 24)
        self.assertEqual(FAMILY_BOOTSTRAP_HTTP_CALLS, 4)
        self.assertEqual(FAMILY_BUSINESS_HTTP_CALLS, 20)
        self.assertEqual(PREPROD_COLUMN, FAMILY_ENVIRONMENT["preprod"])

    def test_obligation_anchors_are_verbatim_and_ordered(self):
        rows = parsed_obligations()
        lines = SOURCE.read_text(encoding="utf-8").splitlines()
        previous_call_start = None
        previous_call_end = 0
        for row in rows:
            self.assertEqual(row.quote, lines[row.line_start - 1], row.obligation_id)
            self.assertEqual(row.call_quote, lines[row.call_line_start - 1],
                             row.obligation_id)
            self.assertTrue(row.call_line_end <= row.line_start, row.obligation_id)
            self.assertTrue(STORY_LINE_START <= row.call_line_start
                            <= row.line_end <= STORY_LINE_END)
            self.assertEqual(row.level, "shell")
            self.assertEqual(row.family, "authority")
            self.assertTrue(row.density)
            self.assertIn(row.assertion, ASSERTION_KINDS)
            self.assertTrue(row.as_dict()["located"])
            if (previous_call_start is None
                    or row.call_line_start != previous_call_start):
                self.assertGreater(row.call_line_start, previous_call_end)
                previous_call_start, previous_call_end = (row.call_line_start,
                                                          row.call_line_end)
        self.assertEqual(len(rows), 42)
        self.assertEqual(sum(row.assertion == "require_status" for row in rows),
                         SHELL_STATUS_ASSERTIONS)
        self.assertEqual(sum(row.assertion != "require_status" for row in rows),
                         SHELL_VALUE_ASSERTIONS)
        self.assertEqual(rows[0].obligation_id,
                         "AUTHORITY-JOURNEY.owner-ownership-read-status")
        self.assertEqual(rows[-1].obligation_id, "AUTHORITY-JOURNEY.sent-inbox-contains-bot")

    def test_source_sha256_drift_fails_closed(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA256)
        landed = "owner reads the collaboration ownership view"
        self.assertIn(landed, SOURCE.read_text(encoding="utf-8"))
        drifted = SOURCE.read_text(encoding="utf-8").replace(
            landed, "owner reads the drifted ownership view", 1)
        with tempfile.TemporaryDirectory() as temp:
            shadow = Path(temp) / "authority.sh"
            shadow.write_text(drifted, encoding="utf-8")
            original = density_contract.SOURCE_PATH
            density_contract.SOURCE_PATH = shadow
            try:
                with self.assertRaises(RunStateError):
                    authority_density_plan()
            finally:
                density_contract.SOURCE_PATH = original
        authority_density_plan()

    def test_anchor_and_tiling_tamper_fail_closed(self):
        rows = parsed_obligations()
        lines = SOURCE.read_text(encoding="utf-8").splitlines()
        moved = dataclasses.replace(rows[0], line_start=rows[0].line_start + 1)
        with self.assertRaises(RunStateError):
            density_contract._validate_roster_anchors((moved,) + rows[1:], lines)
        reanchored = dataclasses.replace(rows[0], quote="    drifted anchor")
        with self.assertRaises(RunStateError):
            density_contract._validate_roster_anchors((reanchored,) + rows[1:], lines)
        stretched = dataclasses.replace(rows[0], line_end=rows[0].line_end + 1)
        with self.assertRaises(RunStateError):
            density_contract._validate_tiling((stretched,) + rows[1:], lines)
        original = density_contract.PROJECTION_EXCLUSIONS
        density_contract.PROJECTION_EXCLUSIONS = original[:-1]
        try:
            with self.assertRaises(RunStateError):
                authority_density_plan()
        finally:
            density_contract.PROJECTION_EXCLUSIONS = original
        authority_density_plan()

    def test_projection_exclusions_tile_the_story_function_exactly(self):
        plan = authority_density_plan()
        self.assertEqual(len(plan["projection_exclusions"]), 30)
        self.assertGreaterEqual(
            sum("control-flow" in row["reason"] for row in plan["projection_exclusions"]),
            5)
        lines = SOURCE.read_text(encoding="utf-8").splitlines()
        covered: dict[int, str] = {}

        def mark(start: int, end: int, label: str) -> None:
            for line in range(start, end + 1):
                self.assertNotIn(line, covered,
                                 f"{label} overlaps {covered.get(line)}")
                covered[line] = label

        previous_call_span = None
        for row in plan["obligations"]:
            call_span = (row["call"]["line_start"], row["call"]["line_end"])
            if call_span != previous_call_span:
                mark(call_span[0], call_span[1], row["obligation_id"] + ".call")
                previous_call_span = call_span
            mark(row["source"]["line_start"], row["source"]["line_end"],
                 row["obligation_id"] + ".assertion")
        for exclusion in plan["projection_exclusions"]:
            start, end = exclusion["line_start"], exclusion["line_end"]
            quote, quote_line = exclusion["quote"], exclusion["quote_line"]
            if quote == "":
                self.assertTrue(all(not lines[index - 1].strip()
                                    for index in range(start, end + 1)))
            else:
                self.assertIsNotNone(quote_line)
                self.assertEqual(lines[quote_line - 1], quote)
            mark(start, end, "projection_exclusion")
        self.assertEqual(set(covered), set(range(STORY_LINE_START, STORY_LINE_END + 1)))
        groups = call_groups()
        self.assertEqual(len({group["operation"] for group in groups}), CALL_GROUP_COUNT)
        self.assertEqual(len({group["group_id"] for group in groups}), CALL_GROUP_COUNT)
        self.assertTrue(all(group["group_id"].startswith(GROUP_PREFIX)
                            for group in groups))
        self.assertEqual(sorted(obligation for group in groups
                                for obligation in group["obligation_ids"]),
                         sorted(OBLIGATION_IDS))


class TestAuthorityReplay(unittest.TestCase):
    def test_owned_local_replay_passes_all_forty_two_obligations(self):
        report = run_authority_density(profile=local_profile())
        self.assertEqual(report["status"], STATUS_PASS)
        self.assertEqual(report["execution"]["completed_count"], 42)
        self.assertEqual(report["execution"]["failed_count"], 0)
        self.assertEqual(report["execution"]["not_run_count"], 0)
        self.assertEqual(report["execution"]["total_count"], 42)
        self.assertTrue(all(row["status"] == STATUS_PASS for row in report["assertions"]))
        self.assertEqual(len(report["assertions"]), 42)
        self.assertEqual(density_accounting(report), {
            "counts": {"PASS": 42, "FAIL": 0, "NOT_RUN": 0}, "expected": 42,
            "observed": 42, "residual_count_gap": 0, "grants_release_credit": False})
        self.assertEqual(report["case_scope"], authority_density_plan()["accounting"])
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])

    def test_injected_replay_transport_records_42_calls_over_20_operations(self):
        transport = ReplayTransport(canonical_replay_observations())
        report = run_authority_density(profile=local_profile(), transport=transport)
        self.assertEqual(report["status"], STATUS_PASS)
        self.assertEqual(len(transport.calls), 42)
        self.assertEqual(len({operation for operation, _ in transport.calls}), 20)
        rows = parsed_obligations()
        for row, receipt in zip(rows, report["assertions"]):
            self.assertEqual(receipt["source"]["quote"], row.quote)
            self.assertEqual(receipt["call"]["quote"], row.call_quote)
            self.assertEqual(receipt["call"]["line_start"], row.call_line_start)
            self.assertEqual(receipt["call"]["method"], row.method)
            self.assertEqual(receipt["expected"], row.expected)
        self.assertEqual(report["replay"]["transport"], "injected-replay")
        self.assertEqual(report["replay"]["operations_replayed"], 20)
        self.assertEqual(report["replay"]["obligation_evaluations"], 42)
        self.assertTrue(report["replay"]["envelope_precondition"]["satisfied"])
        self.assertEqual(sorted(report["replay"]["envelope_precondition"]
                                ["required_keys"]), sorted(BUSINESS_ENVELOPE_KEYS))
        self.assertEqual(report["http_matrix"]["business_operations"], 20)
        self.assertEqual(report["http_matrix"]["bootstrap_operations_excluded"], 4)
        self.assertEqual(report["http_matrix"]["family_http_matrix"], 24)

    def test_local_without_transport_uses_only_the_canonical_replay(self):
        report = run_authority_density(profile=local_profile())
        self.assertEqual(report["status"], STATUS_PASS)
        self.assertEqual(report["replay"]["transport"], "in-memory-replay")

    def test_preprod_without_transport_is_blocked_with_named_gaps(self):
        report = run_authority_density(profile=preprod_profile())
        self.assertEqual(report["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(report["execution"]["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(report["execution"]["error_type"], PREPROD_ADMISSION_BLOCKED)
        self.assertEqual(report["execution"]["completed_count"], 0)
        self.assertEqual(report["execution"]["not_run_count"], 42)
        self.assertEqual(report["gap"]["id"], PREPROD_GAP_IDS[0])
        self.assertEqual(report["gap"]["status"], "NOT_RUN")
        attribution = gap_attribution()
        identity = set(IDENTITY_IDS)
        self.assertEqual(len(identity), 33)
        for receipt in report["assertions"]:
            self.assertEqual(receipt["status"], STATUS_NOT_RUN)
            self.assertEqual(receipt["error_type"], PREPROD_ADMISSION_BLOCKED)
            self.assertEqual(receipt["gap_ids"],
                             list(attribution[receipt["obligation_id"]]))
            gaps = receipt["gap_ids"]
            self.assertEqual(gaps[:5], list(PREPROD_GAP_IDS[:5]))
            self.assertNotIn(PREPROD_GAP_IDS[6], gaps)
            if receipt["obligation_id"] in identity:
                self.assertEqual(len(gaps), 6)
                self.assertEqual(gaps[5], PREPROD_GAP_IDS[5])
            else:
                self.assertEqual(len(gaps), 5)
        self.assertEqual(sum(1 for row in report["assertions"] if len(row["gap_ids"]) == 6),
                         33)
        admission = report["preprod_admission"]
        self.assertEqual(admission["gap_ids"], list(PREPROD_GAP_IDS))
        self.assertEqual(set(admission["gaps"]), set(PREPROD_GAP_IDS))
        self.assertEqual(admission["column"], PREPROD_COLUMN)
        self.assertIn("r8_evidence", admission)
        self.assertIn("route_binding_review", admission)
        self.assertIn("transport_injection", admission)
        self.assertIn("never grants admission", admission["transport_injection"])
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])

    def test_preprod_transport_injection_never_promotes_an_obligation(self):
        report = run_authority_density(
            profile=preprod_profile(), transport=canonical_replay_transport())
        self.assertEqual(report["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(report["execution"]["error_type"], PREPROD_ADMISSION_BLOCKED)
        self.assertTrue(all(row["status"] == STATUS_NOT_RUN for row in report["assertions"]))

    def test_envelope_precondition_failure_fails_closed_and_cascades(self):
        observations = canonical_replay_observations()
        observations["ownership.owner.read"] = Observation(
            200, {"code": 200, "message": "stub", "data": {}})
        report = run_authority_density(
            profile=local_profile(), transport=ReplayTransport(observations))
        self.assertEqual(report["status"], STATUS_FAIL)
        by_id = {row["obligation_id"]: row for row in report["assertions"]}
        first = by_id["AUTHORITY-JOURNEY.owner-ownership-read-status"]
        self.assertEqual(first["status"], STATUS_FAIL)
        self.assertEqual(first["error_type"], ENVELOPE_PRECONDITION_FAILED)
        self.assertEqual(first["observed"]["missing_keys"], ["request_id"])
        self.assertEqual(report["execution"]["failed_count"], 1)
        self.assertEqual(report["execution"]["not_run_count"], 41)
        self.assertEqual(report["execution"]["error_type"],
                         "AUTHORITY_DENSITY_ASSERTION_FAILED")
        self.assertFalse(report["replay"]["envelope_precondition"]["satisfied"])
        self.assertEqual(report["replay"]["envelope_precondition"]["operations_admitted"], 1)
        later = by_id["AUTHORITY-JOURNEY.transfer-accept-accepted"]
        self.assertEqual(later["status"], STATUS_NOT_RUN)
        self.assertEqual(later["error_type"], BLOCKED_AFTER_ASSERTION_FAILURE)

    def test_bad_manager_metadata_fails_and_cascades_to_later_rows(self):
        observations = canonical_replay_observations()
        observations["managers.page.populated"] = Observation(
            200, {"code": 200, "message": "stub", "request_id": "x",
                  "data": {"total": 0, "items": []}})
        report = run_authority_density(
            profile=local_profile(), transport=ReplayTransport(observations))
        self.assertEqual(report["status"], STATUS_FAIL)
        by_id = {row["obligation_id"]: row for row in report["assertions"]}
        kept = by_id["AUTHORITY-JOURNEY.manager-page-keeps-manager"]
        self.assertEqual(kept["status"], STATUS_FAIL)
        self.assertEqual(kept["observed"], 0)
        exposes = by_id["AUTHORITY-JOURNEY.manager-page-exposes-added-manager"]
        self.assertEqual(exposes["status"], STATUS_NOT_RUN)
        self.assertEqual(exposes["error_type"], BLOCKED_AFTER_ASSERTION_FAILURE)
        self.assertEqual(report["execution"], {
            "status": STATUS_FAIL, "completed_count": 10, "failed_count": 1,
            "not_run_count": 31, "total_count": 42,
            "error_type": "AUTHORITY_DENSITY_ASSERTION_FAILED"})

    def test_transfer_renewal_window_mirrors_the_family_semantics(self):
        canonical = run_authority_density(profile=local_profile())
        by_id = {row["obligation_id"]: row for row in canonical["assertions"]}
        window = by_id["AUTHORITY-JOURNEY.transfer-refresh-window-renewed"]
        self.assertEqual(window["status"], STATUS_PASS)
        self.assertEqual(window["observed"]["initiated_expires_at"], INITIATED_EXPIRES_AT)
        self.assertEqual(window["observed"]["refreshed_expires_at"], REFRESHED_EXPIRES_AT)
        self.assertEqual(window["observed"]["delta_ms"],
                         REFRESHED_EXPIRES_AT - INITIATED_EXPIRES_AT)
        self.assertTrue(window["observed"]["renewed"])
        self.assertEqual(window["observed"]["max_renewal_ms"],
                         TRANSFER_WINDOW_MAX_RENEWAL_MS)
        cases = ((REFRESHED_EXPIRES_AT - 1, True),
                 (INITIATED_EXPIRES_AT - 1, False),
                 (INITIATED_EXPIRES_AT + 299_999, True),
                 (INITIATED_EXPIRES_AT + TRANSFER_WINDOW_MAX_RENEWAL_MS, False),
                 (INITIATED_EXPIRES_AT + 300_001, False))
        for shifted, expected in cases:
            observations = canonical_replay_observations()
            body = observations["transfer.refresh"].body
            observations["transfer.refresh"] = Observation(
                200, dict(body, data=dict(body["data"], expires_at=shifted)))
            report = run_authority_density(
                profile=local_profile(), transport=ReplayTransport(observations))
            row = {item["obligation_id"]: item for item in report["assertions"]}[
                "AUTHORITY-JOURNEY.transfer-refresh-window-renewed"]
            self.assertEqual(row["status"], STATUS_PASS if expected else STATUS_FAIL,
                             f"shifted expiry {shifted}")
            self.assertEqual(row["observed"]["renewed"], expected)

    def test_missing_observation_is_fail_evidence_not_pass(self):
        report = run_authority_density(
            profile=local_profile(), transport=ReplayTransport({}))
        self.assertEqual(report["status"], STATUS_FAIL)
        self.assertEqual(report["assertions"][0]["status"], STATUS_FAIL)
        self.assertEqual(report["assertions"][0]["error_type"], "KeyError")
        self.assertTrue(all(row["status"] == STATUS_NOT_RUN
                            for row in report["assertions"][1:]))


class TestAuthorityAdmission(unittest.TestCase):
    def test_profiles_admit_local_and_preprod_shapes(self):
        validate_density_profile(local_profile())
        validate_density_profile(preprod_profile())

    def test_profile_rejects_credential_material_and_shape_drift(self):
        with self.assertRaises(RunStateError):
            validate_density_profile("not-a-profile")
        bad = local_profile()
        bad["secrets"] = {"driver": "literal"}
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = local_profile()
        bad["auth"]["principal_signing_key_env"] = "BAD_ENV_REFERENCE"
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = local_profile()
        bad["operation_bindings"].pop("transfer.accept")
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = local_profile()
        bad["operation_bindings"]["invented.operation"] = {"method": "GET",
                                                           "path_template": "/x",
                                                           "actor": "owner",
                                                           "query": None}
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = preprod_profile()
        bad["route_bindings"]["managers.add.target"]["reviewed"] = True
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = preprod_profile()
        bad["route_bindings"]["public.mine.read"]["api_face"] = "internal-v1"
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = preprod_profile()
        bad["entrypoint"]["origin"] = "http://bcs-pre.alipay.com"
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = preprod_profile()
        bad["auth"]["header_env"]["Authorization"] = "NOT_AN_ENV_NAME"
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = local_profile()
        bad["route_bindings"] = {}
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = preprod_profile()
        del bad["human"]
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)
        bad = local_profile()
        bad["policy"]["timeout_seconds"] = 0
        with self.assertRaises(RunStateError):
            validate_density_profile(bad)

    def test_admitted_environment_references_are_known_by_name_only(self):
        self.assertEqual(ALLOWED_ENV_NAMES, {
            "BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN",
            "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE",
            "BCS_E2E_AUTHORITY_STAFF_B", "BCS_E2E_AUTHORITY_STAFF_MINE"})
        profile = preprod_profile()
        self.assertEqual(profile["auth"]["header_env"],
                         {"Authorization": "BCS_E2E_DRIVER_TOKEN",
                          "Cookie": "BCS_E2E_HUMAN_COOKIE"})
        self.assertEqual(profile["authority"]["target_user_id_env"],
                         "BCS_E2E_AUTHORITY_STAFF_B")
        self.assertEqual(profile["authority"]["mine_user_id_env"],
                         "BCS_E2E_AUTHORITY_STAFF_MINE")

    def test_operation_and_route_bindings_carry_the_frozen_roster(self):
        local = local_profile()
        self.assertEqual(set(local["operation_bindings"]),
                         {row.operation for row in parsed_obligations()})
        remote = preprod_profile()
        self.assertEqual(set(remote["route_bindings"]),
                         set(remote["operation_bindings"]))
        binding = remote["route_bindings"]["public.mine.read"]
        self.assertEqual(binding["api_face"], "public-v1")
        self.assertEqual(binding["method"], "GET")
        self.assertEqual(binding["path"], "/openapi/v1/collaboration/bots/mine")
        self.assertFalse(binding["reviewed"])
        internal = remote["route_bindings"]["transfer.initiate"]
        self.assertEqual(internal["api_face"], "internal-v1")
        self.assertEqual(internal["auth"], "gateway-principal-jwt")


class TestAuthorityPreprodAdmission(unittest.TestCase):
    def test_named_gap_ledger_is_complete_and_fail_closed(self):
        gaps = preprod_admission_gaps()
        self.assertEqual([gap["id"] for gap in gaps], list(PREPROD_GAP_IDS))
        for gap in gaps:
            self.assertEqual(gap["status"], "NOT_RUN")
            self.assertTrue(gap["detail"])
            self.assertTrue(gap["derivation_basis"])
        all_ids = set(OBLIGATION_IDS)
        for gap in gaps[:5]:
            self.assertEqual(set(gap["blocks"]), all_ids, gap["id"])
        identity = set(IDENTITY_IDS)
        self.assertEqual(len(identity), 33)
        self.assertEqual(set(gaps[5]["blocks"]), identity)
        self.assertEqual(gaps[6]["blocks"], [])
        attribution = gap_attribution()
        self.assertEqual(set(attribution), all_ids)
        for obligation_id, gap_ids in attribution.items():
            self.assertEqual(gap_ids[:5], PREPROD_GAP_IDS[:5])
            self.assertIn(len(gap_ids), (5, 6))
        assert_honesty_ledger()
        self.assertEqual(PRODUCT_DEFECTS, {})
        ledger = defect_gap_ledger()
        self.assertEqual(ledger["identity_preseed_obligations"], 33)
        self.assertEqual(ledger["parsed_obligation_count"], 42)
        self.assertEqual(ledger["call_group_count"], 20)
        self.assertEqual(ledger["preprod_column"], PREPROD_COLUMN)
        self.assertEqual(ledger["product_defects"], {})
        self.assertEqual(len(ledger["documented_upstream_scope_notes"]), 2)

    def test_admission_diagnosis_basis_carries_the_r8_evidence(self):
        basis = admission_diagnosis_basis()
        self.assertEqual(basis["frozen_column"], PREPROD_COLUMN)
        review = basis["route_review"]
        self.assertEqual(review["reviewed_pilot_operations"], 12)
        self.assertEqual(review["authority_operations_unreviewed"], 20)
        self.assertIn("bcs_e2e.route_bindings", review["pilot_table"])
        self.assertEqual(basis["credential_refs"],
                         ["BCS_E2E_DRIVER_TOKEN", "BCS_E2E_HUMAN_COOKIE"])
        self.assertEqual(basis["staff_refs"],
                         ["BCS_E2E_AUTHORITY_STAFF_B", "BCS_E2E_AUTHORITY_STAFF_MINE"])
        self.assertEqual(basis["signing_key_env_ref"],
                         "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE")
        self.assertEqual(basis["frozen_local_fixtures"],
                         {"target_user_id": TARGET_USER_ID, "mine_user_id": "137"})
        r8 = basis["r8_evidence"]
        self.assertEqual(r8["classification"], "PREPROD_AUTHORITY_ROUTE_PROBE_MISMATCH")
        self.assertEqual(r8["observed_statuses"],
                         {"health": None, "register-token-unauthenticated": None})
        self.assertIn("URLError", r8["transport_errors"])
        self.assertEqual(r8["authenticated_probe_routes"],
                         ["ownership-read-driver-bearer", "public-mine-read-human-cookie"])
        self.assertTrue(r8["leaf"].endswith("authority-dual-run.json"))
        self.assertEqual(r8["family_disposition"], "AUTHORITY_PREPROD_ADMISSION_BLOCKED")
        self.assertIn("2026-10-30", basis["trio_context"])
        self.assertEqual(basis["coverage_map_row"], 18)
        self.assertIn("rank 2", basis["coverage_map_queue"])
        self.assertIn("gap_qty 42", basis["coverage_map_queue"])
        self.assertIn("feasibility 3", basis["coverage_map_queue"])
        self.assertIn("126", basis["coverage_map_queue"])

    def test_no_diagnosis_becomes_a_pass(self):
        report = run_authority_density(profile=preprod_profile())
        for row in report["assertions"]:
            self.assertEqual(row["status"], STATUS_NOT_RUN)
        self.assertEqual(report["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertNotEqual(report["status"], STATUS_PASS)
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        accounting = density_accounting(report)
        self.assertEqual(accounting["counts"], {"PASS": 0, "FAIL": 0, "NOT_RUN": 42})
        self.assertEqual(accounting["residual_count_gap"], 0)
        self.assertFalse(accounting["grants_release_credit"])
        for gap in preprod_admission_gaps():
            self.assertEqual(gap["status"], "NOT_RUN")

    def test_local_replay_claims_no_remote_credit(self):
        report = run_authority_density(profile=local_profile())
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertFalse(report["grants_release_credit"])
        pointer = report["local_live_pointer"]
        self.assertEqual(pointer["family_case"], "dual-authority-run")
        self.assertEqual(pointer["owned_local_live_obligation_closure"], "42/42")
        self.assertIn("transfers no release credit", pointer["statement"])
        self.assertEqual(report["honesty"]["product_defects"], {})


class TestAuthorityIntegrationDiscipline(unittest.TestCase):
    def test_describe_only_proposal_reuses_the_dual_authority_envelope(self):
        value = registration_proposal()
        self.assertEqual(value["status"], "DESCRIBE_ONLY")
        self.assertEqual(value["token"], INTEGRATION_TOKEN)
        self.assertEqual(value["executor_row"], 18)
        self.assertEqual(value["executor_migration_row"], 35)
        self.assertEqual(value["envelope_command"], "dual-authority-run")
        self.assertEqual(value["envelope_command"], FAMILY_DUAL_COMMAND)
        self.assertFalse(value["new_registration_command"])
        self.assertEqual(value["parsed_obligations"], 42)
        self.assertEqual(value["residual_count_gap"], 0)
        self.assertEqual(value["source_sha256"], SOURCE_SHA256)
        self.assertEqual(value["preprod_gap_ids"], list(PREPROD_GAP_IDS))
        self.assertIn("no new command", value["notes"])
        self.assertIn("executor-migration.json", value["red_lines"])
        self.assertIn("coverage-map/", value["red_lines"])
        self.assertGreaterEqual(len(value["suggested_notes"]), 2)

    def test_reporting_contains_the_requested_mainline_fragment(self):
        notes = proposed_executor_migration_notes()
        self.assertIn(f"DESCRIBE_ONLY token={INTEGRATION_TOKEN}", notes)
        self.assertIn("no new registration command", " ".join(notes))
        self.assertIn("row #18", " ".join(notes))
        self.assertIn("parsed=42", " ".join(notes))
        self.assertIn("residual_count_gap=0", " ".join(notes))
        joined = " ".join(notes)
        for gap_id in PREPROD_GAP_IDS:
            self.assertIn(gap_id, joined)
        self.assertIn("remote_validation=NOT_RUN", joined)
        self.assertIn("grants_release_credit=false", joined)
        report = run_authority_density(profile=local_profile())
        self.assertEqual(sum(line.startswith("| AUTHORITY-JOURNEY")
                             for line in md_table(report).splitlines()), 42)
        blocked = run_authority_density(profile=preprod_profile())
        self.assertEqual(sum(line.startswith("| AUTHORITY-JOURNEY")
                             for line in md_table(blocked).splitlines()), 42)

    def test_package_has_no_network_process_env_or_credential_value_literals(self):
        text = "\n".join(path.read_text(encoding="utf-8")
                         for path in PACKAGE.glob("*.py"))
        for forbidden in ("requests", "urllib", "socket", "subprocess", "os.getenv",
                          "os.environ"):
            self.assertNotIn(forbidden, text)
        for pattern in (r"Bearer\s", r"Cookie:\s", r"-----BEGIN"):
            self.assertIsNone(re.search(pattern, text), pattern)
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
                self.assertIsNone(pattern.search(text),
                                  f"{path.name} tripped {pattern.pattern}")

    def test_line_budget_and_subpackage_shape(self):
        self.assertEqual({path.stem for path in PACKAGE.glob("*.py")}, {
            "__init__", "contract", "defect", "profile", "reporting",
            "registration_proposal", "run", "transport"})
        for path in PACKAGE.glob("*.py"):
            self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()),
                                 1000, path.name)
        self.assertLessEqual(len(Path(__file__).read_text(encoding="utf-8").splitlines()),
                             1000)

    def test_family_accounting_is_mechanical(self):
        self.assertEqual(family_accounting(), {
            "parsed": 42, "call_groups": 20, "shell_status_assertions": 20,
            "shell_value_assertions": 22, "residual_count_gap": 0})


if __name__ == "__main__":
    unittest.main()
