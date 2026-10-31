"""Focused offline regression for wave E2b row #40 draft-group density.

The suite validates the source-anchored obligation backfill for
story_collaboration_draft_group_launch (executor row index 40): 17 frozen
LOCAL-DRAFT-GROUP.* contract parents plus 75 weighted AST-rebuilt in-script
assertion leaves (DGL.F01..DGL.F75), 92 parsed with residual_count_gap=0.
It checks the weighted slot plan against the owned-local executor catalog,
verbatim disk-byte anchoring, SHA-256 fail-close drift detection for the
story, the incident fixture and the Provider/Judge mock, ordered identity
with the executor site catalog, dynamic-family traceability, the honest
offline replay (local green projection, missing/tampered observations,
honest preprod NOT_RUN with named design gaps and the REMOTE_UNSUPPORTED
cap), the describe-only registration proposal arithmetic against the
on-disk inventory and coverage-map faces, and package hygiene.  Nothing
here launches the owned runtime, opens a transport, resolves a credential,
or writes a registration.
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
from bcs_e2e.local_draft_group_contract import (  # noqa: E402
    ASSERTION_IDS, MARKER_PREFIX, MARKER_SUFFIX, REQUIREMENTS)
from bcs_e2e.draft_group_density import (  # noqa: E402
    DENSITY_OBLIGATION_COUNT, PARSED_OBLIGATION_COUNT, PARENT, PARENT_OBLIGATION_COUNT,
    RESIDUAL_COUNT_GAP, STATUS_BLOCKED_INVENTORIED, STATUS_FAIL, STATUS_NOT_RUN,
    STATUS_PASS, density_plan, mock_observations, obligations, project,
    registration_proposal)
from bcs_e2e.draft_group_density.registration_proposal import (  # noqa: E402
    MD_ROW, NAMED_GAP_DETAILS, NOTE, PROPOSAL_TOKEN, QUEUE_BASIS, STORY_ID,
    describe_only_mainline_fragment)
from bcs_e2e.run_state import RunStateError  # noqa: E402

import bcs_e2e.inventory as inventory_module  # noqa: E402

DG_OBL = importlib.import_module("bcs_e2e.draft_group_density.obligations")
DG_REPLAY = importlib.import_module("bcs_e2e.draft_group_density.replay")
FAMILY = dynamic_family_contract("draft_group")
STORY_DISK = DG_OBL.STORY_SOURCE
FIXTURE_DISK = DG_OBL.FIXTURE_SOURCE
CONTRACT_DISK = DG_OBL.ROOT / DG_OBL.MIGRATION_CONTRACT
COVERAGE_MAP_DISK = ROOT / "coverage-map" / "coverage-map-20261031.json"
PACKAGE_DIR = Path(DG_OBL.__file__).resolve().parent
ZERO_ATOM_PARENT = "LOCAL-DRAFT-GROUP.group-launch-input"

FROZEN_PER_PARENT_COUNTS = {
    "LOCAL-DRAFT-GROUP.human-registration": 5,
    "LOCAL-DRAFT-GROUP.provider-collaborator-friendship": 3,
    "LOCAL-DRAFT-GROUP.immutable-source-upload": 3,
    "LOCAL-DRAFT-GROUP.share-reference-issuance": 3,
    "LOCAL-DRAFT-GROUP.v1-preview-byte-fidelity": 5,
    "LOCAL-DRAFT-GROUP.shared-validation-binding": 5,
    "LOCAL-DRAFT-GROUP.grant-negative-matrix": 4,
    "LOCAL-DRAFT-GROUP.revision-input-guards": 8,
    "LOCAL-DRAFT-GROUP.browser-identity-chain": 4,
    "LOCAL-DRAFT-GROUP.originator-selection": 8,
    "LOCAL-DRAFT-GROUP.group-launch-input": 0,
    "LOCAL-DRAFT-GROUP.browser-context-submission-guards": 3,
    "LOCAL-DRAFT-GROUP.impersonation-rejection": 4,
    "LOCAL-DRAFT-GROUP.group-launch-binding": 9,
    "LOCAL-DRAFT-GROUP.draft-immutability": 4,
    "LOCAL-DRAFT-GROUP.share-revocation": 5,
    "LOCAL-DRAFT-GROUP.cleanup-disposition": 2,
}
FROZEN_SLOT_COUNTS = {
    "http_status": 28, "predicate": 16, "cache_control": 13,
    "envelope_code": 13, "error_code": 5,
}

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


class DraftGroupDensityContractTests(unittest.TestCase):
    def test_accounting_is_17_parents_plus_75_weighted_atoms_zero_residual(self):
        rows = obligations()
        self.assertEqual(len(rows), PARSED_OBLIGATION_COUNT)
        self.assertEqual(PARSED_OBLIGATION_COUNT, 92)
        self.assertEqual(sum(row.level == "PARENT" for row in rows), PARENT_OBLIGATION_COUNT)
        self.assertEqual(sum(row.level == "DENSITY" for row in rows), DENSITY_OBLIGATION_COUNT)
        self.assertEqual((PARENT_OBLIGATION_COUNT, DENSITY_OBLIGATION_COUNT), (17, 75))
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
            self.assertEqual(row.source_file, DG_OBL.SOURCE_FILE)
            self.assertEqual(row.source_line, row.line_start)
            self.assertEqual(row.source_quote, row.quote)

    def test_leaf_ids_and_executor_site_catalog_are_ordered_identical(self):
        rows = obligations()
        leaves = [row for row in rows if row.level == "DENSITY"]
        self.assertEqual([row.obligation_id for row in leaves],
                         [f"DGL.F{index:02d}" for index in range(1, 76)])
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

    def test_per_parent_weighted_counts_match_traceability_and_frozen_face(self):
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
        self.assertEqual(plan["per_parent_atom_counts"], FROZEN_PER_PARENT_COUNTS)
        self.assertEqual(FROZEN_PER_PARENT_COUNTS[ZERO_ATOM_PARENT], 0)
        self.assertEqual(sum(live.values()), DENSITY_OBLIGATION_COUNT)
        self.assertEqual(plan["parent_ids"], list(REQUIREMENTS))
        self.assertEqual(plan["leaf_ids"], [leaf.obligation_id for leaf in leaves])
        self.assertTrue(plan["weighted"])
        self.assertIn("cache_control", plan["weighted_slot_rule"])
        self.assertIn("envelope_code", plan["weighted_slot_rule"])
        self.assertIn("error_code", plan["weighted_slot_rule"])

    def test_weighted_slot_plan_matches_frozen_distribution_and_envelope_rules(self):
        rows = obligations()
        leaves = [row for row in rows if row.level == "DENSITY"]
        self.assertEqual(Counter(row.slot for row in leaves), FROZEN_SLOT_COUNTS)
        self.assertEqual(Counter(
            (row.comparator, str(row.expected)) for row in leaves
            if row.slot == "http_status"),
            {("equals", "200"): 17, ("equals", "201"): 3, ("equals", "204"): 1,
             ("equals", "404"): 2, ("equals", "400"): 2, ("equals", "403"): 1,
             ("equals", "401"): 1, ("in", "[200, 204]"): 1})
        by_base: dict[str, list] = {}
        for leaf in leaves:
            base = "-".join(leaf.executor_site_id.split("-")[:-1])
            by_base.setdefault(base, []).append(leaf)
        envelope_groups = [group for group in by_base.values()
                           if any(row.slot in ("cache_control", "envelope_code")
                                  for row in group)]
        self.assertEqual(len(envelope_groups), 13)
        for group in envelope_groups:
            status_rows = [row for row in group if row.slot == "http_status"]
            cache_rows = [row for row in group if row.slot == "cache_control"]
            envelope_rows = [row for row in group if row.slot == "envelope_code"]
            self.assertEqual(len(status_rows), 1)
            self.assertEqual(len(cache_rows), 1)
            self.assertEqual(len(envelope_rows), 1)
            self.assertEqual(cache_rows[0].expected, "no-store")
            self.assertEqual(cache_rows[0].comparator, "equals")
            self.assertEqual(envelope_rows[0].expected, status_rows[0].expected * 100)
            self.assertEqual(envelope_rows[0].comparator, "equals")
        error_rows = [row for row in leaves if row.slot == "error_code"]
        self.assertEqual(len(error_rows), 5)
        self.assertEqual(Counter(row.expected for row in error_rows),
                         {"DRAFT_REFERENCE_UNAVAILABLE": 2, "DRAFT_INPUT": 2,
                          "unauthenticated": 1})
        self.assertTrue(all(row.comparator == "equals" for row in error_rows))
        predicate_rows = [row for row in leaves if row.slot == "predicate"]
        self.assertEqual(len(predicate_rows), 16)
        self.assertTrue(all(row.expected is True and row.comparator == "predicate"
                            for row in predicate_rows))
        in_rows = [row for row in leaves if row.comparator == "in"]
        self.assertEqual(len(in_rows), 1)
        self.assertEqual(in_rows[0].obligation_id, "DGL.F74")
        self.assertEqual(in_rows[0].expected, [200, 204])
        self.assertEqual(in_rows[0].executor_site_id,
                         "LOCAL-DRAFT-GROUP.cleanup.request-220-01")
        self.assertEqual(in_rows[0].parent_obligation,
                         "LOCAL-DRAFT-GROUP.cleanup-disposition")
        equals_rows = [row for row in leaves if row.slot == "http_status"]
        self.assertEqual(len(equals_rows) + len(envelope_groups), 28 + 13)
        self.assertTrue(all(row.method.startswith("http_") for row in leaves
                            if row.slot != "predicate"))
        self.assertTrue(all(row.method in ("story_assert", "story_check")
                            for row in predicate_rows))

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
            self.assertEqual(leaf.source_file, DG_OBL.SOURCE_FILE)
        for row in rows:
            self.assertTrue(17 <= row.line_start <= row.line_end <= 231)

    def test_story_span_sha_fixture_and_companion_digests_are_frozen(self):
        raw = STORY_DISK.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), DG_OBL.SOURCE_SHA256)
        contract = json.loads(CONTRACT_DISK.read_text(encoding="utf-8"))
        self.assertEqual(contract["sources"]["collaboration_draft_group_story.py"],
                         DG_OBL.SOURCE_SHA256)
        for name, digest in DG_OBL.COMPANION_SOURCES.items():
            self.assertEqual(contract["sources"][name], digest)
        revisions = contract["source_file_revisions"]
        self.assertEqual(set(revisions),
                         set(DG_OBL.COMPANION_PATHS)
                         | {"collaboration_draft_group_story.py"})
        self.assertTrue(all(re.fullmatch("[0-9a-f]{40}", value)
                            for value in revisions.values()))
        tree = ast.parse(raw.decode("utf-8"), filename=str(STORY_DISK))
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        self.assertEqual((classes[0].lineno, classes[0].end_lineno),
                         tuple(DG_OBL.STORY_LINE_SPAN))
        self.assertEqual(DG_OBL.STORY_LINE_SPAN, (17, 231))
        self.assertEqual(FAMILY.local_status, "LOCAL_DRAFT_GROUP_PASS")
        # Two committed faces stay distinct by upstream design and are never
        # conflated: the migration-contract parent stem
        # "collaboration_draft_group_story" (local executor family face,
        # DG_OBL.PARENT) versus the executor-migration legacy row id carried by
        # the dynamic family contract (FAMILY.legacy_id == STORY_ID).
        self.assertEqual(DG_OBL.PARENT, "collaboration_draft_group_story")
        self.assertEqual(FAMILY.legacy_id, STORY_ID)
        self.assertEqual(STORY_ID, "story_collaboration_draft_group_launch")
        self.assertEqual(FAMILY.command, "dual-draft-group-run")
        self.assertEqual(FAMILY.local_executor,
                         "bcs_e2e.local_draft_group_run.local_draft_group_run")
        self.assertEqual(FAMILY.canonical_status, "LOCAL_SUITE_PASS")

    def test_fixture_face_is_frozen_at_2174_definition_yaml_bytes(self):
        contract = json.loads(CONTRACT_DISK.read_text(encoding="utf-8"))
        self.assertIn("definition_yaml_bytes", contract)
        self.assertNotIn("required_story_arguments", contract)
        self.assertNotIn("source_marker_lines", contract)
        self.assertEqual(contract["definition_yaml_bytes"], 2174)
        self.assertTrue(FIXTURE_DISK.is_file())
        self.assertFalse(FIXTURE_DISK.is_symlink())
        fixture = json.loads(FIXTURE_DISK.read_text(encoding="utf-8"))
        definition_yaml = fixture["revision"]["definition_yaml"]
        self.assertEqual(len(definition_yaml.encode("utf-8")), 2174)
        self.assertEqual(hashlib.sha256(FIXTURE_DISK.read_bytes()).hexdigest(),
                         DG_OBL.COMPANION_SOURCES["indentation-regression.json"])
        self.assertEqual(DG_OBL.EXPECTED_WEBHOOKS, 0)

    def test_zero_atom_parent_disclosure_and_uncharged_probes(self):
        rows = obligations()
        leaves = [row for row in rows if row.level == "DENSITY"]
        plan = density_plan()
        self.assertEqual(set(DG_OBL.ZERO_ATOM_PARENTS), {ZERO_ATOM_PARENT})
        charged = {leaf.parent_obligation for leaf in leaves}
        self.assertNotIn(ZERO_ATOM_PARENT, charged)
        self.assertEqual(set(REQUIREMENTS) - charged, set(DG_OBL.ZERO_ATOM_PARENTS))
        stage_text = "\n".join(
            STORY_DISK.read_text(encoding="utf-8").splitlines()[161:172])
        self.assertIn("edited = source.replace(", stage_text)
        self.assertIn('"group_kind": "normal"', stage_text)
        self.assertIn('"originator": driver["bot_uuid"]', stage_text)
        disclosure = DG_OBL.ZERO_ATOM_PARENTS[ZERO_ATOM_PARENT]
        self.assertIn("story lines 162-172", disclosure)
        self.assertIn("DGL.F49..DGL.F51", disclosure)
        self.assertIn("DGL.F56..DGL.F64", disclosure)
        self.assertIn("DGL.F49..DGL.F51", NOTE)
        self.assertIn("DGL.F56..DGL.F64", NOTE)
        guards = [leaf.obligation_id for leaf in leaves
                  if leaf.parent_obligation
                  == "LOCAL-DRAFT-GROUP.browser-context-submission-guards"]
        binding = [leaf.obligation_id for leaf in leaves
                   if leaf.parent_obligation == "LOCAL-DRAFT-GROUP.group-launch-binding"]
        self.assertEqual(guards, ["DGL.F49", "DGL.F50", "DGL.F51"])
        self.assertEqual(len(binding), 9)
        self.assertEqual(binding[0], "DGL.F56")
        self.assertEqual(binding[-1], "DGL.F64")
        story_text = STORY_DISK.read_text(encoding="utf-8")
        self.assertEqual(story_text.count("self.wait("), 0)
        self.assertEqual(story_text.count("print("), 1)
        uncharged = plan["not_charged_source_probes"]
        self.assertEqual(len(uncharged), 1)
        self.assertIn("terminal marker print", uncharged[0])

    def test_story_arguments_are_ast_derived_and_contract_face_is_absent(self):
        plan = density_plan()
        contract = json.loads(CONTRACT_DISK.read_text(encoding="utf-8"))
        self.assertEqual(plan["story_arguments"]["required"],
                         list(DG_OBL.DERIVED_STORY_ARGUMENTS))
        self.assertEqual(DG_OBL.DERIVED_STORY_ARGUMENTS,
                         ("--base-url", "--human", "--mock-sink-url"))
        self.assertEqual(plan["story_arguments"]["optional"], [])
        self.assertNotIn("required_story_arguments", contract)
        self.assertIn("AST main-block parser add_argument(required=True) rebuild",
                      plan["story_arguments"]["derivation"])
        story_text = STORY_DISK.read_text(encoding="utf-8")
        derived = DG_OBL._derive_required_story_arguments(story_text)
        self.assertEqual(derived, DG_OBL.DERIVED_STORY_ARGUMENTS)
        for flag in DERIVED_FLAGS:
            self.assertIn('"' + flag + '"', story_text)

    def test_story_marker_and_mock_child_faces(self):
        plan = density_plan()
        contract = json.loads(CONTRACT_DISK.read_text(encoding="utf-8"))
        self.assertEqual(plan["story_marker"]["keys"], list(DG_OBL.MARKER_KEYS))
        self.assertEqual(len(plan["story_marker"]["patterns"]), 1)
        self.assertEqual(contract["story_markers"], list(DG_OBL.STORY_MARKERS))
        story_text = STORY_DISK.read_text(encoding="utf-8")
        self.assertIn(MARKER_PREFIX, story_text)
        self.assertIn(MARKER_SUFFIX, story_text)
        rendered = plan["story_marker"]["marker"].format(checks=75)
        self.assertIsNotNone(plan["story_marker"]["patterns"][0].search(rendered))
        self.assertEqual(plan["mock_child"],
                         {"script": "http_provider_judge_mock.py",
                          "ready_protocol": "ready-file", "expected_webhook_requests": 0})

    def test_drift_fails_closed_on_story_bytes(self):
        pristine = STORY_DISK.read_text(encoding="utf-8")
        self.assertGreater(pristine.count("no-store"), 0)
        with tempfile.TemporaryDirectory() as tmp:
            drifted = Path(tmp) / STORY_DISK.name
            drifted.write_text(pristine.replace("no-store", "public"),
                               encoding="utf-8")
            with mock.patch.object(DG_OBL, "STORY_SOURCE", drifted):
                with self.assertRaisesRegex(RunStateError, "SHA-256 drifted"):
                    DG_OBL.obligations()
        obligations()

    def test_drift_fails_closed_on_fixture_and_mock_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            drifted = Path(tmp) / FIXTURE_DISK.name
            drifted.write_bytes(FIXTURE_DISK.read_bytes() + b"\n drift\n")
            with mock.patch.dict(DG_OBL.COMPANION_PATHS,
                                 {"indentation-regression.json": drifted}):
                with self.assertRaisesRegex(RunStateError, "companion source drifted"):
                    DG_OBL.obligations()
        mock_path = DG_OBL.PROVIDER_JUDGE_MOCK
        with tempfile.TemporaryDirectory() as tmp:
            drifted = Path(tmp) / mock_path.name
            drifted.write_bytes(mock_path.read_bytes() + b"\n drift\n")
            with mock.patch.dict(DG_OBL.COMPANION_PATHS,
                                 {"http_provider_judge_mock.py": drifted}):
                with self.assertRaisesRegex(RunStateError, "companion source drifted"):
                    DG_OBL.obligations()
        obligations()

    def test_plan_honesty_face(self):
        plan = density_plan()
        self.assertEqual(plan["schema_version"], 1)
        self.assertEqual(plan["contract"], "draft-group-density-v1")
        self.assertEqual(plan["parent"], PARENT)
        self.assertEqual(plan["executor_row_index"], 40)
        self.assertEqual(plan["coverage_row_no"], 26)
        self.assertEqual(plan["source_file"], DG_OBL.SOURCE_FILE)
        self.assertEqual(plan["parsed_unlocated_count"], 0)
        self.assertEqual(plan["parsed_unlocated"], [])
        self.assertEqual(plan["credential_references"],
                         ["BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"])
        self.assertEqual(plan["remote_validation"], "NOT_RUN")
        self.assertFalse(plan["grants_release_credit"])
        self.assertTrue(plan["source_read_only"])
        self.assertEqual(plan["residual_count_gap"], 0)
        self.assertEqual(plan["delta"], DENSITY_OBLIGATION_COUNT)
        self.assertEqual(plan["mock_child"]["expected_webhook_requests"], 0)
        self.assertIn(ZERO_ATOM_PARENT, plan["projection_rule"])
        self.assertEqual(plan["story_marker"]["marker"], DG_OBL.STORY_MARKERS[0])
        self.assertEqual(plan["named_preprod_gaps"], list(FAMILY.preprod_gaps))


DERIVED_FLAGS = ("--base-url", "--human", "--mock-sink-url")


class DraftGroupDensityReplayTests(unittest.TestCase):
    def test_local_replay_projects_all_92_rows_pass(self):
        receipt = project(mock_observations())
        self.assertEqual(receipt["status"], STATUS_PASS)
        self.assertEqual(receipt["counts"], {STATUS_PASS: 92, STATUS_FAIL: 0,
                                             STATUS_NOT_RUN: 0})
        self.assertEqual(receipt["parsed_obligation_count"], 92)
        self.assertEqual(receipt["parent_obligation_count"], 17)
        self.assertEqual(receipt["density_obligation_count"], 75)
        self.assertEqual(receipt["residual_count_gap"], 0)
        self.assertEqual(receipt["delta"], 75)
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
        self.assertEqual(len(parents), 17)
        self.assertEqual(len(atoms), 75)
        self.assertTrue(all(row["status"] == STATUS_PASS for row in receipt["obligations"]))
        self.assertTrue(all(row["observed"] == STATUS_PASS for row in parents))
        for row in parents:
            self.assertEqual(row["method"], "story_stage")
            self.assertEqual(row["evidence_event"], "legacy_contract_parent_mirror")
        for row in atoms:
            self.assertEqual(row["evidence_event"], "owned_local_story_site_mirror")
            self.assertIn("owned-local credential-free replay", row["classification"])
        slot_counter = Counter(row["slot"] for row in atoms)
        self.assertEqual(slot_counter, FROZEN_SLOT_COUNTS)
        adjudication = receipt["case_adjudication"]
        self.assertEqual(adjudication["case_id"], "DUAL-DRAFT-GROUP-01")
        self.assertEqual(adjudication["parent_id"], STORY_ID)
        self.assertEqual(adjudication["status"], "PASS")
        self.assertEqual(adjudication["completed_count"], 17)
        self.assertEqual(adjudication["total_count"], 17)
        self.assertTrue(adjudication["ready_to_execute"])
        self.assertIsNone(adjudication["error_type"])
        self.assertEqual(len(adjudication["steps"]), 17)
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
        self.assertEqual(family["family"], "draft_group")
        self.assertEqual(family["dual_case_id"], "DUAL-DRAFT-GROUP-01")
        self.assertEqual(family["source_case_id"], "LOCAL-DRAFT-GROUP-01")
        self.assertEqual(family["dual_suite"], "dual-draft-group-v1")
        self.assertEqual(family["source_suite"], "owned-local-draft-group-v1")
        self.assertEqual(family["local_status"], "LOCAL_DRAFT_GROUP_PASS")
        self.assertEqual(family["canonical_status"], "LOCAL_SUITE_PASS")
        self.assertEqual(family["preprod_initial_verdict"], "NOT_RUN")
        self.assertEqual(receipt["executor_row"],
                         {"index": 40, "coverage_row_no": 26,
                          "dual_command": "dual-draft-group-run"})
        self.assertEqual(receipt["source"]["sha256"], DG_OBL.SOURCE_SHA256)
        self.assertEqual(receipt["source"]["companion_sources"], DG_OBL.COMPANION_SOURCES)
        self.assertEqual(receipt["source"]["story_line_span"], list(DG_OBL.STORY_LINE_SPAN))
        self.assertEqual(receipt["story_marker"]["keys"], list(DG_OBL.MARKER_KEYS))
        self.assertEqual(receipt["story_arguments"]["required"],
                         list(DERIVED_FLAGS))
        self.assertEqual(receipt["story_arguments"]["optional"], [])
        self.assertEqual(receipt["mock_child"]["observed_webhook_requests"], 0)
        self.assertEqual(receipt["mock_child"]["expected_webhook_requests"], 0)
        self.assertEqual(receipt["zero_atom_parents"], DG_OBL.ZERO_ATOM_PARENTS)
        self.assertEqual(receipt["not_charged_source_probes"], [DG_OBL.NOT_CHARGED_PROBES])
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
        self.assertEqual(receipt["counts"], {STATUS_PASS: 90, STATUS_FAIL: 0,
                                             STATUS_NOT_RUN: 2})
        by_id = {row["obligation_id"]: row for row in receipt["obligations"]}
        self.assertEqual(by_id[absent_leaf]["status"], STATUS_NOT_RUN)
        self.assertIsNone(by_id[absent_leaf]["error_type"])
        self.assertEqual(by_id[absent_leaf]["evidence_event"], "observation_absent")
        self.assertEqual(by_id[first_parent]["status"], STATUS_NOT_RUN)
        self.assertIsNone(by_id[first_parent]["observed"])
        self.assertIn("never defaults to PASS", by_id[absent_leaf]["classification"])
        self.assertIn("never defaults to PASS", by_id[first_parent]["classification"])
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
        self.assertEqual(in_leaf["id"], "DGL.F74")
        self.assertEqual(in_leaf["expected"], [200, 204])
        observations["atoms"][equals_leaf["id"]] = equals_leaf["expected"] + 1
        observations["atoms"][predicate_leaf["id"]] = False
        observations["atoms"][in_leaf["id"]] = 500
        receipt = project(observations)
        self.assertEqual(receipt["status"], STATUS_FAIL)
        self.assertEqual(receipt["counts"], {STATUS_PASS: 89, STATUS_FAIL: 3,
                                             STATUS_NOT_RUN: 0})
        by_id = {row["obligation_id"]: row for row in receipt["obligations"]}
        self.assertEqual(by_id[equals_leaf["id"]]["error_type"], "OBSERVATION_MISMATCH")
        self.assertEqual(by_id[predicate_leaf["id"]]["status"], STATUS_FAIL)
        self.assertIn("fail-close", by_id[equals_leaf["id"]]["classification"])
        adjudication = receipt["case_adjudication"]
        self.assertEqual(adjudication["status"], "BLOCKED")
        self.assertEqual(adjudication["error_type"], "LOCAL_DYNAMIC_FAMILY_BLOCKED")
        self.assertEqual(adjudication["completed_count"], 0)
        self.assertFalse(adjudication["ready_to_execute"])
        self.assertIsNone(receipt["mock_child"]["observed_webhook_requests"])

    def test_preprod_default_is_fully_not_run_with_named_gaps(self):
        receipt = project(tier="preprod")
        self.assertEqual(receipt["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(receipt["counts"], {STATUS_PASS: 0, STATUS_FAIL: 0,
                                             STATUS_NOT_RUN: 92})
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
            "PREPROD_DRAFT_GROUP_CLI_FACE_NOT_EXECUTED_OFFLINE",
            "PREPROD_BROWSER_IDENTITY_CHAIN_UNVERIFIED",
            "PREPROD_THREE_BOT_AUTHORIZATION_MATRIX_UNVERIFIED",
            "PREPROD_FULL_STORY_NOT_EXECUTED_OFFLINE"])
        adjudication = receipt["case_adjudication"]
        self.assertEqual(adjudication["status"], "BLOCKED")
        self.assertEqual(adjudication["completed_count"], 0)
        self.assertEqual(adjudication["total_count"], 17)
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
        self.assertEqual(receipt["counts"], {STATUS_PASS: 92, STATUS_FAIL: 0,
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
        self.assertEqual(receipt["mock_child"]["observed_webhook_requests"], 0)
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
        self.assertEqual(receipt["counts"], {STATUS_PASS: 91, STATUS_FAIL: 1,
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

    def test_weighted_cache_control_and_envelope_rows_carry_frozen_expectations(self):
        receipt = project(mock_observations())
        by_id = {row["obligation_id"]: row for row in receipt["obligations"]}
        plan = density_plan()
        cache_rows = [row for row in plan["obligations"]
                      if row["level"] == "DENSITY" and row["slot"] == "cache_control"]
        envelope_rows = [row for row in plan["obligations"]
                         if row["level"] == "DENSITY" and row["slot"] == "envelope_code"]
        self.assertEqual(len(cache_rows), 13)
        self.assertEqual(len(envelope_rows), 13)
        for row in cache_rows:
            self.assertEqual(row["expected"], "no-store")
            self.assertEqual(by_id[row["id"]]["observed"], "no-store")
        for row in envelope_rows:
            self.assertEqual(by_id[row["id"]]["observed"], row["expected"])
            self.assertIn(str(row["expected"]),
                          {"20000", "20100", "20400", "40400", "40000", "40300", "40100"})


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
        "bcs_e2e.draft_group_density.registration_proposal")
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


class DraftGroupDensityRegistrationProposalTests(unittest.TestCase):
    def test_registration_proposal_refuses_repeat_registration_live(self):
        # E2b integration landed the row face note; the describe-only
        # proposal fail-closes against the live registered world.
        with self.assertRaises(RunStateError):
            registration_proposal()

    def test_proposal_is_describe_only_with_caliber_and_write_domain(self):
        proposal = _proposal_against_unregistered_snapshot()
        self.assertEqual(proposal["status"], "DESCRIBE_ONLY")
        self.assertEqual(proposal["token"], PROPOSAL_TOKEN)
        self.assertEqual(PROPOSAL_TOKEN, "DRAFT_GROUP_DENSITY_75_OBLIGATIONS_20261031")
        self.assertEqual(proposal["contract"], "draft-group-density-v1")
        self.assertEqual(proposal["parent_story"], "story_collaboration_draft_group_launch")
        self.assertEqual(proposal["upstream_issue"], "#26")
        self.assertEqual(proposal["executor_row_index"], 40)
        self.assertEqual(proposal["coverage_row_no"], 26)
        self.assertEqual(proposal["parsed_obligation_count"], 92)
        self.assertEqual(proposal["parent_obligation_count"], 17)
        self.assertEqual(proposal["density_obligation_count"], 75)
        self.assertEqual(proposal["residual_count_gap"], 0)
        self.assertEqual(proposal["delta"], 75)
        source = proposal["source"]
        self.assertEqual(source["file"], DG_OBL.SOURCE_FILE)
        self.assertEqual(source["sha256"], DG_OBL.SOURCE_SHA256)
        self.assertEqual(source["story_line_span"], list(DG_OBL.STORY_LINE_SPAN))
        self.assertEqual(source["companion_sources"], DG_OBL.COMPANION_SOURCES)
        self.assertEqual(source["migration_contract"],
                         "draft-group-migration-contract.json")
        self.assertTrue(re.fullmatch("[0-9a-f]{40}", source["contract_source_revision"]))
        self.assertEqual(source["contract_source_revision"],
                         json.loads(CONTRACT_DISK.read_text(encoding="utf-8"))["source_revision"])
        self.assertNotIn(_BEARER, json.dumps(proposal))
        self.assertEqual(proposal["family"]["dual_command"], "dual-draft-group-run")
        self.assertEqual(proposal["family"]["local_executor"], FAMILY.local_executor)
        self.assertEqual(proposal["family"]["canonical_status"], "LOCAL_SUITE_PASS")
        self.assertEqual(proposal["zero_atom_parents"], DG_OBL.ZERO_ATOM_PARENTS)
        self.assertEqual(proposal["not_charged_source_probes"], [DG_OBL.NOT_CHARGED_PROBES])
        gaps = proposal["named_preprod_gaps"]
        self.assertEqual([gap["id"] for gap in gaps], list(FAMILY.preprod_gaps))
        self.assertEqual(set(NAMED_GAP_DETAILS), set(FAMILY.preprod_gaps))
        for gap in gaps:
            self.assertEqual(gap["status"], "NAMED_DESIGN_GAP")
            self.assertEqual(gap["blocks"], "preprod PASS credit")
            self.assertTrue(NAMED_GAP_DETAILS[gap["id"]])
        replay_caliber = proposal["replay_caliber"]
        self.assertIn("92/92 PASS", replay_caliber["local"])
        self.assertIn("LOCAL-DRAFT-GROUP-01", replay_caliber["local"])
        self.assertIn("weighted slots", replay_caliber["local"])
        self.assertIn("92/92 NOT_RUN", replay_caliber["preprod"])
        self.assertIn("BLOCKED_INVENTORIED", replay_caliber["preprod"])
        self.assertEqual(len(proposal["red_lines"]), 5)
        self.assertIn("executor-migration.json", " ".join(proposal["red_lines"]))
        self.assertFalse(proposal["new_registration_command"])
        self.assertEqual(proposal["write_domain"],
                         ["bcs_e2e/draft_group_density/**",
                          "tests/test_draft_group_density.py"])
        self.assertEqual(proposal["remote_validation"], "NOT_RUN")
        self.assertFalse(proposal["grants_release_credit"])
        self.assertEqual(proposal["credential_references"],
                         ["BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"])

    def test_coverage_overlay_arithmetic_matches_disk_before_and_after(self):
        coverage_map = json.loads(COVERAGE_MAP_DISK.read_text(encoding="utf-8"))
        disk_row = next(row for row in coverage_map["rows"] if row["no"] == 26)
        self.assertEqual(disk_row["case_id"], "story_collaboration_draft_group_launch")
        self.assertEqual(disk_row["local_projection"]["obligations"], 17)
        self.assertEqual(disk_row["upstream"]["snapshot_parsed_count"], 1)
        self.assertEqual(disk_row["gap"]["parsed_minus_obligations"], -16)
        self.assertEqual(disk_row["queue"], {"rank": 20, "gap_qty": 17, "feasibility": 1,
                                             "score": 17,
                                             "basis": "obl 17×1；LOCAL_ONLY 车道"})
        proposal = _proposal_against_unregistered_snapshot()
        row = proposal["coverage_map_increment"]["row"]
        self.assertEqual(row["no"], 26)
        self.assertEqual(row["case_id"], "story_collaboration_draft_group_launch")
        self.assertEqual(row["before"]["obligations"],
                         disk_row["local_projection"]["obligations"])
        self.assertEqual(row["before"]["snapshot_parsed_count"], 1)
        self.assertEqual(row["before"]["gap_parsed_minus_obligations"], -16)
        after = row["after"]
        self.assertEqual(after["obligations"], 92)
        self.assertEqual(after["snapshot_parsed_count"], 92)
        self.assertEqual(after["gap_parsed_minus_obligations"], 0)
        self.assertEqual(after["gap_kind"], "COUNT_GAP_CLOSED+EXECUTION_GAP")
        self.assertIn("parsed 92 − 义务 92 = 0", after["gap_detail"])
        self.assertEqual(after["queue"]["rank"], 20)
        self.assertEqual(after["queue"]["gap_qty"], 75)
        self.assertEqual(after["queue"]["score"], 75)
        self.assertEqual(after["queue"]["feasibility"], 1)
        self.assertIn("执行缺口按密度口径 75（parsed 92−legacy 17）×1",
                      after["queue"]["basis"])
        self.assertIn("75/75 有序恒等", after["queue"]["basis"])
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
        self.assertIn("| 26 |", MD_ROW)
        self.assertIn(PROPOSAL_TOKEN, MD_ROW)
        self.assertIn("parsed 92 − 义务 92 = 0", MD_ROW)

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
        expected_states = {
            f"{mode}/{verdict}": count
            for (mode, verdict), count
            in inventory_module._EXPECTED_EXECUTOR_MIGRATION_COUNTS.items()
        }
        self.assertEqual(increment["unchanged"]["state_counts"], expected_states)
        self.assertEqual(increment["unchanged"]["gate_counts"], {"MOCK": 2})
        self.assertEqual(Counter(inventory_module._EXPECTED_EXECUTOR_MIGRATION_GATE_COUNTS),
                         Counter({"MOCK": 2}))
        statuses = inventory_module.load_executor_migration()
        self.assertEqual(len(statuses), 41)
        face = statuses["story_collaboration_draft_group_launch"]
        self.assertEqual((face["mode"], face["preprod_verdict"], face["gates"],
                          face["notes"]),
                         ("DUAL", "BLOCKED_INVENTORIED", [], [NOTE]))
        self.assertEqual(increment["unchanged"]["row_face"]["id"], STORY_ID)
        self.assertEqual(increment["unchanged"]["row_face"]["mode"], face["mode"])
        self.assertEqual(increment["unchanged"]["row_face"]["preprod_verdict"],
                         face["preprod_verdict"])
        self.assertEqual(increment["unchanged"]["row_face"]["notes_delta"],
                         "+1 (this proposal note only)")

    def test_describe_only_fragment_carries_token_and_honest_counts(self):
        with _unregistered_executor_migration_world():
            fragment = describe_only_mainline_fragment()
        self.assertIn(PROPOSAL_TOKEN, fragment)
        self.assertIn("row #40", fragment)
        self.assertIn("story_collaboration_draft_group_launch", fragment)
        self.assertIn("92 parsed = 17 legacy parents + 75 atoms", fragment)
        self.assertIn("residual_count_gap=0", fragment)
        self.assertIn("ordered identity DGL catalog 75/75", fragment)
        self.assertIn(ZERO_ATOM_PARENT + " disclosed", fragment)
        self.assertIn("the terminal marker print stays parent-charged", fragment)
        self.assertIn("executor-migration.json notes-only increment", fragment)
        self.assertIn("one EXECUTOR_MIGRATION_NOTES member", fragment)
        self.assertIn("states and gates counters unchanged", fragment)
        self.assertIn("DUAL/BLOCKED_INVENTORIED", fragment)
        self.assertIn("obligations 17 to 92", fragment)
        self.assertIn("snapshot_parsed_count 1 to 92", fragment)
        self.assertIn("queue gap_qty 17 to 75", fragment)
        self.assertIn("committed totals 674/638 stay unchanged", fragment)
        self.assertIn("local replay projects 92/92 PASS", fragment)
        self.assertIn("LOCAL-DRAFT-GROUP-01", fragment)
        self.assertIn("remote_validation=NOT_RUN", fragment)
        self.assertIn("grants_release_credit=false", fragment)
        self.assertIn("New registration command: none", fragment)
        self.assertIn("dual-draft-group-run envelope reused", fragment)
        self.assertIn("bcs_e2e/draft_group_density/**", fragment)
        self.assertIn("tests/test_draft_group_density.py", fragment)
        for gap_id in FAMILY.preprod_gaps:
            self.assertIn(gap_id, fragment)


class DraftGroupDensityHygieneTests(unittest.TestCase):
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
        self.assertEqual(_guard_violations(Path(__file__)), [])
        story_text = STORY_DISK.read_text(encoding="utf-8")
        for row in obligations():
            self.assertTrue(row.quote)
            self.assertIn(row.quote, story_text)

    def test_family_tokens_notes_and_leaf_prefixes_are_distinct_from_result_queue(self):
        rq_module = importlib.import_module(
            "bcs_e2e.result_queue_density.registration_proposal")
        self.assertNotEqual(PROPOSAL_TOKEN, rq_module.PROPOSAL_TOKEN)
        self.assertNotEqual(NOTE, rq_module.NOTE)
        self.assertNotEqual(ZERO_ATOM_PARENT, "RESULT-QUEUE.delivery-policy-replacement")
        rq_plan = importlib.import_module(
            "bcs_e2e.result_queue_density.obligations").density_plan()
        dg_plan = density_plan()
        self.assertNotEqual(rq_plan["leaf_ids"], dg_plan["leaf_ids"])
        self.assertNotEqual(rq_plan["parent_ids"], dg_plan["parent_ids"])
        self.assertTrue(all(leaf.startswith("DGL.F") for leaf in dg_plan["leaf_ids"]))


if __name__ == "__main__":
    unittest.main()
