#!/usr/bin/env python3
"""Native Hardening Handler with an in-handler replace seam."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import sys
from pathlib import Path, PurePosixPath
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SKILL_ROOT.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load platform module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(SKILLS_ROOT / "platform"))
from clawevolve_runtime import executor as core_dispatcher, runtime
from clawevolve_runtime.candidate import CandidateChanges

hardening_report = _load("clawevolve_hardening_report", SKILL_ROOT / "scripts" / "report.py")


def _argv(argv: list[str] | None) -> list[str]:
    source = list(sys.argv[1:] if argv is None else argv)
    if len(source) == 1 and source[0].strip().startswith("/clawevolve-hardening"):
        return shlex.split(source[0].strip())[1:]
    return source


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--target", required=True)
    # The native Runner and the Message fallback may preserve the original
    # shell quotes or pass the same goal as several tokens. Collect up to the
    # next option so both transports have identical Handler semantics.
    parser.add_argument("--goal", nargs="*", default=[])
    parser.add_argument("--model", default="")
    parser.add_argument("--clawweb-url", required=True)
    return parser


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    args = _parser().parse_args(_argv(argv))
    args.goal = " ".join(args.goal).strip()
    return args


def _builtin_context(args: argparse.Namespace, business_input: dict[str, Any] | None = None) -> dict[str, Any]:
    root = runtime.SOURCE_WORKSPACE / "clawevolve_results" / args.task_id / "hardening" / args.step_id
    root.mkdir(parents=True, exist_ok=True)
    input_file = root / "input.json"
    result_file = root / "result.json"
    input_file.write_text(json.dumps(business_input if business_input is not None else {
        "task": {"task_id": args.task_id, "step_id": args.step_id},
        "goal": args.goal,
        "workspace": args.workspace,
        "target_skill": {"path": args.target},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result_file.unlink(missing_ok=True)
    return {
        "implementationSkill": str(SKILL_ROOT / "core" / "SKILL.md"),
        "validationUrl": runtime._step_url(args, "validate-output"),
        "inputFile": str(input_file),
        "resultFile": str(result_file),
    }


def _validated_hardening_output(value: Any, target: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Hardening result must be a JSON object")
    summary = value.get("summary")
    changed = value.get("changed")
    changed_files = value.get("changed_files", [])
    if not isinstance(summary, str) or not summary.strip() or type(changed) is not bool or not isinstance(changed_files, list):
        raise ValueError("Hardening result must contain summary, changed and changed_files")
    normalized: list[str] = []
    target_root = Path(target).resolve()
    for raw in changed_files:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("changed_files must contain nonempty relative paths")
        path = PurePosixPath(str(raw))
        if str(raw).startswith("/") or "\\" in str(raw) or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"changed file escapes target Skill: {raw}")
        normalized.append(path.as_posix())
        (target_root / path.as_posix()).resolve().relative_to(target_root)
    if not changed and normalized:
        raise ValueError("changed=false cannot include changed_files")
    return {**value, "changed_files": list(dict.fromkeys(normalized))}


def run_handler(args: argparse.Namespace) -> dict[str, Any]:
    args.workspace = str(runtime._runtime_path(args.workspace))
    args.target = str(runtime._runtime_path(args.target))
    selection = core_dispatcher.begin_stage_core(
        task_id=args.task_id, step_id=args.step_id, clawweb_url=args.clawweb_url,
    )
    business_input = (runtime._read_json(Path(selection["inputFile"]), strict=True)
                      if selection.get("selected") else selection.get("businessInput", {}))
    # A retry creates a new Step but keeps the candidate and interaction history.
    # Only platform feedback Loop advancement starts a new candidate baseline;
    # this is independent of native Optimize round_no.
    loop_round = business_input.get("loop", {}).get("round", 1)
    if type(loop_round) is not int or loop_round < 1:
        raise ValueError("Hardening feedback Loop round must be a positive integer")
    baseline = (runtime.SOURCE_WORKSPACE / "clawevolve_results" / args.task_id
                / "hardening" / "candidate-baselines" / f"round-{loop_round}.json")
    changes = CandidateChanges(args.workspace, args.target, baseline)
    execution = core_dispatcher.dispatch_stage_core(
        selection=selection,
        run_builtin=lambda: core_dispatcher.execute_stage_skill(_builtin_context(args, selection.get("businessInput")), model=args.model),
        run_custom=lambda context: core_dispatcher.execute_stage_skill(context, model=args.model),
    )
    output = runtime._normalize_result(execution.value)
    if not output["hitl"]:
        output["result"] = _validated_hardening_output(output["result"], args.target)
        output["result"] = changes.validate(output["result"])
    summary = "等待用户补充信息" if output["hitl"] else output["result"]["summary"]
    # This Handler owns the one report for either core. Once the network
    # attempt starts, main must not turn a lost response into a second report.
    args._report_attempted = True
    report = hardening_report.post_report(
        status="succeeded", task_id=args.task_id, step_id=args.step_id,
        clawweb_url=args.clawweb_url, summary=summary, output=output,
    )
    return {"ok": True, "implementation": "custom" if execution.selected else "builtin", "result": output, "report": report}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = run_handler(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 - Handler converts failure to a Step report.
        message = f"{type(exc).__name__}: {exc}"
        core_execution_failed = isinstance(exc, core_dispatcher.CoreDispatchError)
        try:
            if not getattr(args, "_report_attempted", False):
                hardening_report.post_report(
                    status="failed", task_id=args.task_id, step_id=args.step_id,
                    clawweb_url=args.clawweb_url, summary=message[:1000], output=None,
                    error_code="STAGE_CORE_EXECUTION_FAILED" if core_execution_failed else "SKILL_HARDENING_FAILED",
                    retryable=core_execution_failed,
                )
        except Exception as report_error:
            message += f"; failure report: {type(report_error).__name__}: {report_error}"
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
