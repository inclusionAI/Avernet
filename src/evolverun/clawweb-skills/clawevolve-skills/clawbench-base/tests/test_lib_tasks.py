from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from lib_tasks import TaskLoader  # noqa: E402


class TaskLoaderTests(unittest.TestCase):
    def test_load_task_trims_section_heading_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_file = Path(temp_dir) / "task_01_example.md"
            task_file.write_text(
                """---
id: task_01_example
name: Example
category: smoke
grading_type: hybrid
timeout_seconds: 120
workspace_files: []
---

## Prompt

请只回复 OK。

## Expected Behavior

The agent should reply OK.
""",
                encoding="utf-8",
            )

            task = TaskLoader(Path(temp_dir)).load_task(task_file)

            self.assertEqual(task.prompt, "请只回复 OK。")


if __name__ == "__main__":
    unittest.main()
