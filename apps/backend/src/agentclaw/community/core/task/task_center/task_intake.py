"""Clarification state machine; it never creates a formal task."""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from typing import Protocol

from agentclaw.community.core.task.domain.errors import (
    TaskA2AStatusUnknownError, TaskNotFoundError, TaskStateError,
)
from agentclaw.community.core.task.domain.intake import (
    IntakeTarget, TaskExecutionMode, TaskIntakeAction, TaskIntakeCommand,
    TaskIntakeContext, TaskIntakeResult, TaskIntakeStatus, missing_intake_fields,
)
from agentclaw.community.core.task.domain.models import AcceptanceCriteria, TaskSpec
from agentclaw.community.core.task.domain.requests import TaskInfoRequest
from agentclaw.community.core.task.domain.intake import IntakeTargetType
from agentclaw.community.core.task.repository.task_intake_repository import (
    TaskIntakeRepositoryProtocol,
)


class TaskIntakeSkill(Protocol):
    async def recognize(
        self, message: str, target: IntakeTarget, owner_user_id: str,
        *, a2a_proxy_bot_id: str | None = None,
        intake_session_id: str | None = None,
    ) -> tuple[TaskSpec, list[str]]: ...
    async def resume_pending(
        self, result: TaskIntakeResult,
    ) -> tuple[TaskSpec, list[str]] | None: ...
    async def clarify(
        self, existing_spec: TaskSpec | None, message: str, target: IntakeTarget,
        owner_user_id: str, *, a2a_proxy_bot_id: str | None = None,
        intake_session_id: str | None = None,
    ) -> tuple[TaskSpec, list[str]]: ...


class IntakeTargetResolver(Protocol):
    async def resolve(
        self, requested: IntakeTarget | None, message: str, context: TaskIntakeContext,
        *, allow_external: bool = False,
    ) -> IntakeTarget: ...

    def resolve_a2a_proxy(
        self, requested_ref: str, context: TaskIntakeContext,
    ) -> str: ...


_QUESTIONS = {
    "objective": "这项任务最终要达到什么目标？",
    "deliverables": "需要交付哪些具体产物？",
    "acceptances": "完成任务的验收标准是什么？",
    "constraints": "有哪些时间、范围或操作约束？如果没有，请明确说明没有。",
}


def _questions(missing: list[str], proposed: list[str]) -> list[str]:
    if not missing:
        return []
    return [str(item).strip() for item in proposed if str(item).strip()] or [
        _QUESTIONS[item] for item in missing
    ]


def _stable_acceptances(previous: TaskSpec | None, spec: TaskSpec) -> TaskSpec:
    prior = previous.goal.acceptances if previous else []
    used: set[str] = set()
    result: list[AcceptanceCriteria] = []
    for index, item in enumerate(spec.goal.acceptances):
        old_id = prior[index].id if index < len(prior) else None
        candidate = old_id or item.id.strip() or f"ac{index + 1}"
        if candidate in used:
            candidate = f"ac{index + 1}"
        used.add(candidate)
        result.append(AcceptanceCriteria(id=candidate, description=item.description))
    return replace(spec, goal=replace(spec.goal, acceptances=result))


def _validate(command: TaskIntakeCommand, context: TaskIntakeContext) -> None:
    if not context.owner_user_id.strip():
        raise TaskStateError("authenticated owner_user_id is required")
    action = command.action
    if action == TaskIntakeAction.START:
        if context.session_id is not None or not (command.message or "").strip():
            raise TaskStateError("START requires message and no session_id")
        return
    if not context.session_id:
        raise TaskStateError(f"{action.value} requires session_id")
    if command.requested_target is not None:
        raise TaskStateError("target cannot change within an intake session")
    if command.requested_a2a_proxy_bot_ref is not None:
        raise TaskStateError("A2A proxy cannot change within an intake session")
    if command.requested_mode is not None:
        raise TaskStateError("execution mode cannot change within an intake session")
    if action == TaskIntakeAction.CONTINUE:
        if not (command.message or "").strip():
            raise TaskStateError("CONTINUE requires message")
    elif command.message is not None:
        raise TaskStateError(f"{action.value} does not accept message")


def build_task_info_request(result: TaskIntakeResult) -> TaskInfoRequest:
    """Map the frozen confirmed result, without re-running recognition."""
    if result.status != TaskIntakeStatus.CONFIRMED or result.task_spec is None:
        raise TaskStateError("intake session is not confirmed")
    if result.target is None or missing_intake_fields(result.task_spec):
        raise TaskStateError("confirmed intake has incomplete task specification")
    if result.target.target_type != IntakeTargetType.BOT:
        raise TaskStateError("Group intake manager resolution is not configured")
    return TaskInfoRequest(
        task_spec=result.task_spec,
        source_type=result.source_type,
        owner_user_id=result.owner_user_id,
        owner_bot_id=result.target.target_id,
        execution_config={
            "orchestration_mode": result.execution_mode.value,
            "session_id": result.session_id,
            "intake_session_id": result.session_id,
            "initial_target": {
                "target_type": result.target.target_type.value,
                "target_id": result.target.target_id,
            },
        },
    )


