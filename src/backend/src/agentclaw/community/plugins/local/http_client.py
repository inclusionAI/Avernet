"""Local ``HttpClient`` — raises on any unstubbed call (no silent network).

A ``base_url``-scoped mock. Via :class:`MockSeam`, a test stubs results with
``set_response`` / ``set_override`` and asserts with ``.calls_to(...)``; an
**unstubbed** call raises :class:`HttpNotConfiguredError`, so local/test code can
never silently make a real HTTP request.
"""
from __future__ import annotations

from typing import Any, Mapping

import httpx

from agentclaw.community.plugin_api.http_client import HttpClient
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam


class HttpNotConfiguredError(RuntimeError):
    """An unstubbed call hit ``LocalHttpClient`` — stub it before exercising."""

    def __init__(self, method: str, base_url: str, path: str):
        super().__init__(
            f"LocalHttpClient.{method} called for {base_url}{path!r} with no "
            f"configured response — set_response()/set_override() it in your test "
            f"(local/test mode never makes real HTTP)."
        )


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.MOCK,
    rationale="outbound HTTP has no local fake; tests stub per-call via MockSeam, "
    "unstubbed calls raise to prevent silent network access.",
)
class LocalHttpClient(MockSeam, HttpClient):
    """Base-URL-scoped mock transport; unstubbed calls raise."""

    def __init__(self, base_url: str = "http://local.invalid"):
        self._base_url = base_url

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        raise HttpNotConfiguredError("get", self._base_url, path)

    def post(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        files: Any | None = None,
        data: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        raise HttpNotConfiguredError("post", self._base_url, path)

    def put(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        raise HttpNotConfiguredError("put", self._base_url, path)

    def delete(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        raise HttpNotConfiguredError("delete", self._base_url, path)
