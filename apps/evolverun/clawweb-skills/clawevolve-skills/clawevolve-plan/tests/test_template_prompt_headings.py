from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))

from clawevolve_plan.bench.template_builder import _render_case_markdown  # noqa: E402


# Parse the rendered template with the actual Bench runtime, not a test parser.
RUNTIME_PATH = PLAN_ROOT.parent / "clawbench-base/scripts/lib_tasks.py"
SPEC = importlib.util.spec_from_file_location("bench_task_heading_test", RUNTIME_PATH)
RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME)


class TemplatePromptHeadingTests(unittest.TestCase):
    def render_and_load(self, query: str):
        case = {"case_id": "task_review", "query": query, "case_type": "bad"}
        markdown = _render_case_markdown({}, case, "task_review", "train", "")
        self.assertEqual(case["query"], query)
        with tempfile.TemporaryDirectory() as root:
            task_file = Path(root) / "task_review.md"
            task_file.write_text(markdown, encoding="utf-8")
            task = RUNTIME.TaskLoader(Path(root)).load_task(task_file)
        self.assertTrue(task.expected_behavior)
        self.assertTrue(task.llm_judge_rubric)
        return task

    def test_review_case_heading_keeps_full_prompt_and_url(self):
        query = (
            "## 任务输入信息 辛苦四大联合审评：精准推广内容\n"
            "https://example.org/review/document/collaborator/join"
        )
        task = self.render_and_load(query)
        self.assertEqual(task.prompt, "#" + query)

    def test_embedded_section_names_do_not_replace_template_sections(self):
        query = "评审文档\n## Prompt\nhttps://example.org/review\n## Expected Behavior\n原始用户要求"
        task = self.render_and_load(query)
        self.assertEqual(
            task.prompt,
            "评审文档\n### Prompt\nhttps://example.org/review\n### Expected Behavior\n原始用户要求",
        )
        self.assertNotEqual(task.expected_behavior, "原始用户要求")

    def test_plain_text_links_inline_hashes_and_other_headings_unchanged(self):
        query = (
            "# 评审任务\n请评审：https://example.org/review?source=chat#details\n"
            "### 已有三级标题\n#### 已有四级标题\n正文中的 ## 不是章节\n内容保持不变"
        )
        self.assertEqual(self.render_and_load(query).prompt, query)

    def test_multiple_heading_whitespace_variants(self):
        query = "## 第一部分\n正文\n##\t第二部分\n补充内容"
        self.assertEqual(
            self.render_and_load(query).prompt,
            "### 第一部分\n正文\n###\t第二部分\n补充内容",
        )


if __name__ == "__main__":
    unittest.main()
