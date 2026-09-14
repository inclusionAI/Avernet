"""
PinchBench grading engine.
"""

from __future__ import annotations

import json
import logging
import re
import inspect
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from lib_agent import call_judge_api, ensure_agent_exists, run_openclaw_prompt, slugify_model
from lib_evolve_identity import task_scoped_agent_id
from lib_tasks import Task


logger = logging.getLogger(__name__)


DEFAULT_JUDGE_MODEL = "antchat/GLM-5.1"
DEFAULT_JUDGE_AGENT_PREFIX = "bench-judge"
DEFAULT_JUDGE_TIMEOUT_SECONDS = 180
DEFAULT_JUDGE_MAX_RETRIES = 5


@dataclass
class GradeResult:
    task_id: str
    score: float
    max_score: float
    grading_type: str
    breakdown: Dict[str, float]
    notes: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "score": self.score,
            "max_score": self.max_score,
            "grading_type": self.grading_type,
            "breakdown": self.breakdown,
            "notes": self.notes,
        }


def grade_task(
    *,
    task: Task,
    execution_result: Dict[str, Any],
    skill_dir: Path,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    judge_agent_prefix: str = DEFAULT_JUDGE_AGENT_PREFIX,
    judge_timeout_seconds: float = DEFAULT_JUDGE_TIMEOUT_SECONDS,
    judge_max_retries: int = DEFAULT_JUDGE_MAX_RETRIES,
    judge_backend: str = "openclaw",
    judge_base_url: Optional[str] = None,
    judge_api_key: Optional[str] = None,
    verbose: bool = False,
) -> GradeResult:
    grading_type = task.grading_type
    if verbose:
        logger.info("   [VERBOSE] Grading task %s with type: %s", task.task_id, grading_type)
        logger.info("   [VERBOSE] Execution status: %s", execution_result.get("status", "unknown"))

    if grading_type == "automated":
        result = _grade_automated(task, execution_result, verbose=verbose)
        if verbose:
            logger.info("   [VERBOSE] Automated grade breakdown: %s", result.breakdown)
        return result
    if grading_type == "llm_judge":
        result = _grade_llm_judge(
            task=task,
            execution_result=execution_result,
            judge_model=judge_model,
            judge_agent_prefix=judge_agent_prefix,
            judge_timeout_seconds=judge_timeout_seconds,
            judge_max_retries=judge_max_retries,
            judge_backend=judge_backend,
            judge_base_url=judge_base_url,
            judge_api_key=judge_api_key,
            skill_dir=skill_dir,
            verbose=verbose,
        )
        if verbose:
            logger.info("   [VERBOSE] LLM judge breakdown: %s", result.breakdown)
        return result
    if grading_type == "hybrid":
        auto_result = _grade_automated(task, execution_result, verbose=verbose)
        llm_result = _grade_llm_judge(
            task=task,
            execution_result=execution_result,
            judge_model=judge_model,
            judge_agent_prefix=judge_agent_prefix,
            judge_timeout_seconds=judge_timeout_seconds,
            judge_max_retries=judge_max_retries,
            judge_backend=judge_backend,
            judge_base_url=judge_base_url,
            judge_api_key=judge_api_key,
            skill_dir=skill_dir,
            verbose=verbose,
        )
        return _combine_grades(task, auto_result, llm_result)
    raise ValueError(f"Unknown grading type: {grading_type}")


def _grade_automated(task: Task, execution_result: Dict[str, Any], verbose: bool = False) -> GradeResult:
    grading_code = _extract_grading_code(task)
    if not grading_code:
        return GradeResult(
            task_id=task.task_id,
            score=0.0,
            max_score=1.0,
            grading_type="automated",
            breakdown={},
            notes="No automated grading code found",
        )

    namespace: Dict[str, Any] = {}
    exec(grading_code, namespace)
    grade_func = namespace.get("grade")
    if not callable(grade_func):
        return GradeResult(
            task_id=task.task_id,
            score=0.0,
            max_score=1.0,
            grading_type="automated",
            breakdown={},
            notes="Automated grading function missing",
        )

    transcript = execution_result.get("transcript", [])
    workspace = execution_result.get("workspace", "")
    try:
        signature = inspect.signature(grade_func)
        accepts_one_arg = len(signature.parameters) == 1
    except (TypeError, ValueError):
        accepts_one_arg = False

    if accepts_one_arg:
        scores = grade_func(transcript)
    else:
        scores = grade_func(transcript, workspace)
    if not isinstance(scores, dict):
        scores = {}

    if verbose:
        logger.info("   [VERBOSE] Automated grading scores: %s", scores)

    total = _average_scores(scores)
    return GradeResult(
        task_id=task.task_id,
        score=total,
        max_score=1.0,
        grading_type="automated",
        breakdown=_normalize_score_dict(scores),
        notes="",
    )


