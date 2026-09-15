#!/usr/bin/env python3
"""Thin ClawEvolve Step adapter for the direct ClawBench workflow."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_http_retry import retry_http


def release_skill_root() -> Path:
    raw = str(os.environ.get("SKILL_BASE_DIR") or "").strip()
    return Path(raw).expanduser().resolve() if raw else Path(__file__).resolve().parents[3]


def diagnostic(log_path: Path | None, event: str, **fields: Any) -> None:
    record = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "component": "clawevolve-bench-handler", "event": event, **fields}
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str)
    print(f"BENCH_DIAGNOSTIC {line}", file=sys.stderr, flush=True)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, *, log_path: Path | None = None, event: str = "http") -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    diagnostic(log_path, f"{event}.request", method=method, url=url, status=(payload or {}).get("status"))
    def request_once() -> str:
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read().decode("utf-8")
                diagnostic(log_path, f"{event}.response", method=method, url=url, httpStatus=getattr(response, "status", 200))
                return raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try: detail = json.loads(raw).get("error") if raw else ""
            except Exception: detail = raw
            diagnostic(log_path, f"{event}.error", method=method, url=url, httpStatus=exc.code, responseBody=raw[-4000:])
            raise RuntimeError(f"ClawWeb {method} {url} returned HTTP {exc.code}: {detail or exc.reason}") from exc
        except Exception as exc:
            diagnostic(log_path, f"{event}.error", method=method, url=url, errorType=type(exc).__name__, error=str(exc))
            raise
    raw = retry_http(request_once, label=f"ClawWeb {event}")
    value = json.loads(raw) if raw else {}
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required from {url}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def report(url: str, payload: dict[str, Any], *, log_path: Path | None = None) -> None:
    http_json("POST", url, payload, log_path=log_path, event="step_report")


def workflow_config(input_payload: dict[str, Any], args: argparse.Namespace, task_root: Path, invocation_root: Path, clawweb_url: str) -> dict[str, Any]:
    task = input_payload.get("task") or {}
    step = input_payload.get("step") or {}
    target = input_payload.get("target") or {}
    value = input_payload.get("input") or {}
    if not value:
        value = task.get("config") or {}
    bench = value.get("bench") or value.get("sourceBench") or value
    if not isinstance(bench, dict):
        raise ValueError("Step Input bench must be an object")
    domain_id = str(bench.get("domainId") or bench.get("benchDomainId") or "")
    if domain_id != args.domain_id:
        raise ValueError(f"bench domain mismatch: command={args.domain_id} input={domain_id}")
    pinned_templates = value.get("pinnedTemplates") if isinstance(value.get("pinnedTemplates"), list) else []
    frozen_template_version = ""
    if len(pinned_templates) == 1 and isinstance(pinned_templates[0], dict):
        frozen_template_version = str(pinned_templates[0].get("templateVersion") or "")
    judge = bench.get("judge") if isinstance(bench.get("judge"), dict) else {}
    runtime_value = value.get("runtime") if isinstance(value.get("runtime"), dict) else {}
    openclaw_execution_mode = str(
        value.get("openclawExecutionMode")
        or runtime_value.get("openclawExecutionMode")
        or "local"
    )
    if openclaw_execution_mode not in {"local", "gateway"}:
        raise ValueError("openclawExecutionMode must be local or gateway")
    expected = {
        "templateName": str(bench.get("templateName") or ""),
        "templateVersion": str(bench.get("templateVersion") or frozen_template_version),
        "ownerId": str(value.get("ownerUserId") or bench.get("ownerUserId") or target.get("userId") or ""),
        "model": str(bench.get("model") or os.environ.get("CLAWEVOLVE_BENCH_MODEL", "")),
        "suite": str(bench.get("suite") or "all"),
        "scene": str(bench.get("scene") or "claw-evolve-bench"),
        "judge": str(judge.get("model") or bench.get("judgeModel") or ""),
    }
    for name, actual in (("templateName", args.template_name), ("templateVersion", args.template_version),
                         ("ownerId", args.owner_id), ("model", args.model), ("suite", args.suite),
                         ("scene", args.scene), ("judge", args.judge)):
        if actual is not None and str(actual) != expected[name]:
            raise ValueError(f"{name} mismatch: command={actual} input={expected[name]}")
    command_execution_mode = getattr(args, "openclaw_execution_mode", None)
    if command_execution_mode is not None and command_execution_mode != openclaw_execution_mode:
        raise ValueError(
            "openclawExecutionMode mismatch: "
            f"command={command_execution_mode} input={openclaw_execution_mode}"
        )
    if step and str(step.get("stepId") or "") not in ("", args.step_id):
        raise ValueError("step id mismatch")
    if task and str(task.get("taskId") or "") not in ("", args.task_id):
        raise ValueError("task id mismatch")
    owner_id = str(value.get("ownerUserId") or bench.get("ownerUserId") or target.get("userId") or task.get("userId") or task.get("user_id") or "")
    bot_id = str(value.get("botId") or target.get("botId") or task.get("botId") or task.get("bot_id") or "")
    return {
        "schemaVersion": "clawbench.workflow.input.v1",
        "identity": {"evolveTaskId": args.task_id, "evolveStepId": args.step_id, "ownerId": owner_id, "botId": bot_id},
        "bench": {
            "domainId": domain_id,
            "templateName": bench.get("templateName") or "",
            "templateVersion": bench.get("templateVersion"),
            "model": bench.get("model") or os.environ.get("CLAWEVOLVE_BENCH_MODEL", ""),
            "suite": bench.get("suite") or "all",
            "scene": bench.get("scene") or "claw-evolve-bench",
            "judge": judge.get("model") or bench.get("judgeModel") or "",
            "judgeBaseUrl": judge.get("baseUrl") or "",
            "judgeBaseUrlRef": judge.get("baseUrlRef") or "",
            "judgeApiKeyRef": judge.get("apiKeyRef") or "",
            "pinnedTemplates": pinned_templates,
        },
        "endpoints": {"clawwebUrl": clawweb_url},
        "runtime": {
            "agentbenchHome": str(release_skill_root() / "clawbench-base"),
            "taskRoot": str(task_root),
            "invocationRoot": str(invocation_root),
            "inputDir": str(invocation_root / "input"),
            "outputDir": str(invocation_root / "output"),
            "logDir": str(invocation_root / "logs"),
            "timeoutSeconds": int((value.get("runtime") or {}).get("timeoutSeconds") or 86400),
            "openclawExecutionMode": openclaw_execution_mode,
        },
        "report": value.get("reportConfig") or {"enabled": True},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--domain-id", dest="domain_id", required=True)
    parser.add_argument("--template-name", dest="template_name")
    parser.add_argument("--template-version", dest="template_version")
    parser.add_argument("--owner-id", dest="owner_id")
    parser.add_argument("--model")
    parser.add_argument("--suite")
    parser.add_argument("--scene")
    parser.add_argument("--judge")
    parser.add_argument("--openclaw-execution-mode", choices=["local", "gateway"])
    parser.add_argument("--clawweb-url", default=os.environ.get("CLAWEVOLVE_CLAWWEB_URL") or os.environ.get("CLAWWEB_URL") or "http://127.0.0.1:5173")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    clawweb_url = args.clawweb_url.rstrip("/")
    encoded_task = urllib.parse.quote(args.task_id, safe="")
    encoded_step = urllib.parse.quote(args.step_id, safe="")
    input_url = f"{clawweb_url}/api/evolve/internal/tasks/{encoded_task}/steps/{encoded_step}/input"
    report_url = f"{clawweb_url}/api/evolve/internal/tasks/{encoded_task}/steps/{encoded_step}/report"
    workspace = Path(os.environ.get("OPENCLAW_WORKSPACE", "/home/admin/.openclaw/workspace"))
    task_root = workspace / "clawevolve_results" / args.task_id
    run_dir = task_root / "bench" / args.step_id
    config_path = run_dir / "workflow_input.json"
    state_path = run_dir / "workflow_state.json"
    result_path = run_dir / "workflow_result.json"
    handler_log = run_dir / "logs" / "handler.log"
    diagnostic(handler_log, "handler.started", taskId=args.task_id, stepId=args.step_id, commandDomainId=args.domain_id,
               commandOwnerId=args.owner_id, clawwebUrl=clawweb_url, runDir=str(run_dir))
    try:
        report(report_url, {"status": "running", "summary": f"正在运行 Bench Domain {args.domain_id}"}, log_path=handler_log)
        input_payload = http_json("GET", input_url, log_path=handler_log, event="step_input")
        config = workflow_config(input_payload, args, task_root, run_dir, clawweb_url)
        diagnostic(handler_log, "input.resolved", taskId=args.task_id, stepId=args.step_id,
                   inputTaskId=(input_payload.get("task") or {}).get("taskId"),
                   inputStepId=(input_payload.get("step") or {}).get("stepId"),
                   inputTaskUserId=(input_payload.get("task") or {}).get("userId"),
                   resolvedOwnerId=config["identity"]["ownerId"], resolvedDomainId=config["bench"]["domainId"],
                   pinnedTemplates=config["bench"]["pinnedTemplates"])
        if not config["identity"]["ownerId"]:
            raise ValueError("ownerUserId missing from Step Input")
        atomic_json(config_path, config)
        workflow = release_skill_root() / "clawevolve-bench/scripts/clawbench-workflow.py"
        process = subprocess.run(
            [sys.executable, "-u", "-B", str(workflow), "run", "--config", str(config_path), "--result", str(result_path), "--state", str(state_path)],
            text=True,
            check=False,
        )
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
        diagnostic(handler_log, "workflow.completed", exitCode=process.returncode, resultStatus=result.get("status"),
                   benchRunId=result.get("benchRunId"), resultDomainId=result.get("domainId"), resultPath=str(result_path))
        if process.returncode != 0 or result.get("status") != "succeeded":
            error = result.get("error") or {"code": "BENCH_WORKFLOW_FAILED", "message": f"workflow exit {process.returncode}", "retryable": True}
            report(report_url, {"status": "failed", "error": error}, log_path=handler_log)
            return 1
        output = {
            "benchRunId": result.get("benchRunId"),
            "domainId": result.get("domainId"),
            "runScope": result.get("runScope"),
            "templateCount": result.get("templateCount"),
            "metrics": result.get("metrics") or {},
            "detailUrl": f"{clawweb_url}/evolve/bench/runs/{urllib.parse.quote(str(result.get('benchRunId') or ''), safe='')}",
            "report": {
                key: (result.get("report") or {}).get(key)
                for key in ("status", "reportSummary", "riskLevel", "recommendations")
                if (result.get("report") or {}).get(key) is not None
            },
            "resultPath": result.get("resultPath"),
            "logPath": result.get("logPath"),
        }
        metrics = output["metrics"]
        diagnostic(handler_log, "success_report.prepared", taskId=args.task_id, stepId=args.step_id,
                   benchRunId=output["benchRunId"], reportedDomainId=output["domainId"], metrics=metrics,
                   detailUrl=output["detailUrl"])
        report(report_url, {"status": "succeeded", "summary": f"Bench {args.domain_id} 完成，得分 {metrics.get('score')} / {metrics.get('maxScore')}", "output": output}, log_path=handler_log)
        diagnostic(handler_log, "handler.succeeded", taskId=args.task_id, stepId=args.step_id, benchRunId=output["benchRunId"])
        return 0
    except Exception as exc:
        diagnostic(handler_log, "handler.failed", taskId=args.task_id, stepId=args.step_id,
                   errorType=type(exc).__name__, error=str(exc))
        try:
            report(report_url, {"status": "failed", "error": {"code": "BENCH_HANDLER_FAILED", "message": str(exc), "retryable": True}}, log_path=handler_log)
        except Exception as report_exc:
            diagnostic(handler_log, "failure_report.failed", errorType=type(report_exc).__name__, error=str(report_exc))
        print(f"clawevolve bench failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
