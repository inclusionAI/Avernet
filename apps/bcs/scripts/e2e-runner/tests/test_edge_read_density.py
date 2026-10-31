"""Offline guards for the Wave-D3 edge_permission density overlay.

These tests pin the honesty disciplines of the 22-leaf density overlay over
the five already-PASS edge_permission parents (#32/#33/#34/#35/#39):

* the roster closes parsed == frozen + density per parent and per total, with
  every leaf anchored to its upstream edge_permission.sh line;
* the evaluator keeps one predicate table for the enrolled posture and the
  recorded-receipt classification posture;
* the owned-local arm renders its four offline pillars and fails honest on a
  contradicting recorded wpc round;
* the preprod arm re-reads the recorded r5/r6/r7 receipts without any
  transport, keeps NOT_ENROLLED / NOT_RUN fail-close, cross-checks the driver
  and member identities against preprod.json, and never promotes credit;
* the dual envelope maps degenerate states with the documented priority;
* the registration proposal stays describe-only and touches no shared face.

All fixtures are synthetic and local; the recorded-evidence assertions are
skipped unless the real preprod-live-evidence root is attached.  Nothing here
reads credentials, opens a transport, or writes outside temporary directories.
"""
from __future__ import annotations

import importlib.util
import json
import stat
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bcs_e2e.edge_permission_backlog import (  # noqa: E402
    density_case_run,
    density_contract,
    density_eval,
    density_local_arm,
    density_obligations,
    density_preprod_arm,
    density_registration_proposal,
    density_reporting,
)
from bcs_e2e.edge_permission_backlog.density_eval import (  # noqa: E402
    CLASSIFICATION_STATUSES,
    EVALUATION_FAIL,
    EVALUATION_NOT_OBSERVABLE,
    EVALUATION_PASS,
    classify_recorded,
    classification_totals,
    evaluate_leaf,
)
from bcs_e2e.edge_permission_backlog.density_obligations import (  # noqa: E402
    ADDED_BY_PARENT,
    EXISTING_BY_PARENT,
    PARSED_BY_PARENT,
    PARENTS,
    SOURCE_PATH,
    build_density_obligations,
    density_reconciliation,
    source_digest,
)
from bcs_e2e.edge_permission_backlog.density_contract import (  # noqa: E402
    DENSITY_CASE_KEYS,
    DENSITY_LEAF_COUNT,
    ENVELOPE_STATUS,
    ENROLLMENT_NOT_ENROLLED,
    ERROR_ADMISSION_CASE_KEY,
    EXISTING_TOTAL,
    FRIEND_REQUEST_AND_LIST_CASE_KEY,
    MUTATION_FAMILY_CONTRACT,
    MUTUAL_AUTO_APPROVE_CASE_KEY,
    OPTIONAL_DENSITY_COMMAND,
    PARSED_TOTAL,
    PREPROD_NOT_RUN,
    PROJECTED_SUPERSET_BY_PARENT,
    READ_FAMILY_CONTRACT,
    assert_family_alignment,
    density_case_row,
    existing_envelope_projection,
)
from bcs_e2e.inventory import assert_reviewed_parent, load_executor_migration  # noqa: E402
from bcs_e2e.profile import ConfigError  # noqa: E402
from bcs_e2e.run_state import RunStateError, read as read_state  # noqa: E402

RUN_ID = "density-unit-guards-1"
EVIDENCE_ROOT = Path.home() / ".codex" / "bcs-e2e" / "preprod-live-evidence"
WPC_ROUND_LABEL = "20261031-migration-wpc-local-owned-edge-3"
MUTATION_R5 = "20261031-regression-r5-edge-mutation"
MUTATION_R6 = "20261031-regression-r6-edge-mutation"
READ_R5 = "20261031-regression-r5-edge-read"
R7_PREPROD = "20261031-regression-r7-preprod"
DRIVER_BOT_ID = "20261026_xq102n2b:39464"
MEMBER_BOT_ID = "20261010_gekng5hk:39464"


def _write(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _profile(**overrides: Any) -> dict:
    profile = {
        "schema_version": 1,
        "contract": "dual-edge-permission-density-profile-v1",
        "environment": {"tier": "local", "id": "density-unit", "entrypoint": "singlebox"},
        "policy": {"timeout_seconds": 30, "maximum_response_bytes": 1048576},
    }
    profile.update(overrides)
    return profile


def _mutation_receipt(case_id: str, parent_id: str, requirement_id: str,
                      data: dict, *, status: str = "PASS",
                      contract: str = MUTATION_FAMILY_CONTRACT) -> dict:
    step = {
        "requirement_id": requirement_id,
        "method": "run_density_probe",
        "status": status,
        "data": data,
        "evidence": {
            "transport": "station-direct",
            "operation": "edge.friend_connection_mutation",
            "projection": {},
            "fixture_effect": "NONE",
            "grants_release_credit": False,
            "remote_validation": "NOT_RUN",
            "requests": [],
        },
    }
    return {
        "schema_version": 1,
        "case_id": case_id,
        "parent_id": parent_id,
        "status": status,
        "run_id": "synthetic-density-round",
        "contract": contract,
        "credential_kind": "human_cookie+registered_bot_token_presence",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "resource_disposition": "REGISTERED_BOT_RETAINED",
        "executor_digest": "synthetic-executor-digest",
        "profiles": {"gateway_digest": "synthetic-gateway", "station_digest": "synthetic-station"},
        "environment": {"gateway": {}, "station": {}},
        "execution": {"status": status, "completed_count": 1, "total_count": 1,
                      "error_type": None, "error_detail": None},
        "case": {
            "case_id": case_id, "parent_id": parent_id, "status": status,
            "completed_count": 1, "total_count": 1, "ready_to_execute": True,
            "error_type": None, "error_detail": None, "steps": [step],
        },
        "case_scope": {"requirement_ids": [requirement_id],
                       "public_operations": ["friend_connection_mutation.create"]},
        "product_defects": {},
        "documented_gaps": {},
    }


def _read_receipt(case_id: str, parent_id: str, *, caller_actor_id: str | None = None,
                  status: str = "PASS") -> dict:
    step = {
        "requirement_id": "EDGE-FRIEND-CONNECTIONS.list",
        "method": "read_friend_connections_list",
        "status": status,
        "data": {"item_count": 3, "page": 1, "page_size": 100, "total": 3},
        "evidence": {
            "transport": "public-v1-http",
            "operation": "edge.friend_connections.read",
            "http_method": "GET",
            "http_status": 200,
            "identity": "HUMAN_COOKIE_DRIVER_VIEW",
            "caller_actor_id": caller_actor_id,
            "fixture_effect": "NONE",
            "item_count": 3, "page": 1, "page_size": 100, "total": 3,
        },
    }
    step_two = dict(step)
    step_two["requirement_id"] = "EDGE-FRIEND-CONNECTIONS.list-repeat"
    step_two["method"] = "read_friend_connections_list_repeat"
    return {
        "schema_version": 1,
        "case_id": case_id,
        "parent_id": parent_id,
        "status": status,
        "run_id": "synthetic-density-read-round",
        "contract": READ_FAMILY_CONTRACT,
        "credential_kind": "human_cookie",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "resource_disposition": "READ_ONLY_NO_BUSINESS_RESOURCE",
        "executor_digest": "synthetic-executor-digest",
        "profile_digest": "synthetic-profile-digest",
        "environment": {"gateway": {}},
        "execution": {"status": status, "completed_count": 2, "total_count": 2,
                      "error_type": None, "error_detail": None},
        "case": {
            "case_id": case_id, "parent_id": parent_id, "status": status,
            "completed_count": 2, "total_count": 2, "ready_to_execute": True,
            "error_type": None, "error_detail": None, "steps": [step, step_two],
        },
    }


def _junit_xml(rows: list[tuple[str, str]]) -> str:
    parts = ['<testsuite name="bcs-e2e-pilot" tests="{0}" failures="0" errors="0" skipped="0">'.format(len(rows))]
    for case_id, runner_status in rows:
        parts.append('<testcase classname="bcs-e2e" name="' + case_id + '">')
        parts.append("<properties>")
        for name, value in (
                ("runner_status", runner_status),
                ("case_version", "1"),
                ("manifest_digest", "synthetic-manifest-digest"),
                ("executor_digest", "synthetic-executor-digest"),
                ("functional_status", runner_status),
                ("evidence_status", runner_status),
                ("cleanup_status", "PASS"),
                ("execution_safety", "NOT_TRACKED"),
                ("resource_disposition", "FRIEND_EDGES_REVOKED_BY_BUSINESS_API_ROWS_RETAINED")):
            parts.append('<property name="' + name + '" value="' + value + '"/>')
        parts.append("</properties></testcase>")
    parts.append("</testsuite>")
    return "".join(parts)


def _flow_report(rows: list[tuple[str, str]], *, run_id: str = "synthetic-wpc") -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "suite": "owned-local-edge-friend-mutations-v1",
        "status": "LOCAL_EDGE_FRIEND_MUTATIONS_PASS",
        "full_release_status": "NOT_QUALIFIED",
        "remote_validation": "NOT_RUN",
        "ready_to_execute": False,
        "case_ids": [case_id for case_id, _ in rows],
        "credentials": {"state": "CLOSED", "selected_roles": ["CEO", "PM", "ENG", "QA"]},
        "cases": [
            {"case_id": case_id, "parent_id": parent, "status": "PASS",
             "completed_count": 1, "total_count": 1, "ready_to_execute": True,
             "error_type": None, "steps": []}
            for case_id, parent in rows],
    }


def _summary(run_id: str) -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": "LOCAL_SUITE_PASS",
        "scope": {"suite": "owned-local-edge-friend-mutations-v1",
                  "selected_cases": ["LOCAL-EDGE-FRIEND-ACCEPT-REJECT-CANCEL-01"]},
        "integrity": {"status": "PASS", "issues": []},
    }


WPC_FROZEN_ROWS = (
    ("LOCAL-EDGE-FRIEND-ACCEPT-REJECT-CANCEL-01",
     "test_ep_friend_connections_friend_accept_reject_cancel"),
    ("LOCAL-EDGE-FRIEND-REVOKE-01", "test_ep_friend_connections_friend_revoke"),
    ("LOCAL-EDGE-ENSURE-BOT-01", "test_ep_ensure_bot"),
    ("LOCAL-EDGE-FRIEND-FULL-LIFECYCLE-01", "test_ep_friend_connections_full_lifecycle"),
    ("LOCAL-EDGE-FRIEND-MUTUAL-AUTO-APPROVE-01",
     "test_ep_friend_connections_mutual_auto_approve"),
    ("LOCAL-EDGE-ERROR-ADMISSION-BRANCHES-01", "test_ep_error_and_admission_branches"),
    ("LOCAL-EDGE-CONNECT-PRIVATE-DISCOVER-01",
     "test_ep_connect_private_and_discover_branches"),
    ("LOCAL-EDGE-TC-FRIEND-AUTH-SYNC-01", "test_ep_tc_friend_auth_sync_readdressed_principal"),
    ("LOCAL-EDGE-DEPT-SCOPE-FRIEND-EXT-01", "test_ep_dept_scope_parses_object_friend_ext_entries"),
)


def _wpc_round_root(base: Path, *, runner_status: str = "PASS") -> Path:
    root = base / "20261031-migration-wpc-local" / "owned-local"
    _write(root / "junit.xml.txt", {})  # placeholder removed below
    (root / "junit.xml").write_text(_junit_xml(
        [(case_id, runner_status) for case_id, _ in WPC_FROZEN_ROWS]), encoding="utf-8")
    (root / "junit.xml.txt").unlink()
    _write(root / "edge-friend-mutations-flow-report.json",
           _flow_report(WPC_FROZEN_ROWS))
    _write(root / "summary.json", _summary(WPC_ROUND_LABEL))
    return base


ERROR_ADMISSION_DATA = {
    "admission_branches": {
        "unknown_bot": "bot_not_found",
        "protected_no_edge": "no_edge",
        "protected_no_edge_human": "no_edge",
        "human_public_default": "public_default",
        "human_public_with_edge": "public_default",
        "bot_party_edge": "edge_grant/ok",
    },
    "self_add": {"http_status": 400, "code": 40000,
                 "data": {"error_code": "cannot_add_self"}},
    "unknown_bot": {"http_status": 404, "code": 40400,
                    "data": {"error_code": "bot_not_found"}},
    "duplicate_pending_count": 1,
    "duplicate_request_ids": ["id-one", "id-one"],
    "connected_create_status": "approved",
}
CONNECT_DATA = {
    "private_create_defect": {"defect": "PD-PRIVATE-CREATE-500",
                              "http_status": 500, "code": 50000,
                              "error_code": {"error_code": "internal_error"}},
    "private_discovery_empty": True,
    "public_discovery_count": 35,
    "public_discovery_self_excluded": True,
    "public_directory_driver_listed": True,
    "role_no_org_drift": {"http_status": 400,
                          "message": "wrapped: role_requires_organization_code"},
    "organization_no_requester_drift": {"http_status": 403, "message": "fail closed"},
}


class TestDensityRoster(unittest.TestCase):
    def test_build_closes_the_roster(self):
        leaves = build_density_obligations()
        self.assertEqual(len(leaves), 22)
        ids = [leaf.obligation_id for leaf in leaves]
        self.assertEqual(len(ids), len(set(ids)))
        for parent in PARENTS:
            self.assertEqual(EXISTING_BY_PARENT[parent] + ADDED_BY_PARENT[parent],
                             PARSED_BY_PARENT[parent])
        recon = density_reconciliation()
        self.assertEqual(recon["parsed_total"], 28)
        self.assertEqual(recon["frozen_existing_total"], 6)
        self.assertEqual(recon["density_leaves_total"], 22)
        self.assertEqual(recon["projected_superset_total"], 28)
        self.assertEqual(recon["source_digest"], source_digest())
        assert_family_alignment()

    def test_leaf_anchors_carry_upstream_quotes(self):
        lines = SOURCE_PATH.read_text(encoding="utf-8").splitlines()
        for leaf in build_density_obligations():
            self.assertIn(leaf.assertion_quote, lines[leaf.assertion_line - 1],
                          leaf.obligation_id)
            self.assertEqual(len(leaf.request_lines), len(leaf.request_quotes))
            for number, quote in zip(leaf.request_lines, leaf.request_quotes):
                self.assertIn(quote, lines[number - 1], leaf.obligation_id)

    def test_build_fail_closed_on_missing_parent(self):
        lines = density_obligations._source_lines()
        with self.assertRaises(RunStateError):
            density_obligations._function_body_lines(lines, "test_ep_not_a_parent")

    def test_family_alignment_and_envelope_projection(self):
        self.assertTrue(assert_family_alignment())
        projection = existing_envelope_projection()
        self.assertEqual(projection["read_family"]["case_key_count"], 4)
        self.assertEqual(projection["mutation_family"]["case_key_count"], 7)
        self.assertEqual(projection["backlog_family"]["case_key_count"], 2)
        self.assertEqual(projection["density_overlay"]["density_leaves"], 22)
        self.assertEqual(projection["registration_state"], "UNCHANGED_OFFLINE_PROJECTION")

    def test_reviewed_parents_and_committed_rows(self):
        for case_key in DENSITY_CASE_KEYS:
            row = density_case_row(case_key)
            assert_reviewed_parent(row["parent_id"], family="dual-edge-permission-density")
        committed = load_executor_migration()
        for case_key in DENSITY_CASE_KEYS:
            row = density_case_row(case_key)
            status = committed[row["parent_id"]]
            self.assertEqual(status["mode"], "DUAL")
            self.assertEqual(status["preprod_verdict"], "PASS")
            self.assertEqual(status["gates"], [])
            self.assertEqual(
                status["notes"],
                [density_registration_proposal.PROPOSED_EXECUTOR_MIGRATION_NOTES[
                    row["parent_id"]]])


class TestDensityEvaluator(unittest.TestCase):
    def test_evaluate_strict_postures(self):
        leaves = {leaf.obligation_id: leaf.as_dict()
                  for leaf in build_density_obligations()}
        self_add = leaves["EDGE-ERROR-ADMISSION.self-add-rejected"]
        good = {"self_add": {"http_status": 400, "data": {"error_code": "cannot_add_self"}}}
        self.assertEqual(evaluate_leaf(self_add, good)["status"], EVALUATION_PASS)
        bad = {"self_add": {"http_status": 403, "data": {"error_code": "nope"}}}
        self.assertEqual(evaluate_leaf(self_add, bad)["status"], EVALUATION_FAIL)
        self.assertEqual(evaluate_leaf(self_add, {})["status"], EVALUATION_NOT_OBSERVABLE)
        self.assertEqual(evaluate_leaf(self_add, None)["status"], EVALUATION_NOT_OBSERVABLE)

    def test_classify_recorded_postures(self):
        leaves = {leaf.obligation_id: leaf.as_dict()
                  for leaf in build_density_obligations()}
        dup = leaves["EDGE-ERROR-ADMISSION.duplicate-create-idempotent"]
        matched = classify_recorded(dup, {"duplicate_pending_count": 1,
                                          "duplicate_request_ids": ["same", "same"]})
        self.assertEqual(matched["status"], "MATCH")
        echoed = classify_recorded(dup, {"duplicate_pending_count": 1,
                                         "duplicate_request_ids": ["a", "b"]})
        self.assertEqual(echoed["status"], "MATCH_PARTIAL")
        self.assertEqual(echoed["drifts"], ["DUP-CREATE-ID-ECHO"])
        missing = classify_recorded(dup, {"unrelated": 1})
        self.assertEqual(missing["status"], "NOT_OBSERVABLE")

    def test_classifier_fail_closed_on_unknown_evaluator(self):
        leaf = {"obligation_id": "x", "evaluator_key": "no_such_key", "recorded_keys": ()}
        with self.assertRaises(RunStateError):
            classify_recorded(leaf, {})

    def test_totals_accounting(self):
        rows = [
            {"status": "MATCH"}, {"status": "MATCH_PARTIAL"},
            {"status": "NOT_OBSERVABLE"}, {"status": "DRIFT_DISCLOSED"},
        ]
        totals = classification_totals(rows)
        self.assertEqual(totals["total"], 4)
        self.assertEqual(totals["MATCH"], 1)
        self.assertEqual(sorted(CLASSIFICATION_STATUSES),
                         sorted(["MATCH", "MATCH_PARTIAL", "DRIFT_DISCLOSED", "NOT_OBSERVABLE"]))


class TestDensityClassificationTruthTable(unittest.TestCase):
    def _leaves(self):
        return {leaf.obligation_id: leaf.as_dict() for leaf in build_density_obligations()}

    def test_synthetic_truth_table_closes_22(self):
        leaves = self._leaves()
        projections = {
            "admission_branches.unknown_bot": ERROR_ADMISSION_DATA,
            "admission_branches.protected_no_edge": ERROR_ADMISSION_DATA,
            "admission_branches.protected_no_edge_human": ERROR_ADMISSION_DATA,
            "self_add": ERROR_ADMISSION_DATA,
            "unknown_bot_create": ERROR_ADMISSION_DATA,
            "duplicate": {"duplicate_pending_count": 1, "duplicate_request_ids": ["a", "a"]},
            "visibility_writes.private": None,
            "visibility_writes.public": None,
            "private_create": CONNECT_DATA,
            "private_discovery": CONNECT_DATA,
            "public_discovery": CONNECT_DATA,
            "role_branch": CONNECT_DATA,
            "organization_branch": CONNECT_DATA,
            "create_request_annex": None,
            "pre_decision_listings": None,
            "pair": None,
            "accept": {"accepted_request_id": "req-one"},
            "edges": {"edge_id": 18932, "mutual": True},
            "friend_lists.ceo": None,
            "friend_lists.eng": None,
            "admissions": {"admissions": {
                "ceo->eng": {"allowed": True, "reason": "ok", "source": "edge_grant"},
                "eng->ceo": {"allowed": True, "reason": "ok", "source": "edge_grant"},
            }},
            "all_direction_listings": None,
        }
        expected = {
            "admission_branches.unknown_bot": "MATCH",
            "admission_branches.protected_no_edge": "MATCH",
            "admission_branches.protected_no_edge_human": "MATCH",
            "self_add": "MATCH",
            "unknown_bot_create": "MATCH",
            "duplicate": "MATCH",
            "visibility_writes.private": "NOT_OBSERVABLE",
            "private_create": "MATCH_PARTIAL",
            "private_discovery": "MATCH",
            "visibility_writes.public": "NOT_OBSERVABLE",
            "public_discovery": "MATCH_PARTIAL",
            "role_branch": "MATCH",
            "organization_branch": "MATCH_PARTIAL",
            "create_request_annex": "NOT_OBSERVABLE",
            "pre_decision_listings": "NOT_OBSERVABLE",
            "pair": "NOT_OBSERVABLE",
            "accept": "MATCH_PARTIAL",
            "edges": "MATCH_PARTIAL",
            "friend_lists.ceo": "NOT_OBSERVABLE",
            "friend_lists.eng": "NOT_OBSERVABLE",
            "admissions": "MATCH",
            "all_direction_listings": "NOT_OBSERVABLE",
        }
        seen = 0
        for leaf in build_density_obligations():
            row = classify_recorded(leaf.as_dict(), projections.get(leaf.evaluator_key))
            self.assertEqual(row["status"], expected[leaf.evaluator_key], leaf.obligation_id)
            seen += 1
        self.assertEqual(seen, 22)

    def test_drift_postures_are_disclosed_not_charged(self):
        leaves = self._leaves()
        drifted_admission = dict(ERROR_ADMISSION_DATA)
        drifted_admission["admission_branches"] = {"unknown_bot": "edge_grant"}
        row = classify_recorded(leaves["EDGE-ERROR-ADMISSION.bot-not-found-admission"], drifted_admission)
        self.assertEqual(row["status"], "DRIFT_DISCLOSED")
        self.assertEqual(row["drifts"], ["ADMISSION-REASON-DRIFT"])
        drifted_private = dict(CONNECT_DATA)
        drifted_private["private_create_defect"] = {"http_status": 403}
        row = classify_recorded(leaves["EDGE-CONNECT-DISCOVER.private-target-create-rejected"], drifted_private)
        self.assertEqual(row["status"], "MATCH")
        row = classify_recorded(leaves["EDGE-CONNECT-DISCOVER.private-target-create-rejected"], CONNECT_DATA)
        self.assertEqual(row["status"], "MATCH_PARTIAL")
        self.assertEqual(row["drifts"], ["PD-PRIVATE-CREATE-500"])


@unittest.skipUnless(EVIDENCE_ROOT.is_dir(), "recorded preprod evidence root not attached")
class TestDensityRecordedReceipts(unittest.TestCase):
    def test_recorded_truth_table_and_identity_cross_check(self):
        arms = density_preprod_arm.density_preprod_arms(EVIDENCE_ROOT)
        self.assertEqual(sorted(arms), sorted(DENSITY_CASE_KEYS))
        family = {"MATCH": 0, "MATCH_PARTIAL": 0, "DRIFT_DISCLOSED": 0, "NOT_OBSERVABLE": 0}
        for case_key, arm in arms.items():
            self.assertEqual(arm["family_receipt_availability"], "ATTACHED", case_key)
            self.assertEqual(arm["conflicts"], [], case_key)
            self.assertEqual(arm["preprod_status"], PREPROD_NOT_RUN)
            self.assertEqual(arm["enrollment"], ENROLLMENT_NOT_ENROLLED)
            self.assertEqual(arm["remote_validation"], "NOT_RUN")
            for round_row in arm["rounds"]:
                self.assertTrue(round_row["envelope"]["envelope_verified"], case_key)
            for row in arm["leaf_classifications"]:
                family[row["status"]] += 1
        self.assertEqual(family["MATCH"], 8)
        self.assertEqual(family["MATCH_PARTIAL"], 7)
        self.assertEqual(family["NOT_OBSERVABLE"], 7)
        self.assertEqual(family["DRIFT_DISCLOSED"], 0)
        read_arm = arms[FRIEND_REQUEST_AND_LIST_CASE_KEY]
        self.assertTrue(read_arm["identity_cross_check"]["driver_observed_on_this_leg"])
        self.assertEqual(read_arm["identity_cross_check"]["observed_actor_ids"], [DRIVER_BOT_ID])
        mutual_arm = arms[MUTUAL_AUTO_APPROVE_CASE_KEY]
        self.assertTrue(mutual_arm["identity_cross_check"]["member_observed_on_this_leg"])
        self.assertIn(MEMBER_BOT_ID, mutual_arm["identity_cross_check"]["observed_actor_ids"])
        annex = read_arm["leaf_classifications"][0]
        self.assertEqual(annex["obligation_id"], "EDGE-FRIEND-CONNECTIONS.create-request")
        self.assertEqual(annex["status"], "MATCH_PARTIAL")
        self.assertEqual(annex["citation"]["create_rows_total"], 6)
        self.assertEqual(annex["citation"]["create_rows_201"], 3)
        self.assertTrue(annex["citation"]["create_rows_request_id_echoed"])

    def test_preprod_arms_render_with_evidence(self):
        arm = density_preprod_arm.density_preprod_arm_receipt(ERROR_ADMISSION_CASE_KEY, EVIDENCE_ROOT)
        self.assertEqual(arm["classification_totals"],
                         {"MATCH": 5, "MATCH_PARTIAL": 1, "DRIFT_DISCLOSED": 0,
                          "NOT_OBSERVABLE": 0, "total": 6})