def _grade_llm_judge(
    *,
    task: Task,
    execution_result: Dict[str, Any],
    judge_model: str,
    judge_agent_prefix: str,
    judge_timeout_seconds: float,
    judge_max_retries: int = DEFAULT_JUDGE_MAX_RETRIES,
    judge_backend: str = "openclaw",
    judge_base_url: Optional[str] = None,
    judge_api_key: Optional[str] = None,
    skill_dir: Optional[Path] = None,
    verbose: bool = False,
) -> GradeResult:
    transcript = execution_result.get("transcript", [])
    execution_status = execution_result.get("status", "unknown")

    if not transcript and execution_status != "success":
        if verbose:
            logger.info(
                "   [VERBOSE] Skipping LLM judge: status=%s, transcript empty",
                execution_status,
            )
        return GradeResult(
            task_id=task.task_id,
            score=0.0,
            max_score=1.0,
            grading_type="llm_judge",
            breakdown={},
            notes=f"Skipped: task execution failed ({execution_status}), no transcript to evaluate",
        )

    transcript_summary = _summarize_transcript(transcript)
    if verbose:
        logger.info("   [VERBOSE] Transcript summary for judge (first 1000 chars):\n%s", transcript_summary[:1000])
    workspace_content = _read_workspace_files(execution_result.get("workspace", ""))
    if verbose and workspace_content:
        logger.info("   [VERBOSE] Workspace files passed to judge (first 500 chars):\n%s", workspace_content[:500])
    rubric = task.llm_judge_rubric or _format_grading_criteria(task)
    prompt = _build_judge_prompt(task, transcript_summary, rubric, workspace_content)

    # Retry logic for judge calls
    max_retries = max(1, int(judge_max_retries))
    last_error = None
    raw_parsed = {}

    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            # Exponential backoff: 1s, 2s, 4s, 8s, 16s
            delay = 2 ** (attempt - 2)
            logger.info("   [VERBOSE] Retrying judge call in %.1fs (attempt %d/%d)", delay, attempt, max_retries)
            time.sleep(delay)

        if judge_backend == "api":
            # Direct API call — bypasses OpenClaw personality injection
            judge_result = call_judge_api(
                prompt=prompt,
                model=judge_model,
                timeout_seconds=judge_timeout_seconds,
                base_url=judge_base_url,
                api_key=judge_api_key,
            )

            if verbose:
                logger.info("   [VERBOSE] Judge execution status (attempt %d): %s", attempt, judge_result.get("status"))
                if judge_result.get("error"):
                    logger.info("   [VERBOSE] Judge error (attempt %d): %s", attempt, judge_result["error"])

            if judge_result.get("status") == "success":
                raw_parsed = _parse_judge_text(judge_result.get("text", ""))
                if raw_parsed:  # Successfully parsed
                    break
                else:
                    logger.warning("Judge API returned success but parsing failed (attempt %d/%d)", attempt, max_retries)
                    last_error = "Parse error: empty result"
            else:
                logger.warning("Judge API call failed (attempt %d/%d): %s", attempt, max_retries, judge_result.get("error", judge_result.get("status")))
                last_error = judge_result.get("error", judge_result.get("status", "Unknown error"))
        else:
            # Default: OpenClaw agent session
            agent_id = _ensure_judge_agent(judge_agent_prefix, judge_model, skill_dir)
            judge_workspace = Path(f"/tmp/pinchbench/judge/{task.task_id}")
            judge_result = run_openclaw_prompt(
                agent_id=agent_id,
                prompt=prompt,
                workspace=judge_workspace,
                timeout_seconds=judge_timeout_seconds,
            )

            if verbose:
                logger.info("   [VERBOSE] Judge execution status (attempt %d): %s", attempt, judge_result.get("status"))
                logger.info("   [VERBOSE] Judge exit code (attempt %d): %s", attempt, judge_result.get("exit_code"))
                logger.info("   [VERBOSE] Judge stderr (attempt %d): %s", attempt, judge_result.get("stderr", "")[:500])

            if judge_result.get("status") == "success":
                raw_parsed = _parse_judge_response(judge_result.get("transcript", []))
                if raw_parsed:  # Successfully parsed
                    break
                else:
                    logger.warning("Judge execution returned success but parsing failed (attempt %d/%d)", attempt, max_retries)
                    last_error = "Parse error: empty result"
            else:
                last_error = _format_judge_execution_error(judge_result)
                logger.warning("Judge execution failed (attempt %d/%d): %s", attempt, max_retries, last_error)

    if not raw_parsed:
        logger.error("Judge failed after %d attempts. Last error: %s", max_retries, last_error)

    if verbose:
        logger.info("   [VERBOSE] Judge raw response parsed: %s", raw_parsed)

    # Normalize the response to handle various formats (criteria_scores, score, justification, etc.)
    parsed = _normalize_judge_response(raw_parsed)
    if verbose:
        logger.info("   [VERBOSE] Normalized judge response: %s", parsed)

    breakdown = parsed.get("scores", {})
    total = parsed.get("total")
    notes = parsed.get("notes", "")
    if not raw_parsed and last_error:
        notes = f"Judge failed: {last_error}"
    return GradeResult(
        task_id=task.task_id,
        score=float(total) if total is not None else 0.0,
        max_score=1.0,
        grading_type="llm_judge",
        breakdown=_normalize_score_dict(breakdown),
        notes=str(notes) if notes is not None else "",
    )


