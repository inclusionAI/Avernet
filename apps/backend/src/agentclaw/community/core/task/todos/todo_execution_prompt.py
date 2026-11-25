"""Bot-visible runtime context for one Todo run generation."""

from __future__ import annotations


TODO_RUNNER_RELATIVE_SKILL_MD_PATH = (
    "/workspace/skills/skills-repo/infra/todo-runner/SKILL.md"
)
_TODO_RUNNER_WORKSPACE_ROOTS = {
    "openclaw": "/home/admin/.openclaw",
    "claude_code": "/home/admin/.claude_code",
    "aicoding": "/home/admin/.aicoding",
    "hermes": "/home/admin/.harness",
}


def todo_runner_skill_md_path(executor_runtime_engine: str) -> str:
    """Return the Bot-visible todo-runner source for one runtime engine.

    Todo loads the runner from the full infra content store, never from the
    Bot-specific active Skill directory. The mapping deliberately covers the
    physical runtimes that dispatch Todos today and fails closed for engines
    without a reviewed runner layout instead of falling back to OpenClaw.
    """

    root = _TODO_RUNNER_WORKSPACE_ROOTS.get(executor_runtime_engine)
    if root is None:
        raise ValueError(
            "todo-runner Skill layout is not implemented for runtime engine: "
            f"{executor_runtime_engine!r}"
        )
    return root + TODO_RUNNER_RELATIVE_SKILL_MD_PATH


def format_todo_execution_instruction(
    *,
    todo_id: str,
    todo_execution_id: str,
    todo_execution_token: str,
    backend_url: str,
    instruction: str,
    resume: bool,
    todo_runner_skill_md_path: str,
) -> str:
    """Build the dynamic runtime context consumed by ``todo-runner``."""
    backend = backend_url.rstrip("/")
    opening = "[todo-resume]" if resume else "[todo-execute]"
    context_label = "【Todo 返工执行上下文】" if resume else "【Todo 执行上下文】"
    objective_label = "本轮修订要求" if resume else "本轮执行目标"
    parts = [
        opening,
        context_label,
        f"执行前必须先读取并加载 todo-runner Skill：{todo_runner_skill_md_path}",
        "每次收到 [todo-execute] 或 [todo-resume] 都必须从上述 infra 路径重新读取 "
        "todo-runner/SKILL.md，并严格按照其中的流程执行；不要为 todo-runner 创建"
        "软链接，也不要假定它已在 workspace/skills 顶层激活。",
        "如果 todo-runner/SKILL.md 不存在或无法读取，必须明确报告 "
        "todo-runner skill unavailable 并停止本轮，不得跳过 Skill 直接执行。",
        "本轮运行参数：",
        f"todo_id={todo_id}",
        f"todo_execution_id={todo_execution_id}",
        f"todo_execution_token={todo_execution_token}",
        f"backend_url={backend}",
        f"{objective_label}：{instruction}",
    ]
    if resume:
        parts.append(
            "这是新的执行代次；之前执行代次已失效，不得继续使用旧的 "
            "todo_execution_id 或 token。"
        )
    parts.append(
        "请严格按照 todo-runner Skill 定义的执行、产物上报和完成流程处理本轮 Todo。"
    )
    return "\n".join(parts)


__all__ = [
    "format_todo_execution_instruction",
    "todo_runner_skill_md_path",
]
