from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery.scripts.case_extractor import OpenClawCaseExtractor
from refinery.scripts.baseline_template import BaselineTemplateLoader
from refinery.scripts.session_parser import OpenClawSessionParser
from refinery.scripts.skill_loader import SkillLoader
from refinery.scripts.models import SkillSummary


class FakeLLM:
    def complete_json(self, prompt: str) -> dict[str, Any]:
        return {
            "case_type": "bad_case",
            "score": 0.2,
            "rubric_scores": {
                "goal_completion": 0.1,
                "tool_and_skill_correctness": 0.1,
                "evidence_and_traceability": 0.4,
                "error_handling": 0.0,
                "output_quality": 0.3,
                "safety_and_boundary": 0.5,
            },
            "confidence": "high",
            "title": "调用 MCP server 名称错误",
            "scene": "fraud_workflow_query",
            "intent_type": "workflow_search",
            "key_evidence": ["Unknown MCP server"],
            "root_cause": {
                "category": "tool_argument_error",
                "description": "server name was not accepted by mcporter",
            },
            "success_pattern": {},
            "guide_suggestion": "mcporter call 前应确认 server 标识。",
            "trigger_condition": {"keywords": ["mcporter", "Unknown MCP server"]},
            "content": {"failure_signal": "Unknown MCP server"},
        }


class ApplicabilityFalseLLM:
    def complete_json(self, prompt: str) -> dict[str, Any]:
        return {
            "applicable": False,
            "confidence": "high",
            "reason": "session only pulled pending tasks and found no tasks",
            "matched_rule": "拉取结果为空，没有实际活动评审任务",
        }


class RetryOnceLLM:
    def __init__(self):
        self.calls = 0

    def complete_json(self, prompt: str) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            raise ValueError("temporary malformed JSON")
        return FakeLLM().complete_json(prompt)


def write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in events), encoding="utf-8")


class OpenClawSessionParserTests(unittest.TestCase):
    def test_parser_extracts_bcs_summary_tool_chain_and_loaded_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp)
            session_id = "abc-123"
            (sessions / "sessions.json").write_text(
                json.dumps(
                    {
                        "key": {
                            "sessionId": session_id,
                            "label": "BCS test",
                            "chatType": "group",
                            "skillsSnapshot": {"skills": [{"name": "fraud-intent-router"}]},
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            write_jsonl(
                sessions / f"{session_id}.jsonl",
                [
                    {
                        "type": "session",
                        "id": session_id,
                        "timestamp": "2026-04-26T22:00:55.558Z",
                        "cwd": "/home/admin/.openclaw/workspace",
                    },
                    {
                        "type": "model_change",
                        "modelId": "Kimi-K2.5",
                    },
                    {
                        "type": "message",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        'metadata\n{"task_id":"task1","target":"2088",'
                                        '"request_type":"解除账户限制","description":"用户不能收款",'
                                        '"domain":"欺诈"}'
                                    ),
                                }
                            ],
                        },
                    },
                    {
                        "type": "message",
                        "timestamp": "2026-04-26T22:01:00Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "toolCall",
                                    "id": "call-1",
                                    "name": "read",
                                    "arguments": {
                                        "path": "/home/admin/.openclaw/workspace/skills/skills-local/fraud-intent-router/SKILL.md"
                                    },
                                }
                            ],
                        },
                    },
                    {
                        "type": "message",
                        "message": {
                            "role": "toolResult",
                            "toolCallId": "call-1",
                            "toolName": "read",
                            "content": [{"type": "text", "text": "skill content"}],
                            "isError": False,
                        },
                    },
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "最终结论"}],
                        },
                    },
                ],
            )

            compact = OpenClawSessionParser(sessions).parse_file(sessions / f"{session_id}.jsonl")

            self.assertEqual(compact.session_id, session_id)
            self.assertEqual(compact.session_label, "BCS test")
            self.assertEqual(compact.model, "Kimi-K2.5")
            self.assertIn("解除账户限制", compact.user_requests[0])
            self.assertEqual(compact.tool_chain[0].tool, "read")
            self.assertEqual(compact.tool_chain[0].result_preview, "[SKILL.md loaded] skill content")
            self.assertEqual(compact.loaded_skills[0].name, "fraud-intent-router")
            self.assertEqual(compact.final_response, "最终结论")

    def test_parser_redacts_proc_environ_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp)
            session_id = "abc-456"
            write_jsonl(
                sessions / f"{session_id}.jsonl",
                [
                    {"type": "session", "id": session_id},
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "toolCall",
                                    "id": "call-1",
                                    "name": "read",
                                    "arguments": {"path": "/proc/self/environ"},
                                }
                            ],
                        },
                    },
                    {
                        "type": "message",
                        "message": {
                            "role": "toolResult",
                            "toolCallId": "call-1",
                            "toolName": "read",
                            "content": [{"type": "text", "text": "OPENCLAW_GATEWAY_TOKEN=abc123SECRET456"}],
                            "isError": False,
                        },
                    },
                ],
            )

            compact = OpenClawSessionParser(sessions).parse_file(sessions / f"{session_id}.jsonl")

            self.assertEqual(
                compact.tool_chain[0].result_preview,
                "[REDACTED: /proc/self/environ output omitted]",
            )
            self.assertNotIn("abc123SECRET456", compact.tool_chain[0].result_preview)


