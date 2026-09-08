"""Avernet gateway-backed agent — stateful bot lifecycle (create → stream → delete)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from typing import Any

from ..._logger import get_logger
from ...spi._agent import Agent, AgentContext, AgentSpec, NodeResult

__all__ = ["AvernetAgent", "AvernetClient"]

logger = get_logger("executor")


_TERMINAL_FAILURE_STATES: frozenset[str] = frozenset(
    {
        "AUTHORIZATION_REJECTED",
        "AUTHORIZATION_EXPIRED",
        "CREATE_FAILED",
        "APPLY_FAILED",
    }
)


class AvernetClient:
    """Stdlib-only HTTP client for the Avernet gateway bot lifecycle."""

    def __init__(
        self,
        gateway_base_url: str,
        principal_token: str,
        user_id: str,
        *,
        create_bot_path: str = "/openapi/v1/bots/with-manifest",
        chat_stream_path: str = "/openapi/v1/chat/stream",
        delete_bot_path: str = "/openapi/v1/bots/{bot_id}",
        status_path: str = "/openapi/v1/bots/{bot_id}/with-manifest/status",
        http_timeout: float = 60.0,
        http_retries: int = 2,
    ) -> None:
        self._gateway_base_url = gateway_base_url.rstrip("/")
        self._principal_token = principal_token
        self._user_id = user_id
        self._create_bot_path = create_bot_path
        self._chat_stream_path = chat_stream_path
        self._delete_bot_path = delete_bot_path
        self._status_path = status_path
        self._http_timeout = http_timeout
        self._http_retries = http_retries
        missing: list[str] = [
            field_name
            for field_name, field_value in (
                ("gateway_base_url", self._gateway_base_url),
                ("principal_token", self._principal_token),
                ("user_id", self._user_id),
            )
            if not field_value
        ]
        if missing:
            raise ValueError(f"AvernetClient requires: {', '.join(missing)}")

    def create_bot_with_manifest(self, manifest_yaml: str) -> str:
        url = (
            f"{self._gateway_base_url}{self._create_bot_path}"
            f"?user_id={urllib.parse.quote(self._user_id)}"
        )
        body = json.dumps({"manifest": manifest_yaml}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._principal_token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._http_timeout) as response:
                payload: Any = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"create bot failed: HTTP {exc.code} {exc.reason}: {message}"
            ) from exc
        bot_id: object = payload["data"]["bot_id"]
        if not isinstance(bot_id, str):
            raise RuntimeError(
                f"create bot failed: bot_id is not a string (got {type(bot_id).__name__})"
            )
        return bot_id

    def poll_status(self, bot_id: str, timeout: float, interval: float) -> str:
        deadline = time.monotonic() + timeout
        last_state = "UNKNOWN"
        url = (
            f"{self._gateway_base_url}{self._status_path.format(bot_id=bot_id)}"
            f"?user_id={urllib.parse.quote(self._user_id)}"
        )
        while True:
            request = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self._principal_token}"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=self._http_timeout) as response:
                    payload: Any = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                message = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"poll status failed: HTTP {exc.code} {exc.reason}: {message}"
                ) from exc
            state_raw: object = payload["data"]["state"]
            last_state = state_raw if isinstance(state_raw, str) else str(state_raw)
            if last_state == "READY":
                return last_state
            if last_state in _TERMINAL_FAILURE_STATES:
                raise RuntimeError(f"bot {bot_id} creation failed: state={last_state}")
            if time.monotonic() >= deadline:
                break
            time.sleep(interval)
        raise RuntimeError(f"bot {bot_id} not READY within {timeout}s (last_state={last_state})")

    def stream_chat(self, bot_id: str, message: str) -> Iterator[str]:
        url = (
            f"{self._gateway_base_url}{self._chat_stream_path}"
            f"?user_id={urllib.parse.quote(self._user_id)}"
        )
        body = json.dumps({"bot_id": bot_id, "message": message}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._principal_token}",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._http_timeout) as response:
                for line in response:
                    text = line.decode("utf-8").strip()
                    if not text.startswith("data:"):
                        continue
                    payload_text = text[len("data:") :].strip()
                    if payload_text == "[DONE]":
                        break
                    payload: Any = json.loads(payload_text)
                    content_raw: object = None
                    try:
                        content_raw = payload["choices"][0]["delta"]["content"]
                    except (KeyError, IndexError, TypeError):
                        content_raw = payload.get("content")
                    if isinstance(content_raw, str) and content_raw:
                        yield content_raw
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"stream chat failed: HTTP {exc.code} {exc.reason}: {message}"
            ) from exc

    def delete_bot(self, bot_id: str) -> None:
        url = (
            f"{self._gateway_base_url}{self._delete_bot_path.format(bot_id=bot_id)}"
            f"?user_id={urllib.parse.quote(self._user_id)}"
        )
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self._principal_token}"},
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._http_timeout) as response:
                response.read()
                return
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                logger.warning("bot %s already gone (404)", bot_id)
                return
            message = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"delete bot failed: HTTP {exc.code} {exc.reason}: {message}"
            ) from exc


class _SafeFormatDict(dict[str, str]):
    """``dict`` that returns ``{key}`` for missing keys, for tolerant ``format_map``."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