def _format_judge_execution_error(judge_result: Dict[str, Any]) -> str:
    status = judge_result.get("status", "unknown")
    parts = [f"status={status}"]
    exit_code = judge_result.get("exit_code")
    if exit_code not in (None, ""):
        parts.append(f"exit_code={exit_code}")
    stderr = str(judge_result.get("stderr") or "").strip()
    if stderr:
        parts.append(f"stderr={stderr[:500]}")
    stdout = str(judge_result.get("stdout") or "").strip()
    if stdout:
        parts.append(f"stdout={stdout[:300]}")
    agent_id = judge_result.get("agent_id")
    if agent_id:
        parts.append(f"agent_id={agent_id}")
    return "; ".join(parts)


def _combine_grades(task: Task, auto_result: GradeResult, llm_result: GradeResult) -> GradeResult:
    weights = task.grading_weights or {"automated": 0.5, "llm_judge": 0.5}
    auto_weight = float(weights.get("automated", 0.5))
    llm_weight = float(weights.get("llm_judge", 0.5))
    total_weight = auto_weight + llm_weight
    if total_weight <= 0:
        auto_weight = llm_weight = 0.5
        total_weight = 1.0
    combined_score = (
        auto_result.score * auto_weight + llm_result.score * llm_weight
    ) / total_weight
    # Defensive cap: never let the hybrid final exceed the 1.0 ceiling even if
    # a sub-result already broke it.
    combined_score = min(1.0, combined_score)
    breakdown = {
        **{f"automated.{k}": v for k, v in auto_result.breakdown.items()},
        **{f"llm_judge.{k}": v for k, v in llm_result.breakdown.items()},
    }
    notes = " | ".join(filter(None, [auto_result.notes, llm_result.notes]))
    return GradeResult(
        task_id=task.task_id,
        score=combined_score,
        max_score=1.0,
        grading_type="hybrid",
        breakdown=breakdown,
        notes=notes,
    )


def _extract_grading_code(task: Task) -> str:
    if not task.automated_checks:
        return ""
    # Support both 3-backtick and 4-backtick fenced code blocks.
    # Use greedy match (.* instead of .*?) so that inner backtick
    # sequences (e.g. strings containing "```json" in the code) do
    # NOT prematurely end the match.
    for fence in ("```", "````"):
        match = re.search(rf"{re.escape(fence)}python\s*(.*)\s*{re.escape(fence)}", task.automated_checks, re.DOTALL)
        if match:
            return match.group(1)
    return ""


