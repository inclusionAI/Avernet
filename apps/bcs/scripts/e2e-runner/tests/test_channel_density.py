"""Offline density, anchor, replay, and integration tests for Wave-C channel stories.

Covers the two frozen upstream stories (6-green/8 HTTP and 2-green/4 CLI roster
snapshots), the 29-row verbatim roster with explicit upstream red/design-red
attribution, SHA-256 fail-close drift detection, legacy dual-channel envelope
reuse equality, the credential-free owned-local replay, the honest preprod
NOT_RUN projection with its four named registrations, the describe-only mainline
proposal, and package hygiene (no transport, no process, no credential values).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bcs_e2e.channel_density import (  # noqa: E402
    CHANNEL_BINDINGS_PATH_FACE_DRIFT_ID,
    CHANNEL_CLI_DISABLED_BRIDGE_DIVERGENCE_ID,
    CHANNEL_HTTP_NOOP_404_POSTURE_DIVERGENCE_ID,
    CHANNEL_SIM_DINGTALK_CREDENTIAL_ABSENT_DESIGN_RED_ID,
    CLI_DENSITY_OBLIGATION_COUNT,
    CLI_PARENT_OBLIGATION_COUNT,
    CLI_PARSED_OBLIGATION_COUNT,
    CLI_SOURCE_FILE,
    CLI_SOURCE_SHA256,
    CLI_STORY_LINE_SPAN,
    DENSITY_CONTRACT,
    DENSITY_OBLIGATION_COUNT,
    DRIVER_TOKEN_ENV,
    HTTP_DENSITY_OBLIGATION_COUNT,
    HTTP_PARENT_OBLIGATION_COUNT,
    HTTP_PARSED_OBLIGATION_COUNT,
    HTTP_SOURCE_FILE,
    HTTP_SOURCE_SHA256,
    HTTP_STORY_LINE_SPAN,
    HUMAN_COOKIE_ENV,
    NAMED_REGISTRATIONS,
    PARSED_OBLIGATION_COUNT,
    PARENT_OBLIGATION_COUNT,
    RESIDUAL_COUNT_GAP,
    SOURCE_REVISION,
    STORY_CLI,
    STORY_HTTP,
    UPSTREAM_RED,
    UPSTREAM_RED_BY_DESIGN,
    channel_density_plan,
    cli_density_plan,
    family_accounting,
    http_density_plan,
    obligations,
)
from bcs_e2e.channel_density import contract as density_contract  # noqa: E402
from bcs_e2e.channel_density.registration_proposal import (  # noqa: E402
    PROPOSAL_TOKEN,
    describe_only_mainline_fragment,
    registration_proposal,
)
from bcs_e2e.channel_density.replay import (  # noqa: E402
    BLOCKED_AFTER_ASSERTION_FAILURE,
    OBSERVATION_MISMATCH,
    STATUS_BLOCKED_INVENTORIED,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    mock_observations,
    named_registration_ids,
    project,
)
from bcs_e2e.local_channel_cli_contract import (  # noqa: E402
    COMMANDS as LEGACY_CLI_COMMANDS,
    REQUIREMENTS as LEGACY_CLI_REQUIREMENTS,
    channel_cli_source_binding,
)
from bcs_e2e.local_channel_contract import (  # noqa: E402
    REQUIREMENTS as LEGACY_HTTP_REQUIREMENTS,
    channel_source_binding,
)
from bcs_e2e.remote_channel_cli_adapter import (  # noqa: E402
    CHANNEL_CLI_ADAPTER_METHODS,
)
from bcs_e2e.run_state import RunStateError  # noqa: E402

SOURCE_HTTP = ROOT.parent / "e2e-test" / "stories.sh"
SOURCE_CLI = ROOT.parent / "e2e-test" / "cli-stories.sh"
LEGACY_HTTP_JSON = ROOT / "channel-http-migration-contract.json"
LEGACY_CLI_JSON = ROOT / "channel-cli-migration-contract.json"
EXECUTOR_JSON = ROOT / "executor-migration.json"
PACKAGE = ROOT / "bcs_e2e" / "channel_density"

HTTP_PARENT_IDS = ("CHANNEL.bind-denied", "CHANNEL.list-before", "CHANNEL.target-empty",
                   "CHANNEL.pause-missing", "CHANNEL.delete-missing", "CHANNEL.list-after")
CLI_PARENT_IDS = tuple(LEGACY_CLI_REQUIREMENTS)
HTTP_ATOM_IDS = tuple(f"CHANNEL-HTTP-DENSITY.D{i:02d}" for i in range(1, 8))
CLI_ATOM_IDS = tuple(f"CHANNEL-CLI-DENSITY.D{i:02d}" for i in range(1, 8))
ANCHOR_RANGES = (
    (1740, 1742), (1746, 1747), (1754, 1755), (1759, 1760), (1763, 1764), (1767, 1768),
    (1743, 1743), (1744, 1744), (1750, 1750), (1756, 1756), (1761, 1761), (1765, 1765),
    (1769, 1769),
    (510, 527), (529, 529), (532, 532), (534, 535), (540, 541), (544, 544), (547, 547),
    (550, 551), (552, 553),
    (515, 518), (67, 67), (530, 530), (537, 537), (542, 542), (545, 545), (548, 548),
)
HTTP_METHODS_FOR_PARENTS = ("api_post", "api_get", "api_get", "api_patch", "api_delete", "api_get")
LEGACY_HTTP_METHODS = ("POST", "GET", "GET", "PATCH", "DELETE", "GET")


class TestChannelDensityRoster(unittest.TestCase):
    def test_accounting_identities_close_both_coverage_map_gaps(self):
        self.assertEqual(family_accounting(), {
            "parsed": 29, "parents": 15, "density_atoms": 14, "residual_count_gap": 0})
        self.assertEqual((HTTP_PARSED_OBLIGATION_COUNT, HTTP_PARENT_OBLIGATION_COUNT,
                          HTTP_DENSITY_OBLIGATION_COUNT), (13, 6, 7))
        self.assertEqual((CLI_PARSED_OBLIGATION_COUNT, CLI_PARENT_OBLIGATION_COUNT,
                          CLI_DENSITY_OBLIGATION_COUNT), (16, 9, 7))
        self.assertEqual(PARSED_OBLIGATION_COUNT, PARENT_OBLIGATION_COUNT + DENSITY_OBLIGATION_COUNT)
        self.assertEqual(RESIDUAL_COUNT_GAP, 0)
        plan = channel_density_plan()
        self.assertEqual(plan["accounting"]["parsed_obligation_count"], 29)
        self.assertEqual(plan["accounting"]["parent_obligation_count"], 15)
        self.assertEqual(plan["accounting"]["density_obligation_count"], 14)
        self.assertEqual(plan["accounting"]["residual_count_gap"], 0)
        http_acct = plan["accounting"]["per_story"][STORY_HTTP]
        cli_acct = plan["accounting"]["per_story"][STORY_CLI]
        self.assertEqual((http_acct["parsed"], http_acct["coverage_map_snapshot_parsed"],
                          http_acct["coverage_map_legacy_obligations"], http_acct["coverage_map_gap"]),
                         (13, 13, 6, 7))
        self.assertEqual((cli_acct["parsed"], cli_acct["coverage_map_snapshot_parsed"],
                          cli_acct["coverage_map_legacy_obligations"], cli_acct["coverage_map_gap"]),
                         (16, 16, 9, 7))
        self.assertIn("13 = 6 legacy parents + 7 density atoms", http_acct["identity"])
        self.assertIn("16 = 9 legacy parents + 7 density atoms", cli_acct["identity"])

    def test_roster_is_verbatim_and_projects_the_legacy_parent_envelopes(self):
        rows = obligations()
        self.assertEqual(len(rows), 29)
        self.assertEqual([row.obligation_id for row in rows],
                         list(HTTP_PARENT_IDS) + list(HTTP_ATOM_IDS)
                         + list(CLI_PARENT_IDS) + list(CLI_ATOM_IDS))
        self.assertEqual(tuple(row.line_start for row in rows) and
                         [tuple((row.line_start, row.line_end)) for row in rows],
                         list(ANCHOR_RANGES))
        http_lines = SOURCE_HTTP.read_text(encoding="utf-8").splitlines()
        cli_lines = SOURCE_CLI.read_text(encoding="utf-8").splitlines()
        for row in rows:
            lines = http_lines if row.source_file == HTTP_SOURCE_FILE else cli_lines
            self.assertTrue(row.line_start <= row.line_end, row.obligation_id)
            self.assertEqual("\n".join(lines[row.line_start - 1:row.line_end]), row.quote,
                             row.obligation_id)
            self.assertTrue(row.located)
            self.assertTrue(row.method)
            self.assertTrue(row.expectation)
            if row.level == "PARENT":
                self.assertEqual(row.parent_obligation, row.obligation_id)
            else:
                self.assertIn(row.parent_obligation, HTTP_PARENT_IDS + CLI_PARENT_IDS)
        # story span containment, with the one disclosed fixture-helper anchor
        for row in obligations():
            if row.source_file == HTTP_SOURCE_FILE:
                self.assertTrue(HTTP_STORY_LINE_SPAN[0] <= row.line_start
                                <= row.line_end <= HTTP_STORY_LINE_SPAN[1], row.obligation_id)
            elif row.obligation_id == "CHANNEL-CLI-DENSITY.D02":
                self.assertTrue(CLI_FIXTURE_HELPER_SPAN_LOW <= row.line_start
                                <= row.line_end <= CLI_FIXTURE_HELPER_SPAN_HIGH, row.obligation_id)
            else:
                self.assertTrue(CLI_STORY_LINE_SPAN[0] <= row.line_start
                                <= row.line_end <= CLI_STORY_LINE_SPAN[1], row.obligation_id)
        # parent methods: HTTP api verbs in order; CLI adapter methods reused verbatim
        self.assertEqual([row.method for row in obligations() if row.source_file == HTTP_SOURCE_FILE
                          and row.level == "PARENT"], list(HTTP_METHODS_FOR_PARENTS))
        self.assertEqual([row.method for row in obligations() if row.source_file == CLI_SOURCE_FILE
                          and row.level == "PARENT"], list(CHANNEL_CLI_ADAPTER_METHODS))

    def test_upstream_colors_are_disclosed_and_aggregate_attributed(self):
        rows = obligations()
        http_rows = [row for row in rows if row.source_file == HTTP_SOURCE_FILE]
        cli_rows = [row for row in rows if row.source_file == CLI_SOURCE_FILE]
        self.assertEqual(sum(1 for row in http_rows if row.upstream_color == "GREEN"), 9)
        self.assertEqual(sum(1 for row in http_rows if row.upstream_color == UPSTREAM_RED), 4)
        self.assertEqual(sum(1 for row in cli_rows if row.upstream_color == "GREEN"), 14)
        self.assertEqual(sum(1 for row in cli_rows if row.upstream_color == UPSTREAM_RED_BY_DESIGN), 2)
        red_ids = {row.obligation_id for row in rows if row.upstream_color == UPSTREAM_RED}
        self.assertEqual(red_ids, {"CHANNEL.pause-missing", "CHANNEL.delete-missing",
                                   "CHANNEL-HTTP-DENSITY.D05", "CHANNEL-HTTP-DENSITY.D06"})
        design_red_ids = {row.obligation_id for row in rows if row.upstream_color == UPSTREAM_RED_BY_DESIGN}
        self.assertEqual(design_red_ids, {"CHANNEL-CLI.conversation-empty", "CHANNEL-CLI-DENSITY.D05"})
        for row in rows:
            self.assertIn("poolab", row.upstream_color_basis)
            self.assertIn("not retained", row.upstream_color_basis)
        plan = channel_density_plan()
        self.assertIn("negative-evidence discipline", plan["upstream_color_basis"])
        # honest leaf classifications
        guard = next(row for row in rows if row.obligation_id == "CHANNEL-CLI-DENSITY.D01")
        self.assertIn("conditional", guard.leaf_classification)
        helper = next(row for row in rows if row.obligation_id == "CHANNEL-CLI-DENSITY.D02")
        self.assertEqual(helper.leaf_classification, "fixture_helper_assertion")
        self.assertTrue(all(row.leaf_classification in {
            "http_request_envelope", "cli_command_envelope", "assertion_atom",
            "conditional_guard_dormant", "fixture_helper_assertion"} for row in rows))

    def test_conditional_guard_is_charged_with_dormancy_disclosure(self):
        guard = next(row for row in obligations() if row.obligation_id == "CHANNEL-CLI-DENSITY.D01")
        self.assertEqual(guard.level, "DENSITY")
        self.assertEqual(guard.comparator, "equals")
        self.assertEqual(guard.expected, "dormant_premise_discharged_by_parent")
        self.assertEqual(guard.parent_obligation, "CHANNEL-CLI.bind-redacted")
        self.assertIn("dormant", guard.expectation)
        self.assertIn("discharged by the parent", guard.expectation)

    def test_source_sha256_is_recorded_and_anchor_drift_fails_closed(self):
        self.assertEqual(hashlib.sha256(SOURCE_HTTP.read_bytes()).hexdigest(), HTTP_SOURCE_SHA256)
        self.assertEqual(hashlib.sha256(SOURCE_CLI.read_bytes()).hexdigest(), CLI_SOURCE_SHA256)
        drifted_http = SOURCE_HTTP.read_text(encoding="utf-8").replace(
            "channel setup error identifies disabled bridge",
            "channel setup error identifies a drifted bridge", 1)
        self.assertNotEqual(drifted_http, SOURCE_HTTP.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            shadow = Path(temp) / "stories.sh"
            shadow.write_text(drifted_http, encoding="utf-8")
            original = density_contract.HTTP_SOURCE_PATH
            density_contract.HTTP_SOURCE_PATH = shadow
            try:
                with self.assertRaises(RunStateError):
                    obligations()
            finally:
                density_contract.HTTP_SOURCE_PATH = original
        drifted_cli = SOURCE_CLI.read_text(encoding="utf-8").replace(
            "CLI-created group returns an id", "CLI-created group returns a drifted id", 1)
        with tempfile.TemporaryDirectory() as temp:
            shadow = Path(temp) / "cli-stories.sh"
            shadow.write_text(drifted_cli, encoding="utf-8")
            original = density_contract.CLI_SOURCE_PATH
            density_contract.CLI_SOURCE_PATH = shadow
            try:
                with self.assertRaises(RunStateError):
                    obligations()
            finally:
                density_contract.CLI_SOURCE_PATH = original
        self.assertEqual(len(obligations()), 29)

    def test_projection_exclusions_are_explicit_and_never_charged(self):
        plan = channel_density_plan()
        self.assertEqual(plan["unlocated_count"], 0)
        self.assertEqual(plan["unlocated_source_probes"], [])
        self.assertTrue(plan["source_read_only"])
        exclusions = plan["projection_exclusions"]
        self.assertGreaterEqual(len(exclusions), 20)
        http_lines = SOURCE_HTTP.read_text(encoding="utf-8").splitlines()
        cli_lines = SOURCE_CLI.read_text(encoding="utf-8").splitlines()
        charged = {HTTP_SOURCE_FILE: [], CLI_SOURCE_FILE: []}
        for row in obligations():
            charged[row.source_file].append((row.line_start, row.line_end))
        for item in exclusions:
            lines = cli_lines if item["source_file"] == CLI_SOURCE_FILE else http_lines
            self.assertEqual("\n".join(lines[item["line_start"] - 1:item["line_end"]]), item["quote"])
            self.assertNotEqual(item["category"], "assertion")
        reasons = " ".join(item["reason"] for item in exclusions)
        self.assertIn("control-flow", reasons)
        self.assertIn("conditional-branch", plan["projection_exclusion_note"])
        for item in exclusions:
            for span in charged[item["source_file"]]:
                self.assertFalse(item["line_start"] <= span[1] and span[0] <= item["line_end"],
                                 (item["id"], span))

    def test_legacy_dual_channel_envelopes_are_reused_not_redeclared(self):
        http_json = json.loads(LEGACY_HTTP_JSON.read_text(encoding="utf-8"))
        cli_json = json.loads(LEGACY_CLI_JSON.read_text(encoding="utf-8"))
        plan = channel_density_plan()
        http_envelope = plan["stories"][STORY_HTTP]["legacy_envelope"]
        cli_envelope = plan["stories"][STORY_CLI]["legacy_envelope"]
        self.assertEqual(http_envelope["requirements"], http_json["requirements"])
        self.assertEqual(cli_envelope["requirements"], cli_json["requirements"])
        self.assertEqual(http_envelope["sources"], http_json["sources"])
        self.assertEqual(cli_envelope["sources"], cli_json["sources"])
        self.assertEqual(http_envelope["source_revision"], SOURCE_REVISION)
        self.assertEqual(cli_envelope["source_revision"], SOURCE_REVISION)
        self.assertEqual([row["id"] for row in http_json["requirements"]], list(LEGACY_HTTP_REQUIREMENTS))
        self.assertEqual([row["command"] for row in cli_json["requirements"]], list(LEGACY_CLI_COMMANDS))
        # validators reused as the fail-close reuse gate
        self.assertEqual([row["id"] for row in channel_source_binding()["requirements"]],
                         list(LEGACY_HTTP_REQUIREMENTS))
        self.assertEqual([row["id"] for row in channel_cli_source_binding()["requirements"]],
                         list(LEGACY_CLI_REQUIREMENTS))
        http_rows = [row for row in obligations() if row.source_file == HTTP_SOURCE_FILE
                     and row.level == "PARENT"]
        self.assertEqual([row.method for row in http_rows], list(HTTP_METHODS_FOR_PARENTS))
        self.assertEqual({row.method for row in http_rows} | {"api_post", "api_patch", "api_delete"},
                         {"api_post", "api_get", "api_patch", "api_delete"})
        legacy_methods = [row["method"] for row in http_json["requirements"]]
        self.assertEqual(legacy_methods, list(LEGACY_HTTP_METHODS))
        cli_rows = [row for row in obligations() if row.source_file == CLI_SOURCE_FILE
                    and row.level == "PARENT"]
        self.assertEqual([row.method for row in cli_rows], list(CHANNEL_CLI_ADAPTER_METHODS))
        self.assertEqual([row.operation for row in cli_rows][:1], ["channel.bind.disabled_bridge"])
        self.assertEqual(http_envelope["reused_dual_command"], "dual-channel-bindings-run")
        self.assertEqual(cli_envelope["reused_dual_command"], "dual-channel-run")
        self.assertEqual(http_envelope["reused_case_ids"], ["LOCAL-CHANNEL-HTTP-01",
                                                            "PUBLIC-CHANNEL-BINDINGS-01"])
        self.assertEqual(cli_envelope["reused_case_ids"], ["LOCAL-CHANNEL-CLI-01",
                                                           "DUAL-CHANNEL-CLI-01"])

    def test_executor_migration_rows_are_reused_not_rewritten(self):
        data = json.loads(EXECUTOR_JSON.read_text(encoding="utf-8"))
        by_id = {row["id"]: row["executor_migration"] for row in data["rows"]}
        self.assertEqual(by_id[STORY_HTTP]["mode"], "DUAL")
        self.assertEqual(by_id[STORY_HTTP]["preprod_verdict"], "PASS")
        self.assertEqual(by_id[STORY_CLI]["mode"], "DUAL")
        self.assertEqual(by_id[STORY_CLI]["preprod_verdict"], "BLOCKED_INVENTORIED")
        proposal = registration_proposal()
        self.assertEqual(proposal["reused_dual_channel_envelopes"][STORY_HTTP]["retained_preprod_verdict"],
                         by_id[STORY_HTTP]["preprod_verdict"])
        self.assertEqual(proposal["reused_dual_channel_envelopes"][STORY_CLI]["retained_preprod_verdict"],
                         by_id[STORY_CLI]["preprod_verdict"])
        self.assertEqual(proposal["reused_dual_channel_envelopes"][STORY_HTTP]["existing_executor_row"], 10)
        self.assertEqual(proposal["reused_dual_channel_envelopes"][STORY_CLI]["existing_executor_row"], 11)

    def test_credential_references_are_environment_variable_names_only(self):
        plan = channel_density_plan()
        self.assertEqual(plan["credential_references"], [HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV])
        self.assertEqual((HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV),
                         ("BCS_E2E_HUMAN_COOKIE", "BCS_E2E_DRIVER_TOKEN"))
        text = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py"))
        self.assertIn(HUMAN_COOKIE_ENV, text)
        self.assertIn(DRIVER_TOKEN_ENV, text)
        names = set(re.findall(r"BCS_E2E_[A-Z_]+", text))
        self.assertEqual(names, {HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV})


class TestChannelDensityReplay(unittest.TestCase):
    def test_owned_local_replay_passes_all_twenty_nine_rows(self):
        receipt = project()
        self.assertEqual(receipt["status"], STATUS_PASS)
        self.assertEqual(receipt["counts"], {STATUS_PASS: 29, STATUS_FAIL: 0, STATUS_NOT_RUN: 0})
        self.assertEqual(receipt["parsed_obligation_count"], 29)
        self.assertEqual(receipt["parent_obligation_count"], 15)
        self.assertEqual(receipt["density_obligation_count"], 14)
        self.assertEqual(receipt["residual_count_gap"], 0)
        self.assertEqual(receipt["remote_validation"], "NOT_RUN")
        self.assertFalse(receipt["grants_release_credit"])
        self.assertEqual(receipt["execution"], "owned-local-offline-replay")
        self.assertEqual(receipt["environment_tier"], "local")
        self.assertTrue(all(row["status"] == STATUS_PASS for row in receipt["obligations"]))
        self.assertEqual(receipt["stories"][STORY_HTTP]["counts"],
                         {STATUS_PASS: 13, STATUS_FAIL: 0, STATUS_NOT_RUN: 0})
        self.assertEqual(receipt["stories"][STORY_CLI]["counts"],
                         {STATUS_PASS: 16, STATUS_FAIL: 0, STATUS_NOT_RUN: 0})
        for row in receipt["obligations"]:
            self.assertEqual(row["source"]["located"], True)
            self.assertEqual(row["error_type"], None)
        # verbatim source blocks in the receipt itself
        http_lines = SOURCE_HTTP.read_text(encoding="utf-8").splitlines()
        cli_lines = SOURCE_CLI.read_text(encoding="utf-8").splitlines()
        for row in receipt["obligations"]:
            lines = http_lines if row["story"] == STORY_HTTP else cli_lines
            self.assertEqual("\n".join(lines[row["source"]["line_start"] - 1:
                                            row["source"]["line_end"]]), row["source"]["quote"])

    def test_local_default_and_injected_mock_observations_agree(self):
        default_receipt = project()
        injected_receipt = project(mock_observations())
        self.assertEqual(default_receipt["status"], injected_receipt["status"])
        self.assertEqual([row["status"] for row in default_receipt["obligations"]],
                         [row["status"] for row in injected_receipt["obligations"]])

    def test_design_red_and_posture_rows_carry_their_named_attributions(self):
        receipt = project()
        by_id = {row["obligation_id"]: row for row in receipt["obligations"]}
        for obligation_id in ("CHANNEL-CLI.conversation-empty", "CHANNEL-CLI-DENSITY.D05"):
            row = by_id[obligation_id]
            self.assertEqual(row["upstream_color"], UPSTREAM_RED_BY_DESIGN)
            self.assertEqual(row["evidence_event"], "owned_local_empty_table_projection")
            self.assertIn("empty owned-table projection", row["local_classification"])
            self.assertIn("M7", row["local_classification"])
            self.assertIn(CHANNEL_SIM_DINGTALK_CREDENTIAL_ABSENT_DESIGN_RED_ID,
                          row["named_registrations"])
        for obligation_id in ("CHANNEL.pause-missing", "CHANNEL.delete-missing",
                              "CHANNEL-HTTP-DENSITY.D05", "CHANNEL-HTTP-DENSITY.D06"):
            row = by_id[obligation_id]
            self.assertEqual(row["upstream_color"], UPSTREAM_RED)
            self.assertEqual(row["evidence_event"], "disabled_bridge_noop_admission")
            self.assertIn("DISABLED_BRIDGE_AND_EMPTY_OWNED_DATABASE", row["local_classification"])
            self.assertIn(CHANNEL_HTTP_NOOP_404_POSTURE_DIVERGENCE_ID, row["named_registrations"])
        guard = by_id["CHANNEL-CLI-DENSITY.D01"]
        self.assertEqual(guard["evidence_event"], "conditional_guard_discharge")
        self.assertIn("dormant", guard["local_classification"])
        helper = by_id["CHANNEL-CLI-DENSITY.D02"]
        self.assertEqual(helper["evidence_event"], "fixture_helper_assertion")
        self.assertIn("fixture helper", helper["local_classification"])

    def test_preprod_defaults_to_blocked_inventoried_not_run_with_registrations(self):
        receipt = project(tier="preprod")
        self.assertEqual(receipt["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertEqual(receipt["counts"], {STATUS_PASS: 0, STATUS_FAIL: 0, STATUS_NOT_RUN: 29})
        self.assertEqual(receipt["execution"], "preprod-not-run")
        self.assertEqual(receipt["environment_tier"], "preprod")
        self.assertTrue(all(row["status"] == STATUS_NOT_RUN for row in receipt["obligations"]))
        self.assertTrue(all(row["error_type"] is None for row in receipt["obligations"]))
        self.assertEqual(receipt["remote_validation"], "NOT_RUN")
        self.assertFalse(receipt["grants_release_credit"])
        self.assertEqual(sorted(receipt["named_registrations"]), sorted(named_registration_ids()))
        self.assertEqual(receipt["preprod_execution_gap"]["status"], STATUS_NOT_RUN)
        divergence = receipt["named_registrations"][CHANNEL_CLI_DISABLED_BRIDGE_DIVERGENCE_ID]
        self.assertIn("TRUE_BUSINESS_JUDGMENT_NOT_DEFECT", divergence["disposition"])
        self.assertIn("ChannelCliRunnerError", divergence["observation"])
        self.assertIn("bind_disabled_bridge", divergence["observation"])
        self.assertIn("human_cookie", divergence["observation"])
        design_red = receipt["named_registrations"][CHANNEL_SIM_DINGTALK_CREDENTIAL_ABSENT_DESIGN_RED_ID]
        self.assertIn("M7", design_red["observation"])
        noop = receipt["named_registrations"][CHANNEL_HTTP_NOOP_404_POSTURE_DIVERGENCE_ID]
        self.assertIn("404", noop["observation"])
        self.assertIn("DISABLED_BRIDGE_AND_EMPTY_OWNED_DATABASE", noop["observation"])
        face = receipt["named_registrations"][CHANNEL_BINDINGS_PATH_FACE_DRIFT_ID]
        self.assertIn("/openapi/v1/collaboration/channels/bindings", face["observation"])
        self.assertIn("%3A", face["observation"])
        envelopes = receipt["dual_channel_envelopes"]
        self.assertIn("dual-channel-bindings-run", envelopes[STORY_HTTP]["retained_conclusion"])
        self.assertIn("dual-channel-run", envelopes[STORY_CLI]["retained_conclusion"])
        self.assertIn("BLOCKED 0/9", envelopes[STORY_CLI]["retained_conclusion"])

    def test_negative_observation_fails_and_cascades_in_roster_order(self):
        observations = mock_observations()
        observations["parents"]["CHANNEL.bind-denied"] = STATUS_FAIL
        receipt = project(observations)
        self.assertEqual(receipt["status"], STATUS_FAIL)
        by_id = {row["obligation_id"]: row for row in receipt["obligations"]}
        self.assertEqual(by_id["CHANNEL.bind-denied"]["status"], STATUS_FAIL)
        self.assertEqual(by_id["CHANNEL.bind-denied"]["error_type"], OBSERVATION_MISMATCH)
        self.assertEqual(by_id["CHANNEL-HTTP-DENSITY.D01"]["status"], STATUS_NOT_RUN)
        self.assertEqual(by_id["CHANNEL-HTTP-DENSITY.D01"]["error_type"],
                         BLOCKED_AFTER_ASSERTION_FAILURE)
        self.assertEqual(by_id["CHANNEL-CLI.group-delete"]["status"], STATUS_NOT_RUN)
        self.assertEqual(receipt["counts"], {STATUS_PASS: 0, STATUS_FAIL: 1, STATUS_NOT_RUN: 28})

    def test_atom_value_mismatch_fails_not_not_run(self):
        observations = mock_observations()
        observations["fields"]["CHANNEL-HTTP-DENSITY.D03"] = {"status": STATUS_PASS, "value": "0"}
        receipt = project(observations)
        by_id = {row["obligation_id"]: row for row in receipt["obligations"]}
        self.assertEqual(by_id["CHANNEL-HTTP-DENSITY.D03"]["status"], STATUS_FAIL)
        self.assertEqual(by_id["CHANNEL-HTTP-DENSITY.D03"]["error_type"], OBSERVATION_MISMATCH)
        self.assertEqual(receipt["status"], STATUS_FAIL)

    def test_malformed_atom_entry_fails_and_missing_entries_stay_not_run(self):
        observations = mock_observations()
        observations["fields"]["CHANNEL-HTTP-DENSITY.D01"] = {"status": STATUS_PASS}
        receipt = project(observations)
        by_id = {row["obligation_id"]: row for row in receipt["obligations"]}
        self.assertEqual(by_id["CHANNEL-HTTP-DENSITY.D01"]["status"], STATUS_FAIL)
        sparse = {"parents": {HTTP_PARENT_IDS[0]: STATUS_PASS}, "fields": {}}
        receipt = project(sparse)
        self.assertEqual(receipt["obligations"][0]["status"], STATUS_PASS)
        self.assertEqual(receipt["obligations"][1]["status"], STATUS_NOT_RUN)
        self.assertIsNone(receipt["obligations"][1]["error_type"])
        self.assertEqual(receipt["status"], STATUS_BLOCKED_INVENTORIED)
        self.assertFalse(any(row["status"] == STATUS_PASS and not row["obligation_id"]
                             for row in receipt["obligations"]))
        self.assertEqual(receipt["counts"], {STATUS_PASS: 1, STATUS_FAIL: 0, STATUS_NOT_RUN: 28})

    def test_preprod_injection_seam_evaluates_without_release_credit(self):
        receipt = project(mock_observations(), tier="preprod")
        self.assertEqual(receipt["counts"][STATUS_PASS], 29)
        self.assertEqual(receipt["execution"], "preprod-injected-observation-projection")
        self.assertEqual(receipt["remote_validation"], "NOT_RUN")
        self.assertFalse(receipt["grants_release_credit"])
        self.assertIn("in-memory observations only", receipt["transport_admission"])

    def test_tier_and_observation_shape_validation_fails_closed(self):
        with self.assertRaises(RunStateError):
            project(tier="staging")
        with self.assertRaises(RunStateError):
            project(["not", "an", "object"])


class TestChannelNamedRegistrations(unittest.TestCase):
    def test_four_named_registrations_are_disclosed(self):
        self.assertEqual(len(NAMED_REGISTRATIONS), 4)
        self.assertEqual(sorted(NAMED_REGISTRATIONS), sorted(named_registration_ids()))
        divergence = NAMED_REGISTRATIONS[CHANNEL_CLI_DISABLED_BRIDGE_DIVERGENCE_ID]
        self.assertIn("TRUE_BUSINESS_JUDGMENT_NOT_DEFECT", divergence["disposition"])
        design_red = NAMED_REGISTRATIONS[CHANNEL_SIM_DINGTALK_CREDENTIAL_ABSENT_DESIGN_RED_ID]
        self.assertEqual(design_red["category"], "upstream_sim_design_red")
        noop = NAMED_REGISTRATIONS[CHANNEL_HTTP_NOOP_404_POSTURE_DIVERGENCE_ID]
        self.assertIn("public-v1", noop["observation"])
        self.assertIn("404", noop["observation"])
        face = NAMED_REGISTRATIONS[CHANNEL_BINDINGS_PATH_FACE_DRIFT_ID]
        self.assertIn("/channels/bindings", face["observation"])
        self.assertIn("/openapi/v1/collaboration/channels/bindings", face["observation"])
        plan = channel_density_plan()
        self.assertEqual(plan["named_registrations"], NAMED_REGISTRATIONS)
        rows = obligations()
        charged = {reg for row in rows for reg in row.named_registrations}
        self.assertEqual(charged, set(named_registration_ids()))


class TestChannelIntegrationDiscipline(unittest.TestCase):
    def test_proposal_is_describe_only_with_two_line_notes_increment(self):
        proposal = registration_proposal()
        self.assertEqual(proposal["status"], "DESCRIBE_ONLY")
        self.assertEqual(proposal["token"], PROPOSAL_TOKEN)
        self.assertEqual(PROPOSAL_TOKEN, "CHANNEL_DENSITY_14_OBLIGATIONS_20261031")
        self.assertFalse(proposal["new_registration_command"])
        self.assertEqual(len(proposal["notes"]), 2)
        self.assertIn("row #10", proposal["notes"][0])
        self.assertIn("13 parsed = 6 legacy parents + 7 atoms", proposal["notes"][0])
        self.assertIn("dual-channel-bindings-run", proposal["notes"][0])
        self.assertIn("row #11", proposal["notes"][1])
        self.assertIn("16 parsed = 9 legacy parents + 7 atoms", proposal["notes"][1])
        self.assertIn("true business judgment", proposal["notes"][1])
        self.assertIn("remote_validation=NOT_RUN", proposal["notes"][1])
        self.assertEqual(proposal["parsed_obligations"], 29)
        self.assertEqual(proposal["residual_count_gap"], 0)
        self.assertFalse(proposal["grants_release_credit"])
        red_lines = " ".join(proposal["red_lines"])
        for forbidden in ("executor-migration.json", "inventory.py", "cli.py", "coverage-map/*"):
            self.assertIn(forbidden, red_lines)
        envelopes = proposal["reused_dual_channel_envelopes"]
        self.assertEqual(envelopes[STORY_HTTP]["reused_command"], "dual-channel-bindings-run")
        self.assertEqual(envelopes[STORY_HTTP]["retained_preprod_verdict"], "PASS")
        self.assertEqual(envelopes[STORY_CLI]["reused_command"], "dual-channel-run")
        self.assertEqual(envelopes[STORY_CLI]["retained_preprod_verdict"], "BLOCKED_INVENTORIED")
        self.assertEqual(envelopes[STORY_HTTP]["reused_legacy_requirement_ids"],
                         list(LEGACY_HTTP_REQUIREMENTS))
        self.assertEqual(envelopes[STORY_CLI]["reused_legacy_requirement_ids"],
                         list(LEGACY_CLI_REQUIREMENTS))
        self.assertEqual(proposal["named_registrations"], list(named_registration_ids()))

    def test_describe_only_fragment_carries_token_notes_and_reuse_conclusions(self):
        fragment = describe_only_mainline_fragment()
        self.assertIn(PROPOSAL_TOKEN, fragment)
        self.assertIn("row #10", fragment)
        self.assertIn("row #11", fragment)
        self.assertIn("New registration command: none", fragment)
        self.assertIn("dual-channel-bindings-run / dual-channel-run", fragment)
        self.assertIn(CHANNEL_CLI_DISABLED_BRIDGE_DIVERGENCE_ID, fragment)
        self.assertIn("bcs_e2e/channel_density/**", fragment)
        self.assertIn("remote_validation=NOT_RUN", fragment)

    def test_package_has_no_network_process_env_or_credential_value_literals(self):
        text = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE.glob("*.py"))
        for forbidden in ("urllib", "socket", "subprocess", "os.getenv", "os.environ",
                          "requests"):
            self.assertNotIn(forbidden, text, f"forbidden literal {forbidden!r} in package")
        for pattern in (r"Bearer\s", r"Cookie:\s", r"-----BEGIN"):
            self.assertIsNone(re.search(pattern, text), pattern)
        self.assertIn(HUMAN_COOKIE_ENV, text)
        self.assertIn(DRIVER_TOKEN_ENV, text)

    def test_no_credential_value_literals(self):
        patterns = (
            re.compile(r"Bearer\s+[A-Za-z0-9._-]{8,}"),
            re.compile(r"sk-[A-Za-z0-9]{16,}"),
            re.compile(r"(?i)(cookie|token|secret)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
        )
        for path in sorted(PACKAGE.glob("*.py")):
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if line.strip().startswith("PROPOSAL_TOKEN = "):
                    # narrow exemption: the task-mandated describe-only announcement token
                    # is an opaque provenance constant, not a credential; verify its exact
                    # value instead of waving the line through
                    self.assertIn(PROPOSAL_TOKEN, line)
                    continue
                for pattern in patterns:
                    self.assertIsNone(pattern.search(line),
                                      f"{path.name}:{line_no} tripped {pattern.pattern}")

    def test_line_budget_and_subpackage_shape(self):
        self.assertEqual({path.stem for path in PACKAGE.glob("*.py")},
                         {"__init__", "contract", "replay", "registration_proposal"})
        for path in PACKAGE.glob("*.py"):
            self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 1000,
                                 path.name)
        self.assertLessEqual(len(Path(__file__).read_text(encoding="utf-8").splitlines()), 1000)

    def test_source_freezes_match_the_legacy_contract_sources(self):
        http_json = json.loads(LEGACY_HTTP_JSON.read_text(encoding="utf-8"))
        cli_json = json.loads(LEGACY_CLI_JSON.read_text(encoding="utf-8"))
        self.assertEqual(http_json["sources"]["stories.sh"], HTTP_SOURCE_SHA256)
        self.assertEqual(cli_json["sources"]["cli-stories.sh"], CLI_SOURCE_SHA256)
        self.assertEqual(http_json["source_revision"], SOURCE_REVISION)
        self.assertEqual(cli_json["source_revision"], SOURCE_REVISION)


CLI_FIXTURE_HELPER_SPAN_LOW, CLI_FIXTURE_HELPER_SPAN_HIGH = 58, 69


if __name__ == "__main__":
    unittest.main()