class AvernetAgent(Agent):
    """Stateful agent backed by an Avernet gateway bot (one bot per instance)."""

    _bot_id: str | None

    def __init__(
        self,
        spec: AgentSpec,
        client: AvernetClient,
        manifest_template: str,
        poll_timeout: float = 300.0,
        poll_interval: float = 1.0,
    ) -> None:
        self._spec = spec
        self._client = client
        self._manifest_template = manifest_template
        self._poll_timeout = poll_timeout
        self._poll_interval = poll_interval
        self.name = spec.name
        self.instance_id = f"{spec.name}-{uuid.uuid4().hex[:8]}"
        self._bot_id = None

    def setup(self) -> None:
        manifest = self._manifest_template.format_map(
            _SafeFormatDict(
                role=self._spec.role,
                instructions=self._spec.instructions,
                goal="",
            )
        )
        bot_id = self._client.create_bot_with_manifest(manifest)
        state = self._client.poll_status(bot_id, self._poll_timeout, self._poll_interval)
        if state == "READY":
            self._bot_id = bot_id

    def execute(self, ctx: AgentContext) -> NodeResult:
        if self._bot_id is None:
            raise RuntimeError(f"agent {self.instance_id} execute called without a READY bot")
        bot_id = self._bot_id
        full_prompt = self._manifest_template.format_map(
            _SafeFormatDict(
                role=self._spec.role,
                instructions=self._spec.instructions,
                goal=ctx.goal,
            )
        )
        ctx.report_progress(0.0)
        parts: list[str] = []
        first_chunk = True
        for chunk in self._client.stream_chat(bot_id, full_prompt):
            if first_chunk:
                ctx.report_progress(0.5)
                first_chunk = False
            parts.append(chunk)
        ctx.report_progress(1.0)
        raw = "".join(parts)
        output: object
        try:
            output = json.loads(raw)
        except json.JSONDecodeError:
            output = raw
        return NodeResult(node_id=ctx.node_id, output=output)

    def teardown(self) -> None:
        if self._bot_id is not None:
            try:
                self._client.delete_bot(self._bot_id)
            except RuntimeError as exc:
                logger.warning("agent %s teardown failed: %s", self.instance_id, exc)
            finally:
                self._bot_id = None

    def halt(self) -> None:
        if self._bot_id is not None:
            try:
                self._client.delete_bot(self._bot_id)
            except RuntimeError as exc:
                logger.warning("agent %s halt failed: %s", self.instance_id, exc)
            finally:
                self._bot_id = None
