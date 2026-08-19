"""
HTTP utilities — drop-in replacement for `requests` using only the standard library.

Provides a minimal ``requests``-like interface (``get``, ``post``, ``put``)
backed by ``urllib.request`` so that production environments without the
``requests`` package can still run CLI scripts.

Usage:
    from http_utils import get, post, put, RequestException
"""

from __future__ import annotations

import json as _json
import urllib.error as _urllib_error
import urllib.request as _urllib_request
from typing import Any, Optional


class RequestException(Exception):
    """Base exception for HTTP request failures (mirrors requests.RequestException)."""


class _Response:
    """Minimal Response object mimicking ``requests.Response``."""

    __slots__ = ("_body", "status_code", "headers")

    def __init__(self, body: bytes, status_code: int, headers: Any) -> None:
        self._body = body
        self.status_code = status_code
        self.headers = headers

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    @property
    def content(self) -> bytes:
        return self._body

    def json(self, **_kwargs: Any) -> Any:
        return _json.loads(self.text)


def _merge_url(url: str, params: Optional[dict[str, str]] = None) -> str:
    if not params:
        return url
    separator = "&" if "?" in url else "?"
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{url}{separator}{query}"


def _request(
    method: str,
    url: str,
    *,
    params: Optional[dict[str, str]] = None,
    data: Optional[bytes | str] = None,
    json: Optional[Any] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: int = 30,
) -> _Response:
    full_url = _merge_url(url, params)
    body: Optional[bytes] = None
    req_headers: dict[str, str] = dict(headers or {})

    if json is not None:
        body = _json.dumps(json, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    elif isinstance(data, str):
        body = data.encode("utf-8")
    elif isinstance(data, bytes):
        body = data

    req = _urllib_request.Request(full_url, data=body, headers=req_headers, method=method.upper())
    try:
        with _urllib_request.urlopen(req, timeout=timeout) as resp:
            return _Response(resp.read(), resp.status, resp.headers)
    except _urllib_error.HTTPError as exc:
        # HTTPError still has a body — read it so callers can inspect the error
        body_bytes = exc.read() if hasattr(exc, "read") else b""
        return _Response(body_bytes, exc.code, exc.headers)
    except (_urllib_error.URLError, OSError) as exc:
        raise RequestException(str(exc)) from exc


def get(
    url: str,
    *,
    params: Optional[dict[str, str]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: int = 30,
) -> _Response:
    return _request("GET", url, params=params, headers=headers, timeout=timeout)


def post(
    url: str,
    *,
    params: Optional[dict[str, str]] = None,
    data: Optional[bytes | str] = None,
    json: Optional[Any] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: int = 30,
) -> _Response:
    return _request("POST", url, params=params, data=data, json=json, headers=headers, timeout=timeout)


def put(
    url: str,
    *,
    params: Optional[dict[str, str]] = None,
    data: Optional[bytes | str] = None,
    json: Optional[Any] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: int = 30,
) -> _Response:
    return _request("PUT", url, params=params, data=data, json=json, headers=headers, timeout=timeout)