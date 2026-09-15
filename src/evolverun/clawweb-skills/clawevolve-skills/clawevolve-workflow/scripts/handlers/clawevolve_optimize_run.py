#!/usr/bin/env python3
"""
clawevolve_optimize_run.py — ClawEvolve Local Runner v4

第四版：砍掉 record/normalize/metrics/build-package 六个冗余步骤，
bench 产物全部 inline 到 round_state.json，日志统一写 run.log。

每轮产出仅：
  round-NNN/
    run.log, round_state.json, input/, tune/, acceptance/, spec/, artifacts/, upload/

bench 原始数据在 clawbench_results/{bench_run_id}/，不再拷贝/归一化到 round 目录。

用法:
  python3 clawevolve_optimize_run.py --action run-round --task-id <id> --round 1
  python3 clawevolve_optimize_run.py --action loop --task-id <id> --start-round 1 --max-rounds 5

watchdog 已默认启用：步骤失败自动重试/兜底并上报 ClawWeb，loop 不因单轮失败而终止。
"""

import argparse
import hashlib
import json
import math
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import time
import threading
import traceback
import urllib.parse
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_clawevolve_bench import run_clawevolve_bench
from lib_artifact_url_client import ArtifactUrlClient

# ═══════════════════════════════════════════════════════════════════════════════
# 配置 — Artifact 引用
# ═══════════════════════════════════════════════════════════════════════════════

OSS_CONFIG = {
    "BUCKET_NAME": os.environ.get("CLAWEVOLVE_ARTIFACT_BUCKET", "clawevolve-artifacts"),
    "BASE_PREFIX": os.environ.get("CLAWEVOLVE_ARTIFACT_PREFIX", "evolution"),
}

def _artifact_client(args) -> ArtifactUrlClient:
    clawweb_url = (
        getattr(args, "clawweb_url", None) or getattr(args, "clawweb_url_camel", None)
        or _env("CLAWEVOLVE_CLAWWEB_URL") or _env("CLAWWEB_URL")
        or "http://127.0.0.1:5173"
    )
    task_id = getattr(args, "task_id", None) or _env("TASK_ID") or _env("EVOLVE_RUN_ID")
    step_id = getattr(args, "step_id", None) or _env("STEP_ID")
    if not task_id or not step_id:
        raise RuntimeError("Artifact URL API requires task-id and step-id")
    return ArtifactUrlClient(clawweb_url, task_id, step_id)

# ═══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════════

def _elapsed_timeout_exceeded(started_at: float | None, timeout_seconds: int | float, *, now: float | None = None) -> bool:
    """Return whether a positive timeout has elapsed; zero/negative disables it."""
    if not started_at or not timeout_seconds or timeout_seconds <= 0:
        return False
    current = time.time() if now is None else now
    return current - started_at > timeout_seconds


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = proc.pid
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            break
        try:
            proc.wait(timeout=3 if sig == signal.SIGTERM else 5)
            break
        except subprocess.TimeoutExpired:
            continue


def _cleanup_orphan_openclaw_agents() -> int:
    # COSEC: a local developer host may run unrelated Bots. PPID/RSS cannot
    # establish ownership; cancellation must use tracked child processes only.
    if os.environ.get("CLAWWEB_VERSION") == "openversion":
        return 0
    killed = 0
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,rss,comm"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            pid, ppid, rss = parts[0], parts[1], parts[2]
            comm = " ".join(parts[3:])
            if not comm.startswith("openclaw-agent"):
                continue
            try:
                pid_int, ppid_int, rss_int = int(pid), int(ppid), int(rss)
            except ValueError:
                continue
            if ppid_int == 1 and rss_int > 50000:
                try:
                    os.kill(pid_int, signal.SIGKILL)
                    killed += 1
                except (ProcessLookupError, PermissionError):
                    pass
    except Exception:
        pass
    return killed


_OPENCLAW_BOOTSTRAP_FILES = (
    "AGENTS.md", "SOUL.md", "IDENTITY.md", "TOOLS.md", "USER.md",
    "HEARTBEAT.md", "BOOTSTRAP.md",
)


def _snapshot_openclaw_workspace_bootstrap(workspace: Path) -> dict:
    """Snapshot bootstrap/config paths before `openclaw agents add/agent`."""
    snapshot = {}
    for name in (*_OPENCLAW_BOOTSTRAP_FILES, ".git"):
        path = workspace / name
        item = {"exists": path.exists(), "is_dir": path.is_dir(), "content": None}
        try:
            if path.is_file():
                item["content"] = path.read_bytes()
        except Exception:
            item["content"] = None
        snapshot[name] = item
    return snapshot


def _cleanup_openclaw_workspace_bootstrap_since_snapshot(
    workspace: Path, before: dict, *, restore_existing: bool = False
) -> list:
    """
    Remove only bootstrap files newly created after a snapshot.

    If restore_existing=True, restore pre-existing regular files that were
    overwritten by `openclaw agents add`. This keeps the real local workspace
    clean while still allowing the later optimization agent to edit existing
    files intentionally. A pre-existing .git directory is never removed.
    """
    cleaned = []
    for name in (*_OPENCLAW_BOOTSTRAP_FILES, ".git"):
        old = before.get(name) or {"exists": False, "content": None}
        path = workspace / name
        try:
            if not old.get("exists"):
                if path.exists():
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    cleaned.append(f"removed-new:{name}")
                continue

            # Never delete or rewrite an existing .git directory.
            if name == ".git":
                continue

            if restore_existing and old.get("content") is not None and path.is_file():
                current = path.read_bytes()
                if current != old["content"]:
                    path.write_bytes(old["content"])
                    cleaned.append(f"restored-existing:{name}")
        except Exception:
            pass
    return cleaned




def _detect_openclaw_workspace_bootstrap_changes_since_snapshot(workspace: Path, before: dict) -> list:
    """Detect bootstrap/config paths created or changed after a snapshot without mutating them."""
    changed = []
    for name in (*_OPENCLAW_BOOTSTRAP_FILES, ".git"):
        old = before.get(name) or {"exists": False, "content": None}
        path = workspace / name
        try:
            if not old.get("exists"):
                if path.exists():
                    changed.append(f"created:{name}")
                continue
            if name == ".git":
                continue
            if old.get("content") is not None and path.is_file():
                try:
                    if path.read_bytes() != old["content"]:
                        changed.append(f"modified:{name}")
                except Exception:
                    changed.append(f"modified-or-unreadable:{name}")
        except Exception:
            pass
    return changed


FIXED_WORKSPACE = Path("/home/admin/.openclaw/workspace")


def _default_workspace() -> str:
    """Return the fixed workspace path for this deployment."""
    return str(FIXED_WORKSPACE)


def _resolve_workspace(args=None) -> Path:
    """Resolve the active workspace, preferring an explicit CLI arg.

    This deployment treats /home/admin/.openclaw/workspace as the canonical
    workspace. We intentionally do not silently fall back to /tmp, because that
    masks environment drift and breaks round-to-round continuity.
    """
    explicit = ""
    if args is not None:
        explicit = _clean(getattr(args, "workspace", ""))
    if os.environ.get("CLAWWEB_VERSION") == "openversion":
        # COSEC: bind every optimization stage to the Bot selected by the local
        # adapter; never silently fall back to the internal/default workspace.
        selected = os.environ.get("OPENCLAW_WORKSPACE", "").strip()
        if not selected:
            raise ValueError("Openversion requires OPENCLAW_WORKSPACE")
        workspace = Path(selected).expanduser().resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError("Openversion workspace must be a directory")
        if explicit and Path(explicit).expanduser().resolve(strict=True) != workspace:
            raise ValueError("Optimize workspace does not match the selected Bot")
        return workspace
    if explicit:
        return Path(explicit).expanduser()
    return FIXED_WORKSPACE


def _resolve_skill_base(args=None) -> Path:
    """Resolve the flat ClawEvolve Release root, never the Bot Skill root."""
    explicit = _clean(getattr(args, "skill_base_dir", "")) if args is not None else ""
    raw = explicit or _env("SKILL_BASE_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parents[3]

def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name, "")
    return str(v).strip() if v is not None and str(v).strip() else default

def _clean(v) -> str:
    s = str(v or "").strip()
    if s.startswith("{{") and s.endswith("}}"):
        return ""
    return s

def _load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def _require_acceptance_report(paths: dict) -> dict:
    """Load acceptance as a fail-closed contract for mutation-sensitive steps."""
    path = paths["accept_dir"] / "acceptance_report.json"
    report = _load_json(path, None)
    if not isinstance(report, dict):
        raise SystemExit(f"acceptance report missing or invalid: {path}")
    if not isinstance(report.get("accepted"), bool):
        raise SystemExit(f"acceptance report missing boolean accepted field: {path}")
    if not str(report.get("decision") or "").strip():
        raise SystemExit(f"acceptance report missing decision: {path}")
    if str(report.get("bench_decision") or "").strip() not in {
        "passed", "not_improved", "bench_failed", "baseline_unavailable",
    }:
        raise SystemExit(f"acceptance report missing valid bench_decision: {path}")
    if str(report.get("promotion_status") or "").strip() not in {
        "not_started", "pending", "succeeded", "failed",
    }:
        raise SystemExit(f"acceptance report missing valid promotion_status: {path}")
    return report


def _acceptance_bench_decision(report: dict) -> str:
    return str((report or {}).get("bench_decision") or "").strip()


def _candidate_passed_bench(report: dict) -> bool:
    return _acceptance_bench_decision(report) == "passed"


def _update_promotion_state(
    paths: dict,
    status: str,
    *,
    accepted: bool | None = None,
    restore_required: bool | None = None,
    error: dict | str | None = None,
) -> dict:
    """Persist promotion independently from the authoritative Bench decision."""
    acceptance_path = Path(paths["accept_dir"]) / "acceptance_report.json"
    acceptance = _load_json(acceptance_path, {})
    if not isinstance(acceptance, dict):
        acceptance = {}
    final_accepted = status == "succeeded" if accepted is None else bool(accepted)
    acceptance["promotion_status"] = status
    acceptance["accepted"] = final_accepted
    acceptance["promote_to_baseline"] = final_accepted
    if restore_required is not None:
        acceptance["restore_required"] = bool(restore_required)
    if error is not None:
        acceptance["promotion_error"] = error
    acceptance["promotion_updated_at"] = _now()
    acceptance_path.parent.mkdir(parents=True, exist_ok=True)
    acceptance_path.write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    state_path = Path(paths["round_dir"]) / "round_state.json"
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    state["promotion_status"] = status
    state["accepted"] = final_accepted
    state["acceptance"] = acceptance
    if restore_required is not None:
        state["restore_required"] = bool(restore_required)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return acceptance


def _should_stop_after_round(*, accepted: bool, validation_score, stop_score: float) -> tuple[bool, str]:
    if accepted is not True:
        return False, "candidate was not accepted"
    try:
        score = float(validation_score)
    except Exception:
        return False, "validation score unavailable"
    if score < float(stop_score):
        return False, "accepted candidate has not reached target score"
    return True, "accepted candidate reached target score"


def _normalized_score_target(value) -> float | None:
    """Validate the canonical primary-metric ratio, fail closed otherwise."""
    if isinstance(value, bool):
        return None
    try:
        target = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(target):
        return None
    return target if 0 <= target <= 1 else None


def _objective_completion_criterion(args, paths: dict) -> dict:
    """Read the sole completion target from objective.json.primary_metric."""
    objective_path = Path(paths.get("optimize_input_dir", "")) / "objective.json"
    if not objective_path.is_file():
        raise SystemExit(f"objective.json is required: {objective_path}")
    objective = _load_json(objective_path, {})
    if not isinstance(objective, dict):
        raise SystemExit(f"objective.json must contain a JSON object: {objective_path}")
    metric = objective.get("primary_metric")
    if not isinstance(metric, dict):
        raise SystemExit("objective.json primary_metric is required")
    target = _normalized_score_target(metric.get("target"))
    if target is None:
        raise SystemExit("objective.json primary_metric.target must be a ratio in [0, 1]")
    operator = str(metric.get("operator") or "").strip()
    if operator != ">=":
        raise SystemExit("objective.json primary_metric.operator must be >=")
    name = str(metric.get("name") or "").strip()
    if not name:
        raise SystemExit("objective.json primary_metric.name is required")
    return {
        "automatic_stop": True,
        "target_score": target,
        "source": "objective_primary_metric",
        "reason": "candidate Test Bench reaches objective primary metric",
        "primary_metric": {
            "name": name,
            "display_name": str(metric.get("display_name") or name),
            "operator": operator,
            "target": target,
            "unit": str(metric.get("unit") or "ratio"),
        },
    }


def _should_stop_for_objective(*, accepted: bool, validation_score, criterion: dict) -> tuple[bool, str]:
    if not criterion.get("automatic_stop"):
        return False, str(criterion.get("reason") or "objective has no automatic completion criterion")
    return _should_stop_after_round(
        accepted=accepted,
        validation_score=validation_score,
        stop_score=float(criterion["target_score"]),
    )


def _read_text(path: Path, limit: int = 200000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text if len(text) <= limit else text[:limit] + "\n\n[truncated]"

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _first_existing(paths: list) -> Optional[Path]:
    for p in paths:
        if p.exists():
            return p
    return None

def _parse_json_line(stdout: str, action: str) -> dict:
    lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    raise SystemExit(f"{action} did not produce JSON on stdout. stdout={stdout!r}")

def _print_json(data: dict):
    print(json.dumps(data, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════════════════════
# 统一日志 — run.log
# ═══════════════════════════════════════════════════════════════════════════════

def _log(args, step: str, msg: str, level: str = "INFO"):
    """Append a line to run.log."""
    try:
        paths = resolve_paths(args)
        log_path = paths["round_dir"] / "run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{level}] [{step}] {msg}\n")
    except Exception:
        pass

def _log_block(args, step: str, title: str, content: str):
    """Append a multi-line block to run.log."""
    try:
        paths = resolve_paths(args)
        log_path = paths["round_dir"] / "run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n[{ts}] [{step}] ═══ {title} ═══\n")
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
            f.write(f"[{ts}] [{step}] ═══ END {title} ═══\n\n")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Bench summary — inline 计算，不依赖 normalize 脚本
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_bench_summary(result_path: Path) -> dict:
    """Read raw benchmark report and compute summary stats inline."""
    report = _load_json(result_path)
    if not report:
        return {"score": None, "pass_rate": None, "total": 0, "passed": 0, "failed": 0, "errors": 0}
    tasks = report.get("tasks") or []
    scores = []
    passed = failed = errors = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue
        grading = task.get("grading") or {}
        runs = grading.get("runs") or []
        score = None
        if runs and isinstance(runs[0], dict) and runs[0].get("score") is not None:
            score = float(runs[0]["score"])
        elif grading.get("mean") is not None:
            score = float(grading["mean"])
        if score is not None and not math.isnan(score):
            scores.append(score)
        raw_status = str(task.get("status") or "").lower()
        if task.get("timed_out") or raw_status in {"timeout", "timed_out"}:
            errors += 1
        elif raw_status in {"error", "failed", "failure"}:
            errors += 1 if "error" in raw_status else 0
            failed += 1 if "error" not in raw_status else 0
        elif score is not None and score >= 0.999:
            passed += 1
        else:
            failed += 1
    total = len(tasks)
    mean_score = (sum(scores) / len(scores)) if scores else None
    pass_rate = (passed / total) if total else None
    efficiency = report.get("efficiency") or {}
    return {
        "score": mean_score,
        "pass_rate": pass_rate,
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "total_tokens": efficiency.get("total_tokens"),
        "total_requests": efficiency.get("total_requests"),
        "total_execution_time_seconds": efficiency.get("total_execution_time_seconds"),
        "model": report.get("model"),
        "benchmark_version": report.get("benchmark_version"),
    }


def _summary_from_workflow_result(result: dict[str, Any]) -> dict:
    """Translate clawbench-workflow result fields into the local summary shape."""
    metrics = result.get("metrics") if isinstance(result, dict) else {}
    if not isinstance(metrics, dict):
        metrics = {}
    score = metrics.get("score")
    pass_rate = metrics.get("passRate")
    case_count = metrics.get("caseCount")
    try:
        total = int(case_count) if case_count is not None else 0
    except Exception:
        total = 0
    return {
        "score": score,
        "pass_rate": pass_rate,
        "total": total,
        "passed": None,
        "failed": None,
        "errors": None,
        "total_tokens": metrics.get("totalTokens"),
        "total_requests": metrics.get("totalRequests"),
        "total_execution_time_seconds": metrics.get("totalExecutionTimeSeconds"),
        "model": result.get("model"),
        "benchmark_version": result.get("benchmarkVersion"),
    }


def _resolve_bench_result_artifacts(output_dir: Path) -> tuple[str, dict[str, Any]]:
    """Return the best known report path plus any workflow result metadata."""
    workflow_result_path = output_dir.parent / "workflow_result.json"
    if workflow_result_path.is_file():
        workflow_result = _load_json(workflow_result_path, {})
        if isinstance(workflow_result, dict):
            candidate_path = str(workflow_result.get("resultPath") or "").strip()
            if candidate_path:
                candidate = Path(candidate_path)
                if not candidate.is_absolute():
                    candidate = workflow_result_path.parent / candidate_path
                if candidate.is_file():
                    return str(candidate), workflow_result
            nested_report = workflow_result.get("report")
            if isinstance(nested_report, dict):
                nested_path = str(nested_report.get("resultPath") or "").strip()
                if nested_path:
                    candidate = Path(nested_path)
                    if not candidate.is_absolute():
                        candidate = workflow_result_path.parent / nested_path
                    if candidate.is_file():
                        return str(candidate), workflow_result
            return "", workflow_result
    reports = sorted(output_dir.glob("**/*_benchmark_report.json"))
    return (str(reports[-1]) if reports else "", {})


def _build_evolution_history_text(args, paths: dict, max_rounds: int = 5, *, include_validation: bool = False) -> str:
    """Build concise prior-round history for tune prompts.

    This closes the loop for rejected/exploratory candidates: logs are already
    retained on disk, but tune needs a compact, explicit summary in-context.
    """
    manifest = _load_json(paths["run_dir"] / "optimize" / "output" / "optimize_manifest.json", {})
    if not isinstance(manifest, dict):
        manifest = {}
    lines = ["## Evolution History"]
    if manifest.get("last_accepted_round"):
        lines.extend([
            "### Last accepted baseline",
            f"- round: {manifest.get('last_accepted_round')}",
            f"- validation_score: {manifest.get('last_accepted_validation_score')}",
            f"- artifact: {manifest.get('last_accepted_artifact', '')}",
        ])
    rounds = manifest.get("rounds") if isinstance(manifest.get("rounds"), list) else []
    recent = [r for r in rounds if int(r.get("round_id") or 0) < int(args.round)]
    recent = sorted(recent, key=lambda r: int(r.get("round_id") or 0), reverse=True)[:max_rounds]
    if recent:
        lines.append("### Recent rounds")
        for r in recent:
            round_line = (
                f"- round {r.get('round_id')}: decision={r.get('decision')}, accepted={r.get('accepted')}, "
                f"optimization_score={r.get('optimization_score')}, restore_required={r.get('restore_required')}"
            )
            if include_validation:
                round_line += f", validation_score={r.get('validation_score')}"
            lines.append(round_line)
            cs = r.get("change_summary") if isinstance(r.get("change_summary"), dict) else {}
            touched = cs.get("touched_paths") or []
            if touched:
                lines.append(f"  touched_paths: {', '.join(str(x) for x in touched[:8])}")
    retained = manifest.get("retained_candidates") if isinstance(manifest.get("retained_candidates"), list) else []
    active = []
    for item in retained:
        if not isinstance(item, dict):
            continue
        try:
            if str(item.get("status") or "active") == "active" and int(item.get("expires_after_round") or 0) >= int(args.round):
                active.append(item)
        except Exception:
            active.append(item)
    if active:
        lines.append("### Active exploratory candidates (not baseline; use as clues only)")
        for item in active[-max_rounds:]:
            lines.append(
                f"- round {item.get('round_id')}: score_delta={item.get('score_delta')}, "
                f"expires_after_round={item.get('expires_after_round')}, next_round_plan={item.get('next_round_plan', '')}"
            )
            # Do not inline candidate_summary files here. They may contain per-case
            # or validation-derived text; effect memory must come from the structured
            # experiment ledger/change manifest instead.
    ledger_path = paths["optimize_output_dir"] / "experiment_ledger.jsonl"
    ledger_by_round = {}
    if ledger_path.is_file():
        for raw_line in ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(raw_line)
                if isinstance(entry, dict):
                    ledger_by_round[int(entry.get("round_id") or 0)] = entry
            except Exception:
                continue
    structured = []
    for rid in sorted(ledger_by_round, reverse=True):
        if rid >= int(args.round):
            continue
        entry = ledger_by_round[rid]
        effect_design = entry.get("effect_design") if isinstance(entry.get("effect_design"), dict) else {}
        complexity = entry.get("complexity_budget") if isinstance(entry.get("complexity_budget"), dict) else {}
        preserved = entry.get("preserved_mechanisms") if isinstance(entry.get("preserved_mechanisms"), list) else []
        if not (effect_design or complexity or preserved):
            continue
        structured.append((rid, effect_design, complexity, preserved))
        if len(structured) >= max_rounds:
            break
    if structured:
        lines.append("### Structured effect memory (behavior clues; not proof of generalization)")
        for rid, effect_design, complexity, preserved in structured:
            lines.append(
                f"- round {rid}: strategy={effect_design.get('change_strategy', 'unknown')}, "
                f"reasoning_expected={_compact_text(effect_design.get('reasoning_quality_expected'), 180)}, "
                f"delivery_expected={_compact_text(effect_design.get('delivery_reliability_expected'), 180)}, "
                f"critical_path_delta={complexity.get('critical_path_delta', 'unknown')}, "
                f"complexity_risk={complexity.get('complexity_risk', 'unknown')}"
            )
            names = [str(x.get("mechanism") or "").strip() for x in preserved if isinstance(x, dict) and str(x.get("mechanism") or "").strip()]
            if names:
                lines.append(f"  preserved_mechanisms: {', '.join(names[:8])}")
    if len(lines) == 1:
        lines.append("(no prior accepted/rejected/exploratory history available)")
    lines.append("\nGuidance: do not memorize a retained/rejected case. Extract reusable lessons, keep baseline safety, and prefer full-coverage evidence for acceptance.")
    return "\n".join(lines)


def _bench_summary_text(args, kind: str) -> str:
    """Read raw benchmark report and return a human-readable summary for prompts."""
    state = _load_json(resolve_paths(args)["round_dir"] / "round_state.json", {})
    bench = (state.get("bench") or {}).get(kind) or {}
    result_path = bench.get("resultPath", "")
    if not result_path or not Path(result_path).exists():
        return f"({kind} bench result not available)"
    report = _load_json(Path(result_path), {})
    if not report:
        return f"({kind} bench result unreadable: {result_path})"
    summary = _compute_bench_summary(Path(result_path))
    lines = [
        f"## {kind} Bench Result",
        f"- score: {summary['score']}",
        f"- pass_rate: {summary['pass_rate']}",
        f"- total: {summary['total']}, passed: {summary['passed']}, failed: {summary['failed']}, errors: {summary['errors']}",
        f"- total_tokens: {summary.get('total_tokens')}",
        f"- model: {summary.get('model')}",
        "",
    ]
    for task in report.get("tasks") or []:
        grading = task.get("grading") or {}
        runs = grading.get("runs") or []
        score = runs[0].get("score") if runs and isinstance(runs[0], dict) else None
        fm = task.get("frontmatter") or {}
        lines.append(f"  - {task.get('task_id')}: status={task.get('status')}, score={score}, timed_out={task.get('timed_out')}, name={fm.get('name','')}")
        if runs and isinstance(runs[0], dict):
            breakdown = runs[0].get("breakdown") or {}
            if breakdown:
                lines.append(f"    breakdown: {json.dumps(breakdown, ensure_ascii=False)}")
    return "\n".join(lines)


def _baseline_optimization_prompt_text(args) -> str:
    """Render the accepted baseline optimization evidence for Tune.

    Tune runs before candidate optimization exists, so reading bench.optimization
    here is a temporal bug. The accepted registry is the sole authority.
    """
    paths = resolve_paths(args)
    state = _load_json(paths["round_dir"] / "round_state.json", {})
    baseline = state.get("baseline_optimization") if isinstance(state, dict) else {}
    if not isinstance(baseline, dict):
        baseline = {}
    result_path = str(baseline.get("result_path") or "")
    report = _load_json(Path(result_path), {}) if result_path else {}
    summary = baseline.get("summary") if isinstance(baseline.get("summary"), dict) else {}
    identity = baseline.get("identity") if isinstance(baseline.get("identity"), dict) else {}
    lines = [
        "## Accepted Baseline Optimization",
        f"- baseline_source: {baseline.get('source')}",
        f"- baseline_round: {baseline.get('round_id')}",
        f"- baseline_score: {summary.get('score')}",
        f"- result_path: {result_path}",
        f"- evaluation_identity_cache_complete: {identity.get('cache_complete', identity.get('complete'))}",
        f"- evaluation_identity_causal_complete: {identity.get('causal_comparability_complete', identity.get('complete'))}",
        f"- evaluation_identity_missing: {identity.get('missing_fields') or identity.get('causal_missing_fields') or []}",
        "",
        "### Task-level Optimization Evidence",
    ]
    for task in report.get("tasks") or []:
        tid = str(task.get("task_id") or task.get("id") or task.get("name") or "").strip()
        grading = task.get("grading") if isinstance(task.get("grading"), dict) else {}
        runs = grading.get("runs") if isinstance(grading.get("runs"), list) else []
        run = runs[0] if runs and isinstance(runs[0], dict) else {}
        score = run.get("score", grading.get("mean"))
        lines.append(f"- {tid}: status={task.get('status')}, score={score}, timed_out={task.get('timed_out')}")
        breakdown = _flatten_breakdown_metrics(run.get("breakdown") if isinstance(run.get("breakdown"), dict) else {})
        if breakdown:
            lines.append(f"  breakdown: {json.dumps(breakdown, ensure_ascii=False, sort_keys=True)}")
    if not report:
        lines.append("- report unavailable; Tune must not infer candidate behavior from this absence")
    return "\n".join(lines)



def _metric_failure_role(metric: str) -> str:
    """Map heterogeneous benchmark dimensions to a stable capability role."""
    key = str(metric or "").lower()
    semantic_markers = (
        "风险识别", "expert", "accuracy", "correct", "requirement_type",
        "external_requirement", "key_fields", "ambigu", "refund", "risk_",
        "classification", "label",
    )
    delivery_markers = (
        "输出格式", "has_risk_list", "has_other_review", "confirm", "流程",
        "validator", "stage", "tool_called", "bizid_correct", "file_path",
        "completion", "format",
    )
    # Specific execution controls win over the generic suffix "correct".
    if any(marker in key for marker in delivery_markers):
        return "delivery_reliability"
    if any(marker in key for marker in semantic_markers):
        return "semantic_accuracy"
    return "other"


def _metric_priority_weight(metric: str) -> float:
    """Return a ranking weight, not a reconstruction of the private scorer.

    Composite task-quality dimensions and holistic judge dimensions should lead
    diagnosis.  Leaf keyword checks remain useful evidence but must not outrank
    the capability-level signal they feed.
    """
    key = str(metric or "").lower()
    if any(token in key for token in ("key_fields_accuracy", "风险识别准确度", "overall", "task_completion")):
        return 3.0
    if any(token in key for token in ("requirement_type_correct", "external_requirement_type_correct", "法务调整建议质量")):
        return 2.0
    if key.startswith("llm_judge."):
        return 1.75
    if key.startswith("automated.expert_"):
        return 1.0
    return 1.25



def _flatten_breakdown_metrics(value: dict, prefix: str = "") -> dict[str, Any]:
    """Flatten nested grader breakdowns without changing already-flat keys.

    Most ClawBench reports use keys such as ``automated.expert_x``.  Some
    adapters and hand-authored reports instead emit ``{"automated":
    {"expert_x": ...}}``.  Scene diagnosis must see the same metrics in both
    representations or Tune silently loses the strongest domain signal.
    """
    out: dict[str, Any] = {}
    if not isinstance(value, dict):
        return out
    for raw_key, raw_value in value.items():
        key = f"{prefix}.{raw_key}" if prefix else str(raw_key)
        if isinstance(raw_value, dict):
            out.update(_flatten_breakdown_metrics(raw_value, key))
        else:
            out[key] = raw_value
    return out


def _optimization_scene_playbook_text(args) -> str:
    """Route Tune to the decision layer that can move the observed scorer.

    This is deliberately evidence-driven rather than skill-name-driven.  It
    uses only exposed optimization behavior, never reads held-out task details,
    and avoids brittle task-id/answer maps.  The emitted guidance is
    still a hypothesis: Tune must verify the authoritative runtime source before
    editing one prompt-level variable.
    """
    paths = resolve_paths(args)
    state = _load_json(paths["round_dir"] / "round_state.json", {})
    baseline = state.get("baseline_optimization") if isinstance(state, dict) else {}
    result_path = str((baseline or {}).get("result_path") or "")
    report = _load_json(Path(result_path), {}) if result_path else {}
    tasks = report.get("tasks") if isinstance(report, dict) else []
    if not isinstance(tasks, list) or not tasks:
        return "## Scene Optimization Playbook\n- scene_family: unknown\n- no task-level optimization evidence"

    pair_tasks = 0
    pair_both_low = 0
    pair_internal_low = 0
    pair_external_low = 0
    pair_delivery_clean = 0
    legal_tasks = 0
    legal_expert_total = 0
    legal_expert_low = 0
    legal_judge_accuracy_low = 0
    legal_format_low = 0
    legal_process_clean_semantic_low = 0
    truncation_notes = 0

    for task in tasks:
        grading = task.get("grading") if isinstance(task.get("grading"), dict) else {}
        runs = grading.get("runs") if isinstance(grading.get("runs"), list) else []
        run = runs[0] if runs and isinstance(runs[0], dict) else {}
        metrics = _flatten_breakdown_metrics(run.get("breakdown") or {})
        numeric = {str(k).lower(): _metric_number(v) for k, v in metrics.items()}
        numeric = {k: v for k, v in numeric.items() if v is not None}

        internal = next((v for k, v in numeric.items() if "requirement_type_correct" in k and "external_" not in k), None)
        external = next((v for k, v in numeric.items() if "external_requirement_type_correct" in k), None)
        key_fields = next((v for k, v in numeric.items() if "key_fields_accuracy" in k), None)
        if internal is not None and external is not None:
            pair_tasks += 1
            pair_internal_low += int(internal < 0.8)
            pair_external_low += int(external < 0.8)
            pair_both_low += int(internal < 0.8 and external < 0.8)
            delivery = [
                v for k, v in numeric.items()
                if any(token in k for token in ("tool_called_correctly", "bizid_correct", "file_path_correct"))
            ]
            if delivery and min(delivery) >= 0.8 and (key_fields is None or key_fields < 0.8):
                pair_delivery_clean += 1

        expert_values = [v for k, v in numeric.items() if k.startswith("automated.expert_")]
        judge_accuracy = next((v for k, v in numeric.items() if "llm_judge." in k and "风险识别准确度" in k), None)
        judge_format = next((v for k, v in numeric.items() if "llm_judge." in k and "输出格式合规" in k), None)
        process_values = [
            v for k, v in numeric.items()
            if any(token in k for token in (
                "confirm_present", "确认动作", "frameworks_read", "validators_run",
                "repair_pass", "dual_verification", "流程与验证器门控",
                "yuque_doc_read", "evidence_based",
            ))
        ]
        if expert_values or judge_accuracy is not None:
            legal_tasks += 1
            legal_expert_total += len(expert_values)
            legal_expert_low += sum(v < 0.8 for v in expert_values)
            legal_judge_accuracy_low += int(judge_accuracy is not None and judge_accuracy < 0.8)
            legal_format_low += int(judge_format is not None and judge_format < 0.8)
            semantic_low = any(v < 0.8 for v in expert_values) or (judge_accuracy is not None and judge_accuracy < 0.8)
            if semantic_low and process_values and min(process_values) >= 0.8:
                legal_process_clean_semantic_low += 1
        notes = str(run.get("notes") or "")
        if re.search(r"截断|输出不完整|对话.{0,12}不完整|缺失【文案瑕疵】|缺失【其他审查项说明】", notes):
            truncation_notes += 1

    lines = ["## Scene Optimization Playbook"]
    if pair_tasks:
        lines.extend([
            "- scene_family: paired_business_taxonomy",
            f"- paired_tasks: {pair_tasks}; both_axes_low: {pair_both_low}; internal_axis_low: {pair_internal_low}; external_axis_low: {pair_external_low}; delivery_clean_semantic_low: {pair_delivery_clean}",
            "- causal routing: persisted requirementType and externalRequirementType are a coupled semantic contract. When tool/bizId/path controls pass, the failure is upstream classification/reconciliation, not workflow delivery.",
            "- authoritative-layer rule: inspect the earliest runtime prompt or reconciliation function that emits the persisted enum values. Prefer changing that source-of-decision prompt over adding a downstream SKILL checklist or asking the outer agent to rewrite JSON after the script finishes.",
            "- taxonomy repair target: preserve exact canonical enum labels; classify the internal and external axes independently; then reconcile impossible or contradictory pairs before persistence. Add compact contrast boundaries and negative examples only for confusions evidenced by optimization data/runtime source.",
            "- underexposed-safe specialization: if only one optimization class is exposed, do not encode its bizId or answer. Encode a reusable decision invariant (evidence span -> axis decision -> exact enum -> pair consistency) that can also distinguish sibling labels.",
            "- proposal preference: a one-hunk replace/merge inside an existing classifier prompt or reconciliation prompt outranks an appended post-processing step. A wrapper-only repair is low-confidence unless history proves that the wrapper actually changes both persisted fields.",
            "- existing correct behavior: keep fetch/analyze invocation, bizId propagation, output path, resumability, and already-correct taxonomy classes unchanged.",
        ])
    if legal_tasks:
        expert_miss_ratio = legal_expert_low / legal_expert_total if legal_expert_total else 0.0
        lines.extend([
            "- scene_family: legal_risk_review" if not pair_tasks else "- additional_scene_family: legal_risk_review",
            f"- legal_tasks: {legal_tasks}; expert_leaf_miss_ratio: {expert_miss_ratio:.3f}; judge_accuracy_low_cases: {legal_judge_accuracy_low}; format_low_cases: {legal_format_low}; process_clean_but_semantic_low: {legal_process_clean_semantic_low}; truncation_note_cases: {truncation_notes}",
            "- causal routing: when Yuque/evidence/framework/validator/confirm controls pass but expert leaves or holistic accuracy fail, do not add another stage, validator, framework read, or broad checklist. The bottleneck is exact risk-atom discovery and preservation into the final answer.",
            "- risk-atom contract: each material ambiguity must survive as one independent item with (subject, action/benefit, condition or boundary, what is missing/conflicting, exact source quote, concrete replacement wording). Do not merge two expert-sized ambiguities into one generic '规则不清晰' item.",
            "- contrast audit: compare parallel clauses, sibling rewards/coupons, channels, time windows, user identities, quantities, eligibility, ownership/use rights, refund/return rules, organizer responsibility, and cross-terminal behavior. Report only evidence-backed asymmetric or undefined terms; do not spray speculative risks.",
            "- scorer-alignment rule: keyword presence is supporting evidence, not completion. Preserve exact quoted evidence and make the finding name semantically explicit enough for a holistic judge; one item must map to one ambiguity and one actionable remedy.",
            "- delivery budget rule: if format/truncation also fails, replace or merge verbose stage narration/checklists to reclaim output budget. Produce the compact complete four-section legal opinion before confirmation narration; never rely on 'written to file' or a later summary as the scored delivery.",
            "- anti-pattern from observable metrics: process controls already near ceiling cannot justify process expansion. Prefer one prompt-level replace/merge at the Stage-2 discovery or Stage-3 final-answer contract, selected according to whether semantic coverage or truncation has the larger weighted loss.",
            "- existing correct behavior: retain exact quotations, no-fabrication discipline, four-section headings, actionable legal wording, validator/confirm safety, and already-hit expert leaves.",
        ])
    if not pair_tasks and not legal_tasks:
        lines.extend([
            "- scene_family: generic",
            "- no specialized scorer signature detected; follow the ranked loss dimensions and edit the earliest authoritative decision prompt.",
        ])
    lines.extend([
        "",
        "### Candidate selection override",
        "- Treat this playbook as routing guidance, not permission to hardcode task ids, bizIds, document titles, grader tokens, or expected answers.",
        "- Before selecting a proposal, name the authoritative value-producing layer and explain why the proposed edit can change the failed persisted/output behavior in the same run.",
        "- Reject proposals that only restate an already-passing process requirement or add mandatory work without deleting/merging equivalent prompt load.",
    ])
    return "\n".join(lines)


def _baseline_generalization_gap(paths: dict, optimization_score: float | None, task_count: int) -> dict:
    optimize_input = paths.get("optimize_input_dir")
    objective = _load_json(Path(optimize_input) / "objective.json", {}) if optimize_input else {}
    test_score = _finite_number((objective or {}).get("test_baseline_score"))
    opt_score = _finite_number((objective or {}).get("optimization_baseline_score"))
    if opt_score is None:
        opt_score = optimization_score
    gap = (opt_score - test_score) if opt_score is not None and test_score is not None else None
    underexposed = task_count < 3
    material = gap is not None and gap >= 0.10
    return {
        "optimization_score": opt_score,
        "test_score": test_score,
        "gap": gap,
        "optimization_task_count": task_count,
        "underexposed": underexposed,
        "material": material,
        "actionable_coverage_risk": bool(material and underexposed),
    }


def _optimization_failure_profile_text(args) -> str:
    """Build a compact loss-and-mechanism profile from optimization evidence.

    Only optimization task details are consumed.  The held-out side contributes
    at most its aggregate baseline score already present in objective.json; no
    validation ids, rubrics, answers, reports, or transcripts are read.
    """
    paths = resolve_paths(args)
    state = _load_json(paths["round_dir"] / "round_state.json", {})
    baseline = state.get("baseline_optimization") if isinstance(state, dict) else {}
    result_path = str((baseline or {}).get("result_path") or "")
    report = _load_json(Path(result_path), {}) if result_path else {}
    tasks = report.get("tasks") if isinstance(report, dict) else []
    if not isinstance(tasks, list) or not tasks:
        return "## Optimization Failure Profile\n- unavailable: baseline optimization report has no tasks"

    metric_failures: dict[str, list[tuple[str, float, float]]] = {}
    semantic_cases: list[tuple[str, list[tuple[str, float]]]] = []
    delivery_cases: list[tuple[str, list[tuple[str, float]]]] = []
    scorer_conflicts: list[str] = []
    classification_pair_failures: list[str] = []
    semantic_only_failures: list[str] = []
    note_excerpts: list[tuple[str, str]] = []
    task_scores: list[float] = []

    for task in tasks:
        tid = str(task.get("task_id") or task.get("id") or task.get("name") or "unknown")
        grading = task.get("grading") if isinstance(task.get("grading"), dict) else {}
        runs = grading.get("runs") if isinstance(grading.get("runs"), list) else []
        run = runs[0] if runs and isinstance(runs[0], dict) else {}
        score = _finite_number(run.get("score", grading.get("mean")))
        if score is not None:
            task_scores.append(score)
        breakdown = _flatten_breakdown_metrics(run.get("breakdown") if isinstance(run.get("breakdown"), dict) else {})
        low_semantic: list[tuple[str, float]] = []
        low_delivery: list[tuple[str, float]] = []
        automated_expert_high = False
        llm_accuracy_low = False
        delivery_controls: list[float] = []
        semantic_controls: dict[str, float] = {}

        for key, raw in breakdown.items():
            value = _metric_number(raw)
            if value is None:
                continue
            role = _metric_failure_role(str(key))
            if role == "delivery_reliability":
                delivery_controls.append(value)
            elif role == "semantic_accuracy":
                semantic_controls[str(key).lower()] = value
            if value < 0.8:
                loss = (1.0 - value) * _metric_priority_weight(str(key))
                metric_failures.setdefault(str(key), []).append((tid, value, loss))
                if role == "semantic_accuracy":
                    low_semantic.append((str(key), value))
                elif role == "delivery_reliability":
                    low_delivery.append((str(key), value))
            if str(key).startswith("automated.expert_") and value >= 0.99:
                automated_expert_high = True
            if "llm_judge." in str(key) and "风险识别准确度" in str(key) and value <= 0.5:
                llm_accuracy_low = True

        if low_semantic:
            semantic_cases.append((tid, low_semantic))
        if low_delivery:
            delivery_cases.append((tid, low_delivery))
        if automated_expert_high and llm_accuracy_low:
            scorer_conflicts.append(tid)

        internal = next((v for k, v in semantic_controls.items() if "requirement_type_correct" in k and "external_" not in k), None)
        external = next((v for k, v in semantic_controls.items() if "external_requirement_type_correct" in k), None)
        key_fields = next((v for k, v in semantic_controls.items() if "key_fields_accuracy" in k), None)
        if internal is not None and external is not None and min(internal, external) < 0.8:
            classification_pair_failures.append(tid)
        if low_semantic and delivery_controls and min(delivery_controls) >= 0.8:
            semantic_only_failures.append(tid)
        if key_fields is not None and key_fields < 0.8 and internal is not None and external is not None:
            # This is the high-leverage paired-classifier contract used by data
            # preprocessing tasks: delivery succeeded, but both decision axes or
            # their reconciliation/output mapping are wrong.
            if tid not in classification_pair_failures:
                classification_pair_failures.append(tid)

        notes = run.get("notes")
        if notes:
            if isinstance(notes, dict):
                notes = json.dumps(notes, ensure_ascii=False, sort_keys=True)
            note_excerpts.append((tid, _compact_text(notes, 700)))

    optimization_score = sum(task_scores) / len(task_scores) if task_scores else None
    coverage = _baseline_generalization_gap(paths, optimization_score, len(tasks))
    ranked = sorted(
        metric_failures.items(),
        key=lambda item: (-sum(loss for _, _, loss in item[1]), -len(item[1]), sum(v for _, v, _ in item[1]) / len(item[1]), item[0]),
    )
    lines = [
        "## Optimization Failure Profile",
        f"- task_count: {len(tasks)}",
        f"- optimization_score: {optimization_score}",
        f"- semantic_accuracy_cases: {len(semantic_cases)}",
        f"- delivery_reliability_cases: {len(delivery_cases)}",
        f"- semantic_only_failures: {len(semantic_only_failures)}",
        f"- paired_classification_failures: {len(classification_pair_failures)}",
        f"- scorer_conflict_cases: {len(scorer_conflicts)}",
        f"- aggregate_generalization_gap: {coverage.get('gap')}",
        f"- optimization_underexposed: {coverage.get('underexposed')}",
        "",
        "### Ranked loss dimensions (coverage × severity × capability weight)",
    ]
    if ranked:
        for key, failures in ranked[:12]:
            mean = sum(v for _, v, _ in failures) / len(failures)
            impact = sum(loss for _, _, loss in failures)
            ids = ", ".join(tid for tid, _, _ in failures[:8])
            lines.append(f"- {key}: cases={len(failures)}, mean={mean:.3f}, weighted_loss={impact:.3f}, task_ids={ids}")
    else:
        lines.append("- none observed in optimization tasks")

    lines.extend(["", "### Mechanism routing"])
    if classification_pair_failures:
        lines.append("- paired classifier contract is weak: requirementType and externalRequirementType must be treated as one mutually-constrained decision. Inspect both prompts, reconciliation, checkpoint restore aliases, and final output mapping; do not patch only one label axis.")
        lines.append(f"  affected_optimization_task_ids: {', '.join(classification_pair_failures[:12])}")
    if semantic_only_failures:
        lines.append("- semantic-only failure: tool invocation, identifier propagation, and output path succeeded while business judgment failed. Prioritize decision boundaries/reconciliation over workflow or polling changes.")
    if semantic_cases:
        lines.append("- semantic_accuracy is weak: inspect decision boundaries, omitted contrast axes, evidence-to-conclusion preservation, and false merging of independent findings.")
    if delivery_cases:
        lines.append("- delivery_reliability is weak: inspect output truncation, excessive intermediate narration, stage/confirm completion, and redundant tool/validator loops.")
    if scorer_conflicts:
        lines.append("- scorer conflict detected: keyword-style automated checks passed while holistic judge accuracy stayed low; do not optimize to token presence. Repair evidence → exact finding → independent item → actionable recommendation.")
        lines.append(f"  affected_optimization_task_ids: {', '.join(scorer_conflicts[:12])}")
    if coverage.get("actionable_coverage_risk"):
        lines.append("- COVERAGE RISK: optimization is underexposed and materially outperforms the held-out aggregate. A no-op is not evidence-based. Without reading held-out case details, audit runtime source for brittle literal rules, asymmetric paired decisions, stale aliases, and missing generic invariants; select one bounded robustness experiment supported by source evidence.")
    elif not (semantic_cases or delivery_cases or scorer_conflicts):
        lines.append("- no dominant optimization failure observed; preserve current behavior.")

    if note_excerpts:
        lines.extend(["", "### Judge note excerpts (optimization only; capped)"])
        for tid, note in note_excerpts[:10]:
            lines.append(f"- {tid}: {note}")
    lines.extend([
        "",
        "### Selection rule",
        "- Rank by repeated weighted loss, then verify the mechanism in raw optimization evidence and runtime source.",
        "- Prefer the smallest runtime-reachable change that repairs the highest-loss reusable mechanism.",
        "- When delivery controls pass and semantic controls fail, do not spend the round on output format, polling, or tool routing.",
        "- Preserve semantic accuracy and delivery reliability separately; a gain in one cannot compensate for collapse of the other.",
        "- Aggregate held-out gap may justify a generic robustness audit, but never reveals or authorizes use of held-out task ids, rubrics, answers, reports, or transcripts.",
    ])
    return "\n".join(lines)

def _bench_aggregate_summary_text(args, kind: str) -> str:
    """Expose only aggregate validation evidence to Review.

    Validation task ids, rubric fields, notes, transcripts, and case-specific
    scores must not flow back through spec-vN into the next Tune prompt.
    """
    state = _load_json(resolve_paths(args)["round_dir"] / "round_state.json", {})
    bench = (state.get("bench") or {}).get(kind) or {}
    summary = bench.get("summary") or {}
    return "\n".join([
        f"## {kind} Aggregate Bench Result",
        f"- score: {summary.get('score')}",
        f"- pass_rate: {summary.get('pass_rate')}",
        f"- total: {summary.get('total')}, passed: {summary.get('passed')}, failed: {summary.get('failed')}, errors: {summary.get('errors')}",
        f"- total_tokens: {summary.get('total_tokens')}",
        f"- model: {summary.get('model')}",
        "- privacy: task ids, per-case scores, rubric fields, notes, and transcripts are intentionally withheld from strategy evolution",
    ])


def _sanitize_review_payload(value, validation_task_ids: set[str]):
    if isinstance(value, dict):
        return {key: _sanitize_review_payload(item, validation_task_ids) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_review_payload(item, validation_task_ids) for item in value]
    if not isinstance(value, str):
        return value
    # Strip the rubric-derived `expert_` prefix but KEEP the mechanism name so
    # failure_signature stays a stable, distinct dedup key across rounds
    # (collapsing different expert_xxx signatures to one [redacted_rubric]
    # would merge unrelated failure mechanisms in failure_registry.json).
    text = re.sub(r"\bexpert_([A-Za-z0-9_]+)\b", r"\1", value, flags=re.I)
    for task_id in sorted(validation_task_ids, key=len, reverse=True):
        if task_id:
            text = re.sub(re.escape(task_id), "[redacted_validation_case]", text, flags=re.I)
    return text


def _acceptance_for_review(acc: dict, validation_task_ids: set[str] | None = None) -> dict:
    """Build a small, validation-safe payload for Review.

    Review is advisory: it explains the Bench result and proposes the next Spec.
    Legacy gate/protected-signal fields are intentionally not forwarded because
    they must not become a second acceptance decision.
    """
    acc = acc if isinstance(acc, dict) else {}
    score = acc.get("score") if isinstance(acc.get("score"), dict) else {}
    train = acc.get("train") if isinstance(acc.get("train"), dict) else {}
    payload = {
        "schema_version": "evolution.review_acceptance.bench_review.v1",
        "bench_decision": acc.get("bench_decision") or acc.get("decision"),
        "accepted": acc.get("accepted") if isinstance(acc.get("accepted"), bool) else None,
        "reason": acc.get("reason"),
        "score": {
            "name": score.get("name", "test_score"),
            "baseline": score.get("baseline"),
            "candidate": score.get("candidate"),
            "delta": score.get("delta"),
        },
        "train": {
            "baseline": train.get("baseline"),
            "candidate": train.get("candidate"),
            "delta": train.get("delta"),
            "role": "informational",
        },
        "review": {"status": acc.get("review_status") or "pending"},
    }
    return _sanitize_review_payload(payload, validation_task_ids or set())

def _report_task_ids(path: str | Path) -> set[str]:
    report = _load_json(Path(path), {}) if path else {}
    return {
        str(task.get("task_id") or task.get("id") or task.get("name") or "").strip()
        for task in (report.get("tasks") or [])
        if str(task.get("task_id") or task.get("id") or task.get("name") or "").strip()
    }


def _round_task_id_sets(paths: dict) -> tuple[set[str], set[str]]:
    state = _load_json(paths.get("round_dir", Path()) / "round_state.json", {}) if paths.get("round_dir") else {}
    bench = state.get("bench") or {} if isinstance(state, dict) else {}
    opt_path = ((bench.get("optimization") or {}).get("resultPath"))
    val_path = ((bench.get("validation") or {}).get("resultPath"))
    return _report_task_ids(opt_path), _report_task_ids(val_path)


def _known_validation_task_ids(paths: dict) -> set[str]:
    task_ids = set()
    run_dir = Path(paths.get("run_dir") or "")
    bootstrap_reports = sorted((run_dir / "bench" / "baseline").glob("**/test/**/*_benchmark_report.json")) if run_dir else []
    for report in bootstrap_reports:
        task_ids.update(_report_task_ids(report))
    output_dir = Path(paths.get("optimize_output_dir") or "")
    if output_dir.is_dir():
        for state_path in sorted(output_dir.glob("round-*/round_state.json")):
            state = _load_json(state_path, {})
            result_path = ((((state.get("bench") or {}).get("validation") or {}).get("resultPath"))) if isinstance(state, dict) else ""
            task_ids.update(_report_task_ids(result_path))
    return task_ids


def _sanitize_review_text(text: str, validation_task_ids: set[str]) -> str:
    """Remove known validation case identifiers from inherited free-form material."""
    clean_lines = []
    for line in str(text or "").splitlines():
        lowered = line.lower()
        if any(task_id and task_id.lower() in lowered for task_id in validation_task_ids):
            continue
        if "validation" in lowered and re.search(r"\bexpert_[A-Za-z0-9_]+\b", line, re.I):
            continue
        clean_lines.append(line)
    return "\n".join(clean_lines)


IMPLEMENTATION_LEAKAGE_RE = re.compile(
    r"(?:ADD_RESULT_VERIFIER|SHORTEN_CRITICAL_PATH|ADD_EXECUTION_CHECKPOINT|CHANGE_OUTPUT_CONTRACT|"
    r"SKILL\.md|(?:skills|clawevolve|clawbench)/[^\s`]+|target[_ ]?file|exact[_ ]?(?:patch|change)|"
    r"proposed[_ ]?change|Block\s*[A-Z0-9](?:\s*/\s*[A-Z0-9])*|anchor|锚点|"
    r"(?:增加|删除|重排|修改|rewrite|modify|add|remove|reorder).{0,30}(?:步骤|step|file|文件|Block|anchor|锚点)|"
    r"task_\d+.{0,50}(?:workaround|规则|answer|答案))", re.I,
)


def _sanitize_legacy_mechanism_text(value, failure_signature: str) -> tuple[str, bool]:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or IMPLEMENTATION_LEAKAGE_RE.search(text):
        signature = failure_signature or "unclassified_failure"
        return f"需要区分 {signature} 是否主要由信息定位、指令密度、关键路径长度或输出约束导致", bool(text)
    return text, False


def _review_output_leak_report(paths: dict, round_id: int) -> dict:
    findings = []
    optimization_task_ids, validation_task_ids = _round_task_id_sets(paths)
    task_regex = re.compile(r"\btask_\d+[A-Za-z0-9_-]*\b", re.I)
    patterns = [
        (re.compile(r"validation[^\n]{0,80}(?:原文|标准答案|golden|expected answer)", re.I), "validation answer detail"),
        (re.compile(r"validation[^\n]{0,80}\bexpert_[A-Za-z0-9_]+\b", re.I), "validation rubric/expert field"),
    ]
    for path in [paths["spec_dir"] / f"spec-v{round_id}.json", paths["spec_dir"] / f"spec-v{round_id}.md", paths["spec_dir"] / "spec_update_report.md"]:
        text = _read_text(path, limit=300000)
        task_matches = sorted(set(m.group(0) for m in task_regex.finditer(text)))
        forbidden_tasks = [task for task in task_matches if task in validation_task_ids or task not in optimization_task_ids]
        if forbidden_tasks:
            findings.append({"path": str(path), "kind": "validation or unknown benchmark task id", "matches": forbidden_tasks[:20]})
        for regex, kind in patterns:
            matches = sorted(set(m.group(0)[:160] for m in regex.finditer(text)))
            if matches:
                findings.append({"path": str(path), "kind": kind, "matches": matches[:20]})
    report = {
        "schema_version": "evolution.review_leak_check.v1",
        "valid": not findings,
        "optimization_task_count": len(optimization_task_ids),
        "validation_task_count": len(validation_task_ids),
        "findings": findings,
        "created_at": _now(),
    }
    out = paths["spec_dir"] / "validation_leak_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _review_decision_path(paths: dict) -> Path:
    return paths["spec_dir"] / "review_decision.json"


REVIEW_EXPERIMENT_OPERATORS = {
    "SHORTEN_CRITICAL_PATH", "ADD_EXECUTION_CHECKPOINT",
    "SEPARATE_ANALYSIS_FROM_RENDERING", "ADD_RESULT_VERIFIER",
    "CHANGE_ROUTING_BOUNDARY", "REDUCE_INSTRUCTION_ENTROPY",
    "ADD_FALLBACK_BUDGET", "CONTEXT_RETRIEVAL_SPLIT",
    "CLARIFY_PARAMETER_CONTRACT", "CHANGE_OUTPUT_CONTRACT",
    "DIAGNOSE_WORKSPACE_INTEGRITY", "NO_PATCH_DIAGNOSIS",
}
REVIEW_OPERATOR_ALIASES = {
    "ENSURE_WORKSPACE_SANITY": "DIAGNOSE_WORKSPACE_INTEGRITY",
    "HARDEN_CANDIDATE_PRODUCTION": "DIAGNOSE_WORKSPACE_INTEGRITY",
    "CLEAN_WORKSPACE": "DIAGNOSE_WORKSPACE_INTEGRITY",
    "WORKSPACE_SANITY_CHECK": "DIAGNOSE_WORKSPACE_INTEGRITY",
}


def _redact_review_value(value, optimization_task_ids: set[str], forbidden_task_ids: set[str], warnings: list[str]):
    if isinstance(value, dict):
        return {key: _redact_review_value(item, optimization_task_ids, forbidden_task_ids, warnings) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_review_value(item, optimization_task_ids, forbidden_task_ids, warnings) for item in value]
    if not isinstance(value, str):
        return value
    text = value
    for task_id in sorted(set(re.findall(r"\btask_\d+[A-Za-z0-9_-]*\b", text, re.I)), key=len, reverse=True):
        if task_id in forbidden_task_ids or task_id not in optimization_task_ids:
            # Strip the `task_<digits>_` prefix but KEEP the descriptive suffix so
            # a leaked task name keeps a usable, distinct signature instead of
            # collapsing every redacted task into one [redacted_case]. Pure
            # `task_=<digits>` with no suffix (no mechanism name to keep) is fully
            # redacted.
            stripped = re.sub(r"^task_\d+_(?=\D)", "", task_id, flags=re.I)
            replacement = stripped if stripped.lower() != task_id.lower() and stripped else "[redacted_case]"
            text = text.replace(task_id, replacement)
            warnings.append(f"redacted validation or unknown task id: {task_id} -> {replacement}")
    # Strip the rubric-derived `expert_` prefix but KEEP the mechanism name so
    # failure_signature stays a stable, distinct dedup key across rounds.
    if re.search(r"\bexpert_[A-Za-z0-9_]+\b", text, re.I):
        text = re.sub(r"\bexpert_([A-Za-z0-9_]+)\b", r"\1", text, flags=re.I)
        warnings.append("stripped rubric/expert prefix (mechanism name kept)")
    return text


def _normalize_review_decision(paths: dict, round_id: int, decision: dict) -> tuple[dict, list[str]]:
    """Normalize Review v1/v2 into implementation-free review_decision.v2."""
    if not isinstance(decision, dict):
        return {}, ["review_decision is not a JSON object"]
    warnings = []
    schema = str(decision.get("schema_version") or "")
    optimization_task_ids, current_validation_ids = _round_task_id_sets(paths)
    forbidden_task_ids = set(current_validation_ids) | _known_validation_task_ids(paths)

    if schema == "evolution.review_decision.v2":
        normalized = json.loads(json.dumps(decision, ensure_ascii=False))
        hypotheses = normalized.get("hypotheses") if isinstance(normalized.get("hypotheses"), list) else []
        if not hypotheses and normalized.get("failure_signature"):
            hypotheses = [{key: normalized.get(key) for key in (
                "failure_signature", "status", "claim", "alternative_causes",
                "disambiguation_signal", "confidence", "revisit_condition",
            )}]
    else:
        # v1 migration intentionally discards operator/scope/patch instructions.
        normalized = {
            "schema_version": "evolution.review_decision.v2",
            "round_id": round_id,
            "acceptance_decision": decision.get("acceptance_decision"),
            "summary": decision.get("summary"),
            "confidence": decision.get("confidence"),
            "hypotheses": [],
            "direction_decisions": decision.get("direction_decisions") if isinstance(decision.get("direction_decisions"), list) else [],
        }
        source_items = decision.get("next_experiments") if isinstance(decision.get("next_experiments"), list) else []
        if not source_items:
            source_items = decision.get("evidence") if isinstance(decision.get("evidence"), list) else []
        hypotheses = []
        for index, item in enumerate(source_items[:4]):
            if not isinstance(item, dict):
                continue
            failure_signature = str(item.get("failure_signature") or item.get("capability") or "unclassified_failure")
            legacy_claim, leaked = _sanitize_legacy_mechanism_text(item.get("hypothesis") or item.get("claim"), failure_signature)
            if leaked:
                warnings.append(f"next_experiments[{index}] implementation text normalized to mechanism question")
            hypotheses.append({
                "hypothesis_id": str(item.get("experiment_id") or f"HYP-{index + 1:03d}"),
                "failure_signature": failure_signature,
                "status": str(item.get("status") or "suspected"),
                "claim": legacy_claim,
                "alternative_causes": list(item.get("alternative_causes") or ["instruction_density", "runtime_variance"]),
                "disambiguation_signal": str(item.get("acceptance_signal") or "observe whether the failure signature changes"),
                "confidence": item.get("confidence", "low"),
                "revisit_condition": str(item.get("revisit_condition") or "new reproducible evidence becomes available"),
            })
        normalized["hypotheses"] = hypotheses
        warnings.append(f"normalized {schema or 'unknown/v1'} to evolution.review_decision.v2 and discarded implementation instructions")

    # Old Review agents may still echo retired gate/signal fields. Strip them
    # at the normalization boundary so they cannot reach the persisted decision
    # or the next-round Spec.
    for retired_key in (
        "protected_behaviors", "protected_signals", "expected_signals",
        "candidate_gate", "candidate_opt_gate", "full_opt_gate",
        "evaluation_contract", "diff_provenance", "business_metrics",
    ):
        if retired_key in normalized:
            normalized.pop(retired_key, None)
            warnings.append(f"discarded retired review field: {retired_key}")

    # Bench is authoritative; Review cannot replace its decision.
    try:
        acceptance = _require_acceptance_report(paths)
    except Exception:
        acceptance = {}
    bench_decision = str((acceptance or {}).get("bench_decision") or (acceptance or {}).get("decision") or "").strip()
    if bench_decision:
        if normalized.get("acceptance_decision") != bench_decision:
            warnings.append("acceptance_decision replaced with authoritative Bench decision")
        normalized["acceptance_decision"] = bench_decision

    normalized["schema_version"] = "evolution.review_decision.v2"
    normalized["round_id"] = round_id
    normalized["summary"] = str(normalized.get("summary") or "Evidence is insufficient; preserve the accepted baseline and run a minimal disambiguation experiment.").strip()
    if normalized.get("confidence") not in {"high", "medium", "low"}:
        normalized["confidence"] = "low"
        warnings.append("confidence normalized to low")

    clean_hypotheses = []
    for index, item in enumerate(hypotheses[:4]):
        if not isinstance(item, dict):
            continue
        status = item.get("status") if item.get("status") in {"suspected", "testing", "supported", "falsified"} else "suspected"
        clean_hypotheses.append({
            "hypothesis_id": str(item.get("hypothesis_id") or f"HYP-{index + 1:03d}"),
            "failure_signature": str(item.get("failure_signature") or "unclassified_failure"),
            "status": status,
            "claim": str(item.get("claim") or "additional mechanism-level evidence is required"),
            "alternative_causes": [str(x) for x in (item.get("alternative_causes") or []) if str(x).strip()][:6],
            "disambiguation_signal": str(item.get("disambiguation_signal") or "observe whether the failure signature changes"),
            "confidence": item.get("confidence", "low"),
            "revisit_condition": str(item.get("revisit_condition") or "new reproducible evidence becomes available"),
        })
    normalized["hypotheses"] = clean_hypotheses

    clean_directions = []
    for index, item in enumerate((normalized.get("direction_decisions") or [])[:12]):
        if not isinstance(item, dict):
            continue
        decision_name = str(item.get("decision") or "defer").lower()
        if decision_name in {"retire", "permanent_retire", "exhausted"}:
            decision_name = "freeze"
            warnings.append(f"direction_decisions[{index}] permanent decision normalized to freeze")
        if decision_name not in {"keep", "strengthen", "split", "freeze", "reject", "defer"}:
            decision_name = "defer"
        clean_directions.append({
            "direction_id": str(item.get("direction_id") or f"DIR-{index + 1:03d}"),
            "decision": decision_name,
            "confidence": item.get("confidence") if item.get("confidence") in {"high", "medium", "low"} else "low",
            "rationale": str(item.get("rationale") or "insufficient evidence"),
            "revisit_condition": str(item.get("revisit_condition") or "new reproducible evidence becomes available"),
        })
    normalized["direction_decisions"] = clean_directions
    normalized = _redact_review_value(normalized, optimization_task_ids, forbidden_task_ids, warnings)
    return normalized, list(dict.fromkeys(warnings))


def _review_implementation_leak_paths(value, prefix="") -> list[str]:
    forbidden_keys = {"operator", "target_file", "exact_change", "proposed_change", "patch", "implementation"}
    leaks = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if str(key).lower() in forbidden_keys:
                leaks.append(path)
            leaks.extend(_review_implementation_leak_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            leaks.extend(_review_implementation_leak_paths(item, f"{prefix}[{index}]"))
    return leaks


PROTECTED_BEHAVIOR_ALIASES = {
    "PB-score-breadth": {"description": "保持 optimization/held-out aggregate score breadth", "metric": "score", "direction": "maintain", "min_value": None, "max_drop": 0.10, "gate": "budget"},
    "complete_output_contract": {"description": "保持完整输出契约", "metric": "output_contract_complete", "direction": "maintain", "min_value": 1, "max_drop": 0},
    "output_contract_complete": {"description": "保持完整输出契约", "metric": "output_contract_complete", "direction": "maintain", "min_value": 1, "max_drop": 0},
    "risk_list_present": {"description": "保持风险清单输出", "metric": "risk_list_present", "direction": "maintain", "min_value": 1, "max_drop": 0},
    "confirm_gate_complete": {"description": "保持确认门完成", "metric": "confirm_gate_complete", "direction": "maintain", "min_value": 1, "max_drop": 0},
    "complete_stage_2_to_4": {"description": "保持 Stage 2-4 完成", "metric": "stage_completion", "direction": "maintain", "min_value": 4, "max_drop": 0},
}


def _resolve_protected_behavior(value, index: int = 0) -> dict:
    if isinstance(value, dict):
        item = dict(value)
        behavior_id = str(item.get("behavior_id") or item.get("id") or f"PB-{index + 1:03d}")
        alias = PROTECTED_BEHAVIOR_ALIASES.get(behavior_id) or {}
        metric = item.get("metric") or alias.get("metric")
        min_value = item.get("min_value") if item.get("min_value") is not None else alias.get("min_value")
        max_drop = item.get("max_drop") if item.get("max_drop") is not None else alias.get("max_drop", 0)
        return {"behavior_id": behavior_id, "description": str(item.get("description") or alias.get("description") or behavior_id), "metric": metric,
                "direction": str(item.get("direction") or alias.get("direction") or "maintain"), "min_value": min_value,
                "max_drop": max_drop, "gate": str(item.get("gate") or alias.get("gate") or "hard"),
                "metric_resolution_status": "resolved" if metric else "unresolved"}
    behavior_id = str(value or "").strip()
    alias = PROTECTED_BEHAVIOR_ALIASES.get(behavior_id)
    if alias:
        return {"behavior_id": behavior_id, **alias, "gate": str(alias.get("gate") or "hard"), "metric_resolution_status": "resolved"}
    return {"behavior_id": behavior_id or f"PB-{index + 1:03d}", "description": behavior_id or "unresolved protected behavior",
            "metric": None, "direction": "maintain", "min_value": None, "max_drop": None, "gate": "hard", "metric_resolution_status": "unresolved"}


def _validate_review_decision(paths: dict, round_id: int, decision: dict) -> dict:
    errors = []
    if not isinstance(decision, dict):
        return {"valid": False, "errors": ["review_decision must be a JSON object"]}
    if decision.get("schema_version") != "evolution.review_decision.v2":
        errors.append("schema_version must be evolution.review_decision.v2")
    if not str(decision.get("summary") or "").strip():
        errors.append("summary is required")
    leaks = _review_implementation_leak_paths(decision)
    if leaks:
        errors.append(f"review_decision contains implementation leakage: {leaks[:10]}")
    hypotheses = decision.get("hypotheses")
    if not isinstance(hypotheses, list):
        errors.append("hypotheses must be a list")
        hypotheses = []
    for index, item in enumerate(hypotheses):
        if not isinstance(item, dict):
            errors.append(f"hypotheses[{index}] must be an object")
            continue
        for field in ("failure_signature", "status", "claim", "alternative_causes", "disambiguation_signal"):
            if item.get(field) in (None, "", []):
                errors.append(f"hypotheses[{index}].{field} is required")
        if item.get("status") not in {"suspected", "testing", "supported", "falsified"}:
            errors.append(f"hypotheses[{index}].status is invalid")
    for index, item in enumerate(decision.get("direction_decisions") or []):
        if not isinstance(item, dict) or item.get("decision") not in {"keep", "strengthen", "split", "freeze", "reject", "defer"}:
            errors.append(f"direction_decisions[{index}].decision is invalid")
    serialized = json.dumps(decision, ensure_ascii=False)
    optimization_task_ids, current_validation_ids = _round_task_id_sets(paths)
    validation_task_ids = set(current_validation_ids) | _known_validation_task_ids(paths)
    mentioned = set(re.findall(r"\btask_\d+[A-Za-z0-9_-]*\b", serialized, re.I))
    forbidden = sorted(task for task in mentioned if task in validation_task_ids or task not in optimization_task_ids)
    if forbidden:
        errors.append(f"review_decision contains validation or unknown benchmark task ids: {forbidden[:10]}")
    if re.search(r"\bexpert_[A-Za-z0-9_]+\b", serialized, re.I):
        errors.append("review_decision cannot contain rubric/expert fields")
    return {"schema_version": "evolution.review_decision_validation.v2", "valid": not errors, "errors": errors, "round_id": round_id, "created_at": _now()}


def _compact_text(value, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _fit_utf8_budget(text: str, max_bytes: int, suffix: str = "\n[renderer truncated non-critical detail]\n") -> tuple[str, bool]:
    raw = str(text).encode("utf-8")
    if len(raw) <= max_bytes:
        return str(text), False
    suffix_bytes = suffix.encode("utf-8")
    clipped = raw[: max(0, max_bytes - len(suffix_bytes))]
    while clipped:
        try:
            return clipped.decode("utf-8") + suffix, True
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    return suffix_bytes[:max_bytes].decode("utf-8", errors="ignore"), True


def _clip_field(value, limit: int, field: str, truncated_fields: list[str]):
    text = str(value or "")
    if len(text) > limit:
        truncated_fields.append(field)
        return text[: max(0, limit - 1)] + "…"
    return text


def _canonicalize_spec_for_tune(spec: dict, max_bytes: int = 8192) -> tuple[dict, dict]:
    canonical = json.loads(json.dumps(spec, ensure_ascii=False))
    truncated_fields: list[str] = []
    # Enforce a top-level allowlist so unknown blobs cannot inflate the persisted spec-vN.json
    # while the budget report only inspects the tune payload subset.
    unknown_fields_removed = sorted(set(canonical.keys()) - set(SPEC_V1_TOP_LEVEL_ALLOWLIST))
    for key in unknown_fields_removed:
        canonical.pop(key, None)
    contract = canonical.get("objective_contract") if isinstance(canonical.get("objective_contract"), dict) else {}
    contract["objective_summary"] = [_clip_field(item, 1000, f"objective_contract.objective_summary[{i}]", truncated_fields) for i, item in enumerate((contract.get("objective_summary") or [])[:4])]
    contract["business_hard_constraints"] = [_clip_field(item, 300, f"objective_contract.business_hard_constraints[{i}]", truncated_fields) for i, item in enumerate((contract.get("business_hard_constraints") or [])[:12])]
    canonical["objective_contract"] = contract
    questions = []
    for index, raw in enumerate((canonical.get("experiment_questions") or [])[:4]):
        if not isinstance(raw, dict): continue
        item = dict(raw)
        item["claim"] = _clip_field(item.get("claim"), 500, f"experiment_questions[{index}].claim", truncated_fields)
        item["disambiguation_signal"] = _clip_field(item.get("disambiguation_signal"), 500, f"experiment_questions[{index}].disambiguation_signal", truncated_fields)
        item["revisit_condition"] = _clip_field(item.get("revisit_condition"), 300, f"experiment_questions[{index}].revisit_condition", truncated_fields)
        item["expected_evidence"] = _clip_field(item.get("expected_evidence"), 300, f"experiment_questions[{index}].expected_evidence", truncated_fields)
        item["alternative_causes"] = [_clip_field(cause, 120, f"experiment_questions[{index}].alternative_causes[{i}]", truncated_fields) for i, cause in enumerate((item.get("alternative_causes") or [])[:6])]
        questions.append(item)
    canonical["experiment_questions"] = questions
    protected = []
    for index, raw in enumerate((canonical.get("protected_behaviors") or [])[:8]):
        if not isinstance(raw, dict): continue
        item = dict(raw)
        item["description"] = _clip_field(item.get("description"), 200, f"protected_behaviors[{index}].description", truncated_fields)
        protected.append(item)
    canonical["protected_behaviors"] = protected
    baseline = canonical.get("accepted_baseline_snapshot") if isinstance(canonical.get("accepted_baseline_snapshot"), dict) else {}
    for key in ("baseline_id", "artifact_sha256", "optimization_report", "evaluation_identity_status"):
        baseline[key] = _clip_field(baseline.get(key), 500 if key == "optimization_report" else 160, f"accepted_baseline_snapshot.{key}", truncated_fields)
    canonical["accepted_baseline_snapshot"] = baseline
    scope = canonical.get("scope_contract") if isinstance(canonical.get("scope_contract"), dict) else {}
    for key in ("allowed_change_areas", "disallowed_change_areas"):
        scope[key] = [_clip_field(item, 200, f"scope_contract.{key}[{i}]", truncated_fields) for i, item in enumerate((scope.get(key) or [])[:20])]
    canonical["scope_contract"] = scope
    # Clean-experiment contract: proposal search may remain diverse, but the
    # executed candidate changes exactly one independent variable at one anchor.
    search = canonical.get("search_contract") if isinstance(canonical.get("search_contract"), dict) else {}
    search = dict(search)
    search.update({
        "exactly_one_selected_mechanism": True,
        "one_failure_signature_per_candidate": True,
        "atomic_single_variable": True,
        "max_changed_files": 1,
        "max_executed_edits": 1,
        "max_diff_hunks": 1,
    })
    canonical["search_contract"] = search

    def budget_metrics() -> tuple[str, int, str, int]:
        payload_text = json.dumps(_tune_spec_prompt_payload(canonical), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        canonical_json = json.dumps(canonical, ensure_ascii=False, sort_keys=True, indent=2)
        return payload_text, len(payload_text.encode("utf-8")), canonical_json, len(canonical_json.encode("utf-8"))
    payload_text, payload_bytes, canonical_json, canonical_json_bytes = budget_metrics()
    # Deterministic secondary reductions, preserving contracts and the first question.
    # Bound BOTH the tune payload and the persisted canonical JSON so the budget report
    # cannot claim under-budget while spec-vN.json is large.
    while max(payload_bytes, canonical_json_bytes) > max_bytes and any(len(item.get("alternative_causes") or []) > 1 for item in canonical["experiment_questions"]):
        for index in range(len(canonical["experiment_questions"]) - 1, -1, -1):
            causes = canonical["experiment_questions"][index].get("alternative_causes") or []
            if len(causes) > 1:
                causes.pop(); truncated_fields.append(f"experiment_questions[{index}].alternative_causes")
                break
        payload_text, payload_bytes, canonical_json, canonical_json_bytes = budget_metrics()
    while max(payload_bytes, canonical_json_bytes) > max_bytes and len(canonical["experiment_questions"]) > 1:
        canonical["experiment_questions"].pop(); truncated_fields.append("experiment_questions")
        payload_text, payload_bytes, canonical_json, canonical_json_bytes = budget_metrics()
    while max(payload_bytes, canonical_json_bytes) > max_bytes and len(canonical["protected_behaviors"]) > 1:
        canonical["protected_behaviors"].pop(); truncated_fields.append("protected_behaviors")
        payload_text, payload_bytes, canonical_json, canonical_json_bytes = budget_metrics()
    if max(payload_bytes, canonical_json_bytes) > max_bytes:
        raise ValueError(f"canonical Tune spec exceeds {max_bytes} bytes after deterministic compression: payload={payload_bytes} canonical_json={canonical_json_bytes}")
    warning_over_4KB = (payload_bytes > 4096) or (canonical_json_bytes > 4096)
    return canonical, {
        "canonical_json_chars": len(canonical_json), "canonical_json_bytes": canonical_json_bytes,
        "canonical_json_over_8KB": canonical_json_bytes > max_bytes,
        "tune_payload_chars": len(payload_text), "tune_payload_bytes": payload_bytes,
        "tune_payload_over_8KB": payload_bytes > max_bytes,
        "warning_over_4KB": warning_over_4KB, "truncated_fields": list(dict.fromkeys(truncated_fields)),
        "unknown_fields_removed": unknown_fields_removed,
        "rejected_over_8KB": False,
    }


def _render_spec_v1_markdown(spec: dict) -> str:
    version = str(spec.get("spec_version") or "v0")
    parent = spec.get("parent_spec_version")
    lines = [
        "---", "schema_version: evolution.spec.v1", f"spec_version: {version}",
        f"parent_spec_version: {'null' if parent is None else parent}",
        "created_by: clawevolve-review-renderer", "objective_ref: ../../../optimize/input/objective.md", "---", "",
        f"# Evolution Strategy Spec {version}", "", "## Objective Contract", "",
    ]
    contract = spec.get("objective_contract") if isinstance(spec.get("objective_contract"), dict) else {}
    for item in contract.get("objective_summary") or ["Objective is inherited unchanged from ../../../optimize/input/objective.md."]:
        lines.append(f"- {item}")
    lines.extend(["", "## Accepted Baseline Snapshot", ""])
    for key, value in (spec.get("accepted_baseline_snapshot") or {}).items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Current Experiment Questions", ""])
    for item in spec.get("experiment_questions") or []:
        lines.extend([
            f"### {item.get('question_id')}: {item.get('failure_signature')}",
            f"- Status: `{item.get('status')}`", f"- Claim: {item.get('claim')}",
            f"- Alternative causes: {_compact_text(', '.join(item.get('alternative_causes') or []), 300)}",
            f"- Disambiguation signal: {item.get('disambiguation_signal')}",
            f"- Expected evidence: {_compact_text(item.get('expected_evidence'), 240)}",
            f"- Revisit condition: {_compact_text(item.get('revisit_condition'), 240)}", "",
        ])
    if spec.get("protected_behaviors"):
        lines.extend(["## Protected Behaviors", ""])
        for item in spec.get("protected_behaviors") or []:
            lines.append(f"- `{item.get('behavior_id')}`: {item.get('description')} (metric={item.get('metric')}, min_value={item.get('min_value')}, max_drop={item.get('max_drop')})")
    for heading, key in (("Search Contract", "search_contract"), ("Scope Contract", "scope_contract")):
        lines.extend(["", f"## {heading}", "", "```json", json.dumps(spec.get(key) or {}, ensure_ascii=False, sort_keys=True, indent=2), "```"])
    # Keep this section readable for legacy Specs, but do not create it for
    # newly generated Review Specs.
    if spec.get("evaluation_contract"):
        lines.extend(["", "## Evaluation Contract", "", "```json", json.dumps(spec.get("evaluation_contract") or {}, ensure_ascii=False, sort_keys=True, indent=2), "```"])
    lines.extend(["", "## References", "", f"- history_ref: `{spec.get('history_ref')}`", f"- failure_registry_ref: `{spec.get('failure_registry_ref')}`", f"- mutation_operator_library_ref: `{spec.get('mutation_operator_library_ref')}`", ""])
    return "\n".join(lines)


def _update_failure_registry(paths: dict, round_id: int, decision: dict) -> dict:
    runtime_path = Path(paths.get("optimize_output_dir") or paths["spec_dir"].parent.parent) / "failure_registry.json"
    template_path = Path(__file__).resolve().parents[2] / "references/failure_registry.template.json"
    registry = _load_json(runtime_path, None)
    if not isinstance(registry, dict):
        registry = _load_json(template_path, {"schema_version": "evolution.failure_registry.v1", "failures": []})
    if registry.get("schema_version") != "evolution.failure_registry.v1" or not isinstance(registry.get("failures"), list):
        raise ValueError("failure registry is invalid")
    library = _load_mutation_operator_library(paths)
    all_families = sorted({str(item.get("family")) for item in library.get("operators") or [] if item.get("family")})
    by_signature = {str(item.get("failure_signature")): dict(item) for item in registry["failures"] if isinstance(item, dict) and item.get("failure_signature")}
    for hypothesis in decision.get("hypotheses") or []:
        if not isinstance(hypothesis, dict):
            continue
        signature = str(hypothesis.get("failure_signature") or "").strip()
        if not signature:
            continue
        item = by_signature.get(signature, {"failure_signature": signature, "observation_count": 0, "reproduction_count": 0, "supporting_evidence_refs": []})
        item["status"] = hypothesis.get("status") or "suspected"
        item["alternative_causes"] = list(hypothesis.get("alternative_causes") or [])
        item["revisit_condition"] = hypothesis.get("revisit_condition") or "new reproducible evidence"
        item["allowed_operator_families"] = item.get("allowed_operator_families") or all_families
        item["observation_count"] = int(item.get("observation_count") or 0) + 1
        if item["status"] == "supported":
            item["reproduction_count"] = int(item.get("reproduction_count") or 0) + 1
        refs = list(item.get("supporting_evidence_refs") or [])
        ref = f"round-{round_id:03d}/spec/review_decision.normalized.json"
        if ref not in refs:
            refs.append(ref)
        item["supporting_evidence_refs"] = refs[-20:]
        item["updated_at"] = _now()
        by_signature[signature] = item
    registry["failures"] = sorted(by_signature.values(), key=lambda item: item.get("failure_signature", ""))
    serialized = json.dumps(registry, ensure_ascii=False)
    if re.search(r"\btask_\d+[A-Za-z0-9_-]*\b|\bexpert_[A-Za-z0-9_]+\b", serialized, re.I):
        raise ValueError("failure registry contains validation/task-specific detail")
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(json.dumps(registry, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"path": str(runtime_path), "failure_count": len(registry["failures"])}


def _render_review_outputs(paths: dict, round_id: int) -> dict:
    decision_path = _review_decision_path(paths)
    raw_decision = _load_json(decision_path, None)
    # fail-soft normalization (evolution plan §11.5): always normalize
    # BEFORE validating, so validation/unknown task ids and expert_/rubric
    # fields are redacted to [redacted_case]/[redacted_rubric] up front. The
    # previous raw-validation short-circuit returned on any drift without ever
    # running normalization, so the redaction logic was dead code and benign
    # mechanism names like "expert_critical_risk_burial_in_patch" were wrongly
    # treated as hard rubric leakage, forcing three fruitless review retries.
    decision, normalization_warnings = _normalize_review_decision(paths, round_id, raw_decision)
    validation = _validate_review_decision(paths, round_id, decision)
    validation["normalization_warnings"] = normalization_warnings
    normalized_path = paths["spec_dir"] / "review_decision.normalized.json"
    normalized_path.write_text(json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validation_path = paths["spec_dir"] / "review_decision_validation.json"
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not validation.get("valid"):
        return {"ok": False, "error": "invalid review_decision", "validation": validation}

    prior_json = paths.get("input_dir", paths["spec_dir"].parent / "input") / f"spec-v{round_id - 1}.json"
    prior = _load_json(prior_json, {})
    if not isinstance(prior, dict) or prior.get("schema_version") != "evolution.spec.v1":
        prior = _normalize_legacy_spec_markdown_to_v1(_read_text(paths.get("input_dir", paths["spec_dir"].parent / "input") / f"spec-v{round_id - 1}.md"), f"v{round_id - 1}")
    state = _load_json(paths.get("round_dir", paths["spec_dir"].parent) / "round_state.json", {})
    manifest = _load_json(paths.get("optimize_output_dir", paths["spec_dir"].parent.parent) / "optimize_manifest.json", {})
    baseline_opt = state.get("baseline_optimization") if isinstance(state, dict) and isinstance(state.get("baseline_optimization"), dict) else _baseline_opt_registry(manifest)
    identity = baseline_opt.get("identity") if isinstance(baseline_opt.get("identity"), dict) else {}
    hypotheses = decision.get("hypotheses") or []
    questions = [{
        "question_id": item.get("hypothesis_id") or f"HYP-{index + 1:03d}",
        "failure_signature": item.get("failure_signature"), "status": item.get("status"),
        "claim": item.get("claim"), "alternative_causes": item.get("alternative_causes") or [],
        "disambiguation_signal": item.get("disambiguation_signal"),
        "expected_evidence": "post-Tune targeted optimization behavior metrics",
        "revisit_condition": item.get("revisit_condition"),
    } for index, item in enumerate(hypotheses[:4])]
    spec = {
        "schema_version": "evolution.spec.v1", "spec_version": f"v{round_id}", "parent_spec_version": f"v{round_id - 1}",
        "created_by": "clawevolve-review-renderer", "objective_ref": "../../../optimize/input/objective.md",
        "objective_contract": prior.get("objective_contract") or {},
        "accepted_baseline_snapshot": {
            "baseline_id": "bootstrap" if baseline_opt.get("round_id") in (None, 0) else f"accepted-round-{baseline_opt.get('round_id')}",
            "round_id": baseline_opt.get("round_id"), "artifact_sha256": identity.get("artifact_sha256") or "",
            "optimization_report": baseline_opt.get("result_path") or "", "optimization_score": ((baseline_opt.get("summary") or {}).get("score")),
            "evaluation_identity_status": "cache_complete" if identity.get("cache_complete", identity.get("complete")) else "incomplete",
        },
        "experiment_questions": questions,
        "search_contract": prior.get("search_contract") or {},
        "scope_contract": prior.get("scope_contract") or {},
        "history_ref": "experiment_ledger.jsonl", "failure_registry_ref": "failure_registry.json",
        "mutation_operator_library_ref": "clawevolve-workflow/references/mutation_operator_library.json",
    }
    try:
        spec, budget = _canonicalize_spec_for_tune(spec)
        # New Review-generated specs are intentionally free of the removed
        # protected-behavior and gate evaluation contracts. Legacy input may
        # still carry them, but they are not propagated to the next round.
        spec.pop("protected_behaviors", None)
        spec.pop("evaluation_contract", None)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "rejected_over_8KB": True}
    serialized = json.dumps(spec, ensure_ascii=False)
    if re.search(r"\bexpert_[A-Za-z0-9_]+\b|\btask_\d+[A-Za-z0-9_-]*\b", serialized, re.I):
        return {"ok": False, "error": "spec v1 contains validation/task-specific detail"}
    spec_text, truncated = _fit_utf8_budget(_render_spec_v1_markdown(spec), 8192)
    spec_bytes = len(spec_text.encode("utf-8"))
    spec_json_path = paths["spec_dir"] / f"spec-v{round_id}.json"
    spec_path = paths["spec_dir"] / f"spec-v{round_id}.md"
    report_path = paths["spec_dir"] / "spec_update_report.md"
    spec_json_path.write_text(json.dumps(spec, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    spec_path.write_text(spec_text, encoding="utf-8")
    report_path.write_text("\n".join([
        "# Spec Update Report", "", f"- schema: `evolution.spec.v1`", f"- spec_version: `v{round_id}`",
        f"- acceptance_decision: `{decision.get('acceptance_decision')}`",
        f"- canonical_json_chars / bytes: `{budget['canonical_json_chars']}` / `{budget['canonical_json_bytes']}`",
        f"- tune_payload_chars / bytes: `{budget['tune_payload_chars']}` / `{budget['tune_payload_bytes']}`",
        f"- markdown_chars / bytes: `{len(spec_text)}` / `{spec_bytes}`",
        f"- warning_over_4KB: `{str(budget['warning_over_4KB'] or spec_bytes > 4096).lower()}`",
        f"- truncated_fields: `{json.dumps(budget['truncated_fields'], ensure_ascii=False)}`",
        f"- rejected_over_8KB: `false`", f"- markdown_truncated_over_8KB: `{str(truncated).lower()}`", "",
        _compact_text(decision.get("summary"), 1000), "",
    ]), encoding="utf-8")
    failure_registry = _update_failure_registry(paths, round_id, decision)
    return {"ok": True, "decision_path": str(decision_path), "validation_path": str(validation_path), "normalized_path": str(normalized_path),
            "normalization_warnings": normalization_warnings, "spec_json_path": str(spec_json_path), "spec_path": str(spec_path), "report_path": str(report_path),
            "spec_chars": len(spec_text), "spec_bytes": spec_bytes, "budget_warning": budget['warning_over_4KB'] or spec_bytes > 4096, "truncated": truncated, "budget": budget,
            "failure_registry_path": failure_registry.get("path")}


# ═══════════════════════════════════════════════════════════════════════════════
# Artifact URL 工具
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# 路径解析
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_paths(args):
    workspace = _resolve_workspace(args)
    skill_base = _resolve_skill_base(args)
    task_id = args.task_id or _env("TASK_ID") or _env("EVOLVE_RUN_ID")
    if not task_id:
        raise SystemExit("task_id is required")

    run_dir = workspace / "clawevolve_results" / task_id
    plan_output_dir = run_dir / "plan" / "output"
    optimize_dir = run_dir / "optimize"
    optimize_input_dir = optimize_dir / "input"
    optimize_output_dir = optimize_dir / "output"
    round_name = f"round-{args.round:03d}"
    round_dir = optimize_output_dir / round_name

    paths = {
        "workspace": str(workspace),
        "skill_base": str(skill_base),
        "task_id": task_id,
        "run_dir": run_dir,
        "plan_output_dir": plan_output_dir,
        "optimize_dir": optimize_dir,
        "optimize_input_dir": optimize_input_dir,
        "optimize_output_dir": optimize_output_dir,
        "round_dir": round_dir,
        "input_dir":  round_dir / "input",
        "tune_dir":   round_dir / "tune",
        "spec_dir":   round_dir / "spec",
        "accept_dir": round_dir / "acceptance",
        "artifacts_dir": round_dir / "artifacts",
        "rollback_dir": round_dir / "rollback",
        "upload_dir": round_dir / "upload",
    }
    return paths


# ═══════════════════════════════════════════════════════════════════════════════
# 状态、断点续跑
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_BENCH_MODEL = os.environ.get("CLAWEVOLVE_BENCH_MODEL", "")
# Tune/review inherit OpenClaw's configured model unless the caller or an
# explicit stage environment variable selects another model.
DEFAULT_OPTIMIZER_MODEL = os.environ.get("CLAWEVOLVE_OPTIMIZER_MODEL", "")
TUNE_AGENT_TIMEOUT = int(os.environ.get("CLAWEVOLVE_TUNE_TIMEOUT", "7200"))
REVIEW_AGENT_TIMEOUT = int(os.environ.get("CLAWEVOLVE_REVIEW_TIMEOUT", "3600"))
# OpenClaw's CLI defaults to a 600-second command timeout.  Tune agents routinely
# exceed that while still making progress, and the CLI does not stream those
# session/tool events to stdout.  Keep the explicit total tune/review limits
# above, but disable the separate stdout-idle killer by default.  Set a positive
# value to opt back into it when the deployed OpenClaw reliably streams output.
DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS = int(os.environ.get("CLAWEVOLVE_AGENT_IDLE_TIMEOUT", "0"))
TUNE_AGENT_NAME = "clawevolve-tune"
REVIEW_AGENT_NAME = "clawevolve-review"
TUNE_AGENT_BASE_URL = os.environ.get("CLAWEVOLVE_TUNE_BASE_URL", "")
TUNE_AGENT_API_KEY = os.environ.get("CLAWEVOLVE_TUNE_API_KEY", "")
REVIEW_AGENT_BASE_URL = os.environ.get("CLAWEVOLVE_REVIEW_BASE_URL", "")
REVIEW_AGENT_API_KEY = os.environ.get("CLAWEVOLVE_REVIEW_API_KEY", "")
TUNE_AGENT_MODEL = os.environ.get("CLAWEVOLVE_TUNE_MODEL", "")
REVIEW_AGENT_MODEL = os.environ.get("CLAWEVOLVE_REVIEW_MODEL", "")
DEFAULT_MAX_ARTIFACT_MB = float(os.environ.get("CLAWEVOLVE_MAX_ARTIFACT_MB", "100"))
DEFAULT_PACK_DRY_RUN = os.environ.get("CLAWEVOLVE_PACK_DRY_RUN", "1").lower() not in {"0", "false", "no"}
DEFAULT_RESTORE_PRECHECK = os.environ.get("CLAWEVOLVE_RESTORE_PRECHECK", "1").lower() not in {"0", "false", "no"}
DEFAULT_ACCEPTANCE_MARGIN = float(os.environ.get("CLAWEVOLVE_ACCEPTANCE_MARGIN", "0"))
DEFAULT_PAIRED_MIN_RUNS = int(os.environ.get("CLAWEVOLVE_PAIRED_MIN_RUNS", "1"))
DEFAULT_PAIRED_MIN_WIN_RATE = float(os.environ.get("CLAWEVOLVE_PAIRED_MIN_WIN_RATE", "0.5"))
DEFAULT_EXPLORATION_LOSS_BUDGET = float(os.environ.get("CLAWEVOLVE_EXPLORATION_LOSS_BUDGET", "0.10"))
DEFAULT_REPLICATION_BAND = float(os.environ.get("CLAWEVOLVE_REPLICATION_BAND", "0.03"))
DEFAULT_PROTECTED_MAX_DROP = float(os.environ.get("CLAWEVOLVE_PROTECTED_MAX_DROP", "0.10"))
DEFAULT_EXPECTED_MIN_DELTA = float(os.environ.get("CLAWEVOLVE_EXPECTED_MIN_DELTA", "0.01"))
DEFAULT_FULL_OPT_MAX_REGRESSED_COUNT = int(os.environ.get("CLAWEVOLVE_FULL_OPT_MAX_REGRESSED_COUNT", "2"))
DEFAULT_FULL_OPT_MAX_REGRESSED_RATIO = float(os.environ.get("CLAWEVOLVE_FULL_OPT_MAX_REGRESSED_RATIO", "0.34"))
DEFAULT_FULL_OPT_MAX_NEGATIVE_DELTA = float(os.environ.get("CLAWEVOLVE_FULL_OPT_MAX_NEGATIVE_DELTA", "0.25"))
DEFAULT_FULL_OPT_MAX_SINGLE_DROP = float(os.environ.get("CLAWEVOLVE_FULL_OPT_MAX_SINGLE_DROP", "0.20"))
DEFAULT_FULL_OPT_MIN_MEAN_DELTA = float(os.environ.get("CLAWEVOLVE_FULL_OPT_MIN_MEAN_DELTA", "-0.02"))
# Temporary rollout mode: keep the static candidate report for diagnosis but do
# not let it stop benchmark execution. Set CLAWEVOLVE_ENFORCE_CANDIDATE_GATE=1
# or pass --enforce-candidate-gate to restore blocking behavior.
DEFAULT_ENFORCE_CANDIDATE_GATE = os.environ.get("CLAWEVOLVE_ENFORCE_CANDIDATE_GATE", "0").lower() in {"1", "true", "yes"}


def _candidate_gate_enforced(args=None) -> bool:
    value = getattr(args, "enforce_candidate_gate", None) if args is not None else None
    return DEFAULT_ENFORCE_CANDIDATE_GATE if value is None else bool(value)


def _candidate_gate_allows_progress(args, gate: dict) -> bool:
    gate = gate if isinstance(gate, dict) else {}
    if gate.get("is_noop"):
        return False
    return bool(gate.get("valid")) or not _candidate_gate_enforced(args)


EVALUATION_IDENTITY_KEYS = (
    "artifact_sha256", "domain_id", "domain_owner_id",
    "optimization_fixture_sha256", "validation_fixture_sha256",
    "benchmark_version", "suite", "agent_model", "agent_model_parameters_sha256",
    "judge_model", "judge_config_sha256", "scorer_sha256", "runtime_version_or_commit",
    "adapter_version_or_sha256", "tool_mcp_config_sha256", "sampling_seed", "temperature",
    "max_tokens", "timeout_retry_policy_sha256",
)

def _bench_model(args) -> str:
    return args.model or DEFAULT_BENCH_MODEL

def _optimizer_model(args, kind: str) -> str:
    specific = getattr(args, f"{kind}_model", "") or (TUNE_AGENT_MODEL if kind == "tune" else REVIEW_AGENT_MODEL)
    return (
        specific
        or getattr(args, "optimizer_model", "")
        or getattr(args, "model", "")
        or DEFAULT_OPTIMIZER_MODEL
    )

STEP_OUTPUTS = {
    "prepare":      lambda paths, args: [paths["round_dir"] / "round_state.json", paths["input_dir"] / f"spec-v{args.round - 1}.md", paths["input_dir"] / f"spec-v{args.round - 1}.json"],
    "baseline-pack": lambda paths, args: _baseline_pack_outputs(paths, args),
    "ensure-tune":  lambda paths, args: [paths["tune_dir"] / "tune_report.md", paths["tune_dir"] / "changed_files.txt", paths["tune_dir"] / "diff.patch", paths["tune_dir"] / "change_manifest.json"],
    "candidate-static-gate": lambda paths, args: [paths["tune_dir"] / "candidate_static_gate.json"],
    "candidate-opt-gate": lambda paths, args: [paths["tune_dir"] / "candidate_opt_gate.json"],
    "accept":       lambda paths, args: [paths["accept_dir"] / "acceptance_report.json"],
    "pack":         lambda paths, args: [paths["artifacts_dir"] / f"artifact_v{args.round}.zip"],
    "ensure-review":  lambda paths, args: [paths["spec_dir"] / f"spec-v{args.round}.json", paths["spec_dir"] / f"spec-v{args.round}.md", paths["spec_dir"] / "spec_update_report.md"],
    "upload-clawweb": lambda paths, args: [paths["upload_dir"] / "clawweb_manifest.json"],
    "upload-oss":   lambda paths, args: [paths["upload_dir"] / "oss_manifest.json"],
}

def _mark_step(args, step: str, status: str, extra: dict | None = None):
    try:
        paths = resolve_paths(args)
        state_path = paths["round_dir"] / "round_state.json"
        state = _load_json(state_path, {})
        if not isinstance(state, dict):
            state = {}
        state.setdefault("steps", {})
        rec = {"status": status, "updated_at": _now()}
        if extra:
            rec.update(extra)
        state["steps"][step] = rec
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass

def _step_done(args, step: str) -> bool:
    if step == "prepare":
        paths = resolve_paths(args)
        state = _load_json(paths["round_dir"] / "round_state.json", {})
        prepared = ((state.get("steps") or {}).get("prepare") or {}) if isinstance(state, dict) else {}
        input_spec = paths["input_dir"] / f"spec-v{args.round - 1}.md"
        input_spec_json = paths["input_dir"] / f"spec-v{args.round - 1}.json"
        if prepared.get("status") != "SUCCESS" or not input_spec.is_file() or not input_spec_json.is_file():
            return False
        if args.round > 1:
            baseline = str(state.get("baseline_artifact") or "")
            return bool(baseline and Path(baseline).is_file())
        return True
    if step == "baseline-pack":
        paths = resolve_paths(args)
        artifact_path, manifest_path = _rollback_snapshot_files(paths, args)
        manifest = _load_json(manifest_path, {})
        state = _load_json(paths["round_dir"] / "round_state.json", {})
        snapshot = state.get("rollback_snapshot") if isinstance(state, dict) else {}
        common = bool(
            artifact_path.is_file()
            and manifest.get("sha256") == _sha256(artifact_path)
            and isinstance(snapshot, dict)
            and snapshot.get("status") == "ready"
            and snapshot.get("path") == str(artifact_path)
            and snapshot.get("sha256") == manifest.get("sha256")
        )
        if not (common and manifest.get("status") == "SUCCESS"):
            return False
        if args.round == 1:
            initial_artifact, initial_manifest_path = _initial_artifact_files(paths)
            initial_manifest = _load_json(initial_manifest_path, {})
            return bool(
                initial_artifact.is_file()
                and initial_manifest.get("sha256") == _sha256(initial_artifact)
                and initial_manifest.get("publishStatus") == "SUCCESS"
                and initial_manifest.get("artifactUploaded") is True
                and initial_manifest.get("manifestUploaded") is True
                and str(initial_artifact) != str(artifact_path)
            )
        return True
    if step == "upload-oss":
        manifest = _load_json(resolve_paths(args)["upload_dir"] / "oss_manifest.json", {})
        return manifest.get("status") in {"SUCCESS", "SKIPPED_PROMOTION_FAILED"}
    if step == "upload-clawweb":
        manifest = _load_json(resolve_paths(args)["upload_dir"] / "clawweb_manifest.json", {})
        return bool(manifest.get("status") == "SUCCESS" and manifest.get("step_id") == getattr(args, "step_id", None))
    if step == "restore":
        paths = resolve_paths(args)
        try:
            acc = _require_acceptance_report(paths)
        except SystemExit:
            return False
        if not acc.get("restore_required", not acc["accepted"]):
            return True
        return (paths["accept_dir"] / "restore_result.json").exists()
    if step == "load-baseline-opt":
        state = _load_json(resolve_paths(args)["round_dir"] / "round_state.json", {})
        if not isinstance(state, dict):
            return False
        return bool(
            _baseline_registry_usable(state.get("baseline_optimization") or {})
            and _baseline_registry_usable(state.get("baseline_validation") or {})
        )
    if step == "replicate-validation":
        paths = resolve_paths(args)
        acc = _load_json(paths["accept_dir"] / "acceptance_report.json", {})
        if acc.get("decision") not in {"needs_replication", "needs_repeated_eval"}:
            return bool(acc)
        paired = _load_json(paths["accept_dir"] / "paired_eval.json", {})
        return _paired_eval_has_replicates(paired, int(getattr(args, "paired_min_runs", DEFAULT_PAIRED_MIN_RUNS) or 1))
    bench_steps = {
        "bench-opt": "candidate_optimization_full", "bench-targeted-opt": "candidate_optimization_targeted",
        "bench-full-opt": "candidate_optimization_full", "bench-val": "validation",
    }
    if step in bench_steps:
        paths = resolve_paths(args)
        state = _load_json(paths["round_dir"] / "round_state.json", {})
        kind = bench_steps[step]
        bench_info = (state.get("bench") or {}).get(kind) or {}
        if str(bench_info.get("status") or "").lower() == "skipped":
            return True
        result_path = bench_info.get("resultPath")
        return bool(result_path and Path(result_path).exists())
    if step == "pack":
        paths = resolve_paths(args)
        report = _load_json(paths["artifacts_dir"] / "pack_report.json", {})
        if isinstance(report, dict) and report.get("status") == "skipped":
            return True
    fn = STEP_OUTPUTS.get(step)
    if not fn:
        return False
    try:
        return all(Path(x).exists() for x in fn(resolve_paths(args), args))
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# find_script
# ═══════════════════════════════════════════════════════════════════════════════

def find_script(skill_base: str, skill_name: str, script_rel: str) -> Path:
    base = Path(skill_base).expanduser().resolve()
    candidate = (base / skill_name / script_rel).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise FileNotFoundError(f"Unsafe script path outside Skill base: {candidate}") from exc
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(f"Script not found: {script_rel} under {skill_name}")


# ═══════════════════════════════════════════════════════════════════════════════
# Local ClawBench helpers
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_clawbench_home(args, paths=None) -> Path:
    candidates = []
    if getattr(args, "clawbench_home", None):
        candidates.append(Path(args.clawbench_home).expanduser())
    if paths is None:
        try:
            paths = resolve_paths(args)
        except Exception:
            paths = None
    skill_base = Path((paths or {}).get("skill_base") or _resolve_skill_base(args)).resolve()
    candidates.extend([
        skill_base / "clawbench-base",
        Path(__file__).resolve().parents[3] / "clawbench-base",
    ])
    seen = set()
    checked = []
    for c in candidates:
        try:
            c = c.resolve()
        except Exception:
            c = c.absolute()
        if str(c) in seen:
            continue
        seen.add(str(c))
        checked.append(c)
        if (c / "scripts" / "benchmark.py").is_file():
            return c
    raise SystemExit("clawbench-base scripts/benchmark.py not found. checked=" + ", ".join(str(x) for x in checked))


def _local_template_dir(args, kind: str, paths: dict) -> Path:
    if kind == "optimization":
        explicit = getattr(args, "local_opt_template_dir", None) or _env("LOCAL_OPT_TEMPLATE_DIR", "")
        default = paths["plan_output_dir"] / "templates" / "opt"
    else:
        explicit = getattr(args, "local_val_template_dir", None) or _env("LOCAL_VAL_TEMPLATE_DIR", "")
        default = paths["plan_output_dir"] / "templates" / "val"
    return Path(explicit).expanduser() if explicit else default

def _safe_slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(value or ""))

def _log_tail(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except Exception:
        return ""



def _sha256_text_or_file(path: Path, limit_bytes: int | None = None) -> str:
    """Best-effort sha256 for identity fields; returns empty string when unavailable."""
    try:
        h = hashlib.sha256()
        with Path(path).open("rb") as f:
            remaining = limit_bytes
            while True:
                size = 1 << 16
                if remaining is not None:
                    if remaining <= 0:
                        break
                    size = min(size, remaining)
                chunk = f.read(size)
                if not chunk:
                    break
                h.update(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _tree_identity(root: Path, patterns: tuple[str, ...] = ("*.md", "*.json", "*.jsonl", "*.yaml", "*.yml", "*.py"), max_files: int = 500) -> dict:
    """Small deterministic content identity for benchmark fixtures/scorers.

    It intentionally records only hashes/counts, not content, and caps file count so it is safe
    for large local benchmark trees.
    """
    try:
        root = Path(root)
        if not root.exists():
            return {"path": str(root), "exists": False}
        files = []
        for pat in patterns:
            files.extend(root.rglob(pat))
        files = sorted({p for p in files if p.is_file()})[:max_files]
        h = hashlib.sha256()
        for fp in files:
            rel = fp.relative_to(root).as_posix()
            h.update(rel.encode()); h.update(b"\0")
            h.update(_sha256_text_or_file(fp, limit_bytes=1 << 20).encode()); h.update(b"\0")
        return {"path": str(root), "exists": True, "file_count_hashed": len(files), "sha256": h.hexdigest()}
    except Exception as exc:
        return {"path": str(root), "error": f"{type(exc).__name__}: {exc}"}


def _parse_changed_files_file(path: Path) -> list[dict]:
    """Parse clawevolve-tune changed_files.txt into a stable structure.

    Accepted formats are intentionally broad: raw path, `M path`, `modified: path`,
    bullets, or comma-separated status/path. Comments and blank lines are ignored.
    """
    out = []
    seen = set()
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return out
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[-*]\s+", "", line).strip()
        change = "modified"
        m = re.match(r"^(?P<change>[A-ZADMRC?]{1,3}|added|modified|deleted|renamed|created|updated)[:\s,]+(?P<path>.+)$", line, re.I)
        if m:
            change = m.group("change").lower()
            line = m.group("path").strip()
        line = line.strip('"\' ')
        if not line or line.lower() in {"none", "no changes", "noop", "no-op"}:
            continue
        if line not in seen:
            seen.add(line)
            out.append({"path": line, "change": change})
    return out


def _diff_stats(path: Path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {
            "exists": False, "sha256": "", "files": [], "additions": 0,
            "deletions": 0, "hunk_count": 0, "per_file_hunks": {},
            "removed_instruction_lines": [], "nonempty": False,
        }
    files = []
    additions = deletions = hunk_count = 0
    current = ""
    per_file_hunks: dict[str, int] = {}
    removed_lines: list[dict[str, str]] = []
    load_bearing = re.compile(
        r"防截断|完整输出|不得只写文件|确认闸门|精简原则|validator|repair|fallback|routing|路由|回退|验证器",
        re.I,
    )
    for line in text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                current = parts[3][2:] if parts[3].startswith("b/") else parts[3]
                files.append(current)
                per_file_hunks.setdefault(current, 0)
        elif line.startswith("@@"):
            hunk_count += 1
            if current:
                per_file_hunks[current] = per_file_hunks.get(current, 0) + 1
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
            value = line[1:].strip()
            if value and load_bearing.search(value) and len(removed_lines) < 40:
                removed_lines.append({"path": current, "line": value[:500]})
    meaningful = bool(files or additions or deletions or text.strip())
    return {
        "exists": True, "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "files": sorted(set(files)), "additions": additions, "deletions": deletions,
        "hunk_count": hunk_count, "per_file_hunks": per_file_hunks,
        "removed_instruction_lines": removed_lines, "nonempty": meaningful,
    }


WORKSPACE_MANIFEST_EXCLUDED_PARTS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    ".venv", "venv", ".tox", "node_modules", "dist", "build",
    "clawevolve_results", "clawbench_results", "skills-pool", "logs", "tmp", "temp",
}
WORKSPACE_MANIFEST_MAX_FILES = 50000


def _workspace_manifest_path(paths: dict, label: str) -> Path:
    return paths["tune_dir"] / f"system-{label}-manifest.json"


def _capture_workspace_manifest(paths: dict, label: str) -> dict:
    workspace = Path(paths["workspace"])
    entries = {}
    truncated = False
    if workspace.is_dir():
        for candidate in sorted(workspace.rglob("*")):
            try:
                rel = candidate.relative_to(workspace).as_posix()
            except Exception:
                continue
            if any(part in WORKSPACE_MANIFEST_EXCLUDED_PARTS for part in Path(rel).parts):
                continue
            if len(entries) >= WORKSPACE_MANIFEST_MAX_FILES:
                truncated = True
                break
            try:
                if candidate.is_symlink():
                    entries[rel] = {"type": "symlink", "target": os.readlink(candidate)}
                elif candidate.is_file():
                    stat = candidate.stat()
                    entries[rel] = {
                        "type": "file",
                        "size": stat.st_size,
                        "mode": stat.st_mode & 0o777,
                        "sha256": _sha256(candidate),
                    }
            except Exception as exc:
                entries[rel] = {"type": "error", "error": f"{type(exc).__name__}: {exc}"}
    report = {
        "schema_version": "evolution.workspace_manifest.v1",
        "label": label,
        "workspace": str(workspace),
        "file_count": len(entries),
        "truncated": truncated,
        "entries": entries,
        "created_at": _now(),
    }
    out = _workspace_manifest_path(paths, label)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _system_workspace_diff(paths: dict, *, capture_after: bool = True) -> dict:
    before_path = _workspace_manifest_path(paths, "before")
    before = _load_json(before_path, None)
    after = _capture_workspace_manifest(paths, "after") if capture_after else _load_json(_workspace_manifest_path(paths, "after"), None)
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {
            "schema_version": "evolution.workspace_diff.v1",
            "available": False,
            "trusted": False,
            "reason": "system before/after workspace manifest is unavailable",
            "changed_files": [],
            "touched_paths": [],
        }
    if before.get("truncated") or after.get("truncated"):
        trusted = False
        reason = "workspace manifest exceeded file limit"
    else:
        trusted = True
        reason = "system-computed before/after workspace diff"
    before_entries = before.get("entries") or {}
    after_entries = after.get("entries") or {}
    changed = []
    for rel in sorted(set(before_entries) | set(after_entries)):
        old = before_entries.get(rel)
        new = after_entries.get(rel)
        if old == new:
            continue
        change = "added" if old is None else "deleted" if new is None else "modified"
        changed.append({"path": rel, "change": change, "before": old, "after": new})
    report = {
        "schema_version": "evolution.workspace_diff.v1",
        "available": True,
        "trusted": trusted,
        "reason": reason,
        "before_manifest": str(before_path),
        "after_manifest": str(_workspace_manifest_path(paths, "after")),
        "changed_files": changed,
        "changed_file_count": len(changed),
        "touched_paths": [item["path"] for item in changed],
        "is_noop": not changed,
        "created_at": _now(),
    }
    out = paths["tune_dir"] / "system-diff.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _agent_change_report(paths: dict) -> dict:
    tune_dir = paths["tune_dir"]
    changed = _parse_changed_files_file(tune_dir / "changed_files.txt")
    diff = _diff_stats(tune_dir / "diff.patch")
    touched = {item.get("path", "") for item in changed}
    touched.update(diff.get("files") or [])
    return {"changed_files": changed, "diff": diff, "touched_paths": sorted(path for path in touched if path)}


def _round_change_summary(paths: dict) -> dict:
    agent = _agent_change_report(paths)
    manifest = _load_change_manifest(paths)
    # A discovery symlink is required activation metadata for CREATE_SKILL.  It
    # may not appear in a text-only unified diff or changed_files.txt, but it is
    # explicitly declared in change_manifest.json and independently verified by
    # the trusted workspace manifest below.  Include it in the agent-side path
    # set so the consistency gate compares equivalent representations.
    declared_discovery_paths = {
        _created_skill_discovery_path(edit.get("created_skill"))
        for edit in (manifest.get("edits") or [])
        if isinstance(edit, dict) and str(edit.get("change_type") or "").upper() == "CREATE_SKILL"
    }
    declared_discovery_paths.discard("")
    if declared_discovery_paths:
        agent["touched_paths"] = sorted(set(agent.get("touched_paths") or []) | declared_discovery_paths)
    system = _system_workspace_diff(paths, capture_after=True)
    if system.get("available"):
        changed = [{"path": item["path"], "change": item["change"]} for item in system.get("changed_files") or []]
        touched = list(system.get("touched_paths") or [])
        agent_paths = set(agent.get("touched_paths") or [])
        system_paths = set(touched)
        consistency = {
            "matches": agent_paths == system_paths,
            "unreported_files": sorted(system_paths - agent_paths),
            "phantom_reported_files": sorted(agent_paths - system_paths),
        }
        no_op = bool(system.get("is_noop"))
        return {
            "schema_version": "evolution.change_summary.v1",
            "source": "system_workspace_diff",
            "trusted": bool(system.get("trusted")),
            "changed_files": changed,
            "changed_file_count": len(changed),
            "diff": agent.get("diff") or {},
            "system_diff": system,
            "agent_report_consistency": consistency,
            "touched_paths": touched,
            "is_noop": no_op,
            "reason": "system diff is empty" if no_op else "system-computed workspace changes detected",
        }
    changed = agent.get("changed_files") or []
    touched = agent.get("touched_paths") or []
    no_op = not changed and not (agent.get("diff") or {}).get("nonempty")
    return {
        "schema_version": "evolution.change_summary.v1",
        "source": "agent_report_fallback",
        "trusted": False,
        "changed_files": changed,
        "changed_file_count": len(changed),
        "diff": agent.get("diff") or {},
        "system_diff": system,
        "agent_report_consistency": {"matches": False, "reason": system.get("reason")},
        "touched_paths": touched,
        "is_noop": no_op,
        "reason": "system workspace diff unavailable; agent report is not trusted",
    }




def _is_runtime_reachable_path(path: str) -> tuple[bool, str]:
    """Heuristic gate: did tune touch something the OpenClaw runtime will load?

    This intentionally stays conservative and path-based. It does not claim the change is
    semantically useful; it only prevents obvious misses such as editing logs, historical
    evolve artifacts or temp files from being treated as an activated candidate.
    """
    p = str(path or "").strip().replace("\\", "/").lstrip("./")
    if not p:
        return False, "empty path"
    runtime_files = {"AGENTS.md", "SOUL.md", "TOOLS.md", "IDENTITY.md", "RULES.md", "OKR.md", "USER.md", "BOOTSTRAP.md"}
    if p in runtime_files:
        return True, "persona/runtime md loaded from workspace root"
    if p == "config/mcporter.json" or p.endswith("/config/mcporter.json"):
        return True, "MCP config loaded by runtime"
    if p.startswith("skills/") and not any(part in {"__pycache__", ".pytest_cache"} for part in p.split("/")):
        if p.endswith((".md", ".py", ".json", ".yaml", ".yml", ".toml", ".sh")) or "/" in p:
            return True, "skill material under workspace/skills"
    if p.startswith(("clawevolve_results/", "clawbench_results/", "logs/", "cache/", "tmp/", "temp/")):
        return False, "runtime artifact/log/cache path is not loaded as candidate capability"
    if "/clawevolve_results/" in p or "/clawbench_results/" in p:
        return False, "historical evolve/bench result path is not loaded as candidate capability"
    if p.endswith((".log", ".tmp", ".bak", ".zip")):
        return False, "log/temp/archive file is not runtime-loaded"
    return False, "path is outside known OpenClaw runtime load roots"


def _candidate_reachability_report(paths: dict, change_summary: dict) -> dict:
    touched = list(change_summary.get("touched_paths") or [])
    checked = []
    reachable = []
    unreachable = []
    for p in touched:
        ok, reason = _is_runtime_reachable_path(p)
        item = {"path": p, "reachable": ok, "reason": reason}
        checked.append(item)
        (reachable if ok else unreachable).append(item)
    report = {
        "schema_version": "evolution.reachability.v0",
        "is_noop": bool(change_summary.get("is_noop")),
        "checked": checked,
        "reachable_paths": reachable,
        "unreachable_paths": unreachable,
        "activated": (bool(reachable) and not unreachable) or bool(change_summary.get("is_noop")),
        "reason": (
            "noop has no candidate to activate"
            if change_summary.get("is_noop")
            else "all touched paths are runtime-reachable"
            if reachable and not unreachable
            else "candidate includes paths outside known runtime load roots"
            if unreachable
            else "no touched path is in known runtime load roots"
        ),
        "created_at": _now(),
    }
    try:
        tune_dir = paths["tune_dir"]
        tune_dir.mkdir(parents=True, exist_ok=True)
        (tune_dir / "reachability_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass

    return report


def _candidate_scope_report(args, paths: dict, change_summary: dict) -> dict:
    """Reject candidate edits that mutate evidence, evaluation, or this running engine.

    Path reachability alone is insufficient: a file may be loadable in a future
    process while being unable to affect the already-running optimization
    process. Business-bot evolution therefore always excludes ClawEvolve/ClawBench
    engine mutation; engine evolution requires a separate workflow and a restarted
    runner/evaluator process.
    """
    allow_engine = bool(getattr(args, "allow_engine_mutation", False))
    checked = []
    blocked = []
    for raw in change_summary.get("touched_paths") or []:
        p = str(raw or "").strip().replace("\\", "/").lstrip("./")
        reason = ""
        category = "allowed"
        if p.startswith(("clawevolve_results/", "clawbench_results/", "temp/", "tmp/", "logs/")) or "/clawevolve_results/" in p or "/clawbench_results/" in p:
            category, reason = "evidence_or_history", "candidate must not edit evolve/bench history, logs, or temporary evidence"
        elif re.search(r"(^|/)(benchmark|benchmarks|scoring|grading|graders|cases|fixtures)(/|$)", p, re.I):
            category, reason = "evaluation_surface", "candidate must not edit benchmark cases, fixtures, grading, or scoring"
        elif (
            p.startswith("clawevolve-")
            or p.startswith("clawbench-")
            or p.startswith("scripts/clawevolve_")
            or re.match(r"^skills/(?:skills-local/|evolve_skills/)?(?:clawevolve-|clawbench-)", p)
        ):
            category = "engine_self_mutation"
            reason = "running ClawEvolve/ClawBench engine changes are forbidden in a business-bot round; use a separate restarted engine-evolution workflow"
        item = {"path": p, "allowed": not reason, "category": category, "reason": reason or "within business-bot candidate scope"}
        checked.append(item)
        if reason:
            blocked.append(item)
    report = {
        "schema_version": "evolution.candidate_scope.v0",
        "allow_engine_mutation": allow_engine,
        "valid": not blocked,
        "checked": checked,
        "blocked": blocked,
        "created_at": _now(),
    }
    try:
        out = paths["tune_dir"] / "candidate_scope_report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass
    return report


def _diff_added_lines_by_file(diff_path: Path) -> dict[str, list[str]]:
    try:
        lines = diff_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return {}
    result: dict[str, list[str]] = {}
    current = ""
    for line in lines:
        if line.startswith("diff --git "):
            parts = line.split()
            current = parts[3][2:] if len(parts) >= 4 and parts[3].startswith("b/") else ""
            if current:
                result.setdefault(current, [])
            continue
        if line.startswith("+++ "):
            value = line[4:].strip()
            if value == "/dev/null":
                current = ""
            elif value.startswith("b/"):
                current = value[2:]
                result.setdefault(current, [])
            continue
        if current and line.startswith("+") and not line.startswith("+++"):
            result[current].append(line[1:])
    return result


def _candidate_diff_provenance_report(paths: dict, change_summary: dict) -> dict:
    added_lines = _diff_added_lines_by_file(paths["tune_dir"] / "diff.patch")
    errors = []
    checked = []
    for item in change_summary.get("changed_files") or []:
        rel = str((item or {}).get("path") or "").strip().replace("\\", "/").lstrip("./")
        change = str((item or {}).get("change") or "modified").lower()
        target = Path(paths["workspace"]) / rel
        if change == "deleted" or target.suffix.lower() not in {".md", ".py", ".json", ".yaml", ".yml", ".toml", ".sh"}:
            continue
        present = rel in added_lines
        checked.append({"path": rel, "present_in_diff": present})
        if not present:
            errors.append(f"system-changed text file is missing from diff.patch: {rel}")
    return {"schema_version": "evolution.diff_provenance.v1", "valid": not errors, "checked": checked, "errors": errors, "created_at": _now()}


def _candidate_hardcode_report(paths: dict, change_summary: dict) -> dict:
    findings = []
    workspace = Path(paths["workspace"])
    added_lines = _diff_added_lines_by_file(paths["tune_dir"] / "diff.patch")
    scanned = []
    for item in change_summary.get("changed_files") or []:
        rel = str((item or {}).get("path") or "").strip().replace("\\", "/").lstrip("./")
        change = str((item or {}).get("change") or "modified").lower()
        target = workspace / rel
        if change == "deleted" or target.suffix.lower() not in {".md", ".py", ".json", ".yaml", ".yml", ".toml", ".sh"}:
            continue
        lines = added_lines.get(rel)
        if lines is None:
            continue
        text = "\n".join(lines)
        scanned.append({"path": rel, "change": change, "added_line_count": len(lines)})
        for finding in _looks_like_benchmark_hardcode(text):
            findings.append({"path": rel, "finding": finding, "source": "added_lines"})
    return {
        "schema_version": "evolution.candidate_hardcode.v2",
        "valid": not findings,
        "scan_mode": "diff_added_lines_only",
        "scanned": scanned,
        "findings": findings,
        "created_at": _now(),
    }


def _protected_signal_binding_report(spec_contract: dict, protected_signals: list[dict], available: set[str] | None = None) -> dict:
    """Validate explicit Spec protected-behavior bindings before spending bench cost."""
    invalid = []
    bindings = {}
    for item in protected_signals or []:
        if not isinstance(item, dict):
            continue
        behavior_id = str(item.get("behavior_id") or "").strip()
        if behavior_id:
            bindings.setdefault(behavior_id, []).append(item)
    for index, raw_behavior in enumerate((spec_contract or {}).get("protected_behaviors") or []):
        behavior = _resolve_protected_behavior(raw_behavior, index)
        behavior_id = str(behavior.get("behavior_id") or "")
        candidates = bindings.get(behavior_id) or []
        if not candidates:
            prefix = "unresolved" if behavior.get("metric_resolution_status") == "unresolved" else "resolved"
            invalid.append(f"{prefix} protected behavior requires Tune binding by behavior_id in protected_signals: {behavior_id}")
            continue
        for binding in candidates:
            if behavior.get("metric_resolution_status") == "unresolved":
                if not str(binding.get("metric") or "").strip():
                    invalid.append(f"unresolved protected behavior `{behavior_id}` requires an observable manifest metric")
            else:
                spec_metric = str(behavior.get("metric") or "")
                if spec_metric and str(binding.get("metric") or "") != spec_metric:
                    invalid.append(f"protected behavior `{behavior_id}` binding metric `{binding.get('metric')}` must equal spec metric `{spec_metric}`")
            spec_min = _finite_number(behavior.get("min_value"))
            bind_min = _finite_number(binding.get("min_value"))
            if spec_min is not None and (bind_min is None or bind_min < spec_min):
                invalid.append(f"protected behavior `{behavior_id}` binding min_value must be >= spec min_value {spec_min}")
            spec_max_drop = _finite_number(behavior.get("max_drop"))
            bind_max_drop = _finite_number(binding.get("max_drop"))
            if spec_max_drop is not None and (bind_max_drop is None or bind_max_drop > spec_max_drop):
                invalid.append(f"protected behavior `{behavior_id}` binding max_drop must not be wider than spec max_drop {spec_max_drop}")
            binding_tid = str(binding.get("task_id") or "").strip()
            if binding_tid and available and binding_tid not in available:
                invalid.append(f"protected behavior `{behavior_id}` binding references unknown optimization task: {binding_tid}")
    return {
        "schema_version": "evolution.protected_signal_binding.v1",
        "valid": not invalid,
        "errors": list(dict.fromkeys(invalid)),
        "required_behavior_ids": [str(_resolve_protected_behavior(x, i).get("behavior_id") or "") for i, x in enumerate((spec_contract or {}).get("protected_behaviors") or [])],
        "bound_behavior_ids": sorted(bindings),
    }


def _candidate_prevalidation_gate(args, paths: dict, change_summary: dict) -> dict:
    reachability = _candidate_reachability_report(paths, change_summary)
    scope = _candidate_scope_report(args, paths, change_summary)
    change_manifest = _load_change_manifest(paths)
    round_state = _load_json(paths["round_dir"] / "round_state.json", {})
    input_spec_contract = round_state.get("input_spec_contract") if isinstance(round_state, dict) and isinstance(round_state.get("input_spec_contract"), dict) else {}
    baseline = round_state.get("baseline_optimization") if isinstance(round_state, dict) and isinstance(round_state.get("baseline_optimization"), dict) else {}
    change_manifest = _apply_runner_protected_binding_defaults(input_spec_contract, change_manifest, baseline.get("task_scores") or {})
    manifest_quality = _validate_change_manifest_quality(change_manifest, change_summary, paths=paths, search_contract=input_spec_contract.get("search_contract") or {})
    protected_binding = _protected_signal_binding_report(input_spec_contract, change_manifest.get("protected_signals") or [])
    skill_creation = _validate_created_skills(args, paths, change_manifest, change_summary.get("changed_files") or [])
    diff_provenance = _candidate_diff_provenance_report(paths, change_summary)
    hardcode = _candidate_hardcode_report(paths, change_summary)
    is_noop = bool(change_summary.get("is_noop"))
    trusted_diff = change_summary.get("trusted") is True
    report_consistency = change_summary.get("agent_report_consistency") if isinstance(change_summary.get("agent_report_consistency"), dict) else {}
    report_matches = report_consistency.get("matches") is True
    valid = bool(
        not is_noop
        and trusted_diff
        and report_matches
        and reachability.get("activated")
        and scope.get("valid")
        and manifest_quality.get("valid")
        and protected_binding.get("valid")
        and skill_creation.get("valid", True)
        and diff_provenance.get("valid")
        and hardcode.get("valid")
    )
    reasons = []
    if is_noop:
        reasons.append("no candidate changes")
    if not trusted_diff:
        reasons.append("system-computed workspace diff is unavailable or untrusted")
    if trusted_diff and not report_matches:
        reasons.append("agent changed_files/diff does not match the system workspace diff")
    if not reachability.get("activated"):
        reasons.append("candidate is not runtime-reachable")
    if not scope.get("valid"):
        reasons.append("candidate changes a forbidden or temporally unreachable surface")
    if not manifest_quality.get("valid"):
        reasons.append("change manifest failed root-cause/operator/experiment contract validation")
    if not protected_binding.get("valid"):
        reasons.append("protected behavior signal binding contract validation failed")
    if not skill_creation.get("valid", True):
        reasons.append("created skill failed contract validation")
    if not diff_provenance.get("valid"):
        reasons.append("candidate diff provenance validation failed")
    if not hardcode.get("valid"):
        reasons.append("candidate contains benchmark-specific hardcoding signals")
    report = {
        "schema_version": "evolution.candidate_gate.v0",
        "valid": valid,
        "is_noop": is_noop,
        "trusted_diff": trusted_diff,
        "agent_report_consistency": report_consistency,
        "reasons": reasons,
        "bench_eligible": valid,
        "safety_gate": {"passed": valid, "errors": reasons},
        "research_quality_gate": manifest_quality.get("research_quality") or {"passed": True, "warnings": []},
        "reachability": reachability,
        "scope": scope,
        "manifest_quality": manifest_quality,
        "protected_signal_binding": protected_binding,
        "skill_creation": skill_creation,
        "diff_provenance": diff_provenance,
        "hardcode": hardcode,
        "created_at": _now(),
    }
    out = paths["tune_dir"] / "candidate_gate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state_path = paths["round_dir"] / "round_state.json"
    state = _load_json(state_path, {})
    if isinstance(state, dict):
        state["change_summary"] = change_summary
        state["reachability"] = reachability
        state["change_manifest"] = change_manifest
        state["skill_creation"] = skill_creation
        state["candidate_gate"] = report
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


STRUCTURAL_CHANGE_TYPES = {
    "CREATE_SKILL",
    "SPLIT_SKILL",
    "REFACTOR_SKILL_ARCHITECTURE",
    "CHANGE_OUTPUT_CONTRACT",
    "CHANGE_ROUTING_POLICY",
}


def _normalize_manifest_signal_schema(data: dict) -> dict:
    """Fail-soft normalization for common Tune signal-selector drift.

    Targeted evaluation only supports one concrete optimization task per signal.
    A concrete ``task_id_or_group=task_*`` is an unambiguous field-name alias and
    can be normalized safely. Aggregate selectors such as ``overall`` or
    ``all_tasks_*`` are not executable by the task-level metric extractor; they
    are retained in an audit list but excluded from the executable contract.
    """
    warnings = list(data.get("_normalization_warnings") or [])
    ignored = list(data.get("_ignored_signals") or [])
    for group_name in ("expected_signals", "protected_signals"):
        raw_items = data.get(group_name) if isinstance(data.get(group_name), list) else []
        normalized = []
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                normalized.append(raw)
                continue
            item = dict(raw)
            if not str(item.get("task_id") or "").strip():
                alias = str(item.get("task_id_or_group") or "").strip()
                if re.fullmatch(r"task_[A-Za-z0-9_-]+", alias):
                    item["task_id"] = alias
                    warnings.append(f"{group_name}[{index}].task_id_or_group normalized to task_id: {alias}")
                elif alias:
                    ignored.append({"group": group_name, "index": index, "reason": "aggregate task selectors are not executable", "signal": item})
                    warnings.append(f"{group_name}[{index}] ignored unsupported aggregate selector: {alias}")
                    continue
            item.pop("task_id_or_group", None)
            normalized.append(item)
        data[group_name] = normalized
    proposals = {str(x.get("proposal_id") or ""): x for x in (data.get("proposals") or []) if isinstance(x, dict)}
    for index, edit in enumerate(data.get("edits") or []):
        if not isinstance(edit, dict) or (isinstance(edit.get("alternative_causes"), list) and edit.get("alternative_causes")):
            continue
        proposal = proposals.get(str(edit.get("proposal_id") or "")) or {}
        inherited = [str(x) for x in (proposal.get("alternative_causes") or []) if str(x).strip()]
        if inherited:
            edit["alternative_causes"] = inherited
            warnings.append(f"edit[{index}].alternative_causes inherited from proposal {edit.get('proposal_id')}")
    data["_normalization_warnings"] = list(dict.fromkeys(warnings))
    data["_ignored_signals"] = ignored
    return data


def _apply_runner_protected_binding_defaults(spec_contract: dict, manifest: dict, baseline_scores: dict) -> dict:
    """Supplement generic score protection with a runner-selected canary.

    Explicit bindings that weaken the Spec still fail later. A behavior_id placed
    on an incompatible metric is treated as an annotation error: the extra signal
    remains protected, but it cannot satisfy that Spec behavior. For a missing
    resolved ``score`` behavior the runner chooses the strongest available
    baseline task, matching the canonical requirement that the runner add an
    independent protected canary.
    """
    signals = [dict(x) for x in (manifest.get("protected_signals") or []) if isinstance(x, dict)]
    warnings = list(manifest.get("_normalization_warnings") or [])
    added = []
    for index, raw_behavior in enumerate((spec_contract or {}).get("protected_behaviors") or []):
        behavior = _resolve_protected_behavior(raw_behavior, index)
        behavior_id = str(behavior.get("behavior_id") or "")
        spec_metric = str(behavior.get("metric") or "")
        compatible = []
        for signal in signals:
            if str(signal.get("behavior_id") or "") != behavior_id:
                continue
            if spec_metric and str(signal.get("metric") or "") != spec_metric:
                signal.pop("behavior_id", None)
                warnings.append(f"detached behavior_id {behavior_id} from incompatible protected metric {signal.get('metric')}")
                continue
            compatible.append(signal)
        if compatible:
            for signal in compatible:
                signal["gate"] = str(behavior.get("gate") or "hard")
            continue
        # Prefer an already-declared concrete signal with the exact metric.
        reusable = next((x for x in signals if not x.get("behavior_id") and str(x.get("task_id") or "") and str(x.get("metric") or "") == spec_metric), None)
        if reusable is not None:
            reusable["behavior_id"] = behavior_id
            reusable["gate"] = str(behavior.get("gate") or "hard")
            reusable["source"] = reusable.get("source") or "runner_bound_declared_signal"
            added.append({"behavior_id": behavior_id, "task_id": reusable.get("task_id"), "source": "declared_signal"})
            continue
        if behavior.get("metric_resolution_status") != "unresolved" and spec_metric == "score" and baseline_scores:
            ranked = [(str(tid), score) for tid, score in baseline_scores.items() if _finite_number(score) is not None]
            if ranked:
                task_id, baseline = max(ranked, key=lambda pair: float(pair[1]))
                signal = {
                    "behavior_id": behavior_id,
                    "task_id": task_id,
                    "metric": "score",
                    "baseline": float(baseline),
                    "direction": "maintain",
                    "max_drop": _finite_number(behavior.get("max_drop")) if _finite_number(behavior.get("max_drop")) is not None else DEFAULT_PROTECTED_MAX_DROP,
                    "source": "runner_spec_canary", "gate": str(behavior.get("gate") or "hard"),
                }
                min_value = _finite_number(behavior.get("min_value"))
                if min_value is not None:
                    signal["min_value"] = min_value
                signals.append(signal)
                added.append({"behavior_id": behavior_id, "task_id": task_id, "source": "runner_spec_canary"})
    manifest["protected_signals"] = signals
    manifest["_normalization_warnings"] = list(dict.fromkeys(warnings))
    manifest["_runner_added_protected_bindings"] = added
    return manifest


def _load_change_manifest(paths: dict) -> dict:
    """Load the structured Tune experiment contract with v1 compatibility."""
    path = paths["tune_dir"] / "change_manifest.json"
    data = _load_json(path, None)
    if isinstance(data, dict):
        data.setdefault("_source_path", str(path))
        edits = data.get("edits") if isinstance(data.get("edits"), list) else data.get("changes")
        if not isinstance(edits, list):
            edits = []
        data["edits"] = edits
        data["changes"] = edits
        # Tune historically emitted CREATE_SKILL metadata at the manifest root,
        # while the validator consumes it from the executed edit.  The prompt
        # described the fields but did not unambiguously require the nesting,
        # so normalize the single-create case instead of rejecting an otherwise
        # complete candidate for a serialization-shape mismatch.
        root_created_skill = data.get("created_skill") if isinstance(data.get("created_skill"), dict) else None
        create_edits = [
            edit for edit in edits
            if isinstance(edit, dict) and str(edit.get("change_type") or "").upper() == "CREATE_SKILL"
        ]
        if root_created_skill and len(create_edits) == 1 and not isinstance(create_edits[0].get("created_skill"), dict):
            create_edits[0]["created_skill"] = dict(root_created_skill)
            warnings = list(data.get("_normalization_warnings") or [])
            warnings.append("top-level created_skill normalized into the single CREATE_SKILL edit")
            data["_normalization_warnings"] = warnings
        if not isinstance(data.get("proposals"), list):
            data["proposals"] = []
        return _normalize_manifest_signal_schema(data)
    return {
        "schema_version": "evolution.change_manifest.missing",
        "proposals": [], "edits": [], "changes": [], "_source_path": "",
    }


def _normalize_hypothesis_assessment(value) -> tuple[dict, list[str]]:
    warnings = []
    if isinstance(value, dict):
        decision = str(value.get("decision") or "").lower()
        reason = str(value.get("reason") or "").strip()
        alternatives = [str(x) for x in (value.get("unresolved_alternatives") or []) if str(x).strip()]
    else:
        decision, reason, alternatives = "downgrade", str(value or "").strip(), []
        if reason:
            warnings.append("legacy string spec_hypothesis_assessment normalized to decision=downgrade")
    return {"decision": decision, "reason": reason, "unresolved_alternatives": alternatives}, warnings


def _created_skill_discovery_path(created_skill: dict | None) -> str:
    """Return the workspace path portion of a discovery-link declaration.

    Accept both ``skills/foo`` and the human-readable
    ``skills/foo -> skills-local/foo`` form used by Tune reports.
    """
    raw = str((created_skill or {}).get("discovery_link") or "").strip()
    if "->" in raw:
        raw = raw.split("->", 1)[0].strip()
    normalized, error = _normalize_changed_path(raw)
    return "" if error else normalized


def _normalize_changed_path(value: str) -> tuple[str, str]:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return "", "empty path"
    if raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
        return "", f"absolute path is forbidden: {raw}"
    while raw.startswith("./"):
        raw = raw[2:]
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return "", f"path escape is forbidden: {raw}"
    return "/".join(parts), ""


def _failure_signature_core(signature: str) -> str:
    """Extract the canonical mechanism identifier from a failure_signature.

    Tune proposals and edits reference the same mechanism with different text:
    proposals often attach a human-readable description after a colon (e.g.
    "expert_critical_risk_burial_in_patch_format: multi-tier quantity ..."),
    while edits use the bare identifier
    ("expert_critical_risk_burial_in_patch_format"). Comparing the leading token
    (before the first colon) lets the one-mechanism-per-candidate contract
    (search_contract.one_failure_signature_per_candidate) match the same mechanism
    across proposals and edits without flagging benign wording drift as an
    independent failure mechanism.
    """
    text = str(signature or "").strip()
    head = text.split(":", 1)[0].strip()
    return head.lower()


def _validate_change_manifest_quality(change_manifest: dict, change_summary: dict, *, paths: dict | None = None, search_contract: dict | None = None) -> dict:
    errors = []
    warnings = [str(x) for x in (change_manifest.get("_normalization_warnings") or []) if str(x).strip()]
    research_warnings = []
    is_noop = bool(change_summary.get("is_noop"))
    proposals = change_manifest.get("proposals") if isinstance(change_manifest.get("proposals"), list) else []
    edits = change_manifest.get("edits") if isinstance(change_manifest.get("edits"), list) else []
    selected_id = str(change_manifest.get("selected_proposal_id") or "").strip()
    spec_hypothesis_assessment, assessment_warnings = _normalize_hypothesis_assessment(change_manifest.get("spec_hypothesis_assessment"))
    warnings.extend(assessment_warnings)
    contract = search_contract if isinstance(search_contract, dict) else {}
    atomic_required = contract.get("atomic_single_variable") is not False
    configured_max_edits = int(contract.get("max_executed_edits") or 1)
    configured_max_files = int(contract.get("max_changed_files") or 1)
    max_edits = min(configured_max_edits, 1) if atomic_required else configured_max_edits
    max_files = min(configured_max_files, 1) if atomic_required else configured_max_files
    max_hunks = min(int(contract.get("max_diff_hunks") or 1), 1) if atomic_required else int(contract.get("max_diff_hunks") or 999999)
    required_diversity = int(contract.get("required_operator_diversity") or 3)
    system_diff_available = str(change_summary.get("source") or "") == "system_workspace_diff"
    system_diff_trusted = system_diff_available and change_summary.get("trusted") is True
    actual_files, path_errors = set(), []
    for raw_path in change_summary.get("touched_paths") or []:
        normalized, path_error = _normalize_changed_path(raw_path)
        if path_error: path_errors.append(path_error)
        elif normalized: actual_files.add(normalized)
    declared_files = set()
    declared_discovery_paths = set()
    for edit in edits:
        if not isinstance(edit, dict): continue
        for raw_path in [edit.get("target_file"), *(edit.get("affected_files") or [])]:
            normalized, path_error = _normalize_changed_path(raw_path)
            if path_error: path_errors.append(path_error)
            elif normalized: declared_files.add(normalized)
        if str(edit.get("change_type") or "").upper() == "CREATE_SKILL":
            discovery_path = _created_skill_discovery_path(edit.get("created_skill"))
            if discovery_path:
                declared_files.add(discovery_path)
                declared_discovery_paths.add(discovery_path)
    errors.extend(path_errors)

    # CREATE_SKILL is one behavioral edit but necessarily materializes as a
    # SKILL.md plus one discovery symlink.  Count the verified symlink as
    # activation metadata rather than a second experimental variable.
    system_changes = {
        str(item.get("path") or ""): item
        for item in (((change_summary.get("system_diff") or {}).get("changed_files")) or [])
        if isinstance(item, dict)
    }
    verified_discovery_links = {
        path for path in declared_discovery_paths
        if path in actual_files and ((system_changes.get(path) or {}).get("after") or {}).get("type") == "symlink"
    }
    logical_actual_files = actual_files - verified_discovery_links
    try:
        library = _load_mutation_operator_library(paths)
    except Exception as exc:
        library = {"operators": []}
        errors.append(f"mutation operator library unavailable: {exc}")
    operator_map = {str(item.get("name")): item for item in library.get("operators") or [] if isinstance(item, dict)}
    expected_signals = change_manifest.get("expected_signals") if isinstance(change_manifest.get("expected_signals"), list) else []
    protected_signals = change_manifest.get("protected_signals") if isinstance(change_manifest.get("protected_signals"), list) else []
    if not is_noop:
        if not change_manifest.get("_source_path"):
            errors.append("change_manifest.json is required for a non-noop candidate")
        if len(proposals) < 3:
            research_warnings.append("Tune should compare at least three operator-diverse proposals before applying one")
        if not selected_id:
            errors.append("selected_proposal_id is required")
        if spec_hypothesis_assessment.get("decision") not in {"accept", "reject", "downgrade"} or not spec_hypothesis_assessment.get("reason"):
            errors.append("spec_hypothesis_assessment is required; Tune must accept, reject, or downgrade the Review hypothesis")
        proposal_ids = {str(item.get("proposal_id") or "").strip() for item in proposals if isinstance(item, dict)}
        if selected_id and selected_id not in proposal_ids:
            errors.append("selected_proposal_id does not reference a declared proposal")
        selected_count = sum(1 for item in proposals if isinstance(item, dict) and str(item.get("decision") or "").lower() == "selected")
        if selected_count != 1:
            errors.append("exactly one proposal must have decision=selected")
        if not edits:
            errors.append("non-noop candidate must declare at least one executed edit")
        if not expected_signals:
            errors.append("non-noop candidate must declare expected_signals with behavior metrics")
        if not protected_signals:
            errors.append("non-noop candidate must declare protected_signals; runner will add an independent canary")
        if len(edits) > max_edits:
            errors.append(f"change budget exceeded: at most {max_edits} executed edits are allowed")
        if not system_diff_trusted:
            errors.append("system workspace diff is unavailable or untrusted; non-noop candidate fails closed")
        if len(logical_actual_files) > max_files:
            errors.append(f"changed file budget exceeded: {len(logical_actual_files)} > {max_files}")
        if atomic_required and len(edits) != 1:
            errors.append(f"atomic experiment requires exactly one executed edit; got {len(edits)}")
        if atomic_required and len(logical_actual_files) != 1:
            errors.append(f"atomic experiment requires exactly one changed file; got {len(logical_actual_files)}")
        diff_info = change_summary.get("diff") if isinstance(change_summary.get("diff"), dict) else {}
        hunk_count = diff_info.get("hunk_count")
        if atomic_required and diff_info.get("nonempty") and isinstance(hunk_count, int) and hunk_count != max_hunks:
            errors.append(f"atomic experiment requires exactly {max_hunks} diff hunk; got {hunk_count}")
        effect_design = change_manifest.get("effect_design") if isinstance(change_manifest.get("effect_design"), dict) else {}
        patch_operation = str(effect_design.get("patch_operation") or "").strip().lower()
        if not patch_operation:
            legacy_strategy = str(effect_design.get("change_strategy") or "").strip().lower()
            patch_operation = legacy_strategy if legacy_strategy in {"append", "replace", "delete", "reorder"} else ""
        additions = int(diff_info.get("additions") or 0)
        deletions = int(diff_info.get("deletions") or 0)
        if patch_operation == "append" and deletions:
            errors.append(f"atomic patch_operation=append conflicts with actual deletions={deletions}")
        elif patch_operation == "delete" and additions:
            errors.append(f"atomic patch_operation=delete conflicts with actual additions={additions}")
        elif diff_info.get("nonempty") and patch_operation in {"replace", "reorder"} and (not additions or not deletions):
            errors.append(f"atomic patch_operation={patch_operation} requires both additions and deletions")
        atomic_experiment = change_manifest.get("atomic_experiment") if isinstance(change_manifest.get("atomic_experiment"), dict) else {}
        if not atomic_experiment:
            warnings.append("atomic_experiment metadata is missing; actual file/edit/hunk limits are still enforced")
        else:
            independent_variable = str(atomic_experiment.get("independent_variable") or "").strip()
            target_anchor = str(atomic_experiment.get("target_anchor") or "").strip()
            if not independent_variable:
                errors.append("atomic_experiment.independent_variable is required")
            if not target_anchor:
                errors.append("atomic_experiment.target_anchor is required")
            declared_target, target_error = _normalize_changed_path(atomic_experiment.get("target_file"))
            if target_error:
                errors.append(target_error)
            elif declared_target and logical_actual_files and declared_target not in logical_actual_files:
                errors.append("atomic_experiment.target_file does not match the single system-changed file")
        unreported = sorted(actual_files - declared_files)
        phantom = sorted(declared_files - actual_files)
        report_consistency = change_summary.get("agent_report_consistency") if isinstance(change_summary.get("agent_report_consistency"), dict) else {}
        all_edits_selected = bool(edits) and bool(selected_id) and all(isinstance(edit, dict) and str(edit.get("proposal_id") or "").strip() == selected_id for edit in edits)
        auto_attributed = []
        if unreported and system_diff_trusted and report_consistency.get("matches") is True and all_edits_selected:
            auto_attributed = list(unreported)
            declared_files.update(auto_attributed)
            warnings.append(f"system-diff files auto-attributed to the single selected mechanism: {auto_attributed}")
            unreported = []
        if unreported:
            errors.append(f"unreported changed files: {unreported}")
        if phantom:
            errors.append(f"phantom declared files: {phantom}")
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, dict):
            errors.append(f"proposal[{index}] must be an object")
            continue
        for field in ("proposal_id", "failure_signature", "suspected_root_cause", "selected_operator", "proposed_change", "decision"):
            if not str(proposal.get(field) or "").strip():
                errors.append(f"proposal[{index}].{field} is required")
        alternatives = proposal.get("alternative_causes")
        if not isinstance(alternatives, list) or not alternatives:
            errors.append(f"proposal[{index}].alternative_causes must contain at least one alternative explanation")
        operator = str(proposal.get("selected_operator") or "").strip()
        if operator and operator not in operator_map:
            errors.append(f"proposal[{index}].selected_operator is unsupported: {operator}")
        if str(proposal.get("decision") or "").lower() == "selected":
            if not str(proposal.get("complexity") or "").strip():
                errors.append(f"proposal[{index}].complexity is required for the selected mechanism")
            if not str(proposal.get("impact_radius") or "").strip():
                errors.append(f"proposal[{index}].impact_radius is required for the selected mechanism")
    for group_name, signals in (("expected_signals", expected_signals), ("protected_signals", protected_signals)):
        for index, signal in enumerate(signals):
            if not isinstance(signal, dict):
                errors.append(f"{group_name}[{index}] must be an object")
                continue
            for field in ("task_id", "metric"):
                if signal.get(field) in (None, ""):
                    errors.append(f"{group_name}[{index}].{field} is required")
            direction = str(signal.get("direction") or ("maintain" if group_name == "protected_signals" else "increase")).lower()
            if group_name == "expected_signals" and direction != "boolean_flip" and signal.get("expected_min") is None and signal.get("min_delta") is None:
                errors.append(f"{group_name}[{index}] requires expected_min or min_delta for direction={direction}")
            allowed_directions = {"increase", "decrease", "boolean_flip"} if group_name == "expected_signals" else {"maintain"}
            if direction not in allowed_directions:
                errors.append(f"{group_name}[{index}].direction is invalid: {direction}")
            if group_name == "protected_signals" and signal.get("max_drop") is None:
                errors.append(f"{group_name}[{index}].max_drop is required")
            for numeric_field in ("baseline", "expected_min", "min_delta", "expected_value", "min_value", "max_drop"):
                if signal.get(numeric_field) is None or numeric_field not in signal:
                    continue
                if _finite_number(signal.get(numeric_field)) is None:
                    errors.append(f"{group_name}[{index}].{numeric_field} must be a finite number")
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            errors.append(f"edit[{index}] must be an object")
            continue
        for field in (
            "edit_id", "proposal_id", "target_file", "failure_signature",
            "suspected_root_cause", "selected_operator", "falsifiable_prediction",
            "rollback_condition", "local_check",
        ):
            if not str(edit.get(field) or "").strip():
                errors.append(f"edit[{index}].{field} is required")
        alternatives = edit.get("alternative_causes")
        if not isinstance(alternatives, list) or not alternatives:
            errors.append(f"edit[{index}].alternative_causes must contain at least one alternative explanation")
        if str(edit.get("proposal_id") or "").strip() != selected_id:
            errors.append(f"edit[{index}] must belong to selected_proposal_id")
    selected = next((item for item in proposals if isinstance(item, dict) and str(item.get("proposal_id") or "").strip() == selected_id), {})
    selected_operator = str(selected.get("selected_operator") or "").strip()
    selected_signature = str(selected.get("failure_signature") or "").strip()
    selected_signature_core = _failure_signature_core(selected_signature)
    for index, edit in enumerate(edits):
        if isinstance(edit, dict) and str(edit.get("selected_operator") or "").strip() != selected_operator:
            errors.append(f"edit[{index}].selected_operator must equal selected proposal operator")
        if isinstance(edit, dict) and _failure_signature_core(edit.get("failure_signature")) != selected_signature_core:
            errors.append(
                f"edit[{index}] mixes an independent failure mechanism "
                f"(edit signature {str(edit.get('failure_signature') or '')!r} "
                f"vs selected {selected_signature!r})"
            )
    proposal_operators = {str(item.get("selected_operator") or "").strip() for item in proposals if isinstance(item, dict)}
    proposal_families = {str((operator_map.get(name) or {}).get("family") or "") for name in proposal_operators if name in operator_map}
    proposal_families.discard("")
    required_family_diversity = int(contract.get("required_operator_family_diversity") or required_diversity)
    if not is_noop and len(proposal_operators) < required_diversity:
        research_warnings.append(f"operator diversity insufficient: {len(proposal_operators)} < {required_diversity}")
    if not is_noop and len(proposal_families) < required_family_diversity:
        research_warnings.append(f"operator family diversity insufficient: {len(proposal_families)} < {required_family_diversity}")
    return {
        "schema_version": "evolution.change_manifest_quality.v1",
        "valid": not errors,
        "proposal_count": len(proposals),
        "edit_count": len(edits),
        "selected_proposal_id": selected_id or None,
        "spec_hypothesis_assessment": spec_hypothesis_assessment,
        "operator_library_path": library.get("_source_path"),
        "operator_diversity": len(proposal_operators),
        "operator_family_diversity": len(proposal_families),
        "selected_operator": selected_operator or None,
        "selected_operator_family": (operator_map.get(selected_operator) or {}).get("family"),
        "budgets": {"max_executed_edits": max_edits, "max_changed_files": max_files, "max_diff_hunks": max_hunks, "atomic_single_variable": atomic_required},
        "atomic_experiment": {
            "required": atomic_required,
            "actual_edit_count": len(edits),
            "actual_file_count": len(logical_actual_files),
            "physical_path_count": len(actual_files),
            "activation_metadata_paths": sorted(verified_discovery_links),
            "actual_hunk_count": ((change_summary.get("diff") or {}).get("hunk_count")),
            "patch_operation": str(((change_manifest.get("effect_design") or {}).get("patch_operation") or "")),
            "removed_instruction_lines": ((change_summary.get("diff") or {}).get("removed_instruction_lines") or []),
        },
        "actual_changed_file_count": len(actual_files),
        "logical_changed_file_count": len(logical_actual_files),
        "declared_changed_file_count": len(declared_files),
        "actual_changed_files": sorted(actual_files),
        "declared_changed_files": sorted(declared_files),
        "unreported_changed_files": unreported if not is_noop else sorted(actual_files - declared_files),
        "auto_attributed_changed_files": auto_attributed if not is_noop else [],
        "phantom_declared_files": phantom if not is_noop else sorted(declared_files - actual_files),
        "system_diff_available": system_diff_available,
        "system_diff_trusted": system_diff_trusted,
        "expected_signal_count": len(expected_signals),
        "protected_signal_count": len(protected_signals),
        "errors": errors,
        "warnings": warnings,
        "research_quality": {"passed": not research_warnings, "warnings": research_warnings, "operator_diversity": len(proposal_operators), "operator_family_diversity": len(proposal_families)},
        "created_at": _now(),
    }


def _parse_skill_frontmatter(skill_md: Path) -> dict:
    """Parse minimal SKILL.md YAML frontmatter without adding a YAML dependency."""
    try:
        text = Path(skill_md).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "error": f"read failed: {type(exc).__name__}: {exc}", "name": "", "description": ""}
    if not text.startswith("---"):
        return {"ok": False, "error": "SKILL.md must start with frontmatter delimiter ---", "name": "", "description": ""}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {"ok": False, "error": "frontmatter closing delimiter missing", "name": "", "description": ""}
    fm = parts[1]
    values = {}
    current_key = None
    for raw in fm.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if re.match(r"^\s", line) and current_key:
            values[current_key] = (values.get(current_key, "") + " " + line.strip()).strip()
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            current_key = key.strip()
            values[current_key] = val.strip().strip('"\'')
    return {"ok": True, "name": values.get("name", ""), "description": values.get("description", ""), "body": parts[2]}


def _looks_like_benchmark_hardcode(text: str) -> list[str]:
    findings = []
    sample = str(text or "")[:200000]
    if re.search(r"task_\d+[_a-zA-Z0-9-]*", sample):
        findings.append("contains benchmark-like task id")
    if re.search(r"(?i)(benchmark|clawbench).{0,80}(answer|expected|golden)", sample):
        findings.append("mentions benchmark answers/golden expectations")
    if len(re.findall(r"(?m)^\s*(if|elif)\s+.*==\s*['\"]", sample)) >= 8:
        findings.append("contains many exact literal branches; possible case hardcoding")
    return findings



def _skill_write_root() -> str:
    return "skills" if os.environ.get("CLAWWEB_VERSION") == "openversion" else "skills/skills-local"


def _created_skill_entrypoints_from_changes(paths: dict, changed_files: list[dict] | None = None) -> list[str]:
    """Infer newly-created skill entrypoints without overruling the system diff.

    ``changed_files`` normally comes from the trusted before/after workspace manifest.
    Its status is authoritative: an agent-produced patch may incorrectly render an
    existing untracked file as ``new file mode``. Diff metadata is therefore only a
    fallback for paths absent from the system change set.
    """
    created = set()
    authoritative_changes = {}
    pat = re.compile(rf"^{re.escape(_skill_write_root())}/[^/]+/SKILL\.md$")
    create_statuses = {"a", "add", "added", "create", "created", "new"}
    for item in changed_files or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip().replace("\\", "/").lstrip("./")
        change = str(item.get("change") or "").strip().lower()
        if path:
            authoritative_changes[path] = change
        if pat.match(path) and change in create_statuses:
            created.add(path)

    try:
        diff_text = (paths["tune_dir"] / "diff.patch").read_text(encoding="utf-8", errors="replace")
    except Exception:
        diff_text = ""
    current = ""
    is_new = False
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if current and is_new and pat.match(current) and current not in authoritative_changes:
                created.add(current)
            current = ""
            is_new = False
            parts = line.split()
            if len(parts) >= 4:
                current = parts[3][2:] if parts[3].startswith("b/") else parts[3]
                current = current.replace("\\", "/").lstrip("./")
        elif line.startswith("new file mode"):
            is_new = True
    if current and is_new and pat.match(current) and current not in authoritative_changes:
        created.add(current)
    return sorted(created)

def _validate_created_skills(args, paths: dict, change_manifest: dict, changed_files: list[dict] | None = None) -> dict:
    """Validate tune-created skills before acceptance.

    This is intentionally structural and case-memorization resistant. It does not judge
    semantic quality; full validation bench remains responsible for outcome.
    """
    workspace = Path(paths["workspace"])
    changes = change_manifest.get("changes") if isinstance(change_manifest, dict) else []
    create_changes = [c for c in (changes or []) if isinstance(c, dict) and str(c.get("change_type") or "").upper() == "CREATE_SKILL"]
    errors = []
    warnings = []
    checked = []
    name_re = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")
    before_manifest = _load_json(_workspace_manifest_path(paths, "before"), {})
    before_entries = before_manifest.get("entries") if isinstance(before_manifest, dict) else {}
    before_entries = before_entries if isinstance(before_entries, dict) else {}

    def existing_public_skill(name: str) -> bool:
        if not name:
            return False
        activation = before_entries.get(f"skills/{name}")
        if isinstance(activation, dict):
            target = str(activation.get("target") or "").strip().replace("\\", "/")
            private_targets = {f"skills-local/{name}", f"../skills-local/{name}"}
            if activation.get("type") != "symlink" or target not in private_targets:
                return True
        public_prefix = f"skills/{name}/"
        private_prefix = f"skills/skills-local/{name}/"
        return any(path.startswith(public_prefix) and not path.startswith(private_prefix) for path in before_entries)

    declared_targets = set()
    for change in create_changes:
        cs = change.get("created_skill") if isinstance(change.get("created_skill"), dict) else {}
        target_file = str(change.get("target_file") or cs.get("entrypoint") or "").strip().replace("\\", "/").lstrip("./")
        if target_file:
            declared_targets.add(target_file)
    for entrypoint in _created_skill_entrypoints_from_changes(paths, changed_files):
        if entrypoint not in declared_targets:
            errors.append(f"created skill entrypoint {entrypoint} must be declared as CREATE_SKILL in change_manifest.json")
            checked.append({"target_file": entrypoint, "name": "", "valid": False, "errors": ["missing CREATE_SKILL declaration"], "warnings": []})

    for change in create_changes:
        cs = change.get("created_skill") if isinstance(change.get("created_skill"), dict) else {}
        target_file = str(change.get("target_file") or cs.get("entrypoint") or "").strip().replace("\\", "/").lstrip("./")
        name = str(cs.get("name") or "").strip()
        item = {"target_file": target_file, "name": name, "valid": True, "errors": [], "warnings": []}

        m = re.match(rf"^{re.escape(_skill_write_root())}/([^/]+)/SKILL\.md$", target_file)
        if not m:
            item["errors"].append(f"CREATE_SKILL target_file must be {_skill_write_root()}/<skill-name>/SKILL.md")
            skill_dir_name = name
        else:
            skill_dir_name = m.group(1)
        if not name:
            item["errors"].append("created_skill.name is required")
        elif not name_re.match(name):
            item["errors"].append("created_skill.name must use lowercase letters, digits and hyphens, max 64 chars")
        if name and skill_dir_name and name != skill_dir_name:
            item["errors"].append(f"created_skill.name '{name}' must match directory '{skill_dir_name}'")
        if os.environ.get("CLAWWEB_VERSION") == "openversion":
            # COSEC: flat user Skills must not alias shared/Release code through symlinks.
            candidate = workspace / target_file
            if (not m or skill_dir_name in {"skills-local", "skills-repo", "skills-center", "active"}
                    or skill_dir_name.startswith(("clawevolve-", "clawbench-", "ocb-"))
                    or any(path.is_symlink() for path in (workspace / "skills", candidate.parent, candidate))):
                item["errors"].append("openversion Skill must be a direct ordinary user Skill under skills/")
                item["valid"] = False
                errors.extend(item["errors"])
                checked.append(item)
                continue
        if existing_public_skill(name):
            item["errors"].append(
                f"public/system skill '{name}' already existed before Tune; creating a same-name private copy or replacing its activation is forbidden"
            )

        skill_md = workspace / target_file if target_file else workspace / "__missing__"
        if not skill_md.is_file():
            item["errors"].append(f"SKILL.md not found at {target_file}")
            fm = {"ok": False, "name": "", "description": "", "body": ""}
        else:
            fm = _parse_skill_frontmatter(skill_md)
            if not fm.get("ok"):
                item["errors"].append(str(fm.get("error") or "invalid frontmatter"))
            if fm.get("name") != name:
                item["errors"].append(f"frontmatter name '{fm.get('name')}' must match created_skill.name '{name}'")
            if not str(fm.get("description") or "").strip():
                item["errors"].append("frontmatter description is required")
            if len(str(fm.get("description") or "")) > 1024:
                item["errors"].append("frontmatter description exceeds 1024 chars")
            # Only runtime-loaded skill material can constitute hardcoding.
            # Experiment metadata is expected to name optimization task IDs in
            # falsifiable predictions and signals; scanning that audit metadata
            # made every well-specified CREATE_SKILL candidate fail closed.
            for finding in _looks_like_benchmark_hardcode(skill_md.read_text(encoding="utf-8", errors="replace") if skill_md.exists() else ""):
                item["errors"].append(f"case-memorization guard: {finding}")

        discovery_link = _created_skill_discovery_path(cs) or f"skills/{name}"
        link_path = workspace / discovery_link if discovery_link else workspace / "__missing_link__"
        if not discovery_link.startswith("skills/"):
            item["errors"].append("created_skill.discovery_link must be under skills/")
        elif not link_path.exists():
            item["errors"].append(f"discovery link missing: {discovery_link}")
        else:
            expected_skill_dir = workspace / _skill_write_root() / name if name else None
            if expected_skill_dir is not None:
                try:
                    if link_path.resolve() != expected_skill_dir.resolve():
                        item["errors"].append(f"discovery link {discovery_link} must resolve to {_skill_write_root()}/{name}")
                except Exception as exc:
                    item["errors"].append(f"discovery link target could not be resolved: {type(exc).__name__}: {exc}")

        trigger_scope = str(cs.get("trigger_scope") or "").strip()
        if not trigger_scope:
            item["errors"].append("created_skill.trigger_scope is required")
        elif re.search(r"(?i)\b(all|any|everything|所有|全部|任何)\b", trigger_scope) and len(trigger_scope) < 80:
            item["errors"].append("trigger_scope appears too broad; define a narrow activation scope")
        neg = cs.get("negative_trigger_examples")
        if not isinstance(neg, list) or len([x for x in neg if str(x).strip()]) < 2:
            item["errors"].append("at least two negative_trigger_examples are required")
        if not str(cs.get("why_existing_skills_insufficient") or "").strip():
            item["errors"].append("why_existing_skills_insufficient is required")
        if not str(cs.get("rollback_condition") or change.get("rollback_condition") or "").strip():
            item["errors"].append("rollback_condition is required")
        overlaps = cs.get("overlap_with_existing_skills") or []
        if overlaps and isinstance(overlaps, list):
            for ov in overlaps:
                if isinstance(ov, dict) and ov.get("skill") and not ov.get("resolution"):
                    item["errors"].append(f"overlap with {ov.get('skill')} requires resolution")

        item["valid"] = not item["errors"]
        errors.extend(item["errors"])
        warnings.extend(item["warnings"])
        checked.append(item)

    report = {
        "schema_version": "evolution.skill_creation_report.v0",
        "valid": not errors,
        "created_skill_count": len(create_changes),
        "checked": checked,
        "errors": errors,
        "warnings": warnings,
        "created_at": _now(),
    }
    try:
        paths["tune_dir"].mkdir(parents=True, exist_ok=True)
        (paths["tune_dir"] / "skill_creation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass
    return report


def _change_manifest_structural_types(change_manifest: dict) -> list[str]:
    out = []
    for change in (change_manifest.get("changes") if isinstance(change_manifest, dict) else []) or []:
        if not isinstance(change, dict):
            continue
        ct = str(change.get("change_type") or "").upper()
        if ct in STRUCTURAL_CHANGE_TYPES and ct not in out:
            out.append(ct)
    return out


def _exploration_recovery_plan(change_manifest: dict, tune_report_text: str = "") -> dict:
    plan = {}
    if isinstance(change_manifest, dict):
        exp = change_manifest.get("exploration") if isinstance(change_manifest.get("exploration"), dict) else {}
        plan.update(exp)
        for change in change_manifest.get("changes") or []:
            if not isinstance(change, dict):
                continue
            for key in ("why_temporary_drop_is_expected", "next_round_plan", "expected_recovery_signal", "max_rounds_to_recover"):
                if key not in plan and change.get(key) is not None:
                    plan[key] = change.get(key)
    text = tune_report_text or ""
    if text:
        low = text.lower()
        if "temporary" in low or "阵痛" in text or "下一轮" in text or "next_round" in low:
            plan.setdefault("textual_recovery_plan_present", True)
    required = ["why_temporary_drop_is_expected", "next_round_plan", "expected_recovery_signal"]
    missing = [k for k in required if not str(plan.get(k) or "").strip()]
    max_rounds = plan.get("max_rounds_to_recover", 1)
    try:
        max_rounds = max(1, min(3, int(max_rounds)))
    except Exception:
        max_rounds = 1
    plan["max_rounds_to_recover"] = max_rounds
    plan["complete"] = not missing
    plan["missing"] = missing
    return plan


def _write_exploration_retention(args, paths: dict, report: dict, change_manifest: dict) -> dict:
    exp_dir = paths["round_dir"] / "exploration"
    exp_dir.mkdir(parents=True, exist_ok=True)
    patch_src = paths["tune_dir"] / "diff.patch"
    patch_dst = exp_dir / "candidate.patch"
    try:
        if patch_src.exists():
            shutil.copyfile(patch_src, patch_dst)
        else:
            patch_dst.write_text("", encoding="utf-8")
    except Exception:
        pass
    plan = (report.get("exploration") or {}).get("recovery_plan") or {}
    summary = [
        f"# Exploratory Candidate round-{args.round:03d}",
        "",
        f"- decision: {report.get('decision')}",
        f"- score_delta: {(report.get('delta') or {}).get('validation_score')}",
        f"- structural_change_types: {', '.join((report.get('exploration') or {}).get('structural_change_types') or [])}",
        f"- next_round_plan: {plan.get('next_round_plan', '')}",
        f"- expected_recovery_signal: {plan.get('expected_recovery_signal', '')}",
    ]
    (exp_dir / "candidate_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    retention = {
        "schema_version": "evolution.exploration_retention.v0",
        "round_id": args.round,
        "status": "active",
        "expires_after_round": args.round + int(plan.get("max_rounds_to_recover") or 1),
        "score_delta": (report.get("delta") or {}).get("validation_score"),
        "loss_budget": (report.get("exploration") or {}).get("loss_budget"),
        "structural_change_types": (report.get("exploration") or {}).get("structural_change_types") or [],
        "candidate_patch": str(patch_dst),
        "candidate_summary": str(exp_dir / "candidate_summary.md"),
        "next_round_plan": plan.get("next_round_plan", ""),
        "created_at": _now(),
    }
    (exp_dir / "candidate_retention.json").write_text(json.dumps(retention, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return retention


def _maybe_mark_exploratory_keep(args, decision: str, score_delta, hard_blocked: bool, change_manifest: dict, tune_report_text: str) -> tuple[str, dict | None]:
    """Return possibly replaced decision plus exploration metadata."""
    if hard_blocked or decision != "rejected_score_drop" or score_delta is None:
        return decision, None
    structural = _change_manifest_structural_types(change_manifest)
    if not structural:
        return decision, None
    budget = float(getattr(args, "exploration_loss_budget", DEFAULT_EXPLORATION_LOSS_BUDGET) or 0)
    if budget <= 0 or float(score_delta) < -budget or float(score_delta) >= 0:
        return decision, None
    plan = _exploration_recovery_plan(change_manifest, tune_report_text)
    if not plan.get("complete"):
        return decision, {
            "eligible": False,
            "reason": "structural score drop is within budget but recovery plan is incomplete",
            "missing_recovery_plan_fields": plan.get("missing") or [],
            "loss_budget": budget,
            "score_delta": score_delta,
            "structural_change_types": structural,
            "recovery_plan": plan,
        }
    return "exploratory_keep", {
        "eligible": True,
        "reason": "structural change with bounded temporary score drop retained for next-round recovery",
        "loss_budget": budget,
        "score_delta": score_delta,
        "max_recovery_rounds": plan.get("max_rounds_to_recover", 1),
        "structural_change_types": structural,
        "recovery_plan": plan,
    }


def _task_scores_from_report(path: str | Path) -> dict:
    report = _load_json(Path(path), {}) if path else {}
    if not isinstance(report, dict):
        return {}
    out = {}
    for task in report.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("task_id") or task.get("id") or task.get("name") or "").strip()
        if not tid:
            continue
        grading = task.get("grading") or {}
        runs = grading.get("runs") or []
        score = None
        if runs and isinstance(runs[0], dict) and runs[0].get("score") is not None:
            score = runs[0].get("score")
        elif grading.get("mean") is not None:
            score = grading.get("mean")
        try:
            out[tid] = float(score) if score is not None else None
        except Exception:
            out[tid] = None
    return out


def _find_bootstrap_validation_report(paths: dict) -> Path | None:
    """Locate the immutable test report produced before optimization starts."""
    run_dir = Path(paths["run_dir"])
    prepare = _load_json(run_dir / "prepare_manifest.json", {})
    for key in ("test_benchmark_report", "validation_benchmark_report"):
        candidate = str(prepare.get(key) or "").strip() if isinstance(prepare, dict) else ""
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    reports = sorted((run_dir / "bench" / "baseline").glob("**/test/**/*_benchmark_report.json"))
    return reports[-1] if reports else None


def _eval_identity_complete(identity: dict) -> bool:
    if not isinstance(identity, dict):
        return False
    return all([
        bool(identity.get("bench_model")),
        bool(identity.get("benchmark_version")),
        bool(identity.get("bench_mode")),
        bool(identity.get("suite")),
        bool((identity.get("validation_fixture") or {}).get("sha256")),
    ])


def _stable_json_sha256(value) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        payload = str(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _evaluation_identity(args, paths: dict, *, kind: str, artifact_path: str | Path = "", report_path: str | Path = "") -> dict:
    """Build the shared cache/comparability identity used by every eval stage.

    Some deployments cannot expose a stable remote fixture/scorer/runtime hash.
    Those fields remain empty and `complete` is false rather than pretending the
    score or cache is fully comparable.
    """
    state = _load_json(Path(paths["round_dir"]) / "round_state.json", {}) if paths.get("round_dir") else {}
    legacy = state.get("identity") if isinstance(state, dict) and isinstance(state.get("identity"), dict) else {}
    fixture_key = "optimization_fixture" if kind == "optimization" else "validation_fixture"
    fixture = legacy.get(fixture_key) if isinstance(legacy.get(fixture_key), dict) else {}
    artifact = Path(artifact_path) if artifact_path else None
    report = _load_json(Path(report_path), {}) if report_path else {}
    timeout_policy = {
        "bench_timeout": getattr(args, "bench_timeout", None),
        "strict_bench": bool(getattr(args, "strict_bench", False)),
        "watchdog_retries": WATCHDOG_STEP_POLICIES.get("bench-opt", {}).get("max_attempts") if "WATCHDOG_STEP_POLICIES" in globals() else 3,
    }
    skill_base = Path(paths.get("skill_base") or "")
    workspace = Path(paths.get("workspace") or "")
    workflow_source = Path(__file__).resolve()
    clawbench_candidates = [skill_base / "clawbench-base", workflow_source.parents[3] / "clawbench-base"]
    clawbench_home = next((path for path in clawbench_candidates if path.is_dir()), Path())
    benchmark_version = report.get("benchmark_version") or legacy.get("benchmark_version") or ""
    version_path = clawbench_home / "BENCHMARK_VERSION"
    if not benchmark_version and version_path.is_file():
        benchmark_version = version_path.read_text(encoding="utf-8", errors="replace").strip()
    scorer_path = clawbench_home / "scripts/lib_grading.py"
    adapter_path = clawbench_home / "scripts/clawmind_adapter.py"
    mcp_candidates = [workspace / ".openclaw/openclaw.json", workspace / "openclaw.json", workspace / ".mcp.json"]
    mcp_path = next((path for path in mcp_candidates if path.is_file()), None)
    raw_seed = getattr(args, "sampling_seed", None)
    raw_temperature = getattr(args, "temperature", None)
    raw_max_tokens = getattr(args, "max_tokens", None)
    domain_id = (
        getattr(args, "train_bench_domain_id", "") if kind == "optimization"
        else getattr(args, "test_bench_domain_id", "")
    ) or _env("DOMAIN_ID", "")
    identity = {
        "schema_version": "evolution.evaluation_identity.v1",
        "kind": kind,
        "artifact_sha256": _sha256(artifact) if artifact and artifact.is_file() else str(legacy.get("artifact_sha256") or ""),
        "domain_id": str(domain_id or ""),
        "domain_owner_id": str(getattr(args, "owner_id", "") or _env("CLAWBENCH_OWNER_ID", "")),
        "optimization_fixture_sha256": str(((legacy.get("optimization_fixture") or {}).get("sha256")) or (fixture.get("sha256") if kind == "optimization" else "") or ""),
        "validation_fixture_sha256": str(((legacy.get("validation_fixture") or {}).get("sha256")) or (fixture.get("sha256") if kind == "validation" else "") or ""),
        "benchmark_version": benchmark_version,
        "suite": getattr(args, "suite", None) or legacy.get("suite") or "all",
        "agent_model": _bench_model(args),
        "agent_model_parameters_sha256": _stable_json_sha256({"model": _bench_model(args)}),
        "judge_model": getattr(args, "judge", "") or legacy.get("judge_model") or "",
        "judge_config_sha256": str(legacy.get("judge_config_sha256") or _stable_json_sha256({"judge": getattr(args, "judge", "")})),
        "scorer_sha256": str(legacy.get("scorer_sha256") or (_sha256(scorer_path) if scorer_path.is_file() else "")),
        "runtime_version_or_commit": str(legacy.get("runtime_version_or_commit") or _env("CLAWEVOLVE_RUNTIME_VERSION") or f"sha256:{_sha256(workflow_source)}"),
        "adapter_version_or_sha256": str(legacy.get("adapter_version_or_sha256") or _env("CLAWEVOLVE_ADAPTER_VERSION") or (f"sha256:{_sha256(adapter_path)}" if adapter_path.is_file() else "")),
        "tool_mcp_config_sha256": str(legacy.get("tool_mcp_config_sha256") or (_sha256(mcp_path) if mcp_path else _stable_json_sha256({"mcp_config": "absent"}))),
        "sampling_seed": raw_seed if raw_seed is not None else "unset",
        "temperature": raw_temperature if raw_temperature is not None else "provider_default",
        "max_tokens": raw_max_tokens if raw_max_tokens is not None else "provider_default",
        "timeout_retry_policy_sha256": _stable_json_sha256(timeout_policy),
        # Legacy aliases remain during migration.
        "bench_model": _bench_model(args),
        "bench_mode": getattr(args, "bench_mode", None),
        fixture_key: fixture,
    }
    cache_required = ["artifact_sha256", f"{kind}_fixture_sha256", "benchmark_version", "suite", "agent_model", "scorer_sha256", "runtime_version_or_commit", "adapter_version_or_sha256", "timeout_retry_policy_sha256"]
    if getattr(args, "bench_mode", None) != "local":
        cache_required.append("domain_id")
    identity["cache_missing_fields"] = [key for key in cache_required if identity.get(key) in (None, "")]
    identity["cache_complete"] = not identity["cache_missing_fields"]
    causal_missing = [key for key in EVALUATION_IDENTITY_KEYS if identity.get(key) in (None, "")]
    if raw_seed is None:
        causal_missing.append("sampling_seed(stable_seed_unavailable)")
    if raw_temperature is None:
        causal_missing.append("temperature(explicit_value_unavailable)")
    if raw_max_tokens is None:
        causal_missing.append("max_tokens(explicit_value_unavailable)")
    identity["causal_missing_fields"] = list(dict.fromkeys(causal_missing))
    identity["causal_comparability_complete"] = not identity["causal_missing_fields"]
    identity["missing_fields"] = identity["causal_missing_fields"]
    identity["complete"] = identity["causal_comparability_complete"]
    identity["identity_sha256"] = _stable_json_sha256({key: identity.get(key) for key in EVALUATION_IDENTITY_KEYS})
    return identity


def _evaluation_identity_matches(left: dict, right: dict, *, require_complete: bool = True) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if require_complete and (not left.get("cache_complete", left.get("complete")) or not right.get("cache_complete", right.get("complete"))):
        return False
    return bool(left.get("identity_sha256") and left.get("identity_sha256") == right.get("identity_sha256"))


def _evaluation_identity_matches_frozen_task(left: dict, right: dict, *, require_complete: bool = True) -> bool:
    """Compare identities while recovering a fixture hash hidden by adapter-only Round state.

    The Domain and its pinned context are task-scoped. A new Optimize Step may no longer
    carry the adapter's temporary inputPath, although the cached identity still does.
    Reusing that cached fixture hash is safe only when the frozen Domain identity and all
    other evaluation fields still match.
    """
    if _evaluation_identity_matches(left, right, require_complete=require_complete):
        return True
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if not left.get("domain_id") or left.get("domain_id") != right.get("domain_id"):
        return False
    patched = dict(right)
    kind = str(right.get("kind") or left.get("kind") or "")
    fixture_key = "optimization_fixture" if kind == "optimization" else "validation_fixture"
    fixture_sha_key = f"{kind}_fixture_sha256"
    current_fixture = patched.get(fixture_key) if isinstance(patched.get(fixture_key), dict) else {}
    cached_fixture = left.get(fixture_key) if isinstance(left.get(fixture_key), dict) else {}
    if current_fixture.get("sha256") or not cached_fixture.get("sha256"):
        return False
    patched[fixture_key] = dict(cached_fixture)
    patched[fixture_sha_key] = left.get(fixture_sha_key) or cached_fixture.get("sha256") or ""
    patched["cache_missing_fields"] = [key for key in patched.get("cache_missing_fields") or [] if key != fixture_sha_key]
    patched["cache_complete"] = not patched["cache_missing_fields"]
    patched["identity_sha256"] = _stable_json_sha256({key: patched.get(key) for key in EVALUATION_IDENTITY_KEYS})
    return _evaluation_identity_matches(left, patched, require_complete=require_complete)


def _find_bootstrap_optimization_report(paths: dict) -> Path | None:
    run_dir = Path(paths["run_dir"])
    prepare = _load_json(run_dir / "prepare_manifest.json", {})
    for key in ("train_benchmark_report", "optimization_benchmark_report"):
        candidate = str(prepare.get(key) or "").strip() if isinstance(prepare, dict) else ""
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    reports = sorted((run_dir / "bench" / "baseline").glob("**/train/**/*_benchmark_report.json"))
    return reports[-1] if reports else None


def _baseline_result_metadata(report_path: Path | None) -> dict:
    if not report_path:
        return {}
    candidate = Path(report_path).parent.parent / "baseline_result.json"
    value = _load_json(candidate, {})
    return value if isinstance(value, dict) else {}


def _bootstrap_optimization_baseline(args, paths: dict) -> dict:
    report_path = _find_bootstrap_optimization_report(paths)
    if not report_path:
        return {"available": False, "source": "bootstrap_train_missing"}
    summary = _compute_bench_summary(report_path)
    baseline_artifact = Path(paths["optimize_input_dir"]) / "baseline" / "artifact_v0.zip"
    metadata = _baseline_result_metadata(report_path)
    identity = metadata.get("identity") if isinstance(metadata.get("identity"), dict) else {}
    if not identity:
        identity = _evaluation_identity(args, paths, kind="optimization", artifact_path=baseline_artifact, report_path=report_path)
    return {
        "schema_version": "evolution.baseline_optimization.v1",
        "available": summary.get("score") is not None,
        "source": "bootstrap_train",
        "round_id": 0,
        "result_path": str(report_path),
        "summary": summary,
        "task_scores": _task_scores_from_report(report_path),
        "identity": identity,
        "bench_run_id": metadata.get("benchRunId") or "",
        "producer_step_id": metadata.get("producerStepId") or "",
        "domain_id": metadata.get("domainId") or "",
        "domain_owner_id": metadata.get("domainOwnerId") or "",
        "registered_at": _now(),
    }


def _baseline_opt_registry(manifest: dict) -> dict:
    value = manifest.get("accepted_baseline_optimization") if isinstance(manifest, dict) else None
    return value if isinstance(value, dict) else {}


def _baseline_validation_registry(manifest: dict) -> dict:
    value = manifest.get("accepted_baseline_validation") if isinstance(manifest, dict) else None
    return value if isinstance(value, dict) else {}


def _baseline_registry_usable(registry: dict) -> bool:
    if not isinstance(registry, dict) or registry.get("available") is False:
        return False
    result_path = str(registry.get("result_path") or "").strip()
    summary = registry.get("summary") if isinstance(registry.get("summary"), dict) else {}
    return bool(result_path and Path(result_path).is_file() and summary.get("score") is not None)


def _round_one_baseline_cache_matches(args, paths: dict, *, kind: str, registry: dict, artifact_path: Path) -> bool:
    """Validate task-scoped Round 1 caches, including legacy bench-plan identities."""
    if not _baseline_registry_usable(registry):
        return False
    cached = registry.get("identity") if isinstance(registry.get("identity"), dict) else {}
    desired = _evaluation_identity(
        args, paths, kind=kind, artifact_path=artifact_path,
        report_path=registry.get("result_path", ""),
    )
    if cached.get("identity_sha256"):
        return _evaluation_identity_matches_frozen_task(cached, desired, require_complete=False)
    cached_model = str(cached.get("agent_model") or cached.get("bench_model") or "")
    if cached_model and cached_model != str(desired.get("agent_model") or ""):
        return False
    cached_suite = str(cached.get("suite") or "")
    if cached_suite and cached_suite != str(desired.get("suite") or ""):
        return False
    cached_artifact = str(registry.get("artifact_sha256") or cached.get("artifact_sha256") or "")
    if cached_artifact and cached_artifact != str(desired.get("artifact_sha256") or ""):
        return False
    cached_domain = str(cached.get("domain_id") or registry.get("domain_id") or "")
    if cached_domain and cached_domain != str(desired.get("domain_id") or ""):
        return False
    return True


def _baseline_artifact_path(args, paths: dict, manifest: dict, state: dict) -> Path | None:
    if int(getattr(args, "round", 1) or 1) == 1:
        candidate = Path(paths["optimize_input_dir"]) / "baseline" / "artifact_v0.zip"
        return candidate if candidate.is_file() else None
    raw = str(state.get("baseline_artifact") or ((_accepted_artifact_record(manifest) or {}).get("localPath")) or "").strip()
    return Path(raw) if raw and Path(raw).is_file() else None


def _persist_baseline_bench_report(args, paths: dict, *, role: str, bench_record: dict) -> dict:
    """Copy an on-demand baseline report outside the retry-archived Round directory."""
    source = Path(str(bench_record.get("resultPath") or ""))
    summary = bench_record.get("summary") if isinstance(bench_record.get("summary"), dict) else {}
    if not source.is_file() or summary.get("score") is None:
        raise SystemExit(
            f"baseline {role} bench did not produce a readable scored report: "
            f"result_path={source}, log_path={bench_record.get('logPath') or ''}"
        )
    producer = _safe_slug(str(getattr(args, "step_id", "") or f"optimize-round-{int(args.round):03d}"))
    output_dir = Path(paths["run_dir"]) / "bench" / "baseline" / producer / role / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / source.name
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    durable = dict(bench_record)
    durable["sourceResultPath"] = str(source)
    durable["resultPath"] = str(target)
    durable["baselineRole"] = role
    durable["producerStepId"] = str(getattr(args, "step_id", "") or "")
    durable["domainId"] = str(getattr(args, "train_bench_domain_id" if role == "train" else "test_bench_domain_id", "") or "")
    durable["domainOwnerId"] = str(getattr(args, "owner_id", "") or "")
    durable["persistedAt"] = _now()
    (output_dir.parent / "baseline_result.json").write_text(
        json.dumps(durable, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return durable


def _run_and_register_baseline_role(args, paths: dict, *, kind: str, artifact_path: Path | None, round_id) -> dict:
    role = "train" if kind == "optimization" else "test"
    state_key = "baseline_optimization" if kind == "optimization" else "baseline_validation"
    _log(args, "load-baseline-opt", f"baseline {role} cache miss; running Bench Core")
    if getattr(args, "bench_mode", "local") == "local":
        action_bench_local(
            args, kind, step_name_override="load-baseline-opt", state_key=state_key,
            mark_step=False, print_result=False,
        )
    else:
        _adapter_bench(
            args, kind, step_name_override="load-baseline-opt", state_key=state_key,
            mark_step=False, print_result=False,
        )
    state_path = Path(paths["round_dir"]) / "round_state.json"
    state = _load_json(state_path, {})
    bench_record = ((state.get("bench") or {}).get(state_key) or {}) if isinstance(state, dict) else {}
    if str(bench_record.get("status") or "").lower() != "succeeded":
        raise SystemExit(
            f"baseline {role} bench failed: status={bench_record.get('status')}, "
            f"domain={getattr(args, 'train_bench_domain_id' if role == 'train' else 'test_bench_domain_id', '')}, "
            f"log_path={bench_record.get('logPath') or ''}"
        )
    durable_record = _persist_baseline_bench_report(args, paths, role=role, bench_record=bench_record)
    state.setdefault("bench", {})[state_key] = durable_record
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result_path = durable_record["resultPath"]
    identity = _evaluation_identity(
        args, paths, kind=kind, artifact_path=artifact_path or "", report_path=result_path,
    )
    durable_record["identity"] = identity
    (Path(result_path).parent.parent / "baseline_result.json").write_text(
        json.dumps(durable_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    schema = "evolution.baseline_optimization.v1" if kind == "optimization" else "evolution.baseline_validation.v1"
    return {
        "schema_version": schema,
        "available": True,
        "source": "optimize_cache_miss_rerun",
        "round_id": round_id,
        "artifact_path": str(artifact_path or ""),
        "artifact_sha256": _sha256(artifact_path) if artifact_path and artifact_path.is_file() else "",
        "result_path": result_path,
        "summary": durable_record.get("summary") or {},
        "task_scores": _task_scores_from_report(result_path),
        "identity": identity,
        "bench_run_id": durable_record.get("benchRunId") or "",
        "producer_step_id": durable_record.get("producerStepId") or "",
        "domain_id": durable_record.get("domainId") or "",
        "domain_owner_id": durable_record.get("domainOwnerId") or "",
        "registered_at": _now(),
    }


def _bootstrap_validation_baseline(paths: dict) -> dict:
    """Return the task-start validation baseline with its own immutable identity."""
    run_dir = Path(paths["run_dir"])
    bootstrap = _load_json(run_dir / "bench" / "bootstrap_manifest.json", {})
    prepare = _load_json(run_dir / "prepare_manifest.json", {})
    report_path = _find_bootstrap_validation_report(paths)
    report = _load_json(report_path, {}) if report_path else {}
    score = None
    if isinstance(bootstrap, dict):
        score = ((bootstrap.get("test_summary") or {}).get("overall_score"))
    if score is None and isinstance(prepare, dict):
        score = prepare.get("test_baseline_score")
    if score is None and report_path:
        score = _compute_bench_summary(report_path).get("score")
    try:
        score = float(score) if score is not None else None
    except Exception:
        score = None
    identity = {}
    metadata = _baseline_result_metadata(report_path)
    candidate = metadata.get("identity") if isinstance(metadata.get("identity"), dict) else None
    if candidate:
        identity = dict(candidate)
    for source in (bootstrap, prepare):
        if identity:
            break
        candidate = source.get("test_identity") if isinstance(source, dict) else None
        if isinstance(candidate, dict) and candidate:
            identity = dict(candidate)
            break
    if not identity and isinstance(report, dict):
        identity = {
            "schema_version": "evolution.eval_identity.legacy",
            "bench_model": report.get("model"),
            "benchmark_version": report.get("benchmark_version"),
            "suite": report.get("suite"),
            "report_sha256": _sha256_text_or_file(report_path) if report_path else "",
        }
    identity_complete = _eval_identity_complete(identity)
    task_scores = _task_scores_from_report(report_path) if report_path else {}
    return {
        "available": score is not None,
        "round_id": 0,
        "validation_score": score,
        "validation_result_path": str(report_path or ""),
        "validation_task_scores": task_scores,
        "identity": identity,
        "identity_complete": identity_complete,
        "source": "bootstrap_test_baseline",
        "bench_run_id": metadata.get("benchRunId") or "",
        "producer_step_id": metadata.get("producerStepId") or "",
        "domain_id": metadata.get("domainId") or "",
        "domain_owner_id": metadata.get("domainOwnerId") or "",
    }



def _task_id_from_template(path: Path) -> str:
    """Return the task id represented by a ClawBench task_*.md template."""
    return Path(path).stem


def _list_bench_template_task_ids(template_dir: Path) -> list[str]:
    return [_task_id_from_template(p) for p in sorted(Path(template_dir).glob("task_*.md"))]


def _load_validation_bench_plan(paths: dict) -> dict:
    """Load tune-proposed staged validation bench plan, if present."""
    candidates = [
        paths["tune_dir"] / "bench_plan.json",
        paths["round_dir"] / "bench_plan" / "bench_plan.json",
    ]
    for path in candidates:
        data = _load_json(path, None)
        if isinstance(data, dict):
            data.setdefault("_source_path", str(path))
            return data
    return {}


def _validation_smoke_stage_from_plan(plan: dict) -> dict:
    if not isinstance(plan, dict) or str(plan.get("strategy") or "").lower() != "staged":
        return {}
    for stage in plan.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        kind = str(stage.get("kind") or "validation").lower()
        suite = str(stage.get("suite") or "").lower()
        task_ids = [str(x).strip() for x in (stage.get("task_ids") or []) if str(x).strip()]
        if kind == "validation" and task_ids and suite not in {"all", "full"}:
            return stage
    return {}


def _validate_validation_bench_plan(args, paths: dict, plan: dict, template_dir: Path) -> dict:
    """Validate a tune-proposed staged validation plan. Invalid plans fall back to full bench."""
    all_task_ids = _list_bench_template_task_ids(template_dir)
    all_set = set(all_task_ids)
    stage = _validation_smoke_stage_from_plan(plan)
    errors = []
    selected = []
    if not plan:
        errors.append("bench_plan.json not found")
    if plan and not stage:
        errors.append("no validation smoke stage with non-empty task_ids")
    if stage:
        seen = set()
        for tid in [str(x).strip() for x in (stage.get("task_ids") or []) if str(x).strip()]:
            if tid in seen:
                continue
            seen.add(tid)
            if tid not in all_set:
                errors.append(f"unknown validation task_id: {tid}")
            else:
                selected.append(tid)
    if not all_task_ids:
        errors.append(f"no task_*.md files in validation template dir: {template_dir}")
    if selected and len(selected) >= len(all_task_ids):
        errors.append("smoke task_ids cover all validation tasks; staged bench would not save work")
    threshold = stage.get("promotion_threshold", None) if stage else None
    try:
        threshold = float(threshold) if threshold is not None else None
    except Exception:
        errors.append("promotion_threshold must be numeric when provided")
        threshold = None
    report = {
        "schema_version": "evolution.bench_plan_report.v0",
        "strategy": plan.get("strategy") if isinstance(plan, dict) else None,
        "source_path": plan.get("_source_path") if isinstance(plan, dict) else "",
        "valid": not errors,
        "errors": errors,
        "all_task_ids": all_task_ids,
        "selected_task_ids": selected,
        "remaining_task_ids": [tid for tid in all_task_ids if tid not in set(selected)],
        "promotion_threshold": threshold,
        "fallback_to_full": bool(errors),
        "created_at": _now(),
    }
    out_dir = paths["round_dir"] / "bench_plan"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bench_plan_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _prepare_bench_subset_dir(source_dir: Path, task_ids: list[str], subset_dir: Path) -> dict:
    """Create a benchmark template subset by copying shared files and selected task_*.md."""
    if subset_dir.exists():
        shutil.rmtree(subset_dir)
    subset_dir.mkdir(parents=True, exist_ok=True)
    selected = set(task_ids or [])
    copied = []
    for item in sorted(source_dir.iterdir()):
        target = subset_dir / item.name
        if item.is_file() and item.name.startswith("task_") and item.suffix == ".md":
            if item.stem not in selected:
                continue
            shutil.copy2(item, target)
            copied.append(item.stem)
        elif item.is_dir():
            shutil.copytree(item, target)
        elif item.is_file():
            shutil.copy2(item, target)
    return {"subset_dir": str(subset_dir), "task_ids": copied, "task_count": len(copied)}


def _merge_benchmark_reports(report_paths: list[Path], merged_path: Path) -> dict:
    """Merge non-overlapping ClawBench reports into one report for final full-coverage acceptance."""
    merged = None
    tasks = []
    seen = set()
    duplicate_task_ids = []
    efficiency_totals = {}
    sources = []
    for path in report_paths:
        report = _load_json(Path(path), {}) if path else {}
        if not isinstance(report, dict):
            continue
        if merged is None:
            merged = dict(report)
        sources.append(str(path))
        eff = report.get("efficiency") or {}
        for key, value in eff.items():
            if isinstance(value, (int, float)):
                efficiency_totals[key] = efficiency_totals.get(key, 0) + value
        for task in report.get("tasks") or []:
            tid = str(task.get("task_id") or task.get("id") or task.get("name") or "").strip()
            if tid and tid in seen:
                duplicate_task_ids.append(tid)
                continue
            if tid:
                seen.add(tid)
            tasks.append(task)
    if merged is None:
        merged = {"tasks": []}
    merged["tasks"] = tasks
    if efficiency_totals:
        merged["efficiency"] = {**(merged.get("efficiency") or {}), **efficiency_totals}
    merged.setdefault("metadata", {})
    if isinstance(merged["metadata"], dict):
        merged["metadata"].update({"merged_from": sources, "staged_merge": True, "duplicate_task_ids": sorted(set(duplicate_task_ids))})
    merged_path.parent.mkdir(parents=True, exist_ok=True)
    merged_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"resultPath": str(merged_path), "summary": _compute_bench_summary(merged_path), "task_ids": sorted(seen), "sources": sources, "duplicate_task_ids": sorted(set(duplicate_task_ids))}


def _paired_eval_from_scores(baseline_scores: dict, current_scores: dict, margin: float, min_win_rate: float) -> dict:
    pairs = []
    for tid, b in sorted((baseline_scores or {}).items()):
        c = (current_scores or {}).get(tid)
        if b is None or c is None:
            continue
        try:
            delta = float(c) - float(b)
        except Exception:
            continue
        pairs.append({"task_id": tid, "baseline": float(b), "current": float(c), "delta": delta, "win": delta > 0, "loss": delta < 0})
    n = len(pairs)
    mean_delta = (sum(p["delta"] for p in pairs) / n) if n else None
    wins = sum(1 for p in pairs if p["delta"] > 0)
    losses = sum(1 for p in pairs if p["delta"] < 0)
    ties = n - wins - losses
    win_rate = (wins / n) if n else None
    return {
        "schema_version": "evolution.paired_eval.v0",
        "n": n,
        "mean_delta": mean_delta,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "win_rate": win_rate,
        "margin": margin,
        "min_win_rate": min_win_rate,
        "passed": bool(n and mean_delta is not None and mean_delta >= margin and (win_rate or 0) >= min_win_rate),
        "pairs": pairs[:200],
    }


def _paired_eval_has_replicates(paired_eval: dict, min_replicates: int) -> bool:
    if not isinstance(paired_eval, dict):
        return False
    if paired_eval.get("evaluation_unit") != "task_x_replicate":
        return False
    try:
        return int(paired_eval.get("replicate_count") or 0) >= int(min_replicates)
    except Exception:
        return False


def _load_or_compute_paired_eval(args, state: dict, run_state: dict, current_score, baseline_score) -> dict:
    paths = resolve_paths(args)
    margin = float(getattr(args, "acceptance_margin", DEFAULT_ACCEPTANCE_MARGIN) or 0)
    min_win_rate = float(getattr(args, "paired_min_win_rate", DEFAULT_PAIRED_MIN_WIN_RATE) or 0)
    explicit = _load_json(paths["accept_dir"] / "paired_eval.json", {})
    if isinstance(explicit, dict) and explicit:
        explicit.setdefault("source", "acceptance/paired_eval.json")
        return explicit
    baseline_scores = run_state.get("last_accepted_validation_task_scores") if isinstance(run_state, dict) else None
    current_result = (((state.get("bench") or {}).get("validation") or {}).get("resultPath"))
    current_scores = _task_scores_from_report(current_result) if current_result else {}
    if isinstance(baseline_scores, dict) and current_scores:
        paired = _paired_eval_from_scores(baseline_scores, current_scores, margin, min_win_rate)
        paired["source"] = "baseline/current task scores"
        try:
            paths["accept_dir"].mkdir(parents=True, exist_ok=True)
            (paths["accept_dir"] / "paired_eval.json").write_text(json.dumps(paired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
        return paired
    delta = None
    try:
        if current_score is not None and baseline_score is not None:
            delta = float(current_score) - float(baseline_score)
    except Exception:
        pass
    return {"schema_version": "evolution.paired_eval.v0", "source": "aggregate_only", "n": 0, "mean_delta": delta, "margin": margin, "min_win_rate": min_win_rate, "passed": None, "reason": "no paired task-level baseline/current scores available"}


def _write_evidence_database(args, stage: str = "accept") -> dict:
    """Persist a small suspected/verified evidence DB for the round.

    This converts natural-language and runtime signals into machine-readable evidence
    without requiring a specific review-agent output schema.
    """
    paths = resolve_paths(args)
    state = _load_json(paths["round_dir"] / "round_state.json", {})
    if not isinstance(state, dict):
        state = {}
    evidence = []
    change = state.get("change_summary") if isinstance(state.get("change_summary"), dict) else _round_change_summary(paths)
    reach = state.get("reachability") if isinstance(state.get("reachability"), dict) else _candidate_reachability_report(paths, change)
    evidence.append({"id": "candidate.change", "status": "verified" if not change.get("is_noop") else "verified_noop", "kind": "change_summary", "data": change})
    evidence.append({"id": "candidate.reachability", "status": "verified" if reach.get("activated") else "failed", "kind": "activation_gate", "data": reach})
    acc = state.get("acceptance") if isinstance(state.get("acceptance"), dict) else _load_json(paths["accept_dir"] / "acceptance_report.json", {})
    if acc:
        evidence.append({"id": "acceptance.decision", "status": "verified", "kind": "acceptance", "data": {k: acc.get(k) for k in ("decision", "accepted", "restore_required", "reason")}})
    review_text = ""
    for rp in [paths["spec_dir"] / f"spec-v{args.round}.md", paths["spec_dir"] / "review_report.md"]:
        try:
            if rp.exists():
                review_text += rp.read_text(encoding="utf-8", errors="replace")[:4000] + "\n"
        except Exception:
            pass
    if review_text.strip():
        evidence.append({"id": "review.natural_language", "status": "suspected", "kind": "review_claims", "data": {"excerpt": review_text[:1200], "note": "natural-language review is not treated as verified until linked to bench/reachability evidence"}})
    db = {"schema_version": "evolution.evidence_db.v0", "task_id": paths["task_id"], "round_id": args.round, "stage": stage, "evidence": evidence, "created_at": _now()}
    try:
        diag_dir = paths["round_dir"] / "diagnosis"
        diag_dir.mkdir(parents=True, exist_ok=True)
        (diag_dir / "evidence_db.json").write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        run_diag = paths["run_dir"] / "diagnose" / "evidence_db.jsonl"
        run_diag.parent.mkdir(parents=True, exist_ok=True)
        with run_diag.open("a", encoding="utf-8") as f:
            f.write(json.dumps(db, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return db


def _bench_preflight(args, kind: str, template_dir: Path | None = None, benchmark_py: Path | None = None) -> dict:
    paths = resolve_paths(args)
    checks = []
    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    ws = Path(paths["workspace"])
    add("workspace_exists", ws.is_dir(), ws)
    if benchmark_py is not None:
        add("benchmark_py_exists", Path(benchmark_py).is_file(), benchmark_py)
    if template_dir is not None:
        t = Path(template_dir)
        add("template_dir_exists", t.is_dir(), t)
        add("template_has_tasks", bool(list(t.glob("task_*.md"))) if t.is_dir() else False, t)
    disk = _check_disk_space(args, DEFAULT_WATCHDOG_MIN_DISK_SPACE_GB if 'DEFAULT_WATCHDOG_MIN_DISK_SPACE_GB' in globals() else 1.0)
    add("disk_space", disk.get("ok", True), json.dumps(disk, ensure_ascii=False))
    report = {"schema_version": "evolution.bench_preflight.v0", "kind": kind, "ok": all(c["ok"] for c in checks), "checks": checks, "created_at": _now()}
    try:
        out = paths["round_dir"] / "preflight" / f"bench_{kind}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass
    return report

def _write_round_change_summary(args) -> dict:
    paths = resolve_paths(args)
    summary = _round_change_summary(paths)
    reachability = _candidate_reachability_report(paths, summary)
    change_manifest = _load_change_manifest(paths)
    skill_creation = _validate_created_skills(args, paths, change_manifest, summary.get("changed_files") or [])
    tune_dir = paths["tune_dir"]
    tune_dir.mkdir(parents=True, exist_ok=True)
    (tune_dir / "change_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state_path = paths["round_dir"] / "round_state.json"
    state = _load_json(state_path, {})
    if isinstance(state, dict):
        state["change_summary"] = summary
        state["reachability"] = reachability
        state["change_manifest"] = change_manifest
        state["skill_creation"] = skill_creation
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _record_round_identity(args, paths: dict, state: dict) -> dict:
    """Record model/benchmark/fixture/scorer identity for comparability audits."""
    try:
        clawbench_home = str(resolve_clawbench_home(args, paths)) if getattr(args, "bench_mode", "local") == "local" else ""
    except Exception as exc:
        clawbench_home = ""
        clawbench_error = f"{type(exc).__name__}: {exc}"
    else:
        clawbench_error = ""
    identity = {
        "schema_version": "evolution.identity.v0",
        "bench_model": _bench_model(args),
        "optimizer_model": _optimizer_model(args, "tune"),
        "review_model": _optimizer_model(args, "review"),
        "bench_mode": getattr(args, "bench_mode", "local"),
        "suite": getattr(args, "suite", ""),
        "clawbench_home": clawbench_home,
        "clawbench_home_error": clawbench_error,
        "local_opt_template_dir": str(_local_template_dir(args, "optimization", paths)),
        "local_val_template_dir": str(_local_template_dir(args, "validation", paths)),
        "input_spec_sha256": _sha256_text_or_file(Path(state.get("input_spec") or "")),
        "objective_sha256": _sha256_text_or_file(Path(state.get("objective_md") or "")),
        "baseline_artifact_sha256": _sha256_text_or_file(Path(state.get("baseline_artifact") or "")) if state.get("baseline_artifact") else "",
    }
    try:
        identity["optimization_fixture"] = _tree_identity(Path(identity["local_opt_template_dir"]))
        identity["validation_fixture"] = _tree_identity(Path(identity["local_val_template_dir"]))
    except Exception:
        pass
    return identity




def _deploy_dry_run(args, image_path: Path, *, log_step: str, reason: str) -> dict:
    """Validate an artifact with clawevolve-deploy --dry-run before trusting it.

    The dry-run deploys into a temp workspace inside deploy.py, so it does not mutate the
    real openclaw workspace. It catches broken package structure, invalid manifests and
    hard laydown/verification failures before an artifact is promoted or used for restore.
    """
    paths = resolve_paths(args)
    script = find_script(paths["skill_base"], "clawevolve-deploy", "scripts/deploy.sh")
    cmd = [
        "bash", str(script),
        "--image", str(image_path),
        "--dry-run",
        "--skip-evolve-results",
        "--evolve-run-id", paths["task_id"],
    ]
    _log(args, log_step, f"deploy dry-run precheck ({reason}): {' '.join(cmd)}")
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=600)
    result = {
        "schema_version": "evolution.deploy_dry_run.v0",
        "ok": proc.returncode == 0,
        "reason": reason,
        "image": str(image_path),
        "exitCode": proc.returncode,
        "stdout": proc.stdout[-12000:],
        "stderr": proc.stderr[-12000:],
        "created_at": _now(),
    }
    if proc.returncode != 0:
        _log_block(args, log_step, "DEPLOY DRY-RUN FAILED", proc.stdout + proc.stderr)
    return result


def _deploy_dry_run_error(result: dict) -> str:
    detail = str(result.get("stderr") or result.get("stdout") or "").strip()
    if detail:
        detail = re.sub(r"\s+", " ", detail)[-3000:]
        return f"exit={result.get('exitCode')}: {detail}"
    return f"exit={result.get('exitCode')} (no deploy output)"




def _accepted_artifact_record(manifest: dict) -> dict:
    value = manifest.get("last_accepted_artifact")
    return value if isinstance(value, dict) else {}


def _task_initial_artifact(optimize_input_dir: Path) -> tuple[Optional[Path], dict]:
    """Return the immutable task-start Pack when it was fully published."""
    baseline_dir = optimize_input_dir / "baseline"
    artifact = baseline_dir / "artifact_v0.zip"
    manifest = _load_json(baseline_dir / "baseline-manifest.json", {})
    if not isinstance(manifest, dict) or manifest.get("publishStatus") != "SUCCESS":
        return None, {}
    if not manifest.get("artifactUploaded") or not manifest.get("manifestUploaded"):
        return None, {}
    if not artifact.is_file() or manifest.get("sha256") != _sha256(artifact):
        return None, {}
    published = manifest.get("publishedArtifact")
    return artifact, published if isinstance(published, dict) else {}


def _accepted_artifact_from_manifest(optimize_output_dir: Path, max_round: int) -> Optional[Path]:
    """Return the committed Accepted artifact; never infer it from Round files."""
    manifest = _load_json(optimize_output_dir / "optimize_manifest.json", {})
    if not isinstance(manifest, dict):
        return None
    last_round = manifest.get("last_accepted_round")
    artifact = _accepted_artifact_record(manifest).get("localPath")
    try:
        last_round_ok = last_round is not None and int(last_round) <= int(max_round)
    except (TypeError, ValueError):
        last_round_ok = False
    if artifact and last_round_ok and Path(artifact).is_file():
        return Path(artifact)
    return None


def _ensure_accepted_artifact_local(args, source_round: int, target: Path, expected: dict | None = None) -> Path:
    """Download an already registered accepted Pack when its local copy is unavailable."""
    target.parent.mkdir(parents=True, exist_ok=True)
    ticket = _artifact_client(args).download_accepted(source_round, target)
    artifact = ticket.get("artifact") or {}
    if expected:
        for key in ("ref", "size", "sha256", "contentType"):
            if expected.get(key) is not None and artifact.get(key) != expected.get(key):
                target.unlink(missing_ok=True)
                raise RuntimeError("accepted Pack and local manifest disagree")
    if not zipfile.is_zipfile(target):
        target.unlink(missing_ok=True)
        raise RuntimeError("downloaded accepted Pack is not a valid ZIP")
    return target

def _find_latest_existing_spec(optimize_output_dir: Path, optimize_input_dir: Path, max_version: int) -> Optional[Path]:
    """Find the newest spec-vK.md at or before max_version, falling back to spec-v0.md."""
    for version in range(int(max_version), 0, -1):
        candidate = optimize_output_dir / f"round-{version:03d}" / "spec" / f"spec-v{version}.md"
        if candidate.exists():
            return candidate
    for name in ("spec-v0.md", "spec_v0.md"):
        candidate = optimize_input_dir / name
        if candidate.exists():
            return candidate
    return None


def _find_last_accepted_round_state(optimize_output_dir: Path, max_version: int) -> Optional[tuple[int, dict]]:
    """Walk back from max_version to find the latest accepted round state."""
    for version in range(int(max_version), 0, -1):
        state_path = optimize_output_dir / f"round-{version:03d}" / "round_state.json"
        state = _load_json(state_path, {})
        if not isinstance(state, dict):
            continue
        if state.get("status") in {"ROUND_COMPLETED", "ROUND_COMPLETED_WITH_FALLBACK"} and state.get("accepted"):
            return version, state
    return None


def _check_previous_round_interrupted(args) -> dict:
    """Detect if previous round was left in ROUND_RUNNING (interrupted)."""
    paths = resolve_paths(args)
    round_id = args.round
    if round_id <= 1:
        return {"interrupted": False}
    prev_version = round_id - 1
    prev_state_path = Path(paths["optimize_output_dir"]) / f"round-{prev_version:03d}" / "round_state.json"
    prev_state = _load_json(prev_state_path, {})
    if not isinstance(prev_state, dict):
        return {"interrupted": False}
    status = str(prev_state.get("status") or "")
    if status in {"", "ROUND_STARTED", "ROUND_RUNNING"}:
        return {
            "interrupted": True,
            "prev_round": prev_version,
            "prev_status": status or "MISSING",
            "prev_state_path": str(prev_state_path),
        }
    return {"interrupted": False}



def _infer_resume_round(args, start_round: int) -> int | None:
    """Find the earliest incomplete round at or after start_round."""
    try:
        paths = resolve_paths(args)
        opt_dir = Path(paths["optimize_output_dir"])
    except Exception:
        return None
    if not opt_dir.exists():
        return None
    candidates: list[int] = []
    for state_path in sorted(opt_dir.glob("round-*/round_state.json")):
        try:
            round_name = state_path.parent.name
            round_id = int(round_name.split("-")[-1])
        except Exception:
            continue
        if round_id < int(start_round):
            continue
        state = _load_json(state_path, {})
        if not isinstance(state, dict):
            continue
        status = str(state.get("status") or "")
        if status in {"ROUND_STARTED", "ROUND_RUNNING"}:
            candidates.append(round_id)
    return min(candidates) if candidates else None


def _load_manifest(args) -> dict:
    try:
        paths = resolve_paths(args)
        manifest = _load_json(paths["run_dir"] / "optimize" / "output" / "optimize_manifest.json", {})
        return manifest if isinstance(manifest, dict) else {}
    except Exception:
        return {}


def _artifact_legacy_noise_paths(artifact_path: Path, limit: int = 20) -> list[str]:
    """List undeployable VCS/NFS entries in a zip artifact, capped."""
    path = Path(artifact_path)
    if not path.is_file() or not zipfile.is_zipfile(path):
        return []
    out = []
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                parts = Path(name).parts
                if ".git" in parts or any(part.startswith(".nfs") for part in parts):
                    out.append(name)
                    if len(out) >= limit:
                        break
    except (OSError, zipfile.BadZipFile):
        return []
    return out


def _deploy_supports_legacy_noise_filter(deploy_script: Path) -> bool:
    """Detect the deploy capability required for contaminated historical packs."""
    script = Path(deploy_script)
    candidates = [script, script.with_name("deploy.py")]
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "_ignore_deploy_noise" in text and "DEPLOY_NOISE_NAMES" in text:
            return True
    return False


def _assert_restore_runtime_compatible(artifact_path: Path, deploy_script: Path) -> dict:
    """Fail before workspace mutation when a legacy artifact needs newer Deploy."""
    noise = _artifact_legacy_noise_paths(Path(artifact_path))
    supported = _deploy_supports_legacy_noise_filter(Path(deploy_script))
    report = {
        "compatible": not noise or supported,
        "artifact": str(artifact_path),
        "deploy_script": str(deploy_script),
        "legacy_noise_detected": bool(noise),
        "legacy_noise_preview": noise,
        "deploy_supports_legacy_noise_filter": supported,
    }
    if not report["compatible"]:
        raise SystemExit(json.dumps({
            "code": "LEGACY_ARTIFACT_REQUIRES_UPDATED_DEPLOY",
            "message": "artifact contains nested .git/.nfs entries but installed clawevolve-deploy lacks the legacy-noise filter; publish/sync one coherent ClawEvolve release before retrying",
            **report,
        }, ensure_ascii=False))
    return report


def _restore_workspace_from_artifact(args, artifact_path: Path, reason: str) -> dict:
    """Run deploy.sh to restore workspace from an artifact zip."""
    paths = resolve_paths(args)
    skill_base = paths["skill_base"]
    script = find_script(skill_base, "clawevolve-deploy", "scripts/deploy.sh")
    compatibility = _assert_restore_runtime_compatible(artifact_path, script)
    cmd = [
        "bash", str(script),
        "--image", str(artifact_path),
        "--workspace", args.workspace or paths["workspace"],
        "--skip-evolve-results",
        "--force-overwrite",
        "--evolve-run-id", paths["task_id"],
    ]
    _log(args, "watchdog", f"{reason}: restoring from {artifact_path}")
    proc = subprocess.run(cmd, text=True, capture_output=True)
    result = {
        "restored": proc.returncode == 0,
        "restoreFrom": str(artifact_path),
        "exitCode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "reason": reason,
        "runtimeCompatibility": compatibility,
    }
    if proc.returncode != 0:
        _log_block(args, "watchdog", "RESTORE ERROR", proc.stdout + proc.stderr)
        raise SystemExit(json.dumps(result, ensure_ascii=False))
    (paths["round_dir"] / "watchdog_startup_restore.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _log(args, "watchdog", f"{reason}: restore done")
    return result


def _handle_startup_interrupt(args) -> None:
    """If the previous round was interrupted, restore its own pre-Tune snapshot."""
    paths = resolve_paths(args)
    check = _check_previous_round_interrupted(args)
    if not check.get("interrupted"):
        return
    _log(args, "watchdog", f"detected interrupted previous round: {check}", "WARN")

    prev_state = _load_json(Path(check["prev_state_path"]), {})
    snapshot = prev_state.get("rollback_snapshot") if isinstance(prev_state, dict) else {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    artifact = Path(str(snapshot.get("path") or ""))
    expected_sha = str(snapshot.get("sha256") or "")
    if snapshot.get("status") != "ready" or not artifact.is_file() or not expected_sha:
        raise SystemExit("applied_candidate_without_rollback_snapshot: interrupted previous Round has no ready rollback Pack")
    actual_sha = _sha256(artifact)
    if actual_sha != expected_sha:
        raise SystemExit(f"interrupted Round rollback snapshot digest mismatch: expected={expected_sha}, actual={actual_sha}")
    _restore_workspace_from_artifact(args, artifact, "interrupted round recovery")


def _check_disk_space(args, min_gb: float = 1.0) -> dict:
    """Check free disk space on the workspace filesystem."""
    paths = resolve_paths(args)
    try:
        stat = os.statvfs(paths["workspace"])
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
        result = {"free_gb": free_gb, "min_gb": min_gb, "ok": free_gb >= min_gb}
        if not result["ok"]:
            _log(args, "watchdog", f"disk space low: {free_gb:.1f}GB < {min_gb}GB", "ERROR")
        return result
    except Exception as exc:
        return {"ok": True, "error": str(exc)}


def _foreach_orphan_cleanup() -> int:
    """Aggressive variant of orphan cleanup that also attempts pkill for openclaw agents."""
    count = _cleanup_orphan_openclaw_agents()
    try:
        # Best-effort pkill by full command line pattern (not process name).
        subprocess.run(
            ["pkill", "-9", "-f", "openclaw.*agent"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        pass
    return count


# ═══════════════════════════════════════════════════════════════════════════════
# bench 执行 — local 模式
# ═══════════════════════════════════════════════════════════════════════════════

def action_bench_local(args, kind: str, *, template_dir_override: Path | None = None, step_name_override: str | None = None, state_key: str | None = None, mark_step: bool = True, print_result: bool = True):
    """本地模式 bench：直接调用 benchmark.py，结果元数据+summary 写入 round_state.json。

    template_dir_override/state_key support staged validation benches. The default
    path and state key preserve the original production behavior.
    """
    paths = resolve_paths(args)
    task_id = paths["task_id"]
    workspace = Path(paths["workspace"])
    clawbench_home = resolve_clawbench_home(args, paths)
    benchmark_py = clawbench_home / "scripts" / "benchmark.py"
    template_dir = Path(template_dir_override) if template_dir_override is not None else _local_template_dir(args, kind, paths)
    template_dir = template_dir.resolve() if template_dir.exists() else template_dir.absolute()

    step_name = step_name_override or ("bench-opt" if kind == "optimization" else "bench-val")
    preflight = _bench_preflight(args, kind, template_dir=template_dir, benchmark_py=benchmark_py)
    if not preflight.get("ok"):
        _log(args, step_name, f"FAILED: bench preflight failed: {json.dumps(preflight, ensure_ascii=False)}", level="ERROR")
        raise SystemExit("bench preflight failed")
    if not template_dir.is_dir():
        _log(args, step_name, f"FAILED: Local {kind} template dir not found: {template_dir}", level="ERROR")
        raise SystemExit(f"Local {kind} template dir not found: {template_dir}")
    task_files = sorted(template_dir.glob("task_*.md"))

    model = _bench_model(args)
    suite = args.suite or "all"
    kind_short = "opt" if kind == "optimization" else "val"
    scene = f"clawevolve-{kind}-{task_id}-round-{args.round}"
    bench_run_id = f"bench-{kind_short}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    bench_dir = workspace / "clawbench_results" / bench_run_id
    output_dir = bench_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "run_benchmark.log"

    _log(args, step_name, f"start: bench_run_id={bench_run_id}, model={model}, template={template_dir}, tasks={len(task_files)}")
    _log(args, step_name, f"output_dir={output_dir}")

    cmd = [
        sys.executable, str(benchmark_py),
        "--model", model,
        "--benchmark", str(template_dir),
        "--suite", suite,
        "--scene", scene,
        "--output-dir", str(output_dir),
        "--no-fail-fast",
    ]
    child_env = os.environ.copy()
    child_env.update({
        "CLAWBENCH_CALLBACK_ENABLED": "0",
        "CLAWWEB_URL": "",
        "CLAWBENCH_BENCH_RUN_ID": "",
        "BENCH_RUN_ID": bench_run_id,
        "AGENTBENCH_HOME": str(clawbench_home),
        "OUTPUT_DIR": str(output_dir),
        "BENCHMARK_DIR": str(template_dir),
        "MODEL": model,
        "SUITE": suite,
        "SCENE": scene,
        "CLAWEVOLVE_TASK_ID": str(task_id),
        "CLAWBENCH_OPENCLAW_EXECUTION_MODE": args.openclaw_execution_mode,
    })

    started_at = int(time.time())
    _log(args, step_name, f"cmd: {' '.join(cmd)}")

    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write("$ " + " ".join(cmd) + "\n")
        log_file.write(f"cwd={clawbench_home}\nbenchMode=local\nkind={kind}\n")
        log_file.write(f"templateDir={template_dir}\noutputDir={output_dir}\n")
        log_file.write(f"taskCount={len(task_files)}\n\n")
        log_file.flush()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(clawbench_home),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=child_env,
                check=False,
                timeout=getattr(args, "bench_timeout", 86400) or 86400,
            )
        except subprocess.TimeoutExpired:
            _log(args, step_name, f"TIMEOUT after {getattr(args, 'bench_timeout', 86400) or 86400}s", level="ERROR")
            _log_block(args, step_name, "ERROR LOG TAIL", _log_tail(log_path))
            raise
    completed_at = int(time.time())
    elapsed = completed_at - started_at

    result_path, workflow_result = _resolve_bench_result_artifacts(output_dir)
    if not result_path:
        _log(args, step_name, "WARNING: no benchmark report found in output_dir", level="WARN")
    status = "succeeded" if proc.returncode == 0 else "failed"
    err = "" if proc.returncode == 0 else f"Local ClawBench failed (exit {proc.returncode}). See {log_path}"

    # inline 计算 summary
    summary = _compute_bench_summary(Path(result_path)) if result_path else {}
    if not summary and workflow_result:
        summary = _summary_from_workflow_result(workflow_result)

    _log(args, step_name, f"done: exit={proc.returncode}, elapsed={elapsed}s, score={summary.get('score')}, status={status}")
    if proc.returncode != 0:
        _log_block(args, step_name, "ERROR LOG TAIL", _log_tail(log_path))

    # 直接写入 round_state.json，不再单独建 bench 目录/文件
    state_path = paths["round_dir"] / "round_state.json"
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    bench_record = {
        "benchRunId": bench_run_id,
        "benchDir": str(bench_dir),
        "outputDir": str(output_dir),
        "resultPath": result_path,
        "logPath": str(log_path),
        "status": status,
        "exitCode": proc.returncode,
        "startedAt": started_at,
        "completedAt": completed_at,
        "elapsedSeconds": elapsed,
        "summary": summary,
    }
    state.setdefault("bench", {})
    state["bench"][state_key or kind] = bench_record
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if mark_step:
        _mark_step(args, step_name, "SUCCESS", {"benchRunId": bench_run_id, "score": summary.get("score")})

    out = {
        "status": status,
        "benchRunId": bench_run_id,
        "benchDir": str(bench_dir),
        "outputDir": str(output_dir),
        "resultPath": result_path,
        "logPath": str(log_path),
        "exitCode": proc.returncode,
        "score": summary.get("score"),
        "summary": summary,
        "error": err,
    }
    if print_result:
        _print_json(out)

    if getattr(args, "strict_bench", False) and proc.returncode != 0:
        raise SystemExit(err)
    if proc.returncode != 0 and not result_path:
        raise SystemExit(err)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# action: prepare
# ═══════════════════════════════════════════════════════════════════════════════

def _prepare_openversion_source(args, workspace: Path) -> None:
    """Materialize the frozen primary Plan into a new local optimization Task.

    This only transports existing files. It does not rerun Plan, merge sources,
    change Bench Domains, or alter the optimization/acceptance algorithms.
    """
    if os.environ.get("CLAWWEB_VERSION") != "openversion" or int(args.round) != 1:
        return
    import urllib.request
    base = str(args.clawweb_url or "").rstrip("/")
    # COSEC: the local adapter owns the callback address; never fetch an arbitrary source URL.
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("Openversion requires a local ClawWeb URL")
    task_id, step_id = str(args.task_id), str(args.step_id)
    for value in (task_id, step_id):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value) or ".." in value:
            raise ValueError("Invalid local Task/Step")
    url = f"{base}/api/evolve/internal/tasks/{task_id}/steps/{step_id}/input"
    with urllib.request.urlopen(url, timeout=30) as response:
        frozen = json.load(response)
    if (frozen.get("task", {}).get("taskId") != task_id
        or frozen.get("step", {}).get("stepId") != step_id
        or frozen.get("target", {}).get("userId") != args.owner_id):
        raise ValueError("Frozen optimization Task mismatch")
    if frozen["task"].get("taskType") != "optimize":
        return  # Full and Bench Plan already wrote their own Task inputs.
    sources = (frozen.get("inputs") or {}).get("diagnoses") or []
    primary = [item for item in sources if item.get("role") == "primary"]
    if len(primary) != 1 or not primary[0].get("plan"):
        raise ValueError("Frozen primary Plan is missing")
    source_id = str(primary[0].get("taskId", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", source_id) or ".." in source_id or source_id == task_id:
        raise ValueError("Invalid primary Plan source Task")
    root = workspace / "clawevolve_results"
    if root.is_symlink():
        raise ValueError("Symlinked Bot results root")
    root = root.resolve()
    root.relative_to(workspace.resolve())
    source = root / source_id
    target = root / task_id
    # COSEC: source and destination must remain in this Bot's result root; reject
    # symlinks instead of following them into another Bot or credential directory.
    for path in (source, target):
        path.resolve().relative_to(root)
        if path.is_symlink():
            raise ValueError("Symlinked optimization Task directory")
    for suffix in ("plan/output", "optimize/input"):
        source_dir, target_dir = source / suffix, target / suffix
        if not source_dir.exists():
            continue
        files = list(source_dir.rglob("*"))
        for path in [source_dir, *files]:
            if path.is_symlink() or path.resolve() != path.absolute():
                raise ValueError("Symlinked Plan input")
            path.resolve().relative_to(root)
        for path in files:
            if not path.is_file():
                continue
            destination = target_dir / path.relative_to(source_dir)
            destination.resolve().relative_to(root)
            if destination.is_symlink() or destination.resolve() != destination.absolute():
                raise ValueError("Symlinked optimization input")
            if not destination.exists():  # Retry must not overwrite round state or edited input.
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)


def action_prepare(args):
    workspace = _resolve_workspace(args)
    _prepare_openversion_source(args, workspace)
    skill_base = str(_resolve_skill_base(args))
    task_id = args.task_id or _env("TASK_ID") or _env("EVOLVE_RUN_ID")
    evolve_run_id = task_id
    if not task_id:
        raise SystemExit("task_id is required")

    round_id = args.round
    if round_id < 1:
        raise SystemExit("round is 1-based; round must be >= 1")

    run_dir = workspace / "clawevolve_results" / evolve_run_id
    diagnose_dir = run_dir / "diagnose"
    plan_dir = run_dir / "plan"
    plan_output_dir = plan_dir / "output"
    optimize_dir = run_dir / "optimize"
    optimize_input_dir = optimize_dir / "input"
    optimize_output_dir = optimize_dir / "output"

    for d in [diagnose_dir / "input", diagnose_dir / "output", plan_dir / "input",
              plan_output_dir, optimize_input_dir, optimize_output_dir]:
        d.mkdir(parents=True, exist_ok=True)

    round_name = f"round-{round_id:03d}"
    round_dir = optimize_output_dir / round_name
    input_dir = round_dir / "input"
    tune_dir = round_dir / "tune"
    spec_dir = round_dir / "spec"
    acceptance_dir = round_dir / "acceptance"
    artifacts_dir = round_dir / "artifacts"
    rollback_dir = round_dir / "rollback"
    upload_dir = round_dir / "upload"

    for d in [input_dir, tune_dir, spec_dir, acceptance_dir, artifacts_dir, rollback_dir, upload_dir]:
        d.mkdir(parents=True, exist_ok=True)

    initial_spec = _first_existing([
        optimize_input_dir / "spec-v0.md",
        optimize_input_dir / "spec_v0.md",
        plan_output_dir / "spec-v0.md",
        plan_output_dir / "spec_v0.md",
    ])
    if initial_spec and not (optimize_input_dir / "spec-v0.md").exists():
        shutil.copyfile(initial_spec, optimize_input_dir / "spec-v0.md")
    initial_spec_json = _first_existing([
        optimize_input_dir / "spec-v0.json", optimize_input_dir / "spec_v0.json",
        plan_output_dir / "spec-v0.json", plan_output_dir / "spec_v0.json",
    ])
    if initial_spec_json and not (optimize_input_dir / "spec-v0.json").exists():
        shutil.copyfile(initial_spec_json, optimize_input_dir / "spec-v0.json")

    initial_objective = _first_existing([
        optimize_input_dir / "objective.md",
        plan_output_dir / "objective.md",
    ])
    if initial_objective and not (optimize_input_dir / "objective.md").exists():
        shutil.copyfile(initial_objective, optimize_input_dir / "objective.md")
    initial_objective_json = _first_existing([
        optimize_input_dir / "objective.json",
        plan_output_dir / "objective.json",
    ])
    if initial_objective_json and not (optimize_input_dir / "objective.json").exists():
        shutil.copyfile(initial_objective_json, optimize_input_dir / "objective.json")
    objective_md = optimize_input_dir / "objective.md"
    objective_json = optimize_input_dir / "objective.json"
    missing = []
    for path in [objective_md, objective_json, Path(skill_base)]:
        if not path.exists():
            missing.append(str(path))

    input_spec_version = round_id - 1
    output_spec_version = round_id
    if round_id == 1:
        input_spec = optimize_input_dir / "spec-v0.md"
    else:
        prev_round_dir = optimize_output_dir / f"round-{round_id - 1:03d}"
        input_spec = prev_round_dir / "spec" / f"spec-v{round_id - 1}.md"
        if not input_spec.exists():
            fallback_spec = _find_latest_existing_spec(optimize_output_dir, optimize_input_dir, round_id - 1)
            if fallback_spec:
                _log(args, "prepare", f"input spec missing: {input_spec}; fallback to {fallback_spec}", "WARN")
                input_spec = fallback_spec
    if not input_spec.exists():
        missing.append(str(input_spec))
    if missing:
        _log(args, "prepare", f"FAILED: missing required local inputs: {'; '.join(missing)}", level="ERROR")
        raise SystemExit("missing required local inputs: " + "; ".join(missing))

    round_input_spec = input_dir / f"spec-v{input_spec_version}.md"
    shutil.copyfile(input_spec, round_input_spec)
    source_spec_json = input_spec.with_suffix(".json")
    round_input_spec_json = input_dir / f"spec-v{input_spec_version}.json"
    if source_spec_json.is_file():
        shutil.copyfile(source_spec_json, round_input_spec_json)
    else:
        normalized_legacy = _normalize_legacy_spec_markdown_to_v1(_read_text(input_spec), f"v{input_spec_version}")
        round_input_spec_json.write_text(json.dumps(normalized_legacy, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    baseline_artifact = ""
    baseline_artifact_info = {}
    if round_id > 1:
        run_manifest = _load_json(optimize_output_dir / "optimize_manifest.json", {})
        accepted_round = int(run_manifest.get("last_accepted_round") or 0) if isinstance(run_manifest, dict) else 0
        accepted_record = _accepted_artifact_record(run_manifest)
        baseline_artifact_path = _accepted_artifact_from_manifest(optimize_output_dir, round_id - 1)
        if baseline_artifact_path:
            baseline_artifact = str(baseline_artifact_path)
            baseline_artifact_info = accepted_record
        elif accepted_round > 0 and accepted_round < round_id:
            _log(args, "prepare", f"local accepted Pack missing; downloading Round {accepted_round} from ClawWeb", "WARN")
            baseline_artifact_path = optimize_output_dir / f"round-{accepted_round:03d}" / "artifacts" / f"artifact_v{accepted_round}.zip"
            baseline_artifact_path = _ensure_accepted_artifact_local(
                args, accepted_round, baseline_artifact_path, accepted_record
            )
            baseline_artifact = str(baseline_artifact_path)
            baseline_artifact_info = accepted_record
        else:
            initial_path, initial_record = _task_initial_artifact(optimize_input_dir)
            if not initial_path:
                raise SystemExit("effective baseline artifact for evaluation not found in committed manifest or task initial artifact")
            baseline_artifact = str(initial_path)
            baseline_artifact_info = initial_record
            _log(args, "prepare", f"no accepted Round Pack yet; using task-start baseline {initial_path}", "WARN")

    state = {
        "schema_version": "evolution.round_state.v1",
        "task_id": task_id,
        "step_id": args.step_id or _env("STEP_ID"),
        "evolve_run_id": task_id,
        "round_id": round_id,
        "round_name": round_name,
        "status": "ROUND_STARTED",
        "workspace": str(workspace),
        "run_dir": str(run_dir),
        "optimize_input_dir": str(optimize_input_dir),
        "optimize_output_dir": str(optimize_output_dir),
        "round_dir": str(round_dir),
        "skill_base_dir": skill_base,
        "objective_md": str(objective_md),
        "objective_json": str(objective_json) if objective_json.exists() else "",
        "input_spec_version": f"v{input_spec_version}",
        "output_spec_version": f"v{output_spec_version}",
        "input_spec": str(round_input_spec),
        "input_spec_json": str(round_input_spec_json),
        "input_spec_contract": _load_json(round_input_spec_json, {}),
        "source_input_spec": str(input_spec),
        "baseline_artifact": baseline_artifact,
        "baselineArtifact": {"status": "available", "artifact": baseline_artifact_info} if baseline_artifact_info else {},
        "started_at": _now(),
    }
    state["identity"] = _record_round_identity(args, {
        "workspace": workspace,
        "run_dir": run_dir,
        "plan_output_dir": plan_output_dir,
        "optimize_input_dir": optimize_input_dir,
        "optimize_output_dir": optimize_output_dir,
        "round_dir": round_dir,
    }, state)
    (round_dir / "round_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _mark_step(args, "prepare", "SUCCESS")
    _log(args, "prepare", f"round_dir={round_dir}, spec=v{input_spec_version}, baseline={baseline_artifact or 'none'}")
    _print_json(state)


# ═══════════════════════════════════════════════════════════════════════════════
# action: bench-opt / bench-val
# ═══════════════════════════════════════════════════════════════════════════════

def _write_bench_skip(paths: dict, state_key: str, *, skip_type: str, reason: str) -> dict:
    state_path = paths["round_dir"] / "round_state.json"
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    record = {
        "status": "skipped", "skipType": skip_type, "skipReason": reason, "resultPath": "",
        "summary": {"score": None, "pass_rate": None, "total": 0, "passed": 0, "failed": 0, "errors": 0},
        "updated_at": _now(),
    }
    state.setdefault("bench", {})[state_key] = record
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def action_load_baseline_opt(args):
    """Ensure scored Train/Test baselines exist before Tune, reusing valid caches."""
    paths = resolve_paths(args)
    manifest_path = paths["optimize_output_dir"] / "optimize_manifest.json"
    manifest = _load_json(manifest_path, {})
    state_path = paths["round_dir"] / "round_state.json"
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    manifest = dict(manifest or {})
    expected_round = 0 if args.round == 1 else manifest.get("last_accepted_round")
    artifact_path = _baseline_artifact_path(args, paths, manifest, state)
    if artifact_path is None:
        raise SystemExit(f"baseline artifact is required before baseline evaluation for Round {args.round}")

    optimization = _baseline_opt_registry(manifest)
    if args.round == 1:
        if (
            optimization.get("round_id") in (None, 0)
            and _round_one_baseline_cache_matches(
                args, paths, kind="optimization", registry=optimization, artifact_path=artifact_path,
            )
        ):
            optimization_status = "registry_hit"
        else:
            optimization = _bootstrap_optimization_baseline(args, paths)
            optimization_status = "bootstrap_hit" if _round_one_baseline_cache_matches(
                args, paths, kind="optimization", registry=optimization, artifact_path=artifact_path,
            ) else "miss"
    else:
        desired = _evaluation_identity(
            args, paths, kind="optimization", artifact_path=artifact_path,
            report_path=optimization.get("result_path", ""),
        )
        cached_identity = optimization.get("identity") if isinstance(optimization.get("identity"), dict) else {}
        optimization_status = "registry_hit" if (
            _baseline_registry_usable(optimization)
            and optimization.get("round_id") == expected_round
            and _evaluation_identity_matches_frozen_task(cached_identity, desired)
        ) else "miss"
    if optimization_status == "miss":
        optimization = _run_and_register_baseline_role(
            args, paths, kind="optimization", artifact_path=artifact_path, round_id=expected_round,
        )
        optimization_status = "generated"

    validation = _baseline_validation_registry(manifest)
    if args.round == 1:
        if (
            validation.get("round_id") in (None, 0)
            and _round_one_baseline_cache_matches(
                args, paths, kind="validation", registry=validation, artifact_path=artifact_path,
            )
        ):
            validation_status = "registry_hit"
        else:
            bootstrap_validation = _bootstrap_validation_baseline(paths)
            if bootstrap_validation.get("available"):
                validation = {
                    "schema_version": "evolution.baseline_validation.v1",
                    "available": True,
                    "source": bootstrap_validation.get("source"),
                    "round_id": 0,
                    "artifact_path": str(artifact_path),
                    "artifact_sha256": _sha256(artifact_path),
                    "result_path": bootstrap_validation.get("validation_result_path") or "",
                    "summary": {"score": bootstrap_validation.get("validation_score")},
                    "task_scores": bootstrap_validation.get("validation_task_scores") or {},
                    "identity": bootstrap_validation.get("identity") or {},
                    "bench_run_id": bootstrap_validation.get("bench_run_id") or "",
                    "producer_step_id": bootstrap_validation.get("producer_step_id") or "",
                    "domain_id": bootstrap_validation.get("domain_id") or "",
                    "domain_owner_id": bootstrap_validation.get("domain_owner_id") or "",
                    "registered_at": _now(),
                }
                validation_status = "bootstrap_hit" if _round_one_baseline_cache_matches(
                    args, paths, kind="validation", registry=validation, artifact_path=artifact_path,
                ) else "miss"
            else:
                validation_status = "miss"
    else:
        desired = _evaluation_identity(
            args, paths, kind="validation", artifact_path=artifact_path,
            report_path=validation.get("result_path", ""),
        )
        cached_identity = validation.get("identity") if isinstance(validation.get("identity"), dict) else {}
        validation_status = "registry_hit" if (
            _baseline_registry_usable(validation)
            and validation.get("round_id") == expected_round
            and _evaluation_identity_matches_frozen_task(cached_identity, desired)
        ) else "miss"
    if validation_status == "miss":
        validation = _run_and_register_baseline_role(
            args, paths, kind="validation", artifact_path=artifact_path, round_id=expected_round,
        )
        validation_status = "generated"

    if not _baseline_registry_usable(optimization) or not _baseline_registry_usable(validation):
        raise SystemExit(
            "Train/Test baseline ensure incomplete: "
            f"train_available={_baseline_registry_usable(optimization)}, "
            f"test_available={_baseline_registry_usable(validation)}"
        )

    manifest.setdefault("schema_version", "evolution.run_manifest.v0")
    manifest.setdefault("task_id", paths["task_id"])
    manifest.setdefault("evolve_run_id", paths["task_id"])
    manifest.setdefault("rounds", [])
    manifest["accepted_baseline_optimization"] = optimization
    manifest["accepted_baseline_validation"] = validation
    manifest["last_accepted_validation_score"] = (validation.get("summary") or {}).get("score")
    manifest["last_accepted_validation_result_path"] = validation.get("result_path") or ""
    manifest["last_accepted_validation_task_scores"] = validation.get("task_scores") or {}
    manifest["last_accepted_identity"] = validation.get("identity") or {}
    manifest["last_accepted_identity_complete"] = _eval_identity_complete(validation.get("identity") or {})
    if manifest.get("last_accepted_round") is None and expected_round not in (None, 0):
        manifest["last_accepted_round"] = expected_round
    manifest["baseline_registry"] = {
        "schema_version": "evolution.baseline_registry.v1",
        "round_id": expected_round,
        "artifact": {
            "localPath": str(artifact_path),
            "sha256": _sha256(artifact_path),
        },
        "optimization": optimization,
        "validation": validation,
        "registered_at": _now(),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    state["baseline_optimization"] = optimization
    state["baseline_validation"] = validation
    state.setdefault("bench", {})["baseline_optimization"] = {
        "status": "cached", "cacheStatus": optimization_status, "resultPath": optimization.get("result_path", ""),
        "summary": optimization.get("summary") or {}, "identity": optimization.get("identity") or {},
        "sourceRound": optimization.get("round_id"), "loadedAt": _now(),
    }
    state.setdefault("bench", {})["baseline_validation"] = {
        "status": "cached", "cacheStatus": validation_status, "resultPath": validation.get("result_path", ""),
        "summary": validation.get("summary") or {}, "identity": validation.get("identity") or {},
        "sourceRound": validation.get("round_id"), "loadedAt": _now(),
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _mark_step(args, "load-baseline-opt", "SUCCESS", {
        "trainCacheStatus": optimization_status,
        "testCacheStatus": validation_status,
        "trainScore": (optimization.get("summary") or {}).get("score"),
        "testScore": (validation.get("summary") or {}).get("score"),
    })
    _print_json({
        "status": "succeeded",
        "cacheStatus": {"train": optimization_status, "test": validation_status},
        "baselineOptimization": optimization,
        "baselineValidation": validation,
    })
    return optimization


def action_candidate_static_gate(args):
    paths = resolve_paths(args)
    change_summary = _write_round_change_summary(args)
    gate = _candidate_prevalidation_gate(args, paths, change_summary)
    enforcement_enabled = _candidate_gate_enforced(args)
    gate["enforcement"] = {
        "enabled": enforcement_enabled,
        "would_block": bool(not gate.get("valid") and not gate.get("is_noop")),
        "bypassed": bool(not enforcement_enabled and not gate.get("valid") and not gate.get("is_noop")),
        "restore": "set CLAWEVOLVE_ENFORCE_CANDIDATE_GATE=1 or pass --enforce-candidate-gate",
    }
    if gate["enforcement"]["bypassed"]:
        _log(args, "candidate-static-gate", f"advisory only; continuing despite: {'; '.join(gate.get('reasons') or ['candidate gate failed'])}", "WARN")
    state_path = paths["round_dir"] / "round_state.json"
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    state["change_summary"] = change_summary
    state["candidate_gate"] = gate
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path = paths["tune_dir"] / "candidate_static_gate.json"
    report_path.write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (paths["tune_dir"] / "candidate_gate.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _mark_step(args, "candidate-static-gate", "SUCCESS", {"valid": gate.get("valid"), "reasons": gate.get("reasons") or []})
    _print_json(gate)
    return gate


def _metric_is_unit_bounded(metric: str) -> bool:
    metric = str(metric or "")
    return metric == "score" or metric.startswith(("automated.", "llm_judge.")) or metric in {
        "output_contract_complete", "risk_list_present", "confirm_gate_complete",
    }


def _compile_signal_baselines(baseline_report: Path, expected: list[dict], protected: list[dict]) -> tuple[list[str], list[str]]:
    """Bind manifest signals to authoritative baseline evidence before bench cost."""
    errors, warnings = [], []
    if not baseline_report.is_file():
        return errors, [f"baseline report unavailable for signal preflight: {baseline_report}"]
    for group_name, signals in (("expected", expected), ("protected", protected)):
        for index, item in enumerate(signals):
            tid = str(item.get("task_id") or "").strip()
            metric = str(item.get("metric") or "score").strip()
            if not tid or not metric:
                continue
            observed = _extract_candidate_metric(baseline_report, tid, metric)
            gate = str(item.get("gate") or ("hard" if item.get("behavior_id") else "supporting")).lower()
            role = str(item.get("role") or "required").lower()
            blocks = (group_name == "expected" and role != "supporting") or (group_name == "protected" and gate == "hard")
            if not observed.get("observable"):
                message = f"{group_name} signal baseline metric is not observable: task={tid} metric={metric} error={observed.get('error')}"
                (errors if blocks else warnings).append(message)
                continue
            authoritative = float(observed["value"])
            declared = item.get("baseline")
            if declared is not None and not _approximately_equal(declared, authoritative):
                warnings.append(
                    f"{group_name} signal baseline corrected from {declared} to authoritative {authoritative}: task={tid} metric={metric}"
                )
            item["baseline"] = authoritative
            item["baseline_source"] = "runner_authoritative_report"
            if group_name != "expected" or role == "supporting":
                continue
            direction = str(item.get("direction") or "increase").lower()
            if direction == "boolean_flip":
                expected_value = _finite_number(item.get("expected_value"))
                expected_value = 1.0 if expected_value is None else expected_value
                if _approximately_equal(authoritative, expected_value):
                    errors.append(f"required boolean_flip signal baseline already equals expected_value: task={tid} metric={metric} value={authoritative}")
                continue
            raw_delta = _finite_number(item.get("min_delta"))
            min_delta = raw_delta if raw_delta is not None and raw_delta > 0 else (DEFAULT_EXPECTED_MIN_DELTA if metric == "score" else 1.0)
            threshold = _finite_number(item.get("expected_min"))
            target = authoritative + min_delta if direction == "increase" else authoritative - min_delta
            if threshold is not None:
                target = max(target, threshold) if direction == "increase" else min(target, threshold)
            if _metric_is_unit_bounded(metric) and (target > 1.0 + 1e-9 or target < -1e-9):
                errors.append(
                    f"required signal target is outside bounded metric range [0,1]: task={tid} metric={metric} baseline={authoritative} target={target}"
                )
    return errors, warnings


def _candidate_opt_signal_plan(args, paths: dict, state: dict) -> dict:
    manifest = state.get("change_manifest") if isinstance(state.get("change_manifest"), dict) else _load_change_manifest(paths)
    baseline = state.get("baseline_optimization") if isinstance(state.get("baseline_optimization"), dict) else {}
    baseline_scores = baseline.get("task_scores") if isinstance(baseline.get("task_scores"), dict) else {}
    template_ids = []
    if getattr(args, "bench_mode", "local") == "local":
        template_ids = _list_bench_template_task_ids(_local_template_dir(args, "optimization", paths))
    available = set(template_ids or baseline_scores.keys())
    expected = [dict(x) for x in (manifest.get("expected_signals") or []) if isinstance(x, dict)]
    protected = [dict(x) for x in (manifest.get("protected_signals") or []) if isinstance(x, dict)]
    invalid = []
    spec_contract = state.get("input_spec_contract") if isinstance(state.get("input_spec_contract"), dict) else {}
    binding_report = _protected_signal_binding_report(spec_contract, protected, available)
    invalid.extend(binding_report.get("errors") or [])
    plan_warnings = list(manifest.get("_normalization_warnings") or [])
    for item in expected:
        tid = str(item.get("task_id") or "").strip()
        if tid and available and tid not in available:
            invalid.append(f"expected signal references unknown optimization task: {tid}")
    executable_protected = []
    for item in protected:
        tid = str(item.get("task_id") or "").strip()
        if tid and available and tid not in available:
            if item.get("behavior_id"):
                invalid.append(f"protected behavior binding references unknown optimization task: {tid}")
            else:
                plan_warnings.append(f"ignored extra protected signal for unknown optimization task: {tid}")
            continue
        executable_protected.append(item)
    protected = executable_protected
    for item in protected:
        item["gate"] = str(item.get("gate") or ("hard" if item.get("behavior_id") else "supporting")).lower()
        if item["gate"] not in {"hard", "budget", "supporting"}:
            invalid.append(f"protected signal gate is invalid: {item['gate']}")
    selected = []
    for item in expected + protected:
        tid = str(item.get("task_id") or "").strip()
        if tid and tid in available and tid not in selected:
            selected.append(tid)
    # Runner adds a protected canary even when Tune proposes only favorable tasks.
    canary = None
    remaining = [(tid, score) for tid, score in baseline_scores.items() if tid in available and tid not in selected and score is not None]
    if remaining:
        canary = max(remaining, key=lambda pair: pair[1])[0]
        selected.append(canary)
        protected.append({"task_id": canary, "metric": "score", "baseline": baseline_scores.get(canary), "max_drop": DEFAULT_PROTECTED_MAX_DROP, "source": "runner_canary", "gate": "budget"})
    if not selected:
        ranked = sorted(((tid, score) for tid, score in baseline_scores.items() if tid in available and score is not None), key=lambda pair: pair[1])
        selected = [tid for tid, _ in ranked[:2]]
        if ranked:
            high = ranked[-1][0]
            if high not in selected:
                selected.append(high)
                protected.append({"task_id": high, "metric": "score", "baseline": baseline_scores.get(high), "max_drop": DEFAULT_PROTECTED_MAX_DROP, "source": "runner_canary", "gate": "budget"})
    if not selected and template_ids:
        selected = template_ids[: min(3, len(template_ids))]
    required_expected, supporting_expected = [], []
    for item in expected:
        role = str(item.get("role") or "required").strip().lower()
        if role not in {"required", "supporting"}:
            invalid.append(f"expected signal role is invalid: {role}")
            role = "required"
        item["role"] = role
        (supporting_expected if role == "supporting" else required_expected).append(item)
    baseline_report = Path(str(baseline.get("result_path") or ""))
    baseline_errors, baseline_warnings = _compile_signal_baselines(baseline_report, expected, protected)
    invalid.extend(baseline_errors)
    plan_warnings.extend(baseline_warnings)
    if not required_expected:
        invalid.append("candidate signal plan requires at least one required expected signal")
    canonical_payload = {
        "expected_signals": expected,
        "protected_signals": protected,
        "selected_task_ids": selected,
        "runner_added_canary": canary,
    }
    plan_id = hashlib.sha256(json.dumps(canonical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "schema_version": "evolution.candidate_opt_signal_plan.v2", "plan_id": plan_id,
        "valid": not invalid, "compile_status": "valid" if not invalid else "invalid", "errors": list(dict.fromkeys(invalid)),
        "expected_signals": expected, "required_expected_signals": required_expected, "supporting_expected_signals": supporting_expected,
        "protected_signals": protected, "selected_task_ids": selected,
        "runner_added_canary": canary, "available_task_count": len(available), "warnings": list(dict.fromkeys(plan_warnings)), "created_at": _now(),
    }


def _persist_candidate_opt_signal_plan(state_path: Path, plan_path: Path, plan: dict) -> dict:
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    state["candidate_opt_signal_plan"] = plan
    state["candidate_opt_signal_plan_status"] = str(plan.get("compile_status") or ("valid" if plan.get("valid") else "invalid"))
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def _targeted_eval_not_run_report(reason: str, *, plan: dict | None = None, decision: str = "targeted_eval_not_run") -> dict:
    return {
        "schema_version": "evolution.candidate_opt_gate.v4",
        "valid": False,
        "evaluation_status": "not_run",
        "decision": decision,
        "effect_gate_passed": None,
        "protected_gate_passed": None,
        "behavior_changed": None,
        "expected_results": [],
        "protected_results": [],
        "signal_plan_id": (plan or {}).get("plan_id"),
        "reasons": [reason],
        "created_at": _now(),
    }


def action_bench_candidate_opt_targeted(args):
    paths = resolve_paths(args)
    state_path = paths["round_dir"] / "round_state.json"
    state = _load_json(state_path, {})
    gate = state.get("candidate_gate") if isinstance(state, dict) else {}
    if not _candidate_gate_allows_progress(args, gate):
        reason = "; ".join(gate.get("reasons") or ["candidate static gate failed"])
        _write_bench_skip(paths, "candidate_optimization_targeted", skip_type="invalid_candidate", reason=reason)
        _mark_step(args, "bench-targeted-opt", "SKIPPED", {"reason": reason})
        return {"status": "skipped", "reason": reason}
    plan = _candidate_opt_signal_plan(args, paths, state)
    state = _persist_candidate_opt_signal_plan(state_path, paths["tune_dir"] / "candidate_opt_signal_plan.json", plan)
    if not plan.get("valid"):
        reason = "; ".join(plan.get("errors") or ["invalid candidate opt selector"])
        _write_bench_skip(paths, "candidate_optimization_targeted", skip_type="invalid_selector", reason=reason)
        _mark_step(args, "bench-targeted-opt", "SKIPPED", {"reason": reason})
        return {"status": "skipped", "reason": reason}
    if getattr(args, "bench_mode", "local") == "local" and plan.get("selected_task_ids"):
        source = _local_template_dir(args, "optimization", paths)
        subset = paths["round_dir"] / "bench_candidate_opt" / "targeted"
        _prepare_bench_subset_dir(source, plan["selected_task_ids"], subset)
        out = action_bench_local(args, "optimization", template_dir_override=subset, step_name_override="bench-targeted-opt", state_key="candidate_optimization_targeted", mark_step=False, print_result=False)
    elif getattr(args, "bench_mode", "local") == "local":
        out = action_bench_local(args, "optimization", step_name_override="bench-targeted-opt", state_key="candidate_optimization_targeted", mark_step=False, print_result=False)
    else:
        # Remote adapter currently cannot request a task subset; run the full train domain and mark it explicit.
        out = _adapter_bench(args, "optimization", step_name_override="bench-targeted-opt", state_key="candidate_optimization_targeted", mark_step=False, print_result=False)
        plan["adapter_fallback_full_suite"] = True
    state = _load_json(state_path, {})
    state["candidate_opt_signal_plan"] = plan
    record = ((state.get("bench") or {}).get("candidate_optimization_targeted") or {})
    record["evaluation_identity"] = _evaluation_identity(args, paths, kind="optimization", report_path=record.get("resultPath"))
    record["signalPlanId"] = plan.get("plan_id")
    state["bench"]["candidate_optimization_targeted"] = record
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _mark_step(args, "bench-targeted-opt", "SUCCESS", {"score": (record.get("summary") or {}).get("score"), "taskIds": plan.get("selected_task_ids")})
    _print_json(out)
    return out


def _task_from_report(report: dict, task_id: str) -> dict:
    for task in report.get("tasks") or []:
        tid = str(task.get("task_id") or task.get("id") or task.get("name") or "").strip()
        if tid == task_id:
            return task if isinstance(task, dict) else {}
    return {}


def _metric_number(value):
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _breakdown_metric(breakdown: dict, key: str):
    # Keys in ClawBench may themselves contain dots; exact match has priority.
    if key in breakdown:
        return breakdown[key], key
    current = breakdown
    parts = key.split(".")
    traversed = []
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            current = None
            break
        current = current[part]
        traversed.append(part)
    if current is not None:
        return current, ".".join(traversed)
    for nested_key, value in breakdown.items():
        if isinstance(value, dict):
            found, source = _breakdown_metric(value, key)
            if source:
                return found, f"{nested_key}.{source}"
    return None, ""


def _candidate_transcript_path(report_path: Path, task_id: str) -> Path | None:
    roots = [report_path.parent, report_path.parent.parent]
    for root in roots:
        for directory in sorted(root.glob("*_transcripts")) if root.is_dir() else []:
            for suffix in (".jsonl", ".json", ".txt"):
                candidate = directory / f"{task_id}{suffix}"
                if candidate.is_file():
                    return candidate
    return None


def _derived_metric_from_text(metric: str, text: str):
    if metric == "stage_completion":
        matches = list(re.finditer(r"(?:^|\n)\s*(?:[-*]\s*)?Stage\s*([0-9]+)\s*[:=\-]\s*(?:completed|complete|done|passed|已完成|完成|通过)\b|\"stage\"\s*:\s*([0-9]+)\s*,\s*\"status\"\s*:\s*\"(?:completed|passed)\"", text, re.I))
        values = [int(match.group(1) or match.group(2)) for match in matches]
        best = max(values) if values else None
        span = next((match.group(0) for match in matches if int(match.group(1) or match.group(2)) == best), "") if best is not None else ""
        return best, {"matched_span": span, "detail": "explicit Stage completion marker"}
    patterns = {
        "output_contract_complete": r"output[_ -]?contract.{0,40}(?:complete|passed|satisfied)|输出契约.{0,20}(?:完整|通过|满足)",
        "risk_list_present": r"risk[_ -]?list.{0,20}(?:present|emitted|complete)|风险(?:清单|列表).{0,20}(?:已生成|完整|存在)|\"risks\"\s*:",
        "confirm_gate_complete": r"confirm[_ -]?gate.{0,30}(?:complete|passed)|确认门.{0,20}(?:完成|通过)",
    }
    if metric in patterns:
        match = re.search(patterns[metric], text, re.I)
        return (1.0 if match else None), {"matched_span": match.group(0) if match else "", "detail": f"{metric} explicit marker"}
    if metric == "failure_signature_count":
        matches = list(re.finditer(r"failure[_ -]?signature|失败签名", text, re.I))
        return float(len(matches)), {"matched_span": matches[0].group(0) if matches else "", "detail": "failure signature marker count"}
    if metric == "first_artifact_stage":
        match = re.search(r"(?:first|首个).{0,20}(?:artifact|产物).{0,20}Stage\s*([0-9]+).{0,20}(?:completed|完成)", text, re.I)
        return (float(match.group(1)) if match else None), {"matched_span": match.group(0) if match else "", "detail": "first artifact completion marker"}
    return None, {"matched_span": "", "detail": ""}


BREAKDOWN_METRIC_ALIASES = {
    # The same judge rubric has appeared with and without the evidence suffix in
    # otherwise compatible ClawBench reports. Treat these as one semantic metric.
    "llm_judge.风险识别准确度（含原文证据）": ["llm_judge.风险识别准确度"],
    "llm_judge.风险识别准确度": ["llm_judge.风险识别准确度（含原文证据）"],
    # Abstract Spec behavior metrics must resolve to structured benchmark fields
    # when available; transcript regex remains only a last-resort fallback.
    "output_contract_complete": ["output_contract.complete", "llm_judge.输出格式合规"],
    "risk_list_present": ["risk_list.present", "automated.has_risk_list"],
    "confirm_gate_complete": ["confirm_gate.complete", "automated.confirm_present", "llm_judge.确认动作"],
}


def _breakdown_metric_candidates(metric: str) -> list[str]:
    requested = metric[len("breakdown."):] if metric.startswith("breakdown.") else metric
    return list(dict.fromkeys([requested, *(BREAKDOWN_METRIC_ALIASES.get(requested) or [])]))


def _extract_candidate_metric(report_path: Path, task_id: str, metric: str, *, transcript_root: Path | None = None) -> dict:
    report_path = Path(report_path)
    report = _load_json(report_path, {}) if report_path.is_file() else {}
    task = _task_from_report(report, task_id)
    if not task:
        return {"observable": False, "value": None, "source": "report", "confidence": "none", "error": "task_not_found", "evidence": {"task_id": task_id}}
    grading = task.get("grading") if isinstance(task.get("grading"), dict) else {}
    runs = grading.get("runs") if isinstance(grading.get("runs"), list) else []
    run = runs[0] if runs and isinstance(runs[0], dict) else {}
    if metric == "score":
        value = run.get("score", grading.get("mean"))
        number = _metric_number(value)
        return {"observable": number is not None, "value": number, "source": "score", "confidence": "high", "structured": True, "error": "" if number is not None else "score_missing", "evidence": {"raw": value}}
    breakdown = run.get("breakdown") if isinstance(run.get("breakdown"), dict) else {}
    requested_key = metric[len("breakdown."):] if metric.startswith("breakdown.") else metric
    # Benchmark breakdown keys are commonly emitted as fully-qualified flat names.
    # Resolve exact keys first, then a small audited semantic-alias set for rubric
    # labels that drifted across scorer versions.
    raw, source_key = None, ""
    matched_request = requested_key
    for candidate_key in _breakdown_metric_candidates(metric):
        raw, source_key = _breakdown_metric(breakdown, candidate_key)
        if source_key:
            matched_request = candidate_key
            break
    number = _metric_number(raw)
    if source_key:
        return {"observable": number is not None, "value": number, "source": "breakdown", "confidence": "high" if number is not None else "none", "structured": True, "error": "" if number is not None else "unobservable_metric", "evidence": {"requested_key": requested_key, "matched_request": matched_request, "matched_key": source_key, "raw": raw}}
    if metric.startswith("breakdown."):
        return {"observable": False, "value": None, "source": "breakdown", "confidence": "none", "structured": True, "error": "unobservable_metric", "evidence": {"requested_key": requested_key, "matched_key": source_key, "raw": raw}}
    aliases = {
        "stage_completion": ["stage_completion", "stage.completed", "completed_stage"],
        "output_contract_complete": ["output_contract_complete"],
        "risk_list_present": ["risk_list_present"],
        "confirm_gate_complete": ["confirm_gate_complete"],
        "failure_signature_count": ["failure_signature_count", "failure.signature_count"],
        "first_artifact_stage": ["first_artifact_stage", "artifact.first_stage"],
    }
    if metric not in aliases:
        return {"observable": False, "value": None, "source": "registry", "confidence": "none", "structured": False, "error": "unobservable_metric", "evidence": {"supported": ["score", "breakdown.<exact_key>", *sorted(aliases)]}}
    for key in aliases[metric]:
        raw, source_key = _breakdown_metric(breakdown, key)
        number = _metric_number(raw)
        if number is not None:
            return {"observable": True, "value": number, "source": "breakdown", "confidence": "high", "structured": True, "error": "", "evidence": {"matched_key": source_key, "raw": raw}}
    # Optional structured validator/artifact fields in task/run records.
    for container_name, container in (("validator", run.get("validator")), ("structured_output", run.get("structured_output")), ("artifact_metrics", task.get("artifact_metrics"))):
        if isinstance(container, dict):
            raw, source_key = _breakdown_metric(container, metric)
            number = _metric_number(raw)
            if number is not None:
                return {"observable": True, "value": number, "source": container_name, "confidence": "high", "structured": True, "error": "", "evidence": {"matched_key": source_key, "raw": raw}}
    transcript = None
    if transcript_root:
        for suffix in (".jsonl", ".json", ".txt"):
            candidate = Path(transcript_root) / f"{task_id}{suffix}"
            if candidate.is_file(): transcript = candidate; break
    if transcript is None:
        transcript = _candidate_transcript_path(report_path, task_id)
    text = _read_text(transcript, limit=500000) if transcript else ""
    value, evidence = _derived_metric_from_text(metric, text)
    number = _metric_number(value)
    return {"observable": number is not None, "value": number, "source": "transcript_heuristic" if transcript else "registry", "confidence": "low" if number is not None else "none", "structured": False, "requires_structured_confirmation": number is not None, "error": "" if number is not None else "unobservable_metric", "evidence": {**evidence, "transcript": str(transcript or "")}}


def _approximately_equal(left, right, tolerance: float = 1e-6) -> bool:
    try: return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError): return False


def _finite_number(value) -> float | None:
    """Return float(value) for finite real numbers; reject bool/None/str/NaN/Inf.

    Manifest signal validation relies on this so malformed numeric fields become a
    structured fail-closed gate decision instead of crashing the round via float().
    """
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return None
    if not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


SPEC_V1_TOP_LEVEL_ALLOWLIST = (
    "schema_version", "spec_version", "parent_spec_version", "created_by", "objective_ref",
    "objective_contract", "accepted_baseline_snapshot", "experiment_questions",
    "protected_behaviors", "search_contract", "scope_contract", "evaluation_contract",
    "history_ref", "failure_registry_ref", "mutation_operator_library_ref",
)


def _expected_signal_direction(item: dict, metric: str, default_min_delta: float) -> tuple[str, float | None, float | None, list[str]]:
    errors: list[str] = []
    direction = str(item.get("direction") or "increase").strip().lower()
    if direction not in {"increase", "decrease", "boolean_flip"}:
        errors.append(f"invalid expected signal direction: {direction}")
    raw_delta = item.get("min_delta")
    required: float | None = None
    if raw_delta is None:
        if direction in {"increase", "decrease"}:
            required = float(default_min_delta) if metric == "score" else 1.0
    else:
        required = _finite_number(raw_delta)
        if required is None:
            errors.append("expected signal min_delta must be a finite number")
            required = 0.0
    if direction in {"increase", "decrease"} and required is not None and required <= 0:
        required = float(default_min_delta) if metric == "score" else 1.0
    if direction in {"increase", "decrease"} and (required is None or required <= 0):
        errors.append("expected signal requires a positive min_delta")
    expected_value: float | None = None
    if direction == "boolean_flip":
        raw_ev = item.get("expected_value")
        if raw_ev is None:
            expected_value = 1.0
        else:
            expected_value = _finite_number(raw_ev)
            if expected_value is None:
                errors.append("expected signal expected_value must be a finite number")
    return direction, required, expected_value, errors


def _candidate_opt_effect_report(state: dict, plan: dict, *, expected_min_delta: float = DEFAULT_EXPECTED_MIN_DELTA, protected_default_max_drop: float = DEFAULT_PROTECTED_MAX_DROP) -> dict:
    baseline = state.get("baseline_optimization") if isinstance(state.get("baseline_optimization"), dict) else {}
    baseline_report = Path(str(baseline.get("result_path") or ""))
    candidate_record = (((state.get("bench") or {}).get("candidate_optimization_targeted") or {}))
    candidate_report = Path(str(candidate_record.get("resultPath") or ""))
    expected_results, protected_results, errors = [], [], []
    if not isinstance(plan, dict) or not plan.get("valid"):
        return _targeted_eval_not_run_report("canonical candidate signal plan is missing or invalid", plan=plan if isinstance(plan, dict) else {})
    bench_status = str(candidate_record.get("status") or "").lower()
    result_path = str(candidate_record.get("resultPath") or "")
    if (bench_status and bench_status != "succeeded") or not result_path or not candidate_report.is_file():
        reason = str(candidate_record.get("skipReason") or "targeted candidate optimization bench did not run to completion")
        return _targeted_eval_not_run_report(reason, plan=plan)
    if candidate_record.get("signalPlanId") and candidate_record.get("signalPlanId") != plan.get("plan_id"):
        return _targeted_eval_not_run_report("targeted bench signal plan id does not match canonical plan", plan=plan, decision="targeted_eval_plan_mismatch")

    def evaluate_signal(item: dict, protected: bool) -> dict:
        tid, metric = str(item.get("task_id") or ""), str(item.get("metric") or "score")
        before = _extract_candidate_metric(baseline_report, tid, metric)
        if metric == "score" and not before.get("observable"):
            registry_score = ((baseline.get("task_scores") or {}).get(tid))
            if registry_score is not None:
                before = {"observable": True, "value": float(registry_score), "source": "baseline_registry_task_scores", "confidence": "high", "structured": True, "error": "", "evidence": {"task_id": tid}}
        after = _extract_candidate_metric(candidate_report, tid, metric)
        declared = item.get("baseline")
        if not before.get("observable"): errors.append(f"unobservable baseline metric: task={tid} metric={metric} error={before.get('error')}")
        if not after.get("observable"): errors.append(f"unobservable candidate metric: task={tid} metric={metric} error={after.get('error')}")
        if declared is not None and before.get("observable") and not _approximately_equal(declared, before.get("value")):
            errors.append(f"declared baseline mismatch: task={tid} metric={metric} declared={declared} authoritative={before.get('value')}")
        result = {"task_id": tid, "metric": metric, "declared_baseline": declared, "baseline": before, "candidate": after}
        if protected:
            raw_max_drop = item.get("max_drop", protected_default_max_drop)
            max_drop = _finite_number(raw_max_drop)
            if max_drop is None:
                errors.append(f"protected signal max_drop must be a finite number: task={tid} metric={metric}")
                max_drop = 0.0
            raw_min_value = item.get("min_value")
            min_value = _finite_number(raw_min_value)
            min_value_invalid = raw_min_value is not None and min_value is None
            if min_value_invalid:
                errors.append(f"protected signal min_value must be a finite number: task={tid} metric={metric}")
            candidate_value = float(after["value"]) if after.get("observable") else None
            drop = (float(before["value"]) - float(after["value"])) if before.get("observable") and after.get("observable") else None
            drop_passed = drop is not None and drop <= max_drop
            if min_value_invalid:
                minimum_passed = False
            elif min_value is None:
                minimum_passed = True
            else:
                minimum_passed = candidate_value is not None and candidate_value >= min_value
            result.update({"direction": "maintain", "drop": drop, "max_drop": max_drop, "minimum": min_value, "minimum_passed": minimum_passed, "drop_passed": drop_passed, "passed": bool(drop_passed and minimum_passed), "source": item.get("source")})
            return result
        direction, required_delta, expected_value, direction_errors = _expected_signal_direction(item, metric, expected_min_delta)
        errors.extend(direction_errors)
        baseline_value = float(before["value"]) if before.get("observable") else None
        candidate_value = float(after["value"]) if after.get("observable") else None
        delta = candidate_value - baseline_value if baseline_value is not None and candidate_value is not None else None
        change_reason = ""
        if direction == "increase":
            change_passed = delta is not None and required_delta is not None and delta >= required_delta
        elif direction == "decrease":
            change_passed = delta is not None and required_delta is not None and -delta >= required_delta
        elif direction == "boolean_flip":
            if expected_value is None or baseline_value is None or candidate_value is None:
                change_passed = False
                change_reason = "boolean_flip requires a finite expected_value and observable baseline/candidate"
            elif baseline_value == expected_value:
                change_passed = False
                change_reason = "baseline already matches expected_value; no fix direction to satisfy"
            elif candidate_value != expected_value:
                change_passed = False
                change_reason = f"candidate did not flip to expected_value={expected_value}; regression or no change"
            else:
                change_passed = True
        else:
            change_passed = False
        expected_min = item.get("expected_min")
        if expected_min is None:
            threshold_passed = candidate_value is not None
        else:
            ev_min = _finite_number(expected_min)
            if ev_min is None:
                errors.append(f"expected signal expected_min must be a finite number: task={tid} metric={metric}")
                threshold_passed = False
            elif direction == "decrease":
                threshold_passed = candidate_value is not None and candidate_value <= ev_min
            else:
                threshold_passed = candidate_value is not None and candidate_value >= ev_min
        low_confidence_only = after.get("requires_structured_confirmation") is True
        passed = bool(threshold_passed and change_passed and not direction_errors)
        # Targeted evaluation is an activation screen, not final promotion. A
        # continuous metric moving materially in the intended direction should
        # reach full-opt even when it misses Tune's optimistic target magnitude;
        # full-opt/validation decide whether the smaller gain generalizes.
        activation_min_delta = required_delta
        if direction in {"increase", "decrease"} and (metric == "score" or metric.startswith("llm_judge.")):
            activation_min_delta = min(float(required_delta or expected_min_delta), float(expected_min_delta))
        if direction == "increase":
            activation_change_passed = delta is not None and activation_min_delta is not None and delta >= activation_min_delta
        elif direction == "decrease":
            activation_change_passed = delta is not None and activation_min_delta is not None and -delta >= activation_min_delta
        elif direction == "boolean_flip":
            activation_change_passed = change_passed
        else:
            activation_change_passed = False
        activation_passed = bool(
            before.get("observable") and after.get("observable")
            and activation_change_passed and not direction_errors
        )
        result.update({"direction": direction, "required_min_delta": required_delta, "activation_min_delta": activation_min_delta, "expected_value": expected_value, "threshold": expected_min, "threshold_passed": threshold_passed, "change_passed": change_passed, "activation_change_passed": activation_change_passed, "change_reason": change_reason, "delta": delta, "low_confidence_only": low_confidence_only, "passed": passed, "target_attained": passed, "activation_passed": activation_passed, "partial_pass": bool(activation_passed and not passed)})
        return result

    for item in plan.get("expected_signals") or []:
        if isinstance(item, dict):
            error_start = len(errors)
            result = evaluate_signal(item, False)
            result["role"] = str(item.get("role") or "required")
            result["errors"] = errors[error_start:]
            if result["role"] == "supporting":
                del errors[error_start:]
            expected_results.append(result)
    for item in plan.get("protected_signals") or []:
        if isinstance(item, dict):
            error_start = len(errors)
            result = evaluate_signal(item, True)
            legacy_default_gate = "supporting" if str(plan.get("schema_version") or "").endswith(".v2") else "hard"
            result["gate"] = str(item.get("gate") or ("hard" if item.get("behavior_id") else legacy_default_gate))
            result["errors"] = errors[error_start:]
            if result["gate"] != "hard":
                del errors[error_start:]
            protected_results.append(result)
    temporal_valid, temporal_reason = True, ""
    try:
        tune_time = str((((state.get("steps") or {}).get("ensure-tune") or {}).get("updated_at")) or "")
        tune_ts = datetime.fromisoformat(tune_time.replace("Z", "+00:00")).timestamp() if tune_time else None
        candidate_started = candidate_record.get("startedAt")
        if tune_ts is not None and candidate_started is not None and float(candidate_started) < float(tune_ts) - 1:
            temporal_valid, temporal_reason = False, "candidate optimization started before Tune completed"
    except Exception:
        temporal_valid, temporal_reason = False, "candidate optimization/tune timestamps are not comparable"
    required_results = [item for item in expected_results if item.get("role") != "supporting"]
    supporting_results = [item for item in expected_results if item.get("role") == "supporting"]
    structured_support = any(item.get("activation_passed", item.get("passed")) and not item.get("low_confidence_only") for item in required_results)
    target_attained = bool(required_results) and all(item.get("target_attained", item.get("passed")) for item in required_results)
    expected_passed = bool(required_results) and all(item.get("activation_passed", item.get("passed")) for item in required_results)
    if expected_passed and any(item.get("low_confidence_only") for item in required_results) and not structured_support:
        errors.append("low-confidence transcript heuristic requires another structured expected signal")
        expected_passed = False
    hard_protected_results = [item for item in protected_results if item.get("gate") == "hard"]
    budget_protected_results = [item for item in protected_results if item.get("gate") == "budget"]
    supporting_protected_results = [item for item in protected_results if item.get("gate") == "supporting"]
    protected_passed = all(item["passed"] for item in hard_protected_results)
    behavior_changed = expected_passed
    activation_probe = state.get("activation_probe") if isinstance(state.get("activation_probe"), dict) else {}
    valid = expected_passed and protected_passed and behavior_changed and temporal_valid and not errors
    reasons = list(errors)
    if not expected_passed: reasons.append("expected-fix behavior did not meet both threshold and minimum directional change")
    if not protected_passed: reasons.append("protected behavior has a major regression or is unobservable")
    if not behavior_changed: reasons.append("candidate produced no minimum-effect behavior change")
    if not temporal_valid: reasons.append(temporal_reason)
    decision = "effect_rejected" if not valid else ("effect_passed" if target_attained else "effect_partial")
    return {"schema_version": "evolution.candidate_opt_gate.v5", "valid": valid, "evaluation_status": "completed", "decision": decision, "effect_gate_passed": expected_passed, "effect_target_attained": target_attained, "protected_gate_passed": protected_passed, "behavior_changed": behavior_changed, "expected_results": expected_results, "required_expected_results": required_results, "supporting_expected_results": supporting_results, "protected_results": protected_results, "hard_protected_results": hard_protected_results, "budget_protected_results": budget_protected_results, "supporting_protected_results": supporting_protected_results, "signal_plan_id": plan.get("plan_id"), "temporal_activation_valid": temporal_valid, "activation_probe": activation_probe, "reasons": list(dict.fromkeys(reasons)), "created_at": _now()}


def action_candidate_opt_gate(args):
    paths = resolve_paths(args)
    state_path = paths["round_dir"] / "round_state.json"
    state = _load_json(state_path, {})
    static_gate = state.get("candidate_gate") if isinstance(state, dict) else {}
    signal_plan = state.get("candidate_opt_signal_plan") if isinstance(state.get("candidate_opt_signal_plan"), dict) else _load_json(paths["tune_dir"] / "candidate_opt_signal_plan.json", {})
    if not _candidate_gate_allows_progress(args, static_gate):
        report = _targeted_eval_not_run_report("; ".join(static_gate.get("reasons") or ["candidate static gate failed"]), plan=signal_plan, decision="candidate_static_gate_failed")
    elif not isinstance(signal_plan, dict) or not signal_plan:
        report = _targeted_eval_not_run_report("canonical candidate signal plan is missing", plan={})
    elif not signal_plan.get("valid"):
        report = _targeted_eval_not_run_report("; ".join(signal_plan.get("errors") or ["candidate optimization signal plan is invalid"]), plan=signal_plan, decision="invalid_signal_plan")
    else:
        report = _candidate_opt_effect_report(state, signal_plan, expected_min_delta=float(getattr(args, "expected_min_delta", DEFAULT_EXPECTED_MIN_DELTA) or 0), protected_default_max_drop=float(getattr(args, "protected_max_drop", DEFAULT_PROTECTED_MAX_DROP) or 0))
    state["candidate_opt_gate"] = report
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_path = paths["tune_dir"] / "candidate_opt_gate.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _mark_step(args, "candidate-opt-gate", "SUCCESS", {"valid": report.get("valid"), "reasons": report.get("reasons") or []})
    _print_json(report)
    return report


def action_bench_candidate_opt_full(args):
    paths = resolve_paths(args)
    state = _load_json(paths["round_dir"] / "round_state.json", {})
    gate = (
        state.get("candidate_opt_gate")
        if isinstance(state, dict) and isinstance(state.get("candidate_opt_gate"), dict)
        else {}
    )
    # Legacy gate reports may still exist in old round state, but they are
    # advisory only. The optimization Bench is informational and must run even
    # when a legacy gate is missing or invalid.
    upstream_advisory = None if gate.get("valid") else {
        "decision": gate.get("decision"), "reasons": list(gate.get("reasons") or []),
    } if gate else None
    if getattr(args, "bench_mode", "local") == "local":
        out = action_bench_local(args, "optimization", step_name_override="bench-full-opt", state_key="candidate_optimization_full", mark_step=False, print_result=False)
    else:
        out = _adapter_bench(args, "optimization", step_name_override="bench-full-opt", state_key="candidate_optimization_full", mark_step=False, print_result=False)
    state_path = paths["round_dir"] / "round_state.json"
    state = _load_json(state_path, {})
    record = ((state.get("bench") or {}).get("candidate_optimization_full") or {})
    record["evaluation_identity"] = _evaluation_identity(args, paths, kind="optimization", report_path=record.get("resultPath"))
    if upstream_advisory:
        record["upstream_candidate_opt_advisory"] = upstream_advisory
    state["bench"]["candidate_optimization_full"] = record
    # Compatibility alias: optimization now always means post-Tune candidate full opt.
    state["bench"]["optimization"] = record
    baseline_scores = ((state.get("baseline_optimization") or {}).get("task_scores") or {})
    current_scores = _task_scores_from_report(record.get("resultPath")) if record.get("resultPath") else {}
    max_drop = float(getattr(args, "protected_max_drop", DEFAULT_PROTECTED_MAX_DROP) or 0)
    max_regressed_count = int(getattr(args, "full_opt_max_regressed_count", DEFAULT_FULL_OPT_MAX_REGRESSED_COUNT))
    max_regressed_ratio = float(getattr(args, "full_opt_max_regressed_ratio", DEFAULT_FULL_OPT_MAX_REGRESSED_RATIO))
    max_negative_delta = float(getattr(args, "full_opt_max_negative_delta", DEFAULT_FULL_OPT_MAX_NEGATIVE_DELTA))
    max_single_drop = float(getattr(args, "full_opt_max_single_drop", DEFAULT_FULL_OPT_MAX_SINGLE_DROP))
    min_mean_delta = float(getattr(args, "full_opt_min_mean_delta", DEFAULT_FULL_OPT_MIN_MEAN_DELTA))
    deltas, regressions = [], []
    for tid, before in baseline_scores.items():
        after = current_scores.get(tid)
        if before is None or after is None:
            continue
        delta = float(after) - float(before)
        deltas.append(delta)
        if delta < -max_drop:
            regressions.append({"task_id": tid, "baseline": before, "candidate": after, "delta": delta, "threshold": max_drop})
    missing_task_ids = sorted(tid for tid, score in baseline_scores.items() if score is not None and current_scores.get(tid) is None)
    mean_delta = (sum(deltas) / len(deltas)) if deltas else None
    regression_ratio = (len(regressions) / len(deltas)) if deltas else None
    negative_delta_mass = sum(-delta for delta in deltas if delta < 0)
    worst_delta = min(deltas) if deltas else None
    plan = state.get("candidate_opt_signal_plan") if isinstance(state.get("candidate_opt_signal_plan"), dict) else {}
    baseline_report = Path(str(((state.get("baseline_optimization") or {}).get("result_path")) or ""))
    candidate_report = Path(str(record.get("resultPath") or ""))
    hard_protected = []
    for signal in plan.get("protected_signals") or []:
        legacy_default_gate = "supporting" if str(plan.get("schema_version") or "").endswith(".v2") else "hard"
        if not isinstance(signal, dict) or str(signal.get("gate") or ("hard" if signal.get("behavior_id") else legacy_default_gate)) != "hard":
            continue
        tid, metric = str(signal.get("task_id") or ""), str(signal.get("metric") or "score")
        before_metric = _extract_candidate_metric(baseline_report, tid, metric)
        after_metric = _extract_candidate_metric(candidate_report, tid, metric)
        before_value = _finite_number(before_metric.get("value")) if before_metric.get("observable") else None
        after_value = _finite_number(after_metric.get("value")) if after_metric.get("observable") else None
        allowed_drop = _finite_number(signal.get("max_drop"))
        allowed_drop = max_drop if allowed_drop is None else allowed_drop
        minimum = _finite_number(signal.get("min_value"))
        drop = before_value - after_value if before_value is not None and after_value is not None else None
        passed = after_value is not None and drop is not None and drop <= allowed_drop and (minimum is None or after_value >= minimum)
        if not passed:
            hard_protected.append({"behavior_id": signal.get("behavior_id"), "task_id": tid, "metric": metric, "baseline": before_value, "candidate": after_value, "drop": drop, "max_drop": allowed_drop, "min_value": minimum})
    budget_failures = []
    if len(regressions) > max_regressed_count: budget_failures.append(f"regressed task count {len(regressions)} > {max_regressed_count}")
    if regression_ratio is not None and regression_ratio > max_regressed_ratio: budget_failures.append(f"regressed task ratio {regression_ratio:.4f} > {max_regressed_ratio:.4f}")
    if negative_delta_mass > max_negative_delta: budget_failures.append(f"negative delta mass {negative_delta_mass:.4f} > {max_negative_delta:.4f}")
    if worst_delta is not None and worst_delta < -max_single_drop: budget_failures.append(f"worst task delta {worst_delta:.4f} < {-max_single_drop:.4f}")
    if mean_delta is not None and mean_delta < min_mean_delta: budget_failures.append(f"mean delta {mean_delta:.4f} < {min_mean_delta:.4f}")
    valid_full = bool(current_scores) and not missing_task_ids and not hard_protected and not budget_failures
    state["full_opt_gate"] = {
        "schema_version": "evolution.full_opt_gate.v2", "valid": valid_full,
        "mean_delta": mean_delta, "compared_task_count": len(deltas),
        "major_regressions": regressions, "hard_protected_regressions": hard_protected,
        "regression_count": len(regressions), "regression_ratio": regression_ratio,
        "negative_delta_mass": negative_delta_mass, "worst_delta": worst_delta,
        "regression_budget": {"max_regressed_task_count": max_regressed_count, "max_regressed_task_ratio": max_regressed_ratio, "max_total_negative_delta": max_negative_delta, "max_single_unprotected_drop": max_single_drop, "min_mean_delta": min_mean_delta},
        "budget_failures": budget_failures,
        "missing_task_ids": missing_task_ids, "full_coverage": not missing_task_ids,
        "upstream_candidate_opt_advisory": upstream_advisory, "created_at": _now(),
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _mark_step(args, "bench-full-opt", "SUCCESS", {"score": (record.get("summary") or {}).get("score")})
    _print_json(out)
    return out


def action_bench_opt(args):
    """Compatibility alias for the post-Tune full candidate optimization."""
    return action_bench_candidate_opt_full(args)

def _full_opt_failure_reasons(gate: dict) -> list[str]:
    """Return complete, stable diagnostics for every full-opt rejection mode."""
    gate = gate if isinstance(gate, dict) else {}
    reasons = [str(item) for item in (gate.get("budget_failures") or []) if str(item).strip()]
    hard = gate.get("hard_protected_regressions") or []
    if hard:
        reasons.append(f"hard protected regressions: {len(hard)}")
    missing = gate.get("missing_task_ids") or []
    if missing:
        reasons.append(f"missing full-opt tasks: {len(missing)}")
    if not reasons:
        major = gate.get("major_regressions") or []
        if major:
            reasons.append(f"full optimization major regressions: {len(major)}")
        else:
            reasons.append("full optimization gate failed")
    return list(dict.fromkeys(reasons))


def action_bench_val(args):
    paths = resolve_paths(args)
    state_path = paths["round_dir"] / "round_state.json"
    prior_state = _load_json(state_path, {})
    change_summary = prior_state.get("change_summary") if isinstance(prior_state, dict) and isinstance(prior_state.get("change_summary"), dict) else _write_round_change_summary(args)
    candidate_gate = prior_state.get("candidate_gate") if isinstance(prior_state, dict) and isinstance(prior_state.get("candidate_gate"), dict) else _candidate_prevalidation_gate(args, paths, change_summary)
    candidate_opt_gate = prior_state.get("candidate_opt_gate") if isinstance(prior_state, dict) and isinstance(prior_state.get("candidate_opt_gate"), dict) else {}
    full_opt_gate = prior_state.get("full_opt_gate") if isinstance(prior_state, dict) and isinstance(prior_state.get("full_opt_gate"), dict) else {}
    full_record = (((prior_state.get("bench") or {}).get("candidate_optimization_full") or {})) if isinstance(prior_state, dict) else {}
    full_completed = str(full_record.get("status") or "").lower() == "succeeded" and bool(full_record.get("resultPath"))
    # Validation Bench is the sole acceptance evidence. Legacy gate reports and
    # optimization coverage are retained only as diagnostics and never skip Test.
    state = _load_json(state_path, {})
    if isinstance(state, dict):
        state["evaluation_advisories"] = {
            "candidate_opt": None if candidate_opt_gate.get("valid") else {"decision": candidate_opt_gate.get("decision"), "reasons": list(candidate_opt_gate.get("reasons") or [])},
            "full_opt": None if full_opt_gate.get("valid") else {"reasons": _full_opt_failure_reasons(full_opt_gate)},
        }
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if getattr(args, "bench_mode", "local") == "local":
        if getattr(args, "staged_bench", True):
            staged = _action_bench_val_staged_if_planned(args, paths)
            if staged is not None:
                return staged
        return action_bench_local(args, "validation")
    _adapter_bench(args, "validation")


def _action_bench_val_staged_if_planned(args, paths: dict):
    """Run validation in staged mode when tune emits a valid bench_plan.json.

    Semantics: the smoke subset can early-reject, but only a merged full-coverage
    validation result can be accepted. The full stage runs only remaining task
    templates not already covered by smoke, then merges both reports.
    """
    plan = _load_validation_bench_plan(paths)
    if not plan:
        return None
    template_dir = _local_template_dir(args, "validation", paths)
    template_dir = template_dir.resolve() if template_dir.exists() else template_dir.absolute()
    report = _validate_validation_bench_plan(args, paths, plan, template_dir)
    if not report.get("valid"):
        _log(args, "bench-val", f"staged bench plan invalid; fallback to full validation: {report.get('errors')}", "WARN")
        return action_bench_local(args, "validation")

    round_dir = paths["round_dir"]
    subset_root = round_dir / "bench_plan" / "subsets"
    selected = list(report.get("selected_task_ids") or [])
    remaining = list(report.get("remaining_task_ids") or [])
    threshold = report.get("promotion_threshold")

    smoke_subset = subset_root / "smoke-val"
    smoke_subset_info = _prepare_bench_subset_dir(template_dir, selected, smoke_subset)
    _log(args, "bench-val", f"staged smoke validation: tasks={selected}, threshold={threshold}")
    smoke_out = action_bench_local(
        args,
        "validation",
        template_dir_override=smoke_subset,
        step_name_override="bench-smoke-val",
        state_key="validation_smoke",
        mark_step=False,
        print_result=False,
    )
    smoke_score = (smoke_out.get("summary") or {}).get("score")
    smoke_status = str(smoke_out.get("status") or "").lower()
    smoke_ok = smoke_status == "succeeded"
    promoted = bool(smoke_ok)
    if threshold is not None:
        try:
            promoted = smoke_ok and smoke_score is not None and float(smoke_score) >= float(threshold)
        except Exception:
            promoted = False

    state_path = round_dir / "round_state.json"
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("bench", {})
    staged_meta = {
        "schema_version": "evolution.staged_bench.v0",
        "enabled": True,
        "plan_report_path": str(round_dir / "bench_plan" / "bench_plan_report.json"),
        "smoke": {"task_ids": selected, "subset": smoke_subset_info, "status": smoke_out.get("status"), "score": smoke_score, "resultPath": smoke_out.get("resultPath")},
        "remaining_task_ids": remaining,
        "promotion_threshold": threshold,
        "promoted_to_full": promoted,
        "full_coverage": False,
        "created_at": _now(),
    }

    if not promoted:
        # Early reject: keep smoke evidence, but mark validation as partial so accept cannot promote it.
        state["bench"]["validation"] = {
            **(state.get("bench", {}).get("validation_smoke") or {}),
            "status": "partial_rejected",
            "summary": smoke_out.get("summary") or {},
            "resultPath": smoke_out.get("resultPath") or "",
            "staged": staged_meta,
        }
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _mark_step(args, "bench-val", "SUCCESS", {"staged": True, "earlyRejected": True, "score": smoke_score, "threshold": threshold})
        reason = "smoke benchmark failed" if not smoke_ok else "smoke score below promotion threshold"
        out = {"status": "partial_rejected", "reason": reason, "score": smoke_score, "threshold": threshold, "summary": smoke_out.get("summary") or {}, "staged": staged_meta}
        _log(args, "bench-val", f"staged early reject: smoke_score={smoke_score}, threshold={threshold}")
        _print_json(out)
        return out

    report_paths = [Path(smoke_out.get("resultPath"))] if smoke_out.get("resultPath") else []
    remaining_out = None
    if remaining:
        remaining_subset = subset_root / "remaining-val"
        remaining_subset_info = _prepare_bench_subset_dir(template_dir, remaining, remaining_subset)
        _log(args, "bench-val", f"staged remaining validation: tasks={remaining}")
        remaining_out = action_bench_local(
            args,
            "validation",
            template_dir_override=remaining_subset,
            step_name_override="bench-val-remaining",
            state_key="validation_remaining",
            mark_step=False,
            print_result=False,
        )
        remaining_ok = str(remaining_out.get("status") or "").lower() == "succeeded"
        if remaining_ok and remaining_out.get("resultPath"):
            report_paths.append(Path(remaining_out["resultPath"]))
        staged_meta["remaining"] = {"task_ids": remaining, "subset": remaining_subset_info, "status": remaining_out.get("status"), "score": (remaining_out.get("summary") or {}).get("score"), "resultPath": remaining_out.get("resultPath")}
    else:
        staged_meta["remaining"] = {"task_ids": [], "subset": {}, "score": None, "resultPath": ""}

    merged_path = round_dir / "bench_plan" / "merged_validation_benchmark_report.json"
    merged = _merge_benchmark_reports(report_paths, merged_path)
    all_task_ids = set(report.get("all_task_ids") or [])
    merged_task_ids = set(merged.get("task_ids") or [])
    duplicate_task_ids = list(merged.get("duplicate_task_ids") or [])
    full_coverage = bool(all_task_ids) and all_task_ids.issubset(merged_task_ids) and not duplicate_task_ids
    staged_meta["full_coverage"] = full_coverage
    staged_meta["duplicate_task_ids"] = duplicate_task_ids
    staged_meta["merged_result_path"] = str(merged_path)
    staged_meta["merged_task_ids"] = sorted(merged_task_ids)

    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("bench", {})
    state["bench"]["validation"] = {
        "benchRunId": f"staged-val-{args.round}",
        "benchDir": str(round_dir / "bench_plan"),
        "outputDir": str(round_dir / "bench_plan"),
        "resultPath": str(merged_path),
        "logPath": "",
        "status": "succeeded" if full_coverage else "incomplete",
        "exitCode": 0 if full_coverage else 1,
        "startedAt": None,
        "completedAt": int(time.time()),
        "elapsedSeconds": None,
        "summary": merged.get("summary") or {},
        "staged": staged_meta,
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _mark_step(args, "bench-val", "SUCCESS", {"staged": True, "fullCoverage": full_coverage, "score": (merged.get("summary") or {}).get("score")})
    out = {"status": "succeeded" if full_coverage else "incomplete", "score": (merged.get("summary") or {}).get("score"), "summary": merged.get("summary") or {}, "resultPath": str(merged_path), "staged": staged_meta}
    _log(args, "bench-val", f"staged validation done: full_coverage={full_coverage}, score={out['score']}")
    _print_json(out)
    return out


def _record_adapter_fixture_identity(state: dict, kind: str, workflow_result: dict) -> dict:
    """Bind adapter evaluation identity to the actual frozen Bench input.

    Domain mode does not populate ``plan/output/templates/val``.  Using that
    empty directory made a candidate incomparable with its bootstrap baseline
    even when ClawBench resolved the exact same pinned template.
    """
    raw = str((workflow_result or {}).get("inputPath") or "").strip()
    if not raw:
        return {}
    fixture = _tree_identity(Path(raw))
    if not fixture.get("exists") or not fixture.get("sha256"):
        return {}
    identity = state.setdefault("identity", {})
    if not isinstance(identity, dict):
        identity = {}
        state["identity"] = identity
    fixture_key = "optimization_fixture" if kind == "optimization" else "validation_fixture"
    identity[fixture_key] = fixture
    identity[f"{fixture_key}_source"] = "clawbench.workflow_result.inputPath"
    return fixture


def _adapter_bench(args, kind: str, *, step_name_override: str | None = None, state_key: str | None = None, mark_step: bool = True, print_result: bool = True):
    """Run a standard ClawWeb Bench through the clawevolve-bench product CLI."""
    paths = resolve_paths(args)
    task_id = paths["task_id"]
    domain_id = (args.train_bench_domain_id if kind == "optimization" else args.test_bench_domain_id) or _env("DOMAIN_ID", "")
    if not domain_id:
        raise SystemExit("DOMAIN_ID is required for adapter mode")
    model = _bench_model(args)
    suite = args.suite or "all"
    scene = f"clawevolve-{kind}-{task_id}-round-{args.round}"
    clawweb_url = (
        args.clawweb_url or args.clawweb_url_camel
        or _env("CLAWEVOLVE_CLAWWEB_URL") or _env("CLAWWEB_URL") or _env("CLAWWEBURL")
        or "http://127.0.0.1:5173/"
    )
    owner_id = args.owner_id or _env("CLAWBENCH_OWNER_ID", "")
    step_name = step_name_override or ("bench-opt" if kind == "optimization" else "bench-val")
    record_key = state_key or kind
    work_dir = Path(paths["round_dir"]) / "bench" / record_key
    _log(args, step_name, f"start clawevolve-bench: domain={domain_id}, model={model}, work_dir={work_dir}")
    workflow_result = run_clawevolve_bench(
        owner_id=owner_id, domain_id=domain_id, work_dir=work_dir,
        context_dir=Path(paths["run_dir"]) / "bench" / "context" / ("train" if kind == "optimization" else "test"),
        workspace=Path(paths["workspace"]), skill_base_dir=Path(paths["skill_base"]),
        model=model, suite=suite, scene=scene, judge=args.judge or "", report=False,
        timeout_seconds=args.bench_timeout, clawweb_url=clawweb_url,
        evolve_task_id=task_id, evolve_step_id=str(getattr(args, "step_id", "") or ""),
        trace_role=(f"baseline_{'train' if kind == 'optimization' else 'test'}"
                    if str(record_key).startswith("baseline_")
                    else f"candidate_{'train' if kind == 'optimization' else 'test'}"),
        openclaw_execution_mode=args.openclaw_execution_mode,
    )
    result_path = str(workflow_result.get("resultPath") or "")
    summary = _compute_bench_summary(Path(result_path)) if result_path else _summary_from_workflow_result(workflow_result)
    if not summary or summary.get("score") is None:
        summary = _summary_from_workflow_result(workflow_result)
    bench_run_id = str(workflow_result.get("benchRunId") or "")
    output_dir = work_dir / "output"
    log_path = Path(str(workflow_result.get("logPath") or (work_dir / "logs/clawevolve-bench.log")))

    # 写入 round_state.json
    state_path = paths["round_dir"] / "round_state.json"
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    bench_record = {
        "benchRunId": bench_run_id,
        "benchDir": str(work_dir),
        "outputDir": str(output_dir),
        "resultPath": result_path,
        "logPath": str(log_path),
        "status": "succeeded",
        "exitCode": 0,
        "startedAt": workflow_result.get("startedAt"),
        "completedAt": workflow_result.get("completedAt"),
        "summary": summary,
        "uploadStatus": "succeeded",
        "detailUrl": _clean(workflow_result.get("detailUrl")),
        "producerStepId": str(getattr(args, "step_id", "") or ""),
        "domainId": domain_id,
        "domainOwnerId": str(owner_id),
    }
    fixture_identity = _record_adapter_fixture_identity(state, kind, workflow_result)
    if fixture_identity:
        bench_record["fixtureIdentity"] = fixture_identity
    state.setdefault("bench", {})
    state["bench"][record_key] = bench_record
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if mark_step:
        _mark_step(args, step_name, "SUCCESS", {"benchRunId": bench_run_id, "score": summary.get("score")})
    _log(args, step_name, f"done: bench_run_id={bench_run_id}, score={summary.get('score')}")

    out = {
        "status": "succeeded", "benchRunId": bench_run_id, "benchDir": str(work_dir),
        "outputDir": str(output_dir), "resultPath": result_path,
        "detailUrl": _clean(workflow_result.get("detailUrl")),
        "error": "", "logPath": str(log_path), "exitCode": 0,
        "score": summary.get("score"), "summary": summary,
        "uploadStatus": "succeeded",
    }
    if print_result:
        _print_json(out)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# action: accept — acceptance gate（inline 读 bench score）
# ═══════════════════════════════════════════════════════════════════════════════

def action_accept(args):
    """Decide acceptance from Test Bench score only.

    This is intentionally the only promotion rule for a round:
    candidate Test score must be strictly greater than the baseline Test score.
    Train Bench, legacy gates, signals, reachability and Review are advisory.
    """
    paths = resolve_paths(args)
    round_dir = paths["round_dir"]
    run_dir = paths["run_dir"]
    state = _load_json(round_dir / "round_state.json", {})
    if not isinstance(state, dict):
        state = {}

    bench = state.get("bench") or {}
    val_bench = bench.get("validation") or {}
    opt_bench = bench.get("optimization") or {}
    candidate_test_score = _finite_number((val_bench.get("summary") or {}).get("score"))
    candidate_train_score = _finite_number((opt_bench.get("summary") or {}).get("score"))

    manifest_path = run_dir / "optimize" / "output" / "optimize_manifest.json"
    run_state = _load_json(manifest_path, {"schema_version": "evolution.run_manifest.v0", "rounds": []})
    if not isinstance(run_state, dict):
        run_state = {"schema_version": "evolution.run_manifest.v0", "rounds": []}
    baseline_test_score = _finite_number(run_state.get("last_accepted_validation_score"))
    baseline_round = run_state.get("last_accepted_round")
    baseline_train_score = None
    accepted_opt = run_state.get("accepted_baseline_optimization")
    if isinstance(accepted_opt, dict):
        baseline_train_score = _finite_number((accepted_opt.get("summary") or {}).get("score"))
    if baseline_train_score is None:
        baseline_train_score = _finite_number(run_state.get("last_accepted_optimization_score"))

    # Round 1 uses the immutable task-start Test baseline when no accepted round
    # exists yet. This is still the same Test Bench comparison, not a new gate.
    if baseline_test_score is None:
        bootstrap = _bootstrap_validation_baseline(paths)
        if bootstrap.get("available"):
            baseline_test_score = _finite_number(bootstrap.get("validation_score"))
            baseline_round = bootstrap.get("round_id")
            baseline_train_score = baseline_train_score if baseline_train_score is not None else _finite_number(bootstrap.get("optimization_score"))
            run_state["last_accepted_validation_score"] = baseline_test_score
            run_state["last_accepted_round"] = baseline_round
            run_state["last_accepted_validation_result_path"] = bootstrap.get("validation_result_path") or ""
            run_state["baseline_source"] = bootstrap.get("source")
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(run_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if baseline_test_score is None:
        bench_decision = "baseline_unavailable"
    elif candidate_test_score is None:
        bench_decision = "bench_failed"
    elif candidate_test_score > baseline_test_score:
        bench_decision = "passed"
    else:
        bench_decision = "not_improved"
    effect_passed = bench_decision == "passed"
    promotion_status = "pending" if effect_passed else "not_started"
    delta = None if baseline_test_score is None or candidate_test_score is None else candidate_test_score - baseline_test_score
    train_delta = None if baseline_train_score is None or candidate_train_score is None else candidate_train_score - baseline_train_score

    change_summary = state.get("change_summary") if isinstance(state.get("change_summary"), dict) else _round_change_summary(paths)
    reason = {
        "passed": "candidate Test Bench score is higher than baseline",
        "not_improved": "candidate Test Bench score is not higher than baseline",
        "bench_failed": "candidate Test Bench did not produce a valid score",
        "baseline_unavailable": "baseline Test Bench did not produce a valid score",
    }[bench_decision]
    report = {
        "schema_version": "evolution.acceptance.bench_review.v1",
        "evolve_run_id": paths["task_id"],
        "round_id": args.round,
        "bench_decision": bench_decision,
        # Bench success selects a Candidate for promotion; it is not an
        # Accepted version until Pack, publish and manifest commit all succeed.
        "accepted": False,
        "promotion_status": promotion_status,
        "decision": bench_decision,  # legacy alias consumed by existing Bot clients
        "score": {
            "name": "test_score",
            "baseline": baseline_test_score,
            "candidate": candidate_test_score,
            "delta": delta,
        },
        "train": {
            "baseline": baseline_train_score,
            "candidate": candidate_train_score,
            "delta": train_delta,
            "role": "informational",
        },
        "baseline": {"round_id": baseline_round, "validation_score": baseline_test_score},
        "current": {"validation_score": candidate_test_score, "optimization_score": candidate_train_score},
        "delta": {"validation_score": delta},
        "change_summary": change_summary,
        "reason": reason,
        "review_status": "pending",
        # A rejected candidate may have modified the live workspace. Keep the
        # existing restore contract; action_restore now handles absent baselines
        # without converting a normal rejected round into a watchdog failure.
        "restore_required": not effect_passed,
        "promote_to_baseline": False,
        "created_at": _now(),
    }
    acc_dir = round_dir / "acceptance"
    acc_dir.mkdir(parents=True, exist_ok=True)
    (acc_dir / "acceptance_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state["accepted"] = False
    state["promotion_status"] = promotion_status
    state["acceptance_decision"] = bench_decision
    state["restore_required"] = report["restore_required"]
    state["acceptance"] = report
    state["bench_decision"] = bench_decision
    state["review_status"] = "pending"
    (round_dir / "round_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_evidence_database(args, stage="accept")
    _log(args, "accept", f"bench_decision={bench_decision}, promotion_status={promotion_status}, accepted=false, test_score={candidate_test_score}, baseline={baseline_test_score}")
    _mark_step(args, "accept", "SUCCESS", {"accepted": False, "promotion_status": promotion_status, "bench_decision": bench_decision, "decision": bench_decision, "restore_required": report["restore_required"], "validation_score": candidate_test_score, "baseline_score": baseline_test_score})
    _print_json(report)


# ═══════════════════════════════════════════════════════════════════════════════
# action: replicate-validation — minimum replication before promotion
# ═══════════════════════════════════════════════════════════════════════════════

def _run_validation_replication(args, state_key: str, step_name: str) -> dict:
    if getattr(args, "bench_mode", "local") == "local":
        return action_bench_local(args, "validation", step_name_override=step_name, state_key=state_key, mark_step=False, print_result=False)
    return _adapter_bench(args, "validation", step_name_override=step_name, state_key=state_key, mark_step=False, print_result=False)


def _build_repeated_paired_eval(args, baseline_result: str, candidate_result: str, existing: dict | None = None) -> dict:
    baseline_scores = _task_scores_from_report(baseline_result)
    candidate_scores = _task_scores_from_report(candidate_result)
    current = _paired_eval_from_scores(
        baseline_scores, candidate_scores,
        float(getattr(args, "acceptance_margin", DEFAULT_ACCEPTANCE_MARGIN) or 0),
        float(getattr(args, "paired_min_win_rate", DEFAULT_PAIRED_MIN_WIN_RATE) or 0),
    )
    existing = existing if isinstance(existing, dict) else {}
    prior_pairs = [dict(x) for x in (existing.get("pairs") or []) if isinstance(x, dict)] if existing.get("evaluation_unit") == "task_x_replicate" else []
    prior_replicates = [dict(x) for x in (existing.get("replicates") or []) if isinstance(x, dict)]
    replicate_id = max([int(x.get("replicate_id") or 0) for x in prior_replicates] + [int(existing.get("replicate_count") or 0), 0]) + 1
    seed = getattr(args, "sampling_seed", None)
    new_pairs = []
    for item in current.get("pairs") or []:
        pair = dict(item)
        pair["seed"] = seed
        pair["replicate"] = replicate_id
        pair["baseline_score"] = pair.pop("baseline", None)
        pair["candidate_score"] = pair.pop("current", None)
        new_pairs.append(pair)
    result_fingerprint = hashlib.sha256((str(baseline_result) + "|" + str(candidate_result)).encode("utf-8")).hexdigest()
    if any(str(x.get("result_fingerprint") or "") == result_fingerprint for x in prior_replicates):
        return existing
    pairs = prior_pairs + new_pairs
    n = len(pairs)
    mean_delta = sum(float(x.get("delta") or 0) for x in pairs) / n if n else None
    wins = sum(1 for x in pairs if float(x.get("delta") or 0) > 0)
    losses = sum(1 for x in pairs if float(x.get("delta") or 0) < 0)
    ties = n - wins - losses
    win_rate = wins / n if n else None
    margin = float(getattr(args, "acceptance_margin", DEFAULT_ACCEPTANCE_MARGIN) or 0)
    min_win_rate = float(getattr(args, "paired_min_win_rate", DEFAULT_PAIRED_MIN_WIN_RATE) or 0)
    replicates = prior_replicates + [{
        "replicate_id": replicate_id, "seed": seed,
        "baseline_result_path": baseline_result, "candidate_result_path": candidate_result,
        "result_fingerprint": result_fingerprint, "task_count": len(new_pairs), "created_at": _now(),
    }]
    same_task_set = set(baseline_scores) == set(candidate_scores)
    passed = bool(n and same_task_set and mean_delta is not None and mean_delta >= margin and (win_rate or 0) >= min_win_rate)
    return {
        "schema_version": "evolution.paired_eval.v2", "evaluation_unit": "task_x_replicate",
        "replicate_count": len(replicates), "pair_count": n, "n": n,
        "mean_delta": mean_delta, "wins": wins, "losses": losses, "ties": ties, "win_rate": win_rate,
        "margin": margin, "min_win_rate": min_win_rate, "passed": passed,
        "pairs": pairs[:1000], "replicates": replicates,
        "same_task_set": bool(existing.get("same_task_set", True)) and same_task_set,
        "same_seed_requested": seed is not None,
        "source": "cumulative automatic baseline/candidate validation replication",
        "created_at": _now(),
    }


def action_replicate_validation(args):
    paths = resolve_paths(args)
    acceptance = _require_acceptance_report(paths)
    if acceptance.get("decision") not in {"needs_replication", "needs_repeated_eval"}:
        _mark_step(args, "replicate-validation", "SKIPPED", {"reason": "acceptance is conclusive", "decision": acceptance.get("decision")})
        _print_json({"status": "skipped", "decision": acceptance.get("decision")})
        return {"status": "skipped", "decision": acceptance.get("decision")}

    state_path = paths["round_dir"] / "round_state.json"
    state = _load_json(state_path, {})
    baseline_artifact = str(state.get("baseline_artifact") or "") if isinstance(state, dict) else ""
    if not baseline_artifact and args.round == 1:
        candidate = paths["optimize_input_dir"] / "baseline" / "artifact_v0.zip"
        baseline_artifact = str(candidate) if candidate.is_file() else ""
    if not baseline_artifact or not Path(baseline_artifact).is_file():
        raise SystemExit("automatic replication requires a local accepted baseline artifact")

    replication_dir = paths["round_dir"] / "replication"
    candidate_artifact = replication_dir / "candidate.zip"
    replication_dir.mkdir(parents=True, exist_ok=True)
    if not candidate_artifact.is_file():
        _create_pack_artifact(args, candidate_artifact, log_step="replicate-validation", artifact_kind="replication_candidate")

    paired_path = paths["accept_dir"] / "paired_eval.json"
    paired = _load_json(paired_path, {})
    target_replicates = max(1, int(getattr(args, "paired_min_runs", DEFAULT_PAIRED_MIN_RUNS) or 1))
    original_seed = getattr(args, "sampling_seed", None)
    last_baseline_out, last_candidate_out = {}, {}
    replication_attempts = 0
    max_replication_attempts = max(target_replicates * 2, target_replicates + 1)
    try:
        while int((paired or {}).get("replicate_count") or 0) < target_replicates:
            replication_attempts += 1
            if replication_attempts > max_replication_attempts:
                raise RuntimeError(
                    f"replication attempt budget exhausted without reaching {target_replicates} distinct result pairs"
                )
            before_count = int((paired or {}).get("replicate_count") or 0)
            next_id = before_count + 1
            if original_seed is not None:
                args.sampling_seed = int(original_seed) + next_id - 1
            baseline_restored = False
            try:
                _restore_workspace_from_artifact(args, Path(baseline_artifact), f"automatic validation replication baseline #{next_id}")
                baseline_restored = True
                last_baseline_out = _run_validation_replication(args, f"validation_replication_baseline_{next_id}", f"replicate-baseline-val-{next_id}")
                _restore_workspace_from_artifact(args, candidate_artifact, f"automatic validation replication candidate #{next_id}")
                baseline_restored = False
                last_candidate_out = _run_validation_replication(args, f"validation_replication_candidate_{next_id}", f"replicate-candidate-val-{next_id}")
            finally:
                if baseline_restored:
                    _restore_workspace_from_artifact(args, candidate_artifact, "replication failure candidate recovery")
            updated = _build_repeated_paired_eval(args, str(last_baseline_out.get("resultPath") or ""), str(last_candidate_out.get("resultPath") or ""), paired)
            after_count = int((updated or {}).get("replicate_count") or 0)
            if after_count <= before_count:
                raise RuntimeError(
                    "replication made no progress: duplicate or stale baseline/candidate result pair"
                )
            paired = updated
            paired_path.write_text(json.dumps(paired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        args.sampling_seed = original_seed
    if not last_baseline_out and paired.get("replicates"):
        last = paired["replicates"][-1]
        last_baseline_out = {"resultPath": last.get("baseline_result_path")}
        last_candidate_out = {"resultPath": last.get("candidate_result_path")}
    state = _load_json(state_path, {})
    state["replication"] = {
        "status": "completed", "paired_eval": str(paired_path),
        "baseline_result_path": last_baseline_out.get("resultPath"), "candidate_result_path": last_candidate_out.get("resultPath"),
        "replicate_count": paired.get("replicate_count"), "replicates": paired.get("replicates") or [],
        "candidate_artifact": str(candidate_artifact), "completed_at": _now(),
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _mark_step(args, "replicate-validation", "SUCCESS", {"pairedPassed": paired.get("passed"), "replicateCount": paired.get("replicate_count")})
    # Re-enter the same deterministic acceptance gate with repeated-pair evidence.
    action_accept(args)
    return paired


# ═══════════════════════════════════════════════════════════════════════════════
# action: pack
# ═══════════════════════════════════════════════════════════════════════════════

def _create_pack_artifact(args, target: Path, *, log_step: str, artifact_kind: str) -> dict:
    """Create one immutable workspace pack using the existing clawevolve-pack product."""
    paths = resolve_paths(args)
    target.parent.mkdir(parents=True, exist_ok=True)
    script = find_script(paths["skill_base"], "clawevolve-pack", "scripts/pack.sh")
    before = set(target.parent.glob("*.zip"))
    cmd = [
        "bash", str(script),
        "--workspace", str(paths["workspace"]),
        "--out-dir", str(target.parent),
        "--evolve-run-id", paths["task_id"],
        "--max-artifact-mb", str(getattr(args, "max_artifact_mb", DEFAULT_MAX_ARTIFACT_MB) or 0),
    ]
    _log(args, log_step, f"cmd: {' '.join(cmd)}")
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        _log_block(args, log_step, "ERROR", proc.stdout + proc.stderr)
        raise SystemExit(proc.stdout + proc.stderr)
    after = set(target.parent.glob("*.zip"))
    candidates = list(after - before) or sorted(target.parent.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    generated = candidates[-1] if candidates else None
    if not generated or not generated.exists():
        match = re.search(r"(?m)^\s*path:\s*(\S+)\s*$", proc.stdout + "\n" + proc.stderr)
        generated = Path(match.group(1)) if match else None
    if not generated or not generated.exists():
        raise SystemExit("pack succeeded but generated zip was not found")
    if generated.resolve() != target.resolve():
        if target.exists():
            raise SystemExit(f"refusing to overwrite existing pack: {target}")
        shutil.move(str(generated), str(target))
    return {
        "kind": artifact_kind,
        "path": str(target),
        "size": target.stat().st_size,
        "sha256": _sha256(target),
        "contentType": "application/zip",
        "createdAt": _now(),
    }


def _rollback_snapshot_files(paths: dict, args) -> tuple[Path, Path]:
    """Return the immutable rollback artifact and manifest for this Round."""
    base = Path(paths.get("rollback_dir") or (Path(paths["round_dir"]) / "rollback"))
    return base / "artifact_before_tune.zip", base / "rollback-manifest.json"


def _initial_artifact_files(paths: dict) -> tuple[Path, Path]:
    base = Path(paths["optimize_input_dir"]) / "baseline"
    return base / "artifact_v0.zip", base / "baseline-manifest.json"


def _baseline_pack_outputs(paths: dict, args) -> list[Path]:
    outputs = list(_rollback_snapshot_files(paths, args))
    if int(args.round) == 1:
        outputs.extend(_initial_artifact_files(paths))
    return outputs


def _register_initial_pack_with_clawweb(args, published_artifact: dict) -> dict:
    """Register the task-start Pack before Tune is allowed to mutate Workspace."""
    if not isinstance(published_artifact, dict) or not published_artifact.get("ref"):
        raise SystemExit("baseline Pack registration requires a published artifact")
    if getattr(args, "skip_clawweb", False):
        return {"status": "SKIPPED", "reason": "--skip-clawweb"}
    configured_url = (
        getattr(args, "clawweb_url", "")
        or getattr(args, "clawweb_url_camel", "")
        or _env("CLAWEVOLVE_CLAWWEB_URL")
        or _env("CLAWWEB_URL")
    )
    if not configured_url:
        return {"status": "SKIPPED", "reason": "missing explicit ClawWeb URL"}
    payload = {
        "status": "running",
        "summary": "任务初始 Pack 已创建并登记，准备开始优化",
        "output": {
            "baselineArtifact": {
                "status": "available",
                "artifact": published_artifact,
            }
        },
    }
    result = _post_clawweb_payload_best_effort(
        args, payload, "baseline-pack-clawweb-register"
    )
    if result.get("status") not in {"SUCCESS", "SKIPPED"}:
        detail = result.get("stderr") or result.get("stdout") or result.get("error")
        raise SystemExit(f"baseline Pack ClawWeb registration failed: {detail}")
    return result


def _validate_rollback_snapshot(args, artifact_path: Path, *, log_step: str) -> dict:
    """Prove that a freshly captured snapshot is readable before Tune can start."""
    artifact_path = Path(artifact_path)
    if not artifact_path.is_file() or artifact_path.stat().st_size <= 0:
        raise SystemExit(f"rollback snapshot missing or empty: {artifact_path}")
    if not zipfile.is_zipfile(artifact_path):
        raise SystemExit(f"rollback snapshot is not a readable ZIP: {artifact_path}")
    if not getattr(args, "restore_precheck", DEFAULT_RESTORE_PRECHECK):
        return {"ok": True, "skipped": True, "reason": "restore precheck disabled"}
    precheck = _deploy_dry_run(args, artifact_path, log_step=log_step, reason="round rollback snapshot precheck")
    if not precheck.get("ok"):
        raise SystemExit(f"rollback snapshot precheck failed: {_deploy_dry_run_error(precheck)}")
    return precheck


def _record_rollback_snapshot(args, paths: dict, artifact_path: Path, precheck: dict, *, source: str) -> dict:
    state_path = Path(paths["round_dir"]) / "round_state.json"
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    record = {
        "status": "ready",
        "path": str(artifact_path),
        "sha256": _sha256(Path(artifact_path)),
        "round": int(args.round),
        "source": source,
        "precheck": precheck,
        "created_at": _now(),
    }
    state["rollback_snapshot"] = record
    state.setdefault("workspace_mode", "live")
    state.setdefault("candidate_mutation_state", "not_started")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def _ensure_round_rollback_snapshot(
    args, paths: dict, *, source_artifact: Path | None = None
) -> dict:
    """Create or reuse this Round's immutable pre-Tune rollback snapshot.

    Round 1 may materialize the bytes captured for artifact_v0 into a separate
    immutable file.  The two lifecycle assets never share a path, manifest or
    state field.  Later rounds capture the live Workspace directly.
    """
    target, manifest_path = _rollback_snapshot_files(paths, args)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_json(manifest_path, {})
    if (
        target.is_file()
        and existing.get("status") == "SUCCESS"
        and existing.get("sha256") == _sha256(target)
    ):
        precheck = _validate_rollback_snapshot(args, target, log_step="baseline-pack")
        rollback = _record_rollback_snapshot(
            args, paths, target, precheck,
            source=str(existing.get("source") or "round_live_workspace"),
        )
        return {**existing, "rollbackSnapshot": rollback}

    if target.exists() or manifest_path.exists():
        raise SystemExit(
            "round rollback Pack is incomplete or changed; refusing to overwrite "
            "the pre-Tune recovery point"
        )

    if source_artifact is not None:
        source_artifact = Path(source_artifact)
        if not source_artifact.is_file():
            raise SystemExit(f"task initial artifact is missing: {source_artifact}")
        pending_artifact = target.with_name(f".{target.name}.pending")
        shutil.copy2(source_artifact, pending_artifact)
        os.replace(pending_artifact, target)
        artifact = {
            "kind": "round_rollback_pack",
            "path": str(target),
            "size": target.stat().st_size,
            "sha256": _sha256(target),
            "contentType": "application/zip",
            "createdAt": _now(),
            "materializedFrom": str(source_artifact),
        }
    else:
        artifact = _create_pack_artifact(
            args, target, log_step="baseline-pack", artifact_kind="round_rollback_pack"
        )

    precheck = _validate_rollback_snapshot(args, target, log_step="baseline-pack")
    manifest = {
        "schemaVersion": "clawevolve.round-rollback.v1",
        "status": "SUCCESS",
        "taskId": paths["task_id"],
        "stepId": args.step_id or _env("STEP_ID"),
        "round": int(args.round),
        "source": "task_initial_capture_materialized" if source_artifact is not None else "round_live_workspace",
        **artifact,
        "precheck": precheck,
    }
    pending_manifest = manifest_path.with_name(f".{manifest_path.name}.pending")
    pending_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(pending_manifest, manifest_path)
    rollback = _record_rollback_snapshot(
        args, paths, target, precheck, source=manifest["source"]
    )
    return {**manifest, "rollbackSnapshot": rollback}


def action_baseline_pack(args):
    """Capture an immutable pre-Tune rollback Pack for every Round.

    Round 1 keeps the user-visible task-start artifact_v0 and materializes a
    separate internal rollback file from the same capture. Later rounds capture
    the live Workspace directly into their own rollback directory.
    """
    paths = resolve_paths(args)
    if args.round > 1:
        out = _ensure_round_rollback_snapshot(args, paths)
        _mark_step(args, "baseline-pack", "SUCCESS", out)
        _print_json(out)
        return

    target, manifest_path = _initial_artifact_files(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_json(manifest_path, {})
    if (
        target.is_file()
        and existing.get("sha256") == _sha256(target)
        and existing.get("publishStatus") == "SUCCESS"
        and existing.get("artifactUploaded") is True
        and existing.get("manifestUploaded") is True
    ):
        state_path = Path(paths["round_dir"]) / "round_state.json"
        state = _load_json(state_path, {})
        if not isinstance(state, dict):
            state = {}
        state["baseline_artifact"] = str(target)
        if isinstance(existing.get("publishedArtifact"), dict):
            state["baselineArtifact"] = {
                "status": "available", "artifact": existing["publishedArtifact"]
            }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        registration = _register_initial_pack_with_clawweb(
            args, existing["publishedArtifact"]
        )
        rollback_out = _ensure_round_rollback_snapshot(args, paths, source_artifact=target)
        out = {
            **existing,
            "clawwebRegistration": registration,
            "rollbackSnapshot": rollback_out["rollbackSnapshot"],
            "rollbackArtifact": rollback_out,
        }
        _mark_step(args, "baseline-pack", "SUCCESS", out)
        _print_json(out)
        return
    if target.exists() and existing.get("sha256") == _sha256(target):
        artifact = {key: existing.get(key) for key in ("kind", "path", "size", "sha256", "contentType", "createdAt")}
    elif target.exists() or manifest_path.exists():
        raise SystemExit("baseline pack is incomplete or changed; refusing to overwrite task-start snapshot")
    else:
        artifact = _create_pack_artifact(args, target, log_step="baseline-pack", artifact_kind="baseline_pack")
    precheck = _validate_rollback_snapshot(args, target, log_step="baseline-pack")
    client = _artifact_client(args)
    baseline_dir = manifest_path.parent
    published_artifact = existing.get("publishedArtifact") if isinstance(existing.get("publishedArtifact"), dict) else None
    artifact_uploaded = bool(existing.get("artifactUploaded") and published_artifact)
    manifest = {
        "schemaVersion": "clawevolve.baseline-artifact.v1",
        "taskId": paths["task_id"],
        "stepId": args.step_id or _env("STEP_ID"),
        "artifactRef": published_artifact.get("ref") if published_artifact else None,
        **artifact,
        "artifactUploaded": artifact_uploaded,
        "manifestUploaded": False,
        "publishStatus": "PENDING",
        "rollbackPrecheck": precheck,
    }
    pending_path = baseline_dir / ".baseline-manifest.pending.json"
    pending_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(pending_path, manifest_path)
    if not artifact_uploaded:
        published_artifact = client.upload("baseline-pack", target, "application/zip")
        manifest["artifactRef"] = published_artifact["ref"]
        manifest["publishedArtifact"] = published_artifact
        manifest["artifactUploaded"] = True
        pending_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(pending_path, manifest_path)
    manifest["manifestUploaded"] = True
    manifest["publishStatus"] = "SUCCESS"
    publish_path = baseline_dir / ".baseline-manifest.publish.json"
    publish_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    client.upload("baseline-manifest", publish_path, "application/json")
    os.replace(publish_path, manifest_path)
    state = _load_json(paths["round_dir"] / "round_state.json", {})
    state["baseline_artifact"] = str(target)
    state["baselineArtifact"] = {"status": "available", "artifact": published_artifact}
    (paths["round_dir"] / "round_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    registration = _register_initial_pack_with_clawweb(args, published_artifact)
    rollback_out = _ensure_round_rollback_snapshot(args, paths, source_artifact=target)
    out = {
        **manifest,
        "clawwebRegistration": registration,
        "rollbackSnapshot": rollback_out["rollbackSnapshot"],
        "rollbackArtifact": rollback_out,
    }
    _mark_step(args, "baseline-pack", "SUCCESS", out)
    _print_json(out)

def action_pack(args):
    paths = resolve_paths(args)
    round_dir = paths["round_dir"]
    artifacts_dir = round_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    skill_base = paths["skill_base"]
    round_dir = paths["round_dir"]
    round_id = args.round
    workspace = paths["workspace"]

    artifacts_dir = round_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    target = artifacts_dir / f"artifact_v{round_id}.zip"

    try:
        script = find_script(skill_base, "clawevolve-pack", "scripts/pack.sh")
    except FileNotFoundError as exc:
        _log(args, "pack", f"FAILED: {exc}", level="ERROR")
        raise

    acc_report = _require_acceptance_report(paths)
    candidate_selected = _candidate_passed_bench(acc_report)
    decision = _acceptance_bench_decision(acc_report)
    preserve_rejected = decision == "not_improved"
    if not candidate_selected and not preserve_rejected:
        result = {
            "schema_version": "evolution.pack_report.v1",
            "status": "skipped",
            "artifactPath": "",
            "artifactName": "",
            "artifactKind": "no_artifact",
            "size": 0,
            "sha256": "",
            "reason": f"candidate has no comparable Bench result (decision={decision}); skip packing",
            "created_at": _now(),
        }
        (artifacts_dir / "pack_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _mark_step(args, "pack", "SKIPPED", result)
        _log(args, "pack", result["reason"])
        _print_json(result)
        return

    before = set(artifacts_dir.glob("*.zip"))
    cmd = [
        "bash", str(script),
        "--workspace", workspace,
        "--out-dir", str(artifacts_dir),
        "--evolve-run-id", paths["task_id"],
        "--max-artifact-mb", str(getattr(args, "max_artifact_mb", DEFAULT_MAX_ARTIFACT_MB) or 0),
    ]
    if getattr(args, "pack_include_evolve_results", False):
        cmd.extend(["--include-evolve-results", "--evolve-results-dir", str(paths["run_dir"])])
    promotion_status = "pending" if candidate_selected else "not_started"
    artifact_kind = "accepted" if candidate_selected else "rejected"
    _log(args, "pack", f"cmd: {' '.join(cmd)}, bench_decision={decision}, promotion_status={promotion_status}")
    proc = subprocess.run(cmd, text=True, capture_output=True)
    after = set(artifacts_dir.glob("*.zip"))
    candidates = list(after - before) or sorted(artifacts_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    generated = candidates[-1] if candidates else None

    if proc.returncode != 0:
        _log_block(args, "pack", "ERROR", proc.stdout + proc.stderr)
        raise SystemExit(proc.stdout + proc.stderr)
    if not generated or not generated.exists():
        _log(args, "pack", "FAILED: pack succeeded but generated zip was not found", level="ERROR")
        m = re.search(r"(?m)^\s*path:\s*(\S+)\s*$", proc.stdout + "\n" + proc.stderr)
        generated = Path(m.group(1)) if m else None
    if not generated or not generated.exists():
        raise SystemExit("pack succeeded but generated zip was not found")

    if generated.resolve() != target.resolve():
        if target.exists():
            target.unlink()
        shutil.move(str(generated), str(target))

    max_mb = float(getattr(args, "max_artifact_mb", DEFAULT_MAX_ARTIFACT_MB) or 0)
    if max_mb > 0 and target.stat().st_size > max_mb * 1024 * 1024:
        raise SystemExit(f"artifact too large: {target.stat().st_size} bytes > {max_mb} MiB; refusing to keep polluted baseline artifact")

    dry_run = {"ok": None, "skipped": True, "reason": "disabled"}
    if getattr(args, "pack_dry_run", DEFAULT_PACK_DRY_RUN):
        dry_run = _deploy_dry_run(args, target, log_step="pack", reason=f"{artifact_kind} candidate artifact preflight")
        (artifacts_dir / "deploy_dry_run_report.json").write_text(json.dumps(dry_run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not dry_run.get("ok"):
            raise SystemExit(f"{artifact_kind} candidate artifact deploy dry-run failed: {_deploy_dry_run_error(dry_run)}")

    _log(args, "pack", f"done: {target.name} ({target.stat().st_size} bytes)")
    result = {
        "schema_version": "evolution.pack_report.v1",
        "status": "success",
        "artifactPath": str(target),
        "artifactName": target.name,
        # artifactKind describes the candidate's intended asset role; the
        # authoritative lifecycle state is promotion_status.
        "artifactKind": artifact_kind,
        "promotionStatus": promotion_status,
        "size": target.stat().st_size,
        "sha256": _sha256(target),
        "exitCode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "deployDryRun": dry_run,
        "created_at": _now(),
    }
    (artifacts_dir / "pack_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _mark_step(args, "pack", "SUCCESS", result)
    _print_json(result)


# ═══════════════════════════════════════════════════════════════════════════════
# action: restore
# ═══════════════════════════════════════════════════════════════════════════════

def action_restore(args):
    paths = resolve_paths(args)
    round_dir = paths["round_dir"]
    skill_base = paths["skill_base"]

    acc_report = _require_acceptance_report(paths)
    accepted = acc_report["accepted"]
    restore_required = acc_report.get("restore_required", not accepted)

    if not restore_required:
        out = {"restored": False, "reason": acc_report.get("reason") or "restore not required", "decision": acc_report.get("decision"), "accepted": accepted}
        (round_dir / "acceptance" / "restore_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _log(args, "restore", f"skipped (restore_required=false, decision={acc_report.get('decision')})")
        _print_json(out)
        return

    state = _load_json(round_dir / "round_state.json", {})
    snapshot = state.get("rollback_snapshot") if isinstance(state, dict) else {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    rollback_path = str(snapshot.get("path") or "").strip()
    if snapshot.get("status") != "ready" or not rollback_path:
        raise SystemExit("applied_candidate_without_rollback_snapshot: current Round has no ready rollback Pack")
    rollback_artifact = Path(rollback_path)
    if not rollback_artifact.is_file():
        raise SystemExit(f"rollback snapshot not found: {rollback_artifact}")
    expected_sha = str(snapshot.get("sha256") or "")
    actual_sha = _sha256(rollback_artifact)
    if not expected_sha or expected_sha != actual_sha:
        raise SystemExit(
            f"rollback snapshot digest mismatch: expected={expected_sha or '<missing>'}, actual={actual_sha}"
        )

    script = find_script(skill_base, "clawevolve-deploy", "scripts/deploy.sh")
    compatibility = _assert_restore_runtime_compatible(rollback_artifact, script)

    precheck = {"ok": None, "skipped": True, "reason": "disabled"}
    if getattr(args, "restore_precheck", DEFAULT_RESTORE_PRECHECK):
        precheck = _deploy_dry_run(args, rollback_artifact, log_step="restore", reason="round rollback snapshot precheck")
        (round_dir / "acceptance" / "restore_precheck.json").write_text(json.dumps(precheck, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not precheck.get("ok"):
            raise SystemExit(f"round rollback snapshot precheck failed: {_deploy_dry_run_error(precheck)}")

    cmd = [
        "bash", str(script),
        "--image", str(rollback_artifact),
        "--workspace", args.workspace or paths["workspace"],
        "--skip-evolve-results",
        "--force-overwrite",
        "--evolve-run-id", paths["task_id"],
    ]
    _log(args, "restore", f"restoring from {rollback_artifact}")
    proc = subprocess.run(cmd, text=True, capture_output=True)
    result = {
        "restored": proc.returncode == 0,
        "restoreFrom": str(rollback_artifact),
        "exitCode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "precheck": precheck,
        "runtimeCompatibility": compatibility,
    }
    if proc.returncode != 0:
        _log_block(args, "restore", "ERROR", proc.stdout + proc.stderr)
        raise SystemExit(json.dumps(result, ensure_ascii=False))
    state["candidate_mutation_state"] = "restored"
    state["restore_status"] = "succeeded"
    (round_dir / "round_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 写 restore_result.json 供 _step_done 检查
    (round_dir / "acceptance" / "restore_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _log(args, "restore", "done")
    _print_json(result)


# ═══════════════════════════════════════════════════════════════════════════════
# action: upload-clawweb — 读 round_state + raw report 构建 payload
# ═══════════════════════════════════════════════════════════════════════════════

def _clawweb_report_result(proc) -> dict:
    if proc.returncode != 0:
        return {
            "status": "UPLOAD_FAILED", "exitCode": proc.returncode,
            "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:],
        }
    try:
        body = json.loads(proc.stdout)
    except Exception as exc:
        return {
            "status": "UPLOAD_FAILED", "error": f"invalid ClawWeb response: {type(exc).__name__}: {exc}",
            "exitCode": proc.returncode, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:],
        }
    if not isinstance(body, dict) or body.get("ok") is not True:
        detail = body.get("error") or body.get("detail") or "missing ok=true" if isinstance(body, dict) else "invalid JSON object"
        return {
            "status": "UPLOAD_FAILED", "error": f"clawweb API rejected: {detail}",
            "exitCode": proc.returncode, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:],
        }
    return {
        "status": "SUCCESS", "exitCode": proc.returncode,
        "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:],
    }


def _accepted_baseline_provenance(
    args, state: dict, *, role: str,
) -> dict:
    """Capture the immutable Bench source fields required by later rounds.

    An accepted candidate becomes the next round's Baseline.  Its score and
    evaluation identity are not sufficient for the ClawWeb contract: the
    producer Step, Bench Run and resource ownership must survive promotion as
    first-class registry fields as well.
    """

    bench_key = "optimization" if role == "train" else "validation"
    bench_record = ((state.get("bench") or {}).get(bench_key) or {})
    identity = bench_record.get("identity") or {}
    domain_arg = "train_bench_domain_id" if role == "train" else "test_bench_domain_id"
    return {
        "bench_run_id": str(bench_record.get("benchRunId") or ""),
        "producer_step_id": str(
            bench_record.get("producerStepId")
            or getattr(args, "step_id", "")
            or state.get("step_id")
            or ""
        ),
        "domain_id": str(
            bench_record.get("domainId")
            or identity.get("domain_id")
            or getattr(args, domain_arg, "")
            or ""
        ),
        "domain_owner_id": str(
            bench_record.get("domainOwnerId")
            or bench_record.get("ownerUserId")
            or identity.get("domain_owner_id")
            or getattr(args, "owner_id", "")
            or ""
        ),
    }


def _validate_accepted_baseline_provenance(role: str, value: dict) -> str:
    required = ("bench_run_id", "producer_step_id", "domain_id", "domain_owner_id")
    missing = [key for key in required if not str(value.get(key) or "").strip()]
    if missing:
        return f"accepted {role} Baseline provenance missing: {', '.join(missing)}"
    return ""


def _validate_clawweb_baseline_output(baseline: object) -> str:
    if not isinstance(baseline, dict):
        return "baseline must be an object"
    required = ("role", "producerStepId", "ownerUserId", "domainId", "benchRunId", "metrics")
    for role in ("train", "test"):
        value = baseline.get(role)
        if not isinstance(value, dict):
            return f"baseline.{role} must be an object"
        missing = []
        for key in required:
            field = value.get(key)
            if key == "metrics":
                if not isinstance(field, dict):
                    missing.append(key)
            elif not isinstance(field, str) or not field.strip():
                missing.append(key)
        if missing:
            return f"baseline.{role} missing required fields: {', '.join(missing)}"
        if value.get("role") != role:
            return f"baseline.{role}.role must equal {role}"
        if value.get("source") not in {"generated", "reused"}:
            return f"baseline.{role}.source must be generated or reused"
    return ""


def action_upload_clawweb(args):
    paths = resolve_paths(args)
    round_dir = paths["round_dir"]
    task_id = paths["task_id"]
    round_id = args.round

    state = _load_json(round_dir / "round_state.json", {})
    step_id = args.step_id or state.get("step_id", "")

    upload_dir = round_dir / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)

    acceptance = _require_acceptance_report(paths)
    bench = state.get("bench") or {}
    opt_summary = (bench.get("optimization") or {}).get("summary") or {}
    val_summary = (bench.get("validation") or {}).get("summary") or {}

    spec_path = round_dir / "spec" / f"spec-v{round_id}.md"
    tune_report = _read_text(round_dir / "tune" / "tune_report.md", 120000)
    spec_report = _read_text(round_dir / "spec" / "spec_update_report.md", 120000)

    accepted = acceptance.get("accepted")
    decision = str(acceptance.get("decision") or ("accepted" if accepted else "rejected" if accepted is False else "completed"))
    validation_score = val_summary.get("score")
    optimization_score = opt_summary.get("score")
    baseline_optimization = state.get("baseline_optimization") if isinstance(state.get("baseline_optimization"), dict) else {}
    baseline_validation = state.get("baseline_validation") if isinstance(state.get("baseline_validation"), dict) else {}

    objective_criterion = _objective_completion_criterion(args, paths)
    stop, stop_reason = _should_stop_for_objective(
        accepted=accepted is True,
        validation_score=validation_score,
        criterion=objective_criterion,
    )
    target_score = objective_criterion.get("target_score")
    reason = acceptance.get("reason") or stop_reason
    try:
        if stop:
            reason = f"已接受候选的测试 Bench 分数达到 objective 目标 {float(target_score):.3f}，停止优化"
        elif round_id >= int(getattr(args, "max_rounds", 0) or 0):
            reason = f"测试 Bench 分数为 {float(validation_score):.3f}，已到最后一轮"
        elif round_id == int(getattr(args, "max_rounds", 0) or 0) - 1:
            reason = f"测试 Bench 分数为 {float(validation_score):.3f}，继续最后一轮"
        elif accepted is not True:
            reason = f"候选未被接受（{decision}），继续优化"
        elif objective_criterion.get("automatic_stop"):
            reason = f"已接受候选尚未达到 objective 目标 {float(target_score):.3f}，继续优化"
        else:
            reason = "objective 未提供可机器验证的完成阈值，继续至最大轮次或由外部调度终止"
    except Exception:
        stop = False
        reason = acceptance.get("reason") or stop_reason

    max_rounds = int(getattr(args, "max_rounds", 0) or 0)
    next_action = "停止优化" if stop else ("已到最后一轮" if max_rounds and round_id >= max_rounds else "继续最后一轮" if max_rounds and round_id == max_rounds - 1 else "继续下一轮")

    changed_files = _parse_changed_files_file(round_dir / "tune" / "changed_files.txt")[:100]

    diff_summary = f"第 {round_id} 轮调优变更"
    if tune_report:
        for line in tune_report.splitlines():
            t = line.strip(" -#\t")
            if t:
                diff_summary = t[:200]
                break

    spec_content = spec_report or _read_text(spec_path, 120000)

    train_score = float(optimization_score) if optimization_score is not None else 0.0
    test_score = float(validation_score) if validation_score is not None else 0.0
    score_target_passed = (lambda score: score >= target_score) if target_score is not None else (lambda score: None)
    primary_metric = objective_criterion["primary_metric"]
    metric_key = primary_metric["name"]
    metric_name = primary_metric["display_name"]
    metric_unit = primary_metric["unit"]
    baseline_train_score = _finite_number((baseline_optimization.get("summary") or {}).get("score"))
    baseline_test_score = _finite_number((baseline_validation.get("summary") or {}).get("score"))
    owner_id = str(getattr(args, "owner_id", "") or "")
    metrics_list = [
        {"key": metric_key, "name": f"训练 Bench {metric_name}", "role": "candidate_train", "value": train_score, "unit": metric_unit, "target": target_score, "passed": score_target_passed(train_score), "baselineValue": baseline_train_score, "delta": train_score - baseline_train_score if baseline_train_score is not None else None, "ownerUserId": owner_id, "domainId": str(getattr(args, "train_bench_domain_id", "") or ""), "benchRunId": (bench.get("optimization") or {}).get("benchRunId") or ""},
        {"key": metric_key, "name": f"测试 Bench {metric_name}", "role": "candidate_test", "value": test_score, "unit": metric_unit, "target": target_score, "passed": score_target_passed(test_score), "baselineValue": baseline_test_score, "delta": test_score - baseline_test_score if baseline_test_score is not None else None, "ownerUserId": owner_id, "domainId": str(getattr(args, "test_bench_domain_id", "") or ""), "benchRunId": (bench.get("validation") or {}).get("benchRunId") or ""},
    ]

    def baseline_output(role: str, registry: dict, cache_record: dict) -> dict:
        summary = registry.get("summary") if isinstance(registry.get("summary"), dict) else {}
        producer_step_id = str(registry.get("producer_step_id") or "")
        return {
            "role": role,
            "producerStepId": producer_step_id,
            "source": "generated" if producer_step_id == str(step_id) else "reused",
            "cacheStatus": str(cache_record.get("cacheStatus") or ""),
            "ownerUserId": str(registry.get("domain_owner_id") or owner_id),
            "domainId": str(registry.get("domain_id") or getattr(args, "train_bench_domain_id" if role == "train" else "test_bench_domain_id", "") or ""),
            "benchRunId": str(registry.get("bench_run_id") or ""),
            "metrics": {
                "score": summary.get("score"),
                "maxScore": 1,
                "passRate": summary.get("pass_rate"),
                "caseCount": summary.get("total"),
            },
        }

    payload = {
        "status": "succeeded" if decision != "failed" else "failed",
        "summary": f"第 {round_id} 轮优化完成，{next_action}",
        "output": {
            "diff": {"summary": diff_summary, "files": changed_files},
            "metrics": metrics_list,
            # Additive fields for new UI rendering. Existing Bot/ClawWeb fields
            # above remain unchanged for protocol compatibility.
            "benchDecision": acceptance.get("bench_decision") or decision,
            "accepted": accepted is True,
            "promotionStatus": str(acceptance.get("promotion_status") or "not_started"),
            "reviewStatus": state.get("review_status") or acceptance.get("review_status") or "pending",
            "scoreComparison": {
                "name": "test_score",
                "baseline": baseline_test_score,
                "candidate": validation_score,
                "delta": validation_score - baseline_test_score if baseline_test_score is not None and validation_score is not None else None,
            },
            "baseline": {
                "train": baseline_output("train", baseline_optimization, bench.get("baseline_optimization") or {}),
                "test": baseline_output("test", baseline_validation, bench.get("baseline_validation") or {}),
            },
            "spec": {"version": f"v{round_id}", "content_type": "text", "content": spec_content},
            "roundDecision": {"stop": stop, "reason": reason, "objectiveCompletion": objective_criterion},
        },
    }
    oss_manifest = _load_json(upload_dir / "oss_manifest.json", {})
    round_manifest = oss_manifest.get("manifest") or {}
    round_objects = round_manifest.get("objects") or {}
    if oss_manifest.get("status") == "SUCCESS":
        payload["output"]["diff"]["artifact"] = round_objects.get("diff")
        payload["output"]["pack"] = {
            "status": "available" if round_objects.get("artifact") else "unchanged",
            "artifact": round_objects.get("artifact"),
            "effectiveArtifact": round_manifest.get("effectiveArtifact"),
        }
        payload["output"]["roundArtifacts"] = {
            "manifestRef": oss_manifest.get("manifestRef"),
            "schemaVersion": round_manifest.get("schemaVersion"),
        }
        baseline_artifact = (_load_json(round_dir / "round_state.json", {}).get("baselineArtifact") or {})
        if baseline_artifact:
            payload["output"]["baselineArtifact"] = baseline_artifact

    baseline_contract_error = _validate_clawweb_baseline_output(
        payload.get("output", {}).get("baseline")
    )
    if baseline_contract_error:
        # A reachable ClawWeb must receive a terminal failure instead of an
        # invalid Optimize output that is rejected with 422 and leaves the
        # Step looking perpetually running.
        payload = {
            "status": "failed",
            "summary": "Optimize 结果上报失败",
            "error": {
                "code": "OPTIMIZE_REPORT_CONTRACT_FAILED",
                "message": baseline_contract_error,
                "retryable": False,
            },
        }

    manifest_path = upload_dir / "clawweb_manifest.json"

    clawweb_url = (
        args.clawweb_url or args.clawweb_url_camel
        or _env("CLAWEVOLVE_CLAWWEB_URL") or _env("CLAWWEB_URL")
        or "http://127.0.0.1:5173/"
    ).rstrip("/")

    report_url = ""
    if clawweb_url and task_id and step_id:
        report_url = (
            clawweb_url + "/api/evolve/internal/tasks/"
            + urllib.parse.quote(task_id, safe="")
            + "/steps/"
            + urllib.parse.quote(step_id, safe="")
            + "/report"
        )

    manifest = {
        "schema_version": "evolution.clawweb_report_manifest.v0",
        "task_id": task_id, "step_id": step_id, "round_id": round_id,
        "status": "PENDING", "clawweb_url": clawweb_url, "url": report_url,
        "created_at": _now(),
    }
    if baseline_contract_error:
        manifest["reported_terminal_failure"] = True
        manifest["contract_error"] = baseline_contract_error

    if not task_id or not step_id:
        manifest.update({"status": "SKIPPED", "reason": "missing task_id or step_id"})
        _log(args, "upload-clawweb", "SKIPPED: missing task_id or step_id")
    elif not clawweb_url:
        manifest.update({"status": "UPLOAD_FAILED", "reason": "missing evolve ClawWeb URL"})
        _log(args, "upload-clawweb", "UPLOAD_FAILED: missing evolve ClawWeb URL", level="ERROR")
    else:
        cmd = [
            "curl", "--noproxy", "*", "-sS", "--fail-with-body", "--retry", "4", "--retry-delay", "3", "--retry-all-errors", "-X", "POST",
            report_url, "-H", "Content-Type: application/json",
            "--data-raw", json.dumps(payload, ensure_ascii=False),
        ]
        _log(args, "upload-clawweb", f"POST {report_url}")
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=180)
            manifest.update(_clawweb_report_result(proc))
        except Exception as exc:
            manifest.update({"status": "UPLOAD_FAILED", "error": f"{type(exc).__name__}: {exc}"})

    manifest["finished_at"] = _now()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _log(args, "upload-clawweb", f"status={manifest.get('status')}")
    _print_json(manifest)


# ═══════════════════════════════════════════════════════════════════════════════
# action: upload-oss
# ═══════════════════════════════════════════════════════════════════════════════

def action_upload_oss(args):
    """Compatibility action: publish the constrained current-round artifact set."""
    return action_publish_round_oss(args)


def action_publish_round_oss(args):
    """Publish the constrained round artifact set under evolution/{taskId}/."""
    paths = resolve_paths(args)
    task_id, round_id = paths["task_id"], args.round
    round_dir, upload_dir = paths["round_dir"], paths["upload_dir"]
    upload_dir.mkdir(parents=True, exist_ok=True)
    client = _artifact_client(args)
    acceptance = _require_acceptance_report(paths)
    candidate_selected = _candidate_passed_bench(acceptance)
    promotion_status = str(acceptance.get("promotion_status") or "not_started")
    if candidate_selected and promotion_status == "failed":
        skipped = {
            "status": "SKIPPED_PROMOTION_FAILED",
            "reason": "candidate promotion already failed before OSS publish",
            "createdAt": _now(),
        }
        (upload_dir / "oss_manifest.json").write_text(
            json.dumps(skipped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _mark_step(args, "upload-oss", "SKIPPED", skipped)
        _print_json(skipped)
        return
    candidates = [
        ("diff", "round-diff", round_dir / "tune" / "diff.patch", "text/x-diff; charset=utf-8"),
        ("changedFiles", "round-changed-files", round_dir / "tune" / "changed_files.txt", "application/json"),
        ("tuneReport", "round-tune-report", round_dir / "tune" / "tune_report.md", "text/markdown; charset=utf-8"),
        ("spec", "round-spec", round_dir / "spec" / f"spec-v{round_id}.md", "text/markdown; charset=utf-8"),
        ("acceptance", "round-acceptance", round_dir / "acceptance" / "acceptance_report.json", "application/json"),
    ]
    changed_json = upload_dir / "changed-files.json"
    changed_json.write_text(json.dumps(_parse_changed_files_file(round_dir / "tune" / "changed_files.txt"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    candidates[1] = ("changedFiles", "round-changed-files", changed_json, "application/json")
    objects = {}
    for kind, upload_kind, local_path, content_type in candidates:
        if not local_path.exists():
            raise SystemExit(f"required round artifact not found: {local_path}")
        objects[kind] = client.upload(upload_kind, local_path, content_type, round_id)
    pack_report = _load_json(round_dir / "artifacts" / "pack_report.json", {})
    pack_available = str(pack_report.get("status") or "").lower() == "success"
    artifact_path = Path(pack_report.get("artifactPath") or "") if pack_available else None
    if pack_available:
        if not artifact_path or not artifact_path.is_file():
            raise SystemExit("candidate Pack report exists but artifact is missing")
        objects["artifact"] = client.upload("round-pack", artifact_path, "application/zip", round_id)
    state = _load_json(round_dir / "round_state.json", {})
    run_manifest = _load_json(paths["optimize_output_dir"] / "optimize_manifest.json", {})
    prior_artifact = _accepted_artifact_record(run_manifest)
    initial_artifact = ((state.get("baselineArtifact") or {}).get("artifact") or {})
    effective_artifact = objects.get("artifact") if candidate_selected else (prior_artifact or initial_artifact)
    baseline_ref = (effective_artifact or {}).get("ref")
    manifest = {
        "schemaVersion": "clawevolve.round-artifacts.v1",
        "identity": {"taskId": task_id, "stepId": args.step_id or _env("STEP_ID"), "round": round_id},
        "decision": {
            "accepted": False,
            "benchDecision": _acceptance_bench_decision(acceptance),
            "promotionStatus": promotion_status,
            "type": acceptance.get("decision") or ("passed" if candidate_selected else "rejected"),
        },
        "objects": objects,
        "baselineArtifactRef": baseline_ref,
        "effectiveArtifact": effective_artifact or None,
        "createdAt": _now(),
    }
    manifest_path = upload_dir / "round-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_artifact = client.upload("round-manifest", manifest_path, "application/json", round_id)
    manifest_ref = manifest_artifact["ref"]
    local_status = {"status": "SUCCESS", "manifestRef": manifest_ref, "manifest": manifest}
    (upload_dir / "oss_manifest.json").write_text(json.dumps(local_status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _mark_step(args, "upload-oss", "SUCCESS", {"manifestRef": manifest_ref})
    _print_json(local_status)


# ═══════════════════════════════════════════════════════════════════════════════
# action: complete — 读 round_state 更新 manifest
# ═══════════════════════════════════════════════════════════════════════════════

def _append_experiment_ledger(paths: dict, round_id: int, state: dict, acceptance: dict) -> str:
    decision = _load_json(paths["spec_dir"] / "review_decision.normalized.json", {})
    manifest = state.get("change_manifest") if isinstance(state.get("change_manifest"), dict) else {}
    quality = (((state.get("candidate_gate") or {}).get("manifest_quality") or {}))
    assessment, _ = _normalize_hypothesis_assessment(manifest.get("spec_hypothesis_assessment"))
    item = {
        "schema_version": "evolution.experiment_ledger_entry.v1",
        "round_id": round_id,
        "selected_proposal_id": manifest.get("selected_proposal_id"),
        "selected_operator": quality.get("selected_operator"),
        "selected_operator_family": quality.get("selected_operator_family"),
        "spec_hypothesis_assessment": assessment,
        "effect_design": manifest.get("effect_design") if isinstance(manifest.get("effect_design"), dict) else {},
        "complexity_budget": manifest.get("complexity_budget") if isinstance(manifest.get("complexity_budget"), dict) else {},
        "preserved_mechanisms": manifest.get("preserved_mechanisms") if isinstance(manifest.get("preserved_mechanisms"), list) else [],
        "candidate_opt_gate": {
            "valid": ((state.get("candidate_opt_gate") or {}).get("valid")),
            "effect_gate_passed": ((state.get("candidate_opt_gate") or {}).get("effect_gate_passed")),
            "protected_gate_passed": ((state.get("candidate_opt_gate") or {}).get("protected_gate_passed")),
            "expected_results": ((state.get("candidate_opt_gate") or {}).get("expected_results") or []),
            "protected_results": ((state.get("candidate_opt_gate") or {}).get("protected_results") or []),
        },
        "full_opt_breadth": state.get("full_opt_gate") or {},
        "validation": {"decision": acceptance.get("decision"), "score": (((state.get("bench") or {}).get("validation") or {}).get("summary") or {}).get("score")},
        "acceptance": {"accepted": acceptance.get("accepted"), "decision": acceptance.get("decision"), "reason": acceptance.get("reason")},
        "artifact_ref": state.get("artifact") or "",
        "baseline_ref": state.get("baseline_artifact") or "",
        "hypotheses": decision.get("hypotheses") if isinstance(decision, dict) else [],
        "hypothesis_status_changes": [{"failure_signature": item.get("failure_signature"), "status": item.get("status")} for item in (decision.get("hypotheses") or []) if isinstance(item, dict)],
        "created_at": _now(),
    }
    path = paths["optimize_output_dir"] / "experiment_ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_rounds = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                existing_rounds.add(int(json.loads(line).get("round_id")))
            except Exception:
                pass
    if round_id not in existing_rounds:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return str(path)


def _write_json_projection(path: Path, payload: dict) -> None:
    """Replace a derived JSON projection without exposing a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.pending")
    pending.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(pending, path)


def _finalize_accepted_round_projections(args, paths: dict, acceptance: dict) -> dict:
    """Converge post-Promotion projections without changing the decision.

    Pack publication happens before ``complete`` so the first OSS manifest is a
    pending projection.  Once ``complete`` has durably committed Promotion, this
    best-effort finalizer updates only derived metadata.  A projection upload
    failure must never revoke an already accepted Candidate.
    """
    if acceptance.get("accepted") is not True or str(
        acceptance.get("promotion_status") or ""
    ) != "succeeded":
        return {"status": "skipped", "reason": "round is not accepted"}

    round_dir = Path(paths["round_dir"])
    upload_dir = Path(paths["upload_dir"])
    warnings: list[str] = []

    pack_report_path = round_dir / "artifacts" / "pack_report.json"
    pack_report = _load_json(pack_report_path, {})
    if isinstance(pack_report, dict) and str(pack_report.get("status") or "").lower() == "success":
        pack_report["artifactKind"] = "accepted"
        pack_report["promotionStatus"] = "succeeded"
        pack_report.setdefault("finalizedAt", _now())
        _write_json_projection(pack_report_path, pack_report)
    else:
        warnings.append("accepted round pack_report is unavailable for finalization")

    oss_manifest_path = upload_dir / "oss_manifest.json"
    oss_manifest = _load_json(oss_manifest_path, {})
    round_manifest = oss_manifest.get("manifest") if isinstance(oss_manifest, dict) else None
    if not isinstance(round_manifest, dict):
        warnings.append("accepted round OSS manifest is unavailable for finalization")
    else:
        decision = round_manifest.get("decision")
        if not isinstance(decision, dict):
            decision = {}
        decision.update({
            "accepted": True,
            "benchDecision": _acceptance_bench_decision(acceptance),
            "promotionStatus": "succeeded",
            "type": acceptance.get("decision") or "passed",
        })
        round_manifest["decision"] = decision
        objects = round_manifest.get("objects") if isinstance(round_manifest.get("objects"), dict) else {}
        artifact = objects.get("artifact") if isinstance(objects.get("artifact"), dict) else None
        if artifact:
            round_manifest["effectiveArtifact"] = artifact
            round_manifest["baselineArtifactRef"] = artifact.get("ref")
        round_manifest.setdefault("finalizedAt", _now())
        round_manifest_path = upload_dir / "round-manifest.json"
        _write_json_projection(round_manifest_path, round_manifest)
        oss_manifest["manifest"] = round_manifest
        oss_manifest.setdefault("finalizedAt", _now())
        if not getattr(args, "skip_oss", False) and str(
            oss_manifest.get("status") or ""
        ).upper() == "SUCCESS":
            try:
                uploaded = _artifact_client(args).upload(
                    "round-manifest", round_manifest_path, "application/json", args.round
                )
                uploaded_ref = str(uploaded.get("ref") or "").strip()
                if uploaded_ref:
                    oss_manifest["manifestRef"] = uploaded_ref
                    oss_manifest["finalManifest"] = uploaded
                else:
                    warnings.append("final round-manifest upload returned no artifact ref")
            except Exception as exc:
                warning = f"final round-manifest upload failed: {type(exc).__name__}: {exc}"
                warnings.append(warning)
                _log(args, "complete", warning, "WARN")
        if warnings:
            oss_manifest["finalizationWarnings"] = warnings
        else:
            oss_manifest.pop("finalizationWarnings", None)
        _write_json_projection(oss_manifest_path, oss_manifest)

    return {
        "status": "warning" if warnings else "success",
        "warnings": warnings,
    }

def action_complete(args):
    paths = resolve_paths(args)
    run_dir = paths["run_dir"]
    round_dir = paths["round_dir"]
    round_id = args.round
    task_id = paths["task_id"]

    state = _load_json(round_dir / "round_state.json", {})
    bench = state.get("bench") or {}
    val_bench = bench.get("validation") or {}
    opt_bench = bench.get("optimization") or {}
    val_summary = val_bench.get("summary") or {}
    opt_summary = opt_bench.get("summary") or {}
    acceptance = _require_acceptance_report(paths)
    upload = _load_json(round_dir / "upload" / "oss_manifest.json", {})
    clawweb_upload = _load_json(round_dir / "upload" / "clawweb_manifest.json", {})

    candidate_selected = _candidate_passed_bench(acceptance)
    promotion_status = str(acceptance.get("promotion_status") or "not_started")
    if candidate_selected and promotion_status == "failed":
        raise SystemExit("candidate promotion failed; Round rollback has been required")
    accepted = candidate_selected
    score = val_summary.get("score")
    opt_score = opt_summary.get("score")

    pack_report = _load_json(round_dir / "artifacts" / "pack_report.json", {})
    default_artifact = str(round_dir / "artifacts" / f"artifact_v{round_id}.zip")
    artifact_path = args.artifact_path or (pack_report.get("artifactPath") if isinstance(pack_report, dict) and pack_report.get("status") != "skipped" else "") or (default_artifact if Path(default_artifact).exists() else "")
    if candidate_selected:
        artifact = Path(str(artifact_path or ""))
        if not artifact_path or not artifact.is_file():
            raise SystemExit(f"Bench-passed round cannot complete without artifact: {artifact_path or '<missing>'}")
        pack_status = str(pack_report.get("status") or "").lower() if isinstance(pack_report, dict) else ""
        if pack_status != "success":
            raise SystemExit(f"Bench-passed round pack status is not success: {pack_status or '<missing>'}")
        reported_path = str(pack_report.get("artifactPath") or "")
        if not reported_path or Path(reported_path).resolve() != artifact.resolve():
            raise SystemExit("Bench-passed round artifact does not match pack report")
        expected_sha = str(pack_report.get("sha256") or "")
        actual_sha = _sha256(artifact)
        if not expected_sha or expected_sha != actual_sha:
            raise SystemExit("Bench-passed round artifact hash does not match pack report")
        if not getattr(args, "skip_oss", False):
            upload_status = str(upload.get("status") or "").upper() if isinstance(upload, dict) else ""
            uploaded_artifact = (((upload.get("manifest") or {}).get("objects") or {}).get("artifact") or {}) if isinstance(upload, dict) else {}
            if upload_status != "SUCCESS" or not uploaded_artifact:
                raise SystemExit(
                    f"Bench-passed round cannot complete without published artifact: "
                    f"upload_status={upload_status or '<missing>'}"
                )

    # Build the final acceptance view in memory. It is persisted only after the
    # Accepted registry manifest has been committed below.
    acceptance["promotion_status"] = "succeeded" if accepted else "not_started"
    acceptance["accepted"] = accepted
    acceptance["promote_to_baseline"] = accepted
    acceptance["restore_required"] = False if accepted else bool(acceptance.get("restore_required"))
    acceptance["promotion_updated_at"] = _now()

    manifest_path = run_dir / "optimize" / "output" / "optimize_manifest.json"
    manifest = _load_json(manifest_path, {
        "schema_version": "evolution.run_manifest.v0",
        "task_id": task_id, "evolve_run_id": task_id, "rounds": [],
    })
    manifest["task_id"] = task_id
    manifest["evolve_run_id"] = task_id

    validation_task_scores = _task_scores_from_report(val_bench.get("resultPath", "")) if val_bench.get("resultPath") else {}

    record = {
        "round_id": round_id,
        "accepted": accepted,
        "promotion_status": "succeeded" if accepted else "not_started",
        "validation_score": score,
        "optimization_score": opt_score,
        "optimization_result_path": opt_bench.get("resultPath", ""),
        "validation_result_path": val_bench.get("resultPath", ""),
        "validation_task_scores": validation_task_scores,
        "artifact": artifact_path,
        "decision": acceptance.get("decision"),
        "bench_decision": acceptance.get("bench_decision") or acceptance.get("decision"),
        "review_status": state.get("review_status") or acceptance.get("review_status") or "pending",
        "score": acceptance.get("score") or {},
        "restore_required": acceptance.get("restore_required"),
        "change_summary": acceptance.get("change_summary") or state.get("change_summary"),
        "identity": state.get("identity"),
        "spec": str(round_dir / "spec" / f"spec-v{round_id}.md"),
        "spec_json": str(round_dir / "spec" / f"spec-v{round_id}.json"),
        "oss_status": upload.get("status"),
        "clawweb_status": clawweb_upload.get("status"),
        "updated_at": _now(),
    }
    manifest.setdefault("rounds", [])
    manifest["rounds"] = [r for r in manifest["rounds"] if r.get("round_id") != round_id]
    manifest["rounds"].append(record)
    manifest["rounds"] = sorted(manifest["rounds"], key=lambda r: r.get("round_id", 0))

    if acceptance.get("decision") == "exploratory_keep":
        retention = ((acceptance.get("exploration") or {}).get("retention") or {})
        if retention:
            manifest.setdefault("retained_candidates", [])
            manifest["retained_candidates"] = [r for r in manifest["retained_candidates"] if r.get("round_id") != round_id]
            manifest["retained_candidates"].append({
                "round_id": round_id,
                "expires_after_round": retention.get("expires_after_round"),
                "status": retention.get("status", "active"),
                "score_delta": retention.get("score_delta"),
                "candidate_summary": retention.get("candidate_summary", ""),
                "next_round_plan": retention.get("next_round_plan", ""),
            })
    if accepted:
        published_artifact = ((upload.get("manifest") or {}).get("objects") or {}).get("artifact") or {}
        manifest["last_accepted_round"] = round_id
        manifest["last_accepted_validation_score"] = score
        manifest["last_accepted_artifact"] = {"localPath": artifact_path, **published_artifact}
        manifest["last_accepted_spec"] = record["spec"]
        manifest["last_accepted_identity"] = record.get("identity") or {}
        manifest["last_accepted_identity_complete"] = _eval_identity_complete(record.get("identity") or {})
        manifest["last_accepted_validation_result_path"] = record.get("validation_result_path", "")
        manifest["last_accepted_validation_task_scores"] = validation_task_scores
        candidate_opt_identity = _evaluation_identity(
            args, paths, kind="optimization", artifact_path=artifact_path,
            report_path=record.get("optimization_result_path", ""),
        )
        candidate_val_identity = _evaluation_identity(
            args, paths, kind="validation", artifact_path=artifact_path,
            report_path=record.get("validation_result_path", ""),
        )
        optimization_provenance = _accepted_baseline_provenance(args, state, role="train")
        validation_provenance = _accepted_baseline_provenance(args, state, role="test")
        provenance_errors = [
            error for error in (
                _validate_accepted_baseline_provenance("train", optimization_provenance),
                _validate_accepted_baseline_provenance("test", validation_provenance),
            ) if error
        ]
        if provenance_errors:
            raise SystemExit("; ".join(provenance_errors))
        manifest["accepted_baseline_optimization"] = {
            "schema_version": "evolution.baseline_optimization.v1",
            "source": "accepted_candidate_full_opt", "round_id": round_id,
            "artifact_path": artifact_path, "artifact_sha256": _sha256(Path(artifact_path)) if artifact_path and Path(artifact_path).is_file() else "",
            "result_path": record.get("optimization_result_path", ""), "summary": opt_summary,
            "task_scores": _task_scores_from_report(record.get("optimization_result_path", "")),
            "identity": candidate_opt_identity, "promoted_at": _now(),
            **optimization_provenance,
        }
        manifest["accepted_baseline_validation"] = {
            "schema_version": "evolution.baseline_validation.v1", "round_id": round_id,
            "result_path": record.get("validation_result_path", ""), "summary": val_summary,
            "task_scores": validation_task_scores, "identity": candidate_val_identity, "promoted_at": _now(),
            **validation_provenance,
        }
        manifest["baseline_registry"] = {
            "schema_version": "evolution.baseline_registry.v1", "round_id": round_id,
            "artifact": manifest["last_accepted_artifact"],
            "optimization": manifest["accepted_baseline_optimization"],
            "validation": manifest["accepted_baseline_validation"],
            "promoted_at": _now(),
        }
    else:
        manifest["last_rejected_round"] = round_id
        manifest["last_rejected_validation_score"] = score
        manifest["last_rejected_artifact"] = artifact_path
        manifest["restored_from_artifact"] = ((state.get("rollback_snapshot") or {}).get("path") or "")

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    record["experiment_ledger"] = _append_experiment_ledger(paths, round_id, state, acceptance)
    previous_manifest = manifest_path.read_bytes() if manifest_path.is_file() else None
    pending_manifest = manifest_path.with_name(f".{manifest_path.name}.pending")
    pending_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(pending_manifest, manifest_path)

    try:
        round_state = _load_json(round_dir / "round_state.json", {})
        round_state.update(record)
        if accepted:
            round_state["candidate_mutation_state"] = "promoted"
        round_state["status"] = "ROUND_COMPLETED"
        round_state["acceptance"] = acceptance
        round_state["accepted"] = accepted
        round_state["promotion_status"] = acceptance.get("promotion_status")
        (paths["accept_dir"] / "acceptance_report.json").write_text(
            json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (round_dir / "round_state.json").write_text(
            json.dumps(round_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        rollback_manifest = manifest_path.with_name(f".{manifest_path.name}.rollback")
        if previous_manifest is None:
            manifest_path.unlink(missing_ok=True)
        else:
            rollback_manifest.write_bytes(previous_manifest)
            os.replace(rollback_manifest, manifest_path)
        raise

    try:
        finalization = _finalize_accepted_round_projections(args, paths, acceptance)
    except Exception as exc:
        # Final projections are observability metadata.  They must not turn an
        # already committed Promotion into a failed Optimize round.
        warning = f"accepted round projection finalization failed: {type(exc).__name__}: {exc}"
        finalization = {"status": "warning", "warnings": [warning]}
        _log(args, "complete", warning, "WARN")
    if finalization.get("warnings"):
        record["finalization_warnings"] = finalization["warnings"]

    _log(args, "complete", f"ROUND_COMPLETED, accepted={accepted}, val_score={score}")
    _print_json({"runManifest": str(manifest_path), "roundState": str(round_dir / "round_state.json"), **record})


# ═══════════════════════════════════════════════════════════════════════════════
# AI 节点 — openclaw agent --message
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_skill_md(skill_base: str, skill_name: str) -> str:
    path = Path(skill_base).expanduser().resolve() / skill_name / "SKILL.md"
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _agent_output_ready(path: Path) -> bool:
    """Return whether an expected agent output is usable."""
    try:
        if not path.exists() or not path.is_file():
            return False
        # diff.patch may legitimately be empty for a no-op/no-diff tune.
        if path.name == "diff.patch":
            return True
        return path.stat().st_size > 0
    except Exception:
        return False


def _agent_outputs_ready(paths: list[Path] | None) -> bool:
    return bool(paths) and all(_agent_output_ready(Path(p)) for p in paths)


def _agent_outputs_fingerprint(paths: list[Path] | None) -> tuple:
    items = []
    for pth in paths or []:
        path = Path(pth)
        try:
            st = path.stat()
            items.append((str(path), True, st.st_size, st.st_mtime_ns))
        except FileNotFoundError:
            items.append((str(path), False, 0, 0))
        except Exception as exc:
            items.append((str(path), "error", type(exc).__name__, str(exc)))
    return tuple(items)


def _collect_agent_output_with_timeout(
    proc: subprocess.Popen,
    timeout_seconds: int,
    idle_timeout: int = 0,
    *,
    completion_marker: str | None = None,
    expected_outputs: list[Path] | None = None,
    output_grace_seconds: int = 10,
    output_without_marker_seconds: int = 120,
) -> tuple[int, str, str, str | None, bool, str]:
    """Collect agent stdout and optionally finish once marker + outputs are stable."""
    q = queue.Queue()  # type: queue.Queue[bytes]

    def _reader():
        try:
            for line in iter(proc.stdout.readline, b""):
                if line:
                    q.put(line)
        except Exception as exc:
            try:
                q.put(f"[agent stdout reader error: {type(exc).__name__}: {exc}]".encode("utf-8", errors="replace"))
            except Exception:
                pass

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    started_at = time.time()
    last_data_at = started_at
    chunks: list[bytes] = []
    timed_out_on: str | None = None
    marker_found = False
    completed_by = "process_exit"
    forced_returncode: int | None = None
    stable_since: float | None = None
    stable_fingerprint: tuple | None = None

    while True:
        now = time.time()
        elapsed = now - started_at
        remaining_total = max(0.0, timeout_seconds - elapsed)
        max_poll_wait = 1.0 if expected_outputs else 5.0
        poll_wait = min(max_poll_wait, remaining_total) if remaining_total > 0 else 0.1
        try:
            chunk = q.get(timeout=poll_wait)
            chunks.append(chunk)
            last_data_at = time.time()
            if completion_marker and completion_marker.encode("utf-8", errors="replace") in b"".join(chunks[-20:]):
                marker_found = True
        except queue.Empty:
            pass

        if completion_marker and not marker_found:
            try:
                # Robust fallback for markers split across chunks/lines.
                marker_found = completion_marker in b"".join(chunks).decode("utf-8", errors="replace")
            except Exception:
                pass

        outputs_ready = _agent_outputs_ready(expected_outputs)
        if outputs_ready:
            fp = _agent_outputs_fingerprint(expected_outputs)
            if fp != stable_fingerprint:
                stable_fingerprint = fp
                stable_since = time.time()
            stable_for = time.time() - (stable_since or time.time())

            if marker_found and stable_for >= output_grace_seconds:
                completed_by = "marker_and_outputs_stable"
                forced_returncode = 0
                _kill_process_group(proc)
                break

            # Conservative fallback: outputs are stable, and the agent has been
            # quiet for a while even though it did not print the marker.
            if (
                not marker_found
                and stable_for >= output_without_marker_seconds
                and time.time() - last_data_at >= output_without_marker_seconds
            ):
                completed_by = "outputs_stable_without_marker"
                forced_returncode = 0
                _kill_process_group(proc)
                break
        else:
            stable_since = None
            stable_fingerprint = None

        if proc.poll() is not None and q.empty():
            completed_by = "process_exit"
            break

        if timeout_seconds and timeout_seconds > 0 and elapsed >= timeout_seconds:
            timed_out_on = "total_timeout"
            completed_by = "total_timeout"
            _kill_process_group(proc)
            break

        if _elapsed_timeout_exceeded(last_data_at, idle_timeout):
            timed_out_on = "idle_timeout"
            completed_by = "idle_timeout"
            _kill_process_group(proc)
            break

    while not q.empty():
        try:
            chunks.append(q.get_nowait())
        except queue.Empty:
            break

    stdout = b"".join(chunks).decode("utf-8", errors="replace")
    stderr = ""

    returncode = forced_returncode if forced_returncode is not None else proc.returncode
    if returncode is None:
        returncode = 124 if timed_out_on == "total_timeout" else -9

    if completion_marker and not marker_found:
        marker_found = completion_marker in stdout

    return returncode, stdout, stderr, timed_out_on, marker_found, completed_by


def _build_openclaw_agent_command(
    openclaw_path: str, agent_id: str, session_id: str, message: str,
    timeout_seconds: int, *, local: bool = True,
) -> list[str]:
    """Build the agent command with local execution and an explicit timeout.

    OpenClaw otherwise applies its own 600-second default, which used to race
    with this runner's former 600-second stdout-idle watchdog.
    """
    cmd = [openclaw_path, "agent"]
    if local:
        cmd.append("--local")
    cmd.extend([
        "--agent", agent_id, "--session-id", session_id,
        "--message", message, "--json",
    ])
    if timeout_seconds and timeout_seconds > 0:
        cmd.extend(["--timeout", str(int(timeout_seconds))])
    return cmd


def _archive_openclaw_agent_session(result: dict, archive_dir: Path) -> dict:
    """Persist the complete CLI envelope and raw transcript before later cleanup."""

    archive_dir.mkdir(parents=True, exist_ok=True)
    envelope_path = archive_dir / "agent_result.json"
    envelope = result.get("agentJson")
    envelope_path.write_text(
        json.dumps(envelope if isinstance(envelope, dict) else {}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    transcript_source = ""
    if isinstance(envelope, dict):
        meta = ((envelope.get("result") or {}).get("meta") or {})
        agent_meta = meta.get("agentMeta") or {}
        transcript_source = str(agent_meta.get("sessionFile") or "")
    transcript_path = archive_dir / "transcript.jsonl"
    transcript_archived = False
    if transcript_source and Path(transcript_source).is_file():
        shutil.copy2(transcript_source, transcript_path)
        transcript_archived = True
    record = {
        "agent_id": str(result.get("agentId") or ""),
        "session_id": str(result.get("sessionId") or ""),
        "agent_result_json": str(envelope_path),
        "transcript_source": transcript_source,
        "transcript_jsonl": str(transcript_path) if transcript_archived else "",
        "transcript_archived": transcript_archived,
    }
    (archive_dir / "archive.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return record


def _openclaw_agent_message(
    agent_id: str, message: str, workspace: Path, timeout_seconds: int,
    *, model: str = "", base_url: str = "", api_key: str = "",
    completion_marker: str | None = None, poll_interval: int = 10,
    expected_outputs: list[Path] | None = None,
    idle_timeout_seconds: int = DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS,
) -> dict:
    agent_workspace = workspace.resolve()
    agent_workspace.mkdir(parents=True, exist_ok=True)
    openclaw_path = os.environ.get("OPENCLAW_PATH", "openclaw")
    session_id = f"{agent_id}_{int(time.time() * 1000)}"

    agent_env = os.environ.copy()
    gw_token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
    if not gw_token:
        try:
            oc_home = os.environ.get("OPENCLAW_HOME") or os.path.expanduser("~/.openclaw")
            oc_cfg = json.loads(Path(oc_home, "openclaw.json").read_text(encoding="utf-8"))
            gw_token = (oc_cfg.get("gateway", {}).get("auth", {}).get("token") or "")
        except Exception:
            pass
    if gw_token:
        agent_env["OPENCLAW_GATEWAY_TOKEN"] = gw_token

    diag: dict = {}

    def _agent_in_list() -> bool:
        try:
            proc = subprocess.run(
                [openclaw_path, "agents", "list"],
                capture_output=True, text=True, timeout=30, env=agent_env, start_new_session=True,
            )
        except Exception as exc:
            diag["agentsListError"] = str(exc)
            return False
        diag["agentsListExitCode"] = proc.returncode
        diag["agentsListStdout"] = proc.stdout[-3000:] if proc.stdout else ""
        diag["agentsListStderr"] = proc.stderr[-3000:] if proc.stderr else ""
        if proc.returncode != 0:
            return False
        normalized = agent_id.replace(":", "-").lower()
        for line in proc.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                name = stripped[2:].split()[0] if stripped[2:].strip() else ""
                name_lower = name.lower()
                if agent_id.lower() == name_lower or normalized == name_lower:
                    return True
        return False

    bootstrap_before_add = _snapshot_openclaw_workspace_bootstrap(agent_workspace)

    already_exists = _agent_in_list()
    if already_exists:
        diag["agentRegistration"] = "already-exists"
    else:
        add_cmd = [openclaw_path, "agents", "add", agent_id]
        if model:
            add_cmd.extend(["--model", model])
        add_cmd.extend(["--workspace", str(agent_workspace), "--non-interactive"])
        if base_url:
            add_cmd.extend(["--base-url", base_url])
        if api_key:
            add_cmd.extend(["--api-key", api_key])
        diag["addCmd"] = " ".join(add_cmd)
        add_ok = False
        for attempt in (1, 2):
            try:
                add_proc = subprocess.run(add_cmd, capture_output=True, text=True, timeout=120, env=agent_env, start_new_session=True)
            except Exception as exc:
                diag[f"addAttempt{attempt}Error"] = str(exc)
                continue
            diag[f"addAttempt{attempt}ExitCode"] = add_proc.returncode
            diag[f"addAttempt{attempt}Stdout"] = add_proc.stdout[-3000:] if add_proc.stdout else ""
            diag[f"addAttempt{attempt}Stderr"] = add_proc.stderr[-3000:] if add_proc.stderr else ""
            if add_proc.returncode == 0:
                add_ok = True
                break
        if _agent_in_list():
            diag["agentRegistration"] = "created-ok" if add_ok else "created-tolerant(add-failed-but-listed)"
        else:
            return {"status": "failed", "error": f"Agent '{agent_id}' not found in registry after add attempt(s).", "diagnostics": diag}

    cleaned = _cleanup_openclaw_workspace_bootstrap_since_snapshot(
        agent_workspace, bootstrap_before_add, restore_existing=True,
    )
    if cleaned:
        diag["workspaceBootstrapCleanedAfterAdd"] = cleaned

    bootstrap_before_agent = _snapshot_openclaw_workspace_bootstrap(agent_workspace)

    started_at = time.time()
    _agent_cmd = _build_openclaw_agent_command(
        openclaw_path, agent_id, session_id, message, timeout_seconds,
    )
    diag["agentExecutionTransport"] = "local"
    diag["agentCommandTimeoutSeconds"] = timeout_seconds
    diag["agentIdleTimeoutSeconds"] = idle_timeout_seconds

    if not already_exists:
        sync_wait_seconds = 2
        time.sleep(sync_wait_seconds)
        diag["gatewaySyncWaitAfterAdd"] = sync_wait_seconds

    _message_returncode = 1
    _message_stdout = ""
    _message_stderr = ""
    max_message_attempts = 4
    for msg_attempt in range(1, max_message_attempts + 1):
        _attempt_started_at = time.time()
        _remaining_timeout = max(1, int(timeout_seconds - (_attempt_started_at - started_at)))

        msg_proc = subprocess.Popen(
            _agent_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=False,
            cwd=str(agent_workspace), env=agent_env, start_new_session=True,
        )
        _message_returncode, _message_stdout, _message_stderr, _timed_out_on, _marker_found_live, _completed_by = _collect_agent_output_with_timeout(
            msg_proc, _remaining_timeout, idle_timeout=idle_timeout_seconds,
            completion_marker=completion_marker,
            expected_outputs=expected_outputs,
        )
        diag["agentCompletedBy"] = _completed_by
        if _marker_found_live:
            diag["completionMarkerFoundLive"] = True

        changed = _detect_openclaw_workspace_bootstrap_changes_since_snapshot(
            agent_workspace, bootstrap_before_agent,
        )
        if changed:
            # Do not mutate after the agent runs: at this point new/modified
            # bootstrap md files may be intentional tune changes in the real
            # workspace.  We still report them for visibility.
            diag.setdefault("workspaceBootstrapChangedAfterRun", []).extend(changed)

        if _timed_out_on:
            diag["agentTimedOutOn"] = _timed_out_on
        if _timed_out_on == "idle_timeout":
            diag["agentIdleTimeout"] = True

        if _message_returncode == 0:
            break

        stderr_lower = (_message_stderr or "").lower()
        stdout_lower = (_message_stdout or "").lower()
        if "unknown agent id" in stderr_lower or "unknown agent id" in stdout_lower:
            if msg_attempt < max_message_attempts:
                wait = 3 * msg_attempt
                diag.setdefault("gatewayUnknownAgentRetries", []).append({
                    "attempt": msg_attempt, "waitSeconds": wait, "at": _now(),
                })
                time.sleep(wait)
                continue
        break

    elapsed = time.time() - started_at

    class _ProcResult:
        __slots__ = ("returncode", "stdout", "stderr")
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr
    proc = _ProcResult(_message_returncode, _message_stdout, _message_stderr)

    agent_json = None
    try:
        agent_json = json.loads(proc.stdout)
    except Exception:
        pass

    marker_found = None
    if completion_marker:
        haystack = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if agent_json is not None:
            try:
                haystack += "\n" + json.dumps(agent_json, ensure_ascii=False)
            except Exception:
                pass
        marker_found = completion_marker in haystack or bool(diag.get("completionMarkerFoundLive"))
        if not marker_found:
            diag["completionMarkerMissing"] = completion_marker

    return {
        "status": "succeeded" if proc.returncode == 0 else "failed",
        "agentId": agent_id,
        "exitCode": proc.returncode,
        "stdout": proc.stdout[-8000:] if proc.stdout else "",
        "stderr": proc.stderr[-8000:] if proc.stderr else "",
        "elapsed": elapsed, "sessionId": session_id, "diagnostics": diag, "agentJson": agent_json,
        "completionMarker": completion_marker or "", "completionMarkerFound": marker_found,
    }


def _normalize_legacy_spec_markdown_to_v1(markdown: str, version: str) -> dict:
    """Deprecated Markdown compatibility path; implementation instructions are discarded."""
    text = str(markdown or "")
    objective = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("-") and len(objective) < 6:
            value = stripped.lstrip("- ").strip()
            if value and not re.search(r"operator|target.file|exact.patch|suggested.direction|Block|anchor|锚点", value, re.I):
                objective.append(value)
    signatures = sorted(set(re.findall(r"(?:failure[_ -]?signature|failure mode)[:：]?\s*`?([A-Za-z0-9_.-]+)", text, re.I)))
    questions = [{
        "question_id": f"HYP-{index + 1:03d}", "failure_signature": signature,
        "status": "suspected", "claim": f"需要区分 {signature} 的机制原因",
        "alternative_causes": ["instruction_density", "runtime_variance"],
        "disambiguation_signal": "optimization 行为信号发生可复现变化",
        "expected_evidence": "runner-computed targeted optimization evidence",
        "revisit_condition": "新的重复实验提供证据",
    } for index, signature in enumerate(signatures[:4])]
    return {
        "schema_version": "evolution.spec.v1", "spec_version": version,
        "parent_spec_version": None, "objective_contract": {"objective_summary": objective[:4]},
        "accepted_baseline_snapshot": {}, "experiment_questions": questions,
        "protected_behaviors": [],
        "search_contract": {"required_operator_diversity": 3, "exactly_one_selected_mechanism": True, "atomic_single_variable": True, "max_changed_files": 1, "max_executed_edits": 1, "max_diff_hunks": 1},
        "scope_contract": {"allowed_change_areas": [], "disallowed_change_areas": []},
        "evaluation_contract": {"candidate_full_opt_policy": "after_targeted_gate", "validation_policy": "after_candidate_opt_gate", "expected_signal_required": True, "protected_regression_guard_required": True},
        "history_ref": "experiment_ledger.jsonl", "failure_registry_ref": "failure_registry.json",
        "mutation_operator_library_ref": "clawevolve-workflow/references/mutation_operator_library.json",
        "normalized_from": "legacy_markdown", "deprecation": "Markdown-only specs are deprecated",
    }


def _load_input_spec_contract(args, paths: dict) -> tuple[dict, str]:
    version = f"v{args.round - 1}"
    json_path = paths["input_dir"] / f"spec-{version}.json"
    md_path = paths["input_dir"] / f"spec-{version}.md"
    raw = _load_json(json_path, None)
    if isinstance(raw, dict):
        if raw.get("schema_version") == "evolution.spec.v1":
            required = {
                "objective_contract": dict, "accepted_baseline_snapshot": dict,
                "experiment_questions": list, "scope_contract": dict,
            }
            errors = [field for field, expected in required.items() if not isinstance(raw.get(field), expected)]
            if errors:
                raise ValueError(f"invalid evolution.spec.v1; missing or invalid fields: {errors}")
            canonical, budget = _canonicalize_spec_for_tune(raw)
            canonical["_budget"] = budget
            return canonical, "json_v1"
        # Legacy JSON is normalized through the same safe field projection.
        questions = []
        for index, item in enumerate((raw.get("failure_modes_to_address") or raw.get("active_optimization_directions") or [])[:4]):
            if not isinstance(item, dict):
                continue
            signature = str(item.get("failure_mode") or item.get("failure_signature") or item.get("problem_type") or "unclassified_failure")
            raw_claim = item.get("claim") or item.get("expected_effect") or item.get("evidence")
            claim, _ = _sanitize_legacy_mechanism_text(raw_claim, signature)
            questions.append({
                "question_id": f"HYP-{index + 1:03d}", "failure_signature": signature,
                "status": "suspected", "claim": claim,
                "alternative_causes": list(item.get("alternative_causes") or ["instruction_density", "runtime_variance"]),
                "disambiguation_signal": str(item.get("expected_evidence") or "runner-computed behavior metric changes"),
                "expected_evidence": str(item.get("expected_evidence") or "targeted optimization report"),
                "revisit_condition": str(item.get("revisit_condition") or "new reproducible evidence"),
            })
        normalized = {
            "schema_version": "evolution.spec.v1", "spec_version": raw.get("spec_version") or version,
            "parent_spec_version": raw.get("parent_spec_version"),
            "objective_contract": raw.get("objective_contract") or {}, "accepted_baseline_snapshot": raw.get("accepted_baseline_snapshot") or {},
            "experiment_questions": questions,
            "search_contract": raw.get("search_contract") or {},
            "scope_contract": raw.get("scope_contract") or raw.get("tuning_scope") or {},
            "history_ref": "experiment_ledger.jsonl", "failure_registry_ref": "failure_registry.json",
            "mutation_operator_library_ref": "clawevolve-workflow/references/mutation_operator_library.json",
            "normalized_from": str(raw.get("schema_version") or "legacy_json"),
        }
        canonical, budget = _canonicalize_spec_for_tune(normalized)
        canonical["_budget"] = budget
        return canonical, "json_legacy_normalized"
    normalized = _normalize_legacy_spec_markdown_to_v1(_read_text(md_path), version)
    canonical, budget = _canonicalize_spec_for_tune(normalized)
    canonical["_budget"] = budget
    return canonical, "markdown_legacy_normalized"


def _tune_spec_prompt_payload(spec: dict) -> dict:
    return {key: spec.get(key) for key in (
        "schema_version", "spec_version", "objective_contract", "accepted_baseline_snapshot",
        "experiment_questions", "search_contract", "scope_contract",
        "failure_registry_ref", "mutation_operator_library_ref",
    )}


def _mutation_operator_library_path(paths: dict | None = None) -> Path:
    candidates = []
    if paths:
        base = Path(paths.get("skill_base") or "")
        candidates.append(base / "clawevolve-workflow/references/mutation_operator_library.json")
    candidates.append(Path(__file__).resolve().parents[2] / "references/mutation_operator_library.json")
    return next((path for path in candidates if path.is_file()), candidates[-1])


def _load_mutation_operator_library(paths: dict | None = None) -> dict:
    path = _mutation_operator_library_path(paths)
    data = _load_json(path, None)
    if not isinstance(data, dict) or data.get("schema_version") != "evolution.mutation_operator_library.v1":
        raise ValueError(f"mutation operator library missing or invalid: {path}")
    operators = data.get("operators")
    if not isinstance(operators, list) or not operators:
        raise ValueError("mutation operator library operators must be a non-empty list")
    normalized = []
    for index, item in enumerate(operators):
        if not isinstance(item, dict) or not str(item.get("name") or "").strip() or not str(item.get("family") or "").strip():
            raise ValueError(f"invalid mutation operator at index {index}")
        normalized.append(dict(item))
    return {**data, "operators": normalized, "_source_path": str(path)}


def _build_tune_prompt(args, paths: dict) -> str:
    skill_md = _resolve_skill_md(paths["skill_base"], "clawevolve-tune")
    skill_write_root = _skill_write_root()
    layout_note = ("当前运行模式：openversion。用户 Skill 直接使用 skills/<name>/SKILL.md；不创建 skills-local、active 或激活软链。公共目录、软链和 Release Skill 仍只读。"
                   if os.environ.get("CLAWWEB_VERSION") == "openversion" else "")
    objective_md = _read_text(Path(paths["optimize_input_dir"]) / "objective.md")
    input_spec, spec_source = _load_input_spec_contract(args, paths)
    validation_task_ids = _known_validation_task_ids(paths)
    safe_spec = _redact_review_value(_tune_spec_prompt_payload(input_spec), set(), validation_task_ids, [])
    opt_result_text = _baseline_optimization_prompt_text(args)
    operator_library = _load_mutation_operator_library(paths)
    evolution_history_text = _build_evolution_history_text(args, paths)
    failure_profile_text = _optimization_failure_profile_text(args)
    scene_playbook_text = _optimization_scene_playbook_text(args)

    return f"""你正在执行 clawevolve-tune skill。请严格按照以下 SKILL 指令完成调优任务。

<skill_instructions>
{skill_md}
</skill_instructions>

<objective>
{objective_md}
</objective>

<input_spec>
source={spec_source}
{json.dumps(safe_spec, ensure_ascii=False, indent=2)}
</input_spec>

<optimization_bench_result>
{opt_result_text}
</optimization_bench_result>

<optimization_failure_profile>
{failure_profile_text}
</optimization_failure_profile>

<scene_optimization_playbook>
{scene_playbook_text}
</scene_optimization_playbook>

<mutation_operator_library>
{json.dumps(operator_library, ensure_ascii=False, indent=2)}
</mutation_operator_library>

<evolution_history>
{evolution_history_text}
</evolution_history>

## 工作区与读写边界

{layout_note}

- 主 workspace / 被优化对象：`{paths['workspace']}`
- skill_base：`{paths['skill_base']}`
- 当前 agent 的工作目录就是主 workspace。请直接在主 workspace 中读取和修改真实文件，包括需要调优的源码、配置、md/skill 文件等。
- OpenClaw Skill 的可写范围仅限 `{paths['workspace']}/{skill_write_root}/**`。公共 Skill 及其来源目录（包括 `skills-repo`、`skills-center`，以及 `skills/` 下指向这些目录的入口）只允许读取分析，不得修改。
- `{paths['workspace']}/clawevolve-skills/**` 是 Release 私有运行代码，禁止读取、修改或列为候选目标。
- 如果候选修改涉及 Skill，`target_file`、`affected_files`、`changed_files.txt` 和实际写入都必须位于 `{skill_write_root}/**`；不要跟随公共 Skill 软链写入其真实目标。公共 Skill 无法在该边界内调整时，本轮应明确记录无法优化的原因，不得绕过边界。
- 如果需要修改本机配置类 md（如 SOUL.md、AGENTS.md、IDENTITY.md、TOOLS.md、USER.md、HEARTBEAT.md、BOOTSTRAP.md、SKILL.md 等），请修改主 workspace 下的真实文件，而不是在 round/tune 目录里新建临时副本。
- round/tune 目录只用于记录本轮 tune 产物，不是被优化对象。

## 简化调优协议

1. 基于 objective、上一轮 Review 和 Train Bench 结果，选择一个最小、可解释的机制改动。
2. 先说明 failure_signature、suspected_root_cause 和 alternative_causes；不要把单个低分 case 当作根因。
3. 只实施一个独立改动，避免无关重构；修改前确认实际运行时会读取该文件。
4. 不需要生成或满足任何额外验收条件、业务指标阈值或第二验收门禁。
5. 不读取 validation 的逐任务结果、case、rubric、答案或 validation task id，也不生成按 validation case 选择的 bench_plan。
6. 保持现有 workspace/{skill_write_root} 写入边界，不跟随公共 Skill 软链写入其真实目标。

## 输出要求

请将调优记录产物写入以下文件（绝对路径）：

1. 调优报告 → `{paths['tune_dir']}/tune_report.md`
   - 说明观察到的问题、根因假设、实施的最小改动、修改文件和预期效果。
2. 修改的文件列表 → `{paths['tune_dir']}/changed_files.txt`
   - 每行一个相对于 workspace 根目录的路径。
3. diff/patch → `{paths['tune_dir']}/diff.patch`
   - 所有修改的 unified diff。
4. 可选变更清单 → `{paths['tune_dir']}/change_manifest.json`
   - 如生成，使用 `evolution.change_manifest.v2`；仅记录 selected change、原因、风险和本地检查。
   - 不要求 `expected_signals`、`protected_signals`、`protected_behaviors`、Gate 或 Diff Provenance 字段。

不要修改 round/tune 目录以外的非 workspace 文件。完成后在回复末尾输出: TUNE_COMPLETE"""


def _build_review_prompt(args, paths: dict) -> str:
    """Build the smallest useful Review prompt.

    Review is deliberately downstream of Bench.  It explains the observed
    score change and writes the next-round Spec; it is not a second acceptance
    gate and must not reconstruct the removed gate/signal/provenance contract.
    """
    skill_md = _resolve_skill_md(paths["skill_base"], "clawevolve-review")
    objective_md = _read_text(Path(paths["optimize_input_dir"]) / "objective.md")
    validation_task_ids = _known_validation_task_ids(paths)
    input_spec_contract, input_spec_source = _load_input_spec_contract(args, paths)
    input_spec = json.dumps(
        _redact_review_value(_tune_spec_prompt_payload(input_spec_contract), set(), validation_task_ids, []),
        ensure_ascii=False,
        indent=2,
    )
    acc = _require_acceptance_report(paths)
    safe_acc = _acceptance_for_review(acc, validation_task_ids)
    opt_result_text = _bench_summary_text(args, "optimization")
    val_result_text = _bench_aggregate_summary_text(args, "validation")
    tune_report = _sanitize_review_text(_read_text(paths["tune_dir"] / "tune_report.md"), validation_task_ids)
    diff_patch = _sanitize_review_text(_read_text(paths["tune_dir"] / "diff.patch"), validation_task_ids)
    evolution_history_text = _sanitize_review_text(
        _build_evolution_history_text(args, paths, include_validation=True), validation_task_ids
    )
    decision = str(acc.get("bench_decision") or acc.get("decision") or "baseline_unavailable")
    review_status = str(acc.get("review_status") or "pending")

    return f"""你正在执行 clawevolve-review。Review 只负责解释本轮结果并生成下一轮 Spec，不能改变本轮 Bench 决策。

<skill_instructions>
{skill_md}
</skill_instructions>

## 工作区与边界

- 被优化 workspace：`{paths['workspace']}`
- 当前 round/spec 目录只保存 Review 产物，不要修改 workspace，除非生成下一轮 Spec 明确需要。
- 不读取 validation 的逐任务结果、transcript、case、rubric 或答案；只使用 Test Bench aggregate score/pass rate。

## 本轮 Bench 结论（权威）

{json.dumps(safe_acc, ensure_ascii=False, indent=2)}

Bench decision = `{decision}`；当前 Review status = `{review_status}`。

规则：
- Test candidate score > Test baseline score 时，本轮就是 `passed`。
- Test score 相等或下降时，本轮就是 `not_improved`。
- 缺少 baseline/candidate 可比较分数时，只能记录 `baseline_unavailable` 或 `bench_failed`。
- Train Bench 仅 informational，不参与 accepted 判断。
- Review 成功、失败或 fallback 都不得覆盖 `bench_decision`、`accepted`、`decision`。
- 不生成、不恢复、不解释已废弃的额外验收条件或第二验收门。

<objective>
{objective_md}
</objective>

<input_spec_v{args.round - 1}>
source={input_spec_source}
{input_spec}
</input_spec_v{args.round - 1}>

<train_bench_informational>
{opt_result_text}
</train_bench_informational>

<test_bench_aggregate>
{val_result_text}
</test_bench_aggregate>

<tune_report>
{tune_report}
</tune_report>

<diff_summary>
{diff_patch}
</diff_summary>

<evolution_history>
{evolution_history_text}
</evolution_history>

## 输出要求

当前 round = {args.round}。不要直接写 Spec Markdown；runner 会渲染 Spec。只写：
`{_review_decision_path(paths)}`

必须符合：
```json
{{
  "schema_version": "evolution.review_decision.v2",
  "round_id": {args.round},
  "acceptance_decision": "{decision}",
  "summary": "机制级总结：解释 Test score 为什么提升、未提升或无法比较",
  "confidence": "high|medium|low",
  "hypotheses": [
    {{
      "hypothesis_id": "HYP-001",
      "failure_signature": "机制级问题",
      "status": "suspected|testing|supported|falsified",
      "claim": "待验证的机制级解释",
      "alternative_causes": ["instruction_density", "output_budget", "tool_latency"],
      "disambiguation_signal": "下一轮可观察的机制级证据",
      "confidence": "high|medium|low",
      "revisit_condition": "何时重新评估"
    }}
  ],
  "direction_decisions": [
    {{
      "direction_id": "DIRECTION-001",
      "decision": "keep|strengthen|split|freeze|reject|defer",
      "confidence": "high|medium|low",
      "rationale": "基于本轮 aggregate Bench 与变更摘要的简短理由",
      "revisit_condition": "何时重新评估"
    }}
  ]
}}
```

硬约束：
- `acceptance_decision` 必须原样等于上面的 Bench decision。
- 不得输出 operator、target_file、exact_change、patch 或 case-specific 修复。
- 不得输出 task id、rubric、expert、validation case 名称、标准答案或逐任务数据。
- 不得新增任何额外验收条件、业务阈值或第二验收门要求。
- 没有重复证据时使用 suspected/testing，不要声称方向永久失效或达到天花板。
- 可以指出 reasoning quality、delivery reliability、输出完整性等机制层面的观察，但不能把它们变成额外验收条件。

写完 review_decision.json 后，在回复末尾输出：SPEC_COMPLETE"""


def action_auto_tune(args):
    killed = _cleanup_orphan_openclaw_agents()
    if killed:
        _log(args, "ensure-tune", f"cleanup: killed {killed} orphaned agents")
    paths = resolve_paths(args)
    tune_dir = paths["tune_dir"]
    tune_dir.mkdir(parents=True, exist_ok=True)

    model = _optimizer_model(args, "tune")
    agent_id = f"{TUNE_AGENT_NAME}-{_safe_slug(paths['task_id'])}-r{args.round:03d}-{uuid.uuid4().hex[:8]}"
    workspace = Path(paths["workspace"])

    prompt = _build_tune_prompt(args, paths)

    _mark_step(args, "ensure-tune", "RUNNING")
    tune_timeout = getattr(args, "tune_timeout", TUNE_AGENT_TIMEOUT)
    agent_idle_timeout = getattr(args, "agent_idle_timeout", DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS)
    _log(args, "ensure-tune", f"agent={agent_id}, model={model}, workspace={workspace}, outputs={tune_dir}, prompt={len(prompt)}B, total_timeout={tune_timeout}s, idle_timeout={agent_idle_timeout}s")

    result = _openclaw_agent_message(
        agent_id=agent_id, message=prompt, workspace=workspace,
        timeout_seconds=tune_timeout, model=model,
        base_url=TUNE_AGENT_BASE_URL, api_key=TUNE_AGENT_API_KEY,
        completion_marker="TUNE_COMPLETE",
        expected_outputs=STEP_OUTPUTS["ensure-tune"](paths, args),
        idle_timeout_seconds=agent_idle_timeout,
    )
    session_archive = _archive_openclaw_agent_session(
        result, tune_dir / "agent_session"
    )

    _log(args, "ensure-tune", f"agent status={result['status']}, exit={result.get('exitCode')}, elapsed={result.get('elapsed'):.0f}s")
    _log_block(args, "ensure-tune", "AGENT STDOUT", result.get("stdout", ""))
    if result.get("stderr"):
        _log_block(args, "ensure-tune", "AGENT STDERR", result.get("stderr", ""))
    if result.get("diagnostics"):
        _log_block(args, "ensure-tune", "AGENT DIAGNOSTICS", json.dumps(result["diagnostics"], ensure_ascii=False, indent=2))

    outputs = STEP_OUTPUTS["ensure-tune"](paths, args)
    all_exist = all(p.exists() for p in outputs)
    agent_succeeded = result["status"] == "succeeded"
    completion_marker_found = result.get("completionMarkerFound")
    degraded = all_exist and (not agent_succeeded or completion_marker_found is False)
    out = {
        "status": "SUCCESS" if all_exist else "FAILED",
        "degraded": degraded,
        "degradedReason": (
            "agent failed or completion marker missing, but required tune outputs exist"
            if degraded else ""
        ),
        "agentStatus": result["status"], "agentExitCode": result.get("exitCode"),
        "agentElapsed": result.get("elapsed"), "agentSessionId": result.get("sessionId"),
        "completionMarker": result.get("completionMarker", ""),
        "completionMarkerFound": completion_marker_found,
        "outputsExist": all_exist, "outputs": [str(p) for p in outputs],
        "missingOutputs": [str(p) for p in outputs if not p.exists()],
        "agentSessionArchive": session_archive,
    }
    if not all_exist:
        out["error"] = f"Tune agent finished ({result['status']}) but outputs missing: {out['missingOutputs']}"

    _mark_step(args, "ensure-tune", "SUCCESS" if all_exist else "FAILED", out)
    _print_json(out)

    killed = _cleanup_orphan_openclaw_agents()
    if killed:
        _log(args, "ensure-tune", f"post-cleanup: killed {killed} orphans")

    if not all_exist:
        raise SystemExit(f"Tune failed: outputs missing: {out['missingOutputs']}")


def action_auto_review(args):
    killed = _cleanup_orphan_openclaw_agents()
    if killed:
        _log(args, "ensure-review", f"cleanup: killed {killed} orphaned agents")
    paths = resolve_paths(args)
    spec_dir = paths["spec_dir"]
    spec_dir.mkdir(parents=True, exist_ok=True)

    model = _optimizer_model(args, "review")
    agent_id = f"{REVIEW_AGENT_NAME}-{_safe_slug(paths['task_id'])}-r{args.round:03d}-{uuid.uuid4().hex[:8]}"
    workspace = Path(paths["workspace"])

    prompt = _build_review_prompt(args, paths)

    _mark_step(args, "ensure-review", "RUNNING")
    review_timeout = getattr(args, "review_timeout", REVIEW_AGENT_TIMEOUT)
    agent_idle_timeout = getattr(args, "agent_idle_timeout", DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS)
    _log(args, "ensure-review", f"agent={agent_id}, model={model}, workspace={workspace}, outputs={spec_dir}, prompt={len(prompt)}B, total_timeout={review_timeout}s, idle_timeout={agent_idle_timeout}s")

    result = _openclaw_agent_message(
        agent_id=agent_id, message=prompt, workspace=workspace,
        timeout_seconds=review_timeout, model=model,
        base_url=REVIEW_AGENT_BASE_URL, api_key=REVIEW_AGENT_API_KEY,
        completion_marker="SPEC_COMPLETE",
        expected_outputs=[_review_decision_path(paths)],
        idle_timeout_seconds=agent_idle_timeout,
    )
    session_archive = _archive_openclaw_agent_session(
        result, spec_dir / "agent_session"
    )

    _log(args, "ensure-review", f"agent status={result['status']}, exit={result.get('exitCode')}, elapsed={result.get('elapsed'):.0f}s")
    _log_block(args, "ensure-review", "AGENT STDOUT", result.get("stdout", ""))
    if result.get("stderr"):
        _log_block(args, "ensure-review", "AGENT STDERR", result.get("stderr", ""))
    if result.get("diagnostics"):
        _log_block(args, "ensure-review", "AGENT DIAGNOSTICS", json.dumps(result["diagnostics"], ensure_ascii=False, indent=2))

    decision_exists = _review_decision_path(paths).is_file()
    render_result = _render_review_outputs(paths, args.round) if decision_exists else {"ok": False, "error": "review_decision.json missing"}
    outputs = STEP_OUTPUTS["ensure-review"](paths, args)
    all_exist = bool(render_result.get("ok")) and all(p.exists() for p in outputs)
    leak_report = _review_output_leak_report(paths, args.round) if all_exist else {"valid": True, "findings": []}
    if all_exist and not leak_report.get("valid"):
        for path in [*outputs, _review_decision_path(paths)]:
            try:
                rejected = path.with_name(path.name + ".rejected-validation-leak")
                if rejected.exists():
                    rejected.unlink()
                if path.exists():
                    path.replace(rejected)
            except Exception:
                try:
                    path.unlink()
                except Exception:
                    pass
        all_exist = False
    review_state = _load_json(paths["round_dir"] / "round_state.json", {})
    if not isinstance(review_state, dict):
        review_state = {}
    review_state["review_status"] = "success" if all_exist else "failed"
    review_state.setdefault("acceptance", {})
    if isinstance(review_state["acceptance"], dict):
        review_state["acceptance"]["review_status"] = review_state["review_status"]
    (paths["round_dir"] / "round_state.json").write_text(
        json.dumps(review_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    acceptance_path = paths["accept_dir"] / "acceptance_report.json"
    acceptance_state = _load_json(acceptance_path, {})
    if isinstance(acceptance_state, dict):
        acceptance_state["review_status"] = review_state["review_status"]
        acceptance_path.write_text(json.dumps(acceptance_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    agent_succeeded = result["status"] == "succeeded"
    completion_marker_found = result.get("completionMarkerFound")
    degraded = all_exist and (not agent_succeeded or completion_marker_found is False)
    out = {
        "status": "SUCCESS" if all_exist else "FAILED",
        "degraded": degraded,
        "degradedReason": (
            "agent failed or completion marker missing, but required review outputs exist"
            if degraded else ""
        ),
        "agentStatus": result["status"], "agentExitCode": result.get("exitCode"),
        "agentElapsed": result.get("elapsed"), "agentSessionId": result.get("sessionId"),
        "completionMarker": result.get("completionMarker", ""),
        "completionMarkerFound": completion_marker_found,
        "outputsExist": all_exist, "outputs": [str(p) for p in outputs],
        "missingOutputs": [str(p) for p in outputs if not p.exists()],
        "validationLeakCheck": leak_report,
        "reviewDecisionExists": decision_exists,
        "renderResult": render_result,
        "agentSessionArchive": session_archive,
    }
    if not all_exist:
        out["error"] = (
            f"Spec output leaked validation case details: {leak_report.get('findings')}"
            if not leak_report.get("valid")
            else f"Spec agent finished ({result['status']}) but outputs missing: {out['missingOutputs']}"
        )

    _mark_step(args, "ensure-review", "SUCCESS" if all_exist else "FAILED", out)
    _print_json(out)

    killed = _cleanup_orphan_openclaw_agents()
    if killed:
        _log(args, "ensure-review", f"post-cleanup: killed {killed} orphans")

    if not all_exist:
        raise SystemExit(f"Spec failed: outputs missing: {out['missingOutputs']}")


def _restore_round_snapshot_before_tune_retry(args, paths: dict, state: dict, *, reason: str) -> dict:
    snapshot = state.get("rollback_snapshot") if isinstance(state, dict) else {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    path = Path(str(snapshot.get("path") or ""))
    expected_sha = str(snapshot.get("sha256") or "")
    if snapshot.get("status") != "ready" or not path.is_file() or not expected_sha:
        raise SystemExit("applied_candidate_without_rollback_snapshot: cannot safely retry Tune")
    actual_sha = _sha256(path)
    if actual_sha != expected_sha:
        raise SystemExit(f"rollback snapshot digest mismatch before Tune retry: expected={expected_sha}, actual={actual_sha}")
    result = _restore_workspace_from_artifact(args, path, reason)
    state["candidate_mutation_state"] = "restored"
    state["restore_status"] = "succeeded"
    (Path(paths["round_dir"]) / "round_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def action_ensure_tune(args):
    paths = resolve_paths(args)
    state_path = Path(paths["round_dir"]) / "round_state.json"
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    snapshot = state.get("rollback_snapshot") if isinstance(state.get("rollback_snapshot"), dict) else {}
    snapshot_path = Path(str(snapshot.get("path") or ""))
    snapshot_sha = str(snapshot.get("sha256") or "")
    if (
        snapshot.get("status") != "ready"
        or not snapshot_path.is_file()
        or not snapshot_sha
        or _sha256(snapshot_path) != snapshot_sha
    ):
        raise SystemExit("Tune cannot start or resume without a valid current-Round rollback Pack")

    missing = [str(p) for p in STEP_OUTPUTS["ensure-tune"](paths, args) if not p.exists()]
    before_path = _workspace_manifest_path(paths, "before")
    if missing and not before_path.exists():
        _capture_workspace_manifest(paths, "before")
    if not missing:
        change_summary = _write_round_change_summary(args)
        out = {"status": "SUCCESS", "message": "tune outputs exist", "outputs": [str(p) for p in STEP_OUTPUTS["ensure-tune"](paths, args)], "change_summary": change_summary}
        _mark_step(args, "ensure-tune", "SUCCESS", out)
        _log(args, "ensure-tune", f"outputs already exist, skipping; noop={change_summary.get('is_noop')}")
        _print_json(out)
        return
    if state.get("candidate_mutation_state") in {"possibly_applied", "applied"}:
        _restore_round_snapshot_before_tune_retry(args, paths, state, reason="Tune retry recovery")
        state = _load_json(state_path, {})
    state["workspace_mode"] = "live"
    state["candidate_mutation_state"] = "possibly_applied"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    action_auto_tune(args)
    _write_round_change_summary(args)
    state = _load_json(state_path, {})
    if isinstance(state, dict):
        state["candidate_mutation_state"] = "applied"
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def action_ensure_review(args):
    paths = resolve_paths(args)
    missing = [str(p) for p in STEP_OUTPUTS["ensure-review"](paths, args) if not p.exists()]
    decision_path = _review_decision_path(paths)
    if missing and decision_path.is_file():
        render_result = _render_review_outputs(paths, args.round)
        if render_result.get("ok"):
            leak_report = _review_output_leak_report(paths, args.round)
            if leak_report.get("valid"):
                missing = [str(p) for p in STEP_OUTPUTS["ensure-review"](paths, args) if not p.exists()]
            else:
                for output in STEP_OUTPUTS["ensure-review"](paths, args):
                    if output.exists():
                        output.unlink()
        else:
            rejected = decision_path.with_name(decision_path.name + ".rejected-invalid")
            if rejected.exists():
                rejected.unlink()
            decision_path.replace(rejected)
    if not missing:
        out = {"status": "SUCCESS", "message": "spec outputs exist", "outputs": [str(p) for p in STEP_OUTPUTS["ensure-review"](paths, args)]}
        state = _load_json(paths["round_dir"] / "round_state.json", {})
        if not isinstance(state, dict):
            state = {}
        state["review_status"] = "success"
        acceptance_path = paths["accept_dir"] / "acceptance_report.json"
        acceptance = _load_json(acceptance_path, {})
        if isinstance(acceptance, dict):
            acceptance["review_status"] = "success"
            acceptance_path.write_text(json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            state["acceptance"] = acceptance
        (paths["round_dir"] / "round_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _mark_step(args, "ensure-review", "SUCCESS", out)
        _log(args, "ensure-review", "outputs already exist, skipping")
        _print_json(out)
        return
    action_auto_review(args)


# ═══════════════════════════════════════════════════════════════════════════════
# Watchdog / resilient execution helpers
# ═══════════════════════════════════════════════════════════════════════════════

# Watchdog defaults — baked into the script (no CLI flags required).
DEFAULT_WATCHDOG_MAX_RETRIES = 3
DEFAULT_WATCHDOG_RETRY_DELAY_SECONDS = 30
# A full optimization + validation bench can legitimately take more than four
# hours.  Zero disables the round wall-clock deadline; deployments that need a
# cap can set CLAWEVOLVE_ROUND_TIMEOUT or pass --round-timeout.
DEFAULT_WATCHDOG_ROUND_TIMEOUT_SECONDS = int(os.environ.get("CLAWEVOLVE_ROUND_TIMEOUT", "0"))
DEFAULT_WATCHDOG_MAX_CONSECUTIVE_FAILURES = 3
DEFAULT_WATCHDOG_MAX_CONSECUTIVE_DEGRADED_ROUNDS = 5
DEFAULT_WATCHDOG_MIN_DISK_SPACE_GB = 1.0
DEFAULT_WATCHDOG_REPORT_RETRY_ATTEMPTS = False


WATCHDOG_STEP_POLICIES = {
    "prepare":        {"max_attempts": 1, "fatal": True,  "fallback": None},
    # Optimization baseline is a Tune precondition. Continuing with a synthetic
    # score/no-op Diff produces a fake completed round and must not advance.
    "load-baseline-opt": {"max_attempts": 3, "fatal": True, "fallback": None},
    "candidate-static-gate": {"max_attempts": 1, "fatal": True, "fallback": None},
    "bench-targeted-opt": {"max_attempts": 3, "fatal": True, "fallback": None},
    "candidate-opt-gate": {"max_attempts": 1, "fatal": True, "fallback": None},
    "bench-full-opt": {"max_attempts": 3, "fatal": True, "fallback": None},
    "bench-opt":      {"max_attempts": 3, "fatal": True,   "fallback": None},
    "ensure-tune":    {"max_attempts": 3, "fatal": False, "fallback": "noop_tune"},
    "bench-val":      {"max_attempts": 3, "fatal": False, "fallback": "bench_val_reject_round"},
    "accept":         {"max_attempts": 1, "fatal": False, "fallback": "reject_round"},
    "replicate-validation": {"max_attempts": 2, "fatal": False, "fallback": "reject_round"},
    "pack":           {"max_attempts": 3, "fatal": False, "fallback": "pack_failed_degraded"},
    "restore":        {"max_attempts": 3, "fatal": True,  "fallback": None},
    "ensure-review":    {"max_attempts": 3, "fatal": False, "fallback": "copy_previous_spec"},
    # curl already performs five attempts with a fixed three-second delay.
    "upload-clawweb": {"max_attempts": 1, "fatal": False, "fallback": "pending_clawweb_report"},
    "baseline-pack":  {"max_attempts": 2, "fatal": True,  "fallback": None},
    "upload-oss":     {"max_attempts": 3, "fatal": False, "fallback": "mark_oss_failed"},
    "complete":       {"max_attempts": 3, "fatal": True,  "fallback": "minimal_complete"},
}


def _step_policy(args, name: str) -> dict:
    # Per-step retry counts and fallbacks are baked into WATCHDOG_STEP_POLICIES.
    return dict(WATCHDOG_STEP_POLICIES.get(name, {"max_attempts": DEFAULT_WATCHDOG_MAX_RETRIES, "fatal": False, "fallback": None}))


def _format_exception(exc) -> dict:
    if isinstance(exc, SystemExit):
        code = exc.code
        message = str(code) if code is not None else "SystemExit"
        return {"type": "SystemExit", "message": message, "exit_code": code, "traceback": traceback.format_exc()[-12000:]}
    result = {"type": type(exc).__name__, "message": str(exc), "repr": repr(exc), "traceback": traceback.format_exc()[-12000:]}
    error_code = getattr(exc, "code", None)
    if error_code:
        result["code"] = str(error_code)
    retryable = getattr(exc, "retryable", None)
    if isinstance(retryable, bool):
        result["retryable"] = retryable
    return result


def _missing_step_outputs(args, step: str) -> list[str]:
    fn = STEP_OUTPUTS.get(step)
    if not fn:
        return []
    paths = resolve_paths(args)
    try:
        return [str(Path(x)) for x in fn(paths, args) if not Path(x).exists()]
    except Exception as exc:
        return [f"<failed to compute outputs for {step}: {type(exc).__name__}: {exc}>"]


def _record_step_failure(args, step: str, attempt: int, error: dict) -> Path | None:
    try:
        paths = resolve_paths(args)
        failure_dir = paths["round_dir"] / "failures"
        failure_dir.mkdir(parents=True, exist_ok=True)
        item = {
            "schema_version": "evolution.step_failure.v1",
            "task_id": paths["task_id"],
            "round_id": args.round,
            "step": step,
            "attempt": attempt,
            "error": error,
            "run_log_tail": _log_tail(paths["round_dir"] / "run.log", 12000),
            "created_at": _now(),
        }
        path = failure_dir / f"{_safe_slug(step)}-attempt-{attempt}.json"
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
    except Exception:
        return None


def _record_round_failure(args, error: dict) -> Path | None:
    try:
        paths = resolve_paths(args)
        failure_dir = paths["round_dir"] / "failures"
        failure_dir.mkdir(parents=True, exist_ok=True)
        state = _load_json(paths["round_dir"] / "round_state.json", {})
        item = {
            "schema_version": "evolution.round_failure.v1",
            "task_id": paths["task_id"],
            "round_id": args.round,
            "error": error,
            "steps": state.get("steps", {}) if isinstance(state, dict) else {},
            "run_log_tail": _log_tail(paths["round_dir"] / "run.log", 16000),
            "created_at": _now(),
        }
        path = failure_dir / "round_failure.json"
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
    except Exception:
        return None


def _clawweb_report_url(args, paths: dict, state: dict | None = None) -> str:
    state = state or {}
    task_id = paths.get("task_id") or ""
    step_id = args.step_id or state.get("step_id", "") or _env("STEP_ID")
    clawweb_url = (
        args.clawweb_url or args.clawweb_url_camel
        or _env("CLAWEVOLVE_CLAWWEB_URL") or _env("CLAWWEB_URL")
        or "http://127.0.0.1:5173/"
    ).rstrip("/")
    if not task_id or not step_id or not clawweb_url:
        return ""
    return (
        clawweb_url + "/api/evolve/internal/tasks/"
        + urllib.parse.quote(task_id, safe="")
        + "/steps/"
        + urllib.parse.quote(step_id, safe="")
        + "/report"
    )


def _post_clawweb_payload_best_effort(args, payload: dict, log_step: str = "watchdog-report") -> dict:
    if getattr(args, "skip_clawweb", False):
        return {"status": "SKIPPED", "reason": "--skip-clawweb"}
    try:
        paths = resolve_paths(args)
        state = _load_json(paths["round_dir"] / "round_state.json", {})
        report_url = _clawweb_report_url(args, paths, state if isinstance(state, dict) else {})
        if not report_url:
            _log(args, log_step, "ClawWeb report skipped (missing task_id/step_id/url)", "WARN")
            return {"status": "SKIPPED", "reason": "missing task_id/step_id/url"}
        proc = subprocess.run(
            ["curl", "--noproxy", "*", "-sS", "--fail-with-body", "--retry", "4", "--retry-delay", "3", "--retry-all-errors", "-X", "POST", report_url,
             "-H", "Content-Type: application/json", "--data-raw", json.dumps(payload, ensure_ascii=False)],
            text=True, capture_output=True, timeout=60,
        )
        result = {"status": "SUCCESS" if proc.returncode == 0 else "UPLOAD_FAILED", "exitCode": proc.returncode, "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:], "url": report_url}
        _log(args, log_step, f"ClawWeb report exit={proc.returncode}")
        return result
    except Exception as exc:
        try:
            _log(args, log_step, f"ClawWeb report failed: {type(exc).__name__}: {exc}", "ERROR")
        except Exception:
            pass
        return {"status": "UPLOAD_FAILED", "error": f"{type(exc).__name__}: {exc}"}


def _report_step_failure_to_clawweb_best_effort(args, step: str, attempt: int, max_attempts: int, error: dict, final: bool) -> dict:
    if not final and not DEFAULT_WATCHDOG_REPORT_RETRY_ATTEMPTS:
        return {"status": "SKIPPED", "reason": "retry-attempt reporting disabled"}
    try:
        paths = resolve_paths(args)
        state = _load_json(paths["round_dir"] / "round_state.json", {})
        steps = state.get("steps", {}) if isinstance(state, dict) else {}
    except Exception:
        steps = {}
    action = "final_failed" if final else "retrying"
    payload = {
        "status": "failed",
        "summary": f"第 {args.round} 轮 {step} {'最终失败' if final else '尝试失败'}: {str(error.get('message', ''))[:200]}",
        "output": {
            "error": {"message": str(error.get("message", ""))[:2000], "type": error.get("type"), "step": step, "attempt": attempt, "max_attempts": max_attempts, "final": final},
            "watchdog": {"enabled": True, "action": action, "step": step},
            "steps_detail": steps,
            "roundDecision": {"stop": False, "reason": "watchdog will apply retry/fallback" if not final else "watchdog step retries exhausted"},
        },
    }
    return _post_clawweb_payload_best_effort(args, payload, "watchdog-step-report")


def _report_round_failure(args, error: dict) -> dict:
    try:
        paths = resolve_paths(args)
        state = _load_json(paths["round_dir"] / "round_state.json", {})
        steps = state.get("steps", {}) if isinstance(state, dict) else {}
    except Exception:
        steps = {}
    failed_steps = [k for k, v in steps.items() if isinstance(v, dict) and str(v.get("status", "")).startswith(("FAILED", "FALLBACK"))]
    payload = {
        "status": "failed",
        "summary": f"第 {args.round} 轮优化失败: {str(error.get('message', ''))[:200]}",
        "output": {
            "error": {"message": str(error.get("message", ""))[:2000], "type": error.get("type"), "round_id": args.round, "failed_steps": failed_steps, "steps_detail": steps},
            "metrics": [{"key": "round_status", "name": "本轮状态", "value": 0, "unit": "boolean", "target": 1, "passed": False}],
            "roundDecision": {"stop": True, "reason": "watchdog detected fatal round failure"},
            "watchdog": {"enabled": True, "status": "ROUND_FAILED"},
        },
    }
    return _post_clawweb_payload_best_effort(args, payload, "watchdog-round-report")


def _fallback_bench_failed_summary(args, kind: str, error: dict) -> dict:
    paths = resolve_paths(args)
    state_path = paths["round_dir"] / "round_state.json"
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("bench", {})
    state["bench"][kind] = {
        "status": "failed",
        "exitCode": None,
        "resultPath": "",
        "summary": {"score": None, "pass_rate": None, "total": 0, "passed": 0, "failed": 0, "errors": 0},
        "error": error,
        "watchdogFallback": True,
        "updated_at": _now(),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "fallback": f"{kind}_bench_failed_summary"}


def _fallback_noop_tune(args, error: dict) -> dict:
    paths = resolve_paths(args)
    tune_dir = paths["tune_dir"]
    tune_dir.mkdir(parents=True, exist_ok=True)
    (tune_dir / "tune_report.md").write_text(
        "# Tune Fallback\n\nclawevolve-tune 未能成功完成，watchdog 自动生成 noop tune。\n\n本轮不会主动修改 workspace。\n\n"
        "## Error\n\n```json\n" + json.dumps(error, ensure_ascii=False, indent=2) + "\n```\n",
        encoding="utf-8",
    )
    (tune_dir / "changed_files.txt").write_text("# no changes\n", encoding="utf-8")
    (tune_dir / "diff.patch").write_text("", encoding="utf-8")
    (tune_dir / "change_manifest.json").write_text(json.dumps({
        "schema_version": "evolution.change_manifest.v2",
        "selected_proposal_id": None,
        "proposals": [],
        "edits": [],
        "fallback": True,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "fallback": "noop_tune"}


def _fallback_reject_round(args, error: dict) -> dict:
    """Record a watchdog failure without inventing a second acceptance gate.

    The Bench decision stays score-based.  If Tune may have touched the live
    Workspace, Restore is driven exclusively by this Round's rollback snapshot,
    never by the effective evaluation baseline.
    """
    paths = resolve_paths(args)
    round_dir = paths["round_dir"]
    acc_dir = paths["accept_dir"]
    acc_dir.mkdir(parents=True, exist_ok=True)
    state = _load_json(round_dir / "round_state.json", {})
    if not isinstance(state, dict):
        state = {}
    bench = state.get("bench") or {}
    opt_summary = (bench.get("optimization") or {}).get("summary") or {}
    val_summary = (bench.get("validation") or {}).get("summary") or {}
    manifest = _load_json(paths["run_dir"] / "optimize" / "output" / "optimize_manifest.json", {})
    mutation_state = str(state.get("candidate_mutation_state") or "")
    restore_required = mutation_state in {"possibly_applied", "applied"}

    baseline_score = _finite_number(
        manifest.get("last_accepted_validation_score") if isinstance(manifest, dict) else None
    )
    candidate_score = _finite_number(val_summary.get("score"))
    if baseline_score is None:
        decision = "baseline_unavailable"
    elif candidate_score is None:
        decision = "bench_failed"
    elif candidate_score > baseline_score:
        decision = "passed"
    else:
        decision = "not_improved"
    effect_passed = decision == "passed"
    promotion_status = "pending" if effect_passed else "not_started"
    report = {
        "schema_version": "evolution.acceptance.bench_review.v1",
        "evolve_run_id": paths["task_id"],
        "round_id": args.round,
        "bench_decision": decision,
        "accepted": False,
        "promotion_status": promotion_status,
        "decision": decision,
        "score": {
            "name": "test_score",
            "baseline": baseline_score,
            "candidate": candidate_score,
            "delta": candidate_score - baseline_score if baseline_score is not None and candidate_score is not None else None,
        },
        "train": {
            "baseline": _finite_number((manifest.get("last_accepted_optimization_score") if isinstance(manifest, dict) else None)),
            "candidate": _finite_number(opt_summary.get("score")),
            "delta": None,
            "role": "informational",
        },
        "baseline": {"round_id": manifest.get("last_accepted_round") if isinstance(manifest, dict) else None, "validation_score": baseline_score},
        "current": {"validation_score": candidate_score, "optimization_score": opt_summary.get("score")},
        "delta": {"validation_score": candidate_score - baseline_score if baseline_score is not None and candidate_score is not None else None},
        "reason": "watchdog recorded a failed step; Bench decision remains score-based",
        "review_status": "pending",
        "restore_required": bool(restore_required and not effect_passed),
        "promote_to_baseline": False,
        "error": error,
        "created_at": _now(),
    }
    # Avoid any accidental dependency on a temporary helper in serialized data.
    report["delta"]["validation_score"] = report["score"]["delta"]
    (acc_dir / "acceptance_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state["acceptance"] = report
    state["accepted"] = False
    state["promotion_status"] = promotion_status
    state["acceptance_decision"] = decision
    state["bench_decision"] = decision
    state["restore_required"] = report["restore_required"]
    (round_dir / "round_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "fallback": "record_bench_decision", "accepted": False, "promotion_status": promotion_status, "restore_required": report["restore_required"], "decision": decision}


def _fallback_copy_previous_spec(args, error: dict) -> dict:
    paths = resolve_paths(args)
    spec_dir = paths["spec_dir"]
    spec_dir.mkdir(parents=True, exist_ok=True)
    input_spec = paths["input_dir"] / f"spec-v{args.round - 1}.md"
    output_spec = spec_dir / f"spec-v{args.round}.md"
    if not input_spec.exists():
        fallback_spec = _find_latest_existing_spec(Path(paths["optimize_output_dir"]), Path(paths["optimize_input_dir"]), args.round - 1)
        input_spec = fallback_spec if fallback_spec else input_spec
    if not input_spec.exists():
        return {"ok": False, "fallback": "copy_previous_spec", "error": f"input spec not found: {input_spec}"}
    input_json = input_spec.with_suffix(".json")
    output_json = spec_dir / f"spec-v{args.round}.json"
    if input_json.is_file():
        data = _load_json(input_json, {})
        if isinstance(data, dict):
            data["spec_version"] = f"v{args.round}"
            data["parent_spec_version"] = f"v{args.round - 1}"
            output_json.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if not output_json.is_file():
        normalized = _normalize_legacy_spec_markdown_to_v1(_read_text(input_spec), f"v{args.round}")
        normalized["parent_spec_version"] = f"v{args.round - 1}"
        output_json.write_text(json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    rendered = _render_spec_v1_markdown(_load_json(output_json, {}))
    rendered, _ = _fit_utf8_budget(rendered, 8192)
    output_spec.write_text(rendered, encoding="utf-8")
    (spec_dir / "spec_update_report.md").write_text(
        "# Spec Fallback\n\nclawevolve-review 未能成功完成，watchdog 复制上一版 spec 作为下一轮输入。\n\n策略未更新。\n\n"
        "## Error\n\n```json\n" + json.dumps(error, ensure_ascii=False, indent=2) + "\n```\n",
        encoding="utf-8",
    )
    state_path = paths["round_dir"] / "round_state.json"
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    state["review_status"] = "fallback"
    acceptance_path = paths["accept_dir"] / "acceptance_report.json"
    acceptance = _load_json(acceptance_path, {})
    if isinstance(acceptance, dict):
        acceptance["review_status"] = "fallback"
        acceptance_path.write_text(json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        state["acceptance"] = acceptance
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "fallback": "copy_previous_spec", "review_status": "fallback", "from": str(input_spec), "to": str(output_spec), "json": str(output_json)}


def _fallback_pack_failed_degraded(args, error: dict) -> dict:
    paths = resolve_paths(args)
    acc = _require_acceptance_report(paths)
    candidate_selected = _candidate_passed_bench(acc)
    artifacts_dir = paths["artifacts_dir"]
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "evolution.pack_report.v0",
        "artifactPath": "",
        "artifactName": "",
        "artifactKind": "pack_failed",
        "accepted": False,
        "promotionStatus": "failed" if candidate_selected else acc.get("promotion_status", "not_started"),
        "error": error,
        "watchdogFallback": True,
        "created_at": _now(),
    }
    (artifacts_dir / "pack_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if candidate_selected:
        state = _load_json(paths["round_dir"] / "round_state.json", {})
        mutation_state = str((state or {}).get("candidate_mutation_state") or "")
        restore_required = mutation_state in {"possibly_applied", "applied"}
        _update_promotion_state(
            paths, "failed", accepted=False,
            restore_required=restore_required, error=error,
        )
        _log(args, "pack", "Bench passed but candidate Pack failed; scheduling Round rollback", "WARN")
        return {
            "ok": True, "fallback": "pack_failed_promotion", "accepted": False,
            "promotion_status": "failed", "restore_required": restore_required,
        }
    return {"ok": True, "fallback": "pack_failed_degraded", "accepted": False}


def _fallback_pending_clawweb_report(args, error: dict) -> dict:
    paths = resolve_paths(args)
    upload_dir = paths["upload_dir"]
    upload_dir.mkdir(parents=True, exist_ok=True)
    item = {"round_id": args.round, "error": error, "created_at": _now(), "kind": "clawweb_upload_failed"}
    with (upload_dir / "pending_clawweb_reports.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    # Keep the original HTTP result in clawweb_manifest.json.  Overwriting it
    # here used to erase the actionable 4xx/5xx response and leave only the
    # watchdog's generic semantic error.
    manifest = {"status": "PENDING_RETRY", "error": error, "watchdogFallback": True, "created_at": _now()}
    (upload_dir / "clawweb_fallback_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"ok": True, "fallback": "pending_clawweb_report"}


def _fallback_mark_oss_failed(args, error: dict) -> dict:
    paths = resolve_paths(args)
    upload_dir = paths["upload_dir"]
    upload_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"status": "UPLOAD_FAILED", "error": error, "watchdogFallback": True, "created_at": _now()}
    (upload_dir / "oss_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    acceptance = _load_json(paths["accept_dir"] / "acceptance_report.json", {})
    if _candidate_passed_bench(acceptance):
        state = _load_json(paths["round_dir"] / "round_state.json", {})
        mutation_state = str((state or {}).get("candidate_mutation_state") or "")
        restore_required = mutation_state in {"possibly_applied", "applied"}
        _update_promotion_state(
            paths, "failed", accepted=False,
            restore_required=restore_required, error=error,
        )
        return {
            "ok": True, "fallback": "mark_oss_failed",
            "promotion_status": "failed", "restore_required": restore_required,
        }
    return {"ok": True, "fallback": "mark_oss_failed"}


def _fallback_minimal_complete(args, error: dict) -> dict:
    """Fail closed; restore a selected Candidate if promotion commit failed."""
    try:
        paths = resolve_paths(args)
        acceptance = _load_json(paths["accept_dir"] / "acceptance_report.json", {})
        restored = False
        if _candidate_passed_bench(acceptance):
            state = _load_json(paths["round_dir"] / "round_state.json", {})
            mutation_state = str((state or {}).get("candidate_mutation_state") or "")
            restore_required = mutation_state in {"possibly_applied", "applied", "promoted"}
            _update_promotion_state(
                paths, "failed", accepted=False,
                restore_required=restore_required, error=error,
            )
            if restore_required:
                action_restore(args)
                restored = True
        return {
            "ok": False,
            "fatal": True,
            "fallback": "complete_failed_restore",
            "restored": restored,
            "error": error,
        }
    except Exception as exc:
        return {"ok": False, "fatal": True, "fallback": "complete_failed_restore", "error": f"{type(exc).__name__}: {exc}"}


def _apply_step_fallback(args, step: str, fallback: str, error: dict) -> dict:
    _log(args, step, f"applying watchdog fallback: {fallback}", "WARN")
    try:
        if fallback == "bench_opt_failed_summary":
            return _fallback_bench_failed_summary(args, "optimization", error)
        if fallback == "bench_val_reject_round":
            b = _fallback_bench_failed_summary(args, "validation", error)
            r = _fallback_reject_round(args, error)
            return {"ok": bool(b.get("ok") and r.get("ok")), "fatal": bool(r.get("fatal")), "fallback": fallback, "bench": b, "acceptance": r}
        if fallback == "noop_tune":
            return _fallback_noop_tune(args, error)
        if fallback == "reject_round":
            return _fallback_reject_round(args, error)
        if fallback == "copy_previous_spec":
            return _fallback_copy_previous_spec(args, error)
        if fallback == "pack_failed_degraded":
            rval = _fallback_pack_failed_degraded(args, error)
            if rval.get("fatal"):
                return rval
            # if accepted round pack failed, we still got ok=True after downgrade
            return rval
        if fallback == "pending_clawweb_report":
            return _fallback_pending_clawweb_report(args, error)
        if fallback == "mark_oss_failed":
            return _fallback_mark_oss_failed(args, error)
        if fallback == "minimal_complete":
            return _fallback_minimal_complete(args, error)
        return {"ok": False, "error": f"unknown fallback: {fallback}"}
    except Exception as exc:
        return {"ok": False, "fallback": fallback, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-8000:]}


def _step_semantic_error(args, name: str) -> str:
    """Return a retry-worthy semantic failure even when the action did not raise."""
    try:
        paths = resolve_paths(args)
        bench_keys = {
            "bench-opt": "candidate_optimization_full", "bench-targeted-opt": "candidate_optimization_targeted",
            "bench-full-opt": "candidate_optimization_full", "bench-val": "validation",
        }
        if name in bench_keys:
            state = _load_json(paths["round_dir"] / "round_state.json", {})
            kind = bench_keys[name]
            bench_info = (state.get("bench") or {}).get(kind) or {} if isinstance(state, dict) else {}
            status = str(bench_info.get("status") or "").lower()
            exit_code = bench_info.get("exitCode")
            if status == "failed" or (exit_code not in (None, 0)):
                return f"{name} semantic failure: status={status}, exitCode={exit_code}, logPath={bench_info.get('logPath')}"
        if name == "upload-clawweb":
            manifest = _load_json(paths["upload_dir"] / "clawweb_manifest.json", {})
            status = str(manifest.get("status") or "").upper() if isinstance(manifest, dict) else ""
            if status in {"UPLOAD_FAILED", "PARTIAL_UPLOAD_FAILED"}:
                return f"upload-clawweb semantic failure: status={status}, reason={manifest.get('reason') or manifest.get('error')}"
        if name == "upload-oss":
            manifest = _load_json(paths["upload_dir"] / "oss_manifest.json", {})
            status = str(manifest.get("status") or "").upper() if isinstance(manifest, dict) else ""
            if status in {"UPLOAD_FAILED", "PARTIAL_UPLOAD_FAILED"}:
                return f"upload-oss semantic failure: status={status}, reason={manifest.get('reason') or manifest.get('error')}"
    except Exception as exc:
        return f"failed to check semantic status for {name}: {type(exc).__name__}: {exc}"
    return ""


def _call_action_for_round_watchdog(args, name: str, fn, round_started_at: float | None = None) -> dict:
    if getattr(args, "resume", True) and not getattr(args, "force", False) and _step_done(args, name):
        out = {"action": name, "status": "SKIPPED", "reason": "outputs already exist"}
        print(json.dumps(out, ensure_ascii=False))
        _mark_step(args, name, "SKIPPED", {"reason": "outputs already exist"})
        _log(args, name, "SKIPPED (outputs already exist)")
        return out

    round_timeout = getattr(args, "round_timeout", DEFAULT_WATCHDOG_ROUND_TIMEOUT_SECONDS)
    if _elapsed_timeout_exceeded(round_started_at, round_timeout):
        raise SystemExit(f"round timeout exceeded before {name}: {round_timeout}s")

    policy = _step_policy(args, name)
    max_attempts = int(policy.get("max_attempts", 1) or 1)
    retry_delay = DEFAULT_WATCHDOG_RETRY_DELAY_SECONDS
    last_error = None

    for attempt in range(1, max_attempts + 1):
        _mark_step(args, name, "RUNNING", {"attempt": attempt, "max_attempts": max_attempts, "started_at": _now()})
        _log(args, name, f"start attempt {attempt}/{max_attempts}")
        try:
            action_result = fn(args)
            if name in STEP_OUTPUTS and not _step_done(args, name):
                missing = _missing_step_outputs(args, name)
                if missing:
                    raise RuntimeError(f"{name} finished but outputs missing: {missing}")
                # All expected output files exist yet _step_done is still False.
                # This is NOT a missing-output condition; the step failed a deeper
                # state/semantic check (e.g. the manifest reports UPLOAD_FAILED, or
                # _step_done's existence check threw). Surface a precise degraded
                # state so the downstream fallback gets an actionable error instead
                # of the misleading "finished but outputs missing: []".
                raise RuntimeError(
                    f"{name} finished but step-done check failed: expected outputs "
                    f"exist yet _step_done({name}) is False; a manifest status or "
                    f"semantic check likely failed (see _step_semantic_error)."
                )
            semantic_error = _step_semantic_error(args, name)
            if semantic_error:
                raise RuntimeError(semantic_error)
            returned_status = str((action_result or {}).get("status") or "").lower() if isinstance(action_result, dict) else ""
            if returned_status == "skipped":
                _log(args, name, f"SKIPPED attempt {attempt}: {(action_result or {}).get('reason', '')}")
                return {"action": name, "status": "SKIPPED", "attempt": attempt, "reason": (action_result or {}).get("reason", "")}
            if name not in {"bench-opt", "bench-targeted-opt", "bench-full-opt", "bench-val", "prepare", "load-baseline-opt"}:
                _mark_step(args, name, "SUCCESS", {"attempt": attempt, "finished_at": _now()})
            _log(args, name, f"SUCCESS attempt {attempt}")
            return {"action": name, "status": "SUCCESS", "attempt": attempt}
        except KeyboardInterrupt:
            raise
        except SystemExit as exc:
            last_error = _format_exception(exc)
        except Exception as exc:
            last_error = _format_exception(exc)

        _log(args, name, f"FAILED attempt {attempt}/{max_attempts}: {last_error.get('message')}", "ERROR")
        _log_block(args, name, f"ERROR attempt {attempt}", json.dumps(last_error, ensure_ascii=False, indent=2))
        _record_step_failure(args, name, attempt, last_error)
        _mark_step(args, name, "FAILED_ATTEMPT", {"attempt": attempt, "max_attempts": max_attempts, "error": last_error, "finished_at": _now()})
        _report_step_failure_to_clawweb_best_effort(args, name, attempt, max_attempts, last_error, final=False)

        if (last_error or {}).get("retryable") is False:
            _log(args, name, "non-retryable failure; stopping retries", "WARN")
            break

        message = str((last_error or {}).get("message") or "")
        non_retryable_runtime = "LEGACY_ARTIFACT_REQUIRES_UPDATED_DEPLOY" in message
        non_retryable_upload = (
            name == "upload-clawweb"
            and ("step 不存在" in message or "task 不存在" in message or "not found" in message.lower())
        ) or (
            name == "upload-oss"
            and ("终态 Step" in message or "HTTP 409" in message)
        )
        if non_retryable_runtime or non_retryable_upload:
            reason = "runtime bundle incompatibility" if non_retryable_runtime else "clawweb rejection"
            _log(args, name, f"non-retryable {reason}; stop retrying", "WARN")
            break

        if attempt < max_attempts:
            delay = retry_delay * (2 ** (attempt - 1))
            _log(args, name, f"retrying in {delay}s")
            _cleanup_orphan_openclaw_agents()
            time.sleep(delay)

    _mark_step(args, name, "FAILED", {"attempts": attempt if 'attempt' in locals() else max_attempts, "max_attempts": max_attempts, "error": last_error, "finished_at": _now()})

    fallback = policy.get("fallback")
    if fallback:
        fallback_result = _apply_step_fallback(args, name, fallback, last_error or {})
        if fallback_result.get("ok"):
            # A successful fallback is degraded progress, not a terminal Step failure.
            # Reporting final=true before the fallback makes ClawWeb close the Step and
            # prevents subsequent artifact uploads from obtaining signed URLs.
            _mark_step(args, name, "FALLBACK", {"fallback": fallback, "error": last_error, "result": fallback_result, "finished_at": _now()})
            _log(args, name, f"FALLBACK applied: {fallback}", "WARN")
            return {"action": name, "status": "FALLBACK", "fallback": fallback, "error": last_error, "result": fallback_result}
        _log(args, name, f"fallback failed: {fallback_result}", "ERROR")
        if fallback_result.get("fatal"):
            _report_step_failure_to_clawweb_best_effort(args, name, attempt if 'attempt' in locals() else max_attempts, max_attempts, last_error or {}, final=True)
            raise SystemExit(f"{name} failed after {max_attempts} attempts and fallback failed: {fallback_result}")

    if policy.get("fatal"):
        _report_step_failure_to_clawweb_best_effort(args, name, attempt if 'attempt' in locals() else max_attempts, max_attempts, last_error or {}, final=True)
        raise SystemExit(f"{name} failed after {max_attempts} attempts: {(last_error or {}).get('message')}")

    _mark_step(args, name, "FAILED_SKIP", {"error": last_error, "finished_at": _now()})
    _log(args, name, "FAILED_SKIP after retries exhausted", "WARN")
    return {"action": name, "status": "FAILED_SKIP", "error": last_error}


def _round_sequence(args):
    sequence = [
        ("prepare", action_prepare),
        ("baseline-pack", action_baseline_pack),
        ("load-baseline-opt", action_load_baseline_opt),
        ("ensure-tune", action_ensure_tune),
        ("bench-full-opt", action_bench_candidate_opt_full),
        ("bench-val", action_bench_val),
        ("accept", action_accept),
        ("pack", action_pack),
        # Review materializes spec-v{round}.md/json.  OSS publication treats
        # that Spec as a required round artifact, so Review must finish first.
        ("ensure-review", action_ensure_review),
    ]
    if not getattr(args, "skip_oss", False):
        sequence.append(("upload-oss", action_publish_round_oss))
    sequence.extend([
        ("restore", action_restore),
    ])
    sequence.append(("complete", action_complete))
    if not getattr(args, "skip_clawweb", False):
        sequence.append(("upload-clawweb", action_upload_clawweb))
    return sequence


def _archive_round_for_new_step(args) -> Path | None:
    """Archive stale outputs when a new ClawWeb Step retries the same Round."""
    paths = resolve_paths(args)
    round_dir = Path(paths["round_dir"])
    current_step_id = str(getattr(args, "step_id", "") or _env("STEP_ID") or "").strip()
    if not current_step_id or not round_dir.is_dir():
        return None
    try:
        if not any(round_dir.iterdir()):
            return None
    except OSError:
        return None

    state = _load_json(round_dir / "round_state.json", {})
    previous_step_id = str(state.get("step_id") or "").strip() if isinstance(state, dict) else ""
    if previous_step_id == current_step_id:
        return None

    archive_root = Path(paths["optimize_output_dir"]) / "retry-history" / round_dir.name
    archive_root.mkdir(parents=True, exist_ok=True)
    previous_label = re.sub(r"[^A-Za-z0-9._-]+", "_", previous_step_id or "unknown-step")
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    archive_dir = archive_root / f"{timestamp}-{previous_label}"
    suffix = 2
    while archive_dir.exists():
        archive_dir = archive_root / f"{timestamp}-{previous_label}-{suffix}"
        suffix += 1
    shutil.move(str(round_dir), str(archive_dir))
    return archive_dir


def _recover_current_round_before_retry_archive(args) -> None:
    """Never archive a retry Round while its live Candidate may still be applied."""
    paths = resolve_paths(args)
    state_path = Path(paths["round_dir"]) / "round_state.json"
    state = _load_json(state_path, {})
    if not isinstance(state, dict) or not state:
        return
    current_step_id = str(getattr(args, "step_id", "") or _env("STEP_ID") or "").strip()
    previous_step_id = str(state.get("step_id") or "").strip()
    if not current_step_id or not previous_step_id or current_step_id == previous_step_id:
        return
    if str(state.get("status") or "") in {"ROUND_COMPLETED", "ROUND_COMPLETED_WITH_FALLBACK"}:
        return
    mutation_state = str(state.get("candidate_mutation_state") or "")
    tune_status = str((((state.get("steps") or {}).get("ensure-tune") or {}).get("status") or ""))
    may_be_dirty = mutation_state in {"possibly_applied", "applied"} or (
        not mutation_state and tune_status in {"RUNNING", "SUCCESS", "FALLBACK"}
    )
    if may_be_dirty:
        _restore_round_snapshot_before_tune_retry(args, paths, state, reason="same-Round Step retry recovery")


def action_watchdog_run_round(args):
    round_started_at = time.time()
    _recover_current_round_before_retry_archive(args)
    archived_round = _archive_round_for_new_step(args)
    if archived_round:
        _log(args, "watchdog", f"new Step retry archived previous Round state to {archived_round}", "WARN")
    # Recover from an interrupted previous round before touching the workspace.
    _handle_startup_interrupt(args)
    # Refuse to start if disk is critically low (best-effort check).
    ds = _check_disk_space(args, min_gb=DEFAULT_WATCHDOG_MIN_DISK_SPACE_GB)
    if not ds.get("ok", True):
        raise SystemExit(f"disk space too low to start round: {ds}")
    killed = _foreach_orphan_cleanup()
    if killed:
        print(f"[cleanup] killed {killed} orphaned agents at round start")
    step_results = []
    try:
        for name, fn in _round_sequence(args):
            result = _call_action_for_round_watchdog(args, name, fn, round_started_at=round_started_at)
            step_results.append(result)
        paths = resolve_paths(args)
        state_path = paths["round_dir"] / "round_state.json"
        state = _load_json(state_path, {})
        if not isinstance(state, dict):
            state = {}
        failed_steps = [r.get("action") for r in step_results if r.get("status") in {"FAILED", "FAILED_SKIP", "FALLBACK"}]
        status = "ROUND_COMPLETED_WITH_FALLBACK" if failed_steps else "ROUND_COMPLETED"
        state["status"] = status
        state["watchdog"] = {"enabled": True, "status": status, "failed_steps": failed_steps, "step_results": step_results, "elapsed_seconds": int(time.time() - round_started_at), "updated_at": _now()}
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _log(args, "watchdog", f"{status}, failed_steps={failed_steps}")
        out = {"status": status, "round": args.round, "roundDir": str(paths["round_dir"]), "failed_steps": failed_steps, "accepted": state.get("accepted")}
        _print_json(out)
        return out
    except KeyboardInterrupt:
        raise
    except BaseException as exc:
        error = _format_exception(exc)
        try:
            paths = resolve_paths(args)
            _log(args, "watchdog", f"ROUND FAILED: {error.get('message')}", "ERROR")
            _record_round_failure(args, error)
            state_path = paths["round_dir"] / "round_state.json"
            state = _load_json(state_path, {})
            if not isinstance(state, dict):
                state = {}
            state["status"] = "ROUND_FAILED"
            state["error"] = error
            state["failed_at"] = _now()
            state["watchdog"] = {"enabled": True, "status": "ROUND_FAILED", "step_results": step_results, "elapsed_seconds": int(time.time() - round_started_at), "updated_at": _now()}
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
        _report_round_failure(args, error)
        _cleanup_orphan_openclaw_agents()
        raise SystemExit(error.get("message") or "round failed")


def action_watchdog_loop(args):
    start = int(getattr(args, "start_round", None) or args.round or 1)
    resume_round = _infer_resume_round(args, start)
    if resume_round and resume_round > start:
        _log(args, "loop", f"auto-resume: advancing start round {start} -> {resume_round}", "WARN")
        start = resume_round
    max_rounds = int(getattr(args, "max_rounds", 5) or 5)
    paths_for_objective = resolve_paths(args)
    objective_criterion = _objective_completion_criterion(args, paths_for_objective)
    max_consecutive_failures = DEFAULT_WATCHDOG_MAX_CONSECUTIVE_FAILURES
    max_consecutive_degraded = DEFAULT_WATCHDOG_MAX_CONSECUTIVE_DEGRADED_ROUNDS
    original_round = args.round
    consecutive_failures = 0
    consecutive_degraded = 0
    try:
        for r in range(start, max_rounds + 1):
            args.round = r
            print(f"\n===== ClawEvolve watchdog loop: round {r}/{max_rounds} =====")
            interrupted_state = _check_previous_round_interrupted(args)
            if interrupted_state.get("interrupted"):
                print(json.dumps({"status": "RECOVERING", "round": r, "prev_round": interrupted_state["prev_round"], "prev_status": interrupted_state["prev_status"]}, ensure_ascii=False))
            try:
                result = action_watchdog_run_round(args)
                consecutive_failures = 0
                if result.get("status") == "ROUND_COMPLETED_WITH_FALLBACK":
                    consecutive_degraded += 1
                else:
                    consecutive_degraded = 0
            except KeyboardInterrupt:
                raise
            except BaseException as exc:
                consecutive_failures += 1
                consecutive_degraded = 0
                print(json.dumps({"status": "ROUND_FAILED", "round": r, "error": str(exc), "consecutive_failures": consecutive_failures, "max_consecutive_failures": max_consecutive_failures}, ensure_ascii=False))
                _cleanup_orphan_openclaw_agents()
                if consecutive_failures >= max_consecutive_failures:
                    print(json.dumps({"status": "ABORTED", "reason": "max consecutive round failures reached", "round": r, "max_consecutive_failures": max_consecutive_failures}, ensure_ascii=False))
                    break
                continue

            if consecutive_degraded >= max_consecutive_degraded:
                print(json.dumps({"status": "ABORTED", "reason": "max consecutive degraded rounds reached", "round": r, "consecutive_degraded": consecutive_degraded, "max_consecutive_degraded": max_consecutive_degraded}, ensure_ascii=False))
                break

            paths = resolve_paths(args)
            state = _load_json(paths["round_dir"] / "round_state.json", {})
            val_bench = (state.get("bench") or {}).get("validation") or {} if isinstance(state, dict) else {}
            score = (val_bench.get("summary") or {}).get("score")
            accepted = bool(state.get("accepted")) if isinstance(state, dict) else False
            stop, stop_reason = _should_stop_for_objective(
                accepted=accepted, validation_score=score, criterion=objective_criterion,
            )
            if stop:
                print(json.dumps({"status": "STOPPED", "reason": stop_reason, "round": r, "validation_score": score, "objectiveCompletion": objective_criterion}, ensure_ascii=False))
                break
    finally:
        args.round = original_round


# ═══════════════════════════════════════════════════════════════════════════════
# run-round / loop
# ═══════════════════════════════════════════════════════════════════════════════

def _call_action_for_round(args, name: str, fn):
    if getattr(args, "resume", True) and not getattr(args, "force", False) and _step_done(args, name):
        print(json.dumps({"action": name, "status": "SKIPPED", "reason": "outputs already exist"}, ensure_ascii=False))
        _mark_step(args, name, "SKIPPED", {"reason": "outputs already exist"})
        _log(args, name, "SKIPPED (outputs already exist)")
        return
    _log(args, name, "RUNNING")
    _mark_step(args, name, "RUNNING")
    fn(args)
    if name not in {"bench-opt", "bench-val", "prepare"}:
        _mark_step(args, name, "SUCCESS")

def action_run_round(args):
    paths = resolve_paths(args)
    _recover_current_round_before_retry_archive(args)
    archived_round = _archive_round_for_new_step(args)
    if archived_round:
        _log(args, "run-round", f"new Step retry archived previous Round state to {archived_round}", "WARN")
    _handle_startup_interrupt(args)
    killed = _cleanup_orphan_openclaw_agents()
    if killed:
        _log(args, "run-round", f"cleanup: killed {killed} orphaned agents at round start")
    # Keep the legacy run-round entrypoint on the same canonical sequence as
    # watchdog.  Every Optimize entry therefore captures/registers the initial
    # Pack before Tune and publishes Review material before OSS upload.
    sequence = _round_sequence(args)

    step_names = [name for name, _ in sequence]
    _log(args, "run-round", f"started: round={args.round}, task_id={paths['task_id']}, steps={step_names}")
    for name, fn in sequence:
        _call_action_for_round(args, name, fn)
    summary = _load_json(paths["round_dir"] / "round_state.json", {})
    _log(args, "run-round", f"completed: round={args.round}, accepted={summary.get('accepted')}")
    _print_json({"status": "ROUND_COMPLETED", "round": args.round, "roundDir": str(paths["round_dir"]), "accepted": summary.get("accepted")})

def action_loop(args):
    start = int(getattr(args, "start_round", None) or args.round or 1)
    resume_round = _infer_resume_round(args, start)
    if resume_round and resume_round > start:
        _log(args, "loop", f"auto-resume: advancing start round {start} -> {resume_round}", "WARN")
        start = resume_round
    max_rounds = int(getattr(args, "max_rounds", 5) or 5)
    objective_criterion = _objective_completion_criterion(args, resolve_paths(args))
    original_round = args.round
    _log(args, "loop", f"started: start_round={start}, max_rounds={max_rounds}, objective_completion={objective_criterion}")
    for r in range(start, max_rounds + 1):
        args.round = r
        _log(args, "loop", f"round {r}/{max_rounds} starting")
        print(f"\n===== ClawEvolve loop: round {r}/{max_rounds} =====")
        action_run_round(args)
        paths = resolve_paths(args)
        state = _load_json(paths["round_dir"] / "round_state.json", {})
        val_bench = (state.get("bench") or {}).get("validation") or {}
        score = (val_bench.get("summary") or {}).get("score")
        accepted = bool(state.get("accepted")) if isinstance(state, dict) else False
        stop, stop_reason = _should_stop_for_objective(
            accepted=accepted, validation_score=score, criterion=objective_criterion,
        )
        if stop:
            _log(args, "loop", f"stopped: {stop_reason} at round {r}, score={score}")
            print(json.dumps({"status": "STOPPED", "reason": stop_reason, "round": r, "validation_score": score, "objectiveCompletion": objective_criterion}, ensure_ascii=False))
            break
    args.round = original_round
    _log(args, "loop", "finished")

def action_validate_round(args):
    paths = resolve_paths(args)
    checks = {}
    for step, fn in STEP_OUTPUTS.items():
        if step == "restore":
            continue
        try:
            checks[step] = {"ok": all(Path(x).exists() for x in fn(paths, args)), "paths": [str(x) for x in fn(paths, args)]}
        except Exception as exc:
            checks[step] = {"ok": False, "error": str(exc)}
    # bench checks via round_state
    state = _load_json(paths["round_dir"] / "round_state.json", {})
    for kind in ("optimization", "validation"):
        bench_info = (state.get("bench") or {}).get(kind) or {}
        checks[f"bench-{kind[:3]}"] = {"ok": bool(bench_info.get("resultPath")), "score": (bench_info.get("summary") or {}).get("score")}
    _print_json({"round": args.round, "roundDir": str(paths["round_dir"]), "checks": checks})

def action_doctor(args):
    paths = resolve_paths(args) if (args.task_id or _env("TASK_ID") or _env("EVOLVE_RUN_ID")) else None
    workspace = _resolve_workspace(args)
    skill_base = _resolve_skill_base(args)
    result = {
        "python": sys.executable,
        "workspace": {"path": str(workspace), "exists": workspace.exists()},
        "skill_base": {"path": str(skill_base), "exists": skill_base.exists()},
        "skills": {},
        "artifact_transport": {"type": "clawweb-signed-url", "bucket": OSS_CONFIG.get("BUCKET_NAME")},
    }
    for name in ["clawbench-base", "clawevolve-tune", "clawevolve-review", "clawevolve-pack", "clawevolve-deploy"]:
        found = skill_base / name
        if not found.exists():
            found = None
        result["skills"][name] = str(found) if found else "MISSING"
    try:
        cb_home = resolve_clawbench_home(args, paths)
        result["clawbench_local"] = {
            "home": str(cb_home),
            "benchmark_py": str(cb_home / "scripts" / "benchmark.py"),
            "benchmark_py_exists": (cb_home / "scripts" / "benchmark.py").is_file(),
        }
    except Exception as exc:
        result["clawbench_local"] = {"error": str(exc)}
    if paths:
        opt_dir = _local_template_dir(args, "optimization", paths)
        val_dir = _local_template_dir(args, "validation", paths)
        result["local_templates"] = {
            "optimization": {"path": str(opt_dir), "exists": opt_dir.is_dir(), "task_count": len(list(opt_dir.glob("task_*.md"))) if opt_dir.is_dir() else 0},
            "validation": {"path": str(val_dir), "exists": val_dir.is_dir(), "task_count": len(list(val_dir.glob("task_*.md"))) if val_dir.is_dir() else 0},
        }
    _print_json(result)

def action_full(args):
    task_id = args.task_id or "{{TASK_ID}}"
    print(f"""
╔═══════════════════════════════════════════════════════════════════════════════
║  ClawEvolve v4 — Section 12 Effect-first round
║  task_id: {task_id}
╚═══════════════════════════════════════════════════════════════════════════════

## 单轮执行

  python3 clawevolve_optimize_run.py --action run-round --task-id {task_id} --round 1

## 核心步骤

  prepare → baseline-pack(each round) → load-baseline-opt → ensure-tune
  → bench-full-opt(train/informational) → bench-val(test) → accept
  → pack → restore(if rejected) → ensure-review → complete/report

关键语义：Test Bench 分数严格高于 baseline 才通过；Train Bench 仅展示；Review 只生成下一轮 Spec，不覆盖本轮 Bench 决策。
""")


# ═══════════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="ClawEvolve 自进化流程 runner v4",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--action", required=True, help="prepare|load-baseline-opt|ensure-tune|candidate-static-gate|bench-targeted-opt|candidate-opt-gate|bench-full-opt|bench-val|accept|replicate-validation|pack|restore|ensure-review|complete|run-round|loop|validate-round|doctor|full")
    parser.add_argument("--task-id", help="evolve run id (used as task_id)")
    parser.add_argument("--round", type=int, default=1, help="round number (1-based)")
    parser.add_argument("--model", default="", help="model id for bench")
    parser.add_argument("--optimizer-model", default="", help="model id for tune/review agents")
    parser.add_argument("--tune-model", default="", help="model id for tune agent")
    parser.add_argument("--review-model", default="", help="model id for review agent")
    parser.add_argument("--workspace", help="workspace dir")
    parser.add_argument("--skill-base-dir", help="skills base dir")
    parser.add_argument("--step-id", help="ClawWeb step id")
    parser.add_argument("--suite", default="all")
    parser.add_argument("--judge", default="", help="judge model/backend passed to clawevolve-bench")
    parser.add_argument("--bench-mode", choices=["local", "adapter"], default=None,
                        help="defaults to adapter when Train/Test Domain is provided, otherwise local")
    parser.add_argument("--openclaw-execution-mode", choices=["local", "gateway"], default=os.environ.get("CLAWBENCH_OPENCLAW_EXECUTION_MODE", "local"))
    parser.add_argument("--clawbench-home", help="clawbench-base skill root")
    parser.add_argument("--local-opt-template-dir")
    parser.add_argument("--local-val-template-dir")
    parser.add_argument("--bench-timeout", type=int, default=86400)
    parser.add_argument(
        "--tune-timeout", type=int, default=TUNE_AGENT_TIMEOUT,
        help="maximum seconds for one tune agent command (also passed to openclaw --timeout)",
    )
    parser.add_argument(
        "--review-timeout", type=int, default=REVIEW_AGENT_TIMEOUT,
        help="maximum seconds for one review agent command (also passed to openclaw --timeout)",
    )
    parser.add_argument(
        "--agent-idle-timeout", type=int, default=DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS,
        help="kill tune/review only after this many seconds without CLI stdout; 0 disables (default)",
    )
    parser.add_argument(
        "--round-timeout", type=int, default=DEFAULT_WATCHDOG_ROUND_TIMEOUT_SECONDS,
        help="maximum wall-clock seconds for one round; 0 disables (default)",
    )
    parser.add_argument("--train-bench-domain-id")
    parser.add_argument("--test-bench-domain-id")
    parser.add_argument("--owner-id")
    parser.add_argument("--clawweb-url")
    parser.add_argument("--clawweb-url-legacy", "--clawweb-url-camel", dest="clawweb_url_camel", help="遗留兼容参数，对应历史字段 clawwebUrl")
    parser.add_argument("--bench-run-id")
    parser.add_argument("--bench-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--result-path")
    parser.add_argument("--artifact-path")
    parser.add_argument("--baseline-artifact")
    parser.add_argument("--start-round", type=int, default=1)
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument("--strict-bench", action="store_true")
    parser.add_argument(
        "--enforce-candidate-gate", action="store_true", default=DEFAULT_ENFORCE_CANDIDATE_GATE,
        help="make static candidate findings block targeted/full/validation execution; default is advisory-only",
    )
    parser.add_argument("--disable-staged-bench", dest="staged_bench", action="store_false", help="disable tune-proposed staged validation bench_plan.json; default is enabled when a valid plan exists")
    parser.add_argument("--acceptance-margin", type=float, default=DEFAULT_ACCEPTANCE_MARGIN, help="legacy compatibility option; Bench acceptance uses strict candidate Test score > baseline Test score")
    parser.add_argument("--exploration-loss-budget", type=float, default=DEFAULT_EXPLORATION_LOSS_BUDGET, help="max validation score drop allowed for exploratory_keep structural candidates; 0 disables")
    parser.add_argument("--paired-min-runs", type=int, default=DEFAULT_PAIRED_MIN_RUNS, help="minimum paired task/repeated eval pairs required inside the acceptance margin")
    parser.add_argument("--paired-min-win-rate", type=float, default=DEFAULT_PAIRED_MIN_WIN_RATE, help="minimum win rate for paired eval acceptance")
    parser.add_argument("--replication-band", type=float, default=DEFAULT_REPLICATION_BAND, help="single-run negative deltas within this distance of the margin are inconclusive and replicated")
    parser.add_argument("--protected-max-drop", type=float, default=DEFAULT_PROTECTED_MAX_DROP, help="default protected canary maximum task-score drop")
    parser.add_argument("--full-opt-max-regressed-count", type=int, default=DEFAULT_FULL_OPT_MAX_REGRESSED_COUNT)
    parser.add_argument("--full-opt-max-regressed-ratio", type=float, default=DEFAULT_FULL_OPT_MAX_REGRESSED_RATIO)
    parser.add_argument("--full-opt-max-negative-delta", type=float, default=DEFAULT_FULL_OPT_MAX_NEGATIVE_DELTA)
    parser.add_argument("--full-opt-max-single-drop", type=float, default=DEFAULT_FULL_OPT_MAX_SINGLE_DROP)
    parser.add_argument("--full-opt-min-mean-delta", type=float, default=DEFAULT_FULL_OPT_MIN_MEAN_DELTA)
    parser.add_argument("--expected-min-delta", type=float, default=DEFAULT_EXPECTED_MIN_DELTA, help="default minimum expected-fix task-score delta")
    parser.add_argument("--sampling-seed", type=int, default=None, help="request the same sampling seed for baseline/candidate replication when supported")
    parser.add_argument("--temperature", type=float, default=None, help="evaluation sampling temperature identity field")
    parser.add_argument("--max-tokens", type=int, default=None, help="evaluation max_tokens identity field")
    parser.add_argument("--pack-include-evolve-results", action="store_true", help="opt-in: include clawevolve_results in accepted artifacts")
    parser.add_argument("--max-artifact-mb", type=float, default=DEFAULT_MAX_ARTIFACT_MB, help="artifact size guard in MiB; 0 disables")
    parser.add_argument("--skip-pack-dry-run", dest="pack_dry_run", action="store_false", help="skip deploy --dry-run validation after accepted pack")
    parser.add_argument("--skip-restore-precheck", dest="restore_precheck", action="store_false", help="skip deploy --dry-run validation before restore")
    parser.add_argument("--skip-clawweb",
    action="store_true")
    parser.add_argument("--skip-oss", action="store_true")
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.set_defaults(resume=True, pack_dry_run=DEFAULT_PACK_DRY_RUN, restore_precheck=DEFAULT_RESTORE_PRECHECK)

    args = parser.parse_args()
    if args.bench_mode is None:
        args.bench_mode = "adapter" if args.train_bench_domain_id or args.test_bench_domain_id else "local"

    actions = {
        "prepare":           action_prepare,
        "baseline-pack":     action_baseline_pack,
        "load-baseline-opt": action_load_baseline_opt,
        "candidate-static-gate": action_candidate_static_gate,
        "bench-targeted-opt": action_bench_candidate_opt_targeted,
        "candidate-opt-gate": action_candidate_opt_gate,
        "bench-full-opt": action_bench_candidate_opt_full,
        "bench-opt":         action_bench_opt,
        "ensure-tune":       action_ensure_tune,
        "bench-val":         action_bench_val,
        "accept":            action_accept,
        "replicate-validation": action_replicate_validation,
        "pack":              action_pack,
        "restore":           action_restore,
        "ensure-review":       action_ensure_review,
        "upload-clawweb":    action_upload_clawweb,
        "upload-oss":        action_publish_round_oss,
        "complete":          action_complete,
        "run-round":         action_watchdog_run_round,
        "loop":              action_watchdog_loop,
        "watchdog-run-round": action_watchdog_run_round,
        "watchdog-loop":     action_watchdog_loop,
        "run-round-legacy":  action_run_round,
        "loop-legacy":       action_loop,
        "validate-round":    action_validate_round,
        "doctor":            action_doctor,
        "full":              action_full,
    }

    try:
        actions[args.action](args)
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR [{args.action}]: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
