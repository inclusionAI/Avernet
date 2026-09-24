#!/usr/bin/env python3
"""Report one clawevolve-hardening result to ClawWeb."""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from typing import Any


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("status", choices=("succeeded", "failed"))
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--clawweb-url", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--changed-file", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not SAFE_ID.fullmatch(args.task_id) or not SAFE_ID.fullmatch(args.step_id):
        raise SystemExit("task-id 或 step-id 不安全")
    summary = args.summary.strip()
    if not summary:
        raise SystemExit("summary 不能为空")
    changed_files = list(dict.fromkeys(path.strip() for path in args.changed_file if path.strip()))
    output: dict[str, Any] | None = None
    if args.status == "succeeded":
        output = {
            "summary": summary,
            "changed": bool(changed_files),
            "changed_files": changed_files,
        }
    result = post_report(
        status=args.status,
        task_id=args.task_id,
        step_id=args.step_id,
        clawweb_url=args.clawweb_url,
        summary=summary,
        output=output,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def post_report(
    *, status: str, task_id: str, step_id: str, clawweb_url: str,
    summary: str, output: dict[str, Any] | None,
    error_code: str = "SKILL_HARDENING_FAILED", retryable: bool = False,
) -> dict[str, Any]:
    if status not in {"succeeded", "failed"}:
        raise ValueError("unsupported hardening report status")
    if not SAFE_ID.fullmatch(task_id) or not SAFE_ID.fullmatch(step_id):
        raise ValueError("task-id 或 step-id 不安全")
    payload: dict[str, object] = {"status": status, "summary": summary}
    if status == "succeeded":
        if not isinstance(output, dict):
            raise ValueError("succeeded report requires output")
        payload["output"] = output
    else:
        payload["error"] = {"code": error_code, "message": summary, "retryable": retryable}
    base = clawweb_url.rstrip("/")
    url = f"{base}/api/evolve/internal/tasks/{task_id}/steps/{step_id}/report"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"ClawWeb 上报失败: HTTP {error.code}: {detail}") from error
    return result


if __name__ == "__main__":
    raise SystemExit(main())
