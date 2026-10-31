"""contract anchoring, frozen spans, static catalogs, drift fail-close batteries Split module of the agent-network density charter."""
from __future__ import annotations

from agent_network_density_test_support import *  # noqa: E402,F401,F403
from agent_network_density_test_support import (  # noqa: E402
    _assert_all, _disk_lines, _disk_text, _sha256, _unregistered_world)



class AgentNetworkDensityContractTests(unittest.TestCase):
    def test_accounting_is_8_parents_plus_3_atoms_with_zero_residual(self):
        rows = obligations()
        plan = density_plan()
        parents = [row for row in rows if row.level == "PARENT"]
        leaves = [row for row in rows if row.level == "DENSITY"]
        self.assertEqual((len(rows), PARSED_OBLIGATION_COUNT, len(parents),
                          len(leaves), PARENT_OBLIGATION_COUNT,
                          DENSITY_OBLIGATION_COUNT, RESIDUAL_COUNT_GAP),
                         (11, 11, 8, 3, 8, 3, 0))
        self.assertEqual(PARSED_OBLIGATION_COUNT - PARENT_OBLIGATION_COUNT,
                         DENSITY_OBLIGATION_COUNT)
        self.assertEqual(len({row.obligation_id for row in rows}), 11)
        self.assertEqual([row.obligation_id for row in parents],
                         list(REQUIREMENTS))
        self.assertEqual([row.obligation_id for row in leaves],
                         ["AN.F01", "AN.F02", "AN.F03"])
        self.assertEqual((plan["parent_obligation_count"],
                          plan["density_obligation_count"],
                          plan["parsed_obligation_count"],
                          plan["residual_count_gap"], plan["delta"],
                          plan["parsed_unlocated_count"]), (8, 3, 11, 0, 3, 0))
        self.assertEqual((plan["parsed_unlocated"], plan["parent_ids"],
                          plan["leaf_ids"]), ([], list(REQUIREMENTS),
                                              ["AN.F01", "AN.F02", "AN.F03"]))

    def test_parent_rows_match_the_frozen_network_contract(self):
        rows = {row.obligation_id: row for row in obligations()}
        binding = network_source_binding()
        self.assertEqual((binding["parent"], binding["source_revision"],
                          binding["sources"]),
                         (PARENT, AN_OBL.CONTRACT_REVISION,
                          {"stories.sh": AN_OBL.SOURCE_SHA256,
                           "actor.sh": AN_OBL.ACTOR_SHA256}))
        contract = json.loads(CONTRACT_DISK.read_text(encoding="utf-8"))
        self.assertEqual((contract["schema_version"], contract["parent"],
                          contract["source_revision"], contract["sources"]),
                         (1, PARENT, AN_OBL.CONTRACT_REVISION,
                          binding["sources"]))
        self.assertTrue(re.fullmatch("[0-9a-f]{40}",
                                     contract["source_revision"]))
        self.assertEqual([row["id"] for row in contract["requirements"]],
                         list(REQUIREMENTS))
        self.assertEqual([row["function"] for row in contract["requirements"]],
                         list(FUNCTIONS))
        by_id = {row["id"]: row for row in contract["requirements"]}
        for obligation_id in REQUIREMENTS:
            row = rows[obligation_id]
            face = by_id[obligation_id]
            self.assertEqual((row.level, row.method, row.field,
                              row.comparator, row.slot, row.parent_obligation,
                              row.executor_site_id, row.located),
                             ("PARENT", "story_stage", "stage", "stage",
                              "stage", obligation_id, None, True))
            self.assertEqual((row.expectation, row.story_label),
                             (face["expectation"], face["function"]))
            self.assertEqual((row.line_start, row.line_end),
                             AN_OBL.EXPECTED_FUNCTION_SPANS[face["function"]])
            self.assertEqual(row.source_file,
                             AN_OBL.SOURCE_FILE
                             if face["source"] == "stories.sh"
                             else AN_OBL.ACTOR_FILE)
            lines = _disk_lines(face["source"])
            self.assertEqual(
                row.quote, "\n".join(lines[row.line_start - 1:row.line_end]))
            self.assertTrue(row.quote)
        self.assertEqual((_sha256(STORY_DISK), _sha256(ACTOR_DISK)),
                         (AN_OBL.SOURCE_SHA256, AN_OBL.ACTOR_SHA256))

    def test_density_leaves_anchor_non_pass_records_verbatim(self):
        rows = {row.obligation_id: row for row in obligations()}
        stories_lines = _disk_lines("stories.sh")
        runner_lines = _disk_lines("e2e.sh")
        for leaf_id, line, source_file, method, field, record_index, kind, \
                trigger in LEAF_TABLE:
            row = rows[leaf_id]
            self.assertEqual(
                (row.obligation_id, row.level, row.method, row.field,
                 row.line_start, row.line_end, row.trigger_line,
                 row.snapshot_index, row.snapshot_kind, row.source_file,
                 row.parent_obligation, row.comparator, row.slot),
                (leaf_id, "DENSITY", method, field, line, line, trigger,
                 record_index, kind, source_file, PLATFORM,
                 "verbatim_record", "upstream_record"))
            self.assertEqual(row.operation, row.emission_chain)
            self.assertEqual((row.expected, row.snapshot_text),
                             (AN_OBL.PARSED_SNAPSHOT[record_index - 1][2],) * 2)
            self.assertEqual(row.quote, AN_OBL.EXPECTED_LEAF_QUOTES[leaf_id])
            disk = (stories_lines if source_file == AN_OBL.SOURCE_FILE
                    else runner_lines)
            self.assertEqual(disk[line - 1], row.quote)
            self.assertIn(row.quote, _disk_text(Path(source_file).name))
            record = AN_OBL.PARSED_SNAPSHOT[record_index - 1]
            self.assertEqual((record[0], record[1], record[2], record[3],
                              record[4]),
                             (record_index, kind, row.snapshot_text,
                              Path(source_file).name, line))
        kinds = [record[1] for record in AN_OBL.PARSED_SNAPSHOT]
        self.assertEqual(kinds, ["PASS"] * 8 + ["FAIL", "WARN", "WARN"])
        self.assertIn("advertised manifest asset is downloadable",
                      AN_OBL.PARSED_SNAPSHOT[8][2])
        self.assertIn("expected='200', actual='404'",
                      AN_OBL.PARSED_SNAPSHOT[8][2])
        self.assertEqual(AN_OBL.PARSED_SNAPSHOT[9][2],
                         "response: asset not found")
        self.assertEqual(AN_OBL.PARSED_SNAPSHOT[10][2],
                         AN_OBL.STORY_FUNCTION
                         + " stopped after a failed assertion")
        self.assertEqual([record[4] for record in AN_OBL.PARSED_SNAPSHOT
                          if record[1] == "PASS"], LEG_PASS_LINES)
        for record_index, line in enumerate(LEG_PASS_LINES, start=1):
            self.assertIn(AN_OBL.PARSED_SNAPSHOT[record_index - 1][2],
                          stories_lines[line - 1])
        platform = AN_OBL.PLATFORM_SPAN
        self.assertTrue(platform[0] <= rows["AN.F01"].line_start <= platform[1])
        require_def = AN_OBL.REQUIRE_STATUS_DEF_SPAN
        self.assertTrue(require_def[0] <= rows["AN.F02"].line_start
                        <= require_def[1])
        chains = (AN_OBL.EMISSION_CHAIN_FAIL_PRINT,
                  AN_OBL.EMISSION_CHAIN_RESPONSE_WARN,
                  AN_OBL.EMISSION_CHAIN_RUNNER_WARN)
        for chain in chains:
            self.assertIn("stories.sh:295", chain)
        _assert_all(self, ["common.sh:104-107", "common.sh:68",
                           "common.sh:74"], chains[0])
        self.assertIn("stories.sh:33-40", chains[1])
        _assert_all(self, ["e2e.sh:202-211", "e2e.sh:210"], chains[2])

    def test_story_span_call_sites_catalogs_and_frozen_shas(self):
        story_lines = _disk_lines("stories.sh")
        self.assertIn(AN_OBL.STORY_FUNCTION + "() {", story_lines[254])
        self.assertEqual(story_lines[264], "}")
        body = "\n".join(story_lines[255:264])
        calls = re.findall(
            r"^    ((?:test_|_story_)[A-Za-z0-9_]+)(?: \|\| return)?$", body,
            re.M)
        self.assertEqual(calls, list(FUNCTIONS))
        story_spans = AN_OBL._function_spans(_disk_text("stories.sh"))
        actor_spans = AN_OBL._function_spans(_disk_text("actor.sh"))
        for name, span in AN_OBL.EXPECTED_FUNCTION_SPANS.items():
            source = (story_spans if name in (AN_OBL.PLATFORM_FUNCTION,
                                              AN_OBL.REGISTER_FUNCTION)
                      else actor_spans)
            self.assertEqual(source[name], span, name)
        self.assertEqual(story_spans["require_status"],
                         AN_OBL.REQUIRE_STATUS_DEF_SPAN)
        catalogs = AN_OBL._static_catalogs(_disk_text("stories.sh"),
                                           _disk_text("actor.sh"))
        self.assertEqual(catalogs, AN_OBL.STATIC_CATALOG_EXPECTED)
        for source_name in ("stories.sh", "actor.sh"):
            for function, catalog in catalogs[source_name].items():
                self.assertEqual(
                    catalog["total"],
                    sum(value for key, value in catalog.items()
                        if key != "total"), function)
        self.assertEqual(catalogs["totals"], AN_OBL.STATIC_TOTALS)
        self.assertEqual(AN_OBL.STATIC_TOTALS["full story surface"], 53)
        self.assertEqual((_sha256(STORY_DISK), _sha256(ACTOR_DISK),
                          _sha256(COMMON_DISK), _sha256(RUNNER_DISK)),
                         (AN_OBL.SOURCE_SHA256, AN_OBL.ACTOR_SHA256,
                          AN_OBL.COMMON_SHA256, AN_OBL.RUNNER_SHA256))
        provenance = AN_OBL.SNAPSHOT_PROVENANCE
        self.assertEqual((provenance["records"], provenance["case_name"]),
                         (11, AN_OBL.STORY_FUNCTION))
        self.assertIn("poolab", provenance["artifact"])
        self.assertTrue(re.fullmatch("[0-9a-f]{64}",
                                     provenance["artifact_sha256"]))
        self.assertIn("coverage-map row no=2",
                      provenance["committed_cross_check"])
        self.assertEqual(AN_OBL.UPSTREAM_ROSTER["roster_verdict"], "全绿†")
        self.assertIsNone(AN_OBL.UPSTREAM_ROSTER["parsed_green"])
        self.assertIsNone(AN_OBL.UPSTREAM_ROSTER["parsed_total"])
        self.assertTrue(AN_OBL.UPSTREAM_ROSTER["dagger"])

    def test_wave_scope_disclosure_and_plan_face(self):
        plan = density_plan()
        rows = obligations()
        charged = Counter(row.parent_obligation for row in rows
                          if row.level == "DENSITY")
        self.assertEqual(charged, Counter({PLATFORM: 3}))
        self.assertEqual(set(AN_OBL.WAVE_SCOPE_UNREACHED_PARENTS),
                         set(UNREACHED))
        self.assertEqual(plan["wave_scope_unreached_parents"],
                         AN_OBL.WAVE_SCOPE_UNREACHED_PARENTS)
        for disclosure in AN_OBL.WAVE_SCOPE_UNREACHED_PARENTS.values():
            _assert_all(self, ["no parsed record", "parent-charged"],
                        disclosure)
        self.assertEqual((plan["parsed_absorption"]["leg_pass_record_indexes"],
                          plan["parsed_absorption"]["leg_pass_anchor_lines"],
                          plan["parsed_absorption"]["density_record_indexes"],
                          plan["parsed_absorption"]["absorption_map"],
                          plan["parsed_absorption"]["uncharged_parents"],
                          plan["parsed_absorption"]["residual_count_gap"]),
                         (list(range(1, 9)), LEG_PASS_LINES, [9, 10, 11],
                          {PLATFORM: list(range(1, 9))},
                          list(AN_OBL.WAVE_SCOPE_UNREACHED_PARENTS), 0))
        self.assertIn("verbatim",
                      plan["parsed_absorption"]["caliber"])
        self.assertEqual(plan["snapshot_provenance"],
                         AN_OBL.SNAPSHOT_PROVENANCE)
        self.assertEqual(plan["static_statement_catalog"],
                         AN_OBL.STATIC_CATALOG_EXPECTED)
        self.assertEqual((plan["emission_chain_disclosure"][
            "require_status_definition"],
                          plan["emission_chain_disclosure"]["fail_print"],
                          plan["emission_chain_disclosure"]["assert_status"],
                          plan["emission_chain_disclosure"]["assert_eq"],
                          plan["emission_chain_disclosure"][
                              "runner_stopped_warn"]),
                         ([33, 40], "common.sh:74", "common.sh:104-107",
                          "common.sh:68", "e2e.sh:210"))
        _assert_all(self, ["stories.sh:283-294", "deliberately not quoted"],
                    plan["emission_chain_disclosure"]["asset_fetch_branch"])
        self.assertEqual(plan["not_charged_source_probes"],
                         list(AN_OBL.NOT_CHARGED_PROBES))
        self.assertEqual(len(AN_OBL.NOT_CHARGED_PROBES), 4)
        _assert_all(self, ["skip_probe", "ensure_cli_token", "skip_case",
                           "excluded"], AN_OBL.NOT_CHARGED_PROBES[3])
        envelope = plan["envelope"]
        self.assertEqual((envelope["dual_commands"], envelope["read_case_keys"],
                          envelope["read_report_contract"],
                          envelope["register_report_contract"],
                          envelope["status_report_contract"],
                          envelope["new_command"],
                          plan["story_arguments"]["required"],
                          plan["story_arguments"]["optional"]),
                         (list(AN_OBL.DUAL_COMMANDS),
                          list(AN_OBL.READ_CASE_KEYS),
                          AN_OBL.READ_REPORT_CONTRACT,
                          AN_OBL.REGISTER_REPORT_CONTRACT,
                          AN_OBL.STATUS_REPORT_CONTRACT, False, [], []))
        self.assertEqual((plan["schema_version"], plan["contract"],
                          plan["parent"], plan["executor_row_index"],
                          plan["coverage_row_no"], plan["source_file"],
                          plan["source_sha256"], plan["story_line_span"],
                          plan["contract_source_revision"]),
                         (1, DENSITY_CONTRACT, PARENT, 1, 2,
                          AN_OBL.SOURCE_FILE, AN_OBL.SOURCE_SHA256, [255, 265],
                          AN_OBL.CONTRACT_REVISION))
        self.assertEqual(plan["companion_sources"], {
            "actor.sh": AN_OBL.ACTOR_SHA256, "common.sh": AN_OBL.COMMON_SHA256,
            "e2e.sh": AN_OBL.RUNNER_SHA256})
        executor = plan["executor_catalog"]
        self.assertEqual((executor["count"], executor["ordered_identity"],
                          executor["case_id"], executor["suite"],
                          executor["local_status"],
                          executor["canonical_status"],
                          executor["completion_contract"]),
                         (8, False, CASE_ID, SUITE, "LOCAL_NETWORK_PASS",
                          "LOCAL_SUITE_PASS",
                          "owned-local-network-completion-v1"))
        _assert_all(self, ["rc=0 iff LOCAL_NETWORK_PASS", "rc=130 CANCELLED"],
                    executor["rc_semantics"])
        _assert_all(self, ["bcs_e2e.local_network_contract.network_source_binding",
                           "LOCAL-NETWORK-01"], executor["source"])
        self.assertIn(executor["executor_digest_status"], ("live", "unavailable"))
        if executor["executor_digest_status"] == "live":
            self.assertEqual(executor["executor_digest"],
                             inventory_module.executor_digest())
        self.assertFalse(plan["family_registration"]["registered"])
        committed = json.loads(EXECUTOR_MIGRATION_DISK.read_text(
            encoding="utf-8"))["rows"]
        live_row = next(row for row in committed if row["id"] == PARENT)
        self.assertIn(live_row["executor_migration"]["notes"], ([], [NOTE]))
        self.assertEqual((plan["dual_committed_face"]["mode"],
                          plan["dual_committed_face"]["preprod_verdict"],
                          plan["dual_committed_face"]["gates"]),
                         (live_row["executor_migration"]["mode"],
                          live_row["executor_migration"]["preprod_verdict"],
                          []))
        self.assertIn("0 to 1", plan["dual_committed_face"]["notes_delta"])
        self.assertEqual((plan["remote_validation"],
                          plan["grants_release_credit"],
                          plan["source_read_only"],
                          plan["credential_references"]),
                         ("NOT_RUN", False, True,
                          [HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV]))

    def test_drift_fails_closed_on_story_bytes(self):
        pristine = _disk_text("stories.sh")
        real = AN_OBL._source_paths()
        with tempfile.TemporaryDirectory() as tmp:
            drifted = Path(tmp) / "stories.sh"
            drifted.write_text(pristine.replace(
                "advertised manifest asset is downloadable",
                "advertised manifest asset is drift"), encoding="utf-8")
            with mock.patch.object(
                    AN_OBL, "_source_paths",
                    return_value=dict(real, **{"stories.sh": drifted})):
                with self.assertRaisesRegex(RunStateError, "SHA-256 drifted"):
                    obligations()
        self.assertEqual(len(obligations()), 11)

    def test_drift_fails_closed_on_companion_bytes(self):
        real = AN_OBL._source_paths()
        for name in ("actor.sh", "common.sh", "e2e.sh"):
            pristine = _disk_text(name)
            with tempfile.TemporaryDirectory() as tmp:
                drifted = Path(tmp) / name
                drifted.write_text(pristine + "\n# drift\n", encoding="utf-8")
                with mock.patch.object(
                        AN_OBL, "_source_paths",
                        return_value=dict(real, **{name: drifted})):
                    with self.assertRaisesRegex(
                            RunStateError, "SHA-256 drifted: " + name):
                        obligations()
        self.assertEqual(len(obligations()), 11)

    def test_drift_fails_closed_on_leaf_anchor_bytes(self):
        stories_lines = _disk_lines("stories.sh")
        runner_lines = _disk_lines("e2e.sh")
        tampered = list(stories_lines)
        tampered[AN_OBL.FAIL_ANCHOR_LINE - 1] = tampered[
            AN_OBL.FAIL_ANCHOR_LINE - 1].replace("downloadable", "drifted")
        with self.assertRaisesRegex(RunStateError, "anchor bytes drifted"):
            AN_OBL._build_leaf_rows({"stories.sh": tampered,
                                     "e2e.sh": runner_lines})
        warn_tampered = list(stories_lines)
        warn_tampered[AN_OBL.WARN_RESPONSE_LINE - 1] = '        warn "drift"'
        with self.assertRaisesRegex(RunStateError, "anchor bytes drifted"):
            AN_OBL._build_leaf_rows({"stories.sh": warn_tampered,
                                     "e2e.sh": runner_lines})
        stop_tampered = list(runner_lines)
        stop_tampered[AN_OBL.RUNNER_STOP_LINE - 1] = '            warn "drift"'
        with self.assertRaisesRegex(RunStateError, "anchor bytes drifted"):
            AN_OBL._build_leaf_rows({"stories.sh": stories_lines,
                                     "e2e.sh": stop_tampered})

    def test_drift_fails_closed_on_contract_and_coverage_faces(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_contract = Path(tmp) / "network-migration-contract.json"
            bad_contract.write_text(json.dumps(
                {"schema_version": 9, "parent": PARENT,
                 "sources": {"stories.sh": AN_OBL.SOURCE_SHA256,
                             "actor.sh": AN_OBL.ACTOR_SHA256},
                 "requirements": []}), encoding="utf-8")
            with mock.patch.object(AN_OBL, "CONTRACT_FILE",
                                   str(bad_contract)):
                with self.assertRaisesRegex(
                        RunStateError, "migration contract face changed"):
                    obligations()
            bad_sources = Path(tmp) / "bad-sources.json"
            bad_sources.write_text(json.dumps(
                {"schema_version": 1, "source_revision": "0" * 40,
                 "parent": PARENT,
                 "sources": {"stories.sh": "0" * 64,
                             "actor.sh": AN_OBL.ACTOR_SHA256},
                 "requirements": []}), encoding="utf-8")
            with mock.patch.object(AN_OBL, "CONTRACT_FILE",
                                   str(bad_sources)):
                with self.assertRaisesRegex(
                        RunStateError, "migration contract face changed"):
                    obligations()
        with mock.patch.object(AN_OBL, "COVERAGE_ROW_NO", 3):
            with self.assertRaisesRegex(RunStateError,
                                        "coverage-map face changed"):
                obligations()
        self.assertEqual(len(obligations()), 11)

    def test_pure_span_and_catalog_helpers_fail_closed(self):
        with self.assertRaisesRegex(RunStateError, "unterminated"):
            AN_OBL._function_spans("alpha() {\n    echo hi\n")
        self.assertEqual(
            AN_OBL._function_spans(
                "alpha() {\n    echo hi\n}\nbeta() {\n    echo bye\n}"),
            {"alpha": (1, 3), "beta": (4, 6)})
        story_snippet = "\n".join([
            '    require_status "desc one" "200" || return',
            '    assert_json_eq "desc two" "$RESPONSE" "status" "ok"',
            '    assert_json_not_empty "desc three" "$RESPONSE" "env"',
            '    assert_not_empty "desc four" "$asset_url"',
            '    assert_contains "desc five" "$HDRS" "application/javascript"',
            '    if [[ "$asset_bytes" -gt 1000 ]]; then',
            '        pass "desc six (${asset_bytes} bytes)"',
            "    fi",
        ])
        self.assertEqual(AN_OBL._story_catalog(story_snippet, 1, 8), {
            "require_status": 1, "assert_json_eq": 1,
            "assert_json_not_empty": 1, "assert_not_empty": 1,
            "assert_contains": 1, "manual_size_verdict": 1, "total": 6})
        actor_snippet = "\n".join([
            '    if [ "$HTTP_STATUS" != "200" ]; then',
            "        return 1",
            "    fi",
            '    if [ "$HTTP_STATUS" = "200" ]; then',
            '        pass "shape"',
            "    fi",
            '    if [ "$n" -gt 0 ]; then',
            '        info "present"',
            "    fi",
            '    assert_eq "reflected" "$STATUS" "online"',
        ])
        self.assertEqual(AN_OBL._actor_catalog(actor_snippet, 1, 10), {
            "status_gate": 2, "shape_gate": 1, "assert_eq": 1, "total": 4})


