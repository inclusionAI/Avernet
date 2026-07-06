#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Interactive local Provider simulator for BCS downlink integration.

This script is intentionally standalone and uses only Python standard library
modules. It runs a Provider webhook and, by default, an interactive console for
Provider/Bot control-plane operations.
"""

from __future__ import annotations

import argparse
import json
import logging
import shlex
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


JsonObject = dict[str, Any]
LOGGER = logging.getLogger("provider_downlink_console")
LOGGER.addHandler(logging.NullHandler())


def now_ms() -> int:
    return int(time.time() * 1000)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def header_value(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is not None:
            return str(value)
    lower_name = name.lower()
    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == lower_name:
                return str(value)
    return None


def redact_token(value: str | None) -> str | None:
    if not value:
        return value
    if value.startswith("Bearer "):
        return "Bearer " + redact_token(value[len("Bearer ") :])
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def message_text(message: JsonObject | None) -> str:
    if not message:
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(str(part.get("text", "")))
            elif isinstance(part, str):
                texts.append(part)
        if texts:
            return "".join(texts)
        return compact_json(content)
    if content is not None:
        return compact_json(content)
    text = message.get("text")
    if text is not None:
        return str(text)
    return compact_json(message)


def session_storage_key(provider_bot_ref: str, session_id: str) -> str:
    return f"{provider_bot_ref}::{session_id}"


def normalize_incoming_message(request: JsonObject) -> JsonObject:
    message = request.get("message")
    if not isinstance(message, dict):
        message = {}
    timestamp = message.get("timestamp")
    if not isinstance(timestamp, int):
        timestamp = now_ms()
    return {
        "id": request.get("id") or str(uuid.uuid4()),
        "role": message.get("role", "user"),
        "content": message_text(message),
        "timestamp": timestamp,
        "historyMeta": {
            "localProviderConsole": True,
            "direction": "received",
            "method": request.get("method"),
        },
    }


def fixed_reply_message(kind: str, run_id: str, received_text: str, timestamp: int) -> JsonObject:
    return {
        "id": f"local-provider-{kind}-{run_id or uuid.uuid4()}",
        "role": "assistant",
        "content": f"收到{kind}类型消息：{received_text}",
        "timestamp": timestamp,
        "stopReason": "complete",
        "historyMeta": {
            "localProviderConsole": True,
            "direction": "sent",
            "method": f"chat.{kind}",
            "run_id": run_id,
        },
    }


class ProviderState:
    """JSON-backed local Provider state."""

    def __init__(self, path: Path, data: JsonObject | None = None) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.data: JsonObject = {
            "version": 1,
            "provider_id": "",
            "provider_admin_token": "",
            "bcs_to_provider_token": "",
            "provider": {},
            "bots": {},
            "sessions": {},
            "requests": [],
            "callbacks": [],
            "processed_request_ids": [],
            "aborted_sessions": [],
            "agentpass_tokens": {},
        }
        if data:
            self.data.update(data)
        self._ensure_shapes()

    @classmethod
    def load(cls, path: Path | str) -> "ProviderState":
        state_path = Path(path)
        if state_path.exists():
            with state_path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict):
                raise ValueError(f"state file must contain a JSON object: {state_path}")
            state = cls(state_path, loaded)
        else:
            state = cls(state_path)
            state.save()
        return state

    def _ensure_shapes(self) -> None:
        self.data.setdefault("version", 1)
        self.data.setdefault("provider_id", "")
        self.data.setdefault("provider_admin_token", "")
        self.data.setdefault("bcs_to_provider_token", "")
        self.data.setdefault("provider", {})
        self.data.setdefault("bots", {})
        self.data.setdefault("sessions", {})
        self.data.setdefault("requests", [])
        self.data.setdefault("callbacks", [])
        self.data.setdefault("processed_request_ids", [])
        self.data.setdefault("aborted_sessions", [])
        self.data.setdefault("agentpass_tokens", {})
        if not isinstance(self.data["bots"], dict):
            self.data["bots"] = {}
        if not isinstance(self.data["sessions"], dict):
            self.data["sessions"] = {}
        if not isinstance(self.data["agentpass_tokens"], dict):
            self.data["agentpass_tokens"] = {}

    @property
    def provider_id(self) -> str:
        return str(self.data.get("provider_id") or "")

    @provider_id.setter
    def provider_id(self, value: str) -> None:
        with self.lock:
            self.data["provider_id"] = value
            self.save()

    @property
    def provider_admin_token(self) -> str:
        return str(self.data.get("provider_admin_token") or "")

    @provider_admin_token.setter
    def provider_admin_token(self, value: str) -> None:
        with self.lock:
            self.data["provider_admin_token"] = value
            self.save()

    @property
    def bcs_to_provider_token(self) -> str:
        return str(self.data.get("bcs_to_provider_token") or "")

    @bcs_to_provider_token.setter
    def bcs_to_provider_token(self, value: str) -> None:
        with self.lock:
            self.data["bcs_to_provider_token"] = value
            self.save()

    @property
    def callback_results(self) -> list[JsonObject]:
        with self.lock:
            return list(self.data.get("callbacks", []))

    @property
    def provider_auth_mode(self) -> str:
        with self.lock:
            provider = self.data.get("provider")
            if not isinstance(provider, dict):
                return ""
            return str(provider.get("auth_mode") or "")

    def merge_tokens(self, data: JsonObject) -> None:
        with self.lock:
            for key in ("provider_id", "provider_admin_token", "bcs_to_provider_token"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    self.data[key] = value
            provider = data.get("provider")
            if isinstance(provider, dict):
                self.data["provider"].update(provider)
            agentpass_tokens = data.get("agentpass_tokens")
            if isinstance(agentpass_tokens, dict):
                tokens = self.data.setdefault("agentpass_tokens", {})
                for provider_bot_ref, token in agentpass_tokens.items():
                    if provider_bot_ref and isinstance(token, str) and token:
                        tokens[str(provider_bot_ref)] = token
            self.save()

    def save_provider_registration(self, response: JsonObject, request: JsonObject) -> None:
        with self.lock:
            self.merge_tokens(response)
            self.data["provider"] = {
                "name": request.get("name", ""),
                "webhook_url": request.get("webhook_url", ""),
                "auth_mode": (request.get("auth") or {}).get("mode", ""),
                "registered_at": now_ms(),
            }
            self.save()

    def upsert_bot(self, bot: JsonObject) -> None:
        provider_bot_ref = str(bot.get("provider_bot_ref") or "").strip()
        if not provider_bot_ref:
            raise ValueError("provider_bot_ref is required")
        with self.lock:
            bots = self.data.setdefault("bots", {})
            existing = bots.get(provider_bot_ref)
            if isinstance(existing, dict):
                merged = dict(existing)
                merged.update({key: value for key, value in bot.items() if value is not None})
                if "bot_runtime_token" not in bot and "bot_runtime_token" in existing:
                    merged["bot_runtime_token"] = existing["bot_runtime_token"]
                bots[provider_bot_ref] = merged
            else:
                bots[provider_bot_ref] = dict(bot)
            self.save()

    def list_bots(self) -> list[JsonObject]:
        with self.lock:
            bots = self.data.get("bots", {})
            if not isinstance(bots, dict):
                return []
            return [dict(value) for value in bots.values() if isinstance(value, dict)]

    def bot_runtime_token(self, provider_bot_ref: str) -> str | None:
        with self.lock:
            bots = self.data.get("bots", {})
            if not isinstance(bots, dict):
                return None
            bot = bots.get(provider_bot_ref)
            if not isinstance(bot, dict):
                return None
            token = bot.get("bot_runtime_token")
            return str(token) if token else None

    def agentpass_token(self, provider_bot_ref: str) -> str | None:
        with self.lock:
            tokens = self.data.get("agentpass_tokens", {})
            if not isinstance(tokens, dict):
                return None
            token = tokens.get(provider_bot_ref)
            return str(token) if token else None

    def set_agentpass_token(self, provider_bot_ref: str, token: str) -> None:
        provider_bot_ref = provider_bot_ref.strip()
        token = token.strip()
        if not provider_bot_ref:
            raise ValueError("provider_bot_ref is required")
        if not token:
            raise ValueError("agentpass token is required")
        with self.lock:
            self.data.setdefault("agentpass_tokens", {})[provider_bot_ref] = token
            self.save()

    def snapshot_agentpass_tokens(self, redacted: bool = False) -> JsonObject:
        with self.lock:
            tokens = self.data.get("agentpass_tokens", {})
            if not isinstance(tokens, dict):
                return {}
            if not redacted:
                return dict(tokens)
            return {
                str(provider_bot_ref): redact_token(str(token))
                for provider_bot_ref, token in tokens.items()
            }

    def mark_processed(self, request_id: str) -> bool:
        if not request_id:
            return False
        with self.lock:
            processed = self.data.setdefault("processed_request_ids", [])
            duplicate = request_id in processed
            if not duplicate:
                processed.append(request_id)
                self.save()
            return duplicate

    def record_request(self, headers: Any, body: JsonObject, duplicate: bool) -> None:
        with self.lock:
            self.data.setdefault("requests", []).append(
                {
                    "received_at": now_ms(),
                    "duplicate": duplicate,
                    "authorization": redact_token(header_value(headers, "Authorization")),
                    "protocol_version": header_value(headers, "X-BCN-Protocol-Version"),
                    "body": body,
                }
            )
            self.save()

    def record_callback(self, result: JsonObject) -> None:
        with self.lock:
            self.data.setdefault("callbacks", []).append(result)
            self.save()

    def add_session_message(
        self, provider_bot_ref: str, session_id: str, message: JsonObject
    ) -> None:
        with self.lock:
            key = session_storage_key(provider_bot_ref, session_id)
            self.data.setdefault("sessions", {}).setdefault(key, []).append(message)
            self.save()

    def session_messages(self, provider_bot_ref: str, session_id: str) -> list[JsonObject]:
        with self.lock:
            key = session_storage_key(provider_bot_ref, session_id)
            messages = self.data.setdefault("sessions", {}).get(key, [])
            if not isinstance(messages, list):
                return []
            return [dict(message) for message in messages if isinstance(message, dict)]

    def snapshot_sessions(self) -> JsonObject:
        with self.lock:
            return dict(self.data.get("sessions", {}))

    def snapshot_requests(self) -> list[JsonObject]:
        with self.lock:
            return list(self.data.get("requests", []))

    def snapshot_callbacks(self) -> list[JsonObject]:
        with self.lock:
            return list(self.data.get("callbacks", []))

    def record_abort(self, provider_bot_ref: str, session_id: str) -> None:
        with self.lock:
            key = session_storage_key(provider_bot_ref, session_id)
            aborted = self.data.setdefault("aborted_sessions", [])
            if key not in aborted:
                aborted.append(key)
            self.save()

    def reset_runtime_records(self) -> None:
        with self.lock:
            self.data["sessions"] = {}
            self.data["requests"] = []
            self.data["callbacks"] = []
            self.data["processed_request_ids"] = []
            self.data["aborted_sessions"] = []
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(self.path)


class ProviderRuntime:
    """Webhook behavior for the local Provider."""

    def __init__(
        self,
        state: ProviderState,
        bcs_url: str,
        strict_auth: bool = False,
        auto_callback: bool = False,
        callback_delay_ms: int = 50,
    ) -> None:
        self.state = state
        self.bcs_url = bcs_url.rstrip("/")
        self.strict_auth = strict_auth
        self.auto_callback = auto_callback
        self.callback_delay_ms = callback_delay_ms

    def handle_webhook(self, headers: Any, body: JsonObject) -> tuple[int, JsonObject]:
        LOGGER.info("webhook received method=%s id=%s", body.get("method"), body.get("id"))
        auth_error = self.validate_auth(headers)
        if auth_error is not None:
            LOGGER.warning("webhook auth failed method=%s", body.get("method"))
            return auth_error

        provider_error = self.validate_provider(body)
        if provider_error is not None:
            LOGGER.warning("webhook provider validation failed body=%s", compact_json(body))
            return provider_error

        method = body.get("method")
        request_id = str(body.get("id") or "")
        duplicate = self.state.mark_processed(request_id)
        self.state.record_request(headers, body, duplicate)

        if method == "chat.send":
            return self.handle_chat_send(body, duplicate)
        if method == "chat.inject":
            return self.handle_chat_inject(body, duplicate)
        if method == "chat.history":
            return self.handle_chat_history(body)
        if method == "chat.abort":
            return self.handle_chat_abort(body)
        return 400, {"ok": False, "error": "unsupported_method", "retryable": False}

    def validate_auth(self, headers: Any) -> tuple[int, JsonObject] | None:
        if not self.strict_auth:
            return None
        expected = self.state.bcs_to_provider_token
        if not expected:
            return 401, {
                "ok": False,
                "error": "bcs_to_provider_token_not_configured",
                "retryable": False,
            }
        authorization = header_value(headers, "Authorization")
        if authorization != f"Bearer {expected}":
            return 401, {"ok": False, "error": "invalid_token", "retryable": False}
        return None

    def validate_provider(self, body: JsonObject) -> tuple[int, JsonObject] | None:
        to_bot = body.get("to_bot")
        if not isinstance(to_bot, dict):
            return 400, {"ok": False, "error": "missing_to_bot", "retryable": False}
        request_provider_id = str(to_bot.get("provider_id") or "")
        if not self.state.provider_id and request_provider_id:
            self.state.provider_id = request_provider_id
        if request_provider_id != self.state.provider_id:
            return 403, {
                "ok": False,
                "error": "provider_id_mismatch",
                "retryable": False,
            }
        return None

    def session_ref(self, body: JsonObject) -> tuple[str, str]:
        to_bot = body.get("to_bot") or {}
        provider_bot_ref = str(to_bot.get("provider_bot_ref") or "")
        session_id = str(body.get("session_id") or body.get("session_key") or "")
        return provider_bot_ref, session_id

    def handle_chat_send(self, body: JsonObject, duplicate: bool) -> tuple[int, JsonObject]:
        provider_bot_ref, session_id = self.session_ref(body)
        run_id = str(body.get("id") or "")
        if not duplicate:
            incoming, reply = self.persist_exchange("send", provider_bot_ref, session_id, body)
            LOGGER.info(
                "chat.send stored provider_bot_ref=%s session_id=%s request=%s reply=%s",
                provider_bot_ref,
                session_id,
                incoming.get("id"),
                reply.get("id"),
            )
            self.schedule_callback(provider_bot_ref, run_id, str(reply.get("content") or ""))
        return 200, {"ok": True}

    def handle_chat_inject(self, body: JsonObject, duplicate: bool) -> tuple[int, JsonObject]:
        provider_bot_ref, session_id = self.session_ref(body)
        if not duplicate:
            incoming, reply = self.persist_exchange("inject", provider_bot_ref, session_id, body)
            LOGGER.info(
                "chat.inject stored provider_bot_ref=%s session_id=%s request=%s reply=%s",
                provider_bot_ref,
                session_id,
                incoming.get("id"),
                reply.get("id"),
            )
        return 200, {"ok": True}

    def persist_exchange(
        self, kind: str, provider_bot_ref: str, session_id: str, body: JsonObject
    ) -> tuple[JsonObject, JsonObject]:
        incoming = normalize_incoming_message(body)
        reply = fixed_reply_message(
            kind,
            str(body.get("id") or ""),
            str(incoming.get("content") or ""),
            int(incoming.get("timestamp", 0)) + 1,
        )
        self.state.add_session_message(provider_bot_ref, session_id, incoming)
        self.state.add_session_message(provider_bot_ref, session_id, reply)
        return incoming, reply

    def handle_chat_history(self, body: JsonObject) -> tuple[int, JsonObject]:
        if body.get("before") is not None and body.get("after") is not None:
            return 400, {
                "ok": False,
                "error": "before_and_after_conflict",
                "retryable": False,
            }

        provider_bot_ref, session_id = self.session_ref(body)
        limit = body.get("limit", 50)
        if not isinstance(limit, int) or limit <= 0:
            limit = 50
        limit = min(limit, 1000)
        before = body.get("before")
        after = body.get("after")

        messages = self.state.session_messages(provider_bot_ref, session_id)
        if isinstance(before, int):
            messages = [message for message in messages if message.get("timestamp", 0) < before]
        if isinstance(after, int):
            messages = [message for message in messages if message.get("timestamp", 0) > after]
        messages.sort(key=lambda message: int(message.get("timestamp", 0)), reverse=True)

        has_more = len(messages) > limit
        page = messages[:limit]
        response: JsonObject = {
            "ok": True,
            "session_id": session_id,
            "messages": page,
            "has_more": has_more,
        }
        if has_more and page:
            if isinstance(after, int):
                response["next_after"] = max(int(message["timestamp"]) for message in page)
            else:
                response["next_before"] = min(int(message["timestamp"]) for message in page)
        LOGGER.info(
            "chat.history provider_bot_ref=%s session_id=%s count=%s has_more=%s",
            provider_bot_ref,
            session_id,
            len(page),
            has_more,
        )
        return 200, response

    def handle_chat_abort(self, body: JsonObject) -> tuple[int, JsonObject]:
        provider_bot_ref, session_id = self.session_ref(body)
        self.state.record_abort(provider_bot_ref, session_id)
        LOGGER.info("chat.abort provider_bot_ref=%s session_id=%s", provider_bot_ref, session_id)
        return 200, {"ok": True}

    def schedule_callback(self, provider_bot_ref: str, run_id: str, text: str) -> None:
        if not self.auto_callback or not run_id:
            return
        delay = max(self.callback_delay_ms, 0) / 1000.0
        timer = threading.Timer(
            delay, self.post_bot_event, args=(provider_bot_ref, run_id, text)
        )
        timer.daemon = True
        timer.start()

    def post_bot_event(self, provider_bot_ref: str, run_id: str, text: str) -> None:
        extra_headers: dict[str, str] = {}
        if self.state.provider_auth_mode == "provider_admin":
            token = self.state.provider_admin_token
            auth_token_kind = "provider_admin_token"
            extra_headers["X-BCN-Provider-Bot-Ref"] = provider_bot_ref
        else:
            token = self.state.bot_runtime_token(provider_bot_ref)
            auth_token_kind = "bot_runtime_token"
            if not token:
                token = self.state.agentpass_token(provider_bot_ref)
                auth_token_kind = "agentpass_token"
        if not token:
            result = {
                "run_id": run_id,
                "provider_bot_ref": provider_bot_ref,
                "ok": False,
                "error": f"{auth_token_kind}_not_configured",
                "created_at": now_ms(),
            }
            self.state.record_callback(result)
            LOGGER.warning("callback skipped %s", compact_json(result))
            return

        url = f"{self.bcs_url}/bot/events"
        event_id = str(uuid.uuid4())
        payload = {
            "run_id": run_id,
            "seq": 1,
            "state": "final",
            "message": {
                "text": text,
            },
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-BCN-Protocol-Version": "1.0",
                "X-BCN-Timestamp": str(now_ms()),
                "X-BCN-Provider-Id": self.state.provider_id,
                "X-BCN-Event-Id": event_id,
                **extra_headers,
            },
        )
        result: JsonObject = {
            "run_id": run_id,
            "provider_bot_ref": provider_bot_ref,
            "event_id": event_id,
            "url": url,
            "auth_token_kind": auth_token_kind,
            "created_at": now_ms(),
        }
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result["status"] = response.status
                result["body"] = response.read().decode("utf-8", errors="replace")
                result["ok"] = 200 <= response.status < 300
        except urllib.error.HTTPError as error:
            result["status"] = error.code
            result["body"] = error.read().decode("utf-8", errors="replace")
            result["ok"] = False
        except Exception as error:  # pragma: no cover - depends on live BCS.
            result["ok"] = False
            result["error"] = str(error)

        self.state.record_callback(result)
        LOGGER.info("callback result %s", compact_json(result))


class BcsApiClient:
    def __init__(
        self,
        bcs_url: str,
        state: ProviderState,
        mock_user_id: str | None = None,
        cookie: str | None = None,
        user_bearer_token: str | None = None,
    ) -> None:
        self.bcs_url = bcs_url.rstrip("/")
        self.state = state
        self.mock_user_id = mock_user_id
        self.cookie = cookie
        self.user_bearer_token = user_bearer_token

    def request_json(
        self,
        method: str,
        path: str,
        body: JsonObject | None = None,
        bearer_token: str | None = None,
        include_user_identity: bool = False,
    ) -> JsonObject:
        url = f"{self.bcs_url}{path}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        elif include_user_identity and self.user_bearer_token:
            headers["Authorization"] = f"Bearer {self.user_bearer_token}"
        if include_user_identity and self.mock_user_id:
            headers["X-Mock-User-Id"] = self.mock_user_id
        if include_user_identity and self.cookie:
            headers["Cookie"] = self.cookie

        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method=method, headers=headers)
        LOGGER.info(
            "bcs request method=%s url=%s bearer=%s user_identity=%s body=%s",
            method,
            url,
            bool(bearer_token),
            include_user_identity,
            compact_json(body) if body is not None else "{}",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                text = response.read().decode("utf-8", errors="replace")
                if not text:
                    return {"ok": True, "status": response.status}
                value = json.loads(text)
                if isinstance(value, dict):
                    return value
                return {"value": value}
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8", errors="replace")
            LOGGER.warning("bcs request failed status=%s body=%s", error.code, text)
            try:
                body_value = json.loads(text) if text else {}
            except json.JSONDecodeError:
                body_value = {"body": text}
            if isinstance(body_value, dict):
                body_value.setdefault("status", error.code)
                raise RuntimeError(compact_json(body_value))
            raise RuntimeError(text or f"HTTP {error.code}")
        except Exception as error:
            LOGGER.exception("bcs request error")
            raise RuntimeError(str(error))

    def register_provider(self, name: str, webhook_url: str, auth_mode: str) -> JsonObject:
        payload = {
            "name": name,
            "webhook_url": webhook_url,
            "auth": {"mode": auth_mode},
        }
        response = self.request_json(
            "POST", "/providers", payload, include_user_identity=True
        )
        self.state.save_provider_registration(response, payload)
        return response

    def get_provider(self) -> JsonObject:
        self.require_provider()
        return self.request_json(
            "GET",
            f"/providers/{self.state.provider_id}",
            bearer_token=self.state.provider_admin_token,
        )

    def patch_provider(self, name: str | None, webhook_url: str | None) -> JsonObject:
        self.require_provider()
        payload: JsonObject = {}
        if name is not None:
            payload["name"] = name
        if webhook_url is not None:
            payload["webhook_url"] = webhook_url
        return self.request_json(
            "PATCH",
            f"/providers/{self.state.provider_id}",
            payload,
            bearer_token=self.state.provider_admin_token,
            include_user_identity=True,
        )

    def set_provider_disabled(self, disabled: bool) -> JsonObject:
        self.require_provider()
        action = "disable" if disabled else "enable"
        return self.request_json(
            "POST",
            f"/providers/{self.state.provider_id}/{action}",
            {},
            bearer_token=self.state.provider_admin_token,
            include_user_identity=True,
        )

    def register_bot(
        self,
        provider_bot_ref: str,
        name: str,
        summary: str,
        owners: list[str],
    ) -> JsonObject:
        self.require_provider()
        payload = {
            "name": name,
            "summary": summary,
            "owners": owners,
            "provider_bot_ref": provider_bot_ref,
        }
        response = self.request_json(
            "POST",
            f"/providers/{self.state.provider_id}/bots",
            payload,
            bearer_token=self.state.provider_admin_token,
        )
        self.state.upsert_bot(response)
        return response

    def list_provider_bots(self) -> JsonObject:
        self.require_provider()
        response = self.request_json(
            "GET",
            f"/providers/{self.state.provider_id}/bots",
            bearer_token=self.state.provider_admin_token,
        )
        items = response.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    self.state.upsert_bot(item)
        return response

    def require_provider(self) -> None:
        if not self.state.provider_id:
            raise RuntimeError("provider_id is not configured")
        if not self.state.provider_admin_token:
            raise RuntimeError("provider_admin_token is not configured")


class ConsoleArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            raise ValueError(message.strip())
        raise ValueError("invalid arguments")


class ProviderConsoleHandler(BaseHTTPRequestHandler):
    server: "ProviderConsoleServer"

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self.write_json(200, {"ok": True})
            return
        if path == "/requests":
            self.write_json(200, {"ok": True, "requests": self.server.state.snapshot_requests()})
            return
        if path == "/sessions":
            self.write_json(200, {"ok": True, "sessions": self.server.state.snapshot_sessions()})
            return
        if path == "/callbacks":
            self.write_json(200, {"ok": True, "callbacks": self.server.state.snapshot_callbacks()})
            return
        self.write_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/reset":
            self.server.state.reset_runtime_records()
            self.write_json(200, {"ok": True})
            return
        if path != "/webhook":
            self.write_json(404, {"ok": False, "error": "not_found"})
            return
        body = self.read_json_body()
        if body is None:
            self.write_json(400, {"ok": False, "error": "invalid_json", "retryable": False})
            return
        status, response = self.server.runtime.handle_webhook(self.headers, body)
        self.write_json(status, response)

    def read_json_body(self) -> JsonObject | None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        return value

    def write_json(self, status: int, body: JsonObject) -> None:
        data = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("http %s", fmt % args)


class ProviderConsoleServer(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        state: ProviderState,
        runtime: ProviderRuntime,
    ) -> None:
        super().__init__(address, ProviderConsoleHandler)
        self.state = state
        self.runtime = runtime


class Console:
    def __init__(
        self,
        state: ProviderState,
        client: BcsApiClient,
        webhook_url: str,
        default_owner: str | None = None,
    ) -> None:
        self.state = state
        self.client = client
        self.webhook_url = webhook_url
        self.default_owner = default_owner or "11111111"

    def loop(self) -> None:
        print("Provider console ready. Type 'help' for commands, 'quit' to stop.", flush=True)
        while True:
            try:
                line = input("provider> ")
            except EOFError:
                print("", flush=True)
                return
            except KeyboardInterrupt:
                print("", flush=True)
                return
            line = line.strip()
            if not line:
                continue
            if line in {"quit", "exit"}:
                return
            try:
                result = self.run_command(line)
                if result is not None:
                    print(result, flush=True)
            except Exception as error:
                LOGGER.exception("console command failed line=%s", line)
                print(f"ERROR: {error}", flush=True)

    def run_command(self, line: str) -> str | None:
        args = shlex.split(line)
        if not args:
            return None
        command = args[0]
        rest = args[1:]
        if command == "help":
            return self.help_text()
        if command == "status":
            return self.status_text()
        if command == "tokens":
            return self.tokens_command(rest)
        if command == "provider":
            return self.provider_command(rest)
        if command == "bot":
            return self.bot_command(rest)
        if command == "sessions":
            return self.sessions_command(rest)
        if command == "requests":
            return pretty_json({"requests": self.state.snapshot_requests()})
        if command == "callbacks":
            return pretty_json({"callbacks": self.state.snapshot_callbacks()})
        if command == "reset":
            self.state.reset_runtime_records()
            return "runtime records reset"
        return f"unknown command: {command}"

    def tokens_command(self, args: list[str]) -> str:
        if not args or args[0] == "show":
            return pretty_json(
                {
                    "provider_id": self.state.provider_id,
                    "provider_admin_token": redact_token(self.state.provider_admin_token),
                    "bcs_to_provider_token": redact_token(self.state.bcs_to_provider_token),
                    "agentpass_tokens": self.state.snapshot_agentpass_tokens(redacted=True),
                    "state_file": str(self.state.path),
                }
            )
        if args[0] == "load":
            if len(args) != 2:
                return "usage: tokens load <json-file>"
            with Path(args[1]).open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                return "token file must contain a JSON object"
            self.state.merge_tokens(data)
            return "tokens loaded"
        if args[0] == "set-agentpass":
            if len(args) < 3:
                return "usage: tokens set-agentpass <provider_bot_ref> <agentpass_token>"
            self.state.set_agentpass_token(args[1], args[2])
            return f"agentpass token updated for {args[1]}"
        if args[0] == "set":
            if len(args) < 3:
                return "usage: tokens set <provider_id|provider_admin_token|bcs_to_provider_token> <value>"
            key = args[1]
            value = args[2]
            if key not in {"provider_id", "provider_admin_token", "bcs_to_provider_token"}:
                return f"unsupported token key: {key}"
            setattr(self.state, key, value)
            return f"{key} updated"
        return "usage: tokens show | tokens load <json-file> | tokens set <key> <value> | tokens set-agentpass <provider_bot_ref> <agentpass_token>"

    def provider_command(self, args: list[str]) -> str:
        if not args:
            return "usage: provider register|get|patch|enable|disable"
        action = args[0]
        if action == "register":
            parser = ConsoleArgumentParser(prog="provider register", add_help=False)
            parser.add_argument("--name", default="Local Console Provider")
            parser.add_argument(
                "--auth",
                default="static_bearer",
                choices=["static_bearer", "agentpass", "provider_admin"],
            )
            parser.add_argument("--webhook-url", default=self.webhook_url)
            ns = parser.parse_args(args[1:])
            response = self.client.register_provider(ns.name, ns.webhook_url, ns.auth)
            return "provider registered\n" + pretty_json(
                {
                    "provider_id": response.get("provider_id"),
                    "provider_admin_token": redact_token(response.get("provider_admin_token")),
                    "bcs_to_provider_token": redact_token(response.get("bcs_to_provider_token")),
                }
            )
        if action == "get":
            return pretty_json(self.client.get_provider())
        if action == "patch":
            parser = ConsoleArgumentParser(prog="provider patch", add_help=False)
            parser.add_argument("--name")
            parser.add_argument("--webhook-url")
            ns = parser.parse_args(args[1:])
            return pretty_json(self.client.patch_provider(ns.name, ns.webhook_url))
        if action == "enable":
            return pretty_json(self.client.set_provider_disabled(False))
        if action == "disable":
            return pretty_json(self.client.set_provider_disabled(True))
        return "usage: provider register|get|patch|enable|disable"

    def bot_command(self, args: list[str]) -> str:
        if not args:
            return "usage: bot register|list|list-local|callback"
        action = args[0]
        if action == "register":
            parser = ConsoleArgumentParser(prog="bot register", add_help=False)
            parser.add_argument("provider_bot_ref")
            parser.add_argument("--name")
            parser.add_argument("--summary", default="")
            parser.add_argument("--owner", action="append")
            ns = parser.parse_args(args[1:])
            owners = ns.owner or [self.default_owner]
            name = ns.name or ns.provider_bot_ref
            response = self.client.register_bot(
                ns.provider_bot_ref,
                name=name,
                summary=ns.summary,
                owners=owners,
            )
            return "bot registered\n" + pretty_json(
                {
                    "bot_uuid": response.get("bot_uuid"),
                    "provider_bot_ref": response.get("provider_bot_ref"),
                    "bot_runtime_token": redact_token(response.get("bot_runtime_token")),
                }
            )
        if action == "list":
            return pretty_json(self.client.list_provider_bots())
        if action == "list-local":
            return pretty_json({"items": self.state.list_bots()})
        if action == "callback":
            if len(args) < 4:
                return "usage: bot callback <provider_bot_ref> <run_id> <text>"
            provider_bot_ref = args[1]
            run_id = args[2]
            text = " ".join(args[3:])
            runtime = ProviderRuntime(
                state=self.state,
                bcs_url=self.client.bcs_url,
                strict_auth=False,
                auto_callback=False,
            )
            runtime.post_bot_event(provider_bot_ref, run_id, text)
            return "callback attempted; use 'callbacks' for result"
        return "usage: bot register|list|list-local|callback"

    def sessions_command(self, args: list[str]) -> str:
        if not args or args[0] == "list":
            return pretty_json({"sessions": self.state.snapshot_sessions()})
        if args[0] == "show":
            if len(args) != 3:
                return "usage: sessions show <provider_bot_ref> <session_id>"
            return pretty_json(
                {
                    "messages": self.state.session_messages(args[1], args[2]),
                }
            )
        return "usage: sessions list | sessions show <provider_bot_ref> <session_id>"

    def status_text(self) -> str:
        return pretty_json(
            {
                "provider_id": self.state.provider_id,
                "webhook_url": self.webhook_url,
                "bots": len(self.state.list_bots()),
                "sessions": len(self.state.snapshot_sessions()),
                "requests": len(self.state.snapshot_requests()),
                "callbacks": len(self.state.snapshot_callbacks()),
            }
        )

    def help_text(self) -> str:
        return """Commands:
  status
  tokens show
  tokens load <json-file>
  tokens set <provider_id|provider_admin_token|bcs_to_provider_token> <value>
  tokens set-agentpass <provider_bot_ref> <agentpass_token>
  provider register [--name NAME] [--auth static_bearer|agentpass|provider_admin] [--webhook-url URL]
  provider get
  provider patch [--name NAME] [--webhook-url URL]
  provider enable | provider disable
  bot register <provider_bot_ref> [--name NAME] [--summary TEXT] [--owner STAFF_NO]
  bot list
  bot list-local
  bot callback <provider_bot_ref> <run_id> <text>
  sessions list
  sessions show <provider_bot_ref> <session_id>
  requests
  callbacks
  reset
  quit
