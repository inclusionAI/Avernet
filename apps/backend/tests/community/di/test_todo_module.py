from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentclaw.community.core.task.context_collab.services.context_collab_service import (
    ContextCollabService,
)
from agentclaw.community.di import todo_config
from agentclaw.community.di.modules import todo_module
from agentclaw.community.di.todo_config import TodoSchedulerConfig
from agentclaw.community.di.modules.todo_module import TodoModule
from agentclaw.community.plugins.community.todo_collaboration import (
    InProcessTodoCollaborationPlugin,
)

ROOT = Path(__file__).resolve().parents[3]


def _patch_block(monkeypatch: pytest.MonkeyPatch, value: Any) -> None:
    monkeypatch.setattr(todo_module, "_block", lambda _name: value)


def test_todo_scheduler_config_defaults_to_enabled():
    assert TodoSchedulerConfig() == TodoSchedulerConfig(
        enabled=True,
        interval=60,
        batch_size=50,
        jitter=5,
        lock_ttl=240,
        heartbeat=45,
    )


def test_todo_module_parses_scheduler_config(monkeypatch: pytest.MonkeyPatch):
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

    assert TodoModule().todo_scheduler_config() == TodoSchedulerConfig(
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
def test_todo_module_rejects_invalid_scheduler_config(
    monkeypatch: pytest.MonkeyPatch,
    block: Any,
    match: str,
):
    _patch_block(monkeypatch, block)

    with pytest.raises(ValueError, match=match):
        TodoModule().todo_scheduler_config()


def test_task_module_no_longer_owns_todo_bindings():
    source = (ROOT / "src/agentclaw/community/di/modules/task_module.py").read_text()

    assert "TodoRepositoryProtocol" not in source
    assert "TodoServiceProtocol" not in source
    assert "TodoCollaborationPlugin" not in source


def test_container_installs_todo_module():
    source = (ROOT / "src/agentclaw/community/di/container.py").read_text()

    assert "from agentclaw.community.di.modules.todo_module import TodoModule" in source
    assert "TodoModule()," in source


def test_config_type_stays_in_dedicated_todo_config_module():
    assert todo_config.TodoSchedulerConfig is TodoSchedulerConfig


def test_todo_module_wires_in_process_collaboration_adapter():
    context_collab = object.__new__(ContextCollabService)

    plugin = TodoModule().todo_collaboration_plugin(context_collab)

    assert isinstance(plugin, InProcessTodoCollaborationPlugin)
    assert plugin._service is context_collab
