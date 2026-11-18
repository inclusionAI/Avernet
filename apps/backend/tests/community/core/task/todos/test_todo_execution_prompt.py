"""Tests for the Todo runtime Skill path contract."""

from __future__ import annotations

import pytest

from agentclaw.community.core.task.todos.todo_execution_prompt import (
    format_todo_execution_instruction,
    todo_runner_skill_md_path,
)


@pytest.mark.parametrize(
    ("engine", "expected_root"),
    [
        ("openclaw", "/home/admin/.openclaw"),
        ("claude_code", "/home/admin/.claude_code"),
        ("aicoding", "/home/admin/.aicoding"),
        ("hermes", "/home/admin/.harness"),
    ],
)
def test_resolves_runner_skill_by_runtime_engine(
    engine: str,
    expected_root: str,
) -> None:
    path = todo_runner_skill_md_path(engine)

    expected = (
        f"{expected_root}/workspace/skills/skills-repo/infra/todo-runner/SKILL.md"
    )
    assert path == expected


def test_rejects_unsupported_runtime_engine_without_openclaw_fallback() -> None:
    with pytest.raises(
        ValueError,
        match="not implemented for runtime engine: 'teclaw'",
    ):
        todo_runner_skill_md_path("teclaw")


def test_instruction_uses_supplied_runner_skill_path() -> None:
    path = todo_runner_skill_md_path("aicoding")

    message = format_todo_execution_instruction(
        todo_id="todo_1",
        todo_execution_id="todoexec_1",
        todo_execution_token="token_1",
        backend_url="https://backend.example/",
        instruction="Do the work",
        resume=False,
        todo_runner_skill_md_path=path,
    )

    assert path in message
    assert "todo_id=todo_1" in message
    assert "todo_execution_id=todoexec_1" in message
    assert "todo_execution_token=token_1" in message
    assert "backend_url=https://backend.example" in message
