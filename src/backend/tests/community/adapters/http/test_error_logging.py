"""Unit tests for the adapter-layer error diagnostics helper.

Covers the three things the log line depends on being true: the summarizer is
bounded (no payload dumps), credential-shaped names never render, and nothing
in the module raises — a broken diagnostic must not replace the error it was
supposed to describe.
"""

from __future__ import annotations

import inspect
import io
import logging
import warnings
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

import pytest
from fastapi import Request
from pydantic import BaseModel

from agentclaw.community.adapters.http import error_logging
from agentclaw.community.adapters.http.error_logging import (
    REDACTED,
    capture_call_params,
    format_call_params,
    log_public_error,
    params_suffix,
    recall_call_params,
    remember_call_params,
)


def _request(method: str = "GET", path: str = "/openapi/v1/bots") -> Request:
    return Request(
        {"type": "http", "method": method, "path": path, "state": {}, "headers": []}
    )


class _Body(BaseModel):
    bot_name: str
    api_token: str = "s3cret"


class _Engine(str, Enum):
    TECLAW = "teclaw"


@dataclass
class _Caller:
    user_id: str
    session_token: str


class _Service:
    """Stands in for an injected dependency."""


# ============================================================
# capture_call_params — names, filtering, redaction
# ============================================================

def _capture(fn, *args, **kwargs) -> dict:
    return capture_call_params(inspect.signature(fn), args, kwargs)


def test_captures_named_and_positional_arguments():
    def handler(bot_id: str, page: int, request=None):
        ...

    assert _capture(handler, "bot-1", page=3) == {"bot_id": "bot-1", "page": 3}


def test_drops_request_and_injected_services():
    def handler(bot_id: str, request, bot_service, page: int):
        ...

    params = _capture(handler, "bot-1", _request(), _Service(), 2)
    assert params == {"bot_id": "bot-1", "page": 2}


def test_request_is_never_walked_as_a_mapping():
    """``Request`` is a ``Mapping`` over its ASGI scope — walking it would put
    the raw header list (Authorization, the signed principal token) and the DI
    injector into the log line."""
    def handler(request, bot_id: str):
        ...

    authed = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/openapi/v1/bots",
            "state": {},
            "headers": [(b"authorization", b"Bearer super-secret")],
        }
    )
    rendered = format_call_params(_capture(handler, authed, "bot-1"))
    assert "super-secret" not in rendered
    assert "authorization" not in rendered.lower()
    assert rendered == "bot_id='bot-1'"


def test_headers_object_is_opaque():
    def handler(headers):
        ...

    from starlette.datastructures import Headers

    params = _capture(handler, Headers({"authorization": "Bearer super-secret"}))
    assert "super-secret" not in format_call_params(params)


def test_redacts_sensitive_parameter_names():
    def handler(token: str, password: str, bot_id: str):
        ...

    params = _capture(handler, "abc", "hunter2", "bot-1")
    assert params == {"token": REDACTED, "password": REDACTED, "bot_id": "bot-1"}


def test_redacts_sensitive_fields_inside_a_request_body():
    def handler(body: _Body):
        ...

    params = _capture(handler, _Body(bot_name="alpha"))
    assert params["body"] == {"bot_name": "alpha", "api_token": REDACTED}


def test_dataclass_and_enum_and_uuid_render_as_data():
    def handler(caller: _Caller, engine: _Engine, trace: UUID):
        ...

    params = _capture(
        handler,
        _Caller(user_id="u1", session_token="tok"),
        _Engine.TECLAW,
        UUID(int=1),
    )
    assert params["caller"] == {"user_id": "u1", "session_token": REDACTED}
    assert params["engine"] == "teclaw"
    assert params["trace"] == "00000000-0000-0000-0000-000000000001"


def test_long_strings_are_truncated():
    def handler(blob: str):
        ...

    params = _capture(handler, "x" * 5000)
    rendered = params["blob"]
    assert len(rendered) < 300
    assert rendered.endswith("chars)")


def test_long_lists_are_truncated():
    def handler(items: list):
        ...

    params = _capture(handler, list(range(100)))
    assert len(params["items"]) == 21
    assert "more" in params["items"][-1]


def test_bytes_render_as_a_size_not_content():
    def handler(payload: bytes):
        ...

    assert _capture(handler, b"abcdef")["payload"] == "<bytes 6 bytes>"


