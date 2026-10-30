"""Owned-local custom-collaboration parent case.

This joins the three already-migrated custom-collaboration slices into one
exact parent lifecycle, in the reviewed source order: validate, one-shot
trial, persistent Group create, then exact Group cleanup. It does not grant
remote or release credit.
"""
from __future__ import annotations

from pathlib import Path
import time

from .local_cli import (
    LocalCliError,
    OwnedBcsProcess,
    _request_trial_json,
    create_custom_collaboration_group,
    custom_collaboration_group_delete_path,
    run_custom_collaboration_trial,
    validate_collaboration_yaml,
)
from .local_operator_cli_contract import (
    CUSTOM_COLLABORATION_PARENT,
    CUSTOM_COLLABORATION_REQUIREMENTS,
    custom_collaboration_source_binding as _base_source_binding,
)
from .profile import identifier
from .run_state import write

CASE_ID = "LOCAL-CUSTOM-COLLABORATION-LIFECYCLE-01"
SUITE = "owned-local-custom-collaboration-lifecycle-v1"
# Keep the three lifecycle aggregates; add only source-backed field obligations.
DENSITY_SPECS = (
    ("yaml-valid", "validate_yaml", 161, 161),
    ("yaml-role-count", "validate_yaml", 162, 162),
    ("yaml-node-count", "validate_yaml", 163, 163),
    ("yaml-planner-required", "validate_yaml", 125, 127),
    ("yaml-reviewer-required", "validate_yaml", 128, 130),
    ("yaml-plan-assignee", "validate_yaml", 137, 142),
    ("yaml-review-assignee", "validate_yaml", 147, 152),
    ("yaml-acyclic", "validate_yaml", 135, 135),
    ("yaml-plan-review-edge", "validate_yaml", 144, 146),
    ("yaml-review-final", "validate_yaml", 147, 154),
    ("trial-group-id", "trial_create_group", 67, 67),
    ("trial-session-id", "trial_create_group", 170, 171),
    ("human-member", "trial_add_member", 180, 182),
    ("human-consultant", "trial_add_member", 180, 182),
    ("permission-allowed", "trial_permission", 194, 194),
    ("permission-session", "trial_permission", 195, 195),
    ("permission-group", "trial_permission", 196, 196),
    ("run-id", "trial_start_run", 206, 207),
    ("run-session", "trial_start_run", 208, 208),
    ("run-group", "trial_start_run", 209, 209),
    ("graph-run-id", "trial_query_graph", 218, 218),
    ("pending-human-empty", "trial_query_pending", 219, 221),
    ("respond-refused", "trial_respond_guard", 222, 225),
    ("respond-not-pending", "trial_respond_guard", 227, 227),
    ("panel-component", "trial_messages", 237, 238),
    ("panel-run", "trial_messages", 239, 239),
    ("cancel-terminal", "cancel_trial_run", 244, 248),
    ("session-running", "read_trial_session", 250, 252),
    ("persistent-id", "create_persistent_group", 265, 266),
    ("persistent-driver", "create_persistent_group", 267, 267),
    ("persistent-reviewer", "create_persistent_group", 268, 268),
    ("persistent-strategy", "read_persistent_group", 272, 274),
    ("trial-cleanup", "delete_trial_group", 254, 254),
    ("persistent-cleanup", "delete_persistent_group", 276, 276),
)
DENSITY_REQUIREMENTS = tuple("CUSTOM-COLLABORATION." + row[0] for row in DENSITY_SPECS)
REQUIREMENTS = CUSTOM_COLLABORATION_REQUIREMENTS + DENSITY_REQUIREMENTS


def validate_density_contract(rows: object, source: str) -> None:
    """Reject missing/duplicate IDs, inaccurate quotes and moved source lines."""
    lines = source.splitlines()
    if not isinstance(rows, list) or len(rows) != len(DENSITY_SPECS):
        raise LocalCliError("custom collaboration density contract is incomplete")
    for row, (name, method, first, last) in zip(rows, DENSITY_SPECS):
        expected = {"id": "CUSTOM-COLLABORATION." + name, "method": method,
                    "source_file": "cli-stories.sh", "line_start": first,
                    "line_end": last, "source_quote": "\n".join(lines[first-1:last])}
        if (not isinstance(row, dict) or any(row.get(k) != v for k, v in expected.items())
                or not isinstance(row.get("expectation"), str) or not row["expectation"]
                or not isinstance(row.get("command"), str) or not row["command"]):
            raise LocalCliError("custom collaboration density source citation differs")


