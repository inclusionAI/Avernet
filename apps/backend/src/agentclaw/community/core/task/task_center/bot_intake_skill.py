"""TaskIntakeSkill implementation using the selected Bot's existing run API."""

from __future__ import annotations

import json
import hashlib
import time
from dataclasses import replace
from typing import Any

from agentclaw.community.core.task.domain.errors import (
    TaskA2AStatusUnknownError, TaskStateError,
)
from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.domain.intake import (
    IntakeTarget,
    IntakeTargetType,
    TaskIntakeResult,
)
from agentclaw.community.core.task.domain.json_extract import extract_json
from agentclaw.community.core.task.domain.models import TaskSpec
from agentclaw.community.core.task.repository.serializers import task_spec_from_dict
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort
from agentclaw.community.core.task.task_runner.client.ports import BcsClientPort
from agentclaw.community.core.task.task_runner.client.bcs_bot_token_provider import (
    BcsBotTokenProvider,
)
from agentclaw.community.core.task.repository.task_intake_repository import (
    TaskIntakeRepositoryProtocol,
)


class BotTaskIntakeSkill:
    def __init__(
        self,
        bot: OpenApiBotPort,
        bot_service: Any,
        *,
        bcs: BcsClientPort | None = None,
        bot_tokens: BcsBotTokenProvider | None = None,
        intake_repository: TaskIntakeRepositoryProtocol | None = None,
        a2a_timeout: float = 180.0,
    ) -> None:
        self._bot = bot
        self._bot_service = bot_service
        self._bcs = bcs
        self._bot_tokens = bot_tokens
        self._intake_repository = intake_repository
        self._a2a_timeout = a2a_timeout

    async def recognize(
        self, message: str, target: IntakeTarget, owner_user_id: str,
        *, a2a_proxy_bot_id: str | None = None,
        intake_session_id: str | None = None,
    ) -> tuple[TaskSpec, list[str]]:
        return await self._infer(
            message, target, owner_user_id, None,
            a2a_proxy_bot_id=a2a_proxy_bot_id,
            intake_session_id=intake_session_id,
        )

    async def clarify(
        self, existing_spec: TaskSpec | None, message: str, target: IntakeTarget,
        owner_user_id: str, *, a2a_proxy_bot_id: str | None = None,
        intake_session_id: str | None = None,
    ) -> tuple[TaskSpec, list[str]]:
        return await self._infer(
            message, target, owner_user_id, existing_spec,
            a2a_proxy_bot_id=a2a_proxy_bot_id,
            intake_session_id=intake_session_id,
        )

    async def _infer(
        self, message: str, target: IntakeTarget, owner_user_id: str,
        existing_spec: TaskSpec | None, *, a2a_proxy_bot_id: str | None,
        intake_session_id: str | None,
    ) -> tuple[TaskSpec, list[str]]:
        if target.target_type != IntakeTargetType.BOT:
            raise TaskStateError("Group intake requires an authenticated manager Bot resolver")
        target_bot_id, separator, target_owner_id = target.target_id.partition(":")
        target_owner_id = target_owner_id if separator else owner_user_id
        bot = self._bot_service.get_bot(target_bot_id, target_owner_id)
        if not isinstance(bot, dict) or bot.get("owner_id") != target_owner_id:
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
        if a2a_proxy_bot_id:
            run = await self._send_and_wait_a2a(
                proxy_bot_id=a2a_proxy_bot_id,
                target_bot_id=target_bot_id,
                target_owner_id=target_owner_id,
                message=prompt,
                intake_session_id=intake_session_id,
                phase="clarify" if existing_spec is not None else "recognize",
            )
        else:
            run = await self._bot.send_and_wait_async(
                bot_id=compose_bot_identity(target_bot_id, target_owner_id),
                message=prompt,
                metadata={"phase": "task_intake"},
            )
        return self._parse_run(run, existing_spec)

    async def resume_pending(
        self, result: TaskIntakeResult,
    ) -> tuple[TaskSpec, list[str]] | None:
        if not result.a2a_run_id:
            return None
        if not result.a2a_proxy_bot_id:
            raise TaskStateError("pending A2A intake has no proxy Bot")
        token = self._require_a2a_token(result.a2a_proxy_bot_id)
        run = await self._wait_a2a_run(
            result.a2a_run_id,
            token=token,
            timeout=self._a2a_timeout,
            since_version=result.a2a_run_version,
        )
        return self._parse_run(run, result.task_spec)

    @staticmethod
    def _parse_run(
        run: dict[str, Any], existing_spec: TaskSpec | None,
    ) -> tuple[TaskSpec, list[str]]:
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

    async def _send_and_wait_a2a(
        self,
        *,
        proxy_bot_id: str,
        target_bot_id: str,
        target_owner_id: str,
        message: str,
        intake_session_id: str | None,
        phase: str,
    ) -> dict[str, Any]:
        if self._bcs is None or self._bot_tokens is None:
            raise TaskStateError("TASK_A2A_PROXY_UNAVAILABLE: BCS A2A is not configured")
        token = self._require_a2a_token(proxy_bot_id)
        session_key = f"task-intake:{intake_session_id or 'unknown'}"
        digest = hashlib.sha256(
            f"{session_key}:{phase}:{message}".encode("utf-8")
        ).hexdigest()
        try:
            sent = await self._bcs.send_message_a2a(
                target_bot_id=target_bot_id,
                target_user_id=target_owner_id,
                message=message,
                session_id=session_key,
                caller_bearer=token,
                idempotency_key=digest,
            )
        except Exception as exc:
            if "403" in str(exc):
                raise TaskStateError("TASK_A2A_FORBIDDEN") from exc
            raise

        if intake_session_id and self._intake_repository is not None:
            current = self._intake_repository.get(intake_session_id)
            if current is not None:
                self._intake_repository.save(current, replace(
                    current,
                    a2a_run_id=sent.run_id,
                    a2a_session_id=sent.session_id or session_key,
                    a2a_run_version=0,
                    update_time_ms=max(
                        int(time.time() * 1000), current.update_time_ms + 1,
                    ),
                ))
        return await self._wait_a2a_run(
            sent.run_id, token=token, timeout=self._a2a_timeout,
        )

    def _require_a2a_token(self, proxy_bot_id: str) -> str:
        if self._bot_tokens is None:
            raise TaskStateError("TASK_A2A_PROXY_UNAVAILABLE: token provider is missing")
        token = self._bot_tokens.get_token(proxy_bot_id)
        if not token:
            raise TaskStateError("TASK_A2A_PROXY_UNAVAILABLE: proxy Bot token is missing")
        return token

    async def _wait_a2a_run(
        self,
        run_id: str,
        *,
        token: str,
        timeout: float,
        since_version: int = 0,
    ) -> dict[str, Any]:
        if self._bcs is None:
            raise TaskStateError("TASK_A2A_PROXY_UNAVAILABLE: BCS A2A is not configured")
        deadline = time.monotonic() + timeout
        version = max(0, int(since_version))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TaskA2AStatusUnknownError(run_id)
            status = await self._bcs.get_chat_run(
                run_id,
                since_version=version,
                wait_ms=min(20_000, max(1, int(remaining * 1000))),
                caller_bearer=token,
            )
            version = int(status.get("version") or version)
            state = str(status.get("state") or "").lower()
            if state == "completed":
                response = status.get("response") or {}
                content = response.get("content") if isinstance(response, dict) else response
                return {"status": "COMPLETED", "result": {"content": content}}
            if state in {"failed", "cancelled"}:
                raise TaskStateError(
                    str(status.get("error_message") or f"A2A intake run {state}")
                )
