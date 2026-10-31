"""Focused offline regression for wave E2b row #20 result-queue density.

The suite validates the source-anchored obligation backfill for
story_state_machine_result_queue (executor row index 20): 18 frozen
RESULT-QUEUE.* contract parents plus 34 AST-rebuilt in-script assertion
leaves (RQ.F01..RQ.F34), 52 parsed with residual_count_gap=0.  It checks
verbatim disk-byte anchoring, SHA-256 fail-close drift detection for the
story and both companions, ordered identity with the owned-local executor
catalog, dynamic-family traceability, the honest offline replay (local green
projection, missing/tampered observations, honest preprod NOT_RUN with
named design gaps and the REMOTE_UNSUPPORTED cap), the describe-only
registration proposal arithmetic against the on-disk inventory and
coverage-map faces, and package hygiene.  Nothing here launches the owned
runtime, opens a transport, resolves a credential, or writes a registration.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import re
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bcs_e2e.dynamic_family import dynamic_family_contract  # noqa: E402
from bcs_e2e.local_result_queue_contract import (  # noqa: E402
    ASSERTION_IDS, MARKER_PREFIX, MARKER_SUFFIX, REQUIREMENTS,
    REQUIRED_STORY_ARGUMENTS)
from bcs_e2e.result_queue_density import (  # noqa: E402
    DENSITY_OBLIGATION_COUNT, PARSED_OBLIGATION_COUNT, PARENT, PARENT_OBLIGATION_COUNT,
    RESIDUAL_COUNT_GAP, STATUS_BLOCKED_INVENTORIED, STATUS_FAIL, STATUS_NOT_RUN,
    STATUS_PASS, density_plan, mock_observations, obligations, project,
    registration_proposal)
from bcs_e2e.result_queue_density.registration_proposal import (  # noqa: E402
    MD_ROW, NAMED_GAP_DETAILS, NOTE, PROPOSAL_TOKEN, QUEUE_BASIS,
    describe_only_mainline_fragment)
from bcs_e2e.run_state import RunStateError  # noqa: E402

import bcs_e2e.inventory as inventory_module  # noqa: E402

RQ_OBL = importlib.import_module("bcs_e2e.result_queue_density.obligations")
RQ_REPLAY = importlib.import_module("bcs_e2e.result_queue_density.replay")
FAMILY = dynamic_family_contract("result_queue")
STORY_DISK = RQ_OBL.STORY_SOURCE
CONTRACT_DISK = RQ_OBL.ROOT / RQ_OBL.MIGRATION_CONTRACT
COVERAGE_MAP_DISK = ROOT / "coverage-map" / "coverage-map-20261031.json"
PACKAGE_DIR = Path(RQ_OBL.__file__).resolve().parent
ZERO_ATOM_PARENT = "RESULT-QUEUE.delivery-policy-replacement"

# Guard replica constants are assembled from pieces so this file itself
# never carries the forbidden literals as single string constants.
_BEARER = "Bearer" + " "
_FORBIDDEN_TEXT = (
    "X-Avernet-" + "Principal", "X-E2E-" + "Run-ID",
    "Authorization: " + "Bearer", _BEARER, "curl" + " ", "pkill" + " ",
    "kill" + " -", "DROP " + "TABLE", "TRUNCATE " + "TABLE", "DELETE " + "FROM")
_FORBIDDEN_IMPORTS = {"requests", "httpx", "urllib", "urllib.request", "socket",
                      "subprocess", "sqlite3", "pymysql", "mysql", "psycopg", "shlex"}
_FORBIDDEN_CALLS = {"urlopen", "create_connection", "Popen", "run", "system", "kill", "popen"}


def _guard_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = node.names[0].name if isinstance(node, ast.Import) else (
                node.module or "")
            if any(module == name or module.startswith(name + ".")
                   for name in _FORBIDDEN_IMPORTS):
                found.append(f"{path}:{node.lineno}: forbidden import {module}")
        elif isinstance(node, ast.Call):
            function = (node.func.id if isinstance(node.func, ast.Name)
                        else node.func.attr if isinstance(node.func, ast.Attribute) else "")
            if function in _FORBIDDEN_CALLS:
                found.append(f"{path}:{node.lineno}: forbidden call {function}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for text in _FORBIDDEN_TEXT:
                if text in node.value:
                    found.append(f"{path}:{node.lineno}: forbidden literal")
    return found


FROZEN_PER_PARENT_COUNTS = {
    "RESULT-QUEUE.human-registration": 2, "RESULT-QUEUE.provider-registration": 1,
    "RESULT-QUEUE.recipient-bot-pair": 1, "RESULT-QUEUE.recipient-visibility-harness": 2,
    "RESULT-QUEUE.delivery-policy-baseline": 1,
    "RESULT-QUEUE.delivery-policy-replacement": 0,
    "RESULT-QUEUE.policy-update-cas": 2, "RESULT-QUEUE.provider-callout-harness": 1,
    "RESULT-QUEUE.final-callback-admission": 2, "RESULT-QUEUE.result-delivery-ledger": 1,
    "RESULT-QUEUE.group-and-session": 2, "RESULT-QUEUE.state-machine-definition": 1,
    "RESULT-QUEUE.state-machine-run-admission": 1,
    "RESULT-QUEUE.one-shot-node-delivery": 1, "RESULT-QUEUE.durable-result-rows": 2,
    "RESULT-QUEUE.scheduled-result-delivery": 3,
    "RESULT-QUEUE.bound-context-and-unique-history": 3,
    "RESULT-QUEUE.run-completion-and-cleanup-disposition": 8,
}


class ResultQueueDensityContractTests(unittest.TestCase):
    def test_accounting_is_18_parents_plus_34_atoms_with_zero_residual(self):
        rows = obligations()
        self.assertEqual(len(rows), PARSED_OBLIGATION_COUNT)
        self.assertEqual(PARSED_OBLIGATION_COUNT, 52)
        self.assertEqual(sum(row.level == "PARENT" for row in rows), PARENT_OBLIGATION_COUNT)
        self.assertEqual(sum(row.level == "DENSITY" for row in rows), DENSITY_OBLIGATION_COUNT)
        self.assertEqual((PARENT_OBLIGATION_COUNT, DENSITY_OBLIGATION_COUNT), (18, 34))
        self.assertEqual(PARSED_OBLIGATION_COUNT - PARENT_OBLIGATION_COUNT,
                         DENSITY_OBLIGATION_COUNT)
        self.assertEqual(RESIDUAL_COUNT_GAP, 0)
        self.assertEqual(len({row.obligation_id for row in rows}), PARSED_OBLIGATION_COUNT)

    def test_parent_envelope_matches_frozen_contract_requirements(self):
        rows = obligations()
        parents = [row for row in rows if row.level == "PARENT"]
        self.assertEqual([row.obligation_id for row in parents], list(REQUIREMENTS))
        contract = json.loads(CONTRACT_DISK.read_text(encoding="utf-8"))
        contract_rows = {row["id"]: row for row in contract["requirements"]}
        lines = STORY_DISK.read_text(encoding="utf-8").splitlines()
        for row in parents:
            face = contract_rows[row.obligation_id]
            self.assertEqual((row.line_start, row.line_end), tuple(face["story_lines"]))
            self.assertEqual(row.expectation, face["expectation"])
            self.assertEqual(row.method, "story_stage")
            self.assertEqual(row.field, "stage")
            self.assertEqual(row.comparator, "stage")
            self.assertIsNone(row.executor_site_id)
            self.assertEqual(row.quote, "\n".join(lines[row.line_start - 1:row.line_end]))
            self.assertTrue(row.quote)
            self.assertTrue(row.located)
            self.assertEqual(row.source_file, RQ_OBL.SOURCE_FILE)
            self.assertEqual(row.source_line, row.line_start)
            self.assertEqual(row.source_quote, row.quote)

    def test_leaf_ids_and_executor_site_catalog_are_ordered_identical(self):
        rows = obligations()
        leaves = [row for row in rows if row.level == "DENSITY"]
        self.assertEqual([row.obligation_id for row in leaves],
                         [f"RQ.F{index:02d}" for index in range(1, 35)])
        self.assertEqual([row.executor_site_id for row in leaves], list(ASSERTION_IDS))
        self.assertEqual(tuple(ASSERTION_IDS), FAMILY.assertion_ids)
        self.assertEqual(len(FAMILY.assertion_ids), DENSITY_OBLIGATION_COUNT)
        self.assertEqual(tuple(REQUIREMENTS), FAMILY.requirement_ids)
        owners = FAMILY.assertion_requirement_ids
        self.assertEqual(set(owners), set(ASSERTION_IDS))
        self.assertTrue(all(len(sites) == 1 for sites in owners.values()))
        for leaf in leaves:
            self.assertEqual(leaf.parent_obligation, owners[leaf.executor_site_id][0])
            self.assertIsNotNone(leaf.executor_site_id)

    def test_per_parent_atom_counts_match_dynamic_family_traceability(self):
        plan = density_plan()
        rows = obligations()
        leaves = [row for row in rows if row.level == "DENSITY"]
        live = Counter(leaf.parent_obligation for leaf in leaves)
        traceability = FAMILY.traceability()
        self.assertEqual(plan["per_parent_atom_counts"],
                         {parent: traceability[parent]["assertion_site_count"]
                          for parent in REQUIREMENTS})
        self.assertEqual(plan["per_parent_atom_counts"],
                         {**dict(live), ZERO_ATOM_PARENT: 0})
        self.assertEqual(set(plan["per_parent_atom_counts"]), set(REQUIREMENTS))
        self.assertEqual(plan["per_parent_atom_counts"], FROZEN_PER_PARENT_COUNTS)
        self.assertEqual(FROZEN_PER_PARENT_COUNTS[ZERO_ATOM_PARENT], 0)
        self.assertEqual(sum(live.values()), DENSITY_OBLIGATION_COUNT)
        self.assertEqual(plan["parent_ids"], list(REQUIREMENTS))
        self.assertEqual(plan["leaf_ids"], [leaf.obligation_id for leaf in leaves])

    def test_every_anchor_matches_disk_bytes_and_span_is_contained(self):
        rows = obligations()
        contract = json.loads(CONTRACT_DISK.read_text(encoding="utf-8"))
        story_lines = {row["id"]: tuple(row["story_lines"]) for row in contract["requirements"]}
        disk_lines = STORY_DISK.read_text(encoding="utf-8").splitlines()
        for leaf in (row for row in rows if row.level == "DENSITY"):
            span = story_lines[leaf.parent_obligation]
            self.assertTrue(span[0] <= leaf.line_start <= leaf.line_end <= span[1],
                            leaf.obligation_id)
            self.assertEqual(leaf.quote,
                             "\n".join(disk_lines[leaf.line_start - 1:leaf.line_end]),
                            leaf.obligation_id)
            self.assertTrue(leaf.quote)
            self.assertTrue(leaf.located)
            self.assertEqual(leaf.source_file, RQ_OBL.SOURCE_FILE)
        class_low, class_high = RQ_OBL.STORY_LINE_SPAN
        for row in rows:
            self.assertTrue(class_low <= row.line_start <= row.line_end <= class_high)

    def test_story_span_sha_and_companion_digests_are_frozen(self):
        raw = STORY_DISK.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), RQ_OBL.SOURCE_SHA256)
        contract = json.loads(CONTRACT_DISK.read_text(encoding="utf-8"))
        self.assertEqual(contract["sources"]["result_queue_story.py"],
                         RQ_OBL.SOURCE_SHA256)
        for name, digest in RQ_OBL.COMPANION_SOURCES.items():
            self.assertEqual(contract["sources"][name], digest)
        revisions = contract["source_file_revisions"]
        self.assertEqual(set(revisions),
                         set(RQ_OBL.COMPANION_PATHS) | {"result_queue_story.py"})
        self.assertTrue(all(re.fullmatch("[0-9a-f]{40}", value)
                            for value in revisions.values()))
        tree = ast.parse(raw.decode("utf-8"), filename=str(STORY_DISK))
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        self.assertEqual((classes[0].lineno, classes[0].end_lineno),
                         tuple(RQ_OBL.STORY_LINE_SPAN))
        self.assertEqual(RQ_OBL.STORY_LINE_SPAN, (14, 140))
        self.assertEqual(FAMILY.local_status, "LOCAL_RESULT_QUEUE_PASS")
        self.assertEqual(FAMILY.legacy_id, PARENT)
        self.assertEqual(FAMILY.command, "dual-result-queue-run")
        self.assertEqual(FAMILY.local_executor,
                         "bcs_e2e.local_result_queue_run.local_result_queue_run")
        self.assertEqual(FAMILY.canonical_status, "LOCAL_SUITE_PASS")
        self.assertEqual(FAMILY.preprod_initial_verdict, "NOT_RUN")

    def test_zero_atom_parent_disclosure_and_uncharged_probes(self):
        rows = obligations()
        leaves = [row for row in rows if row.level == "DENSITY"]
        plan = density_plan()
        self.assertEqual(set(RQ_OBL.ZERO_ATOM_PARENTS), {ZERO_ATOM_PARENT})
        charged = {leaf.parent_obligation for leaf in leaves}
        self.assertNotIn(ZERO_ATOM_PARENT, charged)
        self.assertEqual(set(REQUIREMENTS) - charged, set(RQ_OBL.ZERO_ATOM_PARENTS))
        stage_text = "\n".join(STORY_DISK.read_text(encoding="utf-8").splitlines()[40:46])
        self.assertIn('policy["flow_enabled"].update(', stage_text)
        self.assertIn('"max_running": 1', stage_text)
        self.assertIn('"max_queued": 20', stage_text)
        disclosure = RQ_OBL.ZERO_ATOM_PARENTS[ZERO_ATOM_PARENT]
        self.assertIn("story lines 41-46", disclosure)
        self.assertIn("RQ.F21..RQ.F23", disclosure)
        self.assertIn("RQ.F21..RQ.F23", NOTE)
        scheduled = [leaf.obligation_id for leaf in leaves
                     if leaf.parent_obligation == "RESULT-QUEUE.scheduled-result-delivery"]
        self.assertEqual(scheduled, ["RQ.F21", "RQ.F22", "RQ.F23"])
        story_text = STORY_DISK.read_text(encoding="utf-8")
        self.assertEqual(story_text.count("self.wait("), 7)
        self.assertEqual(story_text.count("print("), 1)
        uncharged = plan["not_charged_source_probes"]
        self.assertEqual(len(uncharged), 1)
        self.assertIn("seven dynamic wait predicates", uncharged[0])
        self.assertIn("marker print", uncharged[0])

    def test_story_argument_face_matches_contract(self):
        plan = density_plan()
        contract = json.loads(CONTRACT_DISK.read_text(encoding="utf-8"))
        self.assertEqual(plan["story_arguments"]["required"], list(REQUIRED_STORY_ARGUMENTS))
        self.assertEqual(list(contract["required_story_arguments"]),
                         list(REQUIRED_STORY_ARGUMENTS))
        self.assertEqual(plan["story_arguments"]["optional"], ["--mock-sink-url"])
        self.assertEqual(REQUIRED_STORY_ARGUMENTS, ("--base-url", "--mock-url", "--human"))

    def test_story_marker_and_mock_child_faces(self):
        plan = density_plan()
        contract = json.loads(CONTRACT_DISK.read_text(encoding="utf-8"))
        self.assertEqual(plan["story_marker"]["keys"], list(RQ_OBL.MARKER_KEYS))
        self.assertEqual(len(plan["story_marker"]["patterns"]), 1)
        self.assertEqual(contract["story_markers"], list(RQ_OBL.STORY_MARKERS))
        self.assertEqual(contract["source_marker_lines"], 1)
        story_text = STORY_DISK.read_text(encoding="utf-8")
        self.assertIn(MARKER_PREFIX, story_text)
        self.assertIn(MARKER_SUFFIX, story_text)
        self.assertIsNotNone(plan["story_marker"]["patterns"][0].search(
            plan["story_marker"]["marker"]))
        self.assertIn(plan["story_marker"]["marker"], story_text)
        self.assertEqual(plan["mock_child"],
                         {"script": "http_provider_judge_mock.py",
                          "ready_protocol": "ready-file", "expected_webhook_requests": 3})
        self.assertEqual(RQ_OBL.EXPECTED_WEBHOOKS, 3)

    def test_drift_fails_closed_on_story_bytes(self):
        pristine = STORY_DISK.read_text(encoding="utf-8")
        self.assertGreater(pristine.count("queued-final-result"), 0)
        with tempfile.TemporaryDirectory() as tmp:
            drifted = Path(tmp) / STORY_DISK.name
            drifted.write_text(pristine.replace("queued-final-result",
                                                "queued-final-result-drift"),
                               encoding="utf-8")
            with mock.patch.object(RQ_OBL, "STORY_SOURCE", drifted):
                with self.assertRaisesRegex(RunStateError, "SHA-256 drifted"):
                    RQ_OBL.obligations()
        obligations()

    def test_drift_fails_closed_on_companion_bytes(self):
        base_path = RQ_OBL.BASE_SOURCE
        with tempfile.TemporaryDirectory() as tmp:
            drifted = Path(tmp) / base_path.name
            drifted.write_text(base_path.read_text(encoding="utf-8") + "\n# drift\n",
                               encoding="utf-8")
            with mock.patch.dict(RQ_OBL.COMPANION_PATHS,
                                 {"fixed_loop_story.py": drifted}):
                with self.assertRaisesRegex(RunStateError, "companion source drifted"):
                    RQ_OBL.obligations()
        mock_path = RQ_OBL.PROVIDER_JUDGE_MOCK
        with tempfile.TemporaryDirectory() as tmp:
            drifted = Path(tmp) / mock_path.name
            drifted.write_bytes(mock_path.read_bytes() + b"\n# drift\n")
            with mock.patch.dict(RQ_OBL.COMPANION_PATHS,
                                 {"http_provider_judge_mock.py": drifted}):
                with self.assertRaisesRegex(RunStateError, "companion source drifted"):
                    RQ_OBL.obligations()
        obligations()

    def test_plan_honesty_face(self):
        plan = density_plan()
        self.assertEqual(plan["schema_version"], 1)
        self.assertEqual(plan["contract"], "result-queue-density-v1")
        self.assertEqual(plan["parent"], PARENT)
        self.assertEqual(plan["executor_row_index"], 20)
        self.assertEqual(plan["coverage_row_no"], 25)
        self.assertEqual(plan["source_file"], RQ_OBL.SOURCE_FILE)
        self.assertEqual(plan["parsed_unlocated_count"], 0)
        self.assertEqual(plan["parsed_unlocated"], [])
        self.assertEqual(plan["credential_references"],
                         ["BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"])
        self.assertEqual(plan["remote_validation"], "NOT_RUN")
        self.assertFalse(plan["grants_release_credit"])
        self.assertTrue(plan["source_read_only"])
        self.assertEqual(plan["residual_count_gap"], 0)
        self.assertEqual(plan["delta"], DENSITY_OBLIGATION_COUNT)
        self.assertIn(ZERO_ATOM_PARENT, plan["projection_rule"])
        self.assertEqual(plan["story_marker"]["marker"], RQ_OBL.STORY_MARKERS[0])
        self.assertEqual(plan["named_preprod_gaps"], list(FAMILY.preprod_gaps))


class ResultQueueDensityReplayTests(unittest.TestCase):
    def test_local_replay_projects_all_52_rows_pass(self):
        receipt = project(mock_observations())
        self.assertEqual(receipt["status"], STATUS_PASS)
        self.assertEqual(receipt["counts"], {STATUS_PASS: 52, STATUS_FAIL: 0,
                                             STATUS_NOT_RUN: 0})
        self.assertEqual(receipt["parsed_obligation_count"], 52)
        self.assertEqual(receipt["parent_obligation_count"], 18)
        self.assertEqual(receipt["density_obligation_count"], 34)
        self.assertEqual(receipt["residual_count_gap"], 0)
        self.assertEqual(receipt["delta"], 34)
        plan = density_plan()
        self.assertEqual([row["obligation_id"] for row in receipt["obligations"]],
                         [row["id"] for row in plan["obligations"]])
        for receipt_row, plan_row in zip(receipt["obligations"], plan["obligations"]):
            self.assertEqual(receipt_row["source"]["file"], plan_row["source_file"])
            self.assertEqual(receipt_row["source"]["line_start"], plan_row["line_start"])
            self.assertEqual(receipt_row["source"]["line_end"], plan_row["line_end"])
            self.assertEqual(receipt_row["source"]["quote"], plan_row["quote"])
            self.assertEqual(receipt_row["comparator"], plan_row["comparator"])
            self.assertEqual(receipt_row["expected"], plan_row["expected"])
            self.assertEqual(receipt_row["slot"], plan_row["slot"])
        parents = [row for row in receipt["obligations"] if row["level"] == "PARENT"]
        atoms = [row for row in receipt["obligations"] if row["level"] == "DENSITY"]
        self.assertEqual(len(parents), 18)
        self.assertEqual(len(atoms), 34)
        self.assertTrue(all(row["status"] == STATUS_PASS for row in receipt["obligations"]))
        self.assertTrue(all(row["observed"] == STATUS_PASS for row in parents))
        for row in parents:
            self.assertEqual(row["method"], "story_stage")
            self.assertEqual(row["evidence_event"], "legacy_contract_parent_mirror")
        for row in atoms:
            self.assertEqual(row["evidence_event"], "owned_local_story_site_mirror")
            self.assertIn("owned-local credential-free replay", row["classification"])
        adjudication = receipt["case_adjudication"]
        self.assertEqual(adjudication["case_id"], "DUAL-RESULT-QUEUE-01")
        self.assertEqual(adjudication["parent_id"], PARENT)
        self.assertEqual(adjudication["status"], "PASS")
        self.assertEqual(adjudication["completed_count"], 18)
        self.assertEqual(adjudication["total_count"], 18)
        self.assertTrue(adjudication["ready_to_execute"])
        self.assertIsNone(adjudication["error_type"])
        self.assertEqual(len(adjudication["steps"]), 18)
        self.assertEqual([step["requirement_id"] for step in adjudication["steps"]],
                         list(REQUIREMENTS))
        traceability = FAMILY.traceability()
        for step in adjudication["steps"]:
            self.assertEqual(step["status"], "PASS")
            face = traceability[step["requirement_id"]]
            self.assertEqual(step["assertion_site_count"], face["assertion_site_count"])
            self.assertIsNone(step["preprod_verdict"])
            self.assertIsNone(step["documented_gaps"])
            self.assertEqual(step["remote_validation"], "NOT_RUN")
            self.assertFalse(step["grants_release_credit"])
        family = receipt["family"]
        self.assertEqual(family["family"], "result_queue")
        self.assertEqual(family["dual_case_id"], "DUAL-RESULT-QUEUE-01")
        self.assertEqual(family["source_case_id"], "LOCAL-RESULT-QUEUE-01")
        self.assertEqual(family["dual_suite"], "dual-result-queue-v1")
        self.assertEqual(family["source_suite"], "owned-local-result-queue-v1")
        self.assertEqual(family["local_status"], "LOCAL_RESULT_QUEUE_PASS")
        self.assertEqual(family["canonical_status"], "LOCAL_SUITE_PASS")
        self.assertEqual(family["preprod_initial_verdict"], "NOT_RUN")
        self.assertEqual(receipt["executor_row"],
                         {"index": 20, "coverage_row_no": 25,
                          "dual_command": "dual-result-queue-run"})
        self.assertEqual(receipt["source"]["sha256"], RQ_OBL.SOURCE_SHA256)
        self.assertEqual(receipt["source"]["companion_sources"], RQ_OBL.COMPANION_SOURCES)
        self.assertEqual(receipt["source"]["story_line_span"], list(RQ_OBL.STORY_LINE_SPAN))
        self.assertEqual(receipt["story_marker"]["marker"], RQ_OBL.STORY_MARKERS[0])
        self.assertEqual(receipt["story_arguments"]["required"],
                         list(REQUIRED_STORY_ARGUMENTS))
        self.assertEqual(receipt["mock_child"]["observed_webhook_requests"], 3)
        self.assertEqual(receipt["mock_child"]["expected_webhook_requests"], 3)
        self.assertEqual(receipt["zero_atom_parents"], RQ_OBL.ZERO_ATOM_PARENTS)
        self.assertEqual(receipt["not_charged_source_probes"], [RQ_OBL.NOT_CHARGED_PROBES])
        self.assertEqual(receipt["upstream_roster"]["roster_verdict"], "0绿/1")
        self.assertEqual(receipt["upstream_roster"]["parsed_green"], 0)
        self.assertEqual(receipt["upstream_roster"]["parsed_total"], 1)
        self.assertEqual([gap["id"] for gap in receipt["named_preprod_gaps"]],
                         list(FAMILY.preprod_gaps))
        self.assertTrue(all(gap["status"] == "NAMED_DESIGN_GAP"
                            for gap in receipt["named_preprod_gaps"]))
        self.assertIsNone(receipt["preprod_credit_cap"])
        self.assertEqual(receipt["execution"], "owned-local-offline-replay")
        self.assertEqual(receipt["environment_tier"], "local")
        self.assertFalse(receipt["preprod_observations_injected"])
        self.assertEqual(receipt["remote_validation"], "NOT_RUN")
        self.assertFalse(receipt["grants_release_credit"])
        self.assertEqual(receipt["credential_references"],
                         ["BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"])
        self.assertIn("no owned runtime", receipt["transport_admission"])

    def test_missing_observation_is_not_run_and_overall_capped(self):
        observations = mock_observations()
        leaf_ids = density_plan()["leaf_ids"]
        absent_leaf = leaf_ids[len(leaf_ids) // 2]
        observations["atoms"].pop(absent_leaf)
        first_parent = REQUIREMENTS[0]
        observations["parents"].pop(first_parent)
        receipt = project(observations)
        self.assertEqual(receipt["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(receipt["counts"], {STATUS_PASS: 50, STATUS_FAIL: 0,
                                             STATUS_NOT_RUN: 2})
        by_id = {row["obligation_id"]: row for row in receipt["obligations"]}
        self.assertEqual(by_id[absent_leaf]["status"], STATUS_NOT_RUN)
        self.assertIsNone(by_id[absent_leaf]["error_type"])
        self.assertEqual(by_id[absent_leaf]["evidence_event"], "observation_absent")
        self.assertEqual(by_id[first_parent]["status"], STATUS_NOT_RUN)
        self.assertIsNone(by_id[first_parent]["observed"])
        self.assertIn("never defaults to PASS", by_id[first_parent]["classification"])
        self.assertIn("never defaults to PASS", by_id[absent_leaf]["classification"])
        self.assertFalse(receipt["grants_release_credit"])

    def test_shifted_observation_is_fail_not_not_run(self):
        observations = mock_observations()
        plan = density_plan()
        equals_leaf = next(row for row in plan["obligations"]
                           if row["level"] == "DENSITY" and row["comparator"] == "equals"
                           and row["expected"] == 200 and row["slot"] == "http_status")
        predicate_leaf = next(row for row in plan["obligations"]
                              if row["level"] == "DENSITY" and row["slot"] == "predicate")
        in_leaf = next(row for row in plan["obligations"]
                       if row["level"] == "DENSITY" and row["comparator"] == "in")
        self.assertEqual(in_leaf["expected"], [200, 409])
        self.assertEqual(in_leaf["parent_obligation"],
                         "RESULT-QUEUE.final-callback-admission")
        observations["atoms"][equals_leaf["id"]] = equals_leaf["expected"] + 1
        observations["atoms"][predicate_leaf["id"]] = False
        observations["atoms"][in_leaf["id"]] = 500
        receipt = project(observations)
        self.assertEqual(receipt["status"], STATUS_FAIL)
        self.assertEqual(receipt["counts"], {STATUS_PASS: 49, STATUS_FAIL: 3,
                                             STATUS_NOT_RUN: 0})
        by_id = {row["obligation_id"]: row for row in receipt["obligations"]}
        self.assertEqual(by_id[equals_leaf["id"]]["error_type"], "OBSERVATION_MISMATCH")
        self.assertEqual(by_id[predicate_leaf["id"]]["status"], STATUS_FAIL)
        self.assertIn("fail-close", by_id[equals_leaf["id"]]["classification"])
        adjudication = receipt["case_adjudication"]
        self.assertEqual(adjudication["status"], "BLOCKED")
        self.assertEqual(adjudication["error_type"], RQ_REPLAY.ADJUDICATION_BLOCKED)
        self.assertEqual(adjudication["completed_count"], 0)
        self.assertFalse(adjudication["ready_to_execute"])
        self.assertIsNone(receipt["mock_child"]["observed_webhook_requests"])

    def test_preprod_default_is_fully_not_run_with_named_gaps(self):
        receipt = project(tier="preprod")
        self.assertEqual(receipt["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(receipt["counts"], {STATUS_PASS: 0, STATUS_FAIL: 0,
                                             STATUS_NOT_RUN: 52})
        self.assertEqual(receipt["environment_tier"], "preprod")
        self.assertFalse(receipt["preprod_observations_injected"])
        self.assertEqual(receipt["execution"], "preprod-not-run")
        for row in receipt["obligations"]:
            self.assertEqual(row["status"], STATUS_NOT_RUN)
            self.assertIn("NOT_RUN", row["classification"])
            self.assertEqual(row["evidence_event"], "preprod_story_not_executed_offline")
        atoms = [row for row in receipt["obligations"] if row["level"] == "DENSITY"]
        self.assertTrue(all(row["error_type"] == "PREPROD_EXECUTION_NOT_RUN"
                            for row in atoms))
        gap_ids = [gap["id"] for gap in receipt["named_preprod_gaps"]]
        self.assertEqual(gap_ids, list(FAMILY.preprod_gaps))
        self.assertEqual(gap_ids, [
            "PREPROD_ADMIN_MESSAGE_DELIVERY_POLICY_ROUTE_UNREGISTERED",
            "PREPROD_PROVIDER_CALLBACK_HARNESS_UNREGISTERED",
            "PREPROD_RESULT_DELIVERY_LEDGER_FACE_VERIFICATION_NOT_RUN",
            "PREPROD_FULL_STORY_NOT_EXECUTED_OFFLINE"])
        adjudication = receipt["case_adjudication"]
        self.assertEqual(adjudication["status"], "BLOCKED")
        self.assertEqual(adjudication["completed_count"], 0)
        self.assertEqual(adjudication["total_count"], 18)
        self.assertFalse(adjudication["ready_to_execute"])
        self.assertEqual(adjudication["error_type"],
                         "PREPROD_DYNAMIC_FAMILY_FULL_STORY_NOT_RUN")
        for step in adjudication["steps"]:
            self.assertEqual(step["status"], "NOT_RUN")
            self.assertEqual(step["documented_gaps"], list(FAMILY.preprod_gaps))
            self.assertEqual(step["preprod_verdict"], "NOT_RUN")
        self.assertIn("REMOTE_UNSUPPORTED", receipt["preprod_credit_cap"])
        self.assertFalse(receipt["grants_release_credit"])

    def test_preprod_injection_is_row_green_but_capped(self):
        receipt = project(mock_observations(), tier="preprod")
        self.assertEqual(receipt["counts"], {STATUS_PASS: 52, STATUS_FAIL: 0,
                                             STATUS_NOT_RUN: 0})
        self.assertEqual(receipt["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertTrue(receipt["preprod_observations_injected"])
        self.assertEqual(receipt["execution"], "preprod-injected-observation-projection")
        self.assertIn("cannot convert", receipt["preprod_credit_cap"])
        adjudication = receipt["case_adjudication"]
        self.assertEqual(adjudication["status"], "BLOCKED")
        self.assertEqual(adjudication["completed_count"], 0)
        self.assertEqual(adjudication["error_type"],
                         "PREPROD_DYNAMIC_FAMILY_FULL_STORY_NOT_RUN")
        self.assertFalse(adjudication["ready_to_execute"])
        self.assertEqual(receipt["mock_child"]["observed_webhook_requests"], 3)
        self.assertFalse(receipt["grants_release_credit"])

    def test_preprod_injection_with_shifted_value_stays_capped_fail_close(self):
        observations = mock_observations()
        plan = density_plan()
        target = next(row for row in plan["obligations"]
                      if row["level"] == "DENSITY" and row["comparator"] == "equals")
        observations["atoms"][target["id"]] = "not-an-int"
        receipt = project(observations, tier="preprod")
        by_id = {row["obligation_id"]: row for row in receipt["obligations"]}
        self.assertEqual(by_id[target["id"]]["status"], STATUS_FAIL)
        self.assertEqual(by_id[target["id"]]["error_type"], "OBSERVATION_MISMATCH")
        self.assertEqual(receipt["counts"], {STATUS_PASS: 51, STATUS_FAIL: 1,
                                             STATUS_NOT_RUN: 0})
        self.assertEqual(receipt["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(receipt["case_adjudication"]["status"], "BLOCKED")
        self.assertIn("REMOTE_UNSUPPORTED", receipt["preprod_credit_cap"])

    def test_replay_fail_close_shapes(self):
        with self.assertRaisesRegex(RunStateError, "tier must be local or preprod"):
            project(tier="stage")
        with self.assertRaisesRegex(RunStateError, "observations must be an object"):
            project(["not", "a", "dict"])
        with self.assertRaisesRegex(RunStateError, "observation layers must be objects"):
            project({"parents": [], "atoms": {}})
        with self.assertRaisesRegex(RunStateError, "observation layers must be objects"):
            project({"parents": {}, "atoms": "dense"})


def _unregistered_executor_migration_world():
    """Patch the three inventory bindings a wave proposal imports.

    Wave E2b landed the row face note, so the live face is registered; the
    describe-only proposal keeps its fail-close contract (unregistered face,
    note membership, counter closure). Replays of registration_proposal()
    and describe_only_mainline_fragment() run inside this context, which
    patches exactly the three proposal-module from-import bindings and
    nothing else.
    """
    proposal_module = importlib.import_module(
        "bcs_e2e.result_queue_density.registration_proposal")
    statuses = inventory_module.load_executor_migration()
    unregistered = proposal_module.STORY_ID
    snapshot = {story_id: (dict(face, notes=[]) if story_id == unregistered
                           else face)
                for story_id, face in statuses.items()}
    unregistered_notes = frozenset(
        inventory_module.EXECUTOR_MIGRATION_NOTES) - {NOTE}
    unregistered_counts = {
        key: value for key, value
        in inventory_module._EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS.items()
        if key != NOTE}
    stack = ExitStack()
    stack.enter_context(mock.patch.object(
        proposal_module, "load_executor_migration",
        return_value=snapshot))
    stack.enter_context(mock.patch.object(
        proposal_module, "EXECUTOR_MIGRATION_NOTES",
        unregistered_notes))
    stack.enter_context(mock.patch.object(
        proposal_module,
        "_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS",
        unregistered_counts))
    return stack


def _proposal_against_unregistered_snapshot():
    with _unregistered_executor_migration_world():
        return registration_proposal()


class ResultQueueDensityRegistrationProposalTests(unittest.TestCase):
    def test_registration_proposal_refuses_repeat_registration_live(self):
        # E2b integration landed the row face note; the describe-only
        # proposal fail-closes against the live registered world.
        with self.assertRaises(RunStateError):
            registration_proposal()

    def test_proposal_is_describe_only_with_caliber_and_write_domain(self):
        proposal = _proposal_against_unregistered_snapshot()
        self.assertEqual(proposal["status"], "DESCRIBE_ONLY")
        self.assertEqual(proposal["token"], PROPOSAL_TOKEN)
        self.assertEqual(PROPOSAL_TOKEN, "RESULT_QUEUE_DENSITY_34_OBLIGATIONS_20261031")
        self.assertEqual(proposal["contract"], "result-queue-density-v1")
        self.assertEqual(proposal["parent_story"], "story_state_machine_result_queue")
        self.assertEqual(proposal["upstream_issue"], "#25")
        self.assertEqual(proposal["executor_row_index"], 20)
        self.assertEqual(proposal["coverage_row_no"], 25)
        self.assertEqual(proposal["parsed_obligation_count"], 52)
        self.assertEqual(proposal["parent_obligation_count"], 18)
        self.assertEqual(proposal["density_obligation_count"], 34)
        self.assertEqual(proposal["residual_count_gap"], 0)
        self.assertEqual(proposal["delta"], 34)
        source = proposal["source"]
        self.assertEqual(source["file"], RQ_OBL.SOURCE_FILE)
        self.assertEqual(source["sha256"], RQ_OBL.SOURCE_SHA256)
        self.assertEqual(source["story_line_span"], list(RQ_OBL.STORY_LINE_SPAN))
        self.assertEqual(source["companion_sources"], RQ_OBL.COMPANION_SOURCES)
        self.assertEqual(source["migration_contract"],
                         "result-queue-migration-contract.json")
        self.assertTrue(re.fullmatch("[0-9a-f]{40}", source["contract_source_revision"]))
        self.assertEqual(source["contract_source_revision"],
                         json.loads(CONTRACT_DISK.read_text(encoding="utf-8"))["source_revision"])
        self.assertNotIn(_BEARER, json.dumps(proposal))
        self.assertEqual(proposal["family"]["dual_command"], "dual-result-queue-run")
        self.assertEqual(proposal["family"]["local_executor"], FAMILY.local_executor)
        self.assertEqual(proposal["family"]["canonical_status"], "LOCAL_SUITE_PASS")
        self.assertEqual(proposal["zero_atom_parents"], RQ_OBL.ZERO_ATOM_PARENTS)
        self.assertEqual(proposal["not_charged_source_probes"], [RQ_OBL.NOT_CHARGED_PROBES])
        gaps = proposal["named_preprod_gaps"]
        self.assertEqual([gap["id"] for gap in gaps], list(FAMILY.preprod_gaps))
        self.assertEqual(set(NAMED_GAP_DETAILS), set(FAMILY.preprod_gaps))
        for gap in gaps:
            self.assertEqual(gap["status"], "NAMED_DESIGN_GAP")
            self.assertEqual(gap["blocks"], "preprod PASS credit")
            self.assertTrue(NAMED_GAP_DETAILS[gap["id"]])
        replay_caliber = proposal["replay_caliber"]
        self.assertIn("52/52 PASS", replay_caliber["local"])
        self.assertIn("LOCAL-RESULT-QUEUE-01", replay_caliber["local"])
        self.assertIn("52/52 NOT_RUN", replay_caliber["preprod"])
        self.assertIn("BLOCKED_INVENTORIED", replay_caliber["preprod"])
        self.assertEqual(len(proposal["red_lines"]), 5)
        self.assertIn("executor-migration.json", " ".join(proposal["red_lines"]))
        self.assertFalse(proposal["new_registration_command"])
        self.assertEqual(proposal["write_domain"],
                         ["bcs_e2e/result_queue_density/**",
                          "tests/test_result_queue_density.py"])
        self.assertEqual(proposal["remote_validation"], "NOT_RUN")
        self.assertFalse(proposal["grants_release_credit"])
        self.assertEqual(proposal["credential_references"],
                         ["BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"])

    def test_coverage_overlay_arithmetic_matches_disk_before_and_after(self):
        coverage_map = json.loads(COVERAGE_MAP_DISK.read_text(encoding="utf-8"))
        disk_row = next(row for row in coverage_map["rows"] if row["no"] == 25)
        self.assertEqual(disk_row["case_id"], "story_state_machine_result_queue")
        self.assertEqual(disk_row["local_projection"]["obligations"], 18)
        self.assertEqual(disk_row["upstream"]["snapshot_parsed_count"], 1)
        self.assertEqual(disk_row["gap"]["parsed_minus_obligations"], -17)
        self.assertEqual(disk_row["queue"], {"rank": 19, "gap_qty": 18, "feasibility": 1,
                                             "score": 18,
                                             "basis": "obl 18×1；LOCAL_ONLY 车道"})
        proposal = _proposal_against_unregistered_snapshot()
        row = proposal["coverage_map_increment"]["row"]
        self.assertEqual(row["no"], 25)
        self.assertEqual(row["case_id"], "story_state_machine_result_queue")
        self.assertEqual(row["before"]["obligations"],
                         disk_row["local_projection"]["obligations"])
        self.assertEqual(row["before"]["snapshot_parsed_count"], 1)
        self.assertEqual(row["before"]["gap_parsed_minus_obligations"], -17)
        after = row["after"]
        self.assertEqual(after["obligations"], 52)
        self.assertEqual(after["snapshot_parsed_count"], 52)
        self.assertEqual(after["gap_parsed_minus_obligations"], 0)
        self.assertEqual(after["gap_kind"], "COUNT_GAP_CLOSED+EXECUTION_GAP")
        self.assertIn("parsed 52 − 义务 52 = 0", after["gap_detail"])
        self.assertEqual(after["queue"]["rank"], 19)
        self.assertEqual(after["queue"]["gap_qty"], 34)
        self.assertEqual(after["queue"]["score"], 34)
        self.assertEqual(after["queue"]["feasibility"], 1)
        self.assertIn("执行缺口按密度口径 34（parsed 52−legacy 18）×1",
                      after["queue"]["basis"])
        self.assertIn("34/34 有序恒等", after["queue"]["basis"])
        self.assertEqual(after["queue"]["basis"], QUEUE_BASIS)
        self.assertEqual(proposal["coverage_map_increment"]["targets"],
                         ["coverage-map/coverage-map-20261031.json",
                          "coverage-map/coverage-map-20261031.md"])
        totals = proposal["coverage_map_increment"]["committed_totals"]
        self.assertEqual(totals["obligations_committed_sum"],
                         coverage_map["totals"]["obligations_committed_sum"])
        self.assertEqual(totals["upstream_parsed_known_sum"],
                         coverage_map["totals"]["upstream_parsed_known_sum"])
        self.assertEqual((totals["obligations_committed_sum"],
                          totals["upstream_parsed_known_sum"]), (674, 638))
        self.assertIn("unchanged", totals["disposition"])
        self.assertEqual(proposal["upstream_roster"]["roster_verdict"], "0绿/1")
        self.assertEqual(proposal["upstream_roster"]["parsed_green"], 0)
        self.assertEqual(proposal["upstream_roster"]["parsed_total"], 1)
        self.assertEqual(disk_row["upstream"]["roster_verdict"], "0绿/1")
        self.assertIn("| 25 |", MD_ROW)
        self.assertIn(PROPOSAL_TOKEN, MD_ROW)
        self.assertIn("parsed 52 − 义务 52 = 0", MD_ROW)

    def test_inventory_increment_is_notes_only_with_fail_close_counter_closure(self):
        proposal = _proposal_against_unregistered_snapshot()
        increment = proposal["inventory_increment"]
        self.assertEqual(increment["target_file"], "bcs_e2e/inventory.py")
        self.assertEqual(increment["operation"], "fail-close membership and counter closure")
        self.assertEqual(increment["adds_to_EXECUTOR_MIGRATION_NOTES"], [NOTE])
        self.assertEqual(increment["adds_to_expected_note_counts"], {NOTE: 1})
        self.assertIn(NOTE, inventory_module.EXECUTOR_MIGRATION_NOTES)
        self.assertNotIn(PROPOSAL_TOKEN, inventory_module.EXECUTOR_MIGRATION_NOTES)
        live_counts = inventory_module._EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS
        live_total = sum(live_counts.values())
        self.assertEqual(len(inventory_module.EXECUTOR_MIGRATION_NOTES), live_total)
        self.assertEqual(live_counts[NOTE], 1)
        self.assertEqual(increment["note_counts_replacement"],
                         {"current_total_notes": live_total - 1,
                          "after_total_notes": live_total,
                          "this_family_delta": 1})
        self.assertIn(NOTE, live_counts)
        self.assertEqual(increment["unchanged"]["state_counts"],
                         {"DUAL/PASS": 19, "DUAL/BLOCKED_INVENTORIED": 11,
                          "DUAL/NOT_RUN": 10,
                          "EDGE_OBLIGATION_IN_FAMILY/NOT_APPLICABLE": 1})
        expected_states = {
            f"{mode}/{verdict}": count
            for (mode, verdict), count
            in inventory_module._EXPECTED_EXECUTOR_MIGRATION_COUNTS.items()
        }
        self.assertEqual(increment["unchanged"]["state_counts"], expected_states)
        self.assertEqual(
            set(increment["unchanged"]["state_counts"]),
            {f"{mode}/{verdict}" for mode, verdict
             in inventory_module._EXPECTED_EXECUTOR_MIGRATION_COUNTS})
        self.assertEqual(increment["unchanged"]["gate_counts"], {"MOCK": 2})
        self.assertEqual(Counter(inventory_module._EXPECTED_EXECUTOR_MIGRATION_GATE_COUNTS),
                         Counter(increment["unchanged"]["gate_counts"]))
        statuses = inventory_module.load_executor_migration()
        self.assertEqual(len(statuses), 41)
        face = statuses["story_state_machine_result_queue"]
        self.assertEqual((face["mode"], face["preprod_verdict"], face["gates"],
                          face["notes"]),
                         ("DUAL", "BLOCKED_INVENTORIED", [], [NOTE]))
        self.assertEqual(increment["unchanged"]["row_face"]["id"], PARENT)
        self.assertEqual(increment["unchanged"]["row_face"]["mode"], face["mode"])
        self.assertEqual(increment["unchanged"]["row_face"]["preprod_verdict"],
                         face["preprod_verdict"])
        self.assertEqual(increment["unchanged"]["row_face"]["notes_delta"],
                         "+1 (this proposal note only)")

    def test_describe_only_fragment_carries_token_and_honest_counts(self):
        with _unregistered_executor_migration_world():
            fragment = describe_only_mainline_fragment()
        self.assertIn(PROPOSAL_TOKEN, fragment)
        self.assertIn("row #20", fragment)
        self.assertIn("story_state_machine_result_queue", fragment)
        self.assertIn("52 parsed = 18 legacy parents + 34 atoms", fragment)
        self.assertIn("residual_count_gap=0", fragment)
        self.assertIn("ordered identity RQ catalog 34/34", fragment)
        self.assertIn(ZERO_ATOM_PARENT + " disclosed", fragment)
        self.assertIn("seven wait predicates plus the terminal marker print stay "
                      "parent-charged", fragment)
        self.assertIn("executor-migration.json notes-only increment", fragment)
        self.assertIn("one EXECUTOR_MIGRATION_NOTES member", fragment)
        self.assertIn("states and gates counters unchanged", fragment)
        self.assertIn("DUAL/BLOCKED_INVENTORIED", fragment)
        self.assertIn("obligations 18 to 52", fragment)
        self.assertIn("snapshot_parsed_count 1 to 52", fragment)
        self.assertIn("queue gap_qty 18 to 34", fragment)
        self.assertIn("committed totals 674/638 stay unchanged", fragment)
        self.assertIn("52/52 PASS", fragment)
        self.assertIn("LOCAL-RESULT-QUEUE-01", fragment)
        self.assertIn("remote_validation=NOT_RUN", fragment)
        self.assertIn("grants_release_credit=false", fragment)
        self.assertIn("New registration command: none", fragment)
        self.assertIn("dual-result-queue-run envelope reused", fragment)
        self.assertIn("bcs_e2e/result_queue_density/**", fragment)
        self.assertIn("tests/test_result_queue_density.py", fragment)
        for gap_id in FAMILY.preprod_gaps:
            self.assertIn(gap_id, fragment)


class ResultQueueDensityHygieneTests(unittest.TestCase):
    def test_package_file_set_line_budget_and_guard_replica(self):
        self.assertEqual({path.name for path in PACKAGE_DIR.glob("*.py")},
                         {"__init__.py", "obligations.py", "replay.py",
                          "registration_proposal.py"})
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 1000,
                                 path.name)
            self.assertEqual(_guard_violations(path), [], path.name)
        self.assertLessEqual(len(Path(__file__).read_text(encoding="utf-8").splitlines()),
                             1000)
        self.assertEqual(_guard_violations(Path(__file__)), [])

    def test_no_transport_process_env_or_credential_value_literals(self):
        text = "\n".join(path.read_text(encoding="utf-8")
                         for path in sorted(PACKAGE_DIR.glob("*.py")))
        for forbidden in ("urllib", "socket", "subprocess", "os.getenv", "os.environ",
                          "http.client"):
            self.assertNotIn(forbidden, text, f"forbidden literal {forbidden!r}")
        for pattern in (r"Bearer\s", r"Cookie:\s", r"-----BEGIN"):
            self.assertIsNone(re.search(pattern, text), pattern)
        uuid_like = re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            self.assertIsNone(uuid_like.search(path.read_text(encoding="utf-8")), path.name)
        self.assertIn("BCS_E2E_HUMAN_COOKIE", text)
        self.assertIn("BCS_E2E_DRIVER_TOKEN", text)
        self.assertNotIn("BCS_E2E_HUMAN_COOKIE=", text)
        self.assertNotIn("BCS_E2E_DRIVER_TOKEN=", text)
        story_text = STORY_DISK.read_text(encoding="utf-8")
        for row in obligations():
            self.assertTrue(row.quote)
            self.assertIn(row.quote, story_text)


if __name__ == "__main__":
    unittest.main()
