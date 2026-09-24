#!/usr/bin/env python3
"""Execute business Skills; callers own Stage lifecycle and reporting."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import urllib.error
from pathlib import Path
from typing import Any, Callable, NamedTuple

from . import runtime


class CoreDispatchError(RuntimeError):
    pass


class CoreDispatchResult(NamedTuple):
    selected: bool
    value: Any


def begin_stage_core(*, task_id: str, step_id: str, clawweb_url: str) -> dict[str, Any]:
    import argparse
    return runtime._begin_core(argparse.Namespace(
        task_id=task_id, step_id=step_id, clawweb_url=clawweb_url, action="execute",
    ))


def dispatch_stage_core(
    *,
    selection: dict[str, Any],
    run_builtin: Callable[[], Any],
    run_custom: Callable[[dict[str, Any]], Any],
) -> CoreDispatchResult:
    """Select exactly one core implementation while the native Handler stays active."""

    if selection.get("selected") is True:
        return CoreDispatchResult(True, run_custom(selection))
    if selection.get("selected") is False:
        return CoreDispatchResult(False, run_builtin())
    raise CoreDispatchError("core selection must contain boolean selected")


def execute_stage_skill(
    context: dict[str, Any],
    *,
    model: str = "",
    agent_runner: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a selected business Skill and require its declared result artifact."""

    skill = Path(str(context.get("implementationSkill") or "")).resolve()
    input_file = Path(str(context.get("inputFile") or "")).resolve()
    result_file = Path(str(context.get("resultFile") or "")).resolve()
    if not skill.is_file() or not input_file.is_file():
        raise CoreDispatchError("custom core context is missing its Skill or input file")
    if result_file == input_file or result_file.is_dir():
        raise CoreDispatchError("custom core result file is invalid")

    runner = agent_runner or _run_openclaw_agent
    outcome = runner({
        **context,
        "implementationSkill": str(skill),
        "inputFile": str(input_file),
        "resultFile": str(result_file),
    }, model)
    if not isinstance(outcome, dict) or outcome.get("status") != "succeeded":
        detail = outcome.get("error") if isinstance(outcome, dict) else outcome
        raise CoreDispatchError(f"custom core agent failed: {detail}")
    return _read_business_output(result_file, outcome)


