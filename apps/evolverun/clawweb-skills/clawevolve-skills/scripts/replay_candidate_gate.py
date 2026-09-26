#!/usr/bin/env python3
"""Replay ClawEvolve candidate gates from downloaded run artifacts.

This tool never runs an agent, benchmark, deploy, upload, or restore.  It rewrites
remote report paths in round_state.json to their downloaded local counterparts and
re-executes the pure candidate effect gate against the recorded reports.

Typical usage:
  python3 scripts/replay_candidate_gate.py temp/EV-... --all-rounds
  python3 scripts/replay_candidate_gate.py temp/EV-... --round 2 --json
  python3 scripts/replay_candidate_gate.py temp/EV-... --all-rounds --fail-on-anomaly
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
HANDLER_PATH = REPO_ROOT / "clawevolve-workflow/scripts/handlers/clawevolve_optimize_run.py"


def _load_handler():
    handler_dir = str(HANDLER_PATH.parent)
    if handler_dir not in sys.path:
        sys.path.insert(0, handler_dir)
    spec = importlib.util.spec_from_file_location("clawevolve_optimize_replay_target", HANDLER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load handler: {HANDLER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path, default: Any = None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _find_by_basename(root: Path, declared: str) -> Path | None:
    name = Path(str(declared or "")).name
    if not name:
        return None
    matches = [p for p in root.rglob(name) if p.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime_ns)


def _latest(root: Path, pattern: str) -> Path | None:
    matches = [p for p in root.glob(pattern) if p.is_file()]
    return max(matches, key=lambda p: p.stat().st_mtime_ns) if matches else None


def _resolve_report(run_dir: Path, round_dir: Path, declared: str, kind: str) -> Path | None:
    declared_path = Path(str(declared or ""))
    if declared_path.is_file():
        return declared_path
    found = _find_by_basename(run_dir, declared)
    if found:
        return found
    if kind == "baseline":
        return _latest(run_dir, "bench/baseline/**/train/output/benchmark/**/*_benchmark_report.json")
    if kind == "targeted":
        return _latest(round_dir, "bench/candidate_optimization_targeted/output/benchmark/**/*_benchmark_report.json")
    return None


def _signal_observability(mod, baseline: Path | None, candidate: Path | None, plan: dict) -> list[dict]:
    rows = []
    for group in ("expected_signals", "protected_signals"):
        for index, signal in enumerate(plan.get(group) or []):
            if not isinstance(signal, dict):
                continue
            task_id = str(signal.get("task_id") or "")
            metric = str(signal.get("metric") or "score")
            before = mod._extract_candidate_metric(baseline or Path("__missing_baseline__"), task_id, metric)
            after = mod._extract_candidate_metric(candidate or Path("__missing_candidate__"), task_id, metric)
            rows.append({
                "group": group,
                "index": index,
                "role": signal.get("role", "required" if group == "expected_signals" else "protected"),
                "task_id": task_id,
                "metric": metric,
                "baseline_observable": before.get("observable") is True,
                "baseline_value": before.get("value"),
                "baseline_error": before.get("error") or "",
                "candidate_observable": after.get("observable") is True,
                "candidate_value": after.get("value"),
                "candidate_error": after.get("error") or "",
            })
    return rows


def _set_report_metric(report: dict, task_id: str, metric: str, value: float) -> bool:
    task = next((t for t in report.get("tasks") or [] if str(t.get("task_id") or t.get("id") or t.get("name") or "") == task_id), None)
    if not isinstance(task, dict):
        return False
    grading = task.setdefault("grading", {})
    runs = grading.setdefault("runs", [{}])
    if not runs or not isinstance(runs[0], dict):
        runs[:] = [{}]
    run = runs[0]
    if metric == "score":
        run["score"] = value
        return True
    # Fully-qualified breakdown metrics are the canonical executable signal form.
    if metric.startswith(("automated.", "llm_judge.", "breakdown.")):
        key = metric[len("breakdown."):] if metric.startswith("breakdown.") else metric
        run.setdefault("breakdown", {})[key] = value
        return True
    return False


def _perfect_candidate_simulation(mod, replay_state: dict, plan: dict, baseline: Path | None) -> dict:
    if not baseline or not baseline.is_file() or not plan.get("valid"):
        return {"available": False, "valid": None, "reason": "baseline or valid signal plan unavailable"}
    report = _load_json(baseline, {})
    if not isinstance(report, dict):
        return {"available": False, "valid": None, "reason": "baseline report is invalid"}
    report = copy.deepcopy(report)
    unsupported = []
    assigned = {}
    # Apply required/supporting expected targets. Protected metrics are left at baseline,
    # which is the strongest possible preservation result.
    for signal in plan.get("expected_signals") or []:
        if not isinstance(signal, dict):
            continue
        task_id = str(signal.get("task_id") or "")
        metric = str(signal.get("metric") or "score")
        before = mod._extract_candidate_metric(baseline, task_id, metric)
        if not before.get("observable"):
            unsupported.append(f"unobservable baseline: {task_id}::{metric}")
            continue
        value = float(before["value"])
        direction = str(signal.get("direction") or "increase")
        delta = signal.get("min_delta")
        try:
            delta = float(delta) if delta is not None else (0.01 if metric == "score" else 1.0)
        except Exception:
            delta = 0.01 if metric == "score" else 1.0
        if direction == "increase":
            target = value + max(delta, 0.000001)
            if signal.get("expected_min") is not None:
                target = max(target, float(signal["expected_min"]))
        elif direction == "decrease":
            target = value - max(delta, 0.000001)
            if signal.get("expected_min") is not None:
                target = min(target, float(signal["expected_min"]))
        elif direction == "boolean_flip":
            target = float(signal.get("expected_value", 1))
        else:
            unsupported.append(f"unsupported direction: {task_id}::{metric}::{direction}")
            continue
        if not _set_report_metric(report, task_id, metric, target):
            unsupported.append(f"cannot synthesize metric: {task_id}::{metric}")
        else:
            assigned[f"{task_id}::{metric}"] = target
    with tempfile.TemporaryDirectory(prefix="clawevolve-perfect-candidate-") as root:
        candidate = Path(root) / "candidate.json"
        candidate.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        state = copy.deepcopy(replay_state)
        state.setdefault("bench", {}).setdefault("candidate_optimization_targeted", {}).update({
            "status": "succeeded", "resultPath": str(candidate), "signalPlanId": plan.get("plan_id"),
        })
        try:
            fn = mod._candidate_opt_effect_report
            gate = fn(state, plan) if "plan" in inspect.signature(fn).parameters else fn(state)
            return {
                "available": True, "valid": gate.get("valid"), "decision": gate.get("decision"),
                "effect_gate_passed": gate.get("effect_gate_passed"),
                "protected_gate_passed": gate.get("protected_gate_passed"),
                "reasons": gate.get("reasons") or [], "assigned": assigned, "unsupported": unsupported,
            }
        except Exception as exc:
            return {"available": True, "valid": False, "reason": f"{type(exc).__name__}: {exc}", "assigned": assigned, "unsupported": unsupported}

def _baseline_stability(mod, run_dir: Path, threshold: float = 0.10) -> dict:
    reports = sorted(p for p in run_dir.glob("bench/baseline/**/train/output/benchmark/**/*_benchmark_report.json") if p.is_file())
    values = {}
    for report in reports:
        for task_id, score in mod._task_scores_from_report(report).items():
            if score is not None:
                values.setdefault(task_id, []).append({"report": str(report), "score": float(score)})
    tasks = []
    for task_id, samples in sorted(values.items()):
        scores = [x["score"] for x in samples]
        spread = max(scores) - min(scores) if scores else 0.0
        tasks.append({"task_id": task_id, "sample_count": len(scores), "min": min(scores), "max": max(scores), "spread": spread, "unstable": len(scores) > 1 and spread > threshold, "samples": samples})
    return {"report_count": len(reports), "threshold": threshold, "unstable": any(x["unstable"] for x in tasks), "tasks": tasks}

def replay_round(mod, run_dir: Path, round_id: int) -> dict:
    round_dir = run_dir / "optimize/output" / f"round-{round_id:03d}"
    state_path = round_dir / "round_state.json"
    state = _load_json(state_path, {})
    if not isinstance(state, dict):
        return {"round": round_id, "ok": False, "errors": [f"invalid state: {state_path}"]}

    replay_state = copy.deepcopy(state)
    baseline_stability = _baseline_stability(mod, run_dir)
    plan = replay_state.get("candidate_opt_signal_plan")
    if not isinstance(plan, dict):
        plan = _load_json(round_dir / "tune/candidate_opt_signal_plan.json", {})
    if not isinstance(plan, dict):
        plan = {}
    replay_state["candidate_opt_signal_plan"] = plan

    baseline_record = replay_state.get("baseline_optimization") if isinstance(replay_state.get("baseline_optimization"), dict) else {}
    targeted_record = ((replay_state.get("bench") or {}).get("candidate_optimization_targeted") or {})
    baseline = _resolve_report(run_dir, round_dir, str(baseline_record.get("result_path") or ""), "baseline")
    candidate = _resolve_report(run_dir, round_dir, str(targeted_record.get("resultPath") or ""), "targeted")
    baseline_record["result_path"] = str(baseline or "")
    targeted_record["resultPath"] = str(candidate or "")
    replay_state["baseline_optimization"] = baseline_record
    replay_state.setdefault("bench", {})["candidate_optimization_targeted"] = targeted_record

    observability = _signal_observability(mod, baseline, candidate, plan)
    perfect_simulation = _perfect_candidate_simulation(mod, replay_state, plan, baseline)
    unavailable = [r for r in observability if not r["baseline_observable"] or not r["candidate_observable"]]

    recorded = state.get("candidate_opt_gate") if isinstance(state.get("candidate_opt_gate"), dict) else _load_json(round_dir / "tune/candidate_opt_gate.json", {})
    try:
        effect_fn = mod._candidate_opt_effect_report
        if "plan" in inspect.signature(effect_fn).parameters:
            replayed = effect_fn(replay_state, plan)
        else:
            replayed = effect_fn(replay_state)
        replay_error = ""
    except Exception as exc:  # diagnostic tool must report, not hide, replay crashes
        replayed = {}
        replay_error = f"{type(exc).__name__}: {exc}"

    targeted_status = str(targeted_record.get("status") or "")
    anomalies = []
    if plan.get("valid") is True and targeted_status == "succeeded" and unavailable:
        anomalies.append("valid signal plan references metrics that the gate cannot observe")
    if targeted_status == "succeeded" and not candidate:
        anomalies.append("targeted bench says succeeded but its report cannot be resolved locally")
    if baseline_record.get("available", True) and not baseline:
        anomalies.append("baseline optimization report cannot be resolved locally")
    if recorded and replayed and recorded.get("valid") != replayed.get("valid"):
        anomalies.append("recorded and replayed candidate gate decisions differ under current code")
    if recorded and replayed and recorded.get("decision") != replayed.get("decision"):
        anomalies.append("recorded and replayed candidate gate classifications differ under current code")
    if recorded and replayed and list(recorded.get("reasons") or []) != list(replayed.get("reasons") or []):
        anomalies.append("recorded and replayed candidate gate reasons differ under current code")
    if replay_error:
        anomalies.append("candidate effect replay crashed")
    if perfect_simulation.get("available") and perfect_simulation.get("valid") is not True:
        anomalies.append("a synthetic perfect candidate cannot pass the compiled gate contract")
    if baseline_stability.get("unstable"):
        anomalies.append("duplicate bootstrap baselines show task-score drift above the stability threshold")

    return {
        "round": round_id,
        "ok": not replay_error,
        "paths": {"state": str(state_path), "baseline_report": str(baseline or ""), "candidate_report": str(candidate or "")},
        "static_gate": {"valid": ((state.get("candidate_gate") or {}).get("valid")), "reasons": ((state.get("candidate_gate") or {}).get("reasons") or [])},
        "signal_plan": {"valid": plan.get("valid"), "plan_id": plan.get("plan_id"), "errors": plan.get("errors") or [], "signal_count": len(observability)},
        "targeted_bench": {"status": targeted_status, "score": ((targeted_record.get("summary") or {}).get("score"))},
        "observability": observability,
        "unobservable_count": len(unavailable),
        "recorded_gate": {k: recorded.get(k) for k in ("valid", "decision", "evaluation_status", "effect_gate_passed", "protected_gate_passed", "behavior_changed", "reasons")} if isinstance(recorded, dict) else {},
        "replayed_gate": {k: replayed.get(k) for k in ("valid", "decision", "evaluation_status", "effect_gate_passed", "effect_target_attained", "protected_gate_passed", "behavior_changed", "reasons", "expected_results", "supporting_expected_results", "protected_results")} if isinstance(replayed, dict) else {},
        "perfect_candidate_simulation": perfect_simulation,
        "baseline_stability": baseline_stability,
        "replay_error": replay_error,
        "anomalies": anomalies,
    }


def _round_ids(run_dir: Path) -> list[int]:
    out = []
    for path in sorted((run_dir / "optimize/output").glob("round-*/round_state.json")):
        try:
            out.append(int(path.parent.name.rsplit("-", 1)[1]))
        except Exception:
            pass
    return out


def _print_human(results: list[dict]) -> None:
    for result in results:
        print(f"\n=== round-{result['round']:03d} ===")
        if not result.get("ok"):
            print(f"REPLAY ERROR: {result.get('replay_error') or result.get('errors')}")
        print(f"static={result.get('static_gate', {}).get('valid')} plan={result.get('signal_plan', {}).get('valid')} targeted={result.get('targeted_bench', {}).get('status')}")
        print(f"recorded={result.get('recorded_gate', {}).get('decision')} valid={result.get('recorded_gate', {}).get('valid')}")
        print(f"replayed={result.get('replayed_gate', {}).get('decision')} valid={result.get('replayed_gate', {}).get('valid')}")
        print(f"signals={result.get('signal_plan', {}).get('signal_count')} unobservable={result.get('unobservable_count')}")
        sim = result.get("perfect_candidate_simulation") or {}
        print(f"perfect-candidate={sim.get('decision')} valid={sim.get('valid')}")
        stability = result.get("baseline_stability") or {}
        unstable = [x for x in stability.get("tasks") or [] if x.get("unstable")]
        if stability.get("report_count", 0) > 1:
            print(f"baseline-replicates={stability.get('report_count')} unstable_tasks={len(unstable)}")
            for item in unstable:
                print(f"  [BASELINE-DRIFT] {item['task_id']} range={item['min']:.6f}..{item['max']:.6f} spread={item['spread']:.6f}")
        for row in result.get("observability") or []:
            status = "OK" if row["baseline_observable"] and row["candidate_observable"] else "UNOBSERVABLE"
            print(f"  [{status}] {row['group']}[{row['index']}] {row['task_id']} :: {row['metric']}  {row['baseline_value']} -> {row['candidate_value']}")
        for anomaly in result.get("anomalies") or []:
            print(f"  ANOMALY: {anomaly}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline replay for ClawEvolve candidate gates")
    parser.add_argument("run_dir", type=Path, help="downloaded EV-* run directory")
    parser.add_argument("--round", type=int, dest="round_id")
    parser.add_argument("--all-rounds", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-anomaly", action="store_true")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    ids = _round_ids(run_dir)
    if args.round_id is not None:
        ids = [args.round_id]
    elif not args.all_rounds:
        ids = ids[-1:] if ids else []
    if not ids:
        parser.error(f"no round_state.json found under {run_dir}")

    mod = _load_handler()
    results = [replay_round(mod, run_dir, round_id) for round_id in ids]
    if args.json:
        print(json.dumps({"run_dir": str(run_dir), "results": results}, ensure_ascii=False, indent=2))
    else:
        _print_human(results)
    if any(not r.get("ok") for r in results):
        return 2
    if args.fail_on_anomaly and any(r.get("anomalies") for r in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