class SkillLoaderTests(unittest.TestCase):
    def test_select_for_session_matches_container_loaded_skill_by_name(self) -> None:
        compact = {
            "loaded_skills": [
                {
                    "name": "fraud-intent-router",
                    "path": "/home/admin/.openclaw/workspace/skills/skills-local/fraud-intent-router/SKILL.md",
                }
            ],
            "user_requests": ["解除账户限制"],
            "tool_chain": [],
        }
        skills = [
            SkillSummary(
                name="fraud-intent-router",
                path="/Users/me/openclaw/workspace/skills/skills-local/fraud-intent-router/SKILL.md",
            ),
            SkillSummary(
                name="docx",
                path="/Users/me/openclaw/workspace/skills/skills-repo/docx/SKILL.md",
            ),
        ]

        selected = SkillLoader.select_for_session(compact, skills)

        self.assertEqual([skill.name for skill in selected], ["fraud-intent-router"])


class BaselineTemplateLoaderTests(unittest.TestCase):
    def test_loader_extracts_fixed_sections_and_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "baseline.md"
            template.write_text(
                """---
id: task_001
name: 活动评审baseline模板
category: simple
grading_type: hybrid
---

# 活动风险评估

## Prompt
用户需要对营销活动进行风险评估。

## Expected Behavior
- [ ] 使用语雀 MCP 工具正确读取文档

## Grading Criteria
- [ ] 未执行任何更新操作

## Automated Checks
```python
def grade(transcript, workspace_path):
    return {}
```

## LLM Judge Rubric
Score 1.0: 完整执行。
""",
                encoding="utf-8",
            )

            data = BaselineTemplateLoader(template).load()

            self.assertEqual(data["template_id"], "task_001")
            self.assertEqual(data["name"], "活动评审baseline模板")
            self.assertIn("prompt", data["sections"])
            self.assertIn("automated_checks", data["sections"])
            self.assertIn("使用语雀 MCP 工具正确读取文档", data["checkpoints"])


