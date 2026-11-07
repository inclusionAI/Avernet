"""Bounded loopback fault proxy; real signed HTTP continues to real BCS.

Only one first ownership request per transient fixture target is disconnected
BEFORE forwarding. No bodies, principals, credentials or raw errors are logged.
"""
from __future__ import annotations

import asyncio
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
import socket
import threading
from urllib.parse import unquote, urlsplit

from agentclaw.community.kernel.lifecycle import LifecycleBase
from tests.community.acceptance.bot_ownership_sync.fixture_passport import PREFIX

_LOG = logging.getLogger("ownership_acceptance.proxy")
_ROUTE = "/api/v1/collaboration/bots/"
_HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "keep-alive", "te", "trailer", "upgrade", "proxy-authorization", "proxy-authenticate"}


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(timeout=1):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        _LOG.warning("owner_acceptance_proxy event=handler_failed")


class OwnershipFaultProxy(LifecycleBase):
    def __init__(self, upstream: str):
        parsed = urlsplit(upstream)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.path not in {"", "/"} or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError("fixture proxy requires a configured HTTP loopback origin")
        self._host, self._port = parsed.hostname, parsed.port or 80
        self._dropped: set[str] = set()
        self._lock = threading.Lock()
        self._thread = None
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_PUT(self):
                self.connection.settimeout(15)
                path = urlsplit(self.path)
                if not path.path.startswith(_ROUTE) or path.query or path.fragment:
                    self.send_error(404)
                    return
                target, separator, resource = path.path[len(_ROUTE):].rpartition("/")
                if not separator or resource not in {"ownership", "managers"} or "/" in target:
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(400)
                    return
                if not 0 <= length <= 2 * 1024 * 1024:
                    self.send_error(413)
                    return
                body = self.rfile.read(length)
                identity = unquote(target)
                bot, _, entity = identity.partition(":")
                transient = (resource == "ownership" and bot.startswith(PREFIX + "transient")
                             and entity.startswith(PREFIX))
                with proxy._lock:
                    drop = transient and identity not in proxy._dropped and len(proxy._dropped) < 16
                    if drop:
                        proxy._dropped.add(identity)
                if drop:
                    _LOG.info("owner_acceptance_proxy event=dropped bot_id=%s", bot)
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                connection = http.client.HTTPConnection(proxy._host, proxy._port, timeout=15)
                try:
                    headers = {key: value for key, value in self.headers.items() if key.lower() not in _HOP_HEADERS}
                    connection.request("PUT", self.path, body=body, headers=headers)
                    response = connection.getresponse()
                    content = response.read()
                    self.send_response(response.status)
                    self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    if transient:
                        _LOG.info("owner_acceptance_proxy event=forwarded bot_id=%s status=%d", bot, response.status)
                except (OSError, http.client.HTTPException):
                    self.close_connection = True
                finally:
                    connection.close()

            def log_message(self, *args):
                pass

        # Reserve a unique ephemeral port before config is consumed. No thread,
        # upstream I/O or other service graph is started during DI construction.
        self._server = BoundedServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self._server.server_port}"

    async def bootstrap(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._server.serve_forever,
                                            name="ownership-acceptance-proxy", daemon=True)
            self._thread.start()

    async def teardown(self):
        # Infrastructure remains available while the production dispatcher drains
        # in shutdown(). Only teardown closes this test-only listening socket.
        if self._thread is not None:
            await asyncio.to_thread(self._server.shutdown)
            await asyncio.to_thread(self._thread.join, 5)
            assert not self._thread.is_alive(), "acceptance proxy did not stop"
            self._thread = None
        self._server.server_close()
