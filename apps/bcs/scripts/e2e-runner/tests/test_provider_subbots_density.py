"""Density fences and dual-environment projections for roster case #20.

Wave D4 density backfill tests: the 131-leaf density contract must load
fail-closed against every frozen pin (sources, wrapper, frozen ranges,
evidence vocabulary), the local projection must PASS only through the frozen
child receipt collective, and the preprod projection must stay honestly
NOT_RUN with named gaps.  The envelope tests patch the same seams as the
committed dual case-run tests; no network, no preprod, no git, no credential
values are involved.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.profile import ConfigError
from bcs_e2e.provider_subbots.contract import (
    REQUIREMENTS,
    STATUS as LOCAL_STATUS,
    STORY_CHECKS,
)
from bcs_e2e.provider_subbots.density import (
    DENSITY_CONTRACT_PATH,
    DENSITY_LEAVES,
    DENSITY_PREFIX,
    GAP_ADMIN_CHANNEL,
    GAP_CONTROL_PLANE,
    GAP_SIGNING_KEY,
    LOCAL_DENSITY_GAP,
    DensityContractError,
    build_case_density_projection,
    density_contract_digest,
    density_families,
    density_parent_projection,
    load_density_contract,
    project_local_density,
    project_preprod_density,
)
from bcs_e2e.provider_subbots_case_run import (
    CASE_KEYS,
    PROBE_BY_OPERATION,
    dual_provider_subbots_case_run,
    provider_subbots_adaptability,
    provider_subbots_case_plan,
)
from bcs_e2e.run_state import read

RUN_ID = "20261031-migration-wpa-subbots-density-test"
COOKIE_ENV = "BCS_E2E_HUMAN_COOKIE"
BOT_ENV = "BCS_E2E_DRIVER_BOT_TOKEN"
COOKIE_CANARY = "synthetic-cookie-value-never-persisted"
BOT_CANARY = "synthetic-bot-token-never-persisted"
FROZEN_CONTRACT = ROOT / "provider-subbots-migration-contract.json"
UPSTREAM_STORY = ROOT.parent / "e2e-test" / "agentpass_subbots_story.py"
UPSTREAM_WRAPPER = ROOT.parent / "e2e-test" / "e2e.sh"

# An independently authored execution model of the frozen child story:
# 24 pre-group checks, the two sibling-pair GROUP iterations (18 leaves each),
# then 71 trailing checks.  One tuple is one executed live check projection.
HAND_RUNS = (
    [(145, "request-status", 1), (160, "request-status", 1), (163, "semantic", 1),
     (175, "request-status", 6), (183, "request-status", 1), (190, "semantic", 1),
     (194, "semantic", 1), (204, "request-status", 1), (213, "request-status", 1),
     (224, "request-status", 1), (234, "request-status", 4), (239, "request-status", 1),
     (245, "semantic", 1), (249, "semantic", 1), (259, "semantic", 1),
     (263, "request-status", 1)]
    + [(line, kind, count) for _ in range(2) for line, kind, count in (
        (274, "self-view", 7), (275, "self-view", 7), (276, "self-view", 2),
        (277, "request-status", 1), (284, "request-status", 1))]
    + [(292, "request-status", 1), (299, "request-status", 1), (307, "request-status", 1),
       (314, "semantic", 1), (318, "self-view", 7), (320, "request-status", 1),
       (328, "self-view", 7), (331, "request-status", 1), (338, "request-status", 4),
       (345, "request-status", 1), (352, "semantic", 1), (366, "request-status", 1),
       (375, "semantic", 1), (379, "request-status", 1), (388, "request-status", 1),
       (408, "request-status", 1), (411, "request-status", 1), (412, "self-view", 2),
       (413, "self-view", 2), (414, "request-status", 1), (421, "request-status", 1),
       (430, "request-status", 1), (431, "self-view", 7), (432, "self-view", 7),
       (433, "request-status", 1), (440, "request-status", 1), (446, "self-view", 2),
       (447, "self-view", 7), (448, "request-status", 1), (455, "request-status", 1),
       (463, "request-status", 1), (469, "request-status", 1), (475, "self-view", 2),
       (476, "request-status", 1)]
)


def provider_profile(*, preprod: bool = False) -> dict:
    value = {
        "schema_version": 1,
        "contract": "dual-provider-subbots-profile-v1",
        "environment": {
            "tier": "preprod" if preprod else "local",
            "id": "provider-subbots-preprod" if preprod else "provider-subbots-local",
            "expected_bcs_env": "pre" if preprod else "local",
        },
        "entrypoint": (
            {
                "kind": "gateway",
                "origin": "https://bcs-pre.alipay.com",
                "api_face": "legacy",
                "manifest_face": "legacy",
            }
            if preprod
            else {"kind": "owned-local"}
        ),
        "human": {"user_id": "39464" if preprod else "e2e-human-fixture"},
        "auth": (
            {
                "kind": "preprod-human-cookie",
                "cookie_env": COOKIE_ENV,
                "probe_bot_token_env": BOT_ENV,
            }
            if preprod
            else {"kind": "owned-local"}
        ),
        "fixture": {
            "webhook_url": "https://provider.example.com/bcs/webhook",
            "takeover_bot_id": "20261031-migration-wpa-subbots-bot",
        },
        "policy": {
            "fixture_pool": "20261031-migration-wpa-subbots",
            "exclusive_use_confirmed": True,
            "timeout_seconds": 10,
            "max_response_bytes": 65536,
            "failure_retention_hours": 24,
        },
        "cli": {
            "transport": "product-http" if preprod else "owned-local-story",
            "command_family": "provider-subbots",
            "api_face": "legacy",
        },
    }
    if preprod:
        value["browser_origin"] = "https://teamclaw-pre.alipay.com"
        value["credential_files"] = {
            COOKIE_ENV: "/Users/example/.codex/bcs-e2e/preprod-human-cookie.txt",
            BOT_ENV: "/Users/example/.codex/bcs-e2e/preprod-driver-bot-token-xq.txt",
        }
        value["route_bindings"] = {
            operation: {
                "method": row["canonical_method"],
                "path": row["path"],
                "api_face": "legacy",
                "auth": row["caller"],
                "response": "legacy-json",
                "traceparent": "disabled",
            }
            for operation, row in PROBE_BY_OPERATION.items()
        }
    return value


def local_result(*, observed: int = STORY_CHECKS, checked=None) -> dict:
    return {
        "status": LOCAL_STATUS,
        "checks": {"status": "PASS", "checked": list(REQUIREMENTS) if checked is None else checked},
        "child_receipt": {"observed_checks": observed},
        "canonical_summary": {"status": "LOCAL_SUITE_PASS"},
    }


def passing_probe() -> dict:
    return {
        "status": "PASS",
        "error_type": None,
        "baseline": {"http_status": 404},
        "checks": [{"operation": operation} for operation in PROBE_BY_OPERATION],
        "present_count": len(PROBE_BY_OPERATION),
        "absent_count": 0,
        "required_count": len(PROBE_BY_OPERATION),
        "credential_kind": "preprod-human-cookie",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }


def write_profile(value: dict, directory: Path, name: str) -> Path:
    path = directory / f"profile-{name}.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class _TamperRoom:
    """Provide one controlled temporary directory per fence test."""

    def __init__(self, test: unittest.TestCase) -> None:
        self._test = test
        self._directory = tempfile.TemporaryDirectory(prefix="density-tamper-")
        test.addCleanup(self._directory.cleanup)

    def path(self, value: object, name: str) -> Path:
        path = Path(self._directory.name) / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path


class DensityContractFenceTests(unittest.TestCase):
    def setUp(self):
        self.contract = load_density_contract()
        self.leaves = self.contract["obligations"]

    def test_contract_top_level_shape_is_bounded(self):
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(self.contract["contract"], "provider-subbots-density-obligations-v1")
        self.assertEqual(self.contract["density_prefix"], DENSITY_PREFIX)
        self.assertEqual(self.contract["parent"], "story_provider_operator_manages_subbots")
        self.assertEqual(self.contract["source_case_id"], "LOCAL-PROVIDER-SUBBOTS-01")
        self.assertEqual(self.contract["story_checks"], 131)
        self.assertEqual(len(self.contract), 18)
        self.assertEqual(self.contract["roster"]["density_delta"], 116)
        self.assertEqual(self.contract["roster"]["local_obligations_before"], 15)
        self.assertEqual(self.contract["roster"]["upstream_parsed_wrappers"], 1)

    def test_leaves_carry_the_full_obligation_space(self):
        self.assertEqual(len(self.leaves), DENSITY_LEAVES)
        self.assertEqual([leaf["check_no"] for leaf in self.leaves], list(range(1, 132)))
        ids = [leaf["id"] for leaf in self.leaves]
        self.assertEqual(len(set(ids)), 131)
        for leaf in self.leaves:
            self.assertTrue(re.fullmatch(
                r"PROVIDER-SUBBOTS-DENSITY\.[a-z0-9-]+\.[0-9]{3}", leaf["id"]))
            self.assertEqual(leaf["parent"], "PROVIDER-SUBBOTS." + leaf["family"])
            self.assertEqual(set(leaf), {
                "id", "check_no", "family", "parent", "kind", "subkind", "title",
                "upstream_label", "expectation", "method", "path", "expected_status",
                "actor", "actor_headers", "anchor", "projection", "evidence", "local",
                "preprod"})
        kinds = Counter(leaf["kind"] for leaf in self.leaves)
        self.assertEqual(kinds, {"request-status": 47, "self-view": 75, "semantic": 9})
        families = Counter(leaf["family"] for leaf in self.leaves)
        self.assertEqual(families, {
            "provider-registration-discovery": 3, "admin-complete-ref-validation": 6,
            "admin-sibling-registration": 3, "scoped-complete-ref-validation": 6,
            "scoped-sibling-distinct-identity": 4, "duplicate-reference-conflicts": 2,
            "self-lookup-actor-binding": 34, "legacy-api-actor-binding": 4,
            "metadata-update-parent-immutability": 9, "onboard-parent-immutability": 8,
            "parent-discovery-fail-closed": 7, "callback-actor-binding": 4,
            "signed-principal-live-state": 1, "disable-enable-state": 23,
            "deletion-tombstone-retirement": 17})

    def test_independent_execution_model_is_isomorphic_to_the_emitted_leaves(self):
        hand = [(line, kind) for line, kind, count in HAND_RUNS for _ in range(count)]
        self.assertEqual(len(hand), 131)
        emitted = [(leaf["anchor"]["site_line"], leaf["kind"]) for leaf in self.leaves]
        self.assertEqual(emitted, hand)
        self.assertEqual(Counter(emitted), Counter(hand))

    def test_parent_projection_is_surjective_onto_the_fifteen_frozen_obligations(self):
        families = density_families(self.contract)
        self.assertEqual(families, [name.split(".", 1)[1] for name in REQUIREMENTS])
        projection = density_parent_projection(self.contract)
        self.assertEqual(sum(projection.values()), 131)
        frozen = json.loads(FROZEN_CONTRACT.read_text(encoding="utf-8"))
        ranges = {row["id"]: list(row["story_lines"]) for row in frozen["requirements"]}
        for row in self.contract["parent_projection"]:
            self.assertEqual([row["story_lines"][0], row["story_lines"][1]],
                             ranges[row["parent"]])
        exceptional = [leaf for leaf in self.leaves
                       if leaf["projection"]["basis"] == "documented-exception"]
        self.assertEqual(len(exceptional), 1)
        self.assertEqual(exceptional[0]["anchor"]["site_line"], 204)
        self.assertEqual(exceptional[0]["parent"],
                         "PROVIDER-SUBBOTS.duplicate-reference-conflicts")
        semantic = [leaf for leaf in self.leaves if leaf["kind"] == "semantic"]
        self.assertEqual(len(semantic), 9)
        self.assertTrue(all(leaf["projection"]["basis"] == "density-semantic-declaration"
                            for leaf in semantic))
        declared = semantic[[leaf["anchor"]["site_line"] for leaf in semantic].index(249)]
        self.assertEqual(declared["parent"], "PROVIDER-SUBBOTS.scoped-sibling-distinct-identity")

    def test_anchor_slices_wrapper_lines_and_evidence_vocabulary_are_pinned(self):
        story_text = UPSTREAM_STORY.read_text(encoding="utf-8")
        story_lines = story_text.splitlines()
        for leaf in self.leaves:
            anchor = leaf["anchor"]
            self.assertEqual(anchor["site_slice"], "\n".join(
                story_lines[anchor["site_line"] - 1:anchor["site_end_line"]]))
            self.assertIn(anchor["label_literal"], story_text)
        segments = set(re.findall(r"evidence\.get\('([a-z_]+)'\)",
                                  Path(ROOT / "bcs_e2e" / "provider_subbots" / "checks.py")
                                  .read_text(encoding="utf-8")))
        vocabulary = set(re.findall(r"'([a-z][a-z0-9_]*)'",
                                    Path(ROOT / "bcs_e2e" / "provider_subbots" / "checks.py")
                                    .read_text(encoding="utf-8")))
        null_primary = [leaf for leaf in self.leaves
                        if leaf["evidence"]["primary_segment"] is None]
        self.assertEqual([leaf["check_no"] for leaf in null_primary], [14])
        for leaf in self.leaves:
            evidence = leaf["evidence"]
            if evidence["primary_segment"] is not None:
                self.assertIn(evidence["primary_segment"], segments)
                self.assertEqual(evidence["primary_path"][0], evidence["primary_segment"])
                self.assertTrue(all(item in vocabulary for item in evidence["primary_path"]))
            for entry in evidence["cross_segment_validation"] or ():
                self.assertIn(entry["segment"], segments)
                self.assertIn(entry["field"], vocabulary)
        disabled = [leaf for leaf in self.leaves if leaf["anchor"]["site_line"] == 414]
        self.assertEqual(disabled[0]["evidence"]["cross_segment_validation"],
                         [{"field": "disabled_status", "segment": "signed_principal"}])
        deleted = [leaf for leaf in self.leaves if leaf["anchor"]["site_line"] == 448]
        self.assertEqual(deleted[0]["evidence"]["cross_segment_validation"],
                         [{"field": "deleted_status", "segment": "signed_principal"}])

    def test_source_digests_wrapper_shape_and_mirror_marker_are_pinned(self):
        sources = self.contract["sources"]
        declared = self.contract["sources_relative"]
        self.assertEqual(sources["agentpass_subbots_story.py"],
                         hashlib.sha256(UPSTREAM_STORY.read_bytes()).hexdigest())
        self.assertEqual(sources["e2e.sh"],
                         hashlib.sha256(UPSTREAM_WRAPPER.read_bytes()).hexdigest())
        self.assertEqual(declared["story"], "apps/bcs/scripts/e2e-test/agentpass_subbots_story.py")
        wrapper = self.contract["wrapper"]
        self.assertEqual(wrapper["lines"], [97, 103])
        shell = UPSTREAM_WRAPPER.read_text(encoding="utf-8")
        self.assertEqual(shell.count("story_provider_operator_manages_subbots() {"), 1)
        wrapper_slice = "\n".join(shell.splitlines()[96:103])
        self.assertIn("agentpass_subbots_story.py", wrapper_slice)
        self.assertIn(wrapper["marker_assertion"], wrapper_slice)
        mirror = (ROOT / "bcs_e2e" / "provider_subbots" / "story.py").read_text(
            encoding="utf-8")
        self.assertIn("MARKER_PREFIX + str(story.checks) + MARKER_SUFFIX", mirror)

    def test_density_contract_digest_is_stable_and_raw(self):
        import hashlib
        self.assertEqual(density_contract_digest(),
                         hashlib.sha256(DENSITY_CONTRACT_PATH.read_bytes()).hexdigest())
        self.assertEqual(density_contract_digest(), density_contract_digest())
        self.assertTrue(DENSITY_CONTRACT_PATH.is_file())
        self.assertFalse(DENSITY_CONTRACT_PATH.is_symlink())

    def test_load_fences_fail_closed_on_every_drift_axis(self):
        base = json.loads(DENSITY_CONTRACT_PATH.read_text(encoding="utf-8"))
        cases = {}

        value = copy.deepcopy(base)
        value.pop("roster")
        cases["missing top-level key"] = value

        value = copy.deepcopy(base)
        value["extra_block"] = {}
        cases["unknown top-level key"] = value

        duplicate = DENSITY_CONTRACT_PATH.read_text(encoding="utf-8")
        cases["duplicate JSON key"] = ("__raw__", duplicate.replace(
            '{\n  "accounting"', '{\n  "contract": "tampered",\n  "accounting"', 1))

        value = copy.deepcopy(base)
        value["obligations"][0]["check_no"], value["obligations"][1]["check_no"] = (
            value["obligations"][1]["check_no"], value["obligations"][0]["check_no"])
        cases["check_no order"] = value

        value = copy.deepcopy(base)
        value["obligations"][1]["id"] = value["obligations"][0]["id"]
        cases["duplicate leaf id"] = value

        value = copy.deepcopy(base)
        value["obligations"][0]["parent"] = "PROVIDER-SUBBOTS.deletion-tombstone-retirement"
        cases["family-parent mismatch"] = value

        value = copy.deepcopy(base)
        value["obligations"][0]["projection"]["basis"] = "story-lines-range"
        cases["basis accounting drift"] = value

        value = copy.deepcopy(base)
        value["obligations"][0]["anchor"]["label_literal"] = "drifted label literal"
        cases["anchor label literal"] = value

        value = copy.deepcopy(base)
        value["obligations"][0]["projection"]["story_lines"] = [1, 1]
        cases["projection range drift"] = value

        value = copy.deepcopy(base)
        value["sources"]["e2e.sh"] = "0" * 64
        cases["pinned source digest"] = value

        value = copy.deepcopy(base)
        value["obligations"][4]["evidence"]["primary_segment"] = "unknown_segment"
        cases["evidence vocabulary"] = value

        value = copy.deepcopy(base)
        value["wrapper"]["lines"] = [97, 104]
        cases["wrapper line span"] = value

        value = copy.deepcopy(base)
        value["obligations"][0]["preprod"]["status"] = "PASS"
        cases["remote PASS fabrication"] = value

        value = copy.deepcopy(base)
        value["obligations"][14]["evidence"]["primary_segment"] = None
        value["obligations"][14]["evidence"]["primary_path"] = None
        cases["null primary outside L213"] = value

        value = copy.deepcopy(base)
        value["accounting"]["total"] = 130
        cases["accounting denominator"] = value

        room = _TamperRoom(self)
        for name, payload in cases.items():
            with self.subTest(drift=name):
                if isinstance(payload, tuple) and payload[0] == "__raw__":
                    path = room.path("__raw__", name.replace(" ", "-") + ".json")
                    path.write_text(payload[1], encoding="utf-8")
                else:
                    path = room.path(payload, name.replace(" ", "-") + ".json")
                with self.assertRaises(DensityContractError):
                    load_density_contract(path)


class DensityProjectionTests(unittest.TestCase):
    def setUp(self):
        self.contract = load_density_contract()

    def test_project_local_density_passes_only_through_the_collective(self):
        projection = project_local_density(local_result())
        self.assertEqual(projection["status"], "PASS")
        self.assertIsNone(projection["named_gap"])
        self.assertEqual(projection["counts"], {"pass": 131, "not_run": 0})
        self.assertEqual(projection["derivation"], "FROZEN_CHILD_RECEIPT_COLLECTIVE")
        self.assertEqual(projection["collective"]["observed_checks"], 131)
        self.assertEqual(projection["collective"]["checked_count"], 15)
        self.assertEqual(projection["collective"]["checked_requirement_ids"],
                         sorted(REQUIREMENTS))
        self.assertEqual([leaf["status"] for leaf in projection["leaves"]], ["PASS"] * 131)
        self.assertEqual({leaf["basis"] for leaf in projection["leaves"]},
                         {"FROZEN_CHILD_RECEIPT_COLLECTIVE"})

    def test_project_local_density_keeps_every_leaf_honest_on_partial_receipts(self):
        variants = {
            "short observation": local_result(observed=130),
            "blocked checks": dict(local_result(), checks={"status": "BLOCKED", "checked": []}),
            "missing requirement": local_result(checked=list(REQUIREMENTS)[:-1]),
            "blocked status": dict(local_result(), status="BLOCKED"),
            "empty receipt": {},
        }
        for name, receipt in variants.items():
            with self.subTest(receipt=name):
                projection = project_local_density(receipt)
                self.assertEqual(projection["status"], "BLOCKED")
                self.assertEqual(projection["named_gap"], LOCAL_DENSITY_GAP)
                self.assertEqual(projection["counts"], {"pass": 0, "not_run": 131})
                self.assertEqual(
                    [leaf["status"] for leaf in projection["leaves"]], ["NOT_RUN"] * 131)
                self.assertEqual(
                    {leaf["named_gap"] for leaf in projection["leaves"]},
                    {LOCAL_DENSITY_GAP})
        partial = project_local_density(local_result(observed=130, checked=[]))
        self.assertEqual(partial["collective"]["observed_checks"], 130)
        self.assertEqual(partial["collective"]["checked_count"], 0)

    def test_project_preprod_density_is_blocked_inventoried_with_named_gaps(self):
        projection = project_preprod_density()
        self.assertEqual(projection["status"], "BLOCKED_INVENTORIED")
        self.assertEqual(projection["remote_validation"], "NOT_RUN")
        self.assertEqual(projection["network_requests"], 0)
        self.assertIs(projection["grants_release_credit"], False)
        self.assertEqual(set(projection["documented_gaps"]),
                         {GAP_CONTROL_PLANE, GAP_SIGNING_KEY, GAP_ADMIN_CHANNEL})
        self.assertIn("AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE",
                      projection["documented_gaps"][GAP_SIGNING_KEY])
        self.assertIn("provider_admin_token",
                      projection["documented_gaps"][GAP_ADMIN_CHANNEL])
        self.assertEqual(projection["counts"], {"pass": 0, "not_run": 131})
        self.assertEqual([leaf["status"] for leaf in projection["leaves"]],
                         ["NOT_RUN"] * 131)
        self.assertEqual({leaf["network_requests"] for leaf in projection["leaves"]}, {0})
        signing = [leaf["id"] for leaf in projection["leaves"]
                   if GAP_SIGNING_KEY in leaf["gap_refinements"]]
        admin = [leaf["id"] for leaf in projection["leaves"]
                 if GAP_ADMIN_CHANNEL in leaf["gap_refinements"]]
        self.assertEqual(len(signing), 4)
        self.assertEqual(len(admin), 15)
        self.assertEqual(projection["leaf_counts_by_gap_refinement"],
                         {GAP_ADMIN_CHANNEL: 15, GAP_SIGNING_KEY: 4})

    def test_build_case_density_projection_covers_both_tiers_honestly(self):
        local = build_case_density_projection("local", local_result(), None)
        self.assertEqual(local["tier"], "local")
        self.assertEqual(local["status"], "PASS")
        self.assertEqual(local["leaves_total"], 131)
        preprod = build_case_density_projection("preprod", None, passing_probe())
        self.assertEqual(preprod["tier"], "preprod")
        self.assertEqual(preprod["status"], "BLOCKED_INVENTORIED")
        self.assertEqual(preprod["probe_status"], "PASS")
        self.assertEqual([leaf["status"] for leaf in preprod["leaves"]],
                         ["NOT_RUN"] * 131)
        self.assertIsNone(build_case_density_projection("preprod", None, None)["probe_status"])
        with self.assertRaisesRegex(DensityContractError, "local or preprod tier"):
            build_case_density_projection("staging", None, None)
        self.assertTrue(issubclass(DensityContractError, RuntimeError))


class EnvelopeDensityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_local_envelope_reports_the_density_pass_projection(self):
        profile_path = write_profile(provider_profile(), self.root, "local")
        with patch(
            "bcs_e2e.provider_subbots_case_run.local_provider_subbots_run",
            return_value=local_result(),
        ):
            report = dual_provider_subbots_case_run(
                profile_path=profile_path,
                case_key=CASE_KEYS[0],
                state_dir=self.root / "local-state",
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
                binary=self.root / "bcs-fixture",
                binary_sha256="0" * 64,
            )
        self.assertEqual(report["status"], "PASS")
        projection = report["density_projection"]
        self.assertEqual(projection["tier"], "local")
        self.assertEqual(projection["status"], "PASS")
        self.assertEqual(projection["counts"], {"pass": 131, "not_run": 0})
        self.assertEqual(len(projection["leaves"]), 131)
        self.assertIsNone(projection["named_gap"])
        self.assertEqual({leaf["status"] for leaf in projection["leaves"]}, {"PASS"})
        self.assertEqual({leaf["basis"] for leaf in projection["leaves"]},
                         {"FROZEN_CHILD_RECEIPT_COLLECTIVE"})
        summary = report["assertion_summary"]
        self.assertEqual(summary["density_leaf_total"], 131)
        self.assertEqual(summary["density_leaf_pass_count"], 131)
        self.assertEqual(summary["density_leaf_not_run_count"], 0)
        binding = read(self.root / "local-state" /
                       "provider-subbots-provider-subbots-binding.json")
        self.assertEqual(binding["density_leaves"], 131)
        self.assertEqual(binding["density_contract_digest"], density_contract_digest())
        receipts = "".join(path.read_text() for path in
                           sorted((self.root / "local-state").glob("*.json")))
        self.assertNotIn(COOKIE_CANARY, receipts)
        self.assertNotIn(BOT_CANARY, receipts)

    def test_preprod_envelope_never_fabricates_remote_density_pass(self):
        profile_path = write_profile(provider_profile(preprod=True), self.root, "preprod")
        with patch(
            "bcs_e2e.provider_subbots_case_run.run_preprod_route_probe",
            return_value=passing_probe(),
        ):
            report = dual_provider_subbots_case_run(
                profile_path=profile_path,
                case_key=CASE_KEYS[0],
                state_dir=self.root / "preprod-state",
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
            )
        self.assertEqual(report["status"], "BLOCKED")
        projection = report["density_projection"]
        self.assertEqual(projection["status"], "BLOCKED_INVENTORIED")
        self.assertEqual(projection["probe_status"], "PASS")
        self.assertIn("metadata only", projection["probe_semantics"])
        self.assertEqual([leaf["status"] for leaf in projection["leaves"]],
                         ["NOT_RUN"] * 131)
        self.assertEqual({leaf["network_requests"] for leaf in projection["leaves"]}, {0})
        self.assertEqual(set(projection["documented_gaps"]),
                         {GAP_CONTROL_PLANE, GAP_SIGNING_KEY, GAP_ADMIN_CHANNEL})
        self.assertEqual(projection["leaf_counts_by_gap_refinement"],
                         {GAP_ADMIN_CHANNEL: 15, GAP_SIGNING_KEY: 4})
        self.assertEqual(report["assertion_summary"]["density_leaf_not_run_count"], 131)
        self.assertEqual(report["remote_validation"], "NOT_RUN")
        self.assertIs(report["grants_release_credit"], False)

    def test_envelope_partial_child_receipt_projects_density_not_run(self):
        profile_path = write_profile(provider_profile(), self.root, "partial")
        with patch(
            "bcs_e2e.provider_subbots_case_run.local_provider_subbots_run",
            return_value=local_result(observed=130),
        ):
            report = dual_provider_subbots_case_run(
                profile_path=profile_path,
                case_key=CASE_KEYS[0],
                state_dir=self.root / "partial-state",
                run_id=RUN_ID,
                allow_write=True,
                confirm_run_id=RUN_ID,
                binary=self.root / "bcs-fixture",
                binary_sha256="0" * 64,
            )
        self.assertEqual(report["status"], "BLOCKED")
        projection = report["density_projection"]
        self.assertEqual(projection["status"], "BLOCKED")
        self.assertEqual(projection["named_gap"], LOCAL_DENSITY_GAP)
        self.assertEqual(projection["collective"]["observed_checks"], 130)
        self.assertEqual([leaf["status"] for leaf in projection["leaves"]],
                         ["NOT_RUN"] * 131)

    def test_envelope_state_dir_and_confirmation_gates_stay_fail_closed(self):
        profile_path = write_profile(provider_profile(), self.root, "gates")
        with self.assertRaisesRegex(ConfigError, "exact Run confirmation"):
            dual_provider_subbots_case_run(
                profile_path=profile_path, case_key=CASE_KEYS[0],
                state_dir=self.root / "unused", run_id=RUN_ID, allow_write=True,
                confirm_run_id="different", binary=self.root / "bcs-fixture",
                binary_sha256="0" * 64)
        with self.assertRaisesRegex(ConfigError, "explicit write permission"):
            dual_provider_subbots_case_run(
                profile_path=profile_path, case_key=CASE_KEYS[0],
                state_dir=self.root / "unused", run_id=RUN_ID, allow_write=False,
                confirm_run_id=RUN_ID, binary=self.root / "bcs-fixture",
                binary_sha256="0" * 64)
        with self.assertRaisesRegex(ConfigError, "case must be provider-subbots"):
            dual_provider_subbots_case_run(
                profile_path=profile_path, case_key="someone-else",
                state_dir=self.root / "unused", run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID, binary=self.root / "bcs-fixture",
                binary_sha256="0" * 64)
        existing = self.root / "existing-state"
        existing.mkdir()
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_provider_subbots_case_run(
                profile_path=profile_path, case_key=CASE_KEYS[0],
                state_dir=existing, run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID, binary=self.root / "bcs-fixture",
                binary_sha256="0" * 64)
        with self.assertRaisesRegex(ConfigError, "fresh private state directory"):
            dual_provider_subbots_case_run(
                profile_path=profile_path, case_key=CASE_KEYS[0],
                state_dir=Path("relative-state"), run_id=RUN_ID, allow_write=True,
                confirm_run_id=RUN_ID, binary=self.root / "bcs-fixture",
                binary_sha256="0" * 64)

    def test_frozen_layers_and_envelope_plan_carry_the_density_fields(self):
        plan = provider_subbots_case_plan()
        self.assertEqual(plan["requirement_count"], 15)
        self.assertEqual(plan["story_checks"], 131)
        self.assertEqual(plan["density_leaves"], 131)
        self.assertEqual(plan["density_contract"],
                         "provider-subbots-density-obligations-v1")
        self.assertEqual(plan["density_prefix"], DENSITY_PREFIX)
        self.assertEqual(plan["density_contract_digest"], density_contract_digest())
        adaptability = provider_subbots_adaptability()
        self.assertEqual(adaptability["density_leaves"], 131)
        self.assertEqual(adaptability["density_local_derivation"],
                         "FROZEN_CHILD_RECEIPT_COLLECTIVE")
        self.assertEqual(adaptability["density_preprod_status"], "BLOCKED_INVENTORIED")
        self.assertEqual(adaptability["density_remote_validation"], "NOT_RUN")
        self.assertIs(adaptability["density_grants_release_credit"], False)
        self.assertEqual(adaptability["required_assertion_count"], 131)
        frozen = json.loads(FROZEN_CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(len(frozen["requirements"]), 15)
        self.assertEqual(frozen["story_checks"], 131)

    def test_credential_hygiene_and_line_guards(self):
        signing_env_assignment = re.compile(
            r"AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE\s*=\s*[\"'][^\"']+")
        canary = re.compile(r"synthetic-cookie-value|synthetic-bot-token")
        products = [
            Path(ROOT / "bcs_e2e" / "provider_subbots" / "density.py"),
            Path(ROOT / "bcs_e2e" / "provider_subbots" / "build_density_contract.py"),
            DENSITY_CONTRACT_PATH,
            Path(ROOT / "bcs_e2e" / "provider_subbots_case_run.py"),
        ]
        for path in products:
            with self.subTest(file=str(path)):
                text = path.read_text(encoding="utf-8")
                self.assertIsNone(signing_env_assignment.search(text))
                self.assertIsNone(canary.search(text))
        scanned = products + [Path(__file__)]
        for path in scanned:
            with self.subTest(lines=str(path)):
                if path.suffix == ".py":
                    self.assertLessEqual(
                        len(path.read_text(encoding="utf-8").splitlines()), 1000)
                else:
                    self.assertLessEqual(path.stat().st_size, 8 * 1024 * 1024)
        self.assertNotIn("avernet-dev-signing-key", DENSITY_CONTRACT_PATH.read_text())


if __name__ == "__main__":
    unittest.main()
