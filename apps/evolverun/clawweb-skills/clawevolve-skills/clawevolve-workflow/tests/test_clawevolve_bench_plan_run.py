import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/handlers/clawevolve_bench_plan_run.py"
SPEC = importlib.util.spec_from_file_location("clawevolve_bench_plan_run", SCRIPT)
assert SPEC and SPEC.loader
handler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handler)


class BenchPlanHandlerTests(unittest.TestCase):
    def test_parser_requires_train_and_test_domains(self):
        parser = handler.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--task-id", "EV-1"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["--task-id", "EV-1", "--train-domain-id", "train"])
        args = parser.parse_args(["--task-id", "EV-1", "--train-domain-id", "train", "--test-domain-id", "test"])
        self.assertEqual((args.train_domain_id, args.test_domain_id), ("train", "test"))

    def test_generates_bench_driven_scaffold(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            workspace = temp / "workspace"
            source = temp / "templates"
            source.mkdir(parents=True)
            for idx in range(1, 5):
                (source / f"task_{idx:02d}_demo.md").write_text(
                    f"---\nid: task_{idx:02d}_demo\ncategory: cat{idx % 2}\nname: demo {idx}\n---\nbody\n",
                    encoding="utf-8",
                )
            (source / "README.md").write_text("shared helper", encoding="utf-8")

            def fake_bench(**kwargs):
                work_dir = Path(kwargs["work_dir"])
                self.assertEqual(work_dir.parts[-2:], ("local", "train" if kwargs["domain_id"] == "blog-train" else "test"))
                input_dir = work_dir / "input"
                input_dir.mkdir(parents=True, exist_ok=True)
                for item in source.iterdir():
                    if item.is_file():
                        (input_dir / item.name).write_bytes(item.read_bytes())
                score = 0.81 if kwargs["domain_id"] == "blog-train" else 0.55
                report = {
                        "model": "antchat/GLM-5",
                        "benchmark_version": "1.2.1",
                        "suite": "all",
                        "summary": {"score": score},
                        "tasks": [
                            {"task_id": "task_01_demo", "score": 0.40, "grading": {"breakdown": {"reason": 0.50, "format": 0.90}}},
                            {"task_id": "task_02_demo", "score": 1.00, "grading": {"breakdown": {"reason": 1.00, "format": 1.00}}},
                            {"task_id": "task_03_demo", "score": 0.60, "grading": {"breakdown": {"reason": 0.60, "format": 0.70}}},
                            {"task_id": "task_04_demo", "score": 0.95, "grading": {"breakdown": {"reason": 1.00, "format": 0.90}}},
                        ],
                }
                report_path = work_dir / "output/fake_benchmark_report.json"
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(report), encoding="utf-8")
                return {"status": "succeeded", "benchRunId": f"bench-{kwargs['domain_id']}", "domainId": kwargs["domain_id"], "metrics": {"score": score}, "inputPath": str(input_dir), "resultPath": str(report_path)}

            argv = [
                str(SCRIPT),
                "--task-id", "Demo Run 1",
                "--train-domain-id", "blog-train", "--test-domain-id", "blog-test", "--owner-id", "197444",
                "--objective", "提升博客质量",
                "--workspace", str(workspace),
            ]

            with mock.patch.object(handler, "run_clawevolve_bench", side_effect=fake_bench), mock.patch.object(handler.sys, "argv", argv):
                rc = handler.main()

            self.assertEqual(rc, 0)
            run_dir = workspace / "clawevolve_results" / "demo-run-1"
            manifest = json.loads((run_dir / "prepare_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["task_id"], "Demo Run 1")
            self.assertEqual(manifest["source_mode"], "domains")
            self.assertTrue(manifest["validation_independent"])
            self.assertEqual(manifest["validation_mode"], "isolated_train_test")
            self.assertEqual(manifest["benchmark_score"], 0.81)
            self.assertEqual(manifest["opt_count"], 4)
            self.assertEqual(manifest["val_count"], 0)
            self.assertTrue(Path(manifest["test_benchmark_report"]).is_file())
            self.assertEqual(manifest["test_identity"]["bench_model"], "antchat/GLM-5")
            self.assertEqual(manifest["test_identity"]["benchmark_version"], "1.2.1")
            self.assertTrue(manifest["test_identity"]["validation_fixture"]["sha256"])
            self.assertTrue((run_dir / "optimize/input/objective.md").is_file())
            self.assertTrue((run_dir / "optimize/input/spec-v0.md").is_file())
            self.assertTrue((run_dir / "optimize/input/spec-v0.json").is_file())
            self.assertEqual(len(list((run_dir / "plan/output/templates/opt").glob("task_*.md"))), 4)
            self.assertEqual(len(list((run_dir / "plan/output/templates/val").glob("task_*.md"))), 0)
            objective = (run_dir / "optimize/input/objective.md").read_text(encoding="utf-8")
            objective_json = json.loads((run_dir / "optimize/input/objective.json").read_text(encoding="utf-8"))
            self.assertIn("提升博客质量", objective)
            self.assertIn("0.81", objective)
            self.assertIn("0.55", objective)
            self.assertNotIn("0.95", objective)
            self.assertEqual(objective_json["baseline_score"], 0.55)
            self.assertEqual(objective_json["optimization_baseline_score"], 0.81)
            self.assertEqual(objective_json["test_baseline_score"], 0.55)
            self.assertEqual(objective_json["primary_metric"]["name"], "task_success_rate")
            self.assertEqual(objective_json["primary_metric"]["target"], 0.9)
            self.assertIn("任务成功率 >= 90%", objective)
            baseline_results = sorted(run_dir.glob("bench/baseline/local/*/baseline_result.json"))
            self.assertEqual(len(baseline_results), 2)
            persisted = [json.loads(path.read_text(encoding="utf-8")) for path in baseline_results]
            self.assertEqual({item["baselineRole"] for item in persisted}, {"train", "test"})
            self.assertEqual({item["domainOwnerId"] for item in persisted}, {"197444"})
            self.assertEqual({item["benchRunId"] for item in persisted}, {"bench-blog-train", "bench-blog-test"})

            spec = (run_dir / "optimize/input/spec-v0.md").read_text(encoding="utf-8")
            spec_json = json.loads((run_dir / "optimize/input/spec-v0.json").read_text(encoding="utf-8"))
            self.assertIn("schema_version: evolution.spec.v1", spec)
            self.assertEqual(spec_json["schema_version"], "evolution.spec.v1")
            self.assertEqual(spec_json["spec_version"], "v0")
            self.assertEqual(spec_json["acceptance_criteria"]["primary_metric"], objective_json["primary_metric"])
            self.assertIn("任务成功率 >= 90%", spec)

    def test_primary_metric_preserves_explicit_goal_percentage(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "run"
            handler._write_objective_files(
                run_dir, "EV-metric", "MCP 调用成功率达到80%",
                {"overall_score": 0.5}, 3, 2,
                test_summary={"overall_score": 0.4},
            )
            data = json.loads((run_dir / "optimize/input/objective.json").read_text(encoding="utf-8"))
            self.assertEqual(data["primary_metric"]["name"], "mcp_call_success_rate")
            self.assertEqual(data["primary_metric"]["target"], 0.8)
            self.assertIn("MCP 调用成功率 >= 80%", (run_dir / "optimize/input/objective.md").read_text(encoding="utf-8"))

    def test_summarize_bench_reads_run_level_breakdown(self):
        summary = handler._summarize_bench({
            "tasks": [{
                "task_id": "task_legal",
                "status": "success",
                "grading": {"runs": [{
                    "score": 0.4,
                    "breakdown": {"semantic_accuracy": 0.25, "format": 1.0},
                    "notes": "missed core issue",
                }]},
            }]
        })
        self.assertEqual(summary["overall_score"], 0.4)
        self.assertEqual(summary["tasks"][0]["weak_signals"], ["semantic_accuracy=0.25"])
        self.assertEqual(summary["repeated_weak_dimensions"][0]["metric"], "semantic_accuracy")
        self.assertEqual(summary["repeated_weak_dimensions"][0]["case_count"], 1)

    def test_split_preserves_internal_and_external_classification_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            tasks = []
            rows = [
                ("internal_a", "技术改造", "非外部需求类型"),
                ("internal_b", "配置变更", "非外部需求类型"),
                ("internal_c", "对账", "非外部需求类型"),
                ("internal_d", "数据算法", "非外部需求类型"),
                ("external_a", "非内部需求类型", "商户入驻、进件"),
                ("external_b", "非内部需求类型", "前置咨询"),
                ("external_c", "非内部需求类型", "扩展场景"),
            ]
            for name, internal, external in rows:
                path = temp / f"task_{name}.md"
                path.write_text(
                    f"---\nid: task_{name}\ncategory: skill\ngrading_type: automated\n---\n"
                    f"EXPECTED_REQ_TYPE = \"{internal}\"\nEXPECTED_EXTERNAL_REQ_TYPE = \"{external}\"\n",
                    encoding="utf-8",
                )
                tasks.append((path, handler._task_frontmatter(path)))

            opt, val = handler._split_tasks(tasks, 0.7, seed=42, min_val=1)
            opt_strata = {handler._task_behavior_stratum(path, fm) for path, fm in opt}
            val_strata = {handler._task_behavior_stratum(path, fm) for path, fm in val}
            self.assertTrue(any(x.endswith("::internal") for x in opt_strata))
            self.assertTrue(any(x.endswith("::external") for x in opt_strata))
            self.assertTrue(any(x.endswith("::internal") for x in val_strata))
            self.assertTrue(any(x.endswith("::external") for x in val_strata))

    def test_objective_records_aggregate_gap_and_coverage_risk(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "run"
            handler._write_objective_files(
                run_dir, "EV-test", "测试集不低于0.9",
                {"overall_score": 1.0}, 1, 1,
                test_summary={"overall_score": 0.5},
            )
            data = json.loads((run_dir / "optimize/input/objective.json").read_text(encoding="utf-8"))
            self.assertEqual(data["aggregate_generalization_gap"], 0.5)
            self.assertTrue(data["optimization_coverage_risk"])
            text = (run_dir / "optimize/input/objective.md").read_text(encoding="utf-8")
            self.assertIn("optimization coverage risk: True", text)

    def test_preserves_protocol_task_id_case(self):
        args = handler.build_parser().parse_args([
            "--task-id", "EV-20260812-ABCDEF01", "--train-domain-id", "train",
            "--test-domain-id", "test", "--objective", "goal",
        ])
        self.assertEqual(args.task_id, "EV-20260812-ABCDEF01")

    def test_invokes_optimizer_runner_after_prepare(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            workspace = temp / "workspace"
            source = temp / "templates"
            source.mkdir(parents=True)
            for idx in range(1, 3):
                (source / f"task_{idx:02d}_demo.md").write_text(
                    f"---\nid: task_{idx:02d}_demo\ncategory: cat{idx}\nname: demo {idx}\n---\nbody\n",
                    encoding="utf-8",
                )

            calls = []

            def fake_bench(**kwargs):
                work_dir = Path(kwargs["work_dir"])
                input_dir = work_dir / "input"
                input_dir.mkdir(parents=True, exist_ok=True)
                for item in source.iterdir():
                    (input_dir / item.name).write_bytes(item.read_bytes())
                report_path = work_dir / "output/fake_benchmark_report.json"
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps({"summary": {"score": 0.99}, "tasks": [{"task_id": "task_01_demo", "score": 0.99}]}), encoding="utf-8")
                return {"status": "succeeded", "benchRunId": f"bench-{kwargs['domain_id']}", "domainId": kwargs["domain_id"], "metrics": {"score": 0.99}, "inputPath": str(input_dir), "resultPath": str(report_path)}

            def fake_run(cmd, **kwargs):
                calls.append((cmd, kwargs))
                return types.SimpleNamespace(returncode=0, stdout='', stderr='')

            argv = [
                str(SCRIPT),
                "--task-id", "Demo Run 2",
                "--train-domain-id", "blog-train", "--test-domain-id", "blog-test", "--owner-id", "197444",
                "--objective", "提升博客质量",
                "--workspace", str(workspace),
            ]

            with mock.patch.object(handler, "run_clawevolve_bench", side_effect=fake_bench), mock.patch.object(handler.subprocess, "run", side_effect=fake_run), mock.patch.object(handler.sys, "argv", argv):
                rc = handler.main()

            self.assertEqual(rc, 0)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
