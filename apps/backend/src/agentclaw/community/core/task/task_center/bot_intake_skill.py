"""TaskIntakeSkill implementation using the selected Bot's existing run API."""

from __future__ import annotations

import json
from typing import Any

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.domain.intake import IntakeTarget, IntakeTargetType
from agentclaw.community.core.task.domain.json_extract import extract_json
from agentclaw.community.core.task.domain.models import TaskSpec
from agentclaw.community.core.task.repository.serializers import task_spec_from_dict
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort


class BotTaskIntakeSkill:
    def __init__(self, bot: OpenApiBotPort, bot_service: Any) -> None:
        self._bot = bot
        self._bot_service = bot_service

    async def recognize(
        self, message: str, target: IntakeTarget, owner_user_id: str,
    ) -> tuple[TaskSpec, list[str]]:
        return await self._infer(message, target, owner_user_id, None)

    async def clarify(
        self, existing_spec: TaskSpec | None, message: str, target: IntakeTarget,
        owner_user_id: str,
    ) -> tuple[TaskSpec, list[str]]:
        return await self._infer(message, target, owner_user_id, existing_spec)

    async def _infer(
        self, message: str, target: IntakeTarget, owner_user_id: str,
        existing_spec: TaskSpec | None,
    ) -> tuple[TaskSpec, list[str]]:
        if target.target_type != IntakeTargetType.BOT:
            raise TaskStateError("Group intake requires an authenticated manager Bot resolver")
        bot = self._bot_service.get_bot(target.target_id, owner_user_id)
        if not isinstance(bot, dict) or bot.get("owner_id") != owner_user_id:
            raise TaskStateError("intake target Bot is unavailable")
        prompt = (
            "你负责识别或澄清任务规格，只输出 JSON 对象，键为 task_spec 和 questions。"
            "task_spec.context 包含 title、background、extend_props；"
            "extend_props 中 deliverables/resources 是列表。只有用户明确说出约束或明确表示无约束，"
            "才写 constraints 列表；未澄清时不写 constraints 键。"
            "task_spec.goal 包含 objective 和 acceptances，acceptances 项有 id、description。"
            "不能创建任务、设置 owner、选择执行者或修改用户未提及的既有字段。\n"
            f"已有规格：{json.dumps(existing_spec.to_dict() if existing_spec else None, ensure_ascii=False)}\n"
            f"用户本轮输入：{message}"
        )
        run = await self._bot.send_and_wait_async(
            bot_id=compose_bot_identity(target.target_id, owner_user_id),
            message=prompt,
            metadata={"phase": "task_intake"},
        )
        if str(run.get("status") or "").upper() != "COMPLETED":
            raise TaskStateError("task intake Bot run did not complete")
        raw = run.get("result")
        content = raw.get("content") if isinstance(raw, dict) else raw
        try:
            data = extract_json(content)
        except (TypeError, ValueError) as exc:
            raise TaskStateError("task intake Bot returned invalid JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("task_spec"), dict):
            raise TaskStateError("task intake Bot returned no task_spec")
        spec_data = data["task_spec"]
        if existing_spec is not None:
            prior = existing_spec.to_dict()
            context = dict(prior["context"])
            incoming_context = spec_data.get("context") or {}
            if not isinstance(incoming_context, dict):
                raise TaskStateError("task intake context must be an object")
            context.update({key: value for key, value in incoming_context.items() if key != "extend_props"})
            context["extend_props"] = {
                **prior["context"].get("extend_props", {}),
                **dict(incoming_context.get("extend_props") or {}),
            }
            spec_data = {
                "context": context,
                "goal": {**prior["goal"], **dict(spec_data.get("goal") or {})},
            }
        spec = task_spec_from_dict(spec_data)
        questions = data.get("questions") or []
        if not isinstance(questions, list):
            raise TaskStateError("task intake questions must be a list")
        return spec, [str(item) for item in questions if str(item).strip()]
