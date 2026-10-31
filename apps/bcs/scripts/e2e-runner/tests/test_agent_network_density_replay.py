"""honest offline replay projections (local/preprod tiers, injection cap) Split module of the agent-network density charter."""
from __future__ import annotations

from agent_network_density_test_support import *  # noqa: E402,F401,F403
from agent_network_density_test_support import (  # noqa: E402
    _assert_all, _unregistered_world)



class AgentNetworkDensityReplayTests(unittest.TestCase):
    def test_local_replay_mirrors_citations_and_frozen_records(self):
        receipt = project()
        plan = density_plan()
        self.assertEqual((receipt["status"], receipt["counts"],
                          receipt["parsed_obligation_count"],
                          receipt["parent_obligation_count"],
                          receipt["density_obligation_count"],
                          receipt["residual_count_gap"], receipt["delta"]),
                         (STATUS_NOT_RUN, {STATUS_PASS: 8, STATUS_FAIL: 0,
                                           STATUS_NOT_RUN: 3}, 11, 8, 3, 0, 3))
        self.assertEqual(
            [row["obligation_id"] for row in receipt["obligations"]],
            [row["id"] for row in plan["obligations"]])
        for receipt_row, plan_row in zip(receipt["obligations"],
                                         plan["obligations"]):
            self.assertEqual((receipt_row["source"]["file"],
                              receipt_row["source"]["line_start"],
                              receipt_row["source"]["line_end"],
                              receipt_row["source"]["quote"],
                              receipt_row["source"]["located"],
                              receipt_row["comparator"],
                              receipt_row["expected"]),
                             (plan_row["source_file"], plan_row["line_start"],
                              plan_row["line_end"], plan_row["quote"], True,
                              plan_row["comparator"], plan_row["expected"]))
        parents = [row for row in receipt["obligations"]
                   if row["level"] == "PARENT"]
        atoms = [row for row in receipt["obligations"]
                 if row["level"] == "DENSITY"]
        self.assertEqual((len(parents), len(atoms)), (8, 3))
        for row in parents:
            self.assertEqual((row["status"], row["error_type"],
                              row["observed"], row["evidence_event"],
                              row["method"]),
                             (STATUS_PASS, None, STATUS_PASS,
                              "committed_leg_citation_mirror", "story_stage"))
            _assert_all(self, ["committed leg citation mirrored",
                               "not re-executed"], row["classification"])
        fixture = mock_observations()
        self.assertEqual(fixture["parents"],
                         {parent: STATUS_PASS
                          for parent in plan["parent_ids"]})
        self.assertEqual(fixture["basis"], MOCK_BASIS)
        for row in atoms:
            self.assertEqual((row["status"], row["error_type"],
                              row["observed"], row["evidence_event"],
                              row["comparator"]),
                             (STATUS_NOT_RUN, None, row["expected"],
                              "upstream_record_mirrored_offline",
                              "verbatim_record"))
            _assert_all(self, ["not a local probe", "never projects PASS"],
                        row["classification"])

    def test_local_replay_receipt_faces(self):
        receipt = project()
        plan = density_plan()
        _assert_all(self, ["frozen upstream record mirrors",
                           "never projects PASS"], receipt["overall_basis"])
        family = receipt["family"]
        self.assertEqual((family["legacy_id"], family["dual_commands"],
                          family["read_case_keys"], family["source_case_id"],
                          family["source_suite"], family["local_executor"],
                          family["local_status"], family["canonical_status"],
                          family["completion_contract"],
                          family["remote_validation"],
                          family["grants_release_credit"]),
                         (PARENT, list(AN_OBL.DUAL_COMMANDS),
                          list(AN_OBL.READ_CASE_KEYS), CASE_ID, SUITE,
                          "bcs_e2e.local_network_run.local_network_run",
                          "LOCAL_NETWORK_PASS", "LOCAL_SUITE_PASS",
                          "owned-local-network-completion-v1", "NOT_RUN",
                          False))
        self.assertIn("static mirror", family["family"])
        self.assertEqual(family["report_contracts"], {
            "read": AN_OBL.READ_REPORT_CONTRACT,
            "register": AN_OBL.REGISTER_REPORT_CONTRACT,
            "status": AN_OBL.STATUS_REPORT_CONTRACT})
        _assert_all(self, ["rc=0 iff LOCAL_NETWORK_PASS", "rc=130 CANCELLED",
                           "PASS(r7) 8/8 leaves", "notes-only"],
                    family["rc_semantics"]
                    + family["committed_preprod_face"])
        self.assertEqual(receipt["executor_row"],
                         {"index": 1, "coverage_row_no": 2,
                          "dual_commands": list(AN_OBL.DUAL_COMMANDS)})
        source = receipt["source"]
        self.assertEqual((source["file"], source["sha256"],
                          source["story_line_span"],
                          source["companion_sources"]),
                         (AN_OBL.SOURCE_FILE, AN_OBL.SOURCE_SHA256, [255, 265],
                          {"actor.sh": AN_OBL.ACTOR_SHA256,
                           "common.sh": AN_OBL.COMMON_SHA256,
                           "e2e.sh": AN_OBL.RUNNER_SHA256}))
        adjudication = receipt["case_adjudication"]
        self.assertEqual((adjudication["case_id"], adjudication["parent_id"],
                          adjudication["status"],
                          adjudication["completed_count"],
                          adjudication["total_count"],
                          adjudication["error_type"],
                          adjudication["ready_to_execute"],
                          adjudication["mirror"]),
                         (CASE_ID, PARENT, STATUS_NOT_RUN, 8, 8, None,
                          False, True))
        _assert_all(self, ["owned-local-network-v1",
                           "expected_assertion_count 8"],
                    adjudication["provenance"])
        self.assertEqual([step["requirement_id"]
                          for step in adjudication["steps"]],
                         list(REQUIREMENTS))
        for step, site, charged in zip(adjudication["steps"], EXPECTED_SITES,
                                       EXPECTED_CHARGED):
            self.assertEqual((step["status"], step["mirror"],
                              step["assertion_site_count"],
                              step["upstream_parsed_records"],
                              step["remote_validation"],
                              step["grants_release_credit"]),
                             (STATUS_PASS, True, site, charged, "NOT_RUN",
                              False))
            _assert_all(self, ["committed r7 preprod PASS citation",
                               "PASS(r7)",
                               "unchanged by this notes-only density wave"],
                        step["provenance"] + step["preprod_verdict"])
            self.assertEqual(step["documented_gaps"],
                             list(AN_OBL.NAMED_PREPROD_GAPS))
        self.assertEqual((receipt["parsed_absorption"],
                          receipt["static_statement_catalog"],
                          receipt["emission_chain_disclosure"],
                          receipt["wave_scope_unreached_parents"],
                          receipt["not_charged_source_probes"],
                          receipt["upstream_roster"]),
                         (plan["parsed_absorption"],
                          plan["static_statement_catalog"],
                          plan["emission_chain_disclosure"],
                          plan["wave_scope_unreached_parents"],
                          list(AN_OBL.NOT_CHARGED_PROBES),
                          AN_OBL.UPSTREAM_ROSTER))
        self.assertIn("require_status guard", receipt["story_marker"]["note"])
        self.assertFalse(receipt["mock_child"]["present"])
        self.assertIn("index.umd.js", receipt["mock_child"]["note"])
        ledger = receipt["owned_local_ledger"]
        self.assertEqual((ledger["operations"], ledger["operation_count"],
                          ledger["fixture_bookends"],
                          ledger["business_operations"],
                          ledger["resource_disposition"],
                          ledger["expected_assertion_count"]),
                         (list(OWNED_LOCAL_LEDGER), 38, 10, 28,
                          "TEMPORARY_BOT_DELETED_FIXTURES_RETAINED", 8))
        self.assertEqual(len(set(OWNED_LOCAL_LEDGER)), 38)
        self.assertIn("panel asset digest probe", ledger["caliber"])
        self.assertEqual(len(ledger["mock_boundary_not_proven"]), 4)
        self.assertIn("remote authentication",
                      " ".join(ledger["mock_boundary_not_proven"]))
        gaps = receipt["named_preprod_gaps"]
        self.assertEqual([gap["id"] for gap in gaps],
                         list(AN_OBL.NAMED_PREPROD_GAPS))
        self.assertEqual(gaps, [{"id": gap, "status": "NAMED_DESIGN_GAP",
                                 "detail": NAMED_PREPROD_GAP_DETAILS[gap],
                                 "blocks": "preprod PASS credit"}
                                for gap in AN_OBL.NAMED_PREPROD_GAPS])
        self.assertIsNone(receipt["preprod_credit_cap"])
        self.assertEqual((receipt["execution"], receipt["environment_tier"],
                          receipt["preprod_observations_injected"],
                          receipt["remote_validation"],
                          receipt["grants_release_credit"],
                          receipt["credential_references"]),
                         (EXECUTION_LOCAL, "local", False, "NOT_RUN", False,
                          [HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV]))
        _assert_all(self, ["in-memory observations only",
                           "no network connection"],
                    receipt["transport_admission"])

    def test_missing_observation_is_not_run_and_never_defaults_to_pass(self):
        observations = mock_observations()
        observations["atoms"].pop("AN.F02")
        observations["parents"].pop("NETWORK.actor-search")
        receipt = project(observations)
        self.assertEqual((receipt["status"], receipt["counts"]),
                         (STATUS_NOT_RUN,
                          {STATUS_PASS: 7, STATUS_FAIL: 0, STATUS_NOT_RUN: 4}))
        by_id = {row["obligation_id"]: row for row in receipt["obligations"]}
        leaf = by_id["AN.F02"]
        self.assertEqual((leaf["status"], leaf["error_type"],
                          leaf["evidence_event"], leaf["observed"]),
                         (STATUS_NOT_RUN, OBSERVATION_ABSENT,
                          "observation_absent", None))
        self.assertIn("never projects PASS", leaf["classification"])
        parent = by_id["NETWORK.actor-search"]
        self.assertEqual((parent["status"], parent["error_type"],
                          parent["evidence_event"], parent["observed"]),
                         (STATUS_NOT_RUN, OBSERVATION_ABSENT,
                          "citation_absent", None))
        self.assertIn("never defaults to PASS", parent["classification"])
        self.assertEqual(receipt["case_adjudication"]["completed_count"], 7)
        self.assertEqual(receipt["case_adjudication"]["status"],
                         STATUS_NOT_RUN)
        self.assertIsNone(receipt["case_adjudication"]["error_type"])
        self.assertFalse(receipt["grants_release_credit"])

    def test_shifted_atom_observation_fail_closes(self):
        observations = mock_observations()
        observations["atoms"]["AN.F01"] = "advertised manifest asset drifted"
        receipt = project(observations)
        self.assertEqual((receipt["status"], receipt["counts"]),
                         (STATUS_FAIL, {STATUS_PASS: 8, STATUS_FAIL: 1,
                                        STATUS_NOT_RUN: 2}))
        leaf = {row["obligation_id"]: row
                for row in receipt["obligations"]}["AN.F01"]
        self.assertEqual((leaf["status"], leaf["error_type"],
                          leaf["evidence_event"], leaf["observed"]),
                         (STATUS_FAIL, OBSERVATION_MISMATCH,
                          "observation_mismatch",
                          "advertised manifest asset drifted"))
        _assert_all(self, ["fail-close", "never normalized to a PASS"],
                    leaf["classification"])
        adjudication = receipt["case_adjudication"]
        self.assertEqual((adjudication["status"], adjudication["error_type"],
                          adjudication["completed_count"],
                          adjudication["ready_to_execute"]),
                         (STATUS_FAIL, ADJUDICATION_MISMATCH, 8, False))
        self.assertFalse(receipt["grants_release_credit"])

    def test_shifted_parent_citation_fail_closes(self):
        observations = mock_observations()
        observations["parents"][PLATFORM] = "FAIL"
        receipt = project(observations)
        self.assertEqual((receipt["status"], receipt["counts"]),
                         (STATUS_FAIL, {STATUS_PASS: 7, STATUS_FAIL: 1,
                                        STATUS_NOT_RUN: 3}))
        parent = {row["obligation_id"]: row
                  for row in receipt["obligations"]}[PLATFORM]
        self.assertEqual((parent["status"], parent["error_type"],
                          parent["evidence_event"], parent["observed"]),
                         (STATUS_FAIL, OBSERVATION_MISMATCH,
                          "observation_mismatch", "FAIL"))
        self.assertIn("fail-close", parent["classification"])
        self.assertEqual(receipt["case_adjudication"]["status"], STATUS_FAIL)
        self.assertEqual(receipt["case_adjudication"]["error_type"],
                         ADJUDICATION_MISMATCH)

    def test_preprod_default_is_fully_not_run_with_named_gaps(self):
        receipt = project(tier="preprod")
        self.assertEqual((receipt["status"], receipt["counts"],
                          receipt["environment_tier"],
                          receipt["preprod_observations_injected"],
                          receipt["execution"]),
                         (STATUS_NOT_RUN, {STATUS_PASS: 0, STATUS_FAIL: 0,
                                           STATUS_NOT_RUN: 11}, "preprod",
                          False, EXECUTION_PREPROD))
        for row in receipt["obligations"]:
            self.assertEqual((row["status"], row["error_type"],
                              row["evidence_event"]),
                             (STATUS_NOT_RUN, PREPROD_EXECUTION_NOT_RUN,
                              "preprod_story_not_executed_offline"))
            _assert_all(self, ["NOT_RUN", "named PREPROD_* gaps"],
                        row["classification"])
        adjudication = receipt["case_adjudication"]
        self.assertEqual((adjudication["status"],
                          adjudication["completed_count"],
                          adjudication["total_count"],
                          adjudication["ready_to_execute"]),
                         (STATUS_NOT_RUN, 0, 8, False))
        for step in adjudication["steps"]:
            self.assertEqual((step["status"], step["documented_gaps"]),
                             (STATUS_NOT_RUN,
                              list(AN_OBL.NAMED_PREPROD_GAPS)))
        gaps = receipt["named_preprod_gaps"]
        self.assertEqual([gap["id"] for gap in gaps],
                         list(AN_OBL.NAMED_PREPROD_GAPS))
        for gap in gaps:
            self.assertEqual(gap["status"], "NAMED_DESIGN_GAP")
        _assert_all(self, ["never converts a row into preprod PASS credit",
                           "stays untouched"], receipt["preprod_credit_cap"])
        self.assertEqual((receipt["remote_validation"],
                          receipt["grants_release_credit"]),
                         ("NOT_RUN", False))

    def test_preprod_injection_is_row_mirrored_but_capped(self):
        receipt = project(mock_observations(), tier="preprod")
        self.assertEqual((receipt["status"], receipt["counts"],
                          receipt["preprod_observations_injected"],
                          receipt["execution"]),
                         (STATUS_NOT_RUN, {STATUS_PASS: 8, STATUS_FAIL: 0,
                                           STATUS_NOT_RUN: 3}, True,
                          EXECUTION_INJECTED))
        for row in receipt["obligations"]:
            if row["level"] == "PARENT":
                self.assertEqual(row["status"], STATUS_PASS)
                _assert_all(self, ["authorized injected citation",
                                   "never becomes preprod PASS credit"],
                            row["classification"])
            else:
                self.assertEqual((row["status"], row["evidence_event"]),
                                 (STATUS_NOT_RUN,
                                  "upstream_record_mirrored_offline"))
        self.assertIn("never converts a row into preprod PASS credit",
                      receipt["preprod_credit_cap"])
        self.assertEqual((receipt["case_adjudication"]["status"],
                          receipt["case_adjudication"]["completed_count"]),
                         (STATUS_NOT_RUN, 8))
        self.assertFalse(receipt["grants_release_credit"])

    def test_preprod_injection_drift_fail_closes_capped(self):
        observations = mock_observations()
        observations["atoms"]["AN.F03"] = "drifted stop warning text"
        receipt = project(observations, tier="preprod")
        leaf = {row["obligation_id"]: row
                for row in receipt["obligations"]}["AN.F03"]
        self.assertEqual((leaf["status"], leaf["error_type"]),
                         (STATUS_FAIL, OBSERVATION_MISMATCH))
        self.assertEqual((receipt["status"], receipt["counts"],
                          receipt["preprod_observations_injected"]),
                         (STATUS_FAIL, {STATUS_PASS: 8, STATUS_FAIL: 1,
                                        STATUS_NOT_RUN: 2}, True))
        self.assertIsNotNone(receipt["preprod_credit_cap"])
        self.assertEqual((receipt["case_adjudication"]["status"],
                          receipt["case_adjudication"]["error_type"]),
                         (STATUS_FAIL, ADJUDICATION_MISMATCH))

    def test_replay_fail_close_shapes(self):
        with self.assertRaisesRegex(RunStateError,
                                    "tier must be local or preprod"):
            project(tier="stage")
        with self.assertRaisesRegex(RunStateError,
                                    "observations must be an object"):
            project(["not", "a", "dict"])
        for observations in ({"parents": [], "atoms": {}},
                             {"parents": {}, "atoms": "dense"},
                             {"parents": {}, "atoms": {}, "basis": 5}):
            with self.assertRaisesRegex(RunStateError,
                                        "observation layers must be objects"):
                project(observations)

    def test_family_stays_outside_the_dynamic_registry(self):
        with self.assertRaisesRegex(ValueError,
                                    "unknown dynamic family: agent_network"):
            dynamic_family_contract("agent_network")
        registered = [contract.family for contract in dynamic_families()]
        self.assertEqual((len(registered), set(registered)),
                         (4, {"fixed_loop", "nested_fixed_loop",
                              "result_queue", "draft_group"}))
        self.assertNotIn("agent_network", registered)
        replay_source = Path(AN_REPLAY.__file__).read_text(encoding="utf-8")
        self.assertNotIn("dynamic_family_contract", replay_source)
        plan = density_plan()
        self.assertFalse(plan["family_registration"]["registered"])
        self.assertIn("density replay mirrors",
                      plan["family_registration"]["note"])
        with _unregistered_world():
            proposal = registration_proposal()
        family = proposal["family"]
        self.assertFalse(family["registered_in_dynamic_family_map"])
        self.assertEqual(family["registered_triads"],
                         ["fixed_loop", "nested_fixed_loop",
                          "result_queue", "draft_group"])
        self.assertEqual((family["legacy_id"], family["source_case_id"],
                          family["source_suite"]), (PARENT, CASE_ID, SUITE))
        self.assertIn("static mirror", family["family"])
        self.assertTrue(callable(local_network_run))

    def test_owned_local_ledger_is_mirrored_not_executed(self):
        self.assertEqual((len(OWNED_LOCAL_LEDGER),
                          len(set(OWNED_LOCAL_LEDGER))), (38, 38))
        self.assertEqual(OWNED_LOCAL_LEDGER[:4], [
            "initial-fixture-ceo", "initial-fixture-pm",
            "initial-fixture-eng", "initial-fixture-qa"])
        self.assertTrue(OWNED_LOCAL_LEDGER[-1].startswith("final-bots-my"))
        self.assertEqual((AN_REPLAY.EXECUTION_LOCAL,
                          AN_REPLAY.EXECUTION_PREPROD,
                          AN_REPLAY.EXECUTION_INJECTED),
                         ("owned-local-offline-mirror", "preprod-not-run",
                          "preprod-injected-observation-projection"))
        _assert_all(self, ["no owned runtime, no story child, no CLI, "
                           "no network", "no credential", "LOCAL-NETWORK-01"],
                    MOCK_BASIS)


