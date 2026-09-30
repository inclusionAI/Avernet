"""Versioned upstream fixtures for standalone Stage tests, never normal tasks."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

FIXTURE_ID = "optimize-v1"
FIXTURE_PATH = Path(__file__).with_name("fixtures") / f"{FIXTURE_ID}.json"


def _plan_imports() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "clawevolve-plan"))


def render_optimize_fixture(root: Path, *, bot_id: str, target: str) -> dict[str, Any]:
    """Use Plan's own renderers and contracts with explicit constructed data."""
    _plan_imports()
    from clawevolve_plan.spec.builder import build_spec, build_objective_document
    from clawevolve_plan.spec.renderer import render_goal_markdown, render_markdown
    from clawevolve_plan.spec.contract import validate_objective_markdown, validate_spec_markdown
    from clawevolve_plan.bench.template_builder import render_templates
    from clawevolve_plan.bench.case_contract import _fallback_contract
    from clawevolve_plan.io import write_named_outputs

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    plan = copy.deepcopy(fixture["context"])
    plan.update(bot_id=bot_id, default_optimization_goal={"goal_text": fixture["goal"],
        "target_task_success_rate": 1.0, "max_iterations": 1})
    spec = build_spec(plan, fixture["goal"], fixture["strategy"], [target])
    spec["created_by"] = "stage-test-fixture"
    objective = build_objective_document(spec)
    objective_md, spec_md = render_goal_markdown(objective), render_markdown(spec)
    validate_objective_markdown(objective_md, objective)
    validate_spec_markdown(spec_md, spec)
    root.mkdir(parents=True, exist_ok=True)
    write_named_outputs(root, "objective", objective, objective_md)
    write_named_outputs(root, "spec-v0", spec, spec_md)
    contracts = {case["case_id"]: _fallback_contract(case, fixture["goal"], {}) for case in plan["cases"]}
    _, _, _, manifest = render_templates(plan, root / "bench", fixture["goal"], contracts=contracts)
    return {"fixture": fixture, "manifest": manifest, "spec": spec_md}


def _publish(client: Any, owner: str, domain: str, package: dict, items: list[dict]) -> dict:
    from clawevolve_plan.integration.clawweb import ClawWebHTTPError, _template_hashes
    # Bench sourceHash uses the same 16-character digest as BenchSplitPackage.
    hashes = {item["id"]: item["content_sha256"][:16] for item in items}
    try:
        client.create_domain(domain, f"Stage integration test fixture {FIXTURE_ID}")
    except ClawWebHTTPError as error:
        if error.status_code != 409:
            raise
        client.get_domain(owner, domain)
    # Stable Task-owned domains allow retries; verify actual publication, not just HTTP success.
    client.upload_zip(owner, domain, Path(package["zip_path"]))
    published = client.batch_publish(owner, domain, list(hashes))
    if int((published.get("body") or {}).get("failed") or 0):
        raise ValueError("Stage test Bench template publication failed")
    listed = client.list_published_templates(owner, domain)
    if _template_hashes(listed) != hashes:
        raise ValueError("Stage test Bench templates do not match constructed fixtures")
    return hashes


