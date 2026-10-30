"""Focused offline tests for the Direct-conversation density backfill.

Wave-D1 closes the clause layer of ``story_user_has_direct_agent_conversation``
(coverage-map row 13): 14 upstream parsed clauses (wave3 clause map S13,
stories.sh L776-805, L789 flow guard counted) against five frozen DM parent
obligations.  These tests pin:

* roster integrity — 14 unique source-anchored rows, verbatim clause quotes,
  the guard counted as clause five, parent projection onto the four main DM
  requirements, and fail-closed anchor/drift behavior;
* profile admission — both tiers, structural gates, env-NAME-only auth
  references, exact four-operation binding;
* honesty — no injected transport means BLOCKED/NOT_RUN on both tiers, defect
  gate disclosure on C09-C14, zero DM credit, remote_validation NOT_RUN, no
  fabricated idempotency clause;
* evaluation truth — context-bound run/session/target clauses, failure
  cascades, transport failures as FAIL evidence;
* describe-only integration — envelope reuse, exact anchors, purity of every
  red-lined shared face;
* package discipline — line budget, import boundary, no network/env/secret
  constructs, flat module budget intact.

All fixtures are synthetic and local; nothing reads ~/.codex, opens a
transport, or launches a process.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import re
from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bcs_e2e.direct_conversation_density import (  # noqa: E402
    DENSITY_ASSERTION_COUNT,
    DENSITY_OPERATIONS,
    FAMILY_OBLIGATION_TOTAL,
    PARENT,
    PARENT_OBLIGATION_COUNT,
    PARSED_CLAUSE_COUNT,
)
from bcs_e2e.direct_conversation_density import contract as density_contract  # noqa: E402
from bcs_e2e.direct_conversation_density import defect as density_defect  # noqa: E402

from bcs_e2e.direct_conversation_density.contract import (  # noqa: E402
    STORY_SPAN,
    ParsedDensityAssertion,
    direct_conversation_density_plan,
    parsed_density_assertions,
)
from bcs_e2e.direct_conversation_density.defect import (  # noqa: E402
    DEFECT_GATED_ASSERTION_IDS,
    DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID,
    assert_defect_gap_alignment,
)
from bcs_e2e.direct_conversation_density.profile import (  # noqa: E402
    DENSITY_PROFILE_CONTRACT,
    validate_density_profile,
)
from bcs_e2e.direct_conversation_density.registration_proposal import (  # noqa: E402
    registration_proposal,
)

density_registration = importlib.import_module(
    "bcs_e2e.direct_conversation_density.registration_proposal")
from bcs_e2e.direct_conversation_density.reporting import (  # noqa: E402
    density_accounting,
    family_accounting,
    md_table,
)
from bcs_e2e.direct_conversation_density.run import (  # noqa: E402
    blocked_direct_density_receipt,
    run_direct_conversation_density,
)
from bcs_e2e.direct_conversation_density.transport import (  # noqa: E402
    Observation,
    ProjectionReplayTransport,
    ReplayTransport,
    canonical_defect_faithful_observations,
)
from bcs_e2e.local_direct_chat_contract import REQUIREMENTS  # noqa: E402
from bcs_e2e.run_state import RunStateError  # noqa: E402

STORIES_SHA_PATH = ROOT.parent / "e2e-test" / "stories.sh"
DENSITY_PACKAGE = ROOT / "bcs_e2e" / "direct_conversation_density"

CLAUSE_LINES = (782, 786, 787, 788, 789, 792, 793, 794, 797, 798, 799, 802, 803, 804)
CLAUSE_QUOTES = (
    'require_status "agent starts an asynchronous direct conversation" "202" || return',
    'assert_not_empty "async conversation returns a run id" "$run_id"',
    'assert_not_empty "async conversation returns a session id" "$session_id"',
    'assert_json_eq "async conversation targets product manager" "$RESPONSE" "bot_uuid" "$BOT_PM_UUID"',
    '[[ -n "$run_id" ]] || return',
    'require_status "caller reads its direct chat run" "200" || return',
    'assert_json_eq "chat run read-back keeps run id" "$RESPONSE" "run_id" "$run_id"',
    'assert_json_not_empty "chat run exposes current state" "$RESPONSE" "state"',
    'require_status "caller cancels the direct chat run" "200" || return',
    'assert_json_eq "chat run cancellation is acknowledged" "$RESPONSE" "cancelled" "true"',
    'assert_json_eq "cancelled chat run has cancelled state" "$RESPONSE" "state" "cancelled"',
    'require_status "caller reads the cancelled chat run" "200" || return',
    'assert_json_eq "cancelled run stays cancelled" "$RESPONSE" "state" "cancelled"',
    'assert_json_eq "cancelled run is terminal" "$RESPONSE" "is_terminal" "true"',
)


def local_profile() -> dict:
    return {
        "schema_version": 1,
        "contract": DENSITY_PROFILE_CONTRACT,
        "environment": {
            "tier": "local", "id": "direct-density-local", "expected_bcs_env": "local",
            "lifecycle": "local-provision", "deployment": "singlebox", "provider": "local",
        },
        "entrypoint": {
            "kind": "singlebox", "origin": "http://127.0.0.1:18789",
            "api_face": "legacy", "manifest_face": "legacy",
        },
        "auth": {"kind": "owned-local-bot-tokens", "driver_token_env": "BCS_E2E_DRIVER_TOKEN"},
        "journey": {"member_bot_id": "bot-pm-direct-density-1"},
        "density_operation_bindings": {
            "direct_chat.main.submit": {"method": "POST", "path": "/bots/{member_actor_id}/chat-async"},
            "direct_chat.main.read": {"method": "GET", "path": "/chat/runs/{run_id}"},
            "direct_chat.main.cancel": {"method": "POST", "path": "/chat/runs/{run_id}/cancel"},
            "direct_chat.main.terminal": {"method": "GET", "path": "/chat/runs/{run_id}"},
        },
    }


def preprod_profile() -> dict:
    profile = local_profile()
    profile["environment"] = {
        "tier": "preprod", "id": "direct-density-preprod", "expected_bcs_env": "pre",
        "lifecycle": "remote-attach", "deployment": "managed", "provider": "internal",
    }
    profile["entrypoint"] = {
        "kind": "gateway", "origin": "https://bcs-pre.alipay.com",
        "api_face": "legacy", "manifest_face": "legacy",
    }
    profile["auth"] = {"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
    profile["journey"] = {"member_bot_id": "bot-pm-direct-density-1:2088901234567890"}
    profile["browser_origin"] = "https://bcs-pre.alipay.com"
    return profile


def canonical_receipt() -> dict:
    return run_direct_conversation_density(
        profile=local_profile(),
        transport=ReplayTransport(canonical_defect_faithful_observations()),
    )


def mutated_observations(target: str, mutation) -> dict:
    observations = canonical_defect_faithful_observations()
    base = observations[target]
    body = dict(base.body) if isinstance(base.body, dict) else base.body
    mutation(body)
    observations[target] = Observation(base.status, body)
    return observations


class TestRosterContract(unittest.TestCase):
    def test_parsed_clause_count_is_fourteen_unique_anchored(self):
        rows = parsed_density_assertions()
        self.assertEqual(len(rows), 14)
        ids = [row.assertion_id for row in rows]
        self.assertEqual(len(set(ids)), 14)
        self.assertEqual(ids, [f"DIRECT_DENSITY.C{index:02d}" for index in range(1, 15)])
        for row in rows:
            self.assertTrue(row.density)
            self.assertTrue(row.located)
            self.assertTrue(STORY_SPAN[0] <= row.source_line <= STORY_SPAN[1])

    def test_clauses_match_wave3_s13_roster_verbatim(self):
        rows = parsed_density_assertions()
        stories_lines = STORIES_SHA_PATH.read_text(encoding="utf-8").splitlines()
        for row, line, quote in zip(rows, CLAUSE_LINES, CLAUSE_QUOTES):
            self.assertEqual(row.source_line, line, row.assertion_id)
            self.assertEqual(row.source_quote, quote, row.assertion_id)
            self.assertIn(quote, stories_lines[line - 1], row.assertion_id)

    def test_guard_is_parsed_clause_five(self):
        rows = parsed_density_assertions()
        guard = rows[4]
        self.assertEqual(guard.assertion_id, "DIRECT_DENSITY.C05")
        self.assertEqual(guard.kind, "guard")
        self.assertEqual(guard.source_line, 789)
        self.assertEqual(guard.field, "run_id")
        self.assertEqual(guard.expected, "NON_EMPTY")

    def test_parent_projection_covers_four_main_dm_requirements(self):
        plan = direct_conversation_density_plan()
        mapping = plan["requirement_clause_map"]
        self.assertEqual(set(mapping), {"DM.main-submit", "DM.main-read", "DM.main-cancel", "DM.main-terminal"})
        self.assertNotIn("DM.ping-submit", mapping)
        self.assertEqual(mapping["DM.main-submit"], ["DIRECT_DENSITY.C01", "DIRECT_DENSITY.C02",
                                                       "DIRECT_DENSITY.C03", "DIRECT_DENSITY.C04",
                                                       "DIRECT_DENSITY.C05"])
        self.assertEqual(mapping["DM.main-terminal"], ["DIRECT_DENSITY.C12", "DIRECT_DENSITY.C13",
                                                       "DIRECT_DENSITY.C14"])
        self.assertIn("fixture 202 check", plan["pairing"]["ping_upstream_site_note"])

    def test_plan_accounting_deltas(self):
        plan = direct_conversation_density_plan()
        accounting = plan["accounting"]
        self.assertEqual(accounting["parent_obligation_count"], 5)
        self.assertEqual(accounting["parsed_clause_count"], 14)
        self.assertEqual(accounting["parsed_minus_parent_obligations"], 9)
        self.assertEqual(accounting["density_assertion_count"], 14)
        self.assertEqual(accounting["family_obligation_total"], 19)
        self.assertEqual(accounting["parsed_clause_coverage"], "14/14")
        self.assertEqual(accounting["residual_count_gap"], 0)

    def test_plan_fail_closes_on_upstream_drift(self):
        drifted = STORIES_SHA_PATH.read_text(encoding="utf-8").replace(
            'assert_json_eq "cancelled run is terminal"',
            'assert_json_eq "cancelled run is terminal(LOST)"', 1)
        self.assertNotEqual(drifted, STORIES_SHA_PATH.read_text(encoding="utf-8"))
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            shadow = Path(tmp) / "stories.sh"
            shadow.write_text(drifted, encoding="utf-8")
            original = density_contract.SOURCE_PATH
            density_contract.SOURCE_PATH = shadow
            try:
                with self.assertRaises(RunStateError):
                    direct_conversation_density_plan()
            finally:
                density_contract.SOURCE_PATH = original

    def test_plan_fail_closes_on_tampered_row_anchor(self):
        rows = list(parsed_density_assertions())
        rows[13] = replace(rows[13], source_line=788)
        with self.assertRaises(RunStateError):
            density_contract._validate_source_anchors(tuple(rows))

    def test_plan_keeps_roster_provenance_and_read_only_flags(self):
        plan = direct_conversation_density_plan()
        self.assertTrue(plan["roster_provenance"]["guard_counted"])
        self.assertEqual(plan["roster_provenance"]["clause_count"], 14)
        self.assertEqual(plan["unlocated_source_probes"], [])
        self.assertEqual(plan["unlocated_count"], 0)
        self.assertTrue(plan["source_read_only"])
        self.assertEqual(plan["parent"], PARENT)


class TestProfileContract(unittest.TestCase):
    def test_canonical_local_and_preprod_profiles_admit(self):
        validate_density_profile(local_profile())
        validate_density_profile(preprod_profile())
        trio = preprod_profile()
        trio["auth"] = {
            "kind": "human-cookie-with-owned-bot-tokens",
            "driver_token_env": "BCS_E2E_DRIVER_TOKEN",
            "member_token_env": "BCS_E2E_DRIVER_TOKEN",
            "cookie_env": "BCS_E2E_HUMAN_COOKIE",
        }
        validate_density_profile(trio)

    def test_rejects_bad_tier_schema_contract_and_unknown_keys(self):
        for mutation, label in (
                (lambda p: p["environment"].__setitem__("tier", "prod"), "tier"),
                (lambda p: p.__setitem__("schema_version", 2), "schema_version"),
                (lambda p: p.__setitem__("contract", "other-v1"), "contract"),
                (lambda p: p.__setitem__("surprise", 1), "unknown top-level key"),
                (lambda p: p["environment"].__setitem__("surprise", 1), "unknown environment key"),
                (lambda p: p["entrypoint"].__setitem__("surprise", 1), "unknown entrypoint key"),
                (lambda p: p.pop("journey"), "missing journey"),
                (lambda p: p["environment"].__setitem__("lifecycle", "remote-attach"), "local lifecycle"),
                (lambda p: p["environment"].__setitem__("expected_bcs_env", "pre"), "local env binding"),
        ):
            with self.subTest(label):
                profile = local_profile()
                mutation(profile)
                self.assertRaises(ValueError, validate_density_profile, profile)

    def test_rejects_bad_member_bot_target_forms(self):
        with self.subTest("local qualified rejected"):
            profile = local_profile()
            profile["journey"]["member_bot_id"] = "bot-pm-direct-density-1:2088id"
            self.assertRaises(ValueError, validate_density_profile, profile)
        with self.subTest("preprod bare rejected"):
            profile = preprod_profile()
            profile["journey"]["member_bot_id"] = "bot-pm-direct-density-1"
            self.assertRaises(ValueError, validate_density_profile, profile)
        with self.subTest("preprod malformed owner rejected"):
            profile = preprod_profile()
            profile["journey"]["member_bot_id"] = "bot-pm-direct-density-1:abc"
            self.assertRaises(ValueError, validate_density_profile, profile)
        with self.subTest("local too-short rejected"):
            profile = local_profile()
            profile["journey"]["member_bot_id"] = "b1"
            self.assertRaises(ValueError, validate_density_profile, profile)

    def test_rejects_non_env_ref_auth_and_secrets_payload(self):
        with self.subTest("lowercase env value rejected"):
            profile = local_profile()
            profile["auth"]["driver_token_env"] = "bcs_e2e_driver_token"
            self.assertRaises(ValueError, validate_density_profile, profile)
        with self.subTest("local human-cookie kind rejected"):
            profile = local_profile()
            profile["auth"] = {"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"}
            self.assertRaises(ValueError, validate_density_profile, profile)
        with self.subTest("preprod unknown auth kind rejected"):
            profile = preprod_profile()
            profile["auth"] = {"kind": "literal-token", "token": "abc"}
            self.assertRaises(ValueError, validate_density_profile, profile)
        with self.subTest("secrets payload rejected"):
            profile = local_profile()
            profile["secrets"] = {"cookie": "value"}
            self.assertRaises(ValueError, validate_density_profile, profile)

    def test_rejects_binding_and_wire_face_drift(self):
        cases = {
            "missing operation": lambda b: b.pop("direct_chat.main.terminal"),
            "extra operation": lambda b: b.__setitem__("direct_chat.ping.submit", {"method": "POST", "path": "/x"}),
            "wrong method": lambda b: b["direct_chat.main.cancel"].__setitem__("method", "GET"),
            "wrong path": lambda b: b["direct_chat.main.read"].__setitem__("path", "/chat/runs/other"),
            "binding shape": lambda b: b["direct_chat.main.submit"].__setitem__("traceparent", "disabled"),
        }
        for label, mutation in cases.items():
            with self.subTest(label):
                profile = local_profile()
                mutation(profile["density_operation_bindings"])
                self.assertRaises(ValueError, validate_density_profile, profile)
        with self.subTest("non-legacy face rejected"):
            profile = local_profile()
            profile["entrypoint"]["api_face"] = "public-v1"
            self.assertRaises(ValueError, validate_density_profile, profile)

    def test_rejects_origin_and_browser_origin_drift(self):
        with self.subTest("local non-loopback rejected"):
            profile = local_profile()
            profile["entrypoint"]["origin"] = "http://192.168.1.10:18789"
            self.assertRaises(ValueError, validate_density_profile, profile)
        with self.subTest("local https rejected"):
            profile = local_profile()
            profile["entrypoint"]["origin"] = "https://127.0.0.1:18789"
            self.assertRaises(ValueError, validate_density_profile, profile)
        with self.subTest("preprod loopback rejected"):
            profile = preprod_profile()
            profile["entrypoint"]["origin"] = "http://127.0.0.1:18789"
            self.assertRaises(ValueError, validate_density_profile, profile)
        with self.subTest("preprod missing browser origin rejected"):
            profile = preprod_profile()
            profile.pop("browser_origin")
            self.assertRaises(ValueError, validate_density_profile, profile)
        with self.subTest("preprod http browser origin rejected"):
            profile = preprod_profile()
            profile["browser_origin"] = "http://bcs-pre.alipay.com"
            self.assertRaises(ValueError, validate_density_profile, profile)


class TestTransportBoundary(unittest.TestCase):
    def test_replay_transport_records_and_miss_fails_openly(self):
        transport = ReplayTransport({"DIRECT_DENSITY.C01": Observation(202, {})})
        transport.request("direct_chat.main.submit", "DIRECT_DENSITY.C01")
        self.assertEqual(transport.calls, [("direct_chat.main.submit", "DIRECT_DENSITY.C01")])
        with self.assertRaises(KeyError):
            transport.request("direct_chat.main.read", "DIRECT_DENSITY.C06")

    def test_projection_replay_maps_operations_and_statuses(self):
        bodies = {"direct_chat.main.submit": {"run_id": "r1"}}
        transport = ProjectionReplayTransport(bodies)
        observation = transport.request("direct_chat.main.submit", "DIRECT_DENSITY.C01")
        self.assertEqual(observation.status, 202)
        self.assertEqual(observation.body, {"run_id": "r1"})
        overridden = ProjectionReplayTransport(bodies, statuses={"direct_chat.main.submit": 502})
        self.assertEqual(overridden.request("direct_chat.main.submit", "DIRECT_DENSITY.C01").status, 502)

    def test_canonical_journey_is_defect_faithful(self):
        observations = canonical_defect_faithful_observations()
        self.assertEqual(len(observations), 14)
        terminal = observations["DIRECT_DENSITY.C13"].body
        self.assertEqual(terminal["state"], "cancelled")
        self.assertTrue(terminal["is_terminal"])
        self.assertEqual(terminal["error_message"], "downstream_error")
        self.assertTrue(observations["DIRECT_DENSITY.C10"].body["cancelled"])
        self.assertTrue(observations["DIRECT_DENSITY.C14"].body["is_terminal"])
        self.assertEqual(observations["DIRECT_DENSITY.C04"].body["bot_uuid"],
                         "bot-pm-direct-density-1")


class TestRunReceiptHonesty(unittest.TestCase):
    def test_without_transport_both_tiers_stay_blocked_not_run(self):
        for profile, executor in ((local_profile(), "owned-local-offline-rehearsal"),
                                  (preprod_profile(), "preprod-direct-chat-gateway-legacy")):
            with self.subTest(tier=profile["environment"]["tier"]):
                receipt = run_direct_conversation_density(profile=profile)
                self.assertEqual(receipt["status"], "BLOCKED")
                self.assertEqual(receipt["execution"]["status"], "BLOCKED")
                self.assertEqual(receipt["execution"]["not_run_count"], 14)
                self.assertEqual(receipt["execution"]["transport_injected"], False)
                self.assertEqual(receipt["execution"]["error_type"], "DENSITY_TRANSPORT_NOT_INJECTED")
                self.assertTrue(all(row["status"] == "NOT_RUN" for row in receipt["assertions"]))
                self.assertTrue(all(row["error_type"] == "DENSITY_TRANSPORT_NOT_INJECTED"
                                    for row in receipt["assertions"]))
                self.assertEqual(receipt["executor"], executor)
                self.assertEqual(receipt["honesty"]["remote_validation"], "NOT_RUN")
                self.assertFalse(receipt["honesty"]["grants_release_credit"])
                self.assertEqual(receipt["case_id"], "DUAL-DIRECT-CHAT-01")
                self.assertEqual(receipt["parent"], PARENT)
                self.assertEqual(receipt["case_scope"]["family_obligation_total"], 19)

    def test_canonical_local_rehearsal_passes_with_gate_disclosure(self):
        receipt = canonical_receipt()
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["execution"]["completed_count"], 14)
        self.assertEqual(receipt["execution"]["failed_count"], 0)
        self.assertEqual(receipt["execution"]["not_run_count"], 0)
        self.assertTrue(receipt["local_offline_rehearsal"])
        self.assertEqual(receipt["executor"], "owned-local-offline-rehearsal")
        for row in receipt["assertions"]:
            self.assertEqual(row["status"], "PASS", row["assertion_id"])
        gated = [row for row in receipt["assertions"] if "defect_gate" in row]
        self.assertEqual([row["assertion_id"] for row in gated],
                         [f"DIRECT_DENSITY.C{index:02d}" for index in range(9, 15)])
        terminal_row = receipt["assertions"][12]
        self.assertEqual(terminal_row["observed"], "cancelled")
        # raw observation disclosure: the on-file station abort product stays visible
        self.assertEqual(terminal_row["observation"]["error_message"], "downstream_error")
        self.assertTrue(terminal_row["observation"]["is_terminal"])
        gate = receipt["upstream_defect_gates"][DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID]
        self.assertFalse(gate["dm_obligation_credit_claimed"])
        self.assertEqual(gate["status"], "PRODUCT_DEFECT_ON_FILE")
        self.assertFalse(receipt["honesty"]["dm_obligation_credit_claimed"])
        self.assertEqual(receipt["honesty"]["remote_validation"], "NOT_RUN")
        self.assertFalse(receipt["honesty"]["grants_release_credit"])

    def test_credential_refs_are_env_names_only_and_digest_is_stable(self):
        local = canonical_receipt()
        self.assertEqual(local["credential_env_refs"], ["BCS_E2E_DRIVER_TOKEN"])
        preprod = run_direct_conversation_density(
            profile=preprod_profile(),
            transport=ReplayTransport(canonical_defect_faithful_observations()))
        self.assertEqual(preprod["credential_env_refs"], ["BCS_E2E_HUMAN_COOKIE"])
        self.assertEqual(local["profile_digest"],
                         run_direct_conversation_density(
                             profile=local_profile(),
                             transport=ReplayTransport(canonical_defect_faithful_observations()))["profile_digest"])
        self.assertNotEqual(local["profile_digest"], preprod["profile_digest"])
        self.assertRegex(local["profile_digest"], r"^[0-9a-f]{64}$")

    def test_blocked_journey_receipt_stays_not_run(self):
        receipt = blocked_direct_density_receipt(
            profile=local_profile(), blocked_by="DM journey fail-closed before density evaluation")
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertEqual(receipt["execution"]["error_type"], "DENSITY_BLOCKED_BY_JOURNEY_ADMISSION")
        self.assertEqual(receipt["blocked_by"], "DM journey fail-closed before density evaluation")
        self.assertTrue(all(row["status"] == "NOT_RUN" for row in receipt["assertions"]))
        self.assertIn(DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID, receipt["upstream_defect_gates"])
        self.assertEqual(sorted(receipt["gap_ledger"]), ["GAP-DC-01", "GAP-DC-02", "GAP-DC-03"])

    def test_read_back_run_id_mismatch_fails_and_cascades(self):
        observations = mutated_observations("DIRECT_DENSITY.C07", lambda body: body.__setitem__("run_id", "run-other-1"))
        receipt = run_direct_conversation_density(
            profile=local_profile(), transport=ReplayTransport(observations))
        self.assertEqual(receipt["status"], "FAIL")
        rows = {row["assertion_id"]: row for row in receipt["assertions"]}
        self.assertEqual(rows["DIRECT_DENSITY.C07"]["status"], "FAIL")
        self.assertEqual(rows["DIRECT_DENSITY.C07"]["observed"], "run-other-1")
        for clause in ("C08", "C09", "C10", "C11", "C12", "C13", "C14"):
            self.assertEqual(rows[f"DIRECT_DENSITY.{clause}"]["status"], "NOT_RUN")
            self.assertEqual(rows[f"DIRECT_DENSITY.{clause}"]["error_type"], "DENSITY_BLOCKED_AFTER_ASSERTION_FAILURE")
        self.assertEqual(receipt["execution"]["completed_count"], 6)
        self.assertEqual(receipt["execution"]["failed_count"], 1)
        self.assertEqual(receipt["execution"]["not_run_count"], 7)

    def test_submit_status_drift_fails_the_roster_open(self):
        observations = canonical_defect_faithful_observations()
        observations["DIRECT_DENSITY.C01"] = Observation(200, observations["DIRECT_DENSITY.C01"].body)
        receipt = run_direct_conversation_density(
            profile=local_profile(), transport=ReplayTransport(observations))
        self.assertEqual(receipt["status"], "FAIL")
        rows = {row["assertion_id"]: row for row in receipt["assertions"]}
        self.assertEqual(rows["DIRECT_DENSITY.C01"]["status"], "FAIL")
        self.assertEqual(rows["DIRECT_DENSITY.C01"]["observed"], 200)
        self.assertEqual(rows["DIRECT_DENSITY.C02"]["status"], "NOT_RUN")

    def test_cancel_ack_and_terminal_mutants_fail_their_own_clauses(self):
        cases = {
            "C10 cancelled false": ("DIRECT_DENSITY.C10", lambda b: b.__setitem__("cancelled", False)),
            "C11 ack state running": ("DIRECT_DENSITY.C11", lambda b: b.__setitem__("state", "running")),
            "C13 terminal state running": ("DIRECT_DENSITY.C13", lambda b: b.__setitem__("state", "running")),
            "C14 is_terminal false": ("DIRECT_DENSITY.C14", lambda b: b.__setitem__("is_terminal", False)),
            "C04 bot_uuid drift": ("DIRECT_DENSITY.C04", lambda b: b.__setitem__("bot_uuid", "bot-other")),
            "C02 empty run id": ("DIRECT_DENSITY.C02", lambda b: b.__setitem__("run_id", "")),
            "C03 missing session": ("DIRECT_DENSITY.C03", lambda b: b.pop("session_id")),
            "C08 empty state": ("DIRECT_DENSITY.C08", lambda b: b.__setitem__("state", "")),
        }
        for label, (target, mutation) in cases.items():
            with self.subTest(label):
                receipt = run_direct_conversation_density(
                    profile=local_profile(),
                    transport=ReplayTransport(mutated_observations(target, mutation)))
                self.assertEqual(receipt["status"], "FAIL", label)
                rows = {row["assertion_id"]: row for row in receipt["assertions"]}
                self.assertEqual(rows[target]["status"], "FAIL", label)

    def test_transport_failure_is_evidence_not_pass(self):
        observations = canonical_defect_faithful_observations()
        observations.pop("DIRECT_DENSITY.C12")
        receipt = run_direct_conversation_density(
            profile=local_profile(), transport=ReplayTransport(observations))
        self.assertEqual(receipt["status"], "FAIL")
        rows = {row["assertion_id"]: row for row in receipt["assertions"]}
        self.assertEqual(rows["DIRECT_DENSITY.C12"]["status"], "FAIL")
        self.assertEqual(rows["DIRECT_DENSITY.C12"]["error_type"], "KeyError")
        self.assertEqual(rows["DIRECT_DENSITY.C13"]["status"], "NOT_RUN")

    def test_evaluation_render_matches_common_sh_json_digest(self):
        from bcs_e2e.direct_conversation_density.run import _render
        self.assertEqual(_render(True), "true")
        self.assertEqual(_render(False), "false")
        self.assertEqual(_render(None), "null")
        self.assertEqual(_render({"b": 1, "a": 2}), '{"a":2,"b":1}')
        self.assertEqual(_render("cancelled"), "cancelled")


class TestDefectAndGapLedger(unittest.TestCase):
    def test_defect_registry_is_on_file_and_named(self):
        registry = density_defect.DEFECT_REGISTRY
        self.assertEqual(list(registry), [DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID])
        entry = registry[DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID]
        self.assertEqual(entry["status"], "PRODUCT_DEFECT_ON_FILE")
        self.assertIn("queued_runtime.rs:85", entry["station_semantics"])
        self.assertIn("downstream_error", entry["observation"])
        self.assertFalse(entry["dm_obligation_credit_claimed"])
        for path in entry["evidence_relative_paths"]:
            self.assertIsInstance(path, str)
        self.assertTrue(entry["evidence_relative_paths"])

    def test_gated_rows_are_exactly_the_cancel_terminal_chain(self):
        self.assertEqual(tuple(DEFECT_GATED_ASSERTION_IDS),
                         tuple(f"DIRECT_DENSITY.C{index:02d}" for index in range(9, 15)))
        assert_defect_gap_alignment()

    def test_gaps_are_documented_and_not_fabricated(self):
        ledger = density_defect.GAP_LEDGER
        self.assertEqual(set(ledger), {"GAP-DC-01", "GAP-DC-02", "GAP-DC-03"})
        self.assertFalse(ledger["GAP-DC-01"]["parsed_clause_claimed"])
        self.assertIn("second", ledger["GAP-DC-01"]["scope"])
        self.assertEqual(ledger["GAP-DC-02"]["status"], "PRODUCT_DEFECT_ON_FILE")
        self.assertEqual(ledger["GAP-DC-03"]["status"], "NOT_RUN_RECEIPT_ONLY")
        roster_ids = {row.assertion_id for row in parsed_density_assertions()}
        self.assertFalse(any("GAP" in assertion_id for assertion_id in roster_ids))
        self.assertEqual(len(roster_ids), 14)

    def test_alignment_guard_fail_closes_on_drift(self):
        original = density_defect.DENSITY_ASSERTION_COUNT
        density_defect.DENSITY_ASSERTION_COUNT = 13
        try:
            with self.assertRaises(RunStateError):
                assert_defect_gap_alignment()
        finally:
            density_defect.DENSITY_ASSERTION_COUNT = original


class TestReporting(unittest.TestCase):
    def test_density_accounting_of_canonical_receipt(self):
        accounting = density_accounting(canonical_receipt())
        self.assertEqual(accounting["total"], 14)
        self.assertEqual(accounting["counts"], {"PASS": 14, "FAIL": 0, "NOT_RUN": 0})
        self.assertEqual(accounting["defect_gated_counts"]["PASS"], 6)
        self.assertEqual(accounting["open_gaps"], ["GAP-DC-01", "GAP-DC-02", "GAP-DC-03"])
        self.assertFalse(accounting["grants_release_credit"])

    def test_family_accounting_is_frozen(self):
        accounting = family_accounting()
        self.assertEqual(accounting["parent_obligation_count"], 5)
        self.assertEqual(accounting["density_assertion_count"], 14)
        self.assertEqual(accounting["family_obligation_total"], 19)
        self.assertEqual(accounting["parsed_clause_coverage"], "14/14")
        self.assertEqual(accounting["residual_count_gap"], 0)

    def test_md_table_covers_all_clauses_and_anchor_lines(self):
        table = md_table(canonical_receipt())
        rows = [line for line in table.splitlines() if line.startswith("| DIRECT_DENSITY")]
        self.assertEqual(len(rows), 14)
        self.assertIn("| 782 |", table)
        self.assertIn("| 804 |", table)


class TestRegistrationProposal(unittest.TestCase):
    RED_LINED = (
        "bcs_e2e/cli.py",
        "bcs_e2e/case_command_registration.py",
        "bcs_e2e/inventory.py",
        "bcs_e2e/migration_inventory.py",
        "bcs_e2e/artifact.py",
        "executor-migration.json",
        "coverage-map/coverage-map-20261031.json",
        "coverage-map/coverage-map-20261031.md",
        "tests/test_registration_dual_waves.py",
    )

    def _digests(self):
        return {relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                for relative in self.RED_LINED}

    def test_proposal_is_describe_only_and_reuses_the_envelope(self):
        proposal = registration_proposal()
        self.assertEqual(proposal["proposal_status"], "DESCRIBE_ONLY")
        self.assertFalse(proposal["applied"])
        self.assertEqual(proposal["shared_face_files_touched"], [])
        self.assertEqual(proposal["envelope"]["command"], "dual-direct-chat-run")
        self.assertFalse(proposal["envelope"]["case_command_registration_required"])
        self.assertFalse(proposal["envelope"]["cli_required"])
        hunk = proposal["primary_hunk"]
        self.assertTrue(hunk["file"].endswith("direct_chat_cli_case_run.py"))
        self.assertIn("run_direct_conversation_density", hunk["imports"])
        self.assertIn("ProjectionReplayTransport(projections)", hunk["body"])
        self.assertIn("blocked_direct_density_receipt", hunk["imports"])
        self.assertIn("extra_evidence", hunk["step_closure_shape"])
        self.assertIn("Additive evidence key", hunk["caveats"])
        self.assertTrue(proposal["optional_new_command_hunks"]["disposition"].startswith("OPTIONAL"))

    def test_anchors_exist_in_the_current_tree(self):
        density_registration.assert_anchors()

    def test_anchor_drift_fail_closes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            original = density_registration.REPO_ROOT
            density_registration.REPO_ROOT = Path(tmp)
            try:
                with self.assertRaises(RunStateError):
                    density_registration.assert_anchors()
            finally:
                density_registration.REPO_ROOT = original

    def test_proposal_is_pure_no_shared_face_bytes_change(self):
        before = self._digests()
        proposal = registration_proposal()
        self.assertFalse(proposal["applied"])
        self.assertEqual(before, self._digests())

    def test_accounting_increments_are_proposals_only(self):
        proposal = registration_proposal()
        increments = proposal["accounting_increments"]
        obligations = increments["coverage_map_increment"]["obligations"]
        self.assertEqual(obligations["before"], 5)
        self.assertEqual(obligations["after"], 19)
        self.assertEqual(increments["coverage_map_increment"]["machine_totals"]["obligations_after"], 688)
        self.assertIn("BLOCKED_INVENTORIED", increments["coverage_map_increment"]["verdict_unchanged"])
        self.assertIn("5->19", increments["executor_migration_row_note"])
        self.assertEqual(increments["inventory_case_count_delta"], 0)
        self.assertEqual(len(proposal["materials"]), 4)
        material_text = " ".join(item["item"] for item in proposal["materials"])
        self.assertIn("credential", material_text.lower())


class TestPackageDiscipline(unittest.TestCase):
    def test_every_module_stays_under_the_line_budget(self):
        for path in sorted(DENSITY_PACKAGE.glob("*.py")):
            with self.subTest(file=path.name):
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 1000)
        self.assertLessEqual(len(Path(__file__).read_text(encoding="utf-8").splitlines()), 1000)

    def test_subpackage_import_boundary(self):
        forbidden_roots = {
            "bcs_e2e.cli", "bcs_e2e.case_command_registration", "bcs_e2e.inventory",
            "bcs_e2e.artifact", "bcs_e2e.migration_inventory", "bcs_e2e.slow_judge",
            "bcs_e2e.webhook_subscription", "bcs_e2e.local_webhook_subscription_contract",
            "bcs_e2e.remote_webhook_subscription_adapter", "bcs_e2e.edge_revoke_lifecycle",
            "bcs_e2e.delivery_density", "bcs_e2e.edge_friend_mutation_case",
            "bcs_e2e.edge_friend_mutation_case_run", "bcs_e2e.local_edge_friend_mutations_case",
            "bcs_e2e.remote_edge_friend_mutation_adapter", "bcs_e2e.workspace_field",
            "bcs_e2e.dynamic_family", "bcs_e2e.structured_density",
        }
        allowed_relative = {"contract", "defect", "profile", "transport", "run",
                            "reporting", "registration_proposal"}
        allowed_parents = {"local_direct_chat_contract", "profile", "run_state"}
        for path in sorted(DENSITY_PACKAGE.glob("*.py")):
            with self.subTest(file=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertFalse(alias.name.startswith("bcs_e2e."), (path.name, alias.name))
                    elif isinstance(node, ast.ImportFrom) and node.level == 2:
                        self.assertIn(node.module, allowed_parents, (path.name, node.module))
                    elif isinstance(node, ast.ImportFrom) and node.level == 1:
                        self.assertIn(node.module, allowed_relative, (path.name, node.module))
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        self.assertNotIn(node.module, forbidden_roots, (path.name, node.module))

    def test_no_network_process_env_or_home_reads(self):
        banned = ("urllib.request", "socket", "subprocess", "os.environ", "expanduser")
        for path in sorted(DENSITY_PACKAGE.glob("*.py")):
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                for token in banned:
                    self.assertNotIn(token, text, f"{path.name} must not contain {token}")

    def test_no_credential_value_literals(self):
        patterns = (
            re.compile(r"Bearer\s+[A-Za-z0-9._-]{8,}"),
            re.compile(r"sk-[A-Za-z0-9]{16,}"),
            re.compile(r"(?i)(cookie|token|secret)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
            re.compile(r"(?<![0-9a-f])[0-9a-f]{32,}(?![0-9a-f])"),
        )
        for path in sorted(DENSITY_PACKAGE.glob("*.py")):
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                for pattern in patterns:
                    self.assertIsNone(pattern.search(text), f"{path.name} tripped {pattern.pattern}")

    def test_flat_budget_intact_and_subpackage_is_new(self):
        flat = list((ROOT / "bcs_e2e").glob("*.py"))
        self.assertGreaterEqual(len(flat), 338)
        self.assertLessEqual(len(flat), 340)
        self.assertTrue((ROOT / "bcs_e2e" / "direct_conversation_density" / "contract.py").is_file())
        self.assertFalse((ROOT / "bcs_e2e" / "direct_conversation_density_flat_marker.py").exists())


class TestFamilyAlignment(unittest.TestCase):
    def test_operation_vocabulary_aligns_with_frozen_dm_leaves(self):
        from bcs_e2e.direct_chat_cli_case_leaf import (
            DIRECT_CHAT_MAIN_CANCEL_OPERATION,
            DIRECT_CHAT_MAIN_READ_OPERATION,
            DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
            DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
        )
        self.assertEqual(set(DENSITY_OPERATIONS), {
            DIRECT_CHAT_MAIN_SUBMIT_OPERATION,
            DIRECT_CHAT_MAIN_READ_OPERATION,
            DIRECT_CHAT_MAIN_CANCEL_OPERATION,
            DIRECT_CHAT_MAIN_TERMINAL_OPERATION,
        })

    def test_dm_parent_obligations_stay_the_frozen_five(self):
        self.assertEqual(list(REQUIREMENTS), [
            "DM.ping-submit", "DM.main-submit", "DM.main-read",
            "DM.main-cancel", "DM.main-terminal"])
        plan = direct_conversation_density_plan()
        self.assertEqual(plan["accounting"]["family_obligation_total"], 19)
        self.assertEqual(FAMILY_OBLIGATION_TOTAL, 19)

    def test_coverage_clause_boundary_no_ping_row_no_extra_row(self):
        rows = parsed_density_assertions()
        self.assertEqual(len(rows), PARSED_CLAUSE_COUNT)
        self.assertEqual(DENSITY_ASSERTION_COUNT, 14)
        self.assertEqual(FAMILY_OBLIGATION_TOTAL, PARENT_OBLIGATION_COUNT + 14)
        self.assertNotIn("DIRECT_DENSITY.C15", [row.assertion_id for row in rows])
        stages = [row.stage for row in rows]
        self.assertEqual(stages.count("async-submit"), 5)
        self.assertEqual(stages.count("run-read"), 3)
        self.assertEqual(stages.count("run-cancel"), 3)
        self.assertEqual(stages.count("run-terminal-read"), 3)


if __name__ == "__main__":
    unittest.main()
