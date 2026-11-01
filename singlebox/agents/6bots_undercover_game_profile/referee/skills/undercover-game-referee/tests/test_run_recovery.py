"""Recovery is driven by persisted phase and authenticated server facts."""
import argparse
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_undercover_panel import undercover as uc


class RunRecoveryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"BOT_DATA_DIR": temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.base = json.loads((Path(__file__).parent / "fixtures/flow_baseline.json").read_text())[0]["state"]
        self.base["pending_ping"] = None
        self.session = self.base["session_id"]
        self.facts = {}
        self.submissions = []

    def prepare(self, kind, pk=False, phase=None):
        state = copy.deepcopy(self.base)
        state["rounds"][0]["speeches"] = {}
        state["rounds"][0]["votes"] = {}
        state["rounds"][0]["renders"] = {kind: 1}
        if pk:
            state["rounds"][0]["pk"] = copy.deepcopy(state["rounds"][0])
        state["phase"] = phase or (("PK_" if pk else "") + ("SPEAK_RUNNING" if kind == "speak" else "VOTE_RUNNING"))
        key = uc._recovery.scope(state, kind)
        state["recovery"] = {"current": {"state": "submitted", "run_id": "old-run", "kind": kind,
                                           "scope": key, "attempt": 1, "correlation": {}}, "retries": {}}
        uc.save_state(state)
        self.record("old-run", "failed", "node_failed")
        return key

    def record(self, run_id, status, reason, marker=None):
        self.facts[run_id] = {"run_id": run_id, "session_id": self.session,
            "group_id": self.base["group_id"], "created_by": self.base["referee_uuid"], "status": status,
            "client_reference": marker, "terminal_result":
            {"run_id": run_id, "status": status, "schema_version": 1, "reason_category": reason}}

    def cli(self, *args, **kwargs):
        if "query" in args:
            run = args[args.index("--run") + 1]
            return 0, json.dumps(self.facts.get(run, {})), ""
        return 0, '{"allowed":true}', ""

    def submit(self, session, yaml, input_path, *other):
        marker = json.loads(Path(input_path).read_text())["bcs_client_reference"]
        run_id = f"new-run-{len(self.submissions) + 1}"
        self.submissions.append(run_id)
        self.record(run_id, "failed", "node_failed", marker)
        return {"run": {"run_id": run_id}}

    def invoke(self, kind, run_id, retry=True):
        command = uc.cmd_open_round if kind == "speak" else uc.cmd_open_vote
        with patch.object(uc, "bcs_cli", side_effect=self.cli), patch.object(uc, "require_run_slot"), \
             patch.object(uc, "self_lock_hint", return_value=None), patch.object(uc, "submit_run", side_effect=self.submit), \
             patch.object(uc, "emit") as emit:
            command(argparse.Namespace(session=self.session, retry=retry, failed_run=run_id))
        return emit.call_args.args[0]

    def test_two_retries_per_round_and_stage_duplicate_and_third_failure_stop(self):
        for kind in ("speak", "vote"):
            for pk in (False, True):
                with self.subTest(kind=kind, pk=pk):
                    self.submissions.clear(); self.facts.clear()
                    key = self.prepare(kind, pk)
                    first = self.invoke(kind, "old-run")
                    self.assertTrue(first["submitted"])
                    duplicate = self.invoke(kind, "old-run")
                    self.assertEqual(duplicate["action"], "duplicate")
                    second = self.invoke(kind, first["run_id"])
                    self.assertTrue(second["submitted"])
                    with patch.object(uc, "bcs_cli", side_effect=self.cli):
                        self.assertEqual(uc.recovery().inspect(self.session, second["run_id"])["action"], "exhausted")
                    # Human fallback omits --failed-run but must share the same cap.
                    with self.assertRaises(SystemExit), patch.object(uc, "bcs_cli", side_effect=self.cli):
                        (uc.cmd_open_round if kind == "speak" else uc.cmd_open_vote)(
                            argparse.Namespace(session=self.session, retry=True, failed_run=None))
                    state = uc.load_state(self.session)
                    self.assertEqual(state["recovery"]["retries"][key], 2)
                    self.assertEqual(len(self.submissions), 2)

    def test_abort_and_untrusted_cross_session_cannot_restart(self):
        self.prepare("speak")
        self.record("old-run", "aborted", "user_aborted")
        before = uc.state_path(self.session).read_bytes()
        with patch.object(uc, "bcs_cli", side_effect=self.cli):
            self.assertEqual(uc.recovery().inspect(self.session, "old-run")["action"], "stopped")
        self.assertEqual(uc.state_path(self.session).read_bytes(), before)
        with self.assertRaises(SystemExit):
            self.invoke("speak", "old-run")
        self.assertEqual(uc.load_state(self.session)["recovery"]["stopped"]["reason"], "user_aborted")
        self.prepare("speak")
        self.facts["old-run"]["session_id"] = "another-session"
        with self.assertRaises(SystemExit), patch.object(uc, "bcs_cli", side_effect=self.cli):
            uc.recovery().inspect(self.session, "old-run")
        self.assertEqual(self.submissions, [])

    def test_late_result_after_round_change_does_not_restart(self):
        self.prepare("speak")
        state = uc.load_state(self.session)
        state["round"] += 1
        uc.save_state(state)
        with patch.object(uc, "bcs_cli", side_effect=self.cli):
            self.assertEqual(uc.recovery().inspect(self.session, "old-run")["action"], "stale")

    def test_collected_speeches_open_vote_without_retrying_speech(self):
        self.prepare("speak", phase="AWAIT_VOTE_START")
        with patch.object(uc, "bcs_cli", side_effect=self.cli):
            self.assertEqual(uc.recovery().inspect(self.session, "old-run")["action"], "open-vote")
        result = self.invoke("vote", "old-run", retry=False)
        self.assertTrue(result["submitted"])
        self.assertEqual(uc.load_state(self.session)["recovery"].get("retries", {}), {})

    def test_stale_collect_and_tally_cannot_overwrite_current_attempt(self):
        for kind, command in (("speak", uc.cmd_speeches_set), ("vote", uc.cmd_votes_set)):
            with self.subTest(kind=kind):
                self.prepare(kind)
                state = uc.load_state(self.session)
                state["recovery"]["current"]["attempt"] = 2
                uc.current_stage(state)["renders"][kind] = 2
                uc.save_state(state)
                args = argparse.Namespace(session=self.session, round=state["round"], stage="regular", attempt=1,
                    json=json.dumps({str(n): "平时偶尔会见到" for n in range(1, 7)}), flag=[])
                with self.assertRaises(SystemExit):
                    command(args)
                after = uc.load_state(self.session)
                self.assertEqual(after["phase"], state["phase"])
                self.assertEqual(uc.current_stage(after)["speeches" if kind == "speak" else "votes"], {})

    def test_lost_response_is_reconciled_by_nonce_without_blind_resubmission(self):
        self.prepare("speak")
        def lost(session, yaml, input_path, *other):
            marker = json.loads(Path(input_path).read_text())["bcs_client_reference"]
            self.record("accepted-but-lost", "failed", "node_failed", marker)
            raise RuntimeError("response lost")
        with patch.object(uc, "bcs_cli", side_effect=self.cli), patch.object(uc, "require_run_slot"), \
             patch.object(uc, "submit_run", side_effect=lost), self.assertRaises(RuntimeError):
            uc.cmd_open_round(argparse.Namespace(session=self.session, retry=True, failed_run="old-run"))
        self.assertEqual(uc.load_state(self.session)["recovery"]["current"]["state"], "uncertain")
        with patch.object(uc, "bcs_cli", side_effect=self.cli):
            result = uc.recovery().inspect(self.session, "accepted-but-lost")
        self.assertEqual(result["action"], "retry-speak")
        self.assertEqual(uc.load_state(self.session)["recovery"]["current"]["state"], "uncertain")
        self.assertTrue(self.invoke("speak", "accepted-but-lost")["submitted"])

    def test_lost_response_with_unrelated_nonce_cannot_be_reconciled(self):
        self.prepare("speak")
        def lost(session, yaml, input_path, *other):
            self.record("unrelated-run", "failed", "node_failed", "different-reference")
            raise RuntimeError("response lost")
        with patch.object(uc, "bcs_cli", side_effect=self.cli), patch.object(uc, "require_run_slot"), \
             patch.object(uc, "submit_run", side_effect=lost), self.assertRaises(RuntimeError):
            uc.cmd_open_round(argparse.Namespace(session=self.session, retry=True, failed_run="old-run"))
        with patch.object(uc, "bcs_cli", side_effect=self.cli), self.assertRaises(SystemExit):
            uc.recovery().inspect(self.session, "unrelated-run")
        self.assertEqual(uc.load_state(self.session)["recovery"]["current"]["state"], "uncertain")
        self.assertEqual(self.submissions, [])

    def status(self, failed_run=None):
        with patch.object(uc, "bcs_cli", side_effect=self.cli) as cli, patch.object(uc, "emit") as emit:
            uc.cmd_status(argparse.Namespace(session=self.session, full=False, failed_run=failed_run))
        return emit.call_args.args[0], cli.call_count

    def test_ordinary_status_detects_failure_and_returns_executable_recovery_without_writing(self):
        import shlex
        for kind in ("speak", "vote"):
            for pk in (False, True):
                with self.subTest(kind=kind, pk=pk):
                    self.prepare(kind, pk)
                    before = uc.state_path(self.session).read_bytes()
                    ordinary, calls = self.status()
                    explicit, explicit_calls = self.status("old-run")
                    self.assertEqual(calls, 1)
                    self.assertEqual(explicit_calls, 1)
                    self.assertEqual(ordinary, explicit)
                    self.assertEqual(ordinary["run_result_action"]["action"], "retry-" + kind)
                    self.assertIn(ordinary["command"], ordinary["next_action"])
                    argv = shlex.split(ordinary["command"])
                    self.assertEqual(argv[1], "open-round" if kind == "speak" else "open-vote")
                    self.assertEqual(argv[argv.index("--failed-run") + 1], "old-run")
                    self.assertIn("--retry", argv)
                    self.assertEqual(uc.state_path(self.session).read_bytes(), before)
                    self.assertEqual(self.submissions, [])
                    for secret in self.base["words"].values():
                        self.assertNotIn(secret, json.dumps(ordinary, ensure_ascii=False))

    def test_status_running_and_completed_preserve_normal_handoff(self):
        for status in ("running", "completed"):
            self.prepare("speak")
            self.record("old-run", status, None)
            result, calls = self.status()
            self.assertEqual(calls, 1)
            self.assertNotIn("command", result)
            self.assertEqual(result["next_action"], uc.NEXT_ACTION["SPEAK_RUNNING"])
            self.assertEqual(self.submissions, [])

    def test_status_stops_for_abort_exhaustion_and_uncertainty_without_writing(self):
        for case in ("abort", "exhausted", "uncertain"):
            key = self.prepare("speak")
            state = uc.load_state(self.session)
            if case == "abort":
                self.record("old-run", "aborted", "user_aborted")
            elif case == "exhausted":
                state["recovery"]["retries"][key] = 2
            else:
                state["recovery"]["current"].update(state="uncertain", run_id=None)
            uc.save_state(state)
            before = uc.state_path(self.session).read_bytes()
            result, calls = self.status()
            self.assertEqual(result["run_result_action"]["action"], "stopped" if case == "abort" else case)
            self.assertEqual(calls, 0 if case == "uncertain" else 1)
            self.assertNotIn("command", result)
            self.assertNotEqual(result["next_action"], uc.NEXT_ACTION["SPEAK_RUNNING"])
            self.assertEqual(uc.state_path(self.session).read_bytes(), before)

    def test_query_failure_does_not_emit_local_wait_or_write_state(self):
        self.prepare("speak")
        before = uc.state_path(self.session).read_bytes()
        with patch.object(uc, "bcs_cli", return_value=(1, "private raw error", "")), \
             patch.object(uc, "emit") as emit, self.assertRaises(SystemExit):
            uc.cmd_status(argparse.Namespace(session=self.session, full=False))
        emit.assert_not_called()
        self.assertEqual(uc.state_path(self.session).read_bytes(), before)

    def test_status_without_submission_or_finished_does_not_query(self):
        for phase in ("AWAIT_START", "FINISHED"):
            state = copy.deepcopy(self.base)
            state["phase"] = phase
            uc.save_state(state)
            result, calls = self.status()
            self.assertEqual(calls, 0)
            self.assertNotIn("command", result)

    def test_open_rechecks_server_after_status_and_never_reopens_an_active_run(self):
        self.prepare("speak")
        self.assertEqual(self.status()[0]["run_result_action"]["action"], "retry-speak")
        self.record("old-run", "running", None)
        with self.assertRaises(SystemExit):
            self.invoke("speak", "old-run")
        self.assertEqual(self.submissions, [])

    def test_open_rechecks_round_after_status(self):
        self.prepare("speak")
        self.status()
        state = uc.load_state(self.session)
        state["round"] += 1
        uc.save_state(state)
        self.assertEqual(self.invoke("speak", "old-run")["action"], "stale")
        self.assertEqual(self.submissions, [])

    def test_concurrent_duplicate_recovery_submits_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        self.prepare("speak")
        args = argparse.Namespace(session=self.session, retry=True, failed_run="old-run")
        with patch.object(uc, "bcs_cli", side_effect=self.cli), patch.object(uc, "require_run_slot"), \
             patch.object(uc, "submit_run", side_effect=self.submit), patch.object(uc, "emit"), \
             ThreadPoolExecutor(max_workers=2) as workers:
            results = [workers.submit(uc.cmd_open_round, args) for _ in range(2)]
            for result in results:
                result.result(timeout=10)
        self.assertEqual(len(self.submissions), 1)
        saved = uc.load_state(self.session)["recovery"]
        self.assertEqual(list(saved["retries"].values()), [1])
        self.assertEqual(list(saved["processed"]), ["old-run"])


if __name__ == "__main__":
    unittest.main()