class TaskIntakeHandler:
    def __init__(
        self,
        repository: TaskIntakeRepositoryProtocol,
        skill: TaskIntakeSkill,
        target_resolver: IntakeTargetResolver,
    ) -> None:
        self._repository = repository
        self._skill = skill
        self._target_resolver = target_resolver

    def get_confirmed(self, session_id: str, owner_user_id: str) -> TaskIntakeResult:
        result = self._repository.get(session_id)
        if result is None or result.owner_user_id != owner_user_id:
            raise TaskNotFoundError("intake session not found")
        if result.status != TaskIntakeStatus.CONFIRMED:
            raise TaskStateError("intake session is not confirmed")
        return result

    async def intake(
        self, command: TaskIntakeCommand, context: TaskIntakeContext,
    ) -> TaskIntakeResult:
        _validate(command, context)
        if command.action == TaskIntakeAction.START:
            proxy_bot_id = None
            if command.requested_a2a_proxy_bot_ref:
                proxy_bot_id = self._target_resolver.resolve_a2a_proxy(
                    command.requested_a2a_proxy_bot_ref, context,
                )
            target = await self._target_resolver.resolve(
                command.requested_target, command.message or "", context,
                allow_external=proxy_bot_id is not None,
            )
            now = int(time.time() * 1000)
            initial = self._repository.create(TaskIntakeResult(
                session_id=f"tis-{uuid.uuid4().hex}",
                owner_user_id=context.owner_user_id,
                source_type=context.source_type,
                status=TaskIntakeStatus.CLARIFYING,
                target=target,
                execution_mode=command.requested_mode or TaskExecutionMode.RELAY,
                a2a_proxy_bot_id=proxy_bot_id,
                task_spec=None,
                missing_fields=missing_intake_fields(None),
                questions=_questions(missing_intake_fields(None), []),
                create_time_ms=now,
                update_time_ms=now,
            ))
            try:
                spec, proposed = await self._skill.recognize(
                    command.message or "", target, context.owner_user_id,
                    a2a_proxy_bot_id=proxy_bot_id,
                    intake_session_id=initial.session_id,
                )
            except TaskA2AStatusUnknownError:
                return self._repository.get(initial.session_id) or initial
            spec = _stable_acceptances(None, spec)
            missing = missing_intake_fields(spec)
            latest = self._repository.get(initial.session_id) or initial
            return self._repository.save(latest, replace(
                latest,
                a2a_run_id=None,
                a2a_session_id=None,
                a2a_run_version=0,
                task_spec=spec,
                missing_fields=missing,
                questions=_questions(missing, proposed),
                update_time_ms=max(int(time.time() * 1000), now + 1),
            ))

        previous = self._repository.get(context.session_id or "")
        if previous is None or previous.owner_user_id != context.owner_user_id:
            raise TaskNotFoundError("intake session not found")
        if command.action == TaskIntakeAction.RESUME:
            if previous.a2a_run_id:
                try:
                    reconciled = await self._skill.resume_pending(previous)
                except TaskA2AStatusUnknownError:
                    return self._repository.get(previous.session_id) or previous
                if reconciled is not None:
                    spec, proposed = reconciled
                    spec = _stable_acceptances(previous.task_spec, spec)
                    missing = missing_intake_fields(spec)
                    latest = self._repository.get(previous.session_id) or previous
                    return self._repository.save(latest, replace(
                        latest,
                        a2a_run_id=None,
                        a2a_session_id=None,
                        a2a_run_version=0,
                        task_spec=spec,
                        missing_fields=missing,
                        questions=_questions(missing, proposed),
                        update_time_ms=max(
                            int(time.time() * 1000), latest.update_time_ms + 1,
                        ),
                    ))
            return previous
        if command.action == TaskIntakeAction.CONFIRM and previous.status == TaskIntakeStatus.CONFIRMED:
            return previous
        if command.action == TaskIntakeAction.CANCEL and previous.status == TaskIntakeStatus.CANCELLED:
            return previous
        if previous.status != TaskIntakeStatus.CLARIFYING:
            raise TaskStateError("intake session is terminal")

        now = max(int(time.time() * 1000), previous.update_time_ms + 1)
        if command.action == TaskIntakeAction.CONFIRM:
            if previous.target is None or missing_intake_fields(previous.task_spec):
                raise TaskStateError("task specification is incomplete")
            updated = replace(
                previous, status=TaskIntakeStatus.CONFIRMED,
                missing_fields=[], questions=[], update_time_ms=now,
            )
        elif command.action == TaskIntakeAction.CANCEL:
            updated = replace(previous, status=TaskIntakeStatus.CANCELLED, update_time_ms=now)
        else:
            if previous.target is None:
                raise TaskStateError("intake target is missing")
            spec, proposed = await self._skill.clarify(
                previous.task_spec, command.message or "", previous.target,
                previous.owner_user_id,
                a2a_proxy_bot_id=previous.a2a_proxy_bot_id,
                intake_session_id=previous.session_id,
            )
            spec = _stable_acceptances(previous.task_spec, spec)
            missing = missing_intake_fields(spec)
            latest = self._repository.get(previous.session_id) or previous
            updated = replace(
                latest, a2a_run_id=None, a2a_session_id=None, a2a_run_version=0,
                task_spec=spec, missing_fields=missing,
                questions=_questions(missing, proposed), update_time_ms=now,
            )
            previous = latest
        return self._repository.save(previous, updated)
