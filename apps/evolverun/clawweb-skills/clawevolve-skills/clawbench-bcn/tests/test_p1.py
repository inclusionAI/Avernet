import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from task_loader import load_case
from bcn_adapter import BcnAdapter
from evidence import collect_member
from grading_adapter import code_grade, checked_scores, combine
from runtime import validate_profile, local_url

class P1Tests(unittest.TestCase):
    def setUp(self):
        self.case = load_case(ROOT / "examples/task_01_singlebox_feedback_plan.md")

    def test_case(self):
        self.assertEqual(self.case["meta"]["bcn"]["entry_member"], "ceo")
        self.assertNotIn("Expected Behavior", self.case["sections"]["Prompt"])

    def test_materials_rejected(self):
        text = (ROOT / "examples/task_01_singlebox_feedback_plan.md").read_text()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "task.md"
            path.write_text(text.replace("workspace_files: []", "workspace_files: [input.txt]"))
            with self.assertRaises(ValueError): load_case(path)

    def test_weight_rejected(self):
        text = (ROOT / "examples/task_01_singlebox_feedback_plan.md").read_text()
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
        self.assertAlmostEqual(combine({"a": 1}, {"b": .5}, {"automated": .1, "llm_judge": .9}), .55)

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

    def test_completion_uses_output(self):
        import time
        adapter = BcnAdapter("bcs", "http://localhost", {})
        with patch.object(adapter, "get", return_value={"status": "completed", "output": "answer"}):
            self.assertEqual(adapter.wait("s", time.monotonic()+5, lambda _: None), ("completed", "answer"))
        with patch.object(adapter, "get", return_value={"status": "completed", "output": "answer", "error_message": "failed"}):
            self.assertEqual(adapter.wait("s", time.monotonic()+5, lambda _: None)[0], "failed")
        self.assertEqual(adapter.wait("s", time.monotonic()-1, lambda _: None)[0], "timeout")

    def test_no_final_fails(self):
        import time
        adapter = BcnAdapter("bcs", "http://localhost", {})
        with patch.object(adapter, "get", return_value={"status": "completed"}):
            self.assertEqual(adapter.wait("s", time.monotonic()+2, lambda _: None)[0], "failed")

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

    def test_local_only(self):
        self.assertEqual(local_url("http://localhost:21000/"), "http://localhost:21000")
        for url in ("https://example.com", "http://u:p@localhost", "file:///x"):
            with self.assertRaises(ValueError): local_url(url)

    def test_start_failure_still_stops_owned_bots(self):
        import benchmark
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as d:
            team = MagicMock()
            team.digest, team.mapping = "hash", {"ceo": "owned-ceo"}
            team.command.side_effect = [RuntimeError("startup failed"), None]
            with patch.object(benchmark, "Singlebox", return_value=team), patch.object(benchmark, "BcnAdapter") as adapter:
                path, result = benchmark.run(self.case, {"output_dir": Path(d)}, Path(d)/"source")
            self.assertEqual([c.args[0] for c in team.command.call_args_list], ["start", "stop"])
            team.verify_stopped.assert_called_once()
            adapter.assert_not_called()
            self.assertEqual(result["execution_status"], "environment_error")
            self.assertTrue((path / "result.json").exists())
            log = (path / "runner.log").read_text()
            self.assertIn("执行异常", log)
            self.assertIn("本次Bot已停止", log)
            self.assertIn("未评分", log)

    def test_wait_cli_deadline_is_timeout(self):
        import subprocess
        adapter = BcnAdapter("bcs", "http://localhost", {})
        with patch("bcn_adapter.time.monotonic", side_effect=[0, 0, 11]), patch.object(adapter, "get", side_effect=subprocess.TimeoutExpired("bcs", 10)):
            self.assertEqual(adapter.wait("s", 10, lambda _: None), ("timeout", ""))

    def test_runtime_identity_is_unique_without_changing_source(self):
        from runtime import Singlebox
        from unittest.mock import MagicMock
        # Resolve repository root without depending on invocation cwd.
        source = next(p for p in ROOT.parents if (p / "singlebox/agents/5bots_profile").is_dir()) / "singlebox/agents/5bots_profile"
        original = (source / "bots.json").read_bytes()
        with tempfile.TemporaryDirectory() as d, patch("runtime.socket.socket", return_value=MagicMock()):
            team = Singlebox({"port_start": 32001, "model_mode": "home"}, source, Path(d)/"run-unique")
            manifest = json.loads((team.profile / "bots.json").read_text())
            self.assertTrue(all("run-unique" in b["name"] for b in manifest["bots"]))
            self.assertTrue(all("run-unique" in b["profile"] for b in manifest["bots"]))
        self.assertEqual(original, (source / "bots.json").read_bytes())

    def test_default_runtime_reuses_model_file(self):
        from runtime import load_runtime
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=False):
            p = Path(d)/"model.json"
            data = {"agents": {"defaults": {"model": {"primary": "test/example"}}},
                    "models": {"providers": {"test": {"api": "openai-completions", "baseUrl": "https://example.com/v1", "apiKey": "${TEST_BCN_KEY}"}}}}
            p.write_text(json.dumps(data))
            os.environ.update(SINGLEBOX_MODEL_CONFIG_FILE=str(p), TEST_BCN_KEY="test-only-value")
            config = load_runtime()
            self.assertEqual(config["model_mode"], "reuse")
            self.assertEqual(config["judge"]["model"], "example")
            self.assertNotIn("test-only-value", str(config))
            self.assertEqual(list(Path(d).iterdir()), [p])

    def test_default_runtime_missing_configuration(self):
        from runtime import load_runtime
        with patch.dict(os.environ, {"SINGLEBOX_MODEL_CONFIG_FILE": "/nonexistent/bcn-model.json"}):
            with self.assertRaisesRegex(ValueError, "configure Singlebox"):
                load_runtime()

    def test_reuse_environment_does_not_change_model_mode(self):
        from runtime import Singlebox
        from unittest.mock import MagicMock
        repo = next(p for p in ROOT.parents if (p / "singlebox/singlebox.sh").is_file())
        with tempfile.TemporaryDirectory() as d, patch("runtime.socket.socket", return_value=MagicMock()), patch.dict(os.environ, {"BCN_BENCH_DEFAULT_JUDGE_KEY": "test-only"}):
            team = Singlebox({"repository": repo, "port_start": 32001, "model_mode": "reuse"}, repo/"singlebox/agents/5bots_profile", Path(d)/"run")
            self.assertEqual(team.env["SINGLEBOX_MODEL_CONFIG_PREPARED"], "1")
            self.assertNotIn("BCN_BENCH_DEFAULT_JUDGE_KEY", team.env)
            self.assertNotEqual(team.env.get("SINGLEBOX_MODEL_CONFIG_MODE"), "reuse")

if __name__ == "__main__": unittest.main()
