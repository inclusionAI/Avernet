"""Focused offline tests for the provider-publishes density wave E1.

Covers the 98-leaf source-anchored roster build (unique frozen ids, 32 retained
legacy PARENT envelopes + 66 density leaves, the 32/44/22 net-inflow partition,
operation-table joins, function-span containment and verbatim upstream quotes),
the reconciliation conservation (parsed 98 == frozen contract == roster, legacy
B-tree 32 disclosed, residual_count_gap 0, two uncounted admission
preconditions), the SHA/span/partition fail-close drift negatives, the offline
owned-local replay PASS projection (default recorded-verdict mirror and a fully
injected true-evaluation fixture), the honest preprod NOT_RUN fail-close with
its named gap registry including the r8-recorded blocker verbatim, the cascade
and evidence-absent disciplines, envelope key compatibility with the committed
dual receipt, and the describe-only registration proposal.  No socket is
opened, no process is spawned, no credential value is resolved, and no shared
face file is touched by this module.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bcs_e2e.provider_publishes import contract, density, density_run  # noqa: E402
from bcs_e2e.provider_publishes.build_contract import generate  # noqa: E402
from bcs_e2e.provider_publishes.dual_case_run import (  # noqa: E402
    COMMAND, NAMED_GAPS, REPORT_CONTRACT)
from bcs_e2e.provider_publishes import (  # noqa: E402
    registration_proposal as registration_proposal_module)
from bcs_e2e.provider_publishes.registration_proposal import (  # noqa: E402
    ANCHORS, REPO_ROOT, PROPOSAL_TOKEN, assert_anchors,
    describe_only_mainline_fragment, registration_proposal)
from bcs_e2e.profile import ConfigError  # noqa: E402
from bcs_e2e.run_state import RunStateError  # noqa: E402

STORIES = ROOT.parent / "e2e-test" / "stories.sh"
OLD_CONTRACT = ROOT / "provider-migration-contract.json"
EXECUTOR_JSON = ROOT / "executor-migration.json"
PARENT_STORY = "story_provider_operator_publishes_agent"
HELPER_FN = "_story_provider_manages_organization"
PARENT_SPAN = (1422, 1607)
HELPER_SPAN = (1609, 1724)

PROV_ID = "prv-density-fixture"
SLUG = "slug-density-fixture"
RENAMED = SLUG + "-renamed"
BOT_UUID = "bot-density-fixture"
BOT_REF = "density-fixture-ref"
ORG_CODE = "org-density-fixture"
ADMIN_RUN = "run-density-fixture"
LEGACY_PROJECTION = {"provider_id": PROV_ID, "slug": SLUG, "name": "E2E Provider"}

HINTS = {
    "provider_id": PROV_ID,
    "provider_slug": SLUG,
    "renamed_slug": RENAMED,
    "provider_bot_uuid": BOT_UUID,
    "provider_bot_ref": BOT_REF,
    "organization_code": ORG_CODE,
    "admin_run_id": ADMIN_RUN,
    "legacy_projection": LEGACY_PROJECTION,
}


def _local_profile(run_id="density-offline-replay-test") -> dict:
    return {"schema_version": 1, "contract": density_run.DENSITY_PROFILE_CONTRACT,
            "run_id": run_id,
            "environment": {"tier": "local", "id": "s04-density-local"},
            "entrypoint": {"kind": "offline_in_memory_replay"}}


def _preprod_profile(run_id="density-offline-preprod-test") -> dict:
    return {"schema_version": 1, "contract": density_run.DENSITY_PROFILE_CONTRACT,
            "run_id": run_id,
            "environment": {"tier": "preprod", "id": "s04-density-preprod"},
            "entrypoint": {"kind": "blocked-no-network"}}


def _story_lines() -> list[str]:
    return STORIES.read_text(encoding="utf-8").splitlines()


def _function_span(lines: list[str], name: str) -> tuple[int, int]:
    start = lines.index(name + "() {") + 1
    end = start + lines[start:].index("}") + 1
    return start, end


def _true_eval_observations() -> tuple[dict, dict]:
    """Deterministic offline演算 fixture; constants chosen against frozen expectations."""
    bodies = {
        "register": {"provider_id": PROV_ID},
        "slug-read": {"auth_mode": "static_bearer", "created_at": "2026-10-31",
                      "enabled": True, "name": "E2E Provider",
                      "protocol_version": "2.0", "provider_id": PROV_ID,
                      "slug": SLUG, "updated_at": "2026-10-31"},
        "collaboration-slug-read": dict(LEGACY_PROJECTION),
        "collaboration-slug-missing": {},
        "collaboration-slug-invalid": {},
        "slug-missing": {},
        "slug-invalid": {},
        "duplicate-slug-conflict": {},
        "read": {"provider_id": PROV_ID, "auth_mode": "static_bearer",
                 "disabled": False},
        "update": {"name": "E2E Provider Updated", "provider_id": PROV_ID},
        "old-slug-missing": {},
        "renamed-slug-read": {"provider_id": PROV_ID,
                              "name": "E2E Provider Updated"},
        "bot-publish": {"bot_uuid": BOT_UUID, "bot_runtime_token": "tok-fixture",
                        "provider_bot_ref": BOT_REF},
        "bot-update": {"bot_uuid": BOT_UUID, "provider_bot_ref": BOT_REF,
                       "name": "Provider Review Agent (Senior)",
                       "domains": ["release", "audit"], "scopes": []},
        "attributes-read-denied": {},
        "attributes-write-denied": {},
        "org-create": {"organization_code": ORG_CODE},
        "org-list": {"organizations": [{"organization_code": ORG_CODE}]},
        "org-read": {"name": "E2E release organization"},
        "org-update": {"name": "E2E release organization updated"},
        "candidate-list-before": {"bots": [{"bot_uuid": BOT_UUID}]},
        "candidate-before": {"is_member": False, "bot_uuid": BOT_UUID},
        "member-add": {"role": "reviewer"},
        "candidate-list-after": {"bots": [{"bot_uuid": "other-bot"}]},
        "candidate-after": {"is_member": True},
        "member-profile": {"profile": {"name": "E2E organization bot"}},
        "member-read": {"bot_uuid": BOT_UUID},
        "member-list": {"members": [{"bot_uuid": BOT_UUID}]},
        "admin-start": {"data": {"run_id": ADMIN_RUN,
                                 "organization_code": ORG_CODE,
                                 "target_bot_uuid": BOT_UUID}},
        "admin-read": {"data": {"run_id": ADMIN_RUN, "status": "completed",
                                "target_bot_uuid": BOT_UUID}},
        "member-remove": {},
        "bot-list": {"items": [{"bot_uuid": BOT_UUID}]},
        "roster-denied": {"error": "not allowed to access the task-mode roster"},
        "agentpass-negative": {"agent_code": None, "provider_bot_binding": None},
        "unknown-run-callback": {"error": "run_not_found"},
        "takeover-denied": {"error": "not allowed to switch bot delivery"},
        "disable": {"disabled": True},
        "disabled-slug-read": {"enabled": False},
        "enable": {"disabled": False},
        "enabled-slug-read": {"enabled": True},
        "bot-retire": {"deleted": True, "provider_bot_ref": BOT_REF},
        "bot-list-empty": {"items": []},
    }
    stations = {station["id"]: station for station in density.provider_publishes_stations()}
    observations = {}
    for operation, body in bodies.items():
        observations[operation] = density_run.Observation(
            stations[operation]["expected_status"], body)
    assert len(observations) == 42, len(observations)
    return observations, dict(HINTS)


class TestDensityObligationBuild(unittest.TestCase):
    def test_roster_builds_98_unique_leaves_in_frozen_order(self):
        rows = density.build_provider_publishes_density_obligations()
        self.assertEqual(len(rows), 98)
        self.assertEqual([r.obligation_id for r in rows], list(contract.REQUIREMENTS))
        self.assertEqual(len({r.obligation_id for r in rows}), 98)
        self.assertTrue(all(density.LEAF_ID_PATTERN.fullmatch(r.obligation_id) for r in rows))

    def test_level_partition_32_parents_66_density(self):
        rows = density.build_provider_publishes_density_obligations()
        parents = [r for r in rows if r.level == "PARENT"]
        leaves = [r for r in rows if r.level == "DENSITY"]
        self.assertEqual((len(parents), len(leaves)), (32, 66))
        self.assertTrue(all(not r.density for r in parents))
        self.assertTrue(all(r.density for r in leaves))
        self.assertEqual(Counter(r.net_inflow for r in rows), {
            "existing-operation": 32,
            "new-field-receipt-existing-inline-semantics": 44,
            "new-slug-operation-or-field": 22})
        self.assertEqual(Counter(r.basis.split(":")[0] for r in rows), {
            "legacy-parent-envelope": 32,
            "inline-field-receipt": 44,
            "slug-operation-atom-status": 10,
            "slug-field-atom": 12})

    def test_legacy_envelopes_and_slug_atom_sets(self):
        rows = density.build_provider_publishes_density_obligations()
        legacy_ops = sorted({r.operation for r in rows if r.legacy_envelope})
        slug_ops = sorted({r.operation for r in rows if r.legacy_envelope is None})
        self.assertEqual(len(legacy_ops), 32)
        self.assertEqual(slug_ops, sorted(density.SLUG_OPERATION_IDS))
        self.assertTrue(all(r.legacy_envelope == "PROVIDER." + r.operation
                            for r in rows if r.legacy_envelope))
        slug_status = [r for r in rows if r.operation in density.SLUG_OPERATION_IDS
                       and r.kind == "status"]
        self.assertEqual(len(slug_status), 10)

    def test_parent_links_field_leaves_to_station_status(self):
        rows = density.build_provider_publishes_density_obligations()
        status_ids = {r.operation: r.obligation_id for r in rows if r.kind == "status"}
        for row in rows:
            if row.kind == "status":
                self.assertEqual(row.parent_requirement, PARENT_STORY)
            else:
                self.assertEqual(row.parent_requirement, status_ids[row.operation])
                self.assertTrue(row.parent_requirement.endswith(".status"))

    def test_method_path_status_join_against_frozen_stations(self):
        rows = density.build_provider_publishes_density_obligations()
        stations = {s["id"]: s for s in density.provider_publishes_stations()}
        self.assertEqual(len(stations), 42)
        for row in rows:
            station = stations[row.operation]
            self.assertEqual(row.method, station["method"])
            self.assertEqual(row.operation_path, station["path"])
            self.assertEqual(row.operation_expected_status, station["expected_status"])
            if row.kind == "status":
                self.assertEqual(row.expected, station["expected_status"])
        by_id = {r.obligation_id: r for r in rows}
        register_status = by_id["PROVIDER-PUBLISHES.register.status"]
        self.assertEqual((register_status.method, register_status.operation_path,
                          register_status.operation_expected_status),
                         ("POST", "/providers", 200))
        self.assertEqual(by_id["PROVIDER-PUBLISHES.member-remove.status"].method, "DELETE")
        self.assertEqual(by_id["PROVIDER-PUBLISHES.member-remove.status"].operation_expected_status, 204)
        self.assertEqual(by_id["PROVIDER-PUBLISHES.agentpass-negative.status"].operation_path,
                         "/providers/agentpass/resolve")

    def test_spans_inside_function_bounds_and_quotes_verbatim(self):
        rows = density.build_provider_publishes_density_obligations()
        lines = _story_lines()
        spans = {PARENT_STORY: _function_span(lines, PARENT_STORY),
                 HELPER_FN: _function_span(lines, HELPER_FN)}
        self.assertEqual(spans[PARENT_STORY], PARENT_SPAN)
        self.assertEqual(spans[HELPER_FN], HELPER_SPAN)
        self.assertEqual(min(r.source_line for r in rows), 1429)
        self.assertEqual(max(r.end_line for r in rows), 1723)
        for row in rows:
            inside = spans[row.function]
            self.assertLessEqual(inside[0], row.source_line)
            self.assertLessEqual(row.end_line, inside[1])
            resliced = "\n".join(s.strip() for s in
                                 lines[row.source_line - 1:row.end_line])
            self.assertEqual(resliced, row.source_quote)

    def test_kind_partition_and_comparator_vocabulary(self):
        rows = density.build_provider_publishes_density_obligations()
        self.assertEqual(Counter(r.kind for r in rows), {
            "status": 42, "eq": 42, "nonempty": 5, "membership": 5,
            "contains": 2, "keys": 1, "projection": 1})
        comparators = Counter(r.comparator for r in rows)
        self.assertEqual(comparators["http_status_equals"], 42)
        self.assertEqual(comparators["json_path_strict_equals"], 42)
        self.assertEqual(comparators["json_path_string_nonempty"], 5)
        self.assertEqual(comparators["list_membership_cardinality"], 5)
        self.assertEqual(comparators["response_json_contains_literal"], 2)
        self.assertEqual(comparators["exact_key_set"], 1)
        self.assertEqual(comparators["whole_body_strict_equals"], 1)
        by_id = {r.obligation_id: r for r in rows}
        keys_row = by_id["PROVIDER-PUBLISHES.slug-read.slug-discovery-exposes-only-basic-public-fields"]
        self.assertEqual(keys_row.comparator, "exact_key_set")
        self.assertEqual(len(keys_row.expected), 8)
        projection_row = by_id[
            "PROVIDER-PUBLISHES.collaboration-slug-read."
            "collaboration-slug-discovery-preserves-the-public-projection"]
        self.assertEqual(projection_row.comparator, "whole_body_strict_equals")
        membership_row = by_id[
            "PROVIDER-PUBLISHES.bot-list.published-provider-agent-appears-in-provider-list"]
        self.assertEqual(membership_row.comparator, "list_membership_cardinality")
        self.assertEqual(membership_row.membership_target, "$provider_bot_uuid")

    def test_admission_preconditions_excluded_and_uncounted(self):
        rows = density.build_provider_publishes_density_obligations()
        ids = {r.obligation_id for r in rows}
        for excluded in density.EXCLUDED_PRECONDITION_IDS:
            self.assertNotIn(excluded, ids)
        raw_lines = [line for line in _story_lines()[1421:1724]
                     if line.strip().startswith(("require_status", "assert"))]
        self.assertEqual(len(raw_lines), 100)

    def test_as_dict_carries_sha_freeze_and_span(self):
        row = density.build_provider_publishes_density_obligations()[0]
        item = row.as_dict()
        self.assertEqual(item["upstream_sha256"], density.STORIES_SHA256)
        self.assertEqual(item["source_file"], "apps/bcs/scripts/e2e-test/stories.sh")
        self.assertEqual((item["source_line"], item["end_line"]), (1429, 1429))
        self.assertEqual(item["function"], PARENT_STORY)


class TestReconciliationConservation(unittest.TestCase):
    def test_parsed_equals_roster_equals_frozen_contract(self):
        binding = contract.source_binding()
        rows = density.build_provider_publishes_density_obligations()
        self.assertEqual(binding["parsed_upstream_steps"], 98)
        self.assertEqual(binding["story_checks"], 98)
        self.assertEqual(len(binding["requirements"]), 98)
        self.assertEqual(len(rows), 98)
        self.assertEqual(generate(), binding)

    def test_residual_count_gap_zero_and_legacy_delta_disclosed(self):
        recon = density.provider_publishes_density_reconciliation()
        self.assertEqual(recon["residual_count_gap"], 0)
        self.assertEqual(recon["parsed_upstream_steps"], 98)
        self.assertEqual(recon["frozen_contract_obligations"], 98)
        self.assertEqual(recon["roster_leaves"], 98)
        self.assertEqual(recon["obligations_on_file_before_wave"], 32)
        self.assertEqual(recon["wave_delta_vs_btree"], 66)
        old = json.loads(OLD_CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(len(old["requirements"]), 32)
        self.assertEqual(recon["raw_assertion_clauses"], 100)
        self.assertEqual(recon["excluded_admission_preconditions"], 2)
        self.assertIn("poolab", recon["upstream_color_basis"])

    def test_plan_rows_match_roster_and_reconciliation(self):
        plan = density.provider_publishes_density_plan()
        rows = density.build_provider_publishes_density_obligations()
        self.assertEqual([item["obligation_id"] for item in plan["obligations"]],
                         [r.obligation_id for r in rows])
        self.assertEqual(plan["reconciliation"]["residual_count_gap"], 0)


class TestDensityDriftFailClose(unittest.TestCase):
    def test_stories_bytes_sha_drift_fail_close(self):
        raw = STORIES.read_text(encoding="utf-8")
        mutated = raw.replace("operator registers a Provider 2.0 integration",
                              "operator registers a Provider 2.0 integration!", 1)
        self.assertNotEqual(mutated, raw)
        with tempfile.TemporaryDirectory() as tmp:
            drift = Path(tmp) / "stories.sh"
            drift.write_text(mutated, encoding="utf-8")
            with mock.patch.object(density, "SOURCE_PATH", drift), \
                    self.assertRaises(RunStateError):
                density.build_provider_publishes_density_obligations()

    def test_frozen_sha_literal_drift_fail_close(self):
        with mock.patch.object(density, "STORIES_SHA256", "0" * 64), \
                self.assertRaises(RunStateError):
            density.build_provider_publishes_density_obligations()

    def test_contract_sources_map_drift_fail_close(self):
        binding = json.loads(json.dumps(contract.source_binding()))
        binding["sources"]["stories.sh"] = "0" * 64
        with mock.patch.object(density, "source_binding", return_value=binding), \
                self.assertRaises(RunStateError):
            density.build_provider_publishes_density_obligations()

    def test_leaf_end_line_quote_reslice_fail_close(self):
        binding = json.loads(json.dumps(contract.source_binding()))
        binding["requirements"][0]["end_line"] += 1
        with mock.patch.object(density, "source_binding", return_value=binding), \
                self.assertRaises(RunStateError):
            density.build_provider_publishes_density_obligations()

    def test_duplicate_leaf_id_fail_close(self):
        binding = json.loads(json.dumps(contract.source_binding()))
        binding["requirements"][5]["id"] = binding["requirements"][4]["id"]
        with mock.patch.object(density, "source_binding", return_value=binding), \
                self.assertRaises(RunStateError):
            density.build_provider_publishes_density_obligations()

    def test_operations_table_drift_fail_close(self):
        binding = json.loads(json.dumps(contract.source_binding()))
        binding["operations"] = binding["operations"][:-1]
        with mock.patch.object(density, "source_binding", return_value=binding), \
                self.assertRaises(RunStateError):
            density.build_provider_publishes_density_obligations()

    def test_status_expectation_join_drift_fail_close(self):
        binding = json.loads(json.dumps(contract.source_binding()))
        binding["operations"][0]["expected_status"] = 500
        with mock.patch.object(density, "source_binding", return_value=binding), \
                self.assertRaises(RunStateError):
            density.build_provider_publishes_density_obligations()

    def test_net_inflow_partition_drift_fail_close(self):
        binding = json.loads(json.dumps(contract.source_binding()))
        binding["requirements"][1]["net_inflow"] = "existing-operation"
        with mock.patch.object(density, "source_binding", return_value=binding), \
                self.assertRaises(RunStateError):
            density.build_provider_publishes_density_obligations()

    def test_parent_span_constant_drift_fail_close(self):
        with mock.patch.object(density, "PARENT_SPAN", (1, 2)), \
                self.assertRaises(RunStateError):
            density.build_provider_publishes_density_obligations()

    def test_missing_source_file_fail_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(density, "SOURCE_PATH",
                                   Path(tmp) / "absent-stories.sh"), \
                    self.assertRaises(RunStateError):
                density.build_provider_publishes_density_obligations()


class TestOfflineReplayPassProjection(unittest.TestCase):
    def test_default_mirror_projects_full_pass(self):
        receipt = density_run.project_provider_publishes_density(
            profile=_local_profile())
        self.assertEqual(receipt["status"], "PASS")
        self.assertIsNone(receipt["reason_class"])
        self.assertEqual(len(receipt["assertions"]), 98)
        self.assertTrue(all(a["status"] == "PASS" for a in receipt["assertions"]))
        self.assertEqual(receipt["obligation_summary"], {
            "baseline": 42, "total": 98, "field_obligations": 56,
            "passed": 98, "failed": 0, "not_run": 0})
        execution = receipt["execution"]
        self.assertEqual(execution["status_leaves_truly_evaluated"], 42)
        self.assertEqual(execution["field_rows_recorded_replay"], 56)
        self.assertEqual(execution["field_rows_live_evaluated"], 0)
        self.assertEqual(execution["stations_with_observations"], 42)
        events = Counter(a["evidence_event"] for a in receipt["assertions"])
        self.assertEqual(events["offline_status_recheck"], 42)
        self.assertEqual(events["recorded_verdict_replay"], 56)

    def test_default_mirror_receipt_identity_and_lineage(self):
        receipt = density_run.project_provider_publishes_density(
            profile=_local_profile())
        self.assertEqual(receipt["contract"], REPORT_CONTRACT)
        self.assertEqual(receipt["suite"], "dual-provider-publishes-v1")
        self.assertEqual(receipt["case_id"], "DUAL-PROVIDER-PUBLISHES-01")
        self.assertEqual(receipt["parent_id"], PARENT_STORY)
        self.assertEqual(receipt["requirement_ids"], list(contract.REQUIREMENTS))
        self.assertEqual(receipt["bcs_binary_sha256"], contract.SERVER_SHA256)
        self.assertEqual(receipt["cli_binary_sha256"], contract.CLI_SHA256)
        self.assertEqual(receipt["author_cli_pair_status"],
                         "ADMITTED_FROZEN_NOT_EXECUTED_OFFLINE")
        self.assertEqual(receipt["remote_validation"], "NOT_RUN")
        self.assertFalse(receipt["grants_release_credit"])
        self.assertEqual(receipt["full_release_status"], "NOT_QUALIFIED")
        self.assertEqual(receipt["resource_disposition"],
                         "OFFLINE_IN_MEMORY_ONLY_NO_RESOURCES")
        self.assertEqual(receipt["environment"]["network_requests"], 0)
        self.assertEqual(receipt["environment"]["credential_refs"],
                         ["BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"])
        self.assertFalse(receipt["environment"]["credential_values_stored"])
        self.assertIn("in-memory observations only",
                      receipt["density_layer"]["transport_admission"])
        self.assertEqual(receipt["density_layer"]["wave_token"],
                         density_run.WAVE_TOKEN)
        self.assertEqual(receipt["density_layer"]["roster_contract"],
                         "provider-publishes-density-v1")
        replay = receipt["replay"]
        self.assertEqual(replay["recorded_local_run_id"],
                         "20261031-w3a-provider-publishes")
        self.assertEqual(replay["recorded_local_leaf"], "attempt-3")
        self.assertEqual(replay["injected"], False)

    def test_first_and_last_rows_anchored(self):
        receipt = density_run.project_provider_publishes_density(
            profile=_local_profile())
        first = receipt["assertions"][0]
        self.assertEqual(first["id"], "PROVIDER-PUBLISHES.register.status")
        self.assertEqual((first["source_line"], first["predicate"]), (1429, "status"))
        self.assertEqual(first["level"], "PARENT")
        last = receipt["assertions"][-1]
        self.assertEqual(last["id"],
                         "PROVIDER-PUBLISHES.bot-list-empty."
                         "provider-agent-list-is-empty-after-retirement")
        self.assertEqual(last["source_line"], 1606)

    def test_injected_observations_true_evaluate_all_98(self):
        observations, hints = _true_eval_observations()
        receipt = density_run.project_provider_publishes_density(
            profile=_local_profile(), observations=observations,
            run_context=hints)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["obligation_summary"]["passed"], 98)
        self.assertEqual(receipt["execution"]["field_rows_live_evaluated"], 56)
        self.assertEqual(receipt["execution"]["field_rows_recorded_replay"], 0)
        self.assertEqual(receipt["execution"]["status_leaves_truly_evaluated"], 42)
        events = Counter(a["evidence_event"] for a in receipt["assertions"])
        self.assertEqual(events["offline_status_recheck"], 42)
        self.assertEqual(events["injected_observation_live_eval"], 56)
        self.assertEqual(receipt["replay"]["run_context_hints"], sorted(HINTS))

    def test_comparator_machinery_bites_on_injected_mismatch(self):
        observations, hints = _true_eval_observations()
        observations["slug-read"] = density_run.Observation(200, {
            "auth_mode": "static_bearer", "created_at": "2026-10-31",
            "enabled": False, "name": "E2E Provider", "protocol_version": "2.0",
            "provider_id": PROV_ID, "slug": SLUG, "updated_at": "2026-10-31"})
        receipt = density_run.project_provider_publishes_density(
            profile=_local_profile(), observations=observations,
            run_context=hints)
        rows = receipt["assertions"]
        self.assertEqual(rows[2]["id"], "PROVIDER-PUBLISHES.slug-read.status")
        self.assertEqual(rows[2]["status"], "PASS")
        mismatch_row = rows[8]
        self.assertEqual(mismatch_row["id"],
                         "PROVIDER-PUBLISHES.slug-read.slug-discovery-starts-enabled")
        self.assertEqual(mismatch_row["status"], "FAIL")
        self.assertEqual(mismatch_row["observed"], False)
        self.assertEqual(mismatch_row["comparator"], "json_path_strict_equals")
        self.assertEqual(rows[9]["id"],
                         "PROVIDER-PUBLISHES.slug-read."
                         "slug-discovery-exposes-only-basic-public-fields")
        self.assertEqual(rows[9]["status"], "NOT_RUN")
        self.assertEqual(rows[9]["error_type"], "BLOCKED_AFTER_ASSERTION_FAILURE")
        self.assertTrue(all(a["status"] == "NOT_RUN" and
                            a["error_type"] == "BLOCKED_AFTER_ASSERTION_FAILURE"
                            for a in rows[9:]))
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["obligation_summary"], {
            "baseline": 42, "total": 98, "field_obligations": 56,
            "passed": 8, "failed": 1, "not_run": 89})
        self.assertEqual(receipt["reason_class"],
                         "PROVIDER_PUBLISHES_DENSITY_ASSERTION_FAILED")


class TestPreprodNotRunFailClose(unittest.TestCase):
    def test_preprod_projects_full_not_run_with_named_gaps(self):
        receipt = density_run.project_provider_publishes_density(
            profile=_preprod_profile())
        self.assertEqual(receipt["status"], "NOT_RUN")
        self.assertEqual(receipt["reason_class"],
                         "PROVIDER_PUBLISHES_DENSITY_PREPROD_TRANSPORT_GAP")
        self.assertEqual(len(receipt["assertions"]), 98)
        self.assertTrue(all(a["status"] == "NOT_RUN" for a in receipt["assertions"]))
        self.assertTrue(all(a["error_type"] == "PREPROD_EXECUTION_NOT_RUN"
                            for a in receipt["assertions"]))
        self.assertEqual(receipt["obligation_summary"], {
            "baseline": 42, "total": 98, "field_obligations": 56,
            "passed": 0, "failed": 0, "not_run": 98})
        self.assertEqual(receipt["remote_validation"], "NOT_RUN")
        self.assertFalse(receipt["grants_release_credit"])
        self.assertEqual(receipt["full_release_status"], "NOT_QUALIFIED")
        self.assertEqual(receipt["resource_disposition"], "NO_REMOTE_RESOURCES_CREATED")
        self.assertEqual(receipt["environment"]["network_requests"], 0)
        gaps = receipt["named_preprod_gaps"]
        self.assertIn("PREPROD_PROVIDER_PUBLISHES_CREDENTIALS_UNAVAILABLE", gaps)
        self.assertIn("USER_NOT_LOGIN", gaps[
            "PREPROD_PROVIDER_PUBLISHES_CREDENTIALS_UNAVAILABLE"]["reason"])
        self.assertEqual(receipt["preprod_blocker"]["recorded_r8_verdict"], {
            "credential_resolution": "NOT_ATTEMPTED", "network_requests": 0,
            "observed_status": None,
            "reason": ("Operator reports canonical 2026-10-29 cookie expired/"
                       "USER_NOT_LOGIN. No re-probe or credential refresh "
                       "authorized; preprod remains blocked."),
            "reason_class": "PREPROD_PROVIDER_PUBLISHES_CREDENTIALS_UNAVAILABLE"})
        for seam in NAMED_GAPS:
            self.assertIn(seam, gaps)
        self.assertIn("PREPROD_PROVIDER_PUBLISHES_P4_MATERIAL_GATE", gaps)
        self.assertIn("3c6d6f374", gaps["PREPROD_PROVIDER_PUBLISHES_P4_MATERIAL_GATE"]["nature"])
        self.assertEqual(receipt["preprod_admission"]["network_requests"], 0)
        self.assertTrue(receipt["preprod_admission"]["read_only"])

    def test_preprod_rejects_observation_injection(self):
        with self.assertRaises(RunStateError):
            density_run.project_provider_publishes_density(
                profile=_preprod_profile(),
                observations=density_run.owned_local_replay_observations())

    def test_profile_admission_fail_close(self):
        with self.assertRaises(RunStateError):
            density_run.project_provider_publishes_density(
                profile={"schema_version": 1,
                         "contract": "wrong-contract",
                         "run_id": "density-x",
                         "environment": {"tier": "local", "id": "s04-x"},
                         "entrypoint": {"kind": "offline_in_memory_replay"}})
        with self.assertRaises(ConfigError):
            density_run.project_provider_publishes_density(
                profile=_local_profile() | {"run_id": "density x"})
        with self.assertRaises(ConfigError):
            density_run.project_provider_publishes_density(
                profile=_local_profile() | {"run_id": ""})
        bad_entry = _local_profile()
        bad_entry["entrypoint"] = {"kind": "blocked-no-network"}
        with self.assertRaises(RunStateError):
            density_run.project_provider_publishes_density(profile=bad_entry)
        bad_tier = _local_profile()
        bad_tier["environment"] = {"tier": "prod", "id": "s04-x"}
        with self.assertRaises(RunStateError):
            density_run.project_provider_publishes_density(profile=bad_tier)
        with self.assertRaises(ConfigError):
            density_run.project_provider_publishes_density(
                profile=_local_profile() | {"run_id": ""})


class TestReplayDisciplines(unittest.TestCase):
    def test_missing_station_projects_honest_not_run(self):
        observations, hints = _true_eval_observations()
        del observations["update"]
        receipt = density_run.project_provider_publishes_density(
            profile=_local_profile(), observations=observations, run_context=hints)
        update_rows = [a for a in receipt["assertions"]
                       if a["id"].startswith("PROVIDER-PUBLISHES.update.")]
        self.assertEqual(len(update_rows), 3)
        self.assertTrue(all(a["status"] == "NOT_RUN" for a in update_rows))
        self.assertTrue(all(a["error_type"] == "DENSITY_OBSERVATION_ABSENT"
                            for a in update_rows))
        self.assertTrue(all(a["evidence_event"] == "station_observation_absent"
                            for a in update_rows))
        self.assertEqual(receipt["obligation_summary"], {
            "baseline": 42, "total": 98, "field_obligations": 56,
            "passed": 95, "failed": 0, "not_run": 3})
        self.assertEqual(receipt["status"], "NOT_RUN")
        self.assertEqual(receipt["reason_class"],
                         "PROVIDER_PUBLISHES_DENSITY_OBSERVATIONS_INCOMPLETE")
        self.assertEqual(receipt["execution"]["stations_with_observations"], 41)

    def test_fail_cascades_later_rows_not_run(self):
        observations, hints = _true_eval_observations()
        observations["slug-read"] = density_run.Observation(404, None)
        receipt = density_run.project_provider_publishes_density(
            profile=_local_profile(), observations=observations, run_context=hints)
        rows = receipt["assertions"]
        self.assertEqual(rows[0]["status"], "PASS")
        self.assertEqual(rows[1]["status"], "PASS")
        self.assertEqual(rows[2]["status"], "FAIL")
        self.assertEqual(rows[2]["observed"], 404)
        self.assertTrue(all(a["status"] == "NOT_RUN" and
                            a["error_type"] == "BLOCKED_AFTER_ASSERTION_FAILURE"
                            for a in rows[3:]))
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["obligation_summary"]["passed"], 2)
        self.assertEqual(receipt["obligation_summary"]["failed"], 1)
        self.assertEqual(receipt["obligation_summary"]["not_run"], 95)

    def test_injected_station_without_body_fails_closed(self):
        observations, hints = _true_eval_observations()
        observations["disable"] = density_run.Observation(200, None)
        receipt = density_run.project_provider_publishes_density(
            profile=_local_profile(), observations=observations,
            run_context=hints)
        rows = receipt["assertions"]
        disable_rows = [a for a in rows
                        if a["id"].startswith("PROVIDER-PUBLISHES.disable.")]
        self.assertEqual(disable_rows[0]["status"], "PASS")
        self.assertEqual(disable_rows[1]["status"], "NOT_RUN")
        self.assertEqual(disable_rows[1]["error_type"],
                         "DENSITY_OBSERVATION_BODY_ABSENT")

    def test_unresolved_run_context_hint_fail_close(self):
        observations, _hints = _true_eval_observations()
        receipt = density_run.project_provider_publishes_density(
            profile=_local_profile(), observations=observations, run_context={})
        unresolved = [a for a in receipt["assertions"]
                      if a.get("error_type") == "DENSITY_RUN_CONTEXT_UNRESOLVED"]
        unresolved_ids = {a["id"] for a in unresolved}
        # frozen roster carries 16 eq $-refs + 5 membership targets + 1 projection
        self.assertEqual(len(unresolved_ids), 22)
        self.assertEqual(Counter(a["predicate"] for a in unresolved),
                         {"eq": 16, "membership": 5, "projection": 1})
        self.assertTrue(all(a["status"] == "NOT_RUN" for a in unresolved))
        self.assertTrue(all(a["evidence_event"] == "run_context_hints_unresolved"
                            for a in unresolved))
        resolved = [a for a in receipt["assertions"]
                    if a["id"] not in unresolved_ids]
        self.assertTrue(resolved)
        self.assertTrue(all(a["status"] == "PASS" for a in resolved))
        # literal expectations, status leaves and nonempty/contains/keys rows
        # never depend on run-context hints and stay PASS with empty hints
        literal_ids = {
            "PROVIDER-PUBLISHES.slug-read.slug-discovery-returns-provider-name",
            "PROVIDER-PUBLISHES.slug-read.slug-discovery-returns-auth-mode",
            "PROVIDER-PUBLISHES.read.provider-metadata-keeps-protocol-auth-mode",
            "PROVIDER-PUBLISHES.roster-denied."
            "task-mode-roster-guardrail-names-provider-allow-list",
            "PROVIDER-PUBLISHES.agentpass-negative."
            "non-agentpass-credential-resolves-no-agent-code",
            "PROVIDER-PUBLISHES.register.status",
            "PROVIDER-PUBLISHES.slug-read.status",
        }
        by_id = {a["id"]: a for a in receipt["assertions"]}
        self.assertTrue(all(by_id[leaf]["status"] == "PASS" for leaf in literal_ids))
        self.assertEqual(receipt["obligation_summary"], {
            "baseline": 42, "total": 98, "field_obligations": 56,
            "passed": 76, "failed": 0, "not_run": 22})
        self.assertEqual(receipt["status"], "NOT_RUN")
        self.assertEqual(receipt["reason_class"],
                         "PROVIDER_PUBLISHES_DENSITY_OBSERVATIONS_INCOMPLETE")

    def test_predicate_exception_fail_close_and_cascade(self):
        observations, hints = _true_eval_observations()
        with mock.patch.object(density_run, "evaluate_frozen",
                               side_effect=RuntimeError("probe")):
            receipt = density_run.project_provider_publishes_density(
                profile=_local_profile(), observations=observations,
                run_context=hints)
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["assertions"][0]["status"], "FAIL")
        self.assertEqual(receipt["assertions"][0]["error_type"], "RuntimeError")
        self.assertEqual(receipt["assertions"][1]["status"], "NOT_RUN")
        self.assertEqual(receipt["assertions"][1]["error_type"],
                         "BLOCKED_AFTER_ASSERTION_FAILURE")


class TestEnvelopeCompatibility(unittest.TestCase):
    DUAL_ENVELOPE_KEYS = (
        "schema_version", "contract", "suite", "case_id", "parent_id", "run_id",
        "bcs_binary_sha256", "cli_binary_sha256", "profile_digest",
        "source_binding_digest", "executor_digest", "requirement_ids",
        "author_cli_pair_status", "environment", "status", "reason_class",
        "assertions", "obligation_summary", "remote_validation",
        "remote_entry_status", "grants_release_credit", "full_release_status",
        "resource_disposition")

    def test_dual_envelope_keys_subset_of_density_receipt(self):
        receipt = density_run.project_provider_publishes_density(
            profile=_local_profile())
        for key in self.DUAL_ENVELOPE_KEYS:
            self.assertIn(key, receipt)
        self.assertEqual(receipt["contract"], REPORT_CONTRACT)
        self.assertEqual(receipt["requirement_ids"], list(contract.REQUIREMENTS))

    def test_obligation_summary_keys_equal_dual_envelope(self):
        receipt = density_run.project_provider_publishes_density(
            profile=_local_profile())
        self.assertEqual(set(receipt["obligation_summary"]),
                         {"baseline", "total", "field_obligations", "passed",
                          "failed", "not_run"})

    def test_assertion_rows_superset_of_dual_rows(self):
        receipt = density_run.project_provider_publishes_density(
            profile=_local_profile())
        row = receipt["assertions"][0]
        for key in ("id", "status", "source_line", "predicate"):
            self.assertIn(key, row)
        for key in ("level", "density", "comparator", "evidence_event", "basis"):
            self.assertIn(key, row)

    def test_envelope_command_reuse_declared(self):
        self.assertEqual(COMMAND, "dual-provider-publishes-run")
        receipt = density_run.project_provider_publishes_density(
            profile=_local_profile())
        self.assertEqual(receipt["source_freeze"]["envelope_command"], COMMAND)
        self.assertIn("strict key superset",
                      receipt["density_layer"]["envelope_reuse"])


class TestRegistrationProposal(unittest.TestCase):
    def test_proposal_is_describe_only_and_anchored(self):
        self.assertEqual(PROPOSAL_TOKEN,
                         "PROVIDER_PUBLISHES_DENSITY_98_OBLIGATIONS_20261031")
        proposal = registration_proposal()
        self.assertEqual(proposal["proposal_status"], "DESCRIBE_ONLY")
        self.assertFalse(proposal["applied"])
        self.assertFalse(proposal["new_registration_command"])
        self.assertEqual(proposal["shared_face_files_touched"], [])
        self.assertEqual(proposal["wave"]["roster_leaves"], 98)
        self.assertEqual(proposal["wave"]["roster_delta_vs_btree"], 66)
        self.assertEqual(proposal["wave"]["residual_count_gap"], 0)

    def test_executor_row_append_spec_discloses_lineage(self):
        proposal = registration_proposal()
        spec = proposal["executor_migration"]
        self.assertEqual(spec["row_index"], 7)
        self.assertEqual(spec["row_id"], PARENT_STORY)
        self.assertEqual(spec["notes_before"], [])
        self.assertEqual(spec["notes_append_after"], [PROPOSAL_TOKEN])
        note = spec["proposed_row_note"]
        for needle in ("residual_count_gap=0", "3c6d6f374", "02297df18",
                       "PREPROD_PROVIDER_PUBLISHES_CREDENTIALS_UNAVAILABLE",
                       "NO_REMOTE_RESOURCES_CREATED", "USER_NOT_LOGIN",
                       "remote_validation=NOT_RUN", "grants_release_credit=false",
                       "dual-provider-publishes-run"):
            self.assertIn(needle, note)

    def test_inventory_spec_is_three_edits(self):
        proposal = registration_proposal()
        inventory = proposal["inventory"]
        self.assertEqual(inventory["declaration"], "EXECUTOR_MIGRATION_NOTES")
        self.assertEqual(inventory["note_counts_declaration"],
                         "_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS")
        self.assertEqual(len(inventory["edits"]), 3)
        self.assertEqual(inventory["proposed_count"], 1)

    def test_coverage_map_increment_zero_residual(self):
        proposal = registration_proposal()
        face = proposal["coverage_map"]
        self.assertEqual(face["row"], 4)
        self.assertEqual(face["parsed_minus_obligations_on_file"], 0)
        self.assertEqual(face["residual_count_gap_after_wave"], 0)
        self.assertEqual(face["execution_gap_on_file"], 98)
        self.assertIn("密度波 E1（20261031）闭合", face["proposed_row_note"])
        self.assertIn("NOT_RUN", face["proposed_row_note"])

    def test_named_preprod_gaps_registered(self):
        proposal = registration_proposal()
        gaps = proposal["named_preprod_gaps"]
        self.assertEqual(gaps["r8_recorded_live_blocker"]["reason_class"],
                         "PREPROD_PROVIDER_PUBLISHES_CREDENTIALS_UNAVAILABLE")
        self.assertIn("USER_NOT_LOGIN",
                      gaps["r8_recorded_live_blocker"]["operator_statement_verbatim"])
        self.assertEqual(gaps["r8_recorded_live_blocker"]["network_requests"], 0)
        self.assertEqual(len(gaps["frozen_seam_gaps"]), len(NAMED_GAPS))
        self.assertIn("人类 cookie 刷新（用户授权物料）",
                      gaps["p4_material_gate"]["materials"])

    def test_anchors_verify_current_tree(self):
        assert_anchors()
        for relative, needles in ANCHORS.items():
            path = REPO_ROOT / relative
            self.assertTrue(path.is_file(), relative)
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                self.assertIn(needle, text)
        executor = json.loads(EXECUTOR_JSON.read_text(encoding="utf-8"))
        row = executor["rows"][7]
        self.assertEqual(row["id"], PARENT_STORY)
        self.assertEqual(row["executor_migration"]["mode"], "DUAL")
        self.assertEqual(row["executor_migration"]["preprod_verdict"], "PASS")
        self.assertEqual(row["executor_migration"]["notes"], [])

    def test_describe_only_fragment_and_drop_dead_checks(self):
        fragment = describe_only_mainline_fragment()
        self.assertTrue(fragment.startswith("DESCRIBE_ONLY token="))
        self.assertIn(PROPOSAL_TOKEN, fragment)
        proposal = registration_proposal()
        checks = proposal["drop_dead_checks"]
        self.assertEqual(checks["leaves_built"], 98)
        self.assertTrue(checks["requirement_ids_equal_frozen_order"])
        self.assertTrue(checks["anchors_verified_read_only"])
        self.assertEqual(proposal["envelope"]["command"], COMMAND)
        self.assertFalse(proposal["envelope"]["cli_required"])
        self.assertEqual(len(proposal["red_lines"]), 6)
        self.assertTrue(proposal["non_goals"])
        self.assertTrue(proposal["user_actions"])


class TestGuardCleanliness(unittest.TestCase):
    GUARD_PATH = REPO_ROOT / "scripts" / "ci" / "e2e_runner_case_guard.py"
    WAVE_SOURCES = (density, density_run, registration_proposal_module)

    def _load_guard(self):
        spec = importlib.util.spec_from_file_location(
            "e2e_runner_case_guard_readonly", self.GUARD_PATH)
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        return guard

    def test_wave_files_pass_the_committed_case_guard(self):
        guard = self._load_guard()
        targets = [Path(module.__file__) for module in self.WAVE_SOURCES]
        targets.append(Path(__file__))
        for path in targets:
            with self.subTest(file=path.name):
                self.assertEqual(guard.inspect(path), [])

    def test_wave_modules_are_credential_literal_clean(self):
        for module in self.WAVE_SOURCES:
            source = Path(module.__file__).read_text(encoding="utf-8")
            for pattern in (r"Bearer\s", r"Cookie:\s", r"-----BEGIN"):
                self.assertIsNone(re.search(pattern, source),
                                  (module.__name__, pattern))


if __name__ == "__main__":
    unittest.main()
