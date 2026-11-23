from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from injector import Injector, Module

from agentclaw.community.core.repository.implementations.task.todos import (
    TodoTemplateRepository,
)
from agentclaw.community.core.repository.protocols.task.todo_templates import (
    TodoTemplateRepositoryProtocol,
)
from agentclaw.community.core.spaces.protocols import SpaceAccessServiceProtocol
from agentclaw.community.core.task.context_collab.l6_products.operations.collaboration_creation import (
    ContextCollabService,
)
from agentclaw.community.core.task.todos.template_management_protocol import (
    TodoTemplateManagementServiceProtocol,
)
from agentclaw.community.core.task.todos.collaboration.in_process import (
    InProcessTodoCollaborationAdapter,
)
from agentclaw.community.di import todo_config
from agentclaw.community.di.modules.task import todo_bindings
from agentclaw.community.di.modules.task.todo_bindings import (
    TaskTodoBindings,
)
from agentclaw.community.di.todo_config import TodoSchedulerConfig
from agentclaw.community.plugin_api.database import DatabasePlugin


class _InstanceModule(Module):
    def __init__(self, instances: dict[type, object]):
        self._instances = instances

    def configure(self, binder):
        for interface, instance in self._instances.items():
            binder.bind(interface, to=instance)


ROOT = Path(__file__).resolve().parents[3]


def _patch_block(monkeypatch: pytest.MonkeyPatch, value: Any) -> None:
    monkeypatch.setattr(todo_bindings, "_block", lambda _name: value)


def test_todo_scheduler_config_defaults_to_enabled():
    assert TodoSchedulerConfig() == TodoSchedulerConfig(
        enabled=True,
        interval=60,
        batch_size=50,
        jitter=5,
        lock_ttl=240,
        heartbeat=45,
    )


def test_task_todo_bindings_parse_scheduler_config(monkeypatch: pytest.MonkeyPatch):
    _patch_block(
        monkeypatch,
        {
            "scheduler": {
                "enabled": True,
                "interval": "30",
                "batch_size": "25",
                "jitter": 0,
                "lock_ttl": 90,
                "heartbeat": 20,
            }
        },
    )

    assert TaskTodoBindings().todo_scheduler_config() == TodoSchedulerConfig(
        enabled=True,
        interval=30,
        batch_size=25,
        jitter=0,
        lock_ttl=90,
        heartbeat=20,
    )


@pytest.mark.parametrize(
    "block,match",
    [
        ({"unknown": {}}, "unknown todo key"),
        ({"scheduler": {"typo": 1}}, "unknown todo.scheduler key"),
        ({"scheduler": {"enabled": "maybe"}}, "must be a boolean"),
        ({"scheduler": {"batch_size": 0}}, "must be at least 1"),
        (
            {"scheduler": {"heartbeat": 60, "lock_ttl": 60}},
            "heartbeat must be less than lock_ttl",
        ),
    ],
)
def test_task_todo_bindings_reject_invalid_scheduler_config(
    monkeypatch: pytest.MonkeyPatch,
    block: Any,
    match: str,
):
    _patch_block(monkeypatch, block)

    with pytest.raises(ValueError, match=match):
        TaskTodoBindings().todo_scheduler_config()


def test_task_module_installs_task_owned_todo_bindings():
    source = (ROOT / "src/agentclaw/community/di/modules/task_module.py").read_text()

    assert (
        "from agentclaw.community.di.modules.task.todo_bindings import TaskTodoBindings"
    ) in source
    assert "binder.install(TaskTodoBindings())" in source


def test_container_does_not_install_peer_todo_module():
    source = (ROOT / "src/agentclaw/community/di/container.py").read_text()

    assert "TodoModule" not in source
    assert "todo_module" not in source


def test_config_type_stays_in_dedicated_todo_config_module():
    assert todo_config.TodoSchedulerConfig is TodoSchedulerConfig


def test_task_todo_bindings_wire_in_process_collaboration_adapter():
    context_collab = object.__new__(ContextCollabService)

    repository, executor = object(), object()
    adapter = TaskTodoBindings().todo_collaboration_port(context_collab, repository, executor)

    assert isinstance(adapter, InProcessTodoCollaborationAdapter)
    assert adapter._service is context_collab
    assert adapter._runtime._service is context_collab
    assert adapter._runtime._repository is repository
    assert adapter._runtime._executor is executor


def test_task_todo_bindings_resolve_template_repository_and_service():
    repository_module = _InstanceModule({DatabasePlugin: object()})
    repository_injector = Injector([TaskTodoBindings(), repository_module])
    repository = repository_injector.get(TodoTemplateRepositoryProtocol)

    assert isinstance(repository, TodoTemplateRepository)
    assert repository._codec is not None

    repository_module = _InstanceModule(
        {
            TodoTemplateRepositoryProtocol: repository,
            SpaceAccessServiceProtocol: object(),
        }
    )
    service_injector = Injector([TaskTodoBindings(), repository_module])
    service = service_injector.get(TodoTemplateManagementServiceProtocol)

    assert service._repository is repository
    assert service._space_access is not None
