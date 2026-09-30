from __future__ import annotations

import email.utils
import hashlib
import http.client
import json
import random
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .. import logger as diag_logger
from ..constants import DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS


DEFAULT_LLM_HTTP_RETRIES = 4
DEFAULT_LLM_HTTP_MAX_ATTEMPTS = 1 + DEFAULT_LLM_HTTP_RETRIES
DEFAULT_LLM_RETRY_BASE_SECONDS = 1.0
DEFAULT_LLM_RETRY_MAX_SECONDS = 30.0


class LlmHttpError(RuntimeError):
    """Non-success response returned by the OpenAI-compatible endpoint."""

    def __init__(
        self,
        status_code: int,
        response_body: str,
        response_headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = int(status_code)
        self.response_body = str(response_body or "")
        self.response_headers = dict(response_headers or {})
        message = _provider_error_message(self.response_body)
        super().__init__(f"LLM HTTP {self.status_code}: {message}")


class LlmTransportError(RuntimeError):
    """The endpoint could not be reached or the response stream was interrupted."""


class LlmResponseError(RuntimeError):
    """The endpoint response does not satisfy the expected streaming contract."""


def chat_json(
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    user: str,
    timeout: int = DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS,
    call_name: str = "",
) -> dict[str, Any]:
    """Call an OpenAI-compatible streaming Chat Completions endpoint.

    The implementation deliberately uses only Python's standard library so the
    Diagnose skill can run in a clean online OpenClaw environment. Transport
    retries are owned here and are never multiplied by an outer business retry.
    """

    api_key_had_bearer_prefix = str(api_key or "").strip().lower().startswith("bearer ")
    api_key = _normalize_api_key_for_auth(api_key)
    validate_chat_runtime(api_key, base_url, model)
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    endpoint = _chat_completions_url(base_url)
    payload = _chat_completion_payload(model, system, user)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request_id = uuid.uuid4().hex[:12]
    prompt_digest = _sha256_short(f"{system}\n---\n{user}")
    phase = _phase_from_call_name(call_name)
    session_id = _session_id_from_call_name(call_name)

    diag_logger.info(
        "llm streaming request prepared",
        request_id=request_id,
        call_name=call_name or "unspecified",
        phase=phase,
        session_id=session_id,
        endpoint=_safe_endpoint_for_log(endpoint),
        model=model,
        transport="python_stdlib_urllib_sse",
        retry_owner="diagnose_http_client",
        retries=DEFAULT_LLM_HTTP_RETRIES,
        max_attempts=DEFAULT_LLM_HTTP_MAX_ATTEMPTS,
        message_count=len(payload.get("messages") or []),
        user_content_shape=_user_content_shape(payload),
        stream=payload.get("stream"),
        system_chars=len(str(system or "")),
        user_chars=len(str(user or "")),
        payload_bytes=len(body),
        prompt_sha256_12=prompt_digest,
        api_key_had_bearer_prefix=api_key_had_bearer_prefix,
        system_preview=_text_preview(system, limit=1200),
        user_content_preview=_text_preview(user, limit=4000),
        payload_preview=_json_preview(payload, limit=5000),
        timeout_seconds=timeout,
    )

    started_at = time.monotonic()
    data = _post_json_stream(
        api_key,
        endpoint,
        payload,
        timeout=float(timeout),
        request_id=request_id,
        call_name=call_name,
    )
    diag_logger.info(
        "llm streaming response assembled",
        request_id=request_id,
        call_name=call_name or "unspecified",
        phase=phase,
        session_id=session_id,
        endpoint=_safe_endpoint_for_log(endpoint),
        model=model,
        response_keys=sorted(data.keys()),
        choice_count=(
            len(data.get("choices") or [])
            if isinstance(data.get("choices"), list)
            else "unknown"
        ),
        usage=_safe_usage_summary(data.get("usage")),
        stream_chunks=data.get("_stream_chunks", 0),
        finish_reason=_first_choice_finish_reason(data),
    )

    try:
        choices = data["choices"]
        message = choices[0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmResponseError(
            f"LLM response is missing choices[0].message: {_json_preview(data, 1000)}"
        ) from exc

    content, content_field = _parseable_assistant_content(message)
    diag_logger.info(
        "llm assistant content selected",
        request_id=request_id,
        call_name=call_name or "unspecified",
        phase=phase,
        session_id=session_id,
        content_field=content_field,
        content_type=type(content).__name__,
        content_chars=len(str(content or "")),
        candidate_fields=_assistant_candidate_fields(message),
        content_preview=_text_preview(content, limit=3000),
    )
    try:
        parsed = _loads_json_object(content)
    except Exception as exc:
        raise LlmResponseError(
            f"LLM assistant output is not a JSON object; field={content_field}; "
            f"preview={_text_preview(content, 1200)}"
        ) from exc

    diag_logger.info(
        "llm streaming request parsed",
        request_id=request_id,
        call_name=call_name or "unspecified",
        phase=phase,
        session_id=session_id,
        endpoint=_safe_endpoint_for_log(endpoint),
        model=model,
        elapsed_seconds=f"{time.monotonic() - started_at:.2f}",
        finish_reason=_first_choice_finish_reason(data),
        content_field=content_field,
        parsed_keys=sorted(parsed.keys()),
    )
    return parsed


def _phase_from_call_name(call_name: str) -> str:
    name = str(call_name or "")
    if name.startswith("preference_parse"):
        return "intent_parse"
    if name.startswith("relevance_filter"):
        return "relevance_filter"
    if name.startswith("dedupe"):
        return "dedupe"
    if name.startswith("query_rewriter"):
        return "eval_query_rewrite"
    if name.startswith("direct_native_session"):
        return "native_session_analysis"
    if name.startswith("request_match"):
        return "request_match"
    if name.startswith("task_split"):
        return "task_split"
    if name.startswith("task_"):
        return name.rsplit("_", 1)[-1]
    return name.split(":", 1)[0] or "unspecified"


def _session_id_from_call_name(call_name: str) -> str:
    name = str(call_name or "")
    if ":" in name:
        return name.split(":", 1)[1]
    return ""


def _normalize_api_key_for_auth(api_key: str) -> str:
    token = str(api_key or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def validate_chat_runtime(api_key: str, base_url: str, model: str) -> None:
    if not api_key:
        raise ValueError("api key is required")
    if not base_url:
        raise ValueError("base_url is required")
    if not model:
        raise ValueError("model is required")
    _validate_header_value("api_key", api_key)
    if _looks_like_unexpanded_shell_variable(api_key):
        raise ValueError(
            "Invalid api_key: it looks like an unexpanded shell variable. "
            "Pass the key via shell expansion, e.g. --api-key \"$OPENAI_API_KEY\", "
            "from a shell where the variable is exported."
        )
    parsed = urllib.parse.urlsplit(_chat_completions_url(base_url))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url must be an absolute http(s) URL")


def _validate_header_value(name: str, value: str) -> None:
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        hint = ""
        if "…" in value or "..." in value:
            hint = " The value looks redacted/truncated; pass the complete raw API key."
        raise ValueError(
            f"Invalid {name}: contains characters that cannot be sent in an HTTP header.{hint}"
        ) from exc


def _looks_like_unexpanded_shell_variable(value: str) -> bool:
    token = str(value or "").strip()
    return bool(re.fullmatch(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?", token))


def _chat_completions_url(base_url: str) -> str:
    url = str(base_url or "").strip().rstrip("/")
    if not url:
        return ""
    return url if url.endswith("/chat/completions") else url + "/chat/completions"


def _chat_completion_payload(model: str, system: str, user: str) -> dict[str, Any]:
    """Build the exact streaming request shape verified against AntChat."""

    messages: list[dict[str, str]] = []
    if str(system or "").strip():
        messages.append({"role": "system", "content": str(system)})
    messages.append({"role": "user", "content": str(user or "")})
    return {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "stream": True,
    }


def _build_url_opener() -> urllib.request.OpenerDirector:
    """Use urllib's environment-aware proxy and HTTPS configuration."""

    return urllib.request.build_opener()


def _post_json_stream(
    api_key: str,
    endpoint: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    request_id: str = "",
    call_name: str = "",
    opener: urllib.request.OpenerDirector | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    random_fn: Callable[[], float] = random.random,
) -> dict[str, Any]:
    """POST once logically, retrying only retryable HTTP/transport failures."""

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    client = opener or _build_url_opener()
    safe_endpoint = _safe_endpoint_for_log(endpoint)
    last_error: Exception | None = None

    for attempt in range(1, DEFAULT_LLM_HTTP_MAX_ATTEMPTS + 1):
        attempt_started = time.monotonic()
        request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "text/event-stream, application/json",
                "X-Client-Request-Id": request_id or uuid.uuid4().hex,
            },
        )
        diag_logger.info(
            "llm streaming attempt start",
            request_id=request_id,
            call_name=call_name or "unspecified",
            endpoint=safe_endpoint,
            attempt=f"{attempt}/{DEFAULT_LLM_HTTP_MAX_ATTEMPTS}",
            timeout_seconds=timeout,
        )
        try:
            # The endpoint was validated as an absolute HTTP(S) URL above.
            with client.open(request, timeout=timeout) as response:  # noqa: S310
                headers = _safe_response_headers(list(response.headers.items()))
                data = _read_streaming_response(response)
                diag_logger.info(
                    "llm streaming attempt succeeded",
                    request_id=request_id,
                    call_name=call_name or "unspecified",
                    endpoint=safe_endpoint,
                    attempt=f"{attempt}/{DEFAULT_LLM_HTTP_MAX_ATTEMPTS}",
                    http_status=getattr(response, "status", 200),
                    provider_request_id=_provider_request_id(headers),
                    response_headers=headers,
                    stream_chunks=data.get("_stream_chunks", 0),
                    elapsed_seconds=f"{time.monotonic() - attempt_started:.2f}",
                )
                return data
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            headers = _safe_response_headers(list(exc.headers.items())) if exc.headers else {}
            error = LlmHttpError(exc.code, raw, headers)
            last_error = error
            retryable = _is_retryable_http_status(exc.code)
            diag_logger.warning(
                "llm streaming http error",
                request_id=request_id,
                call_name=call_name or "unspecified",
                endpoint=safe_endpoint,
                attempt=f"{attempt}/{DEFAULT_LLM_HTTP_MAX_ATTEMPTS}",
                http_status=exc.code,
                retryable=retryable,
                provider_request_id=_provider_request_id(headers),
                response_headers=headers,
                response_preview=_text_preview(raw, 2000),
                elapsed_seconds=f"{time.monotonic() - attempt_started:.2f}",
            )
            if not retryable or attempt >= DEFAULT_LLM_HTTP_MAX_ATTEMPTS:
                raise error from exc
            delay = _retry_delay_seconds(headers, attempt, random_fn)
        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            ssl.SSLError,
            ConnectionError,
            http.client.HTTPException,
            OSError,
        ) as exc:
            cause = getattr(exc, "reason", None) or exc.__cause__ or exc.__context__
            error = LlmTransportError(
                f"LLM stream transport failed: {type(exc).__name__}: {exc}"
            )
            last_error = error
            diag_logger.warning(
                "llm streaming transport error",
                request_id=request_id,
                call_name=call_name or "unspecified",
                endpoint=safe_endpoint,
                attempt=f"{attempt}/{DEFAULT_LLM_HTTP_MAX_ATTEMPTS}",
                error=f"{type(exc).__name__}: {exc}",
                error_cause=(
                    f"{type(cause).__name__}: {cause}"
                    if cause is not None
                    else ""
                ),
                elapsed_seconds=f"{time.monotonic() - attempt_started:.2f}",
            )
            if attempt >= DEFAULT_LLM_HTTP_MAX_ATTEMPTS:
                raise error from exc
            delay = _retry_delay_seconds({}, attempt, random_fn)
        except LlmResponseError:
            raise

        diag_logger.info(
            "llm streaming retry scheduled",
            request_id=request_id,
            call_name=call_name or "unspecified",
            endpoint=safe_endpoint,
            completed_attempt=attempt,
            next_attempt=attempt + 1,
            wait_seconds=f"{delay:.2f}",
        )
        sleep_fn(delay)

    assert last_error is not None
    raise last_error


def _read_streaming_response(response: Any) -> dict[str, Any]:
    content_type = str(response.headers.get("Content-Type", "")).lower()
    if "application/json" in content_type and "event-stream" not in content_type:
        raw = response.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LlmResponseError(
                f"LLM returned invalid JSON: {_text_preview(raw, 1200)}"
            ) from exc
        if not isinstance(data, dict):
            raise LlmResponseError("LLM JSON response must be an object")
        data.setdefault("_stream_chunks", 0)
        return data

    state = _StreamAssembly()
    event_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            _consume_sse_event(event_lines, state)
            event_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            event_lines.append(line[5:].lstrip())
    _consume_sse_event(event_lines, state)
    return state.to_completion()


class _StreamAssembly:
    def __init__(self) -> None:
        self.content: list[str] = []
        self.reasoning_content: list[str] = []
        self.text: list[str] = []
        self.output_text: list[str] = []
        self.finish_reason = ""
        self.usage: dict[str, Any] | None = None
        self.metadata: dict[str, Any] = {}
        self.chunk_count = 0
        self.done = False

    def consume(self, chunk: dict[str, Any]) -> None:
        if isinstance(chunk.get("error"), dict):
            raise LlmResponseError(
                f"LLM stream error: {_json_preview(chunk['error'], 1200)}"
            )
        self.chunk_count += 1
        for key in ("id", "object", "created", "model", "system_fingerprint"):
            if key in chunk:
                self.metadata[key] = chunk[key]
        if isinstance(chunk.get("usage"), dict):
            self.usage = chunk["usage"]
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        choice = choices[0]
        if not isinstance(choice, dict):
            return
        if choice.get("finish_reason") is not None:
            self.finish_reason = str(choice.get("finish_reason") or "")
        fragment = choice.get("delta")
        if not isinstance(fragment, dict):
            fragment = choice.get("message")
        if not isinstance(fragment, dict):
            return
        for key, target in (
            ("content", self.content),
            ("reasoning_content", self.reasoning_content),
            ("reasoningContent", self.reasoning_content),
            ("text", self.text),
            ("output_text", self.output_text),
        ):
            value = _stream_text(fragment.get(key))
            if value:
                target.append(value)

    def to_completion(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self.content)}
        if self.reasoning_content:
            message["reasoning_content"] = "".join(self.reasoning_content)
        if self.text:
            message["text"] = "".join(self.text)
        if self.output_text:
            message["output_text"] = "".join(self.output_text)
        candidate_fields = ("content", "reasoning_content", "text", "output_text")
        if not any(_has_text(message.get(key)) for key in candidate_fields):
            raise LlmResponseError(
                f"LLM stream ended without assistant text; chunks={self.chunk_count}; done={self.done}"
            )
        result = dict(self.metadata)
        result["choices"] = [
            {
                "index": 0,
                "message": message,
                "finish_reason": self.finish_reason,
            }
        ]
        if self.usage is not None:
            result["usage"] = self.usage
        result["_stream_chunks"] = self.chunk_count
        result["_stream_done"] = self.done
        return result


def _consume_sse_event(lines: list[str], state: _StreamAssembly) -> None:
    if not lines or state.done:
        return
    payload = "\n".join(lines).strip()
    if not payload:
        return
    if payload == "[DONE]":
        state.done = True
        return
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LlmResponseError(f"Invalid SSE JSON event: {_text_preview(payload, 1200)}") from exc
    if not isinstance(chunk, dict):
        raise LlmResponseError("SSE data event must contain a JSON object")
    state.consume(chunk)


def _stream_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _is_retryable_http_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or 500 <= status_code <= 599


def _retry_delay_seconds(
    headers: dict[str, str],
    attempt: int,
    random_fn: Callable[[], float],
) -> float:
    retry_after = _parse_retry_after(headers.get("retry-after", ""))
    if retry_after is not None:
        return min(DEFAULT_LLM_RETRY_MAX_SECONDS, max(0.0, retry_after))
    exponential = min(
        DEFAULT_LLM_RETRY_MAX_SECONDS,
        DEFAULT_LLM_RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)),
    )
    return min(DEFAULT_LLM_RETRY_MAX_SECONDS, exponential + random_fn() * 0.25)