def _average_scores(scores: Dict[str, Any]) -> float:
    values = [float(v) for v in scores.values() if isinstance(v, (int, float))]
    if not values:
        return 0.0
    # Cap at 1.0: gate+bonus grading schemes can yield per-board scores >1.0
    # (e.g. 1.0 + two bonuses). Without a ceiling the board mean exceeds the
    # 0..1 scale and the final hybrid score breaks the 1.0 ceiling.
    return min(1.0, sum(values) / len(values))


def _normalize_score_dict(scores: Dict[str, Any]) -> Dict[str, float]:
    normalized: Dict[str, float] = {}
    for key, value in scores.items():
        try:
            # Clamp into 0..1 so the breakdown never advertises a >1.0 score
            # (gate+bonus schemes can produce values above the ceiling).
            normalized[str(key)] = min(1.0, float(value))
        except (TypeError, ValueError):
            continue
    return normalized


def _format_grading_criteria(task: Task) -> str:
    if not task.grading_criteria:
        return ""
    return "\n".join(f"- {criterion}" for criterion in task.grading_criteria)


def _render_flow_overview(trace: dict, nodes: list, summary_parts: List[str]) -> None:
    """Render ``[Workflow 流转概览]`` from ``workflow_trace`` in manifest.

    Injects a structured overview of workflow-level metadata, DAG structure,
    per-node execution status/output, and event timeline into *summary_parts*.
    """
    status = trace.get("status", "?")
    goal = str(trace.get("goal", ""))[:200]
    started = trace.get("started_at")
    ended = trace.get("ended_at")
    triggered = trace.get("triggered_by", "")

    # Duration
    duration_str = "?"
    if started and ended:
        try:
            start_dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
            duration_str = f"{round((end_dt - start_dt).total_seconds(), 1)}s"
        except Exception:
            pass

    lines = [f"[Workflow 流转概览] 状态: {status} | 耗时: {duration_str}"]
    if triggered:
        lines.append(f"触发: {triggered}")
    if goal:
        lines.append(f"目标: {goal[:200]}")

    # DAG structure
    dag_entries = trace.get("dag", [])
    if dag_entries:
        dag_parts = []
        for d in dag_entries:
            exe = d.get("executor", "?")
            dag_parts.append(f"{d['node']}({exe})")
        lines.append(f"DAG: {' → '.join(dag_parts)}")

    # Per-node execution
    node_execs = trace.get("node_executions", [])
    if node_execs:
        lines.append("节点执行:")
        for ne in node_execs:
            nid = ne.get("node_id", "?")
            exe = ne.get("executor_type", "?")
            st = ne.get("status", "?")
            dur = ne.get("duration_s")
            err = ne.get("error")
            out_keys = ne.get("output_keys", [])
            output = ne.get("output")

            emoji = {"succeeded": "✅", "failed": "❌", "started": "⏳"}.get(st, "❓")
            dur_str = f"{dur}s" if dur is not None else "?"
            parts = [f"{emoji} {nid} ({exe}, {dur_str})"]
            if err:
                parts.append(f"错误: {str(err)[:200]}")
            if out_keys:
                parts.append(f"输出键: {out_keys}")
            if output and isinstance(output, dict):
                output_str = json.dumps(output, ensure_ascii=False, default=str)[:300]
                parts.append(f"输出: {output_str}")

            lines.append("  " + " ".join(parts))

    # Subagent session coverage
    subagent_sessions = trace.get("subagent_sessions", {})
    if subagent_sessions:
        lines.append(
            f"Subagent session 已集成: {len(subagent_sessions)} 节点 "
            f"({', '.join(subagent_sessions.keys())})"
        )

    # Timeline (abbreviated: first 20 events)
    timeline = trace.get("timeline", [])
    if timeline:
        tl_parts = []
        for t in timeline[:20]:
            evt = t.get("event_type", "?")
            node = t.get("node", "?")
            tl_parts.append(f"{evt}@{node}")
        if len(timeline) > 20:
            tl_parts.append(f"...(+{len(timeline) - 20})")
        lines.append(f"事件时间线: {' → '.join(tl_parts)}")

    # Workflow-level outputs
    wf_outputs = trace.get("workflow_outputs", {})
    if isinstance(wf_outputs, dict) and wf_outputs:
        lines.append(f"最终输出键: {list(wf_outputs.keys())}")

    summary_parts.append("\n".join(lines))


