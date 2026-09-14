from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))

from clawevolve_plan.pipeline.spec_flow import build_and_write_spec  # noqa: E402
from clawevolve_plan.spec.contract import (  # noqa: E402
    validate_objective_markdown,
    validate_spec_markdown,
)
from clawevolve_plan.spec.renderer import render_markdown  # noqa: E402


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        goal="把读取 {baseDir} 的流程改造成稳定的文档生成能力",
        target=["skills/skills-local/example/SKILL.md"],
        task_id="EV-DOC-1",
    )


def _objective_markdown(goal: str = "稳定生成结构化文档，并读取 {baseDir} 中的输入。") -> str:
    return "\n".join([
        "# clawEvolve Objective",
        "",
        "## 目标",
        goal,
        "",
        "## 质量门禁",
        "输出结构完整且主指标可验证。",
        "",
        "## 跟踪的问题模式",
        "文档生成失败。",
        "",
        "## 停止条件",
        "目标达到后停止。",
    ])


class PlanDocumentAgentRequiredTests(unittest.TestCase):
    def _patch_build_dependencies(self, generated):
        spec = {"schema_version": "evolution.spec.v0", "user_intent": {"intent_text": "原始长输入"}}
        objective = {"user_intent": {"intent_text": "原始长输入"}}
        product = Mock()
        return (
            patch("clawevolve_plan.pipeline.spec_flow.build_spec", return_value=spec),
            patch("clawevolve_plan.pipeline.spec_flow.validate_discovery"),
            patch("clawevolve_plan.pipeline.spec_flow._archived_input_path", return_value=""),
            patch("clawevolve_plan.pipeline.spec_flow.PlanProductService", return_value=product),
            patch("clawevolve_plan.pipeline.spec_flow.build_objective_document", return_value=objective),
            patch("clawevolve_plan.pipeline.spec_flow.load_plan_templates", return_value=("objective-template", "spec-template", {})),
            patch("clawevolve_plan.pipeline.spec_flow.generate_plan_markdown_with_agent", side_effect=generated if isinstance(generated, BaseException) else None, return_value=None if isinstance(generated, BaseException) else generated),
        )

    def test_document_agent_failure_aborts_without_writing_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            patches = self._patch_build_dependencies(RuntimeError("document agent failed"))
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                with self.assertRaisesRegex(RuntimeError, "document agent failed"):
                    build_and_write_spec(
                        args=_args(),
                        plan={},
                        discovery_notes="checked",
                        input_archive={},
                        plan_path=root / "source.json",
                        output_dir=root / "output",
                        task_id="EV-DOC-1",
                    )
            self.assertFalse((root / "output" / "objective.md").exists())
            self.assertFalse((root / "output" / "spec-v0.md").exists())

    def test_agent_documents_are_the_documents_written_to_disk(self) -> None:
        objective_md = _objective_markdown()
        spec_md = "agent generated spec"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            patches = self._patch_build_dependencies((objective_md, spec_md, {"agent_id": "doc-agent"}))
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                    patch("clawevolve_plan.pipeline.spec_flow.validate_objective_markdown"), \
                    patch("clawevolve_plan.pipeline.spec_flow.validate_spec_markdown"):
                build_and_write_spec(
                    args=_args(),
                    plan={},
                    discovery_notes="checked",
                    input_archive={},
                    plan_path=root / "source.json",
                    output_dir=root / "output",
                    task_id="EV-DOC-1",
                )
            self.assertEqual((root / "output" / "objective.md").read_text(), objective_md)
            self.assertEqual((root / "output" / "spec-v0.md").read_text(), spec_md)

    def test_agent_document_validation_failure_aborts_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            generated = (_objective_markdown(), "invalid spec", {"agent_id": "doc-agent"})
            patches = self._patch_build_dependencies(generated)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
                    patch(
                        "clawevolve_plan.pipeline.spec_flow.validate_objective_markdown",
                        side_effect=ValueError("invalid objective"),
                    ):
                with self.assertRaisesRegex(ValueError, "invalid objective"):
                    build_and_write_spec(
                        args=_args(),
                        plan={},
                        discovery_notes="checked",
                        input_archive={},
                        plan_path=root / "source.json",
                        output_dir=root / "output",
                        task_id="EV-DOC-1",
                    )
            self.assertFalse((root / "output" / "objective.md").exists())
            self.assertFalse((root / "output" / "spec-v0.md").exists())

    def test_equivalent_rewrite_and_business_braces_are_allowed(self) -> None:
        objective = {"user_intent": {"intent_text": "逐字复制的原始长输入"}}
        validate_objective_markdown(_objective_markdown(), objective)

        spec = {
            "schema_version": "evolution.spec.v0",
            "user_intent": {"intent_text": "逐字复制的原始长输入"},
            "objective_contract": {"objective_summary": ["以等义方式表达用户目标并读取 {baseDir}。"]},
        }
        validate_spec_markdown(render_markdown(spec), spec)

    def test_metric_label_need_not_be_repeated_verbatim(self) -> None:
        metric = {"name": "task_success_rate", "display_name": "任务成功率",
                  "unit": "ratio", "target": 0.9}
        objective_md = _objective_markdown("收到你好时回复指定问候语，至少90%的测试满足要求。")
        self.assertNotIn("任务成功率", objective_md)
        validate_objective_markdown(objective_md, {"primary_metric": metric})
        spec = {"schema_version": "evolution.spec.v0",
                "acceptance_criteria": {"primary_metric": metric}}
        spec_md = render_markdown(spec).replace("任务成功率", "固定问候通过比例").replace("task_success_rate", "greeting_pass_ratio")
        self.assertNotIn("任务成功率", spec_md)
        self.assertNotIn("task_success_rate", spec_md)
        validate_spec_markdown(spec_md, spec)
        with self.assertRaisesRegex(ValueError, "primary metric target"):
            validate_objective_markdown(objective_md.replace("90%", "80%"), {"primary_metric": metric})
        with self.assertRaisesRegex(ValueError, "primary metric target"):
            validate_spec_markdown(spec_md.replace("90%", "80%"), spec)

    def test_objective_template_placeholders_are_still_rejected(self) -> None:
        for placeholder in ("{用自然语言说明目标}", "{列出本轮问题}"):
            with self.subTest(placeholder=placeholder):
                with self.assertRaisesRegex(ValueError, "unresolved template placeholders"):
                    validate_objective_markdown(_objective_markdown(placeholder), {})


if __name__ == "__main__":
    unittest.main()
