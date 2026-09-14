#!/usr/bin/env python3
"""Bootstrap bench -> spec-v0 -> optimize for ClawEvolve.

This helper runs a bootstrap benchmark over a user-provided template source
(domainId or local templates path), derives objective/spec-v0 from the bench
result, prepares the current clawevolve run layout, and then hands off to the
existing clawevolve_optimize_run.py workflow.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_clawevolve_bench import run_clawevolve_bench
from lib_http_retry import retry_http

DEFAULT_WORKSPACE = Path("/home/admin/.openclaw/workspace")
DEFAULT_BENCH_MODEL = os.environ.get("CLAWEVOLVE_BENCH_MODEL", "openai/gpt-4.1-mini")
DEFAULT_OPTIMIZER_MODEL = os.environ.get("CLAWEVOLVE_OPTIMIZER_MODEL", "openai/gpt-4.1-mini")
DEFAULT_BENCH_SUITE = os.environ.get("CLAWEVOLVE_BENCH_SUITE", "all")
DEFAULT_BENCH_SCENE = os.environ.get("CLAWEVOLVE_BENCH_SCENE", "clawevolve-bootstrap")

_PERCENT_METRIC_PATTERNS = (
    re.compile(
        r"(?P<label>(?:MCP\s*(?:调用|call)?|任务|task\s*)?(?:调用)?(?:成功率|完成率|success\s*rate|completion\s*rate))"
        r"\s*(?:要达到|达到|达|为|至|不低于|至少|>=|≥|:|：|=)?\s*(?P<value>\d+(?:\.\d+)?)\s*%",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<value>\d+(?:\.\d+)?)\s*%\s*(?P<label>(?:MCP\s*(?:调用|call)?|任务|task\s*)?(?:调用)?(?:成功率|完成率|success\s*rate|completion\s*rate))",
        re.IGNORECASE,
    ),
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("{{") and text.endswith("}}"):
        return ""
    return text


def _primary_metric(objective_text: str, default_target: float = 0.90) -> dict[str, Any]:
    """Normalize the frozen Bench objective with the same Plan metric contract."""
    text = str(objective_text or "").strip()
    for pattern in _PERCENT_METRIC_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        target = max(0.0, min(1.0, float(match.group("value")) / 100.0))
        normalized = re.sub(r"\s+", "", match.group("label")).lower()
        if "mcp" in normalized:
            name, display_name = "mcp_call_success_rate", "MCP 调用成功率"
        elif "完成率" in normalized or "completionrate" in normalized:
            name, display_name = "task_completion_rate", "任务完成率"
        elif "任务" in normalized or normalized.startswith("task"):
            name, display_name = "task_success_rate", "任务成功率"
        else:
            name, display_name = "success_rate", "成功率"
        return {
            "name": name, "display_name": display_name, "operator": ">=",
            "target": target, "unit": "ratio", "source": "user_goal",
        }
    return {
        "name": "task_success_rate", "display_name": "任务成功率", "operator": ">=",
        "target": float(default_target), "unit": "ratio", "source": "default",
    }


def _metric_requirement(metric: dict[str, Any]) -> str:
    return f"{metric['display_name']} {metric['operator']} {float(metric['target']) * 100:g}%"


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip())
    value = re.sub(r"-+", "-", value).strip("-_.").lower()
    return value or datetime.now().strftime("clawevolve-bench-%Y%m%d-%H%M%S")


def _read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return default


def _load_json(path: Path, default=None):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    try:
        h = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _tree_identity(root: Path, max_files: int = 1000) -> dict[str, Any]:
    try:
        root = Path(root)
        if not root.is_dir():
            return {"path": str(root), "exists": False, "sha256": ""}
        files = sorted(path for path in root.rglob("*") if path.is_file())[:max_files]
        h = hashlib.sha256()
        for path in files:
            rel = path.relative_to(root).as_posix()
            h.update(rel.encode("utf-8")); h.update(b"\0")
            h.update(_sha256_file(path).encode("ascii")); h.update(b"\0")
        return {"path": str(root), "exists": True, "file_count_hashed": len(files), "sha256": h.hexdigest()}
    except Exception as exc:
        return {"path": str(root), "error": f"{type(exc).__name__}: {exc}", "sha256": ""}


def _bootstrap_eval_identity(args, report_path: Path, report: dict[str, Any], input_path: Path) -> dict[str, Any]:
    return {
        "schema_version": "evolution.eval_identity.v1",
        "bench_model": report.get("model") or args.model or DEFAULT_BENCH_MODEL,
        "benchmark_version": report.get("benchmark_version"),
        "bench_mode": "adapter",
        "suite": report.get("suite") or args.suite or DEFAULT_BENCH_SUITE,
        "validation_fixture": _tree_identity(input_path),
        "report_sha256": _sha256_file(report_path),
    }


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    def request_once() -> str:
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ClawWeb {method} {url} returned HTTP {exc.code}: {raw[-4000:]}") from exc
    raw = retry_http(request_once, label=f"ClawWeb {method}")
    value = json.loads(raw) if raw else {}
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required from {url}")
    return value


def _step_urls(args: argparse.Namespace) -> tuple[str, str]:
    base = (_clean(args.clawweb_url) or "http://127.0.0.1:5173").rstrip("/")
    task_id = urllib.parse.quote(_clean(args.task_id), safe="")
    step_id = urllib.parse.quote(_clean(args.step_id), safe="")
    return (f"{base}/api/evolve/internal/tasks/{task_id}/steps/{step_id}/input",
            f"{base}/api/evolve/internal/tasks/{task_id}/steps/{step_id}/report")


def _load_frozen_input(args: argparse.Namespace, run_dir: Path) -> tuple[str, dict[str, Any]]:
    input_url, _ = _step_urls(args)
    payload = _http_json("GET", input_url)
    config = ((payload.get("task") or {}).get("config") or {})
    if not isinstance(config, dict):
        raise RuntimeError("Bench evolution Task config must be an object")
    objective = _clean(config.get("objective"))
    if not objective:
        raise RuntimeError("Bench evolution objective is required")
    expected = {
        "train": _clean(config.get("trainBenchDomainId")),
        "test": _clean(config.get("testBenchDomainId")),
    }
    if expected != {"train": _clean(args.train_domain_id), "test": _clean(args.test_domain_id)}:
        raise RuntimeError(f"Bench evolution domain mismatch: command={args.train_domain_id}/{args.test_domain_id}, input={expected}")
    pinned = config.get("pinnedBenchDomains") if isinstance(config.get("pinnedBenchDomains"), dict) else {}
    owner_id = _clean(config.get("ownerUserId")) or _resolve_owner_id(args)
    for role, domain_id in expected.items():
        templates = pinned.get(domain_id) if isinstance(pinned, dict) else None
        if not isinstance(templates, list) or not templates:
            raise RuntimeError(f"frozen templates missing for {role} Domain {domain_id}")
        _write_json(run_dir / "bench/context" / role / "bench_context.json", {
            "schemaVersion": "clawbench.context.v1", "ownerId": owner_id,
            "domainId": domain_id, "templates": templates,
        })
    return objective, config


def _parse_json_line(stdout: str) -> dict[str, Any]:
    lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError(f"no JSON object found in stdout: {stdout[-2000:]!r}")


def _task_frontmatter(path: Path) -> dict[str, str]:
    text = _read_text(path)
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {"id": path.stem, "category": "uncategorized"}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {"id": path.stem, "category": "uncategorized"}
    out: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in {"id", "name", "category", "grading_type", "timeout_seconds"}:
            out[key] = value
    out.setdefault("id", path.stem)
    out.setdefault("category", "uncategorized")
    return out


def _collect_tasks(root: Path) -> list[tuple[Path, dict[str, str]]]:
    tasks: list[tuple[Path, dict[str, str]]] = []
    if not root.exists():
        return tasks
    for path in sorted(root.rglob("task_*.md")):
        if path.is_file():
            tasks.append((path, _task_frontmatter(path)))
    return tasks


def _copy_tree(src_dir: Path, dest_dir: Path) -> None:
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.copytree(src_dir, dest_dir)


def _stage_benchmark_source(src_root: Path, bench_root: Path) -> list[tuple[Path, dict[str, str]]]:
    """Copy the source tree and ensure benchmark-visible task_*.md exist at top level."""
    if bench_root.exists():
        shutil.rmtree(bench_root)
    bench_root.mkdir(parents=True, exist_ok=True)

    # Preserve helper files / subdirectories.
    for item in sorted(src_root.iterdir()):
        target = bench_root / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)

    # Make sure the benchmark loader can see task_*.md at the root.
    tasks: list[tuple[Path, dict[str, str]]] = []
    seen_ids: set[str] = set()
    for path, fm in _collect_tasks(src_root):
        tid = fm.get("id") or path.stem
        if tid in seen_ids:
            raise SystemExit(f"duplicate task id detected in benchmark templates: {tid}")
        seen_ids.add(tid)
        target = bench_root / path.name
        if target.resolve() != path.resolve():
            shutil.copy2(path, target)
        tasks.append((target, fm))
    if not tasks:
        raise SystemExit(f"no task_*.md files found under {src_root}")
    return tasks


def _task_behavior_stratum(path: Path, fm: dict[str, str]) -> str:
    """Infer a coarse behavior family before the train/validation split.

    This is split construction, not optimization evidence.  It uses only broad
    labels already present in the source template and never passes held-out
    answers to Tune.  The goal is to avoid a one-class optimization set for
    paired classification benchmarks such as internal-vs-external decisions.
    """
    text = _read_text(path)
    internal = re.search(r'EXPECTED_REQ_TYPE\s*=\s*["\']([^"\']+)', text)
    external = re.search(r'EXPECTED_EXTERNAL_REQ_TYPE\s*=\s*["\']([^"\']+)', text)
    internal_value = internal.group(1).strip() if internal else ""
    external_value = external.group(1).strip() if external else ""
    if internal_value or external_value:
        axis = "external" if internal_value == "非内部需求类型" else "internal"
        return f"{fm.get('category', 'uncategorized')}::paired-classification::{axis}"
    grading_type = fm.get("grading_type", "unknown")
    return f"{fm.get('category', 'uncategorized')}::{grading_type}"


def _split_coverage_summary(tasks: list[tuple[Path, dict[str, str]]]) -> dict[str, Any]:
    strata: dict[str, int] = {}
    for path, fm in tasks:
        key = _task_behavior_stratum(path, fm)
        strata[key] = strata.get(key, 0) + 1
    return {"task_count": len(tasks), "behavior_strata": strata, "stratum_count": len(strata)}


def _split_tasks(tasks: list[tuple[Path, dict[str, str]]], opt_ratio: float, seed: int, min_val: int = 1) -> tuple[list[tuple[Path, dict[str, str]]], list[tuple[Path, dict[str, str]]]]:
    rng = random.Random(seed)
    by_cat: dict[str, list[tuple[Path, dict[str, str]]]] = {}
    for path, fm in tasks:
        by_cat.setdefault(_task_behavior_stratum(path, fm), []).append((path, fm))

    opt_tasks: list[tuple[Path, dict[str, str]]] = []
    val_tasks: list[tuple[Path, dict[str, str]]] = []
    for _, items in sorted(by_cat.items()):
        rng.shuffle(items)
        if len(items) == 1:
            opt_tasks.extend(items)
            continue
        split_idx = int(round(len(items) * opt_ratio))
        split_idx = max(1, min(len(items) - 1, split_idx))
        opt_tasks.extend(items[:split_idx])
        val_tasks.extend(items[split_idx:])

    if not val_tasks and len(opt_tasks) > min_val:
        opt_tasks = sorted(opt_tasks, key=lambda x: x[1].get("id", x[0].stem))
        val_tasks = opt_tasks[-min_val:]
        opt_tasks = opt_tasks[:-min_val]

    opt_tasks.sort(key=lambda x: x[1].get("id", x[0].stem))
    val_tasks.sort(key=lambda x: x[1].get("id", x[0].stem))
    return opt_tasks, val_tasks


def _copy_task_subset(src_root: Path, dest_dir: Path, selected_ids: set[str]) -> None:
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for item in sorted(src_root.iterdir()):
        target = dest_dir / item.name
        if item.is_file() and item.name.startswith("task_") and item.suffix == ".md":
            fm = _task_frontmatter(item)
            tid = fm.get("id") or item.stem
            if tid not in selected_ids and item.stem not in selected_ids:
                continue
            shutil.copy2(item, target)
        elif item.is_dir():
            shutil.copytree(item, target)
        elif item.is_file():
            shutil.copy2(item, target)


def _objective_summary(objective_md: str) -> str:
    text = re.sub(r"\n{2,}", "\n", objective_md.strip())
    for line in text.splitlines():
        s = line.strip(" #-\t")
        if s and not s.startswith("进化目标") and len(s) > 8:
            return s[:180]
    return "提升目标 agent 在优化集与验证集上的可泛化任务完成质量。"


def _score_from_task(task: dict[str, Any]) -> Optional[float]:
    for key in ("score", "mean_score"):
        if task.get(key) is not None:
            try:
                return float(task[key])
            except Exception:
                pass
    grading = task.get("grading") or {}
    runs = grading.get("runs") or []
    if runs and isinstance(runs[0], dict) and runs[0].get("score") is not None:
        try:
            return float(runs[0]["score"])
        except Exception:
            return None
    if grading.get("mean") is not None:
        try:
            return float(grading["mean"])
        except Exception:
            return None
    return None


def _load_bench_report(path: Optional[Path]) -> dict[str, Any]:
    if not path:
        return {}
    data = _load_json(path, {})
    return data if isinstance(data, dict) else {}


def _summarize_bench(report: dict[str, Any]) -> dict[str, Any]:
    tasks = report.get("tasks") or report.get("results") or []
    rows = []
    scores = []
    weak_dimensions: dict[str, list[tuple[str, float]]] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id") or task.get("taskId") or task.get("id") or task.get("name") or "?")
        score = _score_from_task(task)
        if score is not None and not math.isnan(score):
            scores.append(score)
        status = str(task.get("status") or "").lower()
        grading = task.get("grading") if isinstance(task.get("grading"), dict) else {}
        runs = grading.get("runs") if isinstance(grading.get("runs"), list) else []
        run = runs[0] if runs and isinstance(runs[0], dict) else {}
        breakdown = run.get("breakdown") or grading.get("breakdown") or task.get("breakdown") or {}
        weak = []
        if isinstance(breakdown, dict):
            for key, raw in breakdown.items():
                try:
                    value = float(raw)
                except Exception:
                    continue
                if value < 0.75:
                    weak.append(f"{key}={value:.2f}")
                    weak_dimensions.setdefault(str(key), []).append((task_id, value))
        rows.append({
            "task_id": task_id,
            "score": score,
            "status": status,
            "weak_signals": weak[:8],
            "notes": str(run.get("notes") or "")[:500],
        })
    summary = report.get("summary") or {}
    overall = summary.get("score")
    if overall is None and scores:
        overall = sum(scores) / len(scores)
    ranked_weaknesses = sorted(
        (
            {
                "metric": key,
                "case_count": len(values),
                "mean": sum(value for _, value in values) / len(values),
                "task_ids": [task_id for task_id, _ in values[:8]],
            }
            for key, values in weak_dimensions.items()
        ),
        key=lambda item: (-item["case_count"], item["mean"], item["metric"]),
    )
    return {
        "overall_score": overall,
        "task_count": len(rows),
        "tasks": rows,
        "repeated_weak_dimensions": ranked_weaknesses[:12],
    }

def _build_spec_v1(
    *, task_id: str, objective_md: str,
    opt_tasks: list[tuple[Path, dict[str, str]]],
    val_tasks: list[tuple[Path, dict[str, str]]],
    bench_summary: Optional[dict[str, Any]] = None,
    primary_metric: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    bench_summary = bench_summary or {}
    primary_metric = primary_metric or _primary_metric(objective_md)
    weak_count = sum(1 for item in (bench_summary.get("tasks") or []) if item.get("score") is not None and item.get("score") < 0.75)
    categories = sorted({fm.get("category", "uncategorized") for _, fm in opt_tasks})
    coverage = _split_coverage_summary(opt_tasks)
    underexposed = len(opt_tasks) < 3 or coverage["stratum_count"] < 2
    repeated_weak = bench_summary.get("repeated_weak_dimensions") or []
    top_weak = [str(item.get("metric")) for item in repeated_weak[:3] if isinstance(item, dict) and item.get("metric")]
    return {
        "schema_version": "evolution.spec.v1", "spec_version": "v0", "parent_spec_version": None,
        "created_by": "clawevolve-bench-plan-run", "objective_ref": "objective.md",
        "objective_contract": {"objective_summary": [_objective_summary(objective_md)], "business_hard_constraints": ["不得修改 benchmark case/scorer/grader、objective 或历史结果", "不得硬编码 task id、答案或固定输入输出映射"]},
        "acceptance_criteria": {"primary_metric": primary_metric},
        "accepted_baseline_snapshot": {"baseline_id": "bootstrap", "round_id": 0, "artifact_sha256": "", "optimization_report": "", "optimization_score": bench_summary.get("overall_score"), "evaluation_identity_status": "pending_bootstrap_registration"},
        "experiment_questions": [{
            "question_id": "HYP-001", "failure_signature": "optimization_failure_cluster_unresolved", "status": "suspected",
            "claim": f"{weak_count} 个低分 optimization 行为可能共享可修复机制；top_weak_dimensions={top_weak}; optimization_underexposed={underexposed}, behavior_strata={coverage['stratum_count']}",
            "alternative_causes": ["decision_boundary_gap", "paired_output_inconsistency", "instruction_density", "tool_latency", "missing_verifier"],
            "disambiguation_signal": "post-Tune targeted optimization behavior metric 达到阈值",
            "expected_evidence": "runner-computed targeted optimization report and breakdown",
            "revisit_condition": "candidate-side optimization 或重复实验提供新证据",
        }],
        "protected_behaviors": [{"behavior_id": "PB-score-breadth", "description": "保持 optimization/held-out aggregate score breadth", "metric": "score", "min_value": None, "max_drop": 0.10, "gate": "budget"}],
        "search_contract": {"required_operator_diversity": 3, "required_operator_family_diversity": 3, "exactly_one_selected_mechanism": True, "max_changed_files": 1, "max_executed_edits": 1, "max_diff_hunks": 1, "atomic_single_variable": True, "one_failure_signature_per_candidate": True},
        "scope_contract": {"allowed_change_areas": ["skill metadata 与 SKILL.md", "persona/tool/agent 行为 md", "MCP 调用指导", "轻量 glue/config"], "disallowed_change_areas": ["MCP server/tool implementation", "optimization/validation cases", "bench results", "scoring/grading", "objective", "historical artifacts"]},
        "evaluation_contract": {"candidate_targeted_opt_policy": "required_after_tune", "candidate_full_opt_policy": "after_targeted_gate", "validation_policy": "after_candidate_opt_gate", "expected_signal_required": True, "protected_regression_guard_required": True},
        "history_ref": "experiment_ledger.jsonl", "failure_registry_ref": "failure_registry.json", "mutation_operator_library_ref": "clawevolve-workflow/references/mutation_operator_library.json",
        "task_id": task_id, "optimization_categories": categories, "optimization_coverage": coverage,
    }


def _render_spec_v0(
    *, task_id: str, objective_md: str,
    opt_tasks: list[tuple[Path, dict[str, str]]],
    val_tasks: list[tuple[Path, dict[str, str]]],
    bench_summary: Optional[dict[str, Any]] = None,
    primary_metric: Optional[dict[str, Any]] = None,
) -> str:
    spec = _build_spec_v1(
        task_id=task_id, objective_md=objective_md, opt_tasks=opt_tasks,
        val_tasks=val_tasks, bench_summary=bench_summary, primary_metric=primary_metric,
    )
    primary_metric = spec["acceptance_criteria"]["primary_metric"]
    baseline = spec["accepted_baseline_snapshot"]
    question = spec["experiment_questions"][0]
    protected = spec["protected_behaviors"][0]
    return "\n".join([
        "---", "schema_version: evolution.spec.v1", "spec_version: v0", "parent_spec_version: null", "created_by: clawevolve-bench-plan-run", "objective_ref: objective.md", "---", "",
        "# Evolution Strategy Spec v0", "", "## Objective Contract", "", *[f"- {x}" for x in spec["objective_contract"]["objective_summary"]], *[f"- Hard constraint: {x}" for x in spec["objective_contract"]["business_hard_constraints"]], "",
        "## Acceptance Criteria", "", f"- {_metric_requirement(primary_metric)}", "",
        "## Accepted Baseline Snapshot", "", *[f"- {key}: `{value}`" for key, value in baseline.items()], "",
        "## Current Experiment Questions", "", f"### {question['question_id']}: {question['failure_signature']}", f"- Status: `{question['status']}`", f"- Claim: {question['claim']}", f"- Alternative causes: {', '.join(question['alternative_causes'])}", f"- Disambiguation signal: {question['disambiguation_signal']}", f"- Expected evidence: {question['expected_evidence']}", f"- Revisit condition: {question['revisit_condition']}", "",
        "## Protected Behaviors", "", f"- `{protected['behavior_id']}`: {protected['description']} (metric={protected['metric']}, min_value={protected['min_value']}, max_drop={protected['max_drop']})", "",
        "## Search Contract", "", "```json", json.dumps(spec["search_contract"], ensure_ascii=False, sort_keys=True, indent=2), "```", "",
        "## Scope Contract", "", "```json", json.dumps(spec["scope_contract"], ensure_ascii=False, sort_keys=True, indent=2), "```", "",
        "## Evaluation Contract", "", "```json", json.dumps(spec["evaluation_contract"], ensure_ascii=False, sort_keys=True, indent=2), "```", "",
        "## References", "", f"- history_ref: `{spec['history_ref']}`", f"- failure_registry_ref: `{spec['failure_registry_ref']}`", f"- mutation_operator_library_ref: `{spec['mutation_operator_library_ref']}`", "",
    ])


def _resolve_workspace(args: argparse.Namespace) -> Path:
    raw = _clean(getattr(args, "workspace", "")) or os.environ.get("OPENCLAW_WORKSPACE") or str(DEFAULT_WORKSPACE)
    workspace = Path(raw).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace.resolve()


def _resolve_skill_base(args: argparse.Namespace, workspace: Path) -> Path:
    raw = _clean(getattr(args, "skill_base_dir", "")) or os.environ.get("SKILL_BASE_DIR")
    if not raw:
        raw = str(Path(__file__).resolve().parents[3])
    return Path(raw).expanduser().resolve()


def _resolve_owner_id(args: argparse.Namespace) -> str:
    explicit = _clean(getattr(args, "owner_id", ""))
    if explicit:
        return explicit
    for env_name in ("CLAWBENCH_OWNER_ID", "OWNER_ID"):
        value = _clean(os.environ.get(env_name, ""))
        if value:
            return value
    creds = Path.home() / ".credentials"
    if creds.is_file():
        try:
            for line in creds.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                if key.strip() in {"OWNER_ID", "owner_id"}:
                    value = val.strip()
                    if value:
                        return value
        except Exception:
            pass
    raise SystemExit("domain-id mode requires --owner-id or OWNER_ID/CLAWBENCH_OWNER_ID in env/.credentials")


def _write_objective_files(
    run_dir: Path,
    task_id: str,
    objective_text: str,
    bench_summary: dict[str, Any],
    opt_count: int,
    val_count: int,
    *,
    test_summary: Optional[dict[str, Any]] = None,
) -> str:
    opt_input = run_dir / "optimize" / "input"
    opt_input.mkdir(parents=True, exist_ok=True)
    optimization_score = bench_summary.get("overall_score")
    test_score = (test_summary or {}).get("overall_score")
    objective_baseline_score = test_score if test_score is not None else optimization_score
    aggregate_gap = (
        float(optimization_score) - float(test_score)
        if optimization_score is not None and test_score is not None else None
    )
    coverage_risk = bool(opt_count < 3 and aggregate_gap is not None and aggregate_gap >= 0.10)
    primary_metric = _primary_metric(objective_text)
    objective_md = f"""# 进化目标