def _read_business_output(result_file: Path, outcome: dict[str, Any]) -> dict[str, Any]:
    """Validate artifacts before accepting a turn, without changing business text."""
    if not result_file.is_file():
        agent_id = str(outcome.get("agent_id") or "unknown")
        session_id = str(outcome.get("session_id") or "unknown")
        final_output = _tail(str(outcome.get("stdout") or "")) or "<empty>"
        raise CoreDispatchError(
            "custom core agent did not create result file: "
            f"{result_file}; agent={agent_id}; session={session_id}; final_output={final_output}"
        )
    try:
        value = runtime._read_json(result_file, strict=True)
    except Exception as exc:
        raise CoreDispatchError(f"custom core result file is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CoreDispatchError("custom core result file must contain one JSON object")
    collected = collect_content_files(value, result_file.parent)
    runtime._normalize_result(collected)
    return collected


def collect_content_files(value: dict[str, Any], directory: Path) -> dict[str, Any]:
    """Read explicit Markdown references and serialize them without rewriting.

    Business text can stay in ordinary UTF-8 files. The model need not quote a
    long Markdown document into JSON or run platform helper commands.
    """
    value = json.loads(json.dumps(value, ensure_ascii=False))

    def materialize(container: dict[str, Any], reference: str, destination: str) -> None:
        if reference not in container:
            return
        if destination in container:
            raise CoreDispatchError(f"{reference} and {destination} cannot both be supplied")
        name = container[reference]
        if not isinstance(name, str) or not name.strip():
            raise CoreDispatchError(f"{reference} must name a UTF-8 file beside the result")
        path = (directory / name).resolve()
        try:
            path.relative_to(directory.resolve())
        except ValueError as exc:
            raise CoreDispatchError(f"{reference} escapes the business output directory") from exc
        if not path.is_file():
            raise CoreDispatchError(
                f"{reference} {name!r} is missing; resolve references relative to {directory}"
            )
        if path.stat().st_size > 1024 * 1024:
            raise CoreDispatchError(f"{reference} {name!r} exceeds 1 MiB")
        try:
            container[destination] = path.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise CoreDispatchError(f"{reference} {name!r} must be UTF-8") from exc
        del container[reference]

    result = value.get("result") if value.get("hitl") is False else value
    if isinstance(result, dict):
        materialize(result, "summaryFile", "summary")
    question = value.get("question")
    if isinstance(question, dict):
        if question.get("format", "text") == "text":
            materialize(question, "contentFile", "content")
        contents = question.get("contents", [])
        if not isinstance(contents, list):
            raise CoreDispatchError("question.contents must be an array")
        for content in contents:
            if isinstance(content, dict):
                materialize(content, "contentFile", "content")
    return value


def _run_openclaw_agent(context: dict[str, Any], model: str) -> dict[str, Any]:
    openclaw = os.environ.get("OPENCLAW_PATH", "openclaw")
    run_dir = Path(context["resultFile"]).parent
    run_dir.mkdir(parents=True, exist_ok=True)
    agent_id = f"clawevolve-business-{uuid.uuid4().hex[:12]}"
    session_id = f"{agent_id}-{int(time.time() * 1000)}"
    prompt = _business_prompt(context)
    add = [openclaw, "agents", "add", agent_id]
    if model:
        add.extend(["--model", model])
    add.extend(["--workspace", str(run_dir), "--non-interactive"])
    env = os.environ.copy()
    validated = False
    try:
        registered = subprocess.run(
            add, capture_output=True, text=True, timeout=120, env=env, check=False,
        )
        if registered.returncode != 0:
            return {
                "status": "failed", "agent_id": agent_id, "session_id": session_id,
                "error": _tail(registered.stderr or registered.stdout),
            }
        for attempt in (1, 2):
            command = [
                openclaw, "agent", "--local", "--agent", agent_id,
                "--session-id", session_id, "--message", prompt, "--json", "--timeout", "3600",
            ]
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=3660,
                cwd=str(run_dir), env=env, check=False,
            )
            outcome = {"status": "succeeded", "agent_id": agent_id, "session_id": session_id,
                       "stdout": _tail(completed.stdout)}
            if completed.returncode != 0:
                return {**outcome, "status": "failed", "error": _tail(completed.stderr or completed.stdout)}
            try:
                value = _read_business_output(Path(context["resultFile"]), outcome)
                _validate_platform_output(context, value)
            except (CoreDispatchError, runtime.RuntimeFailure) as exc:
                _record_output_validation(context, outcome, attempt, error=str(exc))
                if attempt == 2:
                    return {**outcome, "status": "failed", "error": f"output correction exhausted: {exc}"}
                prompt = _correction_prompt(context, str(exc))
                continue
            if attempt == 2:
                _record_output_validation(context, outcome, attempt)
            validated = True
            return outcome
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "failed", "agent_id": agent_id, "session_id": session_id,
            "error": f"custom core agent timed out: {exc}",
        }
    except Exception as exc:  # noqa: BLE001 - converted to a Handler failure.
        return {
            "status": "failed", "agent_id": agent_id, "session_id": session_id,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        # The same agent/session must survive validation and the bounded repair.
        # Keep failed agents and transcripts as evidence, including invalid JSON
        # or file references; presence of result.json alone is not success.
        if validated:
            try:
                subprocess.run(
                    [openclaw, "agents", "delete", agent_id, "--force", "--json"],
                    capture_output=True, text=True, timeout=60, env=env, check=False,
                )
            except Exception:
                pass


def _validate_platform_output(context: dict[str, Any], value: dict[str, Any]) -> None:
    """Use CW's authoritative interaction parser, without reporting or advancing.

    Only an explicit contract rejection permits model correction. Connection,
    service and state failures must not be disguised as malformed model output.
    """
    url = context.get("validationUrl")
    if not url:
        return  # Standalone executor callers have no CW Step.
    try:
        raw = runtime._http("POST", str(url),
            body=runtime._json({"output": runtime._normalize_result(value)}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
    except urllib.error.HTTPError as exc:
        if exc.code == 422:
            try:
                detail = json.loads(exc.read(8192)).get("error", "Stage output contract rejected")
            except (ValueError, AttributeError):
                detail = "Stage output contract rejected"
            raise CoreDispatchError(str(detail)) from exc
        raise RuntimeError(f"Stage output validation unavailable (HTTP {exc.code})") from exc
    if json.loads(raw).get("ok") is not True:
        raise RuntimeError("Stage output validation was not acknowledged")


def _record_output_validation(
    context: dict[str, Any], outcome: dict[str, Any], attempt: int, *, error: str = "",
) -> None:
    result_file = Path(context["resultFile"])
    directory = result_file.parent / "output-validation" / outcome["session_id"]
    directory.mkdir(parents=True, exist_ok=True)
    if result_file.is_file():
        shutil.copyfile(result_file, directory / f"attempt-{attempt}.result.json")
    runtime._atomic_json(directory / f"attempt-{attempt}.json", {
        "agent_id": outcome["agent_id"], "session_id": outcome["session_id"],
        "attempt": attempt, "valid": not error, "error": error,
    })


def _correction_prompt(context: dict[str, Any], error: str) -> str:
    return "\n".join([
        "平台校验未通过。这是同一次业务调用的唯一一次输出纠错机会，不是新的 Stage、用户回答或反馈 Loop。",
        "只修正结果 JSON 的协议格式或文件引用，保留原有业务结论、问题、材料和已完成的业务变更。",
        "不要重新执行业务、修改业务资源、代替用户回答、增加 Loop 轮次或调用平台上报接口。",
        f"校验错误：{error}",
        f"请核对并修正：{context['resultFile']}",
        f"所有 summaryFile/contentFile 都以此目录为基准：{Path(context['resultFile']).parent}",
        "如果文件放在子目录，引用必须包含该子目录，例如 work/material.md。平台不会猜测或改写路径。",
        "修正完成后结束调用，平台将按相同规则再次校验；仍不通过则明确失败。",
    ])


def _business_prompt(context: dict[str, Any]) -> str:
    lines = [
        "执行本次提供的业务 Skill，遵循其原有业务规则。",
        f"1. 完整读取并严格执行：{context['implementationSkill']}",
        f"2. 本轮平台输入位于：{context['inputFile']}",
        f"3. 把唯一业务结果 JSON 对象写入：{context['resultFile']}",
        "4. 只在输入指定的业务资源范围内执行。需要用户回答时按输出 Contract 返回问题，并结束本次调用；平台负责展示和携带回答恢复。",
        "5. 业务输出完成后结束本次调用；任务初始化、结果提交及后续调度由调用方代码完成。",
        "表单或文本确认后的恢复仍属于同一轮。若本接入点允许反馈 Loop，轮次只以输入 loop.round 为准，未提供 loop 时为第 1 轮；不得按调用次数或交互次数递增轮次。",
        "长篇 Markdown 可写入结果文件同目录的 UTF-8 文件：总结用 summaryFile 引用，文本交互（含执行确认）用 question.contentFile 引用，表单 contents 各项用 contentFile 引用；文件引用与对应的内联文本不能同时提供。平台读取原文并序列化，不需要调用平台脚本。",
        f"文件引用以 {Path(context['resultFile']).parent} 为基准；子目录必须写入引用，例如 work/material.md。",
    ]
    if context.get("outputContract"):
        lines.append("本次输出 Contract：\n" + json.dumps(context["outputContract"], ensure_ascii=False))
    return "\n".join(lines)


def _tail(value: str, limit: int = 4000) -> str:
    text = str(value or "")
    return text[-limit:]