def custom_collaboration_source_binding() -> dict:
    binding = _base_source_binding()
    return {**binding, "requirements": binding["requirements"] + binding["density_requirements"]}


def _object(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value: object) -> list:
    return value if isinstance(value, list) else []


def _panel_observations(value: object, run_id: str) -> tuple[bool, bool]:
    # Parse real AixUI attributes instead of matching unrelated message text.
    import json
    import re
    import xml.etree.ElementTree as ET
    component_found = run_found = False
    for message in _rows(value):
        content = _object(message).get("content", "")
        if not isinstance(content, str):
            continue
        for tag in re.findall(r"<AixUI\b[^>]*?/>", content, flags=re.S):
            try:
                node = ET.fromstring(tag)
                if (node.get("type") != "panel"
                        or node.get("component") != "bcsPanel.StateMachineRunView"):
                    continue
                component_found = True
                params = json.loads(node.get("params", "null"))
                run_found |= bool(run_id) and _object(params).get("runId") == run_id
            except (ET.ParseError, ValueError):
                continue
    return component_found, run_found


def project_density(method: str, value: object, *, group_id: str = "",
                    session_id: str = "", run_id: str = "", driver_id: str = "",
                    reviewer_id: str = "", human_id: str = "") -> dict[str, bool]:
    """Only allowlisted booleans leave the response boundary; no raw text/secrets.

    Absent fields fail closed. A group-derived Run is not a current-session
    Run, and no permission/Human membership is inferred from group creation.
    """
    data = _object(value)
    checks = {}
    if method == "validate_yaml":
        summary, graph = _object(data.get("summary")), _object(data.get("graph"))
        slots = {_object(r).get("binding"): _object(r) for r in _rows(data.get("participants"))}
        nodes = {_object(r).get("node_id"): _object(r) for r in _rows(graph.get("nodes"))}
        plan, review = nodes.get("plan", {}), nodes.get("review", {})
        checks = {
            "yaml-valid": data.get("valid") is True,
            "yaml-role-count": type(summary.get("participants")) is int and summary["participants"] == 2,
            "yaml-node-count": type(summary.get("nodes")) is int and summary["nodes"] == 2,
            "yaml-planner-required": slots.get("planner", {}).get("required") is True,
            "yaml-reviewer-required": slots.get("reviewer", {}).get("required") is True,
            "yaml-plan-assignee": plan.get("kind") == "bot_task" and plan.get("assignee") == {"type": "bot_binding", "binding": "planner"},
            "yaml-review-assignee": review.get("kind") == "bot_task" and review.get("assignee") == {"type": "bot_binding", "binding": "reviewer"},
            "yaml-acyclic": graph.get("graph_mode") == "acyclic",
            "yaml-plan-review-edge": any((_object(e).get("source"), _object(e).get("target"), _object(e).get("outcome")) == ("plan", "review", "complete") for e in _rows(graph.get("edges"))),
            "yaml-review-final": review.get("final_output") is True and summary.get("final_output_node") == "review",
        }
    elif method == "trial_create_group":
        checks = {"trial-group-id": isinstance(data.get("id"), str) and bool(data["id"]),
                  "trial-session-id": isinstance(data.get("session_id"), str) and bool(data["session_id"])}
    elif method == "trial_add_member":
        members = [r for r in _rows(data.get("participants")) if isinstance(r, dict)
                   and (r.get("bot_uuid") or r.get("actor_id")) == human_id and human_id]
        bound = bool(session_id) and (data.get("session_id") or data.get("id")) == session_id
        checks = {"human-member": bound and bool(members),
                  "human-consultant": bound and any(r.get("role") == "consultant" for r in members)}
    elif method == "trial_permission":
        checks = {"permission-allowed": data.get("allowed") is True,
                  "permission-session": bool(session_id) and data.get("session_id") == session_id,
                  "permission-group": bool(group_id) and data.get("group_id") == group_id}
    elif method == "trial_start_run":
        run = _object(data.get("run"))
        checks = {"run-id": isinstance(run.get("run_id"), str) and bool(run["run_id"]),
                  "run-session": bool(session_id) and run.get("session_id") == session_id,
                  "run-group": bool(group_id) and run.get("group_id") == group_id}
    elif method == "trial_query_graph":
        checks = {"graph-run-id": bool(run_id) and _object(data.get("run")).get("run_id") == run_id}
    elif method == "trial_query_pending":
        checks = {"pending-human-empty": isinstance(value, list) and value == []}
    elif method == "trial_respond_guard":
        checks = {"respond-refused": data.get("guard_refused") is True,
                  "respond-not-pending": data.get("not_pending") is True}
    elif method == "trial_messages":
        component, bound = _panel_observations(value, run_id)
        checks = {"panel-component": component, "panel-run": bound}
    elif method == "cancel_trial_run":
        run = _object(data.get("run"))
        checks = {"cancel-terminal": bool(run_id) and run.get("run_id") == run_id
                  and run.get("status") in ("aborted", "completed", "failed")}
    elif method == "read_trial_session":
        checks = {"session-running": bool(session_id) and (data.get("session_id") or data.get("id")) == session_id
                  and data.get("group_id") == group_id and data.get("status") == "running"}
    elif method == "create_persistent_group":
        ids = [r if isinstance(r, str) else _object(r).get("bot_uuid") for r in _rows(data.get("participants"))]
        checks = {"persistent-id": isinstance(data.get("id"), str) and bool(data["id"]),
                  "persistent-driver": bool(driver_id) and driver_id in ids,
                  "persistent-reviewer": bool(reviewer_id) and reviewer_id in ids}
    elif method == "read_persistent_group":
        checks = {"persistent-strategy": bool(group_id) and data.get("id") == group_id
                  and data.get("group_strategy") == "state_machine"}
    elif method in ("delete_trial_group", "delete_persistent_group"):
        name = "trial-cleanup" if method == "delete_trial_group" else "persistent-cleanup"
        checks = {name: bool(group_id) and data.get("id") == group_id and data.get("deleted") is True}
    return {"CUSTOM-COLLABORATION." + name: bool(ok) for name, ok in checks.items()}


