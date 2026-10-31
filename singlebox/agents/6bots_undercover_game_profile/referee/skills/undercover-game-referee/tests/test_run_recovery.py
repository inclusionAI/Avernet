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
                    with self.assertRaises(SystemExit), patch.object(uc, "bcs_cli", side_effect=self.cli):
                        uc.recovery().inspect(self.session, second["run_id"])
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
        with self.assertRaises(SystemExit), patch.object(uc, "bcs_cli", side_effect=self.cli):
            uc.recovery().inspect(self.session, "old-run")
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
        self.assertEqual(uc.load_state(self.session)["recovery"]["current"]["run_id"], "accepted-but-lost")

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


if __name__ == "__main__":
    unittest.main()
