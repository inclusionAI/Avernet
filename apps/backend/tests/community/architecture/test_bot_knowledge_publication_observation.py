"""Keep optional display observations outside native publication transactions."""

import ast
import inspect
from pathlib import Path

from agentclaw.community.core.bot_knowledge import publication_selection
from agentclaw.community.core.service_bot.services.publish_flow import tasks


def test_only_build_task_records_publication_selection():
    root = Path(tasks.__file__).parents[2]
    callers = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "record_build_selection":
                    callers.append(path.relative_to(root).as_posix())
    assert callers == ["services/publish_flow/tasks.py"]
    method = ast.parse(inspect.getsource(tasks))
    handler = next(
        node
        for node in method.body
        if isinstance(node, ast.ClassDef) and node.name == "PublishVerifyFlowHandler"
    )
    run = next(
        node
        for node in handler.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run"
    )
    building = next(
        node
        for node in ast.walk(run)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "status == PublishStatus.BUILDING"
    )
    optional = building.body[0]
    assert isinstance(optional, ast.Try)
    assert optional.handlers[0].type.id == "Exception"
    assert "record_build_selection" in ast.unparse(optional)
    assert "execute_build_phase" not in ast.unparse(optional)
    assert "execute_build_phase" in ast.unparse(building.body[1])


def test_observer_cannot_mutate_native_state_or_create_retry_infrastructure():
    tree = ast.parse(inspect.getsource(publication_selection))
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert calls.count("put") == 1
    assert not set(calls) & {
        "add",
        "delete",
        "update",
        "execute",
        "commit",
        "enqueue",
        "transactional_orm_session",
        "create_task",
        "ThreadPoolExecutor",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(
                ("fastapi", "starlette", "asyncio", "concurrent")
            )
    source = inspect.getsource(publication_selection)
    assert "online_enabled" not in source
    assert "knowledge_selection_result" not in source


def test_version_read_does_not_capture_or_write():
    from textwrap import dedent

    reader = ast.parse(
        dedent(
            inspect.getsource(
                publication_selection.BotKnowledgePublicationSelection.get_publication_selection
            )
        )
    )
    methods = [
        node.func.attr
        for node in ast.walk(reader)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert not set(methods) & {"put", "record_build_selection", "set_enabled", "mutate"}
