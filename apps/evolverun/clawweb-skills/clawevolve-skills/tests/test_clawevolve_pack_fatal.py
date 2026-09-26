import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py"


class PackFatalTest(unittest.TestCase):
    def test_round_one_pack_failure_marks_promotion_failed_and_requires_restore(self):
        spec = importlib.util.spec_from_file_location("cev_pack_fatal", WORKFLOW)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory(prefix="cev-pack-fatal-") as root:
            workspace = Path(root) / "workspace"
            acceptance = workspace / "clawevolve_results/T/optimize/output/round-001/acceptance"
            acceptance.mkdir(parents=True)
            (acceptance / "acceptance_report.json").write_text(
                json.dumps({
                    "accepted": False,
                    "bench_decision": "passed",
                    "decision": "passed",
                    "promotion_status": "pending",
                    "restore_required": False,
                }) + "\n",
                encoding="utf-8",
            )
            round_dir = acceptance.parent
            (round_dir / "round_state.json").write_text(
                json.dumps({"candidate_mutation_state": "applied"}) + "\n",
                encoding="utf-8",
            )

            args = type("Args", (), {
                "task_id": "T",
                "round": 1,
                "workspace": str(workspace),
                "skill_base_dir": str(ROOT),
            })()
            result = module._fallback_pack_failed_degraded(args, {"message": "pack failed"})
            self.assertTrue(result["ok"])
            self.assertEqual(result["promotion_status"], "failed")
            self.assertTrue(result["restore_required"])
            report = json.loads((acceptance / "acceptance_report.json").read_text())
            self.assertFalse(report["accepted"])
            self.assertEqual(report["bench_decision"], "passed")
            self.assertEqual(report["promotion_status"], "failed")
            self.assertTrue(report["restore_required"])


if __name__ == "__main__":
    unittest.main()