class TestDensityLocalArm(unittest.TestCase):
    def test_local_model_ready_offline(self):
        arm = density_local_arm.density_local_arm_receipt(ERROR_ADMISSION_CASE_KEY)
        self.assertEqual(arm["status"], "LOCAL_MODEL_READY")
        self.assertEqual(arm["conflicts"], [])
        self.assertTrue(arm["seam_presence"]["verified"])
        self.assertEqual(arm["leaves_in_arm"], 6)
        self.assertEqual(arm["recorded_owned_local_round"]["availability"], "NOT_ATTACHED")
        for row in arm["seam_literal_rows"]:
            self.assertIn(row["posture"], ("SOURCE_LITERAL", "FOLDED_STRING_CONSTANTS"))

    def test_frozen_binding_projection(self):
        binding = density_local_arm.frozen_binding_projection(ERROR_ADMISSION_CASE_KEY)
        self.assertEqual(binding["case_id"], "LOCAL-EDGE-ERROR-ADMISSION-BRANCHES-01")
        self.assertEqual(binding["frozen_requirement_ids"],
                         ["EDGE_FRIEND_MUTATIONS.error-admission-branches"])
        self.assertEqual(binding["binding"]["source_revision"],
                         binding["binding"]["sources"]["edge_permission.sh"])
        read_binding = density_local_arm.frozen_binding_projection(FRIEND_REQUEST_AND_LIST_CASE_KEY)
        self.assertEqual(read_binding["parent_id"], "test_ep_friend_connections_friend_request_and_list")
        self.assertEqual([item["id"] for item in read_binding["binding"]["requirements"]],
                         ["EDGE-FRIEND-CONNECTIONS.list", "EDGE-FRIEND-CONNECTIONS.list-repeat"])
        self.assertEqual(read_binding["annex_seam"]["method"], "_create_request")

    def test_family_local_arms_ready(self):
        arms = density_local_arm.density_local_arms()
        self.assertEqual(len(arms), 5)
        for case_key, arm in arms.items():
            self.assertEqual(arm["status"], "LOCAL_MODEL_READY", case_key)

    @unittest.skipUnless((EVIDENCE_ROOT / "20261031-migration-wpc-local").is_dir(),
                         "wpc owned-local round not attached")
    def test_recorded_wpc_round_verified_offline(self):
        arm = density_local_arm.density_local_arm_receipt(ERROR_ADMISSION_CASE_KEY, EVIDENCE_ROOT)
        recorded = arm["recorded_owned_local_round"]
        self.assertEqual(recorded["availability"], "ATTACHED")
        self.assertEqual(recorded["round_label"], WPC_ROUND_LABEL)
        self.assertTrue(recorded["verified"])
        self.assertEqual(recorded["conflicts"], [])
        self.assertTrue(recorded["junit"]["all_pass"])
        self.assertEqual(len(recorded["junit"]["cases"]), 9)
        self.assertEqual(recorded["read_slice_disposition"], "LEG_ABSENT")
        self.assertEqual(recorded["flow_suite"]["suite_status"],
                         "LOCAL_EDGE_FRIEND_MUTATIONS_PASS")

    def test_recorded_round_conflict_maps_local_model_conflict(self):
        with tempfile.TemporaryDirectory() as base:
            root = _wpc_round_root(Path(base), runner_status="FAIL")
            arm = density_local_arm.density_local_arm_receipt(ERROR_ADMISSION_CASE_KEY, root)
            self.assertEqual(arm["status"], "LOCAL_MODEL_CONFLICT")
            self.assertTrue(any("junit" in item for item in arm["conflicts"]))
            arm_no_root = density_local_arm.density_local_arm_receipt(ERROR_ADMISSION_CASE_KEY, root / "missing")
            self.assertEqual(arm_no_root["status"], "LOCAL_MODEL_CONFLICT")