"""


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_file),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a standalone interactive BCS Provider downlink simulator.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28080)
    parser.add_argument("--bcs-url", default="http://127.0.0.1:21000")
    parser.add_argument("--state-file", default="provider_downlink_console_state.json")
    parser.add_argument("--log-file")
    parser.add_argument("--provider-id")
    parser.add_argument("--provider-admin-token")
    parser.add_argument("--bcs-to-provider-token")
    parser.add_argument(
        "--agentpass-token",
        action="append",
        default=[],
        metavar="PROVIDER_BOT_REF=TOKEN",
        help="AgentPass token for callbacks in agentpass mode; may be repeated.",
    )
    parser.add_argument("--strict-auth", action="store_true")
    parser.add_argument("--auto-callback", action="store_true")
    parser.add_argument("--callback-delay-ms", type=int, default=50)
    parser.add_argument("--mock-user-id")
    parser.add_argument("--cookie")
    parser.add_argument("--user-bearer-token")
    parser.add_argument("--no-console", action="store_true")
    return parser.parse_args(argv)


def apply_arg_overrides(state: ProviderState, args: argparse.Namespace) -> None:
    overrides: JsonObject = {}
    if args.provider_id:
        overrides["provider_id"] = args.provider_id
    if args.provider_admin_token:
        overrides["provider_admin_token"] = args.provider_admin_token
    if args.bcs_to_provider_token:
        overrides["bcs_to_provider_token"] = args.bcs_to_provider_token
    if overrides:
        state.merge_tokens(overrides)
    for value in args.agentpass_token or []:
        if "=" not in value:
            raise ValueError("--agentpass-token must be PROVIDER_BOT_REF=TOKEN")
        provider_bot_ref, token = value.split("=", 1)
        state.set_agentpass_token(provider_bot_ref, token)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    state_path = Path(args.state_file)
    log_path = Path(args.log_file) if args.log_file else state_path.with_suffix(".log")
    setup_logging(log_path)
    LOGGER.info("starting provider console args=%s", vars(args))

    state = ProviderState.load(state_path)
    apply_arg_overrides(state, args)
    runtime = ProviderRuntime(
        state=state,
        bcs_url=args.bcs_url,
        strict_auth=args.strict_auth,
        auto_callback=args.auto_callback,
        callback_delay_ms=args.callback_delay_ms,
    )
    server = ProviderConsoleServer((args.host, args.port), state, runtime)
    host, port = server.server_address
    webhook_url = f"http://{host}:{port}/webhook"
    client = BcsApiClient(
        bcs_url=args.bcs_url,
        state=state,
        mock_user_id=args.mock_user_id,
        cookie=args.cookie,
        user_bearer_token=args.user_bearer_token,
    )

    print("Provider downlink console listening", flush=True)
    print(f"  webhook_url: http://{host}:{port}/webhook", flush=True)
    print(f"  health:      http://{host}:{port}/health", flush=True)
    print(f"  state_file:  {state.path}", flush=True)
    print(f"  log_file:    {log_path}", flush=True)
    if state.provider_id:
        print(f"  provider_id: {state.provider_id}", flush=True)
    else:
        print("  provider_id: <not configured>", flush=True)

    if args.no_console:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping provider console", flush=True)
        finally:
            server.server_close()
        return 0

    thread = threading.Thread(target=server.serve_forever, name="provider-http", daemon=True)
    thread.start()
    try:
        Console(
            state=state,
            client=client,
            webhook_url=webhook_url,
            default_owner=args.mock_user_id,
        ).loop()
    finally:
        server.shutdown()
        server.server_close()
        LOGGER.info("provider console stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