def test_deeply_nested_values_stop_at_the_depth_limit():
    def handler(tree: dict):
        ...

    deep = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
    rendered = format_call_params(_capture(handler, deep))
    assert "<dict>" in rendered


def test_capture_survives_a_model_that_cannot_be_dumped():
    class Exploding(BaseModel):
        def model_dump(self, *args, **kwargs):
            raise RuntimeError("nope")

    def handler(body):
        ...

    # Unrenderable at top level ⇒ dropped, not raised.
    assert _capture(handler, Exploding()) == {}


def test_attributes_are_never_called_on_an_arbitrary_object():
    """Duck-typing ``model_dump``/``filename`` means *invoking* an attribute on
    whatever was passed. A test double answers every attribute, so the duck-typed
    version called ``model_dump()`` on every injected AsyncMock and left
    un-awaited coroutines behind it."""
    from unittest.mock import AsyncMock, MagicMock

    def handler(service, other):
        ...

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any RuntimeWarning fails this test
        assert _capture(handler, AsyncMock(), MagicMock()) == {}


def test_upload_renders_as_a_filename_not_a_stream():
    from starlette.datastructures import UploadFile

    def handler(file):
        ...

    upload = UploadFile(filename="skill.zip", file=io.BytesIO(b"payload"))
    assert _capture(handler, upload)["file"] == "<upload filename='skill.zip'>"
    assert "payload" not in format_call_params(_capture(handler, upload))


def test_capture_survives_a_signature_mismatch():
    def handler(bot_id: str):
        ...

    params = capture_call_params(
        inspect.signature(handler), (), {"unexpected": "value"}
    )
    assert params == {"unexpected": "value"}


# ============================================================
# remember / recall / params_suffix
# ============================================================

def test_params_round_trip_through_the_request_scope():
    request = _request()
    remember_call_params(request, {"bot_id": "b1"})
    assert recall_call_params(request) == {"bot_id": "b1"}
    assert params_suffix(request) == " params={bot_id='b1'}"


def test_params_suffix_is_empty_when_nothing_was_captured():
    assert params_suffix(_request()) == ""


def test_format_call_params_is_bounded():
    rendered = format_call_params({f"k{i}": "v" * 100 for i in range(50)})
    assert rendered.endswith("(truncated)")
    assert len(rendered) < 2100


# ============================================================
# log_public_error — level by status, traceback, no raising
# ============================================================

@pytest.mark.parametrize(
    "status,expected_level",
    [(404, logging.WARNING), (409, logging.WARNING), (502, logging.ERROR)],
)
def test_level_follows_status_and_traceback_is_attached(
    caplog, status, expected_level,
):
    request = _request(method="POST", path="/openapi/v1/bots/b1")
    with caplog.at_level(logging.DEBUG):
        try:
            raise ValueError("boom")
        except ValueError as exc:
            log_public_error(request, exc, status=status, params={"bot_id": "b1"})

    record = caplog.records[-1]
    assert record.levelno == expected_level
    assert record.exc_info is not None, "traceback must be attached"
    message = record.getMessage()
    assert f"[Public {status}]" in message
    assert "ValueError" in message
    assert "POST /openapi/v1/bots/b1" in message
    assert "params={bot_id='b1'}" in message


def test_log_falls_back_to_stashed_params(caplog):
    request = _request()
    remember_call_params(request, {"page": 3})
    with caplog.at_level(logging.DEBUG):
        log_public_error(request, ValueError("boom"), status=500)
    assert "params={page=3}" in caplog.records[-1].getMessage()


def test_log_never_raises_even_if_the_logger_does(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("logging backend down")

    monkeypatch.setattr(error_logging.logger, "warning", explode)
    monkeypatch.setattr(error_logging.logger, "error", explode)
    # No exception escapes: a failed log must never turn a mapped 4xx into a 500.
    log_public_error(_request(), ValueError("boom"), status=404)


def test_log_survives_a_request_without_method_or_path(caplog):
    bare = Request({"type": "http", "state": {}})
    with caplog.at_level(logging.DEBUG):
        log_public_error(bare, ValueError("boom"), status=404)
    assert "[Public 404]" in caplog.records[-1].getMessage()
