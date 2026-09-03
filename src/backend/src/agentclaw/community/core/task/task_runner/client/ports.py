"""integration Port 契约(对齐 spec §7.4)。transport-agnostic Protocol;组合根选实现。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.domain.models import TaskCallbackData, TaskNode
    from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
        BcsCreateGroupRequest as BcsCreateGroupRequest,
        BcsCreateGroupResult as BcsCreateGroupResult,
    )


@dataclass(frozen=True)
class BotSendResult:
    """Result of sending a message to a bot: the run id (message handle the
    poller correlates on) and the conversation session_id (used by the workflow
    task_type path)."""

    run_id: str
    session_id: str | None = None


@runtime_checkable
class OpenApiBotPort(Protocol):
    @property
    def api_key_prefix(self) -> str:
        """secbaas allowed-bots URL 路径段(api_key 前缀);派发授权 JOIN 用同一前缀口径。"""
        ...

    async def ensure_grant(self, bot_id: str) -> None: ...
    async def send_message(
        self, *, bot_id: str, message: str, metadata: dict[str, Any]
    ) -> BotSendResult: ...
    async def get_run(self, run_id: str) -> dict[str, Any]: ...
    async def cancel_run(self, run_id: str) -> None: ...
    async def send_and_wait_async(
        self,
        *,
        bot_id: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        timeout: float = 180.0,
        poll_interval: float = 2.0,
    ) -> dict[str, Any]: ...
    async def grant(self, *, bcs_bot_id: str, cookie: str, referer: str) -> None: ...
    async def revoke(self, *, bcs_bot_id: str, cookie: str, referer: str) -> None: ...


@runtime_checkable
class BcsBotIdentityResolver(Protocol):
    """把任务领域的产品 Bot ID 解析为 BCS 使用的 ``bot_id:owner_id`` UUID。"""

    def resolve_many(self, product_bot_ids: list[str]) -> dict[str, str]: ...


@runtime_checkable
class BcsClientPort(Protocol):
    async def create_group(
        self, req: "BcsCreateGroupRequest"
    ) -> "BcsCreateGroupResult": ...
    async def create_session(
        self,
        group_id: str,
        *,
        bootstrap_prompt: str | None = None,
        idempotency_key: str | None = None,
    ) -> str: ...
    async def get_group(self, group_id: str) -> dict[str, Any]: ...
    async def get_session_messages(
        self, session_id: str, *, limit: int = 50, since_msg_id: str | None = None
    ) -> list[Any]: ...
    async def start_state_machine_run(
        self,
        group_id: str,
        *,
        definition_yaml: str | None,
        definition_ref: dict[str, Any] | None,
        session_id: str | None,
        input: dict[str, Any],
    ) -> str: ...
    async def get_state_machine_run(self, run_id: str) -> dict[str, Any]: ...
    async def validate_definition(self, definition_yaml: str) -> None: ...
    def task_callback_url(self) -> str:
        """Return the callback origin used by task event subscriptions."""
        ...


@runtime_checkable
class ApiKeyProvider(Protocol):
    @property
    def api_key(self) -> str: ...
    @property
    def api_key_prefix(self) -> str: ...
    @property
    def base_url(self) -> str: ...
    @property
    def cookie(self) -> str: ...
    @property
    def referer(self) -> str: ...


@runtime_checkable
class TaskContextBuilder(Protocol):
    def build(self, task_id: str, node_id: str) -> dict[str, Any]: ...


@runtime_checkable
class PromptFormatter(Protocol):
    def format_execute(self, context: dict[str, Any], node: "TaskNode") -> str: ...
    def format_verify(self, context: dict[str, Any], node: "TaskNode") -> str: ...


@runtime_checkable
class ResultSink(Protocol):
    async def report_result(self, data: "TaskCallbackData") -> None: ...
