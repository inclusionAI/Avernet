import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from core.case_loader import load_case
from adapters.local.bcn import BcnAdapter
from adapters.local.runtime import LocalRuntime
from adapters.local.evidence import collect_member
from core.grading import code_grade, checked_scores, combine

class P1Tests(unittest.TestCase):
    def setUp(self):
        self.case = load_case(ROOT.parents[2] / "bench-case/bcn-bench-case/task_01_singlebox_feedback_plan/case.md")

    def test_case(self):
        self.assertNotIn("bcn", self.case["meta"])
        self.assertNotIn("Expected Behavior", self.case["sections"]["Prompt"])

    def test_materials_rejected(self):
        text = (ROOT.parents[2] / "bench-case/bcn-bench-case/task_01_singlebox_feedback_plan/case.md").read_text()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "task.md"
            path.write_text(text.replace("workspace_files: []", "workspace_files: [input.txt]"))
            with self.assertRaises(ValueError): load_case(path)

    def test_weight_rejected(self):
        text = (ROOT.parents[2] / "bench-case/bcn-bench-case/task_01_singlebox_feedback_plan/case.md").read_text()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "task.md"; p.write_text(text.replace("automated: 0.1", "automated: .nan"))
            with self.assertRaises(ValueError): load_case(p)

    def test_code_grade(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "final.md"
            self.assertEqual(code_grade(self.case, d, 5), {"final_delivery_present": 0})
            p.write_text("  "); self.assertEqual(code_grade(self.case, d, 5)["final_delivery_present"], 0)
            p.write_text("Actual delivery"); self.assertEqual(code_grade(self.case, d, 5)["final_delivery_present"], 1)

    def test_judge_exception(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): code_grade({"code": "raise RuntimeError()"}, d, 5)

    def test_judge_timeout(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(subprocess.TimeoutExpired): code_grade({"code": "while True: pass"}, d, .1)

    def test_finite_scores(self):
        for value in (float("nan"), float("inf"), -1, 2, True):
            with self.assertRaises(ValueError): checked_scores({"x": value})
        self.assertAlmostEqual(combine({"a": 1}, .5, {"automated": .1, "llm_judge": .9}), .55)

    def test_create_goal_once(self):
        adapter = BcnAdapter("bcs-cli", "http://localhost:21000", {})
        with patch.object(adapter, "call", return_value="Group created:\n  ID: grp-1\n  Session: grp-1:abc\n") as call:
            self.assertEqual(adapter.create({"ceo": "b1", "worker": "b2"}, "THE GOAL", "case"), ("grp-1", "grp-1:abc"))
            self.assertEqual(call.call_count, 1)
            args = call.call_args.args
            self.assertEqual(args[args.index("--context") + 1], "THE GOAL")
            self.assertIn("--manager", args)
            self.assertNotIn("chat", args)

    def test_create_uncertain_no_retry(self):
        adapter = BcnAdapter("bcs-cli", "http://localhost:21000", {})
        with patch.object(adapter, "call", return_value="unknown") as call:
            with self.assertRaises(RuntimeError): adapter.create({"ceo": "b1"}, "goal", "case")
            self.assertEqual(call.call_count, 1)



    def test_exact_session_collection(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "profile"; sessions = root / "agents/main/sessions"; sessions.mkdir(parents=True)
            (sessions / "sessions.json").write_text(json.dumps({"agent:main:bcs:direct:grp:abcd": {"sessionId": "a"}, "main": {"sessionId": "other"}}))
            event = {"id": "m1", "message": {"role": "assistant", "usage": {"totalTokens": 12}}}
            (sessions / "a.jsonl").write_text(json.dumps(event)+"\n"+json.dumps(event)+"\n")
            (sessions / "other.jsonl").write_text('{"secret": "unrelated"}')
            got = collect_member(root, "grp:abcd", Path(d)/"evidence")
            self.assertEqual(got["total_tokens"], 12)
            self.assertEqual(len(got["files"]), 1)
            self.assertTrue(got["evidence_complete"])
            missing = collect_member(root, "grp:no", Path(d)/"missing")
            self.assertIsNone(missing["total_tokens"])
            self.assertFalse(missing["evidence_complete"])

    def test_partial_log_marked(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"p"; sessions=root/"agents/main/sessions"; sessions.mkdir(parents=True)
            (sessions/"sessions.json").write_text(json.dumps({"x:grp:s": {"sessionId": "a"}}))
            (sessions/"a.jsonl").write_text('{unfinished')
            got=collect_member(root,"grp:s",Path(d)/"e")
            self.assertFalse(got["evidence_complete"])
            self.assertIsNone(got["total_tokens"])








    def test_local_poll_maps_formal_completion(self):
        from unittest.mock import MagicMock
        runtime = LocalRuntime.__new__(LocalRuntime)
        runtime.sid = 'g:s'; runtime.client = MagicMock()
        for state, expected in [({'status': 'running'}, 'running'),
                                ({'status': 'completed', 'output': 'answer'}, 'completed'),
                                ({'status': 'completed'}, 'failed'),
                                ({'status': 'completed', 'output': 'answer', 'error_message': 'error'}, 'failed')]:
            runtime.client.get.return_value = state
            self.assertEqual(runtime.poll(3)['status'], expected)

    def test_local_poll_timeout(self):
        import subprocess
        from unittest.mock import MagicMock
        runtime = LocalRuntime.__new__(LocalRuntime)
        runtime.sid = 'g:s'; runtime.client = MagicMock()
        runtime.client.get.side_effect = subprocess.TimeoutExpired('bcs', 2)
        with self.assertRaises(TimeoutError): runtime.poll(2)

if __name__ == "__main__": unittest.main()
