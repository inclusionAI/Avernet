import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/calibrate_evolution_gates.py"
SPEC = importlib.util.spec_from_file_location("gate_calibration_test_target", SCRIPT)
CAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAL)


class GateCalibrationTests(unittest.TestCase):
    def test_all_three_gates_exceed_ninety_percent_golden_precision(self):
        suites = [
            CAL.score_cases("static_candidate_gate", CAL.static_cases()),
            CAL.score_cases("candidate_effect_gate", CAL.effect_cases() + CAL.historical_effect_cases(ROOT)),
            CAL.score_cases("full_opt_gate", CAL.full_cases()),
        ]
        required = ("reject_precision", "reject_recall", "pass_precision", "pass_recall", "balanced_accuracy", "accuracy")
        for suite in suites:
            for metric in required:
                self.assertGreaterEqual(suite[metric], 0.90, (suite["gate"], metric, suite))
            self.assertLessEqual(suite["false_reject_rate"], 0.10, suite)


if __name__ == "__main__":
    unittest.main()
