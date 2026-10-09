from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from lib_agent import _extract_usage_from_transcript  # noqa: E402


class AgentUsageTests(unittest.TestCase):
    def test_extract_usage_ignores_cli_summary_message(self) -> None:
        transcript = [
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "api": "openai-completions",
                    "usage": {
                        "input": 100,
                        "output": 20,
                        "cacheRead": 300,
                        "cacheWrite": 0,
                        "totalTokens": 420,
                        "cost": {"total": 0.1},
                    },
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "api": "openai-completions",
                    "usage": {
                        "input": 50,
                        "output": 10,
                        "cacheRead": 150,
                        "cacheWrite": 0,
                        "totalTokens": 210,
                        "cost": {"total": 0.2},
                    },
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "api": "cli",
                    "usage": {
                        "input": 150,
                        "output": 30,
                        "cacheRead": 450,
                        "cacheWrite": 0,
                        "totalTokens": 210,
                        "cost": {"total": 0.3},
                    },
                },
            },
        ]

        usage = _extract_usage_from_transcript(transcript)

        self.assertEqual(usage["input_tokens"], 150)
        self.assertEqual(usage["output_tokens"], 30)
        self.assertEqual(usage["cache_read_tokens"], 450)
        self.assertEqual(usage["cache_write_tokens"], 0)
        self.assertEqual(usage["total_tokens"], 630)
        self.assertEqual(usage["request_count"], 2)
        self.assertAlmostEqual(usage["cost_usd"], 0.3)


if __name__ == "__main__":
    unittest.main()