def prepare_optimize_test(args: Any, payload: dict[str, Any]) -> None:
    """Recognize platform task metadata, prepare only this Task's isolated inputs."""
    from . import runtime
    task = payload.get("task") or {}
    stage = payload.get("stage") or {}
    step = payload.get("step") or {}
    if task.get("taskType") != "stage_test" or (stage.get("key") or step.get("stepType")) != "optimize":
        return
    native = payload
    if payload.get("protocolVersion") == "clawevolve.stage-runtime/v1":
        native = json.loads(runtime._http("GET", runtime._step_url(args, "input") + "?view=native"))
    task, step = native.get("task") or {}, native.get("step") or {}
    config = task.get("config") or {}
    testing = config.get("stageTest") or {}
    if testing.get("inputFixture") is None:
        return  # Previously created tests keep their recorded preparation behavior.
    if (testing.get("inputFixture") != FIXTURE_ID or task.get("taskType") != "stage_test"
            or task.get("taskId") != args.task_id or step.get("stepId") != args.step_id
            or step.get("stepType") != "optimize" or testing.get("stage") != "optimize"
            or testing.get("flow") != "skill_evolution"):
        raise ValueError("Optimize test fixture does not match the frozen Task/Step")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.task_id):
        raise ValueError("Unsafe Stage test Task ID")
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    target = config.get("targetSkill") or {}
    prepared = (target.get("candidate") or {}).get("prepared") or {}
    logical_workspace = f"/home/admin/.openclaw/clawevolve_workspaces/{args.task_id}/workspace"
    logical_skill = f"{logical_workspace}/skills/skills-local/{fixture['skill_name']}"
    if (target.get("name") != fixture["skill_name"] or prepared.get("workspacePath") != logical_workspace
            or prepared.get("skillPath") != logical_skill
            or prepared.get("baselineSha256") != (target.get("baseline") or {}).get("sha256")):
        raise ValueError("Optimize test requires its prepared fixture Skill candidate")
    workspace = runtime._runtime_path(logical_workspace)
    runtime._fixture_safe_path(workspace)
    if not (runtime._runtime_path(logical_skill) / "SKILL.md").is_file():
        raise ValueError("Prepared Optimize test Skill is missing")
    source = (native.get("inputs") or {}).get("diagnoses") or []
    if len(source) != 1 or source[0].get("taskId") != args.task_id or not source[0].get("plan"):
        raise ValueError("Optimize test requires its own labelled Plan input record")
    plan_step = source[0]["plan"]["stepId"]
    owner = (native.get("target") or {}).get("userId")
    if not owner:
        raise ValueError("Optimize test owner is missing")
    domains = {role: f"stage_test_{args.task_id}_{role}" for role in ("train", "test")}
    if (config.get("trainBenchDomainId") != domains["train"]
            or config.get("testBenchDomainId") != domains["test"]):
        raise ValueError("Optimize test Bench domains must belong to this Task")
    runtime._ensure_task_results_link(workspace)
    root = runtime.SOURCE_WORKSPACE / "clawevolve_results" / args.task_id / "optimize" / "input"
    runtime._fixture_safe_path(root)
    marker = root / "stage-test-fixture.json"
    identity = {"fixture": FIXTURE_ID, "sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
        "taskId": args.task_id, "owner": owner, "baselineSha256": prepared["baselineSha256"]}
    if marker.exists():
        receipt = runtime._read_json(marker, strict=True)
        if receipt.get("identity") != identity:
            raise ValueError("Frozen Optimize fixture identity changed")
        for name, digest in receipt["files"].items():
            path = root / name
            runtime._fixture_safe_path(path)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("Frozen Optimize test inputs changed")
        result = receipt["result"]
    else:
        _plan_imports()
        from clawevolve_plan.integration.clawweb import ClawWebClient
        rendered = render_optimize_fixture(root, bot_id=native["target"]["botId"],
            target=f"skills/skills-local/{fixture['skill_name']}/SKILL.md")
        client = ClawWebClient(user_id=owner, base_url=args.clawweb_url)
        manifest = rendered["manifest"]
        for role in ("train", "test"):
            _publish(client, owner, domains[role], manifest["split_packages"][role],
                [item for item in manifest["templates"] if item["split"] == role])
        result = {"goal": {"summary": fixture["goal"], "metrics": []},
            "spec": {"version": "v0", "content_type": "text", "content": rendered["spec"]},
            "benchDomains": {"trainBenchDomainId": domains["train"], "testBenchDomainId": domains["test"]},
            "benchCases": {"trainCount": 2, "testCount": 2, "items": [
                {"sourceCaseId": item["case_id"], "taskId": item["id"], "split": item["split"],
                 "template": {"ownerUserId": owner, "domainId": domains[item["split"]], "templateName": item["id"]}}
                for item in manifest["templates"]]}}
        files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("objective.md", "objective.json", "spec-v0.md", "spec-v0.json")}
        runtime._atomic_json(marker, {"identity": identity, "files": files, "result": result})
    if payload.get("protocolVersion") == "clawevolve.stage-runtime/v1":
        payload["input"]["plan_result"] = result
    # This is a labelled input fixture record, not evidence of an executed Plan.
    report_args = copy.copy(args)
    report_args.step_id = plan_step
    runtime._report(report_args, "succeeded", "固定测试输入已准备（Mock 上游 Plan，未运行 Plan）",
        output=result)
