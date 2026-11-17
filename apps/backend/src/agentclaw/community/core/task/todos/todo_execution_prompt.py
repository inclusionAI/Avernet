"""Bot-visible execution contract for one Todo run generation."""

from __future__ import annotations

import json


TODO_RUNNER_SKILL_MD_PATH = (
    "/home/admin/.openclaw/workspace/skills/skills-repo/infra/todo-runner/SKILL.md"
)


def format_todo_execution_instruction(
    *,
    todo_id: str,
    todo_execution_id: str,
    todo_execution_token: str,
    backend_url: str,
    instruction: str,
    resume: bool,
) -> str:
    """Build the complete Todo execution and artifact-reporting instruction."""
    backend = backend_url.rstrip("/")
    report_url = f"{backend}/api/v1/collaboration/tasks/todos/{todo_id}/report"
    upload_url = f"{backend}/api/v1/collaboration/tasks/todos/{todo_id}/artifacts/object-file"
    example = {
        "todo_execution_id": todo_execution_id,
        "artifact_name": "执行结果",
        "kind": "text",
        "content": {"text": "完整执行产出"},
        "is_final_artifact": True,
    }
    opening = "[todo-resume]" if resume else "[todo-execute]"
    objective_label = "本轮修订要求" if resume else "本轮执行目标"
    parts = [
        opening,
        "【Todo 执行与上报协议】只执行本 Todo，并使用本次执行代次上报产物。",
        f"执行前必须先读取并加载 todo-runner Skill：{TODO_RUNNER_SKILL_MD_PATH}",
        "每次收到 [todo-execute] 或 [todo-resume] 都必须从上述 infra 路径重新读取 "
        "todo-runner/SKILL.md，并严格按照其中的流程执行；不要为 todo-runner 创建"
        "软链接，也不要假定它已在 workspace/skills 顶层激活。",
        "如果 todo-runner/SKILL.md 不存在或无法读取，必须明确报告 "
        "todo-runner skill unavailable 并停止本轮，不得跳过 Skill 直接执行。",
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
    parts.extend(
        [
            "todo_execution_id 标识本次执行代次；todo_execution_token 是该代次的"
            "上报凭证。协作上下文由 Backend 根据执行代次确定，不得传入或猜测。",
            "非文件产物上报：",
            f"POST {report_url}",
            f"X-Todo-Execution-Token: {todo_execution_token}",
            json.dumps(example, ensure_ascii=False, indent=2),
            "文件产物上传：",
            f"POST {upload_url}",
            f"X-Todo-Execution-Token: {todo_execution_token}",
            "multipart/form-data 字段：todo_execution_id、artifact_name（可选）、"
            "is_final_artifact、file。",
            "最终执行结果必须至少上报一个产物；只有 HTTP 成功响应才算上报成功。"
            "收到凭证失效、鉴权失败或状态冲突时停止，不得自行生成新的 ID 或 token。",
            "安全要求：token 只能用于上述请求头，不得写入 Bot 回复、产物内容、"
            "文件、日志或转发给其他主体。metadata 仅用于链路观测，不能依赖其完成上报。",
        ]
    )
    return "\n".join(parts)


__all__ = ["TODO_RUNNER_SKILL_MD_PATH", "format_todo_execution_instruction"]