def _summarize_transcript(transcript: List[Dict[str, Any]]) -> str:
    summary_parts: List[str] = []

    # ── Workflow 节点概览（仅含 __node__ 的 merged transcript 生效）──
    _has_workflow = any(e.get("__node__") for e in transcript[:50] if isinstance(e, dict))
    if _has_workflow:
        manifest = next(
            (e for e in transcript if isinstance(e, dict) and e.get("type") == "__manifest__"),
            None,
        )
        if manifest:
            nodes = manifest.get("nodes", [])
            if nodes:
                summary_parts.append(
                    f"[Workflow 节点概览] 共 {len(nodes)} 个节点，执行顺序: {' → '.join(nodes)}"
                )

            # ── Workflow 流转概览（format_version ≥ 2.1，有 workflow_trace 时生效）──
            trace = manifest.get("workflow_trace")
            if isinstance(trace, dict) and trace:
                _render_flow_overview(trace, nodes, summary_parts)

    for event in transcript:
        if not isinstance(event, dict):
            continue

        # ── 节点边界标记（仅 workflow transcript，skill session 无此事件 → 零回归）──
        if _has_workflow and event.get("type") == "__node_boundary__":
            node = event.get("__node__", "")
            prev = event.get("__prev_node__", "")
            if node:
                summary_parts.append(f"── 进入节点: {node} (前一节点: {prev}) ──")
            continue

        # ── 合成事件 / workflow 控制事件：仅供 viewer 展示，Judge 不渲染 ──
        # Judge 的控制层信息统一来自 _render_flow_overview（workflow_trace 权威源），
        # 不从事件正文重复读取，避免双源不一致。
        if event.get("type") in (
            "__workflow_start__", "__workflow_end__", "__node_synthetic__"
        ):
            continue

        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        role = msg.get("role")
        if role == "assistant":
            for item in msg.get("content", []):
                if item.get("type") == "toolCall":
                    args = item.get("arguments", {})
                    truncated_args: Dict[str, Any] = {}
                    for k, v in args.items():
                        if isinstance(v, str) and len(v) > 200:
                            truncated_args[k] = v[:200] + "...[truncated]"
                        else:
                            truncated_args[k] = v
                    summary_parts.append(
                        f"Tool: {item.get('name')}({json.dumps(truncated_args)})"
                    )
                elif item.get("type") == "text":
                    text = item.get("text", "").strip()
                    if text:
                        summary_parts.append(f"Assistant: {text[:2000]}")
        elif role == "toolResult":
            content = msg.get("content", [])
            if content:
                result_preview = str(content[0])[:200]
                summary_parts.append(f"Result: {result_preview}")
        elif role == "user":
            content = msg.get("content", [])
            if content:
                summary_parts.append(f"User: {content[0]}")
    return "\n".join(summary_parts)