def density_assertions(observations: dict) -> list[dict]:
    return [{"id": name, "status": "PASS" if observations.get(name) is True else "NOT_RUN"}
            for name in DENSITY_REQUIREMENTS]



def custom_collaboration_case_plan() -> dict:
    """Bind the complete reviewed parent to one explicit local case."""
    binding = custom_collaboration_source_binding()
    if (binding["parent"] != CUSTOM_COLLABORATION_PARENT
            or tuple(row["id"] for row in binding["requirements"]) != REQUIREMENTS
            or len(binding["requirements"]) != len(REQUIREMENTS)):
        raise LocalCliError("custom collaboration case source binding changed")
    return {
        "case_id": CASE_ID,
        "legacy_id": CUSTOM_COLLABORATION_PARENT,
        "requirement_ids": list(REQUIREMENTS),
        "parent_requirement_count": len(REQUIREMENTS),
    }


def custom_collaboration_case_adaptability() -> dict:
    plan = custom_collaboration_case_plan()
    return {
        "case_id": plan["case_id"],
        "legacy_id": plan["legacy_id"],
        "requirement_ids": plan["requirement_ids"],
        "selected_obligations": [
            "cli-collaboration-validate",
            "cli-collaboration-trial",
            "cli-collaboration-create-persistent-group",
        ],
        "environments": ["singlebox-product-cli", "preprod-product-cli"],
        "local_executor_status": "LOCAL_FULL_PARENT_EXECUTOR",
        "cli_surface": "product-bcs-cli-collaboration",
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "blocker": (
            "Owned-local implementation and mock-verified CLI evidence exist; "
            "preprod effect admission and execution remain separate gates."
        ),
    }


def _cleanup_created_group(runtime: OwnedBcsProcess, *, group_id: str, driver_id: str,
                           driver_token: str) -> dict:
    """Delete only the exact persistent custom-collaboration Group created by this case."""
    response = _request_trial_json(
        runtime, "DELETE", custom_collaboration_group_delete_path(group_id, driver_id),
        None, driver_token, 200,
    )
    if response.get("deleted") is not True or response.get("id") != group_id:
        raise LocalCliError("custom collaboration persistent Group cleanup differs")
    return project_density("delete_persistent_group", response, group_id=group_id)


