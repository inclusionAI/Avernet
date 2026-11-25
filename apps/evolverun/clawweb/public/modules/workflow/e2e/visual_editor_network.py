"""Fail-closed, local-only routing for the visual editor browser harness.

No server is contacted: requests are either fulfilled from the real build / exact
API fixtures or aborted. This module's policy tests need only the standard library.
"""

from __future__ import annotations

import copy
import json
import mimetypes
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import SplitResult, unquote, urlsplit

if TYPE_CHECKING:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Request,
        Route,
        WebSocketRoute,
    )


PAGE_PATHS = frozenset({"/", "/index.html", "/workflows/workspace"})
SAVE_REQUESTS = (
    ("POST", "/api/workflows/save"),
    ("POST", "/api/auto-heal/save"),
)
READ_REQUESTS = frozenset(
    {
        ("GET", "/api/runs/workflow-types"),
        ("GET", "/api/workflows/editor-test"),
        ("GET", "/api/workflows/editor-test/history"),
        ("GET", "/api/facades"),
        ("GET", "/api/auth/me"),
        # Validation uses POST but is read-only; it never opens the save gate.
        ("POST", "/api/workflows/validate"),
    }
)


def default_dist_dir() -> Path:
    """Resolve the public host build independently of the caller's cwd."""
    return Path(__file__).resolve().parents[3] / "app/clawweb/dist/web"


def _parse_url(url: str) -> SplitResult:
    # urlsplit silently strips some controls; reject them before parsing.
    if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise ValueError("URL contains whitespace, control characters, or backslashes")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP(S) URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not allowed")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Invalid port")
    return parsed


def _origin(url: SplitResult) -> tuple[str, str, int]:
    return (
        url.scheme,
        str(url.hostname),
        url.port or (443 if url.scheme == "https" else 80),
    )


def _safe_path(raw: str) -> str:
    path = unquote(raw, errors="strict")
    if (
        not path.startswith("/")
        or re.search(r"%(?:2f|5c)", raw, re.IGNORECASE)
        or "%" in path
        or "\\" in path
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or (
            path != "/" and any(part in {"", ".", ".."} for part in path[1:].split("/"))
        )
    ):
        raise ValueError("Non-canonical path or path traversal")
    if path.startswith("/api") and raw != path:
        raise ValueError("API paths must use their exact canonical spelling")
    return path


