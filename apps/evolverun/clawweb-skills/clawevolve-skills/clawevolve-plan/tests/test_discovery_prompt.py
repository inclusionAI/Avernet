from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clawevolve_plan.discovery.prompt import (  # noqa: E402
    MAX_DISCOVERY_PROMPT_BYTES,
    build_discovery_prompt,
)
from clawevolve_plan.discovery.service import run_auto_discovery  # noqa: E402

from test_discovery_service import (  # noqa: E402
    TARGET,
    agent_result,
    discovery_payload,
    make_workspace,
)


class DiscoveryPromptBoundaryTests(unittest.TestCase):
    def test_missing_source_is_rejected_before_agent_spawn(self):
        with tempfile.TemporaryDirectory(prefix="plan-discovery-missing-source-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = root / "results" / "plan" / "input"
            input_dir.mkdir(parents=True)

            with patch(
                "clawevolve_plan.discovery.service.run_openclaw_agent_message"
            ) as agent:
                with self.assertRaisesRegex(
                    ValueError, "Plan Source is not readable for Discovery"
                ):
                    run_auto_discovery(
                        plan={
                            "schema_version": "plan-source/v2",
                            "input_mode": "insight_improvement",
                            "agent_context": {"workspace_root": str(workspace)},
                            "cases": [],
                            "root_cause_clusters": [],
                        },
                        plan_path=input_dir / "missing-source.json",
                        input_dir=input_dir,
                        task_id="EV-DISCOVERY-MISSING-SOURCE",
                    )

            agent.assert_not_called()

    def test_large_source_body_stays_out_of_prompt_and_argv(self):
        with tempfile.TemporaryDirectory(prefix="plan-discovery-large-source-") as td:
            root = Path(td)
            source_path = root / "source.json"
            marker = "FULL_EVIDENCE_MUST_STAY_IN_SOURCE_FILE"
            source_path.write_text(
                json.dumps(
                    {
                        "schema_version": "plan-source/v2",
                        "cases": [
                            {
                                "case_id": "case-001",
                                "evidence": {"raw": marker + ("x" * 400_000)},
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            prompt = build_discovery_prompt(
                source_path=source_path,
                workspace_root=root / "workspace",
                output_path=root / "discovery.candidate.json",
                source_schema="plan-source/v2",
                source_size_bytes=source_path.stat().st_size,
                case_count=1,
                cluster_count=0,
                input_mode="insight_improvement",
            )

            self.assertIn(json.dumps(str(source_path.resolve())), prompt)
            self.assertIn("source_size_bytes", prompt)
            self.assertNotIn(marker, prompt)
            self.assertLessEqual(
                len(prompt.encode("utf-8")), MAX_DISCOVERY_PROMPT_BYTES
            )

    def test_prompt_size_is_effectively_independent_of_source_size(self):
        with tempfile.TemporaryDirectory(prefix="plan-discovery-source-sizes-") as td:
            root = Path(td)

            def render(source_bytes: int) -> str:
                return build_discovery_prompt(
                    source_path=root / "source.json",
                    workspace_root=root / "workspace",
                    output_path=root / "discovery.candidate.json",
                    source_schema="plan-source/v2",
                    source_size_bytes=source_bytes,
                    case_count=2,
                    cluster_count=1,
                    input_mode="insight_improvement",
                )

            small = render(1_024)
            large = render(4 * 1024 * 1024)

            self.assertLessEqual(abs(len(large) - len(small)), 8)
            self.assertLessEqual(
                len(large.encode("utf-8")), MAX_DISCOVERY_PROMPT_BYTES
            )

    def test_oversized_discovery_prompt_is_rejected_before_agent_spawn(self):
        with tempfile.TemporaryDirectory(prefix="plan-discovery-prompt-guard-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = root / "results" / "plan" / "input"
            input_dir.mkdir(parents=True)
            source_path = input_dir / "source.json"
            source_path.write_text(
                json.dumps({"schema_version": "plan-source/v2", "cases": []}),
                encoding="utf-8",
            )

            with patch(
                "clawevolve_plan.discovery.service.build_discovery_prompt",
                return_value="中" * MAX_DISCOVERY_PROMPT_BYTES,
            ), patch(
                "clawevolve_plan.discovery.service.run_openclaw_agent_message"
            ) as agent:
                with self.assertRaisesRegex(
                    ValueError, "Discovery prompt exceeds 32768-byte limit"
                ):
                    run_auto_discovery(
                        plan={
                            "schema_version": "plan-source/v2",
                            "input_mode": "insight_improvement",
                            "agent_context": {"workspace_root": str(workspace)},
                            "cases": [],
                            "root_cause_clusters": [],
                        },
                        plan_path=source_path,
                        input_dir=input_dir,
                        task_id="EV-DISCOVERY-PROMPT-GUARD",
                    )

            agent.assert_not_called()

    def test_initial_and_correction_prompts_reference_source_without_embedding_it(self):
        with tempfile.TemporaryDirectory(prefix="plan-discovery-source-reference-") as td:
            root = Path(td)
            workspace = make_workspace(root)
            input_dir = root / "results" / "plan" / "input"
            input_dir.mkdir(parents=True)
            source_path = input_dir / "source.json"
            marker = "DO_NOT_EMBED_THIS_EVIDENCE_" + ("z" * 100_000)
            source_path.write_text(
                json.dumps(
                    {
                        "schema_version": "plan-source/v2",
                        "cases": [
                            {"case_id": "case-001", "evidence": {"raw": marker}}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            messages: list[str] = []

            def fake_agent(**kwargs):
                messages.append(kwargs["message"])
                kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
                if len(messages) == 1:
                    kwargs["output_path"].write_text("{invalid", encoding="utf-8")
                else:
                    kwargs["output_path"].write_text(
                        json.dumps(discovery_payload(workspace), ensure_ascii=False),
                        encoding="utf-8",
                    )
                return agent_result()

            with patch(
                "clawevolve_plan.discovery.service.run_openclaw_agent_message",
                side_effect=fake_agent,
            ):
                result = run_auto_discovery(
                    plan={
                        "schema_version": "plan-source/v2",
                        "input_mode": "insight_improvement",
                        "agent_context": {"workspace_root": str(workspace)},
                        "cases": [
                            {"case_id": "case-001", "evidence": {"raw": marker}}
                        ],
                        "root_cause_clusters": [],
                    },
                    plan_path=source_path,
                    input_dir=input_dir,
                    task_id="EV-DISCOVERY-SOURCE-REFERENCE",
                )

            self.assertEqual(result.targets, [TARGET])
            self.assertEqual(len(messages), 2)
            quoted_source_path = json.dumps(str(source_path.resolve()))
            for message in messages:
                self.assertIn(quoted_source_path, message)
                self.assertNotIn(marker, message)
                self.assertLessEqual(
                    len(message.encode("utf-8")), MAX_DISCOVERY_PROMPT_BYTES
                )


if __name__ == "__main__":
    unittest.main()