class OpenClawCaseExtractorTests(unittest.TestCase):
    def test_extractor_writes_llm_judged_bad_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "sessions"
            sessions.mkdir()
            output = root / "memory"
            session_id = "session-1"
            write_jsonl(
                sessions / f"{session_id}.jsonl",
                [
                    {"type": "session", "id": session_id, "timestamp": "2026-04-26T22:00:55Z"},
                    {
                        "type": "message",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "查询最近3小时的欺诈重复稽核工单"}],
                        },
                    },
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "toolCall",
                                    "id": "call-1",
                                    "name": "exec",
                                    "arguments": {"command": "mcporter call mcp.ant.riskfaasai ..."},
                                }
                            ],
                        },
                    },
                    {
                        "type": "message",
                        "message": {
                            "role": "toolResult",
                            "toolCallId": "call-1",
                            "toolName": "exec",
                            "content": [{"type": "text", "text": "Unknown MCP server"}],
                            "isError": False,
                        },
                    },
                ],
            )

            stats = OpenClawCaseExtractor(
                sessions_dir=sessions,
                skills_dir=None,
                output_dir=output,
                llm=FakeLLM(),
                llm_mode="openapi",
            ).extract()

            self.assertEqual(stats["processed"], 1)
            self.assertEqual(stats["bad_case"], 1)
            files = list((output / "bad_case").glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].name, "bad_case_session-1.json")
            memory = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(memory["id"], "bad_case_session-1")
            self.assertEqual(memory["memory_type"], "bad_case")
            self.assertEqual(memory["case_type"], "bad_case")
            self.assertEqual(memory["score"], 0.2)
            self.assertEqual(memory["source"]["llm_mode"], "openapi")
            self.assertEqual(memory["content"]["root_cause"]["category"], "tool_argument_error")
            self.assertEqual(memory["content"]["grading"]["score"], 0.2)
            self.assertEqual(memory["content"]["grading"]["grading_mode"], "session-prompt")

    def test_extractor_grades_baseline_automated_from_session_only_without_llm_call(self) -> None:
        class FailingLLM:
            def complete_json(self, prompt: str) -> dict[str, Any]:
                raise AssertionError("LLM should not be called for automated-only baseline grading")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "sessions"
            sessions.mkdir()
            output = root / "memory"
            template = root / "baseline.md"
            session_id = "session-baseline"

            template.write_text(
                """---
id: task_auto
name: Auto baseline
category: simple
grading_type: automated
---

# Auto baseline

## Prompt
Run an automated check.

## Expected Behavior
Create result.txt and call validation.

## Grading Criteria
- [ ] Called validation
- [ ] Created result file

## Automated Checks
```python
def grade(transcript):
    called_validation = False
    mentioned_result = False
    for entry in transcript:
        if entry.get("type") != "message":
            continue
        msg = entry.get("message", {})
        if msg.get("role") != "assistant":
            continue
        for item in msg.get("content", []):
            if item.get("type") == "toolCall":
                command = item.get("arguments", {}).get("command", "")
                if "run_validation.py 1" in command:
                    called_validation = True
            elif item.get("type") == "text" and "result.txt" in item.get("text", ""):
                mentioned_result = True
    return {
        "called_validation": 1.0 if called_validation else 0.0,
        "result_mentioned": 1.0 if mentioned_result else 0.0,
    }
```

## LLM Judge Rubric
Unused.
""",
                encoding="utf-8",
            )
            write_jsonl(
                sessions / f"{session_id}.jsonl",
                [
                    {"type": "session", "id": session_id},
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "toolCall",
                                    "id": "call-1",
                                    "name": "exec",
                                    "arguments": {"command": "python run_validation.py 1"},
                                }
                            ],
                        },
                    },
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "已生成 result.txt"}],
                        },
                    },
                ],
            )

            stats = OpenClawCaseExtractor(
                sessions_dir=sessions,
                skills_dir=None,
                output_dir=output,
                llm=FailingLLM(),
                llm_mode="openapi",
                baseline_template=template,
                baseline_grade_mode="automated-only",
            ).extract()

            self.assertEqual(stats["processed"], 1)
            self.assertEqual(stats["good_case"], 1)
            files = list((output / "good_case").glob("*.json"))
            self.assertEqual(len(files), 1)
            memory = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(memory["id"], "good_case_session-baseline")
            self.assertEqual(memory["source"]["llm_mode"], None)
            self.assertEqual(memory["case_type"], "good_case")
            self.assertEqual(memory["score"], 1.0)
            self.assertEqual(memory["baseline_template"]["template_id"], "task_auto")
            self.assertEqual(memory["baseline_template"]["name"], "Auto baseline")
            self.assertEqual(memory["content"]["grading"]["score"], 1.0)
            self.assertEqual(memory["content"]["grading"]["grading_mode"], "automated-only")
            self.assertEqual(memory["content"]["grading"]["breakdown"]["called_validation"], 1.0)
            self.assertEqual(memory["content"]["grading"]["breakdown"]["result_mentioned"], 1.0)

    def test_baseline_applicability_false_writes_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "sessions"
            sessions.mkdir()
            output = root / "memory"
            template = root / "baseline.md"
            session_id = "session-skipped"

            template.write_text(
                """---
id: task_app
name: Applicability baseline
category: simple
grading_type: automated
---

# Applicability baseline

## Applicability

### Include
- actual review task

### Exclude
- pending task pull found no tasks

## Prompt
Review an activity.

## Expected Behavior
Run review.

## Automated Checks
```python
def grade(transcript):
    raise AssertionError("grading should not run for skipped sessions")
```
""",
                encoding="utf-8",
            )
            write_jsonl(
                sessions / f"{session_id}.jsonl",
                [
                    {"type": "session", "id": session_id},
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "没有待办任务，静默完成"}],
                        },
                    },
                ],
            )

            stats = OpenClawCaseExtractor(
                sessions_dir=sessions,
                skills_dir=None,
                output_dir=output,
                llm=ApplicabilityFalseLLM(),
                llm_mode="openapi",
                baseline_template=template,
            ).extract()

            self.assertEqual(stats["processed"], 1)
            self.assertEqual(stats["skipped"], 1)
            self.assertEqual(stats["bad_case"], 0)
            files = list((output / "skipped").glob("*.json"))
            self.assertEqual(len(files), 1)
            skipped = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(skipped["status"], "skipped")
            self.assertIn("no tasks", skipped["reason"])

    def test_extractor_retries_llm_case_extract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "sessions"
            sessions.mkdir()
            output = root / "memory"
            session_id = "session-retry"
            write_jsonl(
                sessions / f"{session_id}.jsonl",
                [
                    {"type": "session", "id": session_id},
                    {
                        "type": "message",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "查询工单"}],
                        },
                    },
                ],
            )

            llm = RetryOnceLLM()
            with patch("refinery.scripts.case_extractor.time.sleep", lambda _: None):
                stats = OpenClawCaseExtractor(
                    sessions_dir=sessions,
                    skills_dir=None,
                    output_dir=output,
                    llm=llm,
                    llm_mode="openapi",
                    llm_max_retries=2,
                ).extract()

            self.assertEqual(llm.calls, 2)
            self.assertEqual(stats["bad_case"], 1)
            files = list((output / "bad_case").glob("*.json"))
            memory = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(memory["source"]["llm_attempts"], 2)

    def test_extractor_processes_sessions_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / "sessions"
            sessions.mkdir()
            output = root / "memory"
            for index in range(3):
                session_id = f"session-parallel-{index}"
                write_jsonl(
                    sessions / f"{session_id}.jsonl",
                    [
                        {"type": "session", "id": session_id},
                        {
                            "type": "message",
                            "message": {
                                "role": "user",
                                "content": [{"type": "text", "text": f"查询工单 {index}"}],
                            },
                        },
                    ],
                )

            stats = OpenClawCaseExtractor(
                sessions_dir=sessions,
                skills_dir=None,
                output_dir=output,
                llm=FakeLLM(),
                llm_mode="openapi",
            ).extract(session_workers=2)

            self.assertEqual(stats["processed"], 3)
            self.assertEqual(stats["bad_case"], 3)
            self.assertEqual(len(list((output / "bad_case").glob("*.json"))), 3)


if __name__ == "__main__":
    unittest.main()
