from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import clawmind_adapter  # noqa: E402


class ClawMindAdapterCallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        clawmind_adapter._owner_user_id = None

    def test_relocate_benchmark_input_preserves_retry_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            home = base / "home"
            agentbench_home = base / "clawbench"
            (agentbench_home / "tasks").mkdir(parents=True)
            source_dir = base / "work" / "input"
            source_dir.mkdir(parents=True)
            (source_dir / "task_01.md").write_text("# task\n", encoding="utf-8")

            with mock.patch.object(clawmind_adapter.Path, "home", return_value=home):
                relocated, benchmark_arg = clawmind_adapter.relocate_benchmark_input(
                    agentbench_home, source_dir, "bench_retry_copy",
                )

            self.assertTrue((source_dir / "task_01.md").is_file())
            self.assertTrue((relocated / "task_01.md").is_file())
            compatibility = agentbench_home / "tasks" / benchmark_arg
            self.assertTrue((compatibility / "task_01.md").is_file())

    def test_explicit_input_dir_uses_temporary_link_without_global_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            home = base / "home"
            agentbench_home = base / "clawbench"
            agentbench_home.mkdir(parents=True)
            input_dir = base / "clawevolve_results" / "EV-1" / "bench" / "input"
            input_dir.mkdir(parents=True)
            (input_dir / "task_01.md").write_text("# task\n", encoding="utf-8")
            env = {
                "AGENTBENCH_HOME": str(agentbench_home),
                "BENCHMARK_DIR": str(input_dir),
                "INPUT_DIR": str(input_dir),
                "OUTPUT_DIR": str(base / "clawevolve_results" / "EV-1" / "bench" / "output"),
                "BENCH_RUN_ID": "bench_evolve_001",
                "OPENCLAW_EXECUTION_MODE": "local",
                "OWNER_ID": "197444",
            }
            completed = SimpleNamespace(returncode=0)
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(clawmind_adapter.Path, "home", return_value=home), \
                    mock.patch.object(clawmind_adapter.subprocess, "run", return_value=completed) as run_mock:
                with self.assertRaises(SystemExit):
                    clawmind_adapter.action_run_agentbench()

            command = run_mock.call_args.args[0]
            benchmark_arg = command[command.index("--benchmark") + 1]
            self.assertEqual(benchmark_arg, "clawbench_runtime/bench_evolve_001/input")
            self.assertEqual(
                run_mock.call_args.kwargs["env"]["CLAWBENCH_OPENCLAW_EXECUTION_MODE"],
                "local",
            )
            self.assertFalse((home / ".openclaw/workspace/clawbench_results/bench_evolve_001/input").exists())
            self.assertFalse((agentbench_home / "tasks/clawbench_runtime/bench_evolve_001").exists())
            self.assertTrue((input_dir / "task_01.md").is_file())

    def test_run_agentbench_reuses_relocated_input_when_cached_source_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            home = base / "home"
            agentbench_home = base / "clawbench"
            (agentbench_home / "tasks").mkdir(parents=True)
            missing_source = base / "work" / "input"
            relocated = home / ".openclaw" / "workspace" / "clawbench_results" / "bench_retry_old" / "input"
            relocated.mkdir(parents=True)
            (relocated / "task_01.md").write_text("# task\n", encoding="utf-8")

            env = {
                "AGENTBENCH_HOME": str(agentbench_home),
                "BENCHMARK_DIR": str(missing_source),
                "BENCH_RUN_ID": "bench_retry_old",
                "OWNER_ID": "197444",
                "SUITE": "all",
                "MODEL": "antchat/GLM-5",
                "SCENE": "openclaw-clawbench",
            }
            completed = SimpleNamespace(returncode=0)
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(clawmind_adapter.Path, "home", return_value=home), \
                    mock.patch.object(clawmind_adapter.subprocess, "run", return_value=completed) as run_mock:
                with self.assertRaises(SystemExit):
                    clawmind_adapter.action_run_agentbench()

            self.assertTrue(run_mock.called, "retry must reach benchmark.py instead of failing path validation")
            command = run_mock.call_args.args[0]
            benchmark_index = command.index("--benchmark") + 1
            self.assertEqual(
                command[benchmark_index],
                "clawbench_results/bench_retry_old/input",
            )
            self.assertTrue((relocated / "task_01.md").is_file())

    def test_run_agentbench_injects_callback_env_into_child_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            home = base / "home"
            agentbench_home = base / "clawbench"
            benchmark_input = agentbench_home / "tasks" / "runtime" / "input"
            benchmark_input.mkdir(parents=True)

            env = {
                "AGENTBENCH_HOME": str(agentbench_home),
                "BENCHMARK_DIR": "runtime/input",
                "BENCH_RUN_ID": "bench_test_001",
                "CLAWWEB_URL": "https://clawweb.example.com/",
                "OWNER_ID": "197444",
                "SUITE": "all",
                "MODEL": "antchat/GLM-5",
                "SCENE": "openclaw-clawbench",
            }

            completed = SimpleNamespace(returncode=0)
            with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(clawmind_adapter.Path, "home", return_value=home), \
                mock.patch.object(clawmind_adapter.subprocess, "run", return_value=completed) as run_mock:
                with self.assertRaises(SystemExit):
                    clawmind_adapter.action_run_agentbench()

            _, kwargs = run_mock.call_args
            child_env = kwargs.get("env")
            self.assertIsNotNone(child_env, "benchmark child process must receive an explicit env")
            self.assertEqual(child_env["CLAWBENCH_CALLBACK_ENABLED"], "1")
            self.assertEqual(child_env["CLAWBENCH_BENCH_RUN_ID"], "bench_test_001")
            self.assertEqual(child_env["CLAWBENCH_OWNER_ID"], "197444")

    def test_run_agentbench_overwrites_template_callback_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            home = base / "home"
            agentbench_home = base / "clawbench"
            benchmark_input = agentbench_home / "tasks" / "runtime" / "input"
            benchmark_input.mkdir(parents=True)

            env = {
                "AGENTBENCH_HOME": str(agentbench_home),
                "BENCHMARK_DIR": "runtime/input",
                "BENCH_RUN_ID": "bench_test_owner_template",
                "CLAWWEB_URL": "https://clawweb.example.com/",
                "CLAWBENCH_OWNER_ID": "{{input.params.ownerId}}",
                "OWNER_ID": "197444",
                "SCENE": "openclaw-clawbench",
            }

            completed = SimpleNamespace(returncode=0)
            with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(clawmind_adapter.Path, "home", return_value=home), \
                mock.patch.object(clawmind_adapter.subprocess, "run", return_value=completed) as run_mock:
                with self.assertRaises(SystemExit):
                    clawmind_adapter.action_run_agentbench()

            _, kwargs = run_mock.call_args
            child_env = kwargs.get("env")
            self.assertEqual(child_env["CLAWBENCH_OWNER_ID"], "197444")
            self.assertEqual(child_env["CLAWBENCH_CALLBACK_ENABLED"], "1")

    def test_run_agentbench_writes_callback_status_to_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            home = base / "home"
            agentbench_home = base / "clawbench"
            benchmark_input = agentbench_home / "tasks" / "runtime" / "input"
            benchmark_input.mkdir(parents=True)

            env = {
                "AGENTBENCH_HOME": str(agentbench_home),
                "BENCHMARK_DIR": "runtime/input",
                "BENCH_RUN_ID": "bench_test_002",
                "CLAWWEB_URL": "https://clawweb.example.com/",
                "OWNER_ID": "197444",
                "SCENE": "openclaw-clawbench",
            }

            completed = SimpleNamespace(returncode=0)
            with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(clawmind_adapter.Path, "home", return_value=home), \
                mock.patch.object(clawmind_adapter.subprocess, "run", return_value=completed):
                with self.assertRaises(SystemExit):
                    clawmind_adapter.action_run_agentbench()

            log_path = home / ".openclaw" / "workspace" / "clawbench_results" / "bench_test_002" / "output" / "run_agentbench.log"
            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn("callback enabled=1", log_text)
            self.assertIn("benchRunId=bench_test_002", log_text)
            self.assertIn("ownerId=set", log_text)

            adapter_log_path = home / ".openclaw" / "workspace" / "clawbench_results" / "bench_test_002" / "output" / "clawmind_adapter.log"
            adapter_log_text = adapter_log_path.read_text(encoding="utf-8")
            self.assertIn("run-agentbench started", adapter_log_text)
            self.assertIn("benchRunId=bench_test_002", adapter_log_text)
            self.assertIn("outputDir=", adapter_log_text)

    def test_upload_results_uploads_archived_session_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_dir = base / "output"
            scene_dir = output_dir / "benchmark" / "openclaw-clawbench"
            transcript_dir = scene_dir / "20260706_antchat-glm-5_transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "task_00_sanity.jsonl").write_text(
                json.dumps({"type": "session", "id": "session_1"}) + "\n",
                encoding="utf-8",
            )
            report_path = scene_dir / "20260706_antchat-glm-5_benchmark_report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "task_id": "task_00_sanity",
                                "name": "Sanity",
                                "status": "success",
                                "grading": {"mean": 1.0},
                                "usage": {"total_tokens": 12},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            env = {
                "BENCH_RUN_ID": "bench_test_003",
                "STATUS": "succeeded",
                "RESULT_PATH": str(report_path),
                "OUTPUT_DIR": str(output_dir),
                "SCENE": "openclaw-clawbench",
                "CLAWWEB_URL": "https://clawweb.example.com/",
                "AGENTBENCH_HOME": str(base / "clawbench"),
                "OWNER_ID": "197444",
            }
            ok_payloads: list[tuple[str, str, dict]] = []

            def fake_api_ok(method: str, url: str, payload: dict) -> bool:
                ok_payloads.append((method, url, payload))
                return True

            def fake_api_json(method: str, url: str, payload: dict | None = None) -> dict:
                if method == "GET" and url.endswith("/artifacts?artifactType=session"):
                    return []
                if method == "GET" and "/api/bench/runs/" in url:
                    return {"summary": {}}
                return {}

            with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(clawmind_adapter, "api_ok", side_effect=fake_api_ok), \
                mock.patch.object(clawmind_adapter, "api_json", side_effect=fake_api_json):
                with self.assertRaises(SystemExit):
                    clawmind_adapter.action_upload_results()

            artifact_calls = [
                payload
                for method, url, payload in ok_payloads
                if method == "POST" and url.endswith("/api/bench/runs/bench_test_003/artifacts")
            ]
            self.assertEqual(len(artifact_calls), 1)
            artifact = artifact_calls[0]
            self.assertEqual(artifact["artifactType"], "session")
            self.assertEqual(artifact["taskId"], "task_00_sanity")
            self.assertIn("session_1", artifact["contentText"])
            self.assertEqual(artifact["summary"]["totalTokens"], 12)

    def test_upload_results_converts_execution_time_seconds_to_ms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            output_dir = base / "output"
            scene_dir = output_dir / "benchmark" / "openclaw-clawbench"
            scene_dir.mkdir(parents=True)
            report_path = scene_dir / "20260706_antchat-glm-5_benchmark_report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "task_id": "task_00_sanity",
                                "name": "Sanity",
                                "status": "success",
                                "execution_time": 42.5,
                                "grading": {"mean": 1.0},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            env = {
                "BENCH_RUN_ID": "bench_test_duration",
                "STATUS": "succeeded",
                "RESULT_PATH": str(report_path),
                "OUTPUT_DIR": str(output_dir),
                "SCENE": "openclaw-clawbench",
                "CLAWWEB_URL": "https://clawweb.example.com/",
                "AGENTBENCH_HOME": str(base / "clawbench"),
                "OWNER_ID": "197444",
            }
            ok_payloads: list[tuple[str, str, dict]] = []

            def fake_api_ok(method: str, url: str, payload: dict) -> bool:
                ok_payloads.append((method, url, payload))
                return True

            def fake_api_json(method: str, url: str, payload: dict | None = None) -> dict:
                if method == "GET" and url.endswith("/artifacts?artifactType=session"):
                    return []
                if method == "GET" and "/api/bench/runs/" in url:
                    return {"summary": {}}
                return {}

            with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(clawmind_adapter, "api_ok", side_effect=fake_api_ok), \
                mock.patch.object(clawmind_adapter, "api_json", side_effect=fake_api_json):
                with self.assertRaises(SystemExit):
                    clawmind_adapter.action_upload_results()

            result_calls = [
                payload
                for method, url, payload in ok_payloads
                if method == "POST" and url.endswith("/api/bench/runs/bench_test_duration/results")
            ]
            self.assertEqual(len(result_calls), 1)
            self.assertEqual(result_calls[0]["results"][0]["executionTimeMs"], 42500)

    def test_api_json_retries_generic_transient_error(self) -> None:
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return b'{"status":"ok"}'

        with mock.patch.dict(os.environ, {"OWNER_ID": "197444"}, clear=True), \
            mock.patch.object(clawmind_adapter, "_retry_delay", return_value=0), \
            mock.patch.object(clawmind_adapter.time, "sleep") as sleep_mock, \
            mock.patch.object(
                clawmind_adapter.urllib.request,
                "urlopen",
                side_effect=[OSError("temporary"), _Response()],
            ) as urlopen_mock:
            result = clawmind_adapter.api_json("GET", "https://clawweb.example.com/api/ping")

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(urlopen_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0)

    def test_api_json_retries_transient_http_error(self) -> None:
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return b'{"status":"ok"}'

        http_error = urllib.error.HTTPError(
            url="https://clawweb.example.com/api/ping",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )

        with mock.patch.dict(os.environ, {"OWNER_ID": "197444"}, clear=True), \
            mock.patch.object(clawmind_adapter, "_retry_delay", return_value=0), \
            mock.patch.object(clawmind_adapter.time, "sleep") as sleep_mock, \
            mock.patch.object(
                clawmind_adapter.urllib.request,
                "urlopen",
                side_effect=[http_error, _Response()],
            ) as urlopen_mock:
            result = clawmind_adapter.api_json("GET", "https://clawweb.example.com/api/ping")

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(urlopen_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
