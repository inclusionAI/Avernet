"""Architecture guards for the task module after removal of ``engine.py``.

The task package is assembled from existing deep modules.  It must not regress to a
new all-purpose engine/lifecycle class or duplicate the owner classes in mode-specific
files.  The checks are structural and intentionally independent of runtime wiring.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_THIS_FILE = Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]
_TASK_ROOT = _BACKEND_ROOT / "src" / "agentclaw" / "community" / "core" / "task"

_OWNER_CLASSES = {
    "TaskPlanner": "task_plan/planner.py",
    "TaskDispatcher": "task_dispatch/dispatcher.py",
    "TaskRunner": "task_runner/task_runner.py",
    "TaskHarness": "task_harness/harness.py",
    "TaskGraphService": "task_context/task_graph_service.py",
    "TaskTrajectoryService": "task_context/task_trajectory/trajectory_service.py",
    "StaticPlanRuntime": "task_plan/static_plan.py",
    "CentralizedExecutionAdapter": "task_runner/execution_adapters.py",
    "RelayExecutionAdapter": "task_runner/execution_adapters.py",
}

_FORBIDDEN_TRANSITION_FILES = {
    "task_context/task_context_projection.py",
    "task_context/task_graph_defaults.py",
    "task_context/task_graph_queries.py",
    "task_context/task_graph_reports.py",
    "task_context/task_graph_state_policy.py",
    "task_plan/static_plan_runtime.py",
    "task_plan/static_plan_execution.py",
    "task_plan/static_plan_bbs.py",
}

_STATIC_METHOD_OWNERS = {
    "TaskPlanner": {"_on_static_execute", "_static_next_wave", "_on_static_report"},
    "TaskDispatcher": {"_prepare_static", "_prepare_static_into"},
    "TaskRunner": {
        "_static_auto_report_on",
        "_static_auto_report",
        "_static_bbs_handoff_auto_report",
    },
    "TaskHarness": {
        "_static_fallback_delay",
        "_static_mock_fallback_delay",
        "_static_auto_report_delay",
        "_bbs_handoff_delay",
        "_on_bbs_handoff_done",
        "_bbs_handoff_claim",
    },
}

_FORBIDDEN_ORCHESTRATOR_CLASSES = {
    "ExecutionEngine",
    "CentralizedTaskLifecycle",
    "RelayTaskLifecycle",
}


def _python_sources() -> list[Path]:
    return sorted(
        path for path in _TASK_ROOT.rglob("*.py") if "__pycache__" not in path.parts
    )


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.unit
def test_task_module_must_not_reintroduce_engine_py() -> None:
    offenders = [
        path.relative_to(_TASK_ROOT).as_posix()
        for path in _python_sources()
        if path.name == "engine.py"
    ]
    assert offenders == [], (
        "The task module is composed from Planner/Dispatcher/Runner/Graph/Harness; "
        f"do not reintroduce an engine.py orchestrator: {offenders}"
    )


@pytest.mark.unit
def test_task_module_must_not_import_removed_engine_module() -> None:
    removed_module = "agentclaw.community.core.task.task_center.engine"
    offenders: list[str] = []
    for path in _python_sources():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Import):
                if any(alias.name == removed_module for alias in node.names):
                    offenders.append(f"{path.relative_to(_TASK_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and node.module == removed_module:
                offenders.append(f"{path.relative_to(_TASK_ROOT)}:{node.lineno}")
    assert offenders == [], f"Removed task engine imports found: {offenders}"


@pytest.mark.unit
def test_task_module_has_no_replacement_engine_or_handler_classes() -> None:
    offenders: list[str] = []
    for path in _python_sources():
        for node in _tree(path).body:
            if not isinstance(node, ast.ClassDef):
                continue
            forbidden = (
                node.name in _FORBIDDEN_ORCHESTRATOR_CLASSES
                or (
                    node.name.startswith("Centralized")
                    and node.name.endswith("Handler")
                )
                or (node.name.startswith("Relay") and node.name.endswith("Handler"))
            )
            if forbidden:
                offenders.append(
                    f"{path.relative_to(_TASK_ROOT)}:{node.lineno} {node.name}"
                )
    assert offenders == [], (
        "Do not replace engine.py with another lifecycle/handler orchestrator: "
        f"{offenders}"
    )


@pytest.mark.unit
def test_task_owner_classes_stay_in_their_owning_modules() -> None:
    definitions: dict[str, list[str]] = {name: [] for name in _OWNER_CLASSES}
    for path in _python_sources():
        relative = path.relative_to(_TASK_ROOT).as_posix()
        for node in _tree(path).body:
            if isinstance(node, ast.ClassDef) and node.name in definitions:
                definitions[node.name].append(relative)

    failures = {
        class_name: {"expected": expected, "actual": definitions[class_name]}
        for class_name, expected in _OWNER_CLASSES.items()
        if definitions[class_name] != [expected]
    }
    assert failures == {}, (
        "Task owner classes must remain unique and in their responsibility module: "
        f"{failures}"
    )


@pytest.mark.unit
def test_task_module_does_not_restore_transitional_split_files() -> None:
    existing = {path.relative_to(_TASK_ROOT).as_posix() for path in _python_sources()}
    offenders = sorted(existing & _FORBIDDEN_TRANSITION_FILES)
    assert offenders == [], (
        "Keep Graph and Static Plan consolidated; transitional split files are forbidden: "
        f"{offenders}"
    )


@pytest.mark.unit
def test_static_plan_orchestration_methods_stay_with_existing_modules() -> None:
    definitions: dict[str, set[str]] = {}
    for path in _python_sources():
        for node in _tree(path).body:
            if not isinstance(node, ast.ClassDef):
                continue
            definitions.setdefault(node.name, set()).update(
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
    failures = {
        owner: sorted(methods - definitions.get(owner, set()))
        for owner, methods in _STATIC_METHOD_OWNERS.items()
        if methods - definitions.get(owner, set())
    }
    assert failures == {}, (
        "Static Plan orchestration must remain in Planner/Dispatcher/Runner/Harness: "
        f"{failures}"
    )


@pytest.mark.unit
def test_domain_services_are_not_extended_by_runtime_monkey_patch() -> None:
    offenders: list[str] = []
    for path in _python_sources():
        for node in _tree(path).body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in {"TaskGraphService", "StaticPlanRuntime"}
                ):
                    offenders.append(f"{path.relative_to(_TASK_ROOT)}:{node.lineno}")
    assert offenders == [], (
        "TaskGraphService/StaticPlanRuntime behavior must be declared by their owning modules, not "
        f"attached at module import time: {offenders}"
    )


_COMMUNITY_SRC_ROOT = (
    _BACKEND_ROOT / "src" / "agentclaw" / "community"
)
_TASK_DOMAIN_REQUIRED_PATHS = (
    "api/task/forum_service.py",
    "api/task/feedback_service.py",
    "core/task/forum/service_protocol.py",
    "core/task/feedback/service_protocol.py",
    "core/repository/protocols/task/forum.py",
    "core/repository/protocols/task/feedback.py",
    "core/repository/implementations/task/forum/forum_repository.py",
    "core/repository/implementations/task/feedback/feedback_repository.py",
)
_LEGACY_TASK_DOMAIN_PATHS = (
    "api/forum_service.py",
    "core/forum/service_protocol.py",
    "core/feedback/service_protocol.py",
    "core/repository/protocols/forum.py",
    "core/repository/protocols/feedback.py",
    "core/repository/implementations/forum/forum_repository.py",
    "core/repository/implementations/feedback/feedback_repository.py",
    "di/modules/forum_module.py",
    "di/modules/feedback_module.py",
)
_LEGACY_TASK_DOMAIN_IMPORTS = frozenset({
    "agentclaw.community.core.forum",
    "agentclaw.community.core.feedback",
    "agentclaw.community.core.repository.protocols.forum",
    "agentclaw.community.core.repository.protocols.feedback",
    "agentclaw.community.core.repository.implementations.forum",
    "agentclaw.community.core.repository.implementations.feedback",
})


@pytest.mark.unit
def test_bbs_and_feedback_sources_stay_in_the_task_domain() -> None:
    """BBS content and task feedback are task-domain modules, not side domains.

    The HTTP layer was relocated first; this guard keeps the owning core,
    repository contracts/implementations, API re-exports, and composition-root
    files in the same domain instead of drifting back into parallel packages.
    """
    missing = [
        relative
        for relative in _TASK_DOMAIN_REQUIRED_PATHS
        if not (_COMMUNITY_SRC_ROOT / relative).is_file()
    ]
    restored = [
        relative
        for relative in _LEGACY_TASK_DOMAIN_PATHS
        if (_COMMUNITY_SRC_ROOT / relative).exists()
    ]
    assert missing == [], f"Missing task-domain source locations: {missing}"
    assert restored == [], f"Legacy BBS/feedback task-domain paths restored: {restored}"


@pytest.mark.unit
def test_python_sources_do_not_import_legacy_bbs_feedback_modules() -> None:
    offenders: list[str] = []
    for path in sorted(_COMMUNITY_SRC_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in _LEGACY_TASK_DOMAIN_IMPORTS:
                        offenders.append(
                            f"{path.relative_to(_COMMUNITY_SRC_ROOT)}:{node.lineno} "
                            f"import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module in _LEGACY_TASK_DOMAIN_IMPORTS:
                offenders.append(
                    f"{path.relative_to(_COMMUNITY_SRC_ROOT)}:{node.lineno} "
                    f"from {node.module} import"
                )
    assert offenders == [], (
        "BBS/forum and task-feedback imports must use the task-domain module paths:\n  "
        + "\n  ".join(offenders)
    )