def _read_workspace_files(workspace_path: str) -> str:
    """Read user-created text files from workspace to provide grading context."""
    if not workspace_path:
        return ""
    workspace = Path(workspace_path)
    if not workspace.exists():
        return ""
    skip_names = {
        "BOOTSTRAP.md", "SOUL.md", "USER.md", "IDENTITY.md",
        "HEARTBEAT.md", "TOOLS.md", "AGENTS.md",
    }
    skip_dirs = {".git", ".openclaw", "__pycache__", "node_modules", "skills"}
    file_contents: List[str] = []
    for f in sorted(workspace.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(workspace)
        parts = rel.parts
        if any(part.startswith(".") or part in skip_dirs for part in parts):
            continue
        if f.name in skip_names:
            continue
        try:
            content = f.read_text(encoding="utf-8")
            file_contents.append(f"### File: {rel}\n{content[:3000]}")
        except (OSError, UnicodeDecodeError):
            pass
    return "\n\n".join(file_contents)


def _build_judge_prompt(task: Task, transcript_summary: str, rubric: str, workspace_content: str = "") -> str:
    workspace_section = ""
    if workspace_content.strip():
        workspace_section = (
            "## Workspace Files Created by Agent\n"
            f"{workspace_content}\n\n"
        )
    return (
        "You are a grading function. Your ONLY job is to output a single JSON object.\n\n"
        "CRITICAL RULES:\n"
        "- Do NOT use any tools (no Read, Write, exec, or any other tool calls)\n"
        "- Do NOT create files or run commands\n"
        "- Do NOT write any prose, explanation, or commentary outside the JSON\n"
        "- Respond with ONLY a JSON object — nothing else\n\n"
        "Be a strict evaluator. Reserve 1.0 for genuinely excellent performance. "
        "An average acceptable completion should score around 0.6-0.7. "
        "Deduct points for unnecessary steps, verbose output, and inefficient tool usage.\n\n"
        "## Task\n"
        f"{task.prompt}\n\n"
        "## Expected Behavior\n"
        f"{task.expected_behavior}\n\n"
        "## Agent Transcript (summarized)\n"
        f"{transcript_summary}\n\n"
        f"{workspace_section}"
        "## Grading Rubric\n"
        f"{rubric}\n\n"
        "Score each criterion from 0.0 to 1.0.\n"
        'The "total" field must also be between 0.0 and 1.0. If the rubric specifies criterion weights, total must be the weighted average of the criterion scores according to those weights. If no weights are specified, use the arithmetic mean of the criterion scores. Do not return a sum.\n\n'
        "Respond with ONLY this JSON structure (no markdown, no code fences, no extra text):\n"
        '{"scores": {"criterion_name": 0.0}, "total": 0.0, "notes": "brief justification(要用中文解释)"}'
    )


def _ensure_judge_agent(judge_agent_prefix: str, judge_model: str, skill_dir: Path) -> str:
    model_slug = slugify_model(judge_model)
    agent_id = task_scoped_agent_id(f"{judge_agent_prefix}-{model_slug}")
    workspace = Path("/tmp/pinchbench/judge/workspace")
    ensure_agent_exists(agent_id, judge_model, workspace)
    return agent_id


def _parse_best_judge_json(raw_text: str) -> Dict[str, Any]:
    """Return the most useful judge JSON from raw model text."""
    raw_text = raw_text.strip()
    if not raw_text:
        return {}

    json_candidates: List[str] = []
    json_candidates.extend(
        match.group(1)
        for match in re.finditer(r"```(?:json)?\s*(.*?)\s*```", raw_text, re.DOTALL)
    )

    brace_depth = 0
    current_json: List[str] = []
    for char in raw_text:
        if char == "{":
            if brace_depth == 0:
                current_json = []
            brace_depth += 1
        if brace_depth > 0:
            current_json.append(char)
        if char == "}":
            brace_depth -= 1
            if brace_depth == 0 and current_json:
                json_candidates.append("".join(current_json))

    parsed_candidates: List[Dict[str, Any]] = []
    seen_candidates: set[str] = set()
    for candidate in json_candidates:
        candidate = candidate.strip()
        if not candidate or candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                parsed_candidates.append(parsed)
        except json.JSONDecodeError:
            continue

    if not parsed_candidates:
        return {}

    def candidate_rank(parsed: Dict[str, Any]) -> int:
        if isinstance(parsed.get("scores"), dict):
            return 5
        if isinstance(parsed.get("criteria_scores"), dict):
            return 4
        if parsed.get("total") is not None:
            return 3
        if isinstance(parsed.get("score"), (int, float)):
            return 2
        if isinstance(parsed.get("overall_score"), (int, float)):
            return 2
        return 1

    return max(
        enumerate(parsed_candidates),
        key=lambda item: (candidate_rank(item[1]), item[0]),
    )[1]


def _parse_judge_response(transcript: List[Dict[str, Any]]) -> Dict[str, Any]:
    content_chunks: List[str] = []
    for event in transcript:
        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if msg.get("role") != "assistant":
            continue
        for item in msg.get("content", []):
            if item.get("type") == "text":
                content_chunks.append(item.get("text", ""))
    raw_text = "\n".join(content_chunks).strip()
    logger.info("   [VERBOSE] Judge raw response text (first 2000 chars):\n%s", raw_text[:2000])
    if not raw_text:
        return {}

    parsed = _parse_best_judge_json(raw_text)
    if parsed:
        return parsed

    # Fallback: try to extract numeric scores from prose responses.
    # Models sometimes return "Total: 0.72" or "Overall score: 0.65" instead of JSON.
    score_pattern = re.search(
        r"(?:total|overall|final)\s*(?:score)?[:\s]*(0\.\d+|1\.0+)",
        raw_text,
        re.IGNORECASE,
    )
    if score_pattern:
        try:
            total = float(score_pattern.group(1))
            if 0.0 <= total <= 1.0:
                logger.warning(
                    "Fell back to regex score extraction from prose (total=%.2f)", total
                )
                return {"scores": {}, "total": total, "notes": "Score extracted from prose (JSON parse failed)"}
        except ValueError:
            pass

    logger.warning("Failed to parse judge JSON response")
    return {}


def _parse_judge_text(raw_text: str) -> Dict[str, Any]:
    """Parse judge response from raw text (direct API call, no OpenClaw transcript)."""
    raw_text = raw_text.strip()
    if not raw_text:
        return {}

    parsed = _parse_best_judge_json(raw_text)
    if parsed:
        return parsed

    # Fallback: regex for total score
    score_pattern = re.search(
        r"(?:total|overall|final)\s*(?:score)?[:\s]*(0\.\d+|1\.0+)",
        raw_text,
        re.IGNORECASE,
    )
    if score_pattern:
        try:
            total = float(score_pattern.group(1))
            if 0.0 <= total <= 1.0:
                logger.warning("Fell back to regex score extraction (total=%.2f)", total)
                return {"scores": {}, "total": total, "notes": "Score extracted from prose"}
        except ValueError:
            pass

    logger.warning("Failed to parse judge text response")
    return {}


def _normalize_judge_response(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize judge response to expected format with 'scores', 'total', and 'notes'.

    Handles various response formats:
    - {"scores": {...}, "total": 0.9, "notes": "..."}  (expected)
    - {"criteria_scores": {...}, ...}  (Claude sometimes uses this)
    - {"score": 0.9, "justification": "..."}  (simplified format)
    """
    result: Dict[str, Any] = {"scores": {}, "total": None, "notes": ""}

    # Extract scores from various keys
    if "scores" in parsed:
        scores_data = parsed["scores"]
        if isinstance(scores_data, dict):
            # Handle nested structure: {"criterion": {"score": 0.9, "weight": 0.3}}
            for key, value in scores_data.items():
                if isinstance(value, dict) and "score" in value:
                    result["scores"][key] = float(value["score"]) if isinstance(value["score"], (int, float, str)) else value["score"]
                elif isinstance(value, (int, float)):
                    result["scores"][key] = value
    elif "criteria_scores" in parsed:
        # Handle Claude's alternate format
        criteria = parsed["criteria_scores"]
        if isinstance(criteria, dict):
            for key, value in criteria.items():
                if isinstance(value, dict) and "score" in value:
                    result["scores"][key] = value["score"]
                elif isinstance(value, (int, float)):
                    result["scores"][key] = value

    # Extract total score
    if "total" in parsed and parsed["total"] is not None:
        result["total"] = float(parsed["total"]) if isinstance(parsed["total"], (int, float)) else None
    elif "score" in parsed and isinstance(parsed["score"], (int, float)):
        result["total"] = float(parsed["score"])
    elif "overall_score" in parsed and isinstance(parsed["overall_score"], (int, float)):
        result["total"] = float(parsed["overall_score"])
    elif result["scores"]:
        # Calculate average if we have individual scores but no total
        values = [v for v in result["scores"].values() if isinstance(v, (int, float))]
        if values:
            result["total"] = sum(values) / len(values)

    # Some judge models return a summed total across criteria even though each
    # criterion is scored on a 0..1 scale. Normalize that back to a 0..1 mean.
    values = [v for v in result["scores"].values() if isinstance(v, (int, float))]
    if (
        values
        and result["total"] is not None
        and result["total"] > 1.0
        and all(0.0 <= float(v) <= 1.0 for v in values)
    ):
        result["total"] = sum(values) / len(values)

    # Extract notes/justification
    if "notes" in parsed:
        result["notes"] = str(parsed["notes"])
    elif "justification" in parsed:
        result["notes"] = str(parsed["justification"])
    elif "reasoning" in parsed:
        result["notes"] = str(parsed["reasoning"])

    return result