def _parse_retry_after(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _provider_error_message(response_body: str) -> str:
    text = str(response_body or "").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _text_preview(text or "empty response body", 1000)
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return _text_preview(error["message"], 1000)
        if isinstance(error, str):
            return _text_preview(error, 1000)
        if data.get("message"):
            return _text_preview(data["message"], 1000)
    return _json_preview(data, 1000)


def _parseable_assistant_content(message: Any) -> tuple[Any, str]:
    """Return the assistant field most likely to contain the JSON answer.

    Some OpenAI-compatible gateways expose reasoning models with an empty
    ``message.content`` and place the full assistant text in provider-specific
    fields such as ``reasoning_content``.  The diagnose judge still needs the
    final JSON object, so try normal content first, then known textual fallback
    fields, accepting the first field from which a JSON object can be parsed.
    """

    if not isinstance(message, dict):
        return message, "message"

    candidates: list[tuple[str, Any]] = []
    for key in ("content", "reasoning_content", "reasoningContent", "text", "output_text"):
        value = message.get(key)
        if _has_text(value):
            candidates.append((key, value))

    if not candidates:
        return message.get("content", ""), "content"

    last_name, last_value = candidates[0]
    for name, value in candidates:
        last_name, last_value = name, value
        try:
            _loads_json_object(value)
        except Exception:
            continue
        return value, name
    return last_value, last_name


def _has_text(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_text(item) for item in value)
    if isinstance(value, dict):
        return bool(value)
    return value is not None


def _first_choice_finish_reason(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    return str(first.get("finish_reason") or "") if isinstance(first, dict) else ""


def _first_choice_keys(data: dict[str, Any]) -> list[str]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return []
    return sorted(str(k) for k in choices[0].keys())


def _first_message_keys(data: dict[str, Any]) -> list[str]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return []
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return []
    return sorted(str(k) for k in message.keys())


def _assistant_candidate_fields(message: Any) -> list[str]:
    if not isinstance(message, dict):
        return []
    fields: list[str] = []
    for key in ("content", "reasoning_content", "reasoningContent", "text", "output_text"):
        value = message.get(key)
        if _has_text(value):
            fields.append(f"{key}:{type(value).__name__}:{len(str(value))}")
    return fields


def _safe_usage_summary(value: Any) -> dict[str, Any] | str:
    if not isinstance(value, dict):
        return ""
    out: dict[str, Any] = {}
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
    ):
        if key in value:
            out[key] = value.get(key)
    return out


def _safe_response_headers(headers: list[tuple[str, str]]) -> dict[str, str]:
    allowed_names = {
        "content-type",
        "content-length",
        "x-request-id",
        "x-correlation-id",
        "request-id",
        "x-ratelimit-limit-requests",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-reset-requests",
        "retry-after",
        "date",
        "server",
    }
    out: dict[str, str] = {}
    for name, value in headers or []:
        key = str(name or "").lower()
        if key not in allowed_names:
            continue
        out[key] = _text_preview(value, limit=300)
    return out


def _provider_request_id(headers: dict[str, str]) -> str:
    for key in ("x-request-id", "x-correlation-id", "request-id"):
        value = headers.get(key)
        if value:
            return value
    return ""


def _user_content_shape(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return "none"
    user_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    if not user_messages:
        return "no_user_message"
    content = user_messages[-1].get("content")
    if isinstance(content, list):
        block_types = [
            str(item.get("type") or type(item).__name__)
            for item in content
            if isinstance(item, dict)
        ]
        return f"list[{len(content)}]:{','.join(block_types)}"
    return type(content).__name__


def _sha256_short(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()[:12]


def _safe_endpoint_for_log(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url or ""))
    if not parsed.scheme or not parsed.netloc:
        return str(url or "")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _json_preview(value: Any, limit: int = 2000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = str(value)
    return _text_preview(text, limit=limit)


def _text_preview(value: Any, limit: int = 2000) -> str:
    text = str(value or "")
    if len(text) > limit:
        return text[:limit] + f"...<truncated {len(text) - limit} chars>"
    return text


def loads_json_object(content: Any) -> dict[str, Any]:
    """Parse the first valid JSON object from an LLM/subagent response.

    LLM transports sometimes wrap JSON in markdown fences or include earlier
    acknowledgement text before the final object.  Prefer an exact parse, then
    scan fenced blocks and balanced brace spans in order.
    """

    if isinstance(content, dict):
        return content
    text = str(content or "").strip()
    try:
        parsed = json.loads(_strip_json_fence(text))
    except json.JSONDecodeError as exc:
        last_error: Exception | None = exc
    else:
        if isinstance(parsed, dict):
            return parsed
        last_error = ValueError("LLM response JSON must be an object")

    parsed_candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in _json_object_candidates(text):
        candidate = _strip_json_fence(candidate.strip())
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(parsed, dict):
            parsed_candidates.append(parsed)
        else:
            last_error = ValueError("LLM response JSON must be an object")
    if parsed_candidates:
        return parsed_candidates[-1]
    raise last_error


def _strip_json_fence(text: str) -> str:
    """Remove a single markdown JSON fence wrapper when present."""

    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I).strip()
        value = re.sub(r"\s*```$", "", value).strip()
    return value


def _loads_json_object(content: Any) -> dict[str, Any]:
    """Internal alias used by chat_json retry parsing."""

    return loads_json_object(content)


def _json_object_candidates(text: str) -> list[str]:
    """Return fenced and balanced-brace JSON object candidates from text."""

    candidates: list[str] = []
    candidates.extend(
        match.group(1)
        for match in re.finditer(r"```(?:json)?\s*(.*?)\s*```", text, re.S | re.I)
    )

    brace_depth = 0
    current: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if brace_depth == 0:
            if char == "{":
                current = [char]
                brace_depth = 1
                in_string = False
                escaped = False
            continue

        current.append(char)
        if escaped:
            escaped = False
        elif char == "\\" and in_string:
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif not in_string and char == "{":
            brace_depth += 1
        elif not in_string and char == "}":
            brace_depth -= 1
            if brace_depth == 0:
                candidates.append("".join(current))
                current = []
    return candidates
