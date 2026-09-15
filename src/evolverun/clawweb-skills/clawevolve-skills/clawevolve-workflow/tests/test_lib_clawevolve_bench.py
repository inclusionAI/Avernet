from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "handlers"
    / "lib_clawevolve_bench.py"
)
SPEC = importlib.util.spec_from_file_location("lib_clawevolve_bench_test", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class ClawEvolveBenchClientTest(unittest.TestCase):
    def _skill_root(self, root: Path) -> Path:
        workflow = root / "clawevolve-bench" / "scripts" / "clawbench-workflow.py"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("# workflow\n", encoding="utf-8")
        return root

    def test_preserves_structured_non_retryable_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work_dir = root / "work"
            skill_root = self._skill_root(root / "skills")

            def fake_run(*_args, **_kwargs):
                result_path = work_dir / "workflow_result.json"
                result_path.parent.mkdir(parents=True, exist_ok=True)
                result_path.write_text(
                    json.dumps({
                        "status": "failed",
                        "error": {
                            "code": "TEMPLATE_RESOLVE_FAILED",
                            "message": "No published templates found for domain",
                            "retryable": False,
                        },
                    }),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(args=[], returncode=1)

            with mock.patch.object(module.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(module.ClawEvolveBenchError) as raised:
                    module.run_clawevolve_bench(
                        owner_id="owner-1",
                        domain_id="domain-1",
                        work_dir=work_dir,
                        model="model-1",
                        workspace=root,
                        skill_base_dir=skill_root,
                    )

            self.assertEqual(raised.exception.code, "TEMPLATE_RESOLVE_FAILED")
            self.assertFalse(raised.exception.retryable)
            self.assertIn("No published templates found", str(raised.exception))

    def test_workflow_resolution_uses_flat_release_root_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            private_root = self._skill_root(root / "clawevolve-skills")
            legacy = root / "workspace/skills/skills-local/clawevolve-bench/scripts"
            legacy.mkdir(parents=True)
            (legacy / "clawbench-workflow.py").write_text("# legacy\n", encoding="utf-8")

            resolved = module._find_workflow(root / "workspace", private_root)

            self.assertEqual(
                resolved,
                (private_root / "clawevolve-bench/scripts/clawbench-workflow.py").resolve(),
            )

    def test_missing_result_includes_inner_log_tail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work_dir = root / "work"
            skill_root = self._skill_root(root / "skills")

            def fake_run(*_args, **kwargs):
                kwargs["stdout"].write("inner bench failure\n")
                kwargs["stdout"].flush()
                return subprocess.CompletedProcess(args=[], returncode=1)

            with mock.patch.object(module.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(module.ClawEvolveBenchError) as raised:
                    module.run_clawevolve_bench(
                        owner_id="owner-1",
                        domain_id="domain-1",
                        work_dir=work_dir,
                        model="model-1",
                        workspace=root,
                        skill_base_dir=skill_root,
                    )

            self.assertIn("log_tail=inner bench failure", str(raised.exception))

    def test_passes_openclaw_execution_mode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work_dir = root / "work"
            skill_root = self._skill_root(root / "skills")

            def fake_run(command, **_kwargs):
                result_path = work_dir / "workflow_result.json"
                result_path.parent.mkdir(parents=True, exist_ok=True)
                result_path.write_text(json.dumps({
                    "status": "succeeded", "benchRunId": "bench-1", "domainId": "domain-1",
                    "metrics": {}, "inputPath": "/tmp/input", "resultPath": "/tmp/result.json",
                    "logPath": "/tmp/run_agentbench.log",
                }), encoding="utf-8")
                self.assertEqual(command[command.index("--openclaw-execution-mode") + 1], "gateway")
                return subprocess.CompletedProcess(args=command, returncode=0)

            with mock.patch.object(module.subprocess, "run", side_effect=fake_run):
                result = module.run_clawevolve_bench(
                    owner_id="owner-1", domain_id="domain-1", work_dir=work_dir,
                    model="model-1", workspace=root, skill_base_dir=skill_root,
                    openclaw_execution_mode="gateway",
                )
            self.assertEqual(result["logPath"], "/tmp/run_agentbench.log")


if __name__ == "__main__":
    unittest.main()
