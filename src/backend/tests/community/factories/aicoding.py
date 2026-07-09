"""Seed helpers for the aicoding data-proxy endpoint.

Two helpers route the :class:`DataProxyService` resolver's tier-1
short-circuit at a controlled destination so endpoint tests can drive
the **real** service through to a known terminal state without
mocking:

* :func:`route_to_fake_engine` — points ``AICODING_ENGINE_URL`` at a
  lazy-started in-process HTTP server that always replies ``200
  {"forwarded":true,"engine":"fake"}``. Lets the success path
  actually execute the ``ForwardResult`` assembly tail of
  :meth:`DataProxyService.forward`. The server runs on a daemon
  thread shared across the pytest session — no teardown needed.

* :func:`route_to_dead_port` — points ``AICODING_ENGINE_URL`` at an
  OS-allocated-then-released local port (no listener). ``httpx``
  raises :class:`httpx.ConnectError`; the service re-raises
  :class:`EngineUnreachable`; the global handler maps to HTTP 502.

Both mutate the process-wide ``AICODING_ENGINE_URL`` env var; the
next seed in the test session overwrites it, so cross-test isolation
relies on each case re-seeding before the request goes out (every
``@endpoint_test`` does this — see ``tests/endpoints/
test_aicoding_data_proxy.py``).
"""
from __future__ import annotations

import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from agentclaw.community.core.aicoding.services.data_proxy_service import ENGINE_URL_ENV
from tests.community.framework.world import World


# Marker bytes the fake engine returns. The ok cases assert on
# ``forwarded`` / ``engine`` to prove ``ForwardResult`` preserved the
# upstream payload byte-for-byte.
_HAPPY_BODY = b'{"forwarded":true,"engine":"fake"}'


class _FakeEngineHandler(BaseHTTPRequestHandler):
    """Echoes ``200 application/json`` for every method the data-proxy
    catch-all can emit. Records nothing — the assertion surface is the
    FastAPI response, not inbound captures."""

    def _reply(self) -> None:
        # Drain the request body so keep-alive doesn't stall.
        length = int(self.headers.get("content-length", 0) or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_HAPPY_BODY)))
        self.end_headers()
        self.wfile.write(_HAPPY_BODY)

    do_GET = _reply
    do_POST = _reply
    do_PUT = _reply
    do_PATCH = _reply
    do_DELETE = _reply
    do_OPTIONS = _reply

    def log_message(self, format, *args):  # noqa: A002 — stdlib signature
        # Silence per-request stderr lines from the stdlib server.
        pass


# SSE frames the streaming fake engine trickles back. Two ``data:``
# events with a small inter-frame gap so a buffering proxy (the
# pre-fix bug) and a streaming one are behaviourally distinguishable,
# plus a terminal ``done`` event mirroring harness-data's shape.
_SSE_FRAMES: tuple[bytes, ...] = (
    b'event: message\ndata: {"type":"turn_start","turn":1}\n\n',
    b'event: message\ndata: {"type":"agent_end","turns":1}\n\n',
    b"event: done\ndata: {}\n\n",
)


class _FakeSseEngineHandler(BaseHTTPRequestHandler):
    """Replies ``200 text/event-stream`` and trickles SSE frames.

    Mirrors harness-data's ``/api/eval/stream``: a long-lived response
    whose ``Content-Type`` is ``text/event-stream``. The service must
    detect this and return a ``StreamingForwardResult``; the router
    must render it as a ``StreamingResponse`` (the regression under
    test). Sent chunked (no Content-Length) and flushed per frame so a
    correct proxy delivers events incrementally.
    """

    def _reply(self) -> None:
        length = int(self.headers.get("content-length", 0) or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        # No Content-Length → chunked/streamed framing.
        self.end_headers()
        for frame in _SSE_FRAMES:
            self.wfile.write(frame)
            self.wfile.flush()
            time.sleep(0.02)

    do_GET = _reply
    do_POST = _reply
    do_PUT = _reply
    do_PATCH = _reply
    do_DELETE = _reply
    do_OPTIONS = _reply

    def log_message(self, format, *args):  # noqa: A002 — stdlib signature
        pass


# Lazy singletons: stand up the fake engine + grab a known-free dead
# port on first use, reuse for the rest of the pytest session.
_FAKE_ENGINE_BASE: Optional[str] = None
_FAKE_SSE_ENGINE_BASE: Optional[str] = None
_DEAD_BASE: Optional[str] = None


def _ensure_fake_engine_started() -> str:
    global _FAKE_ENGINE_BASE
    if _FAKE_ENGINE_BASE is None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeEngineHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        host, port = server.server_address
        _FAKE_ENGINE_BASE = f"http://{host}:{port}"
    return _FAKE_ENGINE_BASE


def _ensure_fake_sse_engine_started() -> str:
    global _FAKE_SSE_ENGINE_BASE
    if _FAKE_SSE_ENGINE_BASE is None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeSseEngineHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        host, port = server.server_address
        _FAKE_SSE_ENGINE_BASE = f"http://{host}:{port}"
    return _FAKE_SSE_ENGINE_BASE


def _ensure_dead_port_chosen() -> str:
    """Bind a socket to port 0, read the OS-assigned port, release the
    socket. The returned port is — modulo a tiny race — known-unused,
    suitable for forcing ``ConnectionRefused`` from ``httpx``."""
    global _DEAD_BASE
    if _DEAD_BASE is None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        _DEAD_BASE = f"http://127.0.0.1:{port}"
    return _DEAD_BASE


def route_to_fake_engine(world: World) -> str:
    """Tier-1 the resolver at an in-process fake engine that always 200s.

    The fake replies ``{"forwarded":true,"engine":"fake"}`` — pair with
    ``ExpectSuccess(status=200, json_contains={"forwarded": True,
    "engine": "fake"})`` to prove the ``ForwardResult`` assembly
    preserved the upstream payload.

    Returns the base URL it pinned (for callers that want to assert on
    the resolved value — most don't need this).
    """
    del world  # not used; signature kept for the factory convention.
    base = _ensure_fake_engine_started()
    os.environ[ENGINE_URL_ENV] = base
    return base


def route_to_fake_engine_sse(world: World) -> str:
    """Tier-1 the resolver at an in-process fake engine that streams SSE.

    The fake replies ``200 text/event-stream`` and trickles a few
    ``data:`` frames. Drives the **real** service down its streaming
    branch (``StreamingForwardResult``) and the **real** router down
    its ``StreamingResponse`` branch — the end-to-end guard for the
    SSE pass-through regression.

    Returns the base URL it pinned.
    """
    del world
    base = _ensure_fake_sse_engine_started()
    os.environ[ENGINE_URL_ENV] = base
    return base


def route_to_dead_port(world: World) -> str:
    """Tier-1 the resolver at a known-closed local port so ``httpx``
    fails to connect.

    Triggers :class:`EngineUnreachable` → HTTP 502. Pair with
    ``ExpectError(status=502, json_contains={"detail": {"op":
    "data_proxy_forward"}})``.

    Returns the base URL it pinned.
    """
    del world
    base = _ensure_dead_port_chosen()
    os.environ[ENGINE_URL_ENV] = base
    return base
