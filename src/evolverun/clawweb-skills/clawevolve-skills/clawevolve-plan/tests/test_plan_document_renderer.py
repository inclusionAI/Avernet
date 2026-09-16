from __future__ import annotations

import argparse
import json
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
from clawevolve_plan.spec.renderer import render_goal_markdown, render_markdown  # noqa: E402


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        goal="把读取 {baseDir} 的流程改造成稳定的文档生成能力",
        target=["skills/skills-local/example/SKILL.md"],
        task_id="EV-DOC-1",
    )


def _objective_markdown(
    goal: str = "稳定生成结构化文档，并读取 {baseDir} 中的输入。",
) -> str:
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


class PlanDocumentRendererTests(unittest.TestCase):
    def _patch_build_dependencies(self):
        spec = {
            "schema_version": "evolution.spec.v0",
            "user_intent": {"intent_text": "原始长输入"},
        }
        objective = {"user_intent": {"intent_text": "原始长输入"}}
        product = Mock()
        return spec, objective, (
            patch("clawevolve_plan.pipeline.spec_flow.build_spec", return_value=spec),
            patch("clawevolve_plan.pipeline.spec_flow.validate_discovery"),
            patch(
                "clawevolve_plan.pipeline.spec_flow._archived_input_path",
                return_value="",
            ),
            patch(
                "clawevolve_plan.pipeline.spec_flow.PlanProductService",
                return_value=product,
            ),
            patch(
                "clawevolve_plan.pipeline.spec_flow.build_objective_document",
                return_value=objective,
            ),
        )

    def test_renderer_documents_are_written_without_agent_call(self) -> None:
        objective_md = _objective_markdown()
        spec_md = "program rendered spec"
        spec, _, patches = self._patch_build_dependencies()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            agent_call = Mock(side_effect=AssertionError("document agent must not run"))
            with patches[0], patches[1], patches[2], patches[3], patches[4], \
                    patch(
                        "clawevolve_plan.pipeline.spec_flow.render_goal_markdown",
                        return_value=objective_md,
                    ), \
                    patch(
                        "clawevolve_plan.pipeline.spec_flow.render_markdown",
                        return_value=spec_md,
                    ), \
                    patch("clawevolve_plan.pipeline.spec_flow.validate_objective_markdown"), \
                    patch("clawevolve_plan.pipeline.spec_flow.validate_spec_markdown"), \
                    patch(
                        "clawevolve_plan.discovery.agent.run_openclaw_agent_message",
                        agent_call,
                    ):
                build_and_write_spec(
                    args=_args(),
                    plan={},
                    discovery_notes="checked",
                    input_archive={},
                    plan_path=root / "source.json",
                    output_dir=root / "output",
                    task_id="EV-DOC-1",
                )

            self.assertEqual(
                (root / "output" / "objective.md").read_text(), objective_md
            )
            self.assertEqual((root / "output" / "spec-v0.md").read_text(), spec_md)
            agent_call.assert_not_called()
            saved_spec = json.loads((root / "output" / "spec-v0.json").read_text())
            self.assertEqual(
                saved_spec["document_generation"],
                {
                    "method": "deterministic_renderer",
                    "generation_method": "deterministic_renderer",
                    "model_used": False,
                },
            )
            self.assertEqual(
                saved_spec["document_generation"], spec["document_generation"]
            )

    def test_renderer_validation_failure_aborts_without_writing(self) -> None:
        _, _, patches = self._patch_build_dependencies()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patches[0], patches[1], patches[2], patches[3], patches[4], \
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

    def test_same_structured_input_renders_identical_documents(self) -> None:
        spec = {
            "schema_version": "evolution.spec.v0",
            "objective_contract": {"objective_summary": ["稳定生成文档。"]},
        }
        objective = {"goal_id": "goal-1", "goal": {"goal_text": "稳定生成文档。"}}
        self.assertEqual(render_markdown(spec), render_markdown(spec))
        self.assertEqual(
            render_goal_markdown(objective), render_goal_markdown(objective)
        )

    def test_equivalent_rewrite_and_business_braces_are_allowed(self) -> None:
        objective = {"user_intent": {"intent_text": "逐字复制的原始长输入"}}
        validate_objective_markdown(_objective_markdown(), objective)

        spec = {
            "schema_version": "evolution.spec.v0",
            "user_intent": {"intent_text": "逐字复制的原始长输入"},
            "objective_contract": {
                "objective_summary": ["以等义方式表达用户目标并读取 {baseDir}。"]
            },
        }
        validate_spec_markdown(render_markdown(spec), spec)

    def test_metric_label_need_not_be_repeated_verbatim(self) -> None:
        metric = {
            "name": "task_success_rate",
            "display_name": "任务成功率",
            "unit": "ratio",
            "target": 0.9,
        }
        objective_md = _objective_markdown(
            "收到你好时回复指定问候语，至少90%的测试满足要求。"
        )
        self.assertNotIn("任务成功率", objective_md)
        validate_objective_markdown(objective_md, {"primary_metric": metric})
        spec = {
            "schema_version": "evolution.spec.v0",
            "acceptance_criteria": {"primary_metric": metric},
        }
        spec_md = (
            render_markdown(spec)
            .replace("任务成功率", "固定问候通过比例")
            .replace("task_success_rate", "greeting_pass_ratio")
        )
        validate_spec_markdown(spec_md, spec)
        with self.assertRaisesRegex(ValueError, "primary metric target"):
            validate_objective_markdown(
                objective_md.replace("90%", "80%"), {"primary_metric": metric}
            )
        with self.assertRaisesRegex(ValueError, "primary metric target"):
            validate_spec_markdown(spec_md.replace("90%", "80%"), spec)

    def test_objective_template_placeholders_are_still_rejected(self) -> None:
        for placeholder in ("{用自然语言说明目标}", "{列出本轮问题}"):
            with self.subTest(placeholder=placeholder):
                with self.assertRaisesRegex(
                    ValueError, "unresolved template placeholders"
                ):
                    validate_objective_markdown(_objective_markdown(placeholder), {})


if __name__ == "__main__":
    unittest.main()