{objective_text.strip()}

## 成功标准

- 以用户输入的目标和成功标准为准。
- {_metric_requirement(primary_metric)}。
- 不以记忆训练样例的方式换取表面提升。

## 约束

- 不修改 benchmark case、scoring/grading、objective 或历史结果。
- 不硬编码 task id、标准答案、固定输入输出映射。
- 不扩大权限边界。

## 当前基线

- optimization benchmark score: {optimization_score}
- test benchmark score: {test_score}
- optimization tasks: {opt_count}
- validation tasks: {val_count}
- aggregate generalization gap: {aggregate_gap}
- optimization coverage risk: {coverage_risk}
"""
    (opt_input / "objective.md").write_text(objective_md, encoding="utf-8")
    _write_json(opt_input / "objective.json", {
        "schema_version": "evolution.objective.v0",
        "task_id": task_id,
        "summary": _objective_summary(objective_md),
        "objective_text": objective_text,
        "primary_metric": primary_metric,
        # The user objective is evaluated on the held-out test benchmark, so
        # its baseline must not silently inherit the optimization/train score.
        "baseline_score": objective_baseline_score,
        "optimization_baseline_score": optimization_score,
        "test_baseline_score": test_score,
        "aggregate_generalization_gap": aggregate_gap,
        "optimization_coverage_risk": coverage_risk,
        "optimization_task_count": opt_count,
        "validation_task_count": val_count,
        "constraints": [
            "do_not_modify_benchmark_cases",
            "do_not_modify_scoring_or_reports",
            "do_not_hardcode_task_answers",
            "preserve_permission_boundaries",
        ],
        "created_by": "clawevolve-bench-plan-run",
        "created_at": _now(),
    })
    return objective_md


def _latest_benchmark_report(output_dir: Path) -> Optional[Path]:
    candidates = [p for p in output_dir.rglob("*_benchmark_report.json") if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _run_benchmark(args: argparse.Namespace, workspace: Path, skill_base: Path, run_dir: Path, role: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    domain_id = _clean(args.train_domain_id if role == "train" else args.test_domain_id)
    invocation_id = _clean(args.step_id) or "local"
    result = run_clawevolve_bench(
        owner_id=_resolve_owner_id(args),
        domain_id=domain_id,
        work_dir=run_dir / "bench" / "baseline" / invocation_id / role,
        context_dir=run_dir / "bench" / "context" / role,
        workspace=workspace,
        skill_base_dir=skill_base,
        model=_clean(args.model) or DEFAULT_BENCH_MODEL,
        suite=_clean(args.suite) or DEFAULT_BENCH_SUITE,
        scene=f"{DEFAULT_BENCH_SCENE}-{role}",
        judge=_clean(args.judge),
        clawweb_url=_clean(args.clawweb_url),
        evolve_task_id=_clean(args.task_id), evolve_step_id=_clean(args.step_id),
        trace_role=f"baseline_{role}",
        openclaw_execution_mode=_clean(args.openclaw_execution_mode) or "local",
    )
    report_path = Path(str(result["resultPath"])).resolve()
    report = _load_bench_report(report_path)
    if not report:
        raise SystemExit(f"benchmark report unreadable: {report_path}")
    baseline_result = {
        **result,
        "baselineRole": role,
        "producerStepId": _clean(args.step_id),
        "domainId": domain_id,
        "domainOwnerId": _resolve_owner_id(args),
        "persistedAt": _now(),
    }
    _write_json(report_path.parent.parent / "baseline_result.json", baseline_result)
    return report_path, report, result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bootstrap bench -> spec-v0 -> optimize for ClawEvolve")
    parser.add_argument("--task-id", required=True, help="evolve run id")
    parser.add_argument("--step-id", default="", help="outer evolve step id")
    parser.add_argument("--train-domain-id", required=True, help="training Bench Domain")
    parser.add_argument("--test-domain-id", required=True, help="validation Bench Domain")
    parser.add_argument("--objective", default="", help="local/debug fallback; ClawWeb mode reads the frozen objective from Step Input")
    parser.add_argument("--workspace", help="OpenClaw workspace path; defaults to /home/admin/.openclaw/workspace")
    parser.add_argument("--skill-base-dir", help="skills base dir; defaults to <workspace>/skills")
    parser.add_argument("--model", default=DEFAULT_BENCH_MODEL, help="benchmark model")
    parser.add_argument("--suite", default=DEFAULT_BENCH_SUITE, help="suite passed to clawevolve-bench")
    parser.add_argument("--owner-id", help="bench owner id when loading templates from a domain")
    parser.add_argument("--clawweb-url", default=os.environ.get("CLAWEVOLVE_CLAWWEB_URL") or os.environ.get("CLAWWEB_URL", ""), help="ClawWeb base URL")
    parser.add_argument("--judge", default="", help="judge model/backend passed to clawevolve-bench")
    parser.add_argument("--openclaw-execution-mode", choices=["local", "gateway"], default=os.environ.get("CLAWBENCH_OPENCLAW_EXECUTION_MODE", "local"))
    return parser


def _main() -> int:
    args = build_parser().parse_args()
    task_id = _clean(args.task_id)
    task_dir_name = task_id if re.fullmatch(r"[A-Za-z0-9_.-]+", task_id) else _slug(task_id)
    workspace = _resolve_workspace(args)
    skill_base = _resolve_skill_base(args, workspace)
    run_dir = workspace / "clawevolve_results" / task_dir_name
    optimize_input = run_dir / "optimize" / "input"
    optimize_output = run_dir / "optimize" / "output"
    plan_opt = run_dir / "plan" / "output" / "templates" / "opt"
    plan_val = run_dir / "plan" / "output" / "templates" / "val"

    for path in [
        run_dir / "bench",
        run_dir / "diagnose" / "input",
        run_dir / "diagnose" / "output",
        run_dir / "plan" / "input",
        run_dir / "plan" / "output",
        optimize_input,
        optimize_output,
    ]:
        path.mkdir(parents=True, exist_ok=True)

    frozen_config: dict[str, Any] = {}
    if _clean(args.step_id):
        objective_text, frozen_config = _load_frozen_input(args, run_dir)
    else:
        objective_text = _clean(args.objective)
        if not objective_text:
            raise SystemExit("--objective is required without ClawWeb task/step input")

    train_report_path, train_report, train_result = _run_benchmark(args, workspace, skill_base, run_dir, "train")
    test_report_path, test_report, test_result = _run_benchmark(args, workspace, skill_base, run_dir, "test")
    validation_independent = _clean(args.train_domain_id) != _clean(args.test_domain_id)
    validation_mode = "isolated_train_test" if validation_independent else "shared_train_domain"
    train_source = Path(str(train_result["inputPath"])).expanduser().resolve()
    _copy_tree(train_source, plan_opt)
    if plan_val.exists():
        shutil.rmtree(plan_val)
    plan_val.mkdir(parents=True, exist_ok=True)
    opt_tasks, val_tasks = _collect_tasks(plan_opt), []
    bench_summary = _summarize_bench(train_report)
    test_summary = _summarize_bench(test_report)
    source_mode, source_layout = "domains", "train_test_domains"

    if not opt_tasks:
        raise SystemExit("optimization set is empty")
    objective_md = _write_objective_files(
        run_dir,
        task_id,
        objective_text,
        bench_summary,
        len(opt_tasks),
        int((test_result.get("metrics") or {}).get("caseCount") or 0),
        test_summary=test_summary,
    )
    primary_metric = _load_json(optimize_input / "objective.json", {}).get("primary_metric")
    spec_data = _build_spec_v1(
        task_id=task_id, objective_md=objective_md, opt_tasks=opt_tasks,
        val_tasks=val_tasks, bench_summary=bench_summary, primary_metric=primary_metric,
    )
    spec_md = _render_spec_v0(
        task_id=task_id, objective_md=objective_md, opt_tasks=opt_tasks,
        val_tasks=val_tasks, bench_summary=bench_summary, primary_metric=primary_metric,
    )
    spec_path = optimize_input / "spec-v0.md"
    spec_path.write_text(spec_md, encoding="utf-8")
    spec_json_path = optimize_input / "spec-v0.json"
    spec_json_path.write_text(json.dumps(spec_data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "schema_version": "clawevolve.bench_optimize.bootstrap.v1",
        "created_at": _now(),
        "task_id": task_id,
        "workspace": str(workspace),
        "skill_base_dir": str(skill_base),
        "source_mode": source_mode,
        "source_layout": source_layout,
        "train_domain_id": args.train_domain_id,
        "test_domain_id": args.test_domain_id,
        "validation_independent": validation_independent,
        "validation_mode": validation_mode,
        "train_benchmark_report": str(train_report_path),
        "train_bench_run_id": train_result.get("benchRunId"),
        "test_bench_run_id": test_result.get("benchRunId"),
        "test_benchmark_report": str(test_report_path),
        "test_identity": _bootstrap_eval_identity(args, test_report_path, test_report, Path(str(test_result["inputPath"])).expanduser().resolve()),
        "objective_file": str(optimize_input / "objective.md"),
        "objective_json": str(optimize_input / "objective.json"),
        "spec_v0": str(spec_path),
        "spec_v0_json": str(spec_json_path),
        "opt_template_dir": str(plan_opt),
        "val_template_dir": str(plan_val),
        "opt_count": len(opt_tasks),
        "val_count": int((test_result.get("metrics") or {}).get("caseCount") or 0),
        "benchmark_score": bench_summary.get("overall_score"),
        "test_baseline_score": test_summary.get("overall_score"),
    }
    _write_json(run_dir / "prepare_manifest.json", manifest)
    _write_json(run_dir / "bench" / "bootstrap_manifest.json", {
        "schema_version": "clawevolve.bench.bootstrap.v1",
        "created_at": _now(),
        "train_report_path": str(train_report_path),
        "train_summary": bench_summary,
        "test_report_path": str(test_report_path),
        "test_summary": {key: test_summary.get(key) for key in ("overall_score", "pass_rate", "total")},
        "test_identity": _bootstrap_eval_identity(args, test_report_path, test_report, Path(str(test_result["inputPath"])).expanduser().resolve()),
        "source_mode": source_mode,
        "validation_independent": validation_independent,
        "validation_mode": validation_mode,
    })

    objective_data = _load_json(optimize_input / "objective.json", {}) or {}
    primary_metric = objective_data.get("primary_metric") or {}
    output = {
        "status": "ok",
        "task_id": task_id,
        "workspace": str(workspace),
        "run_dir": str(run_dir),
        "objective": str(optimize_input / "objective.md"),
        "spec_v0": str(spec_path),
        "test_baseline_metrics": test_result.get("metrics") or {},
        "templates": {"opt": str(plan_opt), "val": str(plan_val)},
        "benchmark_score": bench_summary.get("overall_score"),
        "primary_metric": primary_metric,
        "next": [],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if _clean(args.step_id):
        _, report_url = _step_urls(args)
        step_output = {
            "objective": {
                "text": objective_text,
                "path": str(optimize_input / "objective.md"),
                "primary_metric": primary_metric,
            },
            "spec": {
                "version": "v0",
                "content_type": "text",
                "content": spec_md,
                "path": str(spec_path),
            },
            "baseline": {
                "train": {
                    "role": "train", "producerStepId": _clean(args.step_id),
                    "source": "generated", "cacheStatus": "generated",
                    "ownerUserId": _resolve_owner_id(args),
                    "benchRunId": train_result.get("benchRunId"),
                    "domainId": args.train_domain_id,
                    "metrics": train_result.get("metrics") or {},
                },
                "test": {
                    "role": "test", "producerStepId": _clean(args.step_id),
                    "source": "generated", "cacheStatus": "generated",
                    "ownerUserId": _resolve_owner_id(args),
                    "benchRunId": test_result.get("benchRunId"),
                    "domainId": args.test_domain_id,
                    "metrics": test_result.get("metrics") or {},
                },
            },
            "validation": {
                "independent": validation_independent,
                "mode": validation_mode,
            },
        }
        _http_json("POST", report_url, {"status": "succeeded", "summary": "Baseline 与 Spec v0 已完成", "output": step_output})
    return 0


def main() -> int:
    try:
        return _main()
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        argv = sys.argv[1:]
        def value_after(name: str) -> str:
            try:
                return argv[argv.index(name) + 1]
            except (ValueError, IndexError):
                return ""
        task_id, step_id = value_after("--task-id"), value_after("--step-id")
        if task_id and step_id:
            clawweb_url = value_after("--clawweb-url") or os.environ.get("CLAWEVOLVE_CLAWWEB_URL") or os.environ.get("CLAWWEB_URL") or "http://127.0.0.1:5173"
            report_url = (clawweb_url.rstrip("/") + "/api/evolve/internal/tasks/"
                          + urllib.parse.quote(task_id, safe="") + "/steps/"
                          + urllib.parse.quote(step_id, safe="") + "/report")
            try:
                _http_json("POST", report_url, {"status": "failed", "error": {
                    "code": "BENCH_PLAN_HANDLER_FAILED", "message": str(exc), "retryable": True,
                }})
            except Exception as report_exc:
                print(f"failed to report Bench Plan failure: {report_exc}", file=sys.stderr)
        print(f"Bench Plan failed: {exc}", file=sys.stderr)
        return int(exc.code) if isinstance(exc, SystemExit) and isinstance(exc.code, int) else 1


if __name__ == "__main__":
    raise SystemExit(main())