def run_custom_collaboration_case(runtime: OwnedBcsProcess, *, state_dir: Path,
                                  binary: Path, binary_sha256: str,
                                  yaml_path: Path, driver_id: str, reviewer_id: str,
                                  human_id: str, driver_token: str,
                                  timeout_seconds: int = 30, density: bool = False) -> dict:
    """Run all three reviewed custom-collaboration obligations as one local case."""
    if not state_dir.is_absolute() or state_dir.is_symlink() or not state_dir.is_dir():
        raise LocalCliError("custom collaboration case requires a fresh absolute state directory")
    identifier(CASE_ID, "CLI case ID")
    identifier(driver_id, "custom collaboration driver Bot ID")
    identifier(reviewer_id, "custom collaboration reviewer Bot ID")
    identifier(human_id, "custom collaboration Human participant ID")
    identifier(driver_token, "custom collaboration driver Bot credential")
    if driver_id == reviewer_id:
        raise LocalCliError("custom collaboration requires two distinct Bots")
    if density:
        from .custom_collaboration.owned import run_density_case
        return run_density_case(
            runtime, state_dir=state_dir, binary=binary, binary_sha256=binary_sha256,
            yaml_path=yaml_path, driver_id=driver_id, reviewer_id=reviewer_id,
            human_id=human_id, driver_token=driver_token, timeout_seconds=timeout_seconds)
    plan = custom_collaboration_case_plan()
    started = time.monotonic()

    validate = validate_collaboration_yaml(
        runtime, state_dir=state_dir, case_id=CASE_ID,
        binary=binary, binary_sha256=binary_sha256, yaml_path=yaml_path,
        timeout_seconds=timeout_seconds,
    )
    trial = run_custom_collaboration_trial(
        runtime, state_dir=state_dir, case_id=CASE_ID,
        binary=binary, binary_sha256=binary_sha256, yaml_path=yaml_path,
        driver_id=driver_id, reviewer_id=reviewer_id, human_id=human_id,
        driver_token=driver_token, timeout_seconds=timeout_seconds,
    )
    created = create_custom_collaboration_group(
        runtime, state_dir=state_dir, case_id=CASE_ID,
        binary=binary, binary_sha256=binary_sha256, yaml_path=yaml_path,
        driver_id=driver_id, reviewer_id=reviewer_id, driver_token=driver_token,
        timeout_seconds=timeout_seconds,
    )
    group_id = created.get("group_id")
    if not isinstance(group_id, str) or not group_id:
        raise LocalCliError("custom collaboration persistent Group ID is missing")
    cleanup = _cleanup_created_group(runtime, group_id=group_id,
                           driver_id=driver_id, driver_token=driver_token)

    observations = {**validate.get("density_observations", {}),
                    **trial.get("density_observations", {}),
                    **created.get("density_observations", {}), **cleanup}
    assertions = [{"id": name, "status": "PASS"} for name in CUSTOM_COLLABORATION_REQUIREMENTS]
    assertions += density_assertions(observations)
    complete = all(row["status"] == "PASS" for row in assertions)
    result = {
        "schema_version": 1,
        "status": "LOCAL_CUSTOM_COLLABORATION_LIFECYCLE_PASS" if complete else "BLOCKED",
        "case_id": CASE_ID,
        "legacy_id": CUSTOM_COLLABORATION_PARENT,
        "assertions": assertions,
        "density_observations": observations,
        "selected_requirement_ids": list(REQUIREMENTS),
        "parent_requirement_count": len(REQUIREMENTS),
        "full_parent_coverage": "LOCAL_PARENT_REQUIREMENTS_ONLY",
        "operations": {
            "validate": validate,
            "trial": trial,
            "create-persistent-group": {**created, "group_cleanup": "DELETED"},
        },
        "persistent_group_cleanup": "DELETED",
        "binary_sha256": binary_sha256,
        "yaml_path": str(yaml_path),
        "driver_bot": driver_id,
        "human_participant": human_id,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "legacy_counts": plan,
    }
    write(state_dir / "local-custom-collaboration.json", result)
    return result