class OfflineNetworkGuard:
    """Allow one origin, local build files, and explicitly named synthetic APIs."""

    def __init__(self, base_url: str, dist_dir: Path, spec: dict[str, Any]) -> None:
        base = _parse_url(base_url)
        if base.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Browser tests only allow loopback base origins")
        if base.path not in {"", "/"} or base.query or base.fragment:
            raise ValueError(
                "--base-url must be an origin, without a path/query/fragment"
            )
        self.base_url = f"{base.scheme}://{base.netloc}"
        self.origin = _origin(base)
        self.dist_dir = dist_dir.resolve(strict=True)
        if not self.dist_dir.is_dir():
            raise ValueError("--dist-dir must be a built dist/web directory")
        # Fail before launching Chromium, not after an unrouteable navigation.
        self._local_file("index.html")
        self.saved = copy.deepcopy(spec)
        self.calls: list[dict[str, str]] = []
        self.external: list[str] = []
        self.violations: list[dict[str, str]] = []
        # None means the save dialog's explicit confirmation step is not active.
        self._remaining_saves: list[tuple[str, str]] | None = None

    def _local_file(self, relative: str) -> Path:
        candidate = self.dist_dir
        for part in Path(relative).parts:
            candidate /= part
            if candidate.is_symlink():
                raise ValueError("Symlinked build files/directories are not allowed")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(self.dist_dir) or not resolved.is_file():
            raise ValueError("Requested build path is not a contained regular file")
        return resolved

    @contextmanager
    def allow_save(self) -> Iterator[None]:
        """Temporarily allow exactly one DB-save then matching Git-save pair."""
        if self._remaining_saves is not None:
            raise RuntimeError("Save windows cannot be nested")
        self._remaining_saves = list(SAVE_REQUESTS)
        try:
            yield
        finally:
            self._remaining_saves = None

    def _spec_payload(self, request: Request, *, saving: bool) -> dict[str, Any]:
        payload = json.loads(request.post_data or "null")
        if not isinstance(payload, dict):
            raise TypeError("Expected a JSON object payload")
        spec = payload.get("spec")
        if (
            not isinstance(spec, dict)
            or spec.get("id") != "editor-test"
            or not isinstance(spec.get("nodes"), list)
        ):
            raise ValueError("Expected the editor-test workflow spec")
        if saving and payload.get("workflowId") != "editor-test":
            raise ValueError("Only the synthetic workflow may be saved")
        return spec

    def _api_response(self, request: Request, path: str) -> Any:
        key = (request.method, path)
        if key not in READ_REQUESTS and key not in SAVE_REQUESTS:
            raise ValueError(
                "API method/path is not on the stub allowlist (deploy is forbidden)"
            )
        if key in SAVE_REQUESTS:
            if not self._remaining_saves or self._remaining_saves[0] != key:
                raise ValueError(
                    "Write outside the explicit save step or out of DB/Git order"
                )
            spec = self._spec_payload(request, saving=True)
            if path == "/api/workflows/save":
                self.saved = copy.deepcopy(spec)
                value = self.saved
            else:
                if spec != self.saved:
                    raise ValueError("Git snapshot differs from the saved DB spec")
                value = {
                    "ok": True,
                    "saved": True,
                    "workflowId": "editor-test",
                    "packId": "editor-test",
                    "branch": "saved/editor-test",
                    "commit": "demo1234",
                    "version": 1,
                }
            self._remaining_saves.pop(0)
            return value
        if path == "/api/workflows/validate":
            self._spec_payload(request, saving=False)
            return {"valid": True, "issues": []}
        fixtures = {
            "/api/runs/workflow-types": {
                "workflows": [
                    {
                        "workflow_id": "editor-test",
                        "workflow_title": self.saved["title"],
                        "run_count": 12,
                        "last_status": "succeeded",
                        "owner_id": "demo-user",
                    }
                ]
            },
            "/api/workflows/editor-test": self.saved,
            "/api/workflows/editor-test/history": {
                "workflowId": "editor-test",
                "history": [],
                "active": None,
            },
            "/api/facades": [],
            "/api/auth/me": {"userId": "demo-user", "isAdmin": False},
        }
        return fixtures[path]

    def _response(self, request: Request) -> tuple[str, bytes | str]:
        url = _parse_url(request.url)
        if url.path == "/api" or url.path.startswith("/api/"):
            self.calls.append({"method": request.method, "path": url.path})
        if _origin(url) != self.origin:
            self.external.append(request.url)
            raise ValueError("Request origin differs from the configured base origin")
        if request.redirected_from is not None:
            raise ValueError("Redirect chains are forbidden")
        path = _safe_path(url.path)
        if path == "/api" or path.startswith("/api/"):
            return "application/json", json.dumps(self._api_response(request, path))
        if request.method != "GET":
            raise ValueError("Static files and pages only allow GET")
        if path in PAGE_PATHS:
            relative = "index.html"
        elif path.startswith("/assets/"):
            relative = path[1:]
        else:
            raise ValueError("Page or asset path is not on the local-build allowlist")
        file_path = self._local_file(relative)
        mime = (
            "text/javascript"
            if file_path.suffix in {".js", ".mjs"}
            else mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        )
        return mime, file_path.read_bytes()

    def handle_route(self, route: Route) -> None:
        """Every intercepted request terminates in fulfill or abort, never network IO."""
        try:
            content_type, body = self._response(route.request)
        except (ValueError, TypeError, OSError) as error:
            self.violations.append(
                {
                    "method": route.request.method,
                    "url": route.request.url,
                    "reason": str(error),
                }
            )
            route.abort("blockedbyclient")
            return
        route.fulfill(status=200, content_type=content_type, body=body)

    def handle_websocket(self, websocket: WebSocketRoute) -> None:
        """Close the intercepted client side without ever connecting to a server."""
        self.violations.append(
            {
                "method": "WEBSOCKET",
                "url": websocket.url,
                "reason": "All WebSocket connections are forbidden",
            }
        )
        websocket.close(code=1008, reason="Offline browser harness")

    def assert_clean(self) -> None:
        if self.violations:
            raise AssertionError(
                "Offline network policy violations: "
                + json.dumps(self.violations, ensure_ascii=False)
            )


def create_guarded_context(
    browser: Browser, guard: OfflineNetworkGuard
) -> BrowserContext:
    """Install context-wide HTTP and WS guards before creating any page or popup."""
    context = browser.new_context(
        viewport={"width": 1600, "height": 1000},
        device_scale_factor=1,
        service_workers="block",
    )
    context.route("**/*", guard.handle_route)
    context.route_web_socket("**/*", guard.handle_websocket)
    return context