class TestDensityDualArms(unittest.TestCase):
    def test_arms_render_without_evidence(self):
        for case_key in DENSITY_CASE_KEYS:
            local_arm = density_local_arm.density_local_arm_receipt(case_key)
            preprod_arm = density_preprod_arm.density_preprod_arm_receipt(case_key)
            self.assertEqual(local_arm["status"], "LOCAL_MODEL_READY")
            self.assertEqual(preprod_arm["family_receipt_availability"], "NOT_ATTACHED")
            self.assertEqual(preprod_arm["preprod_status"], PREPROD_NOT_RUN)
            self.assertEqual(preprod_arm["enrollment"], ENROLLMENT_NOT_ENROLLED)
            self.assertEqual(preprod_arm["conflicts"], [])
            self.assertFalse(json.dumps(preprod_arm).find("NOT_ENROLLED") == -1)

    def test_arm_envelope_isolation(self):
        local_arm = density_local_arm.density_local_arm_receipt(ERROR_ADMISSION_CASE_KEY)
        preprod_arm = density_preprod_arm.density_preprod_arm_receipt(ERROR_ADMISSION_CASE_KEY)
        local_serial = json.dumps(local_arm)
        preprod_serial = json.dumps(preprod_arm)
        self.assertNotIn("preprod_status", local_serial)
        self.assertNotIn("live_fire_observation", local_serial)
        self.assertNotIn("local_status", preprod_serial)
        self.assertNotIn("recorded_evidence", preprod_serial)

    def test_envelope_degenerate_priority(self):
        profile = _profile()
        local_ready = {"status": "LOCAL_MODEL_READY"}
        local_conflict = {"status": "LOCAL_MODEL_CONFLICT"}
        self.assertEqual(
            density_case_run.degenerate_status(local_conflict, {"family_receipt_availability": "ATTACHED", "content_conflicts": []}),
            "LOCAL_MODEL_CONFLICT")
        self.assertEqual(
            density_case_run.degenerate_status(local_ready, {"family_receipt_availability": "NO_FAMILY_RECEIPTS", "content_conflicts": []}),
            "BLOCKED_EVIDENCE_CONFLICT")
        self.assertEqual(
            density_case_run.degenerate_status(local_ready, {"family_receipt_availability": "PARTIAL", "content_conflicts": []}),
            "PREPROD_RECEIPT_DRIFT")
        self.assertEqual(
            density_case_run.degenerate_status(local_ready, {"family_receipt_availability": "ATTACHED", "content_conflicts": []}),
            ENVELOPE_STATUS)

    def test_case_run_gates(self):
        with tempfile.TemporaryDirectory() as base:
            profile_path = _write(Path(base) / "profile.json", _profile())
            state_dir = Path(base) / "state"
            kwargs = dict(profile_path=profile_path, case_key=ERROR_ADMISSION_CASE_KEY,
                          state_dir=state_dir, run_id=RUN_ID, confirm_run_id="other",
                          allow_write=True)
            with self.assertRaises(ConfigError):
                density_case_run.dual_edge_permission_density_case_run(**kwargs)
            kwargs["confirm_run_id"] = RUN_ID
            with self.assertRaises(ConfigError):
                density_case_run.dual_edge_permission_density_case_run(
                    allow_write=False, **{k: v for k, v in kwargs.items() if k != "allow_write"})
            with self.assertRaises(RunStateError):
                density_case_run.dual_edge_permission_density_case_run(
                    case_key="not-a-density-case", **{k: v for k, v in kwargs.items()
                                                      if k != "case_key"})
            with self.assertRaises(ConfigError):
                density_case_run.dual_edge_permission_density_case_run(
                    evidence_root=Path(base) / "missing-root",
                    **{k: v for k, v in kwargs.items() if k != "evidence_root"})
            with self.assertRaises(ConfigError):
                density_case_run.dual_edge_permission_density_case_run(
                    profile_path=_write(Path(base) / "bad-profile.json", _profile(extra=1)),
                    **{k: v for k, v in kwargs.items() if k != "profile_path"})
            with self.assertRaises(ConfigError):
                density_case_run.load_density_profile(Path(base) / "absent.json")

    def test_case_run_happy_path_offline(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            profile_path = _write(root / "profile.json", _profile())
            state_dir = root / "state"
            envelope = density_case_run.dual_edge_permission_density_case_run(
                profile_path=profile_path, case_key=ERROR_ADMISSION_CASE_KEY,
                state_dir=state_dir, run_id=RUN_ID, confirm_run_id=RUN_ID,
                allow_write=True, evidence_root=None)
            self.assertEqual(envelope["status"], ENVELOPE_STATUS)
            self.assertEqual(envelope["local_status"], "LOCAL_MODEL_READY")
            self.assertEqual(envelope["preprod_status"], PREPROD_NOT_RUN)
            self.assertEqual(envelope["enrollment"], ENROLLMENT_NOT_ENROLLED)
            self.assertFalse(envelope["grants_release_credit"])
            self.assertEqual(envelope["remote_validation"], "NOT_RUN")
            self.assertEqual(envelope["run_id"], RUN_ID)
            self.assertEqual(stat.S_IMODE(state_dir.stat().st_mode) & 0o777, 0o700)
            binding = read_state(state_dir / "dual-edge-permission-density-binding.json")
            run_state = read_state(state_dir / "dual-edge-permission-density-run.json")
            self.assertEqual(binding["contract"], "dual-edge-permission-density-binding-v1")
            self.assertEqual(run_state["contract"], "dual-edge-permission-density-case-run-v1")
            self.assertEqual(binding["case_key"], ERROR_ADMISSION_CASE_KEY)
            self.assertEqual(len(binding["density_leaf_ids"]), 6)
            self.assertEqual(len(run_state["density_leaf_anchors"]), 6)
            self.assertEqual(len(run_state["frozen_linkage"]["frozen_obligation_refs"]), 6)
            self.assertEqual(run_state["obligation_roster"]["parsed_total"], 28)
            self.assertEqual(run_state["registration"]["optional_density_command"], OPTIONAL_DENSITY_COMMAND)
            row = run_state["registration"]["executor_migration_row"]
            expected_parent = density_case_row(ERROR_ADMISSION_CASE_KEY)["parent_id"]
            expected_notes = [density_registration_proposal.PROPOSED_EXECUTOR_MIGRATION_NOTES[
                expected_parent]]
            self.assertEqual((row["mode"], row["preprod_verdict"], row["gates"], row["notes"]),
                             ("DUAL", "PASS", [], expected_notes))

    @unittest.skipUnless(EVIDENCE_ROOT.is_dir(), "recorded preprod evidence root not attached")
    def test_case_run_happy_path_with_evidence(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            profile_path = _write(root / "profile.json", _profile())
            envelope = density_case_run.dual_edge_permission_density_case_run(
                profile_path=profile_path, case_key=MUTUAL_AUTO_APPROVE_CASE_KEY,
                state_dir=root / "state", run_id=RUN_ID, confirm_run_id=RUN_ID,
                allow_write=True, evidence_root=EVIDENCE_ROOT)
            self.assertEqual(envelope["status"], ENVELOPE_STATUS)
            self.assertEqual(envelope["preprod_arm"]["family_receipt_availability"], "ATTACHED")
            self.assertEqual(envelope["preprod_arm"]["classification_totals"],
                             {"MATCH": 1, "MATCH_PARTIAL": 2, "DRIFT_DISCLOSED": 0,
                              "NOT_OBSERVABLE": 4, "total": 7})

    def test_case_run_degenerate_mapping(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            profile_path = _write(root / "profile.json", _profile())
            empty_root = root / "empty-evidence"
            empty_root.mkdir()
            envelope = density_case_run.dual_edge_permission_density_case_run(
                profile_path=profile_path, case_key=ERROR_ADMISSION_CASE_KEY,
                state_dir=root / "state-blocked", run_id=RUN_ID, confirm_run_id=RUN_ID,
                allow_write=True, evidence_root=empty_root)
            self.assertEqual(envelope["status"], "BLOCKED_EVIDENCE_CONFLICT")
            drift_root = root / "drift-evidence"
            receipt = _mutation_receipt("LOCAL-EDGE-DRIFTED-01",
                                        "test_ep_error_and_admission_branches",
                                        "EDGE_FRIEND_MUTATIONS.error-admission-branches",
                                        ERROR_ADMISSION_DATA)
            _write(drift_root / MUTATION_R5 / "error-admission-branches-stdout.json", receipt)
            envelope = density_case_run.dual_edge_permission_density_case_run(
                profile_path=profile_path, case_key=ERROR_ADMISSION_CASE_KEY,
                state_dir=root / "state-drift", run_id=RUN_ID, confirm_run_id=RUN_ID,
                allow_write=True, evidence_root=drift_root)
            self.assertEqual(envelope["status"], "PREPROD_RECEIPT_DRIFT")
            wpc_root = _wpc_round_root(root / "wpc-bad", runner_status="FAIL")
            envelope = density_case_run.dual_edge_permission_density_case_run(
                profile_path=profile_path, case_key=ERROR_ADMISSION_CASE_KEY,
                state_dir=root / "state-local", run_id=RUN_ID, confirm_run_id=RUN_ID,
                allow_write=True, evidence_root=wpc_root)
            self.assertEqual(envelope["status"], "LOCAL_MODEL_CONFLICT")
            self.assertEqual(envelope["local_status"], "LOCAL_MODEL_CONFLICT")


class TestDensityReporting(unittest.TestCase):
    def test_family_summary_without_evidence(self):
        summary = density_reporting.family_summary()
        self.assertEqual(len(summary["cases"]), 5)
        self.assertEqual(summary["totals"]["parsed_total"], 28)
        self.assertEqual(summary["totals"]["frozen_existing_total"], 6)
        self.assertEqual(summary["totals"]["density_leaves_total"], 22)
        self.assertEqual(summary["totals"]["projected_superset_total"], 28)
        self.assertNotIn("classification", summary["totals"])
        self.assertFalse(summary["grants_release_credit"])
        self.assertFalse(summary["honest_counts"]["enrolled"])
        self.assertEqual(summary["status"], ENVELOPE_STATUS)
        self.assertTrue(summary["material_gaps"])
        self.assertTrue(summary["product_defects_on_file"])

    @unittest.skipUnless(EVIDENCE_ROOT.is_dir(), "recorded preprod evidence root not attached")
    def test_family_summary_with_evidence(self):
        summary = density_reporting.family_summary(EVIDENCE_ROOT)
        self.assertEqual(summary["totals"]["classification"],
                         {"MATCH": 8, "MATCH_PARTIAL": 7, "DRIFT_DISCLOSED": 0,
                          "NOT_OBSERVABLE": 7})
        self.assertEqual(summary["totals"]["classification_total"], 22)
        self.assertEqual(len(summary["classified_leaf_buckets"]["MATCH"]), 8)
        self.assertEqual(len(summary["classified_leaf_buckets"]["MATCH_PARTIAL"]), 7)
        self.assertEqual(len(summary["classified_leaf_buckets"]["NOT_OBSERVABLE"]), 7)


class TestDensityRegistrationProposal(unittest.TestCase):
    def test_proposal_stays_describe_only(self):
        proposal = density_registration_proposal.registration_proposal()
        self.assertEqual(proposal["proposal_status"], "DESCRIBE_ONLY")
        self.assertFalse(proposal["applied"])
        self.assertEqual(proposal["shared_face_files_touched"], [])
        rows = proposal["required_delta"]["executor_migration_rows"]
        self.assertEqual(sorted(rows), sorted(PARENTS))
        for parent, delta in rows.items():
            self.assertEqual(delta["executor_migration"]["mode"], "DUAL")
            self.assertEqual(delta["executor_migration"]["preprod_verdict"], "PASS")
            self.assertEqual(len(delta["executor_migration"]["notes"]), 1)
        counts = proposal["inventory_count_proposal"]
        self.assertEqual(counts["total_parsed_denominator"], 28)
        self.assertEqual(counts["total_frozen_existing"], 6)
        self.assertEqual(counts["total_density_leaves"], 22)
        for row in counts["rows"]:
            self.assertEqual(row["landing_status"], "pending-landing")
        materials = {item["id"] for item in proposal["materials"]}
        self.assertIn("M-DENSITY-RECEIPT-LEAFKEYS", materials)
        self.assertIn("M-PREREQ", materials)


class TestDensityGuardAndHygiene(unittest.TestCase):
    def _guard(self):
        candidate = Path(__file__).resolve()
        for parent in candidate.parents:
            guess = parent / "scripts" / "ci" / "e2e_runner_case_guard.py"
            if guess.is_file():
                spec = importlib.util.spec_from_file_location("e2e_runner_case_guard", guess)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
        self.skipTest("e2e_runner_case_guard.py not discoverable")

    def _new_files(self):
        package = ROOT / "bcs_e2e" / "edge_permission_backlog"
        names = ("density_obligations.py", "density_contract.py", "density_eval.py",
                 "density_local_arm.py", "density_preprod_arm.py", "density_case_run.py",
                 "density_reporting.py", "density_registration_proposal.py")
        files = [package / name for name in names]
        files.append(Path(__file__))
        for item in files:
            self.assertTrue(item.is_file(), item)
            lines = len(item.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(lines, 1000, str(item))
        return files

    def test_guard_inspect_clean(self):
        guard = self._guard()
        for item in self._new_files():
            self.assertEqual(guard.inspect(item), [], str(item))

    def test_credential_hygiene(self):
        profile = _profile()
        envelope = density_case_run.build_density_envelope(
            case_key=ERROR_ADMISSION_CASE_KEY, profile=profile, run_id=RUN_ID)
        serialized = json.dumps(envelope, sort_keys=True)
        self.assertNotIn("BCS_E2E_HUMAN_COOKIE=", serialized)
        self.assertNotIn("BCS_E2E_STATION_BOT_TOKEN=", serialized)
        self.assertEqual(envelope["credential_kind"], "offline_dual_receipt_no_transport")

    def test_supplementary_faces_unchanged(self):
        projection = existing_envelope_projection()
        self.assertEqual(projection["read_family"]["case_key_count"], 4)
        self.assertEqual(projection["mutation_family"]["case_key_count"], 7)
        self.assertEqual(projection["backlog_family"]["case_key_count"], 2)
        self.assertEqual(projection["density_overlay"]["optional_command_registration"],
                         "OPTIONAL_UNREGISTERED_DESCRIBE_ONLY")


if __name__ == "__main__":
    unittest.main()
