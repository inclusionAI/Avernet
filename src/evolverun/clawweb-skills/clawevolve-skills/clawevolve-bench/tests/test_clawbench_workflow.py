import importlib.util
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "clawbench-workflow.py"
SPEC = importlib.util.spec_from_file_location("clawbench_workflow", SCRIPT)
assert SPEC and SPEC.loader
workflow_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow_module)


class WorkflowTests(unittest.TestCase):
    def test_default_bench_model_inherits_openclaw(self):
        args = workflow_module.build_parser().parse_args([
            "run", "--owner-id", "197444", "--domain-id", "test_shanzong",
        ])
        self.assertEqual(args.model, "")

    def test_direct_cli_builds_bench_config_and_internal_files(self):
        with tempfile.TemporaryDirectory() as temp:
            work_dir = Path(temp) / "bench-run"
            args = workflow_module.build_parser().parse_args([
                "run", "--owner-id", "197444", "--domain-id", "test_shanzong",
                "--template", "task_00_sanity@1", "--model", "antchat/GLM-5",
                "--work-dir", str(work_dir), "--no-report",
            ])
            config, state_path, result_path = workflow_module.direct_config(args)
            self.assertEqual(config["identity"], {"ownerId": "197444"})
            self.assertEqual(config["bench"]["pinnedTemplates"], [
                {"templateName": "task_00_sanity", "templateVersion": 1},
            ])
            self.assertNotIn("evolveTaskId", config["identity"])
            self.assertEqual(state_path, work_dir / "workflow_state.json")
            self.assertEqual(result_path, work_dir / "workflow_result.json")
            self.assertTrue((work_dir / "workflow_input.json").is_file())

    def test_direct_cli_resolves_and_reuses_default_bench_context(self):
        with tempfile.TemporaryDirectory() as temp:
            bench_root = Path(temp) / "bench"
            parser = workflow_module.build_parser()
            first_args = parser.parse_args([
                "run", "--owner-id", "197444", "--domain-id", "blog",
                "--work-dir", str(bench_root / "round-1"),
            ])
            second_args = parser.parse_args([
                "run", "--owner-id", "197444", "--domain-id", "blog",
                "--work-dir", str(bench_root / "round-2"),
            ])
            frozen = [{"templateName": "write-blog", "templateVersion": 3}]
            with mock.patch.object(workflow_module, "resolve_published_templates", return_value=frozen) as resolve:
                first, _, _ = workflow_module.direct_config(first_args)
                second, _, _ = workflow_module.direct_config(second_args)
            self.assertEqual(resolve.call_count, 1)
            self.assertEqual(first["bench"]["pinnedTemplates"], frozen)
            self.assertEqual(second["bench"]["pinnedTemplates"], frozen)
            context = json.loads((bench_root / "bench_context.json").read_text(encoding="utf-8"))
            self.assertEqual(context["templates"], frozen)

    def test_direct_cli_template_scope_requires_one_template(self):
        with tempfile.TemporaryDirectory() as temp:
            args = workflow_module.build_parser().parse_args([
                "run", "--owner-id", "u1", "--domain-id", "blog",
                "--template", "a@1", "--template", "b@2", "--run-scope", "template",
                "--work-dir", str(Path(temp) / "run"),
            ])
            with self.assertRaisesRegex(workflow_module.WorkflowError, "exactly one"):
                workflow_module.direct_config(args)

    def test_missing_published_templates_is_non_retryable(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = Path(temp) / "scripts" / "clawmind_adapter.py"
            adapter.parent.mkdir(parents=True)
            adapter.write_text("# adapter\n", encoding="utf-8")
            completed = workflow_module.subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout=json.dumps({
                    "status": "failed",
                    "error": "No published templates found for domain",
                }),
                stderr="No published templates found for domain",
            )
            with mock.patch.object(
                workflow_module.subprocess, "run", return_value=completed
            ):
                with self.assertRaises(workflow_module.WorkflowError) as raised:
                    workflow_module.resolve_published_templates(
                        Path(temp), "owner-1", "domain-1", "https://example.test"
                    )

            self.assertEqual(raised.exception.code, "TEMPLATE_RESOLVE_FAILED")
            self.assertFalse(raised.exception.retryable)

    def test_direct_initialization_failure_writes_workflow_result(self):
        with tempfile.TemporaryDirectory() as temp:
            work_dir = Path(temp) / "bench-run"
            argv = [
                "clawbench-workflow.py",
                "run",
                "--owner-id",
                "owner-1",
                "--domain-id",
                "domain-1",
                "--work-dir",
                str(work_dir),
            ]
            failure = workflow_module.WorkflowError(
                "TEMPLATE_RESOLVE_FAILED",
                "No published templates found for domain",
                retryable=False,
            )
            with mock.patch.object(workflow_module.sys, "argv", argv), mock.patch.object(
                workflow_module, "direct_config", side_effect=failure
            ):
                exit_code = workflow_module.main()

            self.assertEqual(exit_code, 1)
            result = json.loads(
                (work_dir / "workflow_result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result["error"]["code"], "TEMPLATE_RESOLVE_FAILED")
            self.assertFalse(result["error"]["retryable"])

    def test_direct_run_does_not_write_evolve_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            config["identity"] = {"ownerId": "197444"}
            flow = workflow_module.Workflow(config, root / "state.json", root / "result.json")
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'{"benchRunId":"bench-1"}'
            with mock.patch.object(workflow_module.urllib.request, "urlopen", return_value=response) as urlopen:
                flow.create_run("blog", config["bench"], {
                    "runScope": "domain", "templateCount": 1, "templates": [],
                })
            payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
            self.assertNotIn("evolveTaskId", payload["runConfig"])
            self.assertNotIn("evolveStepId", payload["runConfig"])

    def test_create_run_logs_http_error_body(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            flow = workflow_module.Workflow(config, root / "state.json", root / "result.json")
            error = urllib.error.HTTPError("https://example.test/api/bench/runs", 422, "Unprocessable Entity", {}, None)
            error.read = mock.Mock(return_value=b'{"error":"owner mismatch"}')
            with mock.patch.object(workflow_module.urllib.request, "urlopen", side_effect=error):
                with self.assertRaisesRegex(workflow_module.WorkflowError, "owner mismatch"):
                    flow.create_run("blog", config["bench"], {"runScope": "domain", "templateCount": 1, "templates": []})
            self.assertIn("owner mismatch", flow.log_path.read_text(encoding="utf-8"))

    def test_create_run_uses_explicit_template_owner_and_plain_domain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            config["identity"]["ownerId"] = "197444"
            flow = workflow_module.Workflow(config, root / "state.json", root / "result.json")
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'{"benchRunId":"bench-1","detailUrl":"/bench/runs/bench-1"}'
            with mock.patch.object(workflow_module.urllib.request, "urlopen", return_value=response) as urlopen:
                result = flow.create_run("test_shanzong", config["bench"], {
                    "runScope": "domain", "templateCount": 1,
                    "templates": [{"taskId": "t", "templateName": "x", "templateVersion": 1}],
                })
            request = urlopen.call_args.args[0]
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual(payload["ownerId"], "197444")
            self.assertEqual(payload["domainId"], "test_shanzong")
            self.assertNotIn("x-user-id", {key.lower(): value for key, value in request.header_items()})
            self.assertEqual(payload["runConfig"]["evolveTaskId"], "EV-1")
            self.assertEqual(result["benchRunId"], "bench-1")

    def test_loads_and_merges_exact_pinned_template_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            config["bench"]["pinnedTemplates"] = [
                {"templateName": "a", "templateVersion": 2},
                {"templateName": "b", "templateVersion": 4},
            ]
            flow = workflow_module.Workflow(config, root / "state.json", root / "result.json")
            outputs = []
            for index, (name, version) in enumerate((("a", 2), ("b", 4))):
                runtime = root / f"runtime-{index}"
                runtime.mkdir()
                task_file = runtime / f"task_{name}.md"
                task_file.write_text(name, encoding="utf-8")
                outputs.append({
                    "runStamp": f"stamp-{index}", "runtimeDir": str(runtime),
                    "benchmarkDir": f"runtime-{index}",
                    "templates": [{"taskId": f"task_{name}", "templateName": name, "templateVersion": version}],
                    "taskFiles": [{"taskId": f"task_{name}", "templateName": name, "templateVersion": version, "path": str(task_file)}],
                })
            with mock.patch.object(flow, "adapter_action", side_effect=outputs) as adapter:
                loaded = flow.load_templates("blog", config["bench"])
            self.assertEqual(loaded["runScope"], "domain")
            self.assertEqual(loaded["templateCount"], 2)
            self.assertTrue((Path(loaded["runtimeDir"]) / "task_b.md").is_file())
            self.assertEqual(adapter.call_args_list[1].args[1]["TEMPLATE_VERSION"], 4)

    def test_adapter_rejects_failed_json_with_zero_exit_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner = mock.Mock(return_value=workflow_module.subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"status":"failed","error":"benchmark failed","logPath":"/tmp/run_agentbench.log"}\n', stderr="",
            ))
            flow = workflow_module.Workflow(
                self.config(root), root / "state.json", root / "result.json", command_runner=runner,
            )
            with self.assertRaises(workflow_module.WorkflowError) as raised:
                flow.adapter_action("run-agentbench", {})
            self.assertIn("benchmark failed", str(raised.exception))
            self.assertEqual(raised.exception.output["logPath"], "/tmp/run_agentbench.log")

    def test_adapter_requires_entire_stdout_to_be_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runner = mock.Mock(return_value=workflow_module.subprocess.CompletedProcess(
                args=[], returncode=0, stdout='diagnostic\n{"status":"ok"}\n', stderr="",
            ))
            flow = workflow_module.Workflow(
                self.config(root), root / "state.json", root / "result.json", command_runner=runner,
            )
            with self.assertRaises(workflow_module.WorkflowError) as raised:
                flow.adapter_action("load-template", {})
            self.assertEqual(raised.exception.code, "ADAPTER_INVALID_OUTPUT")

    def test_generate_report_uses_skill_agent_isolated_session_and_full_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            skill_dir = root / "clawbench-report"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# report", encoding="utf-8")
            config["report"] = {"enabled": True, "skillDir": str(skill_dir), "agentId": "report-agent"}
            calls = []

            def runner(argv, **kwargs):
                calls.append((argv, kwargs))
                if argv[1:3] == ["agents", "list"]:
                    return workflow_module.subprocess.CompletedProcess(argv, 0, "- report-agent\n", "")
                payload = {"payloads": [{"role": "assistant", "text": json.dumps({
                    "reportMarkdown": "# Report", "reportSummary": "ok",
                    "riskLevel": "low", "recommendations": ["next"],
                })}]}
                return workflow_module.subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

            flow = workflow_module.Workflow(config, root / "state.json", root / "result.json", command_runner=runner)
            prepared = {
                "benchRunId": "bench-1", "detailUrl": "https://example.test/bench-1",
                "resultPath": "/tmp/result.json", "outputDir": "/tmp/output",
                "contextPath": "/tmp/context.json", "reportPromptPath": "/tmp/prompt.md",
                "reportTemplatePath": "/tmp/template.md",
            }
            result = flow.generate_report(prepared, {"benchRunId": "bench-1"})
            agent_argv = calls[-1][0]
            self.assertIn("--agent", agent_argv)
            report_agent_id = agent_argv[agent_argv.index("--agent") + 1]
            self.assertTrue(report_agent_id.startswith("report-agent-"))
            add_argv = next(argv for argv, _ in calls if argv[1:3] == ["agents", "add"])
            self.assertEqual(add_argv[3], report_agent_id)
            self.assertEqual(
                add_argv[add_argv.index("--workspace") + 1],
                str(root / "report-agent-workspace"),
            )
            self.assertIn("--session-id", agent_argv)
            message = agent_argv[agent_argv.index("--message") + 1]
            for expected in prepared.values():
                self.assertIn(expected, message)
            self.assertIn("Skill 入口", message)
            self.assertEqual(result["reportMarkdown"], "# Report")
            self.assertEqual(str(workflow_module.uuid.UUID(result["childSessionKey"])), result["childSessionKey"])
            session_id = agent_argv[agent_argv.index("--session-id") + 1]
            self.assertEqual(session_id, result["childSessionKey"])

    def test_generate_report_adds_evolve_task_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            config["identity"]["evolveTaskId"] = "EV-20260828-ABCDEF12"
            skill_dir = root / "clawbench-report"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# report", encoding="utf-8")
            config["report"] = {"enabled": True, "skillDir": str(skill_dir)}
            calls = []

            def runner(argv, **kwargs):
                calls.append((argv, kwargs))
                if argv[1:3] == ["agents", "list"]:
                    return workflow_module.subprocess.CompletedProcess(argv, 0, "", "")
                payload = {"payloads": [{"role": "assistant", "text": "# Report"}]}
                return workflow_module.subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

            flow = workflow_module.Workflow(config, root / "state.json", root / "result.json", command_runner=runner)
            prepared = {
                "benchRunId": "bench-1", "detailUrl": "https://example.test/bench-1",
                "resultPath": "/tmp/result.json", "outputDir": "/tmp/output",
                "contextPath": "/tmp/context.json", "reportPromptPath": "/tmp/prompt.md",
                "reportTemplatePath": "/tmp/template.md",
            }
            flow.generate_report(prepared, {"benchRunId": "bench-1"})
            add_argv = next(argv for argv, _ in calls if argv[1:3] == ["agents", "add"])
            self.assertIn("ev-20260828-abcdef12", add_argv[3])

    def test_base_env_forwards_evolve_task_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            config["identity"]["evolveTaskId"] = "EV-20260828-ABCDEF12"
            flow = workflow_module.Workflow(config, root / "state.json", root / "result.json")
            self.assertEqual(flow.base_env()["CLAWEVOLVE_TASK_ID"], "EV-20260828-ABCDEF12")

    def test_generate_report_uses_plain_markdown_assistant_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            skill_dir = root / "clawbench-report"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# report", encoding="utf-8")
            config["report"] = {"enabled": True, "skillDir": str(skill_dir), "agentId": "report-agent"}

            def runner(argv, **kwargs):
                if argv[1:3] == ["agents", "list"]:
                    return workflow_module.subprocess.CompletedProcess(argv, 0, "- report-agent\n", "")
                return workflow_module.subprocess.CompletedProcess(
                    argv, 0, json.dumps({
                        "payloads": [{"role": "assistant", "text": "# Plain report\n\nDetails"}],
                        "meta": {"executionTrace": {"attempts": [{"result": "success"}]}},
                    }), "",
                )

            flow = workflow_module.Workflow(config, root / "state.json", root / "result.json", command_runner=runner)
            result = flow.generate_report({"benchRunId": "bench-1"}, {"benchRunId": "bench-1"})
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["reportMarkdown"], "# Plain report\n\nDetails")
            self.assertNotIn("output", result)

    def test_generate_report_creates_missing_agent_with_bench_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            skill_dir = root / "clawbench-report"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# report", encoding="utf-8")
            config["report"] = {"enabled": True, "skillDir": str(skill_dir), "agentId": "report-agent"}
            calls = []

            def runner(argv, **kwargs):
                calls.append(argv)
                if argv[1:3] == ["agents", "list"]:
                    return workflow_module.subprocess.CompletedProcess(argv, 0, "", "")
                if argv[1:3] == ["agents", "add"]:
                    return workflow_module.subprocess.CompletedProcess(argv, 0, "created", "")
                return workflow_module.subprocess.CompletedProcess(
                    argv, 0, json.dumps({"payloads": [{"role": "assistant", "text": "{}"}]}), "",
                )

            flow = workflow_module.Workflow(config, root / "state.json", root / "result.json", command_runner=runner)
            flow.generate_report({"benchRunId": "bench-1"}, {"benchRunId": "bench-1"})
            add_argv = next(argv for argv in calls if argv[1:3] == ["agents", "add"])
            self.assertEqual(add_argv[add_argv.index("--model") + 1], "m")

    def test_secret_reference_is_resolved_only_from_environment(self):
        with mock.patch.dict(os.environ, {"TEST_JUDGE_SECRET": "secret-value"}, clear=False):
            self.assertEqual(workflow_module.resolve_env_ref("env:TEST_JUDGE_SECRET"), "secret-value")
        self.assertEqual(workflow_module.resolve_env_ref("secret-value"), "")

    def config(self, root: Path) -> dict:
        skill = root / "clawbench-base"
        (skill / "scripts").mkdir(parents=True)
        (skill / "scripts/clawmind_adapter.py").write_text("# test\n", encoding="utf-8")
        return {
            "identity": {"ownerId": "u1", "evolveTaskId": "EV-1", "evolveStepId": "STEP-1"},
            "bench": {"domainId": "blog", "model": "m", "suite": "all", "scene": "test", "pinnedTemplates": [{"templateName": "x", "templateVersion": 1}]},
            "endpoints": {"clawwebUrl": "https://example.test"},
            "runtime": {
                "agentbenchHome": str(skill), "taskRoot": str(root / "task"),
                "inputDir": str(root / "task/input"), "outputDir": str(root / "task/output"),
                "logDir": str(root / "task/logs"),
            },
            "report": {"enabled": False},
        }

    def test_runs_phases_and_reuses_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            flow = workflow_module.Workflow(self.config(root), root / "state.json", root / "result.json")
            loaded = {"status": "ok", "runScope": "domain", "runStamp": "stamp", "domainId": "blog", "templateCount": 1, "templates": [{"taskId": "t", "templateName": "x", "templateVersion": 1}], "benchmarkDir": "runtime"}
            created = {"status": "ok", "benchRunId": "bench-1", "detailUrl": "https://example.test/bench/1"}
            outputs = {
                "run-agentbench": {"status": "succeeded", "resultPath": "/tmp/report.json", "logPath": "/tmp/run_agentbench.log", "startedAt": 1, "completedAt": 2, "exitCode": 0},
                "upload-results": {"status": "ok", "benchRunId": "bench-1", "taskCount": 1, "score": 6, "maxScore": 10, "passRate": 0.6},
                "prepare-report": {"status": "ok", "benchRunId": "bench-1"},
                "summarize": {"status": "succeeded", "benchRunId": "bench-1", "detailUrl": "https://example.test/bench/1"},
            }
            with mock.patch.object(flow, "load_templates", return_value=loaded), \
                    mock.patch.object(flow, "create_run", return_value=created), \
                    mock.patch.object(flow, "adapter_action", side_effect=lambda action, env: outputs[action]) as adapter:
                first = flow.run()
                second = flow.run()
            self.assertEqual(first["metrics"]["scoreRatio"], 0.6)
            self.assertEqual(first["logPath"], "/tmp/run_agentbench.log")
            self.assertEqual(second["benchRunId"], "bench-1")
            self.assertEqual(adapter.call_count, 4)
            run_env = next(call.args[1] for call in adapter.call_args_list if call.args[0] == "run-agentbench")
            self.assertEqual(run_env["INPUT_DIR"], str(root / "task/input"))
            self.assertEqual(run_env["OPENCLAW_EXECUTION_MODE"], "local")

    def test_rejects_changed_input_for_existing_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            workflow_module.Workflow(config, root / "state.json", root / "result.json")
            config["bench"]["domainId"] = "changed"
            with self.assertRaises(workflow_module.WorkflowError) as raised:
                workflow_module.Workflow(config, root / "state.json", root / "result.json")
            self.assertEqual(raised.exception.code, "INPUT_CHANGED")

    def test_uploads_and_summarizes_when_benchmark_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            flow = workflow_module.Workflow(self.config(root), root / "state.json", root / "result.json")
            loaded = {"runScope": "domain", "runStamp": "stamp", "templateCount": 1, "templates": [{"taskId": "t", "templateName": "x", "templateVersion": 1}], "benchmarkDir": "runtime"}
            created = {"benchRunId": "bench-failed", "detailUrl": "https://example.test/bench/failed"}
            calls = []
            benchmark_attempts = 0

            def action(name, env):
                nonlocal benchmark_attempts
                calls.append(name)
                if name == "run-agentbench":
                    benchmark_attempts += 1
                    if benchmark_attempts == 1:
                        raise workflow_module.WorkflowError("ADAPTER_FAILED", "benchmark exited 1")
                    return {"status": "succeeded", "startedAt": 1, "completedAt": 2}
                if name == "upload-results":
                    return {"status": "ok", "benchRunId": "bench-failed"}
                if name == "prepare-report":
                    return {"benchRunId": "bench-failed"}
                if name == "summarize":
                    return {"status": "failed", "benchRunId": "bench-failed"}
                raise AssertionError(name)

            with mock.patch.object(flow, "load_templates", return_value=loaded), \
                    mock.patch.object(flow, "create_run", return_value=created), \
                    mock.patch.object(flow, "adapter_action", side_effect=action):
                result = flow.run()
                retried = flow.run()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"]["code"], "BENCHMARK_FAILED")
            self.assertEqual(retried["status"], "succeeded")
            self.assertEqual(calls.count("upload-results"), 2)
            self.assertIn("prepare-report", calls)
            self.assertIn("summarize", calls)

    def test_failed_upload_output_cannot_produce_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            flow = workflow_module.Workflow(self.config(root), root / "state.json", root / "result.json")
            loaded = {"runScope": "domain", "runStamp": "stamp", "templateCount": 1, "templates": [{"taskId": "t", "templateName": "x", "templateVersion": 1}], "benchmarkDir": "runtime"}
            created = {"benchRunId": "bench-1", "detailUrl": "https://example.test/bench/1"}
            outputs = {
                "run-agentbench": {"status": "succeeded"},
                "upload-results": {"status": "failed", "error": "report missing"},
                "prepare-report": {"benchRunId": "bench-1"},
                "summarize": {"status": "failed", "benchRunId": "bench-1"},
            }
            with mock.patch.object(flow, "load_templates", return_value=loaded), \
                    mock.patch.object(flow, "create_run", return_value=created), \
                    mock.patch.object(flow, "adapter_action", side_effect=lambda action, env: outputs[action]):
                result = flow.run()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"]["code"], "UPLOAD_RESULTS_FAILED")

    def test_upload_failure_skips_prepare_and_generate_but_runs_summarize(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            flow = workflow_module.Workflow(self.config(root), root / "state.json", root / "result.json")
            loaded = {"runScope": "domain", "runStamp": "stamp", "templateCount": 1,
                      "templates": [{"taskId": "t", "templateName": "x", "templateVersion": 1}],
                      "benchmarkDir": "runtime"}
            created = {"benchRunId": "bench-1", "detailUrl": "https://example.test/bench/1"}
            calls = []

            def action(name, env):
                calls.append((name, env))
                if name == "run-agentbench":
                    return {"status": "succeeded"}
                if name == "upload-results":
                    raise workflow_module.WorkflowError("ADAPTER_FAILED", "upload failed")
                if name == "summarize":
                    return {"status": "succeeded", "benchRunId": "bench-1"}
                raise AssertionError(name)

            with mock.patch.object(flow, "load_templates", return_value=loaded), \
                    mock.patch.object(flow, "create_run", return_value=created), \
                    mock.patch.object(flow, "adapter_action", side_effect=action):
                result = flow.run()
            self.assertEqual([name for name, _ in calls], ["run-agentbench", "upload-results", "summarize"])
            summarize_env = calls[-1][1]
            self.assertIn("upload failed", summarize_env["REPORT_ERROR"])
            self.assertEqual(flow.state["phases"]["prepare_report"]["status"], "skipped")
            self.assertEqual(flow.state["phases"]["generate_report"]["status"], "skipped")
            self.assertEqual(result["error"]["code"], "UPLOAD_RESULTS_FAILED")

    def test_report_raw_output_and_child_session_are_passed_to_summarize(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            flow = workflow_module.Workflow(self.config(root), root / "state.json", root / "result.json")
            loaded = {"runScope": "domain", "runStamp": "stamp", "templateCount": 1,
                      "templates": [{"taskId": "t", "templateName": "x", "templateVersion": 1}],
                      "benchmarkDir": "runtime"}
            created = {"benchRunId": "bench-1", "detailUrl": "https://example.test/bench/1"}
            summarize_env = {}

            def action(name, env):
                if name == "run-agentbench":
                    return {"status": "succeeded"}
                if name == "upload-results":
                    return {"status": "ok"}
                if name == "prepare-report":
                    return {"benchRunId": "bench-1", "reportPromptPath": "/tmp/prompt", "reportCompactPath": "/tmp/compact"}
                if name == "summarize":
                    summarize_env.update(env)
                    return {"status": "succeeded", "benchRunId": "bench-1"}
                raise AssertionError(name)

            with mock.patch.object(flow, "load_templates", return_value=loaded), \
                    mock.patch.object(flow, "create_run", return_value=created), \
                    mock.patch.object(flow, "generate_report", return_value={
                        "status": "succeeded", "output": "raw assistant", "childSessionKey": "child-1",
                    }), mock.patch.object(flow, "adapter_action", side_effect=action):
                result = flow.run()
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(summarize_env["REPORT_OUTPUT"], "raw assistant")
            self.assertEqual(summarize_env["REPORT_CHILD_SESSION_KEY"], "child-1")
            self.assertEqual(summarize_env["REPORT_PROMPT_PATH"], "/tmp/prompt")

    def test_version_check_uses_bundle_release_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            marker = Path(config["runtime"]["agentbenchHome"]).parent / ".clawevolve-release-version"
            marker.write_text("clawevolve-20260915-v2\n", encoding="utf-8")
            flow = workflow_module.Workflow(config, root / "state.json", root / "result.json")
            self.assertEqual(
                flow.check_version(),
                {"status": "ok", "source": "bundle", "releaseVersion": "clawevolve-20260915-v2"},
            )

    def test_version_check_accepts_source_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.config(root)
            flow = workflow_module.Workflow(config, root / "state.json", root / "result.json")
            self.assertEqual(
                flow.check_version(),
                {"status": "ok", "source": "source", "releaseVersion": ""},
            )


if __name__ == "__main__":
    unittest.main()
