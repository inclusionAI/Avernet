"""Structural guards for the unified task graph domain service."""

from __future__ import annotations

import ast
from pathlib import Path

from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)


def test_task_graph_service_has_no_mode_specific_base_class() -> None:
    assert TaskGraphService.__bases__ == (object,)
    assert hasattr(TaskGraphService, "report")
    assert hasattr(TaskGraphService, "get_task_context")


def test_task_graph_implementation_modules_define_no_service_or_mixin_classes() -> None:
    context_dir = (
        Path(__file__).parents[5]
        / "src"
        / "agentclaw"
        / "community"
        / "core"
        / "task"
        / "task_context"
    )
    implementation_files = ("task_graph_support.py",)
    for filename in implementation_files:
        tree = ast.parse((context_dir / filename).read_text(encoding="utf-8"))
        classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
        assert classes == [], f"{filename} must contain functions only: {classes}"


def test_task_graph_service_has_no_runtime_method_monkey_patch() -> None:
    service_file = (
        Path(__file__).parents[5]
        / "src"
        / "agentclaw"
        / "community"
        / "core"
        / "task"
        / "task_context"
        / "task_graph_service.py"
    )
    tree = ast.parse(service_file.read_text(encoding="utf-8"))
    assignments = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        assignments.extend(
            target
            for target in targets
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "TaskGraphService"
        )
    assert assignments == []
