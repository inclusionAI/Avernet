from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from lib_grading import DEFAULT_JUDGE_MODEL, _combine_grades, _grade_automated, _normalize_judge_response, _parse_judge_response, _parse_judge_text, GradeResult  # noqa: E402


class JudgeNormalizationTests(unittest.TestCase):
    def test_default_judge_model_inherits_openclaw(self) -> None:
        self.assertEqual(DEFAULT_JUDGE_MODEL, "")

    def test_normalize_judge_response_averages_summed_total_when_breakdown_is_unit_scale(
        self,
    ) -> None:
        parsed = {
            "scores": {
                "coverage": 0.75,
                "synthesis": 0.75,
                "structure": 0.75,
                "tone": 0.8,
                "conciseness": 0.8,
            },
            "total": 3.85,
            "notes": "Summed by mistake",
        }

        normalized = _normalize_judge_response(parsed)

        self.assertAlmostEqual(normalized["total"], 0.77)

    def test_hybrid_score_uses_normalized_judge_total(self) -> None:
        auto = GradeResult(
            task_id="task_16_email_triage",
            score=0.7062937062937062,
            max_score=1.0,
            grading_type="automated",
            breakdown={},
            notes="",
        )
        judge = GradeResult(
            task_id="task_16_email_triage",
            score=0.87,
            max_score=1.0,
            grading_type="llm_judge",
            breakdown={},
            notes="",
        )

        class _Task:
            task_id = "task_16_email_triage"
            grading_weights = {"automated": 0.4, "llm_judge": 0.6}

        combined = _combine_grades(_Task(), auto, judge)

        self.assertAlmostEqual(combined.score, 0.8045174825174824)

    def test_parse_judge_response_prefers_scored_json_when_multiple_blocks(self) -> None:
        transcript = [
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": '```json\n{"score": 0.70, "verdict": "PASS_WITH_MINOR_ISSUES"}\n```\n```json\n{"scores": {"风险识别准确度": 0.5, "原文证据引用": 1.0}, "total": 0.71, "notes": "scored"}\n```',
                        }
                    ],
                },
            }
        ]

        parsed = _parse_judge_response(transcript)

        self.assertEqual(parsed["total"], 0.71)
        self.assertEqual(parsed["scores"]["风险识别准确度"], 0.5)
        self.assertEqual(parsed["notes"], "scored")

    def test_parse_judge_text_prefers_scored_json_when_multiple_blocks(self) -> None:
        raw_text = '```json\n{"score": 0.70, "verdict": "PASS_WITH_MINOR_ISSUES"}\n```\n```json\n{"scores": {"风险识别准确度": 0.5, "原文证据引用": 1.0}, "total": 0.71, "notes": "api scored"}\n```'

        parsed = _parse_judge_text(raw_text)

        self.assertEqual(parsed["total"], 0.71)
        self.assertEqual(parsed["scores"]["原文证据引用"], 1.0)
        self.assertEqual(parsed["notes"], "api scored")

    def test_automated_grade_accepts_transcript_only_grade_function(self) -> None:
        class _Task:
            task_id = "task_transcript_only"
            automated_checks = """
```python
def grade(transcript):
    return {"has_transcript": 1.0 if transcript else 0.0}
```
"""

        result = _grade_automated(
            _Task(),
            {"transcript": [{"type": "message"}], "workspace": "/unused"},
        )

        self.assertEqual(result.score, 1.0)
        self.assertEqual(result.breakdown["has_transcript"], 1.0)


if __name__ == "__main__":
    unittest.main()
