"""describe-only registration arithmetic, inventory/coverage closures Split module of the agent-network density charter."""
from __future__ import annotations

from agent_network_density_test_support import *  # noqa: E402,F401,F403
from agent_network_density_test_support import (  # noqa: E402
    _assert_all, _unregistered_world, _BEARER)



class AgentNetworkDensityRegistrationProposalTests(unittest.TestCase):
    def test_live_world_proposal_face_is_honest_in_both_states(self):
        if NOTE in inventory_module.EXECUTOR_MIGRATION_NOTES:
            with self.assertRaises(RunStateError):
                registration_proposal()
        else:
            proposal = registration_proposal()
            self.assertEqual((proposal["status"], proposal["token"]),
                             ("DESCRIBE_ONLY", PROPOSAL_TOKEN))

    def test_proposal_agrees_with_frozen_faces_and_caliber(self):
        with _unregistered_world():
            proposal = registration_proposal()
        self.assertEqual((proposal["status"], proposal["token"],
                          proposal["contract"], proposal["parent_story"],
                          proposal["upstream_issue"],
                          proposal["executor_row_index"],
                          proposal["coverage_row_no"],
                          proposal["parsed_obligation_count"],
                          proposal["parent_obligation_count"],
                          proposal["density_obligation_count"],
                          proposal["residual_count_gap"], proposal["delta"]),
                         ("DESCRIBE_ONLY", PROPOSAL_TOKEN, DENSITY_CONTRACT,
                          PARENT, None, 1, 2, 11, 8, 3, 0, 3))
        self.assertEqual(PROPOSAL_TOKEN,
                         "AGENT_NETWORK_DENSITY_3_OBLIGATIONS_20261031")
        _assert_all(self, ["executor row idx=1", "coverage-map row no=2"],
                    proposal["upstream_issue_basis"])
        source = proposal["source"]
        self.assertEqual((source["file"], source["sha256"],
                          source["contract_sources"],
                          source["companion_sources"],
                          source["emission_chain_sources"],
                          source["story_line_span"], source["function_span"],
                          source["migration_contract"],
                          source["contract_source_revision"]),
                         (AN_OBL.SOURCE_FILE, AN_OBL.SOURCE_SHA256,
                          {"stories.sh": AN_OBL.SOURCE_SHA256,
                           "actor.sh": AN_OBL.ACTOR_SHA256},
                          {"actor.sh": AN_OBL.ACTOR_SHA256,
                           "common.sh": AN_OBL.COMMON_SHA256,
                           "e2e.sh": AN_OBL.RUNNER_SHA256},
                          {"common.sh": AN_OBL.COMMON_FILE,
                           "e2e.sh": AN_OBL.RUNNER_FILE},
                          [255, 265],
                          {"platform": [267, 322], "register": [324, 373]},
                          "network-migration-contract.json",
                          AN_OBL.CONTRACT_REVISION))
        self.assertTrue(re.fullmatch("[0-9a-f]{40}",
                                     source["contract_source_revision"]))
        self.assertEqual(source["contract_source_revision"],
                         json.loads(CONTRACT_DISK.read_text(
                             encoding="utf-8"))["source_revision"])
        family = proposal["family"]
        self.assertIn("static mirror", family["family"])
        self.assertFalse(family["registered_in_dynamic_family_map"])
        self.assertEqual(family["registered_triads"], [
            "fixed_loop", "nested_fixed_loop", "result_queue", "draft_group"])
        self.assertEqual((family["dual_commands"], family["read_case_keys"],
                          family["legacy_id"], family["source_case_id"],
                          family["source_suite"], family["local_executor"],
                          family["local_status"], family["canonical_status"],
                          family["completion_contract"]),
                         (list(AN_OBL.DUAL_COMMANDS),
                          list(AN_OBL.READ_CASE_KEYS), PARENT, CASE_ID, SUITE,
                          "bcs_e2e.local_network_run.local_network_run",
                          "LOCAL_NETWORK_PASS", "LOCAL_SUITE_PASS",
                          "owned-local-network-completion-v1"))
        _assert_all(self, ["PASS(r7) 8/8 leaves", "notes-only"],
                    family["committed_preprod_face"])
        self.assertEqual(proposal["parsed_absorption"],
                         density_plan()["parsed_absorption"])
        self.assertEqual((proposal["static_totals"],
                          proposal["wave_scope_unreached_parents"]),
                         (AN_OBL.STATIC_TOTALS,
                          AN_OBL.WAVE_SCOPE_UNREACHED_PARENTS))
        self.assertEqual(proposal["emission_chain_disclosure"],
                         density_plan()["emission_chain_disclosure"])
        gaps = proposal["named_preprod_gaps"]
        self.assertEqual(gaps, [{"id": gap, "status": "NAMED_DESIGN_GAP",
                                 "detail": NAMED_PREPROD_GAP_DETAILS[gap],
                                 "blocks": "preprod PASS credit"}
                                for gap in AN_OBL.NAMED_PREPROD_GAPS])
        replay_caliber = proposal["replay_caliber"]
        _assert_all(self, ["8/8 parent leg citations mirrored PASS",
                           "NOT_RUN by design", "never mints PASS"],
                    replay_caliber["local"])
        _assert_all(self, ["11/11 NOT_RUN", "capped at NOT_RUN overall"],
                    replay_caliber["preprod"])
        self.assertEqual(len(proposal["red_lines"]), 5)
        _assert_all(self, ["executor-migration.json", "inventory.py",
                           "coverage-map", "SHA-256"],
                    " ".join(proposal["red_lines"]))
        self.assertFalse(proposal["new_registration_command"])
        self.assertEqual(proposal["write_domain"], [
            "bcs_e2e/agent_network_density/**",
            "tests/test_agent_network_density.py",
            "tests/test_agent_network_density_contract.py",
            "tests/test_agent_network_density_replay.py",
            "tests/test_agent_network_density_registration_proposal.py",
            "tests/test_agent_network_density_hygiene.py",
            "tests/agent_network_density_test_support.py"])
        self.assertEqual((proposal["remote_validation"],
                          proposal["grants_release_credit"],
                          proposal["credential_references"]),
                         ("NOT_RUN", False,
                          [HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV]))
        totals = proposal["committed_totals"]
        self.assertEqual((totals["obligations_committed_sum"],
                          totals["upstream_parsed_known_sum"]), (674, 638))
        self.assertIn("unchanged", totals["disposition"])

    def test_proposal_fail_close_already_registered(self):
        registered_notes = frozenset(
            inventory_module.EXECUTOR_MIGRATION_NOTES) | {NOTE}
        registered_counts = dict(
            inventory_module._EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS)
        registered_counts[NOTE] = 1
        with _unregistered_world(), \
                mock.patch.object(AN_PROPOSAL, "EXECUTOR_MIGRATION_NOTES",
                                  registered_notes), \
                mock.patch.object(
                    AN_PROPOSAL, "_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS",
                    registered_counts):
            with self.assertRaisesRegex(RunStateError, "already registered"):
                registration_proposal()

    def test_proposal_fail_close_face_changed(self):
        statuses = inventory_module.load_executor_migration()
        registered_face = dict(EXPECTED_ROW_FACE, notes=[NOTE])
        snapshot = {story: (registered_face if story == AN_OBL.STORY_FUNCTION
                            else dict(face))
                    for story, face in statuses.items()}
        with _unregistered_world(), \
                mock.patch.object(AN_PROPOSAL, "load_executor_migration",
                                  return_value=snapshot):
            with self.assertRaisesRegex(RunStateError, "face changed"):
                registration_proposal()

    def test_proposal_fail_close_note_totals_inconsistent(self):
        live_counts = dict(
            inventory_module._EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS)
        inflated_key = next(iter(live_counts))
        inconsistent = dict(live_counts)
        inconsistent[inflated_key] += 1
        with _unregistered_world(), \
                mock.patch.object(
                    AN_PROPOSAL, "_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS",
                    inconsistent):
            with self.assertRaisesRegex(RunStateError,
                                        "note totals are inconsistent"):
                registration_proposal()

    def test_coverage_overlay_arithmetic_matches_disk_before_and_after(self):
        coverage_map = json.loads(COVERAGE_MAP_DISK.read_text(encoding="utf-8"))
        disk_row = next(row for row in coverage_map["rows"]
                        if row["no"] == AN_OBL.COVERAGE_ROW_NO)
        self.assertEqual(disk_row["case_id"], PARENT)
        with _unregistered_world():
            proposal = registration_proposal()
        row = proposal["coverage_map_increment"]["row"]
        self.assertEqual((row["no"], row["case_id"], row["before"]),
                         (AN_OBL.COVERAGE_ROW_NO, PARENT, disk_row))
        before = row["before"]
        local_before = before["local_projection"]["obligations"]
        gap_before = before["gap"]["parsed_minus_obligations"]
        if local_before == 8:
            self.assertEqual(before["upstream"]["snapshot_parsed_count"], 11)
            self.assertEqual(gap_before, 3)
            self.assertEqual(before["local_projection"]["leaves"],
                             "PASS 8/8 叶")
            self.assertIn("contract:network-migration-contract.json",
                          before["local_projection"]["obligations_basis"])
            self.assertEqual(before["queue"],
                             {"rank": 26, "gap_qty": 3, "feasibility": 3,
                              "score": 9, "basis": "密度 delta=3×3；零物料"})
        else:
            self.assertEqual((local_before, gap_before), (11, 0))
            self.assertEqual((before["queue"]["gap_qty"],
                              before["queue"]["score"]), (11, 33))
        after = row["after"]
        self.assertEqual((after["obligations"], after["snapshot_parsed_count"],
                          after["gap_parsed_minus_obligations"],
                          after["gap_kind"]),
                         (11, 11, 0, "COUNT_GAP_CLOSED+EXECUTION_GAP"))
        self.assertEqual(after["obligations"], local_before + 3)
        _assert_all(self, ["parsed 11 − 义务 11 = 0", PROPOSAL_TOKEN,
                           "AN.F01..AN.F03", "stories.sh:295"],
                    after["gap_detail"])
        self.assertEqual((after["queue"]["rank"], after["queue"]["gap_qty"],
                          after["queue"]["feasibility"],
                          after["queue"]["score"]), (26, 11, 3, 33))
        self.assertEqual(after["queue"]["score"],
                         after["queue"]["gap_qty"]
                         * after["queue"]["feasibility"])
        self.assertEqual(after["queue"]["basis"], QUEUE_BASIS)
        _assert_all(self, ["执行缺口按密度口径 11",
                           "密度 delta=3×feasibility 3", "零新物料"], QUEUE_BASIS)
        self.assertEqual(proposal["coverage_map_increment"]["targets"], [
            "coverage-map/coverage-map-20261031.json",
            "coverage-map/coverage-map-20261031.md"])
        totals = proposal["committed_totals"]
        self.assertEqual((totals["obligations_committed_sum"],
                          totals["upstream_parsed_known_sum"]),
                         (coverage_map["totals"]["obligations_committed_sum"],
                          coverage_map["totals"]["upstream_parsed_known_sum"]))
        self.assertIn("unchanged", totals["disposition"])
        self.assertEqual(proposal["upstream_roster"],
                         density_plan()["upstream_roster"])
        _assert_all(self, ["| 2 |", "`story_user_prepares_agent_network`",
                           "全绿†", "parsed 11", "PASS 8/8 父镜像", "obl 11",
                           "NOT_RUN 0/3", PROPOSAL_TOKEN, "密度闭合", "⏳P5"],
                    MD_ROW)

    def test_inventory_increment_is_notes_only_with_closure(self):
        with _unregistered_world():
            proposal = registration_proposal()
        increment = proposal["inventory_increment"]
        self.assertEqual((increment["target_file"], increment["operation"]),
                         ("bcs_e2e/inventory.py",
                          "fail-close membership and counter closure"))
        self.assertIn("owner session applies", increment["integration_mode"])
        self.assertEqual(increment["adds_to_EXECUTOR_MIGRATION_NOTES"], [NOTE])
        self.assertEqual(increment["adds_to_expected_note_counts"], {NOTE: 1})
        anchors = increment["anchors"]
        self.assertIn("EXECUTOR_MIGRATION_NOTES frozenset",
                      anchors["notes_membership"])
        self.assertIn("_EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS",
                      anchors["note_counts"])
        self.assertIn("state and gate counts stay untouched",
                      anchors["unchanged_counters"])
        # The note-count total is a live registry figure, not a frozen
        # caliber: waves #9/#35/#36 landed legitimate notes after this
        # charter snapshot, so the replacement arithmetic is pinned
        # live-derived (the landed result_queue/draft_group precedent)
        # instead of a stale frozen literal.
        replacement = increment["note_counts_replacement"]
        live_counts = inventory_module._EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS
        unregistered_total = sum(value for key, value in live_counts.items()
                                 if key != NOTE)
        self.assertEqual(replacement,
                         {"current_total_notes": unregistered_total,
                          "after_total_notes": unregistered_total + 1,
                          "this_family_delta": 1})
        unchanged = increment["unchanged"]
        expected_states = {
            f"{mode}/{verdict}": count
            for (mode, verdict), count
            in inventory_module._EXPECTED_EXECUTOR_MIGRATION_COUNTS.items()}
        self.assertEqual(unchanged["state_counts"], expected_states)
        self.assertEqual(unchanged["gate_counts"], {"MOCK": 2})
        self.assertEqual(Counter(
            inventory_module._EXPECTED_EXECUTOR_MIGRATION_GATE_COUNTS),
            Counter(unchanged["gate_counts"]))
        self.assertEqual(unchanged["row_face"],
                         {"id": PARENT, "mode": "DUAL",
                          "preprod_verdict": "PASS", "gates": [],
                          "notes_delta": "+1 (this proposal note only)"})

    def test_note_md_row_and_fragment_carry_frozen_anchors(self):
        self.assertGreater(len(NOTE), 800)
        _assert_all(self, [
            "row #1 story_user_prepares_agent_network",
            "register 3 source-anchored density obligations",
            "retained NETWORK.* parent envelope",
            "11 parsed = 8 legacy parents + 3 non-PASS snapshot atoms",
            "residual_count_gap=0", "AN.F01..AN.F03", "stories.sh:295",
            "stories.sh:37", "e2e.sh:210", "assert_status 104-107",
            "assert_eq 68", "fail print 74", "require_status failure-branch",
            "runner stopped warn",
            "33 unreached actor/register static statements",
            "11 unreached platform statements", "no fabricated leaves",
            "notes-only integration",
            "dual-network-read-run/register-run/status-run", "DUAL/PASS",
            "notes 0 to 1", "three named PREPROD_* gaps",
            "remote_validation=NOT_RUN", "grants_release_credit=false"], NOTE)
        self.assertNotIn(_BEARER, NOTE)
        with _unregistered_world():
            fragment_text = describe_only_mainline_fragment()
        _assert_all(self, [
            PROPOSAL_TOKEN, "row #1", AN_OBL.STORY_FUNCTION,
            "11 parsed = 8 legacy parents + 3 non-PASS snapshot atoms",
            "residual_count_gap=0", "AN.F01..AN.F03", "common.sh:74",
            "stories.sh:37", "e2e.sh:210",
            "33 unreached actor/register static statements",
            "11 unreached platform statements", "obligations 8 to 11",
            "COUNT_GAP 3 to 0", "queue gap_qty 3 to 11", "score 9 to 33",
            "committed totals 674/638 stay unchanged",
            "8 committed leg PASS citations", "3 frozen record mirrors",
            "offline overall never PASS", "no new registration command",
            "six read case keys", "dual-network-register-run",
            "dual-network-status-run", "state and gate counters unchanged",
            "one EXECUTOR_MIGRATION_NOTES member",
            "one _EXPECTED_EXECUTOR_MIGRATION_NOTE_COUNTS entry",
            "committed 全绿† roster face is cited with null parsed-greenness",
            "bcs_e2e/agent_network_density/**",
            "tests/test_agent_network_density.py",
            "remote_validation=NOT_RUN", "grants_release_credit=false",
            "executor row idx=1", "coverage-map row no=2"], fragment_text)
        for gap_id in AN_OBL.NAMED_PREPROD_GAPS:
            self.assertIn(gap_id, fragment_text)
        self.assertGreater(len(fragment_text), len(NOTE))

    def test_describe_only_texts_carry_no_credential_literals(self):
        with _unregistered_world():
            fragment_text = describe_only_mainline_fragment()
        for pattern in (_BEARER + r"\w", "Cookie:" + r"\s", "-----BEGIN"):
            for text in (NOTE, MD_ROW, fragment_text):
                self.assertIsNone(re.search(pattern, text), pattern)
        self.assertNotIn("Bearer", NOTE)
        self.assertNotIn("curl ", MD_ROW)


