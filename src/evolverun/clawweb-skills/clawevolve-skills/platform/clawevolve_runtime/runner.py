#!/usr/bin/env python3
"""Code-owned execution of extension Steps and candidate lifecycle operations.

No Agent interprets a platform SKILL.md. The business executor only produces
business output; this entrypoint owns preparation, normalization and reporting.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clawevolve_runtime import executor, runtime


def execute_extension(
    args: argparse.Namespace,
    *,
    execute_business: Callable[..., dict[str, Any]] = executor.execute_stage_skill,
) -> dict[str, Any]:
    # Business failures are reported here. The CLI boundary also reports a
    # definite payload rejection; an uncertain response is never overwritten.
    try:
        context = runtime._begin(args)
        if context.get("executionContract"):
            raise runtime.RuntimeFailure(
                "This implementation uses a legacy execution contract; migrate it to its native Handler contract"
            )
        output = execute_business(context, model=args.model)
        result = runtime._normalize_result(output)
    except Exception as exc:
        args.message = f"{type(exc).__name__}: {exc}"
        try:
            runtime._fail(args)
        except Exception as report_error:
            raise runtime.RuntimeFailure(
                f"{args.message}; failure report was not acknowledged: {report_error}"
            ) from exc
        raise
    summary = "等待用户补充信息" if result.get("hitl") else (
        "等待用户确认结果" if result.get("loop") else "Stage Skill 已完成"
    )
    report = runtime._report(args, "succeeded", summary, output=result)
    return {"ok": True, "report": report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("execute", "prepare", "finalize"), required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--clawweb-url", required=True)
    parser.add_argument("--model", default="")
    args = parser.parse_args(argv)
    args.message = ""
    try:
        if args.action == "execute":
            result = execute_extension(args)
        elif args.action == "prepare":
            result = {"ok": True, **runtime._prepare(args)}
        else:
            result = {"ok": True, **runtime._finalize(args)}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if not getattr(args, "_report_attempted", False):
            args.message = message
            try:
                runtime._fail(args)
            except Exception as report_error:
                message += f"; failure report was not acknowledged: {report_error}"
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
