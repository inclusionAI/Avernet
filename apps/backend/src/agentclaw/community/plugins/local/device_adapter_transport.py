"""InMemoryDeviceAdapterTransport -- local impl of DeviceAdapterTransport.

A stateful in-memory emulation of a bot's engine adapter. Used by injection
tests (and local boots without a live adapter) so the *real*
``CronRelayService`` and session-resource content proxy run end-to-end: device
resolution, permission checks and response shaping all execute against
production code, with only the HTTP boundary replaced by this local runtime.

State is partitioned per device (keyed off ``conn_info``) so a relay
fanning out across multiple bots sees an isolated cron store per bot,
mirroring the real one-adapter-per-bot topology. The injector binds
this as a singleton, so within one test all calls share state; each
test gets a fresh injector and therefore an empty store.

Response shapes mirror the real adapter's envelopes. ``bot_id`` /
``bot_name`` are intentionally absent — the relay adds those.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import hashlib
import json
from typing import Any, Optional

import httpx

from agentclaw.community.core.devices.services.loopback_targets import (
    is_loopback_target,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterStreamResponse,
    DeviceAdapterTimeoutError,
    DeviceAdapterTransport,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam

# Fixed clock so emitted timestamps are deterministic across runs.
_BASE_MS = 1_700_000_000_000


def _device_key(conn_info: dict[str, Any]) -> str:
    """Stable per-device store key. In local mode each bot's adapter has
    a unique ``url``/``target`` (its own port), so this isolates stores."""
    return (
        conn_info.get("url")
        or conn_info.get("target")
        or conn_info.get("bot_uuid")
        or "default"
    )


_DEFAULT_PROXY_TIMEOUT = 30.0

#: The simulated acli market: market name -> command.
_ACLI_MARKET = {"dataphin": "dataphin", "deepinsight-cli": "di", "yuque-cli": "yuque"}
_ACLI_ALIASES = {"di": "deepinsight-cli"}


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.SIMULATOR,
    rationale="stateful in-memory cron adapter; relay runs end-to-end over it",
)
class InMemoryDeviceAdapterTransport(MockSeam, DeviceAdapterTransport):
    """In-memory cron adapter answering ``/api/cron*`` paths.

    Paths outside the well-known in-memory set (health, capabilities, skills,
    layout probe, ``/api/cron*``) proxy to the per-device adapter URL
    resolved from ``conn_info`` with each ``invoke``, falling back to the
    constructor-supplied ``default_adapter_url`` when the device connection
    info doesn't carry an address. Without either, the ``"unhandled path"``
    sentinel applies (test deployments have no engine to proxy to).
    """

    def __init__(
        self,
        *,
        default_adapter_url: str | None = None,
        proxy_cron: bool = False,
        proxy_skills: bool = False,
    ) -> None:
        self._default_adapter_url = (
            default_adapter_url.rstrip("/") if default_adapter_url else None
        )
        # Singlebox opts in to real runtime state. The defaults preserve the
        # original in-memory behavior used by contract and injection tests.
        self._proxy_cron = bool(proxy_cron)
        self._proxy_skills = bool(proxy_skills)
        # device_key -> {cron_id -> cron item}
        self._crons: dict[str, dict[str, dict[str, Any]]] = {}
        self._materialized_contents: dict[str, tuple[bytes, str, str]] = {}
        # device_key -> installed acli market names
        self._acli_installed: dict[str, set[str]] = {}
        # device_key -> {operation id -> operation}
        self._acli_operations: dict[str, dict[str, dict[str, Any]]] = {}
        self._seq = 0

    # ── helpers ──────────────────────────────────────────────────────────

    def _acli(
        self,
        conn_info: dict[str, Any],
        path: str,
        body: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """A tiny acli install target, so ``/openapi/v1/bots/{bot_id}/clis`` runs end to end.

        Mirrors the engine contract (``/api/cli/acli/*``) with operations that
        end at once: an unknown name fails ``install_failed``, a command alias
        resolves to its market name, uninstalling a tool never installed here
        succeeds, and nothing is ever left running to cancel.
        """
        key = _device_key(conn_info)
        installed = self._acli_installed.setdefault(key, set())
        operations = self._acli_operations.setdefault(key, {})
        name = str(body.get("name") or "")
        if path == "/api/cli/acli/tools":
            return {"success": True, "data": {"tools": [
                {"name": tool, "command": _ACLI_MARKET.get(tool, tool),
                 "description": "", "version": "1.0.0"}
                for tool in sorted(installed)
            ]}}
        if path.startswith("/api/cli/acli/operations/"):
            op = operations.get(path.rsplit("/", 1)[-1])
            if op is None:
                raise DeviceAdapterEndpointNotFoundError(json.dumps({
                    "success": False, "error": "operation_not_found", "data": None,
                }))
            return self._acli_answer(op)
        if path.endswith("/cancel"):
            raise DeviceAdapterHTTPStatusError(409, json.dumps({
                "success": False, "error": "not_active", "message": "nothing is running",
                "data": None,
            }))
        if path not in ("/api/cli/acli/install", "/api/cli/acli/uninstall"):
            return {"success": False, "error": "unsupported_route"}
        self._seq += 1
        op: dict[str, Any] = {
            "id": f"{self._seq:032x}", "action": path.rsplit("/", 1)[-1], "name": name,
            "state": "succeeded", "result": None, "error": None,
        }
        if op["action"] == "install":
            market_name = _ACLI_ALIASES.get(name, name)
            if market_name not in _ACLI_MARKET:
                op.update(state="failed", error={
                    "code": "install_failed", "message": f"no such cli: {name}",
                })
            else:
                op["result"] = {
                    "name": market_name, "command": _ACLI_MARKET[market_name],
                    "description": "", "version": "1.0.0",
                    "already_installed": market_name in installed,
                }
                installed.add(market_name)
        else:
            op["result"] = {"name": name, "uninstalled": name in installed}
            installed.discard(name)
        operations[op["id"]] = op
        return self._acli_answer(op)

    @staticmethod
    def _acli_answer(op: dict[str, Any]) -> dict[str, Any]:
        error = op["error"] or {}
        return {
            "success": op["state"] == "succeeded", "error": error.get("code"),
            "message": error.get("message"), "data": {"operation": dict(op)},
        }

    def _store(self, conn_info: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return self._crons.setdefault(_device_key(conn_info), {})

    def _new_id(self) -> str:
        self._seq += 1
        return f"cron_{self._seq:03d}"

    def register_materialized_content(
        self,
        *,
        resource_id: str,
        content: bytes,
        filename: str,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Register a ready resource exposed by the local Engine adapter.

        Resource IDs are globally unique, so this mirrors the Engine's
        resource-id-only content endpoint without accepting a caller path.
        """
        if not resource_id or not filename:
            raise ValueError("resource_id and filename are required")
        if not isinstance(content, bytes):
            raise ValueError("content must be bytes")
        self._materialized_contents[resource_id] = (
            content,
            filename,
            content_type,
        )

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "next_run_at_ms": _BASE_MS + 86_400_000,
            "last_run_at_ms": _BASE_MS,
            "last_run_status": "success",
            "last_status": "completed",
            "last_duration_ms": 1500,
            "last_delivered": True,
            "last_delivery_status": "delivered",
            "consecutive_errors": 0,
        }

    def _build_item(self, cron_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": cron_id,
            "name": body.get("name"),
            "enabled": body.get("enabled", True),
            "schedule": {
                "kind": "cron",
                "expr": body.get("schedule"),
                "tz": body.get("timezone", "Asia/Shanghai"),
            },
            "payload": {
                "kind": "message",
                "message": body.get("command"),
                "timeout_secs": body.get("timeout_secs", 86400),
                # The engine's cron item carries the model override inside the
                # payload; mirrors what the production adapters read back.
                "model": body.get("model"),
            },
            "session_target": f"session:{cron_id}:user:default",
            "state": self._default_state(),
            "notify": body.get("notify") or {"enabled": False, "user_ids": []},
            "created_at_ms": _BASE_MS,
            "updated_at_ms": _BASE_MS,
        }

    # ── transport ────────────────────────────────────────────────────────

    def _proxied_adapter_url(
        self, conn_info: dict[str, Any], path: str
    ) -> str | None:
        """Resolve an adapter URL for an opted-in runtime-state path."""
        should_proxy = (
            (self._proxy_cron and path.startswith("/api/cron"))
            or (
                self._proxy_skills
                and path
                in {"/api/skills/mappings/apply", "/api/skills/layout/probe"}
            )
        )
        return self._resolve_adapter_url(conn_info) if should_proxy else None

    def _resolve_adapter_url(self, conn_info: dict[str, Any]) -> str | None:
        """Per-device adapter base URL for unmatched paths.

        Resolution order: when the device binding's ``target`` is a loopback
        address, the per-device adapter's own HTTP origin **wins** over
        ``conn_info["url"]`` — singlebox has no BaaS proxy infrastructure, so
        the BaaS ``invoke-http`` endpoint the URL carries returns
        ``PLATFORM_ERROR`` and is never routable locally. In deployments where
        the target is not a bare loopback (production, remote BaaS), the
        URL (the production transport contract) takes priority. The
        constructor ``default_adapter_url`` is the last resort; ``None``
        means this caller cannot be proxied.
        """
        target = conn_info.get("target")
        if isinstance(target, str) and target and is_loopback_target(target):
            return f"http://{target.rstrip('/')}"
        url = conn_info.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url.rstrip("/")
        if isinstance(target, str) and target:
            return f"http://{target.rstrip('/')}"
        return self._default_adapter_url

    async def _proxy(
        self,
        adapter_url: str,
        conn_info: dict[str, Any],
        method: str,
        path: str,
        body: Optional[dict[str, Any]],
        params: Optional[dict[str, Any]],
        timeout: float | None,
    ) -> dict[str, Any]:
        """Issue an HTTP request to the adapter, raising protocol errors.

        Error channel matches the community transport:
        ``DeviceAdapterEndpointNotFoundError`` for 404,
        ``DeviceAdapterHTTPStatusError`` for other HTTP errors,
        ``DeviceAdapterTimeoutError`` for timeouts, and a bare ``ValueError``
        for any remaining transport failures so the relay maps them uniformly.
        """
        url = f"{adapter_url}{path}"
        headers = dict(conn_info.get("headers") or {})
        try:
            async with httpx.AsyncClient(
                timeout=timeout or _DEFAULT_PROXY_TIMEOUT,
                headers=headers or None,
            ) as client:
                resp = await client.request(
                    method, url, json=body, params=params
                )
        except httpx.TimeoutException as exc:
            raise DeviceAdapterTimeoutError(
                f"engine adapter timed out on {path}: {exc}"
            ) from exc
        except httpx.RequestError as exc:
            raise ValueError(
                f"engine adapter unreachable at {adapter_url}: {exc}"
            ) from exc

        if resp.status_code == 404:
            raise DeviceAdapterEndpointNotFoundError(resp.text)
        if resp.status_code >= 400:
            raise DeviceAdapterHTTPStatusError(resp.status_code, resp.text)

        if not resp.content:
            return {"success": True, "data": None, "message": None}
        try:
            return resp.json()
        except ValueError:
            # Non-JSON 2xx (204 No Content, plain text): the transport
            # contract expects an envelope dict; wrap the raw text.
            return {"success": True, "data": resp.text, "message": None}

    async def invoke(
        self,
        conn_info: dict[str, Any],
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        proxied_adapter_url = self._proxied_adapter_url(conn_info, path)
        if proxied_adapter_url is not None:
            return await self._proxy(
                proxied_adapter_url,
                conn_info,
                method,
                path,
                body,
                params,
                timeout,
            )

        if path == "/health":
            return {"status": "ok", "engine": conn_info.get("engine", "openclaw")}
        if path == "/api/engine/capabilities":
            return {
                "success": True,
                "data": {
                    "supported": ["skills.local_package.apply.v1"],
                    "limited": {},
                    "fallback": {},
                },
            }
        if path == "/api/skills/mappings/apply":
            request = body or {}
            # Mirror the Engine's logical deduplication and replacement-owned
            # retirement contract, without inventing physical paths locally.
            desired = {
                tuple(sorted(item.items())): item
                for item in request.get("mappings", [])
            }
            desired_names = {item["link_name"] for item in desired.values()}
            retired = {
                tuple(sorted(item.items())): item
                for item in request.get("retired_mappings", [])
                if item["link_name"] not in desired_names
            }
            return {
                "success": True,
                "data": {
                    "status": "CONVERGED",
                    "items": [
                        {
                            "mapping": item,
                            "target": "",
                            "action": "APPLY",
                            "status": "CONVERGED",
                            "retryable": False,
                        }
                        for item in desired.values()
                    ]
                    + [
                        {
                            "mapping": item,
                            "target": "",
                            "action": "RETIRE",
                            "status": "CONVERGED",
                            "retryable": False,
                        }
                        for item in retired.values()
                    ],
                    "issues": [],
                    "evidence": {"simulated": True},
                },
            }
        if path == "/api/skills/layout/probe":
            contract_version = (body or {}).get(
                "layout_contract_version", "skills-pool-p3-v1"
            )
            return {
                "success": True,
                "data": {
                    "status": "NOT_CAPABLE",
                    "engine": (body or {}).get("engine", "openclaw"),
                    "layout_contract_version": contract_version,
                    "preparation_id": None,
                    "evidence": {
                        "reason": "local_simulator_has_no_persistent_pool_layout"
                    },
                },
            }

        if path.startswith("/api/v1/cli/acli/"):
            # teclaw serves the same acli contract under its own ``/api/v1``.
            path = "/api" + path.removeprefix("/api/v1")
        if path.startswith("/api/cli/acli/"):
            return self._acli(conn_info, path, body or {}, params or {})

        # ── Guard: only /api/cron* paths enter the in-memory store.
        # Everything else either proxies to the device's adapter (when an
        # address is resolvable) or returns the sentinel. This prevents
        # the cron shape parser below from swallowing non-cron paths
        # (sessions, models, nodes …) and returning fabricated data.
        if not path.startswith("/api/cron"):
            adapter_url = self._resolve_adapter_url(conn_info)
            if adapter_url:
                return await self._proxy(
                    adapter_url, conn_info, method, path, body, params, timeout
                )
            return {
                "success": False,
                "message": f"unhandled path {path}",
                "error_code": 404,
            }

        # ── In-memory cron store (paths below are guaranteed /api/cron*)─
        store = self._store(conn_info)
        # Segments after ``/api/cron``: [] | [status] | [running] |
        # [task_id] | [task_id, run|runs]
        rest = [s for s in path.split("?", 1)[0].strip("/").split("/")[2:] if s]
        method = method.upper()

        if not rest:
            if method == "POST":
                cron_id = self._new_id()
                item = self._build_item(cron_id, body or {})
                store[cron_id] = item
                return {"success": True, "data": item}
            # GET list
            return {"success": True, "data": list(store.values())}

        if rest == ["status"]:
            enabled = [c for c in store.values() if c.get("enabled")]
            return {
                "success": True,
                "data": {
                    "running": False,
                    "job_count": len(store),
                    "enabled_count": len(enabled),
                    "next_run_at_ms": _BASE_MS + 86_400_000,
                },
            }

        if rest == ["running"]:
            return {"success": True, "data": {"running": []}}

        task_id = rest[0]

        if len(rest) == 1:
            if method == "GET":
                # Simulator is forgiving: it "knows" any task_id the relay
                # asks about, materialising a default item on first read so
                # permission/routing flows don't need to pre-create one.
                item = store.get(task_id) or self._build_item(
                    task_id, {"name": f"Cron {task_id}"}
                )
                store[task_id] = item
                return {"success": True, "data": item}
            if method == "PUT":
                item = store.get(task_id)
                if item is None:
                    item = self._build_item(task_id, {"name": f"Cron {task_id}"})
                    store[task_id] = item
                if body:
                    if "name" in body:
                        item["name"] = body["name"]
                    if "enabled" in body:
                        item["enabled"] = body["enabled"]
                    if "schedule" in body:
                        item["schedule"]["expr"] = body["schedule"]
                    if "timezone" in body:
                        item["schedule"]["tz"] = body["timezone"]
                    if "command" in body:
                        item["payload"]["message"] = body["command"]
                    if "timeout_secs" in body:
                        item["payload"]["timeout_secs"] = body["timeout_secs"]
                    if "model" in body:
                        item["payload"]["model"] = body["model"]
                item["updated_at_ms"] = _BASE_MS + 1000
                return {"success": True, "data": item}
            if method == "DELETE":
                store.pop(task_id, None)
                return {"success": True}

        if rest == [task_id, "run"]:
            return {
                "success": True,
                "data": {"ok": True, "ran": True, "reason": ""},
            }

        if rest == [task_id, "runs"]:
            item = store.get(task_id)
            return {
                "success": True,
                "data": {
                    "input": (item or {}).get("payload", {}).get("message", ""),
                    "runs": [
                        {
                            "job_id": "run_001",
                            "started_at_ms": _BASE_MS,
                            "finished_at_ms": _BASE_MS + 1500,
                            "status": "success",
                            "error": None,
                            "duration_ms": 1500,
                            "output": "done",
                            "input_tokens": 100,
                            "output_tokens": 50,
                        }
                    ],
                    "unread_count": "0",
                },
            }

        # Unhandled /api/cron sub-shape (e.g. /api/cron/{id}/ nonexistent/verb).
        return {
            "success": False,
            "message": f"unhandled path {path}",
            "error_code": 404,
        }

    async def _proxy_multipart(
        self,
        adapter_url: str,
        conn_info: dict[str, Any],
        path: str,
        *,
        files: Mapping[str, tuple[str, bytes, str]],
        data: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Forward multipart Skill-package operations to the engine adapter."""
        url = f"{adapter_url}{path}"
        request_headers = dict(conn_info.get("headers") or {})
        if headers:
            request_headers.update(headers)
        try:
            async with httpx.AsyncClient(
                timeout=timeout or _DEFAULT_PROXY_TIMEOUT,
                headers=request_headers or None,
            ) as client:
                resp = await client.post(
                    url,
                    files=list(files.items()),
                    data=dict(data),
                )
        except httpx.TimeoutException as exc:
            raise DeviceAdapterTimeoutError(
                f"engine adapter timed out on {path}: {exc}"
            ) from exc
        except httpx.RequestError as exc:
            raise ValueError(
                f"engine adapter unreachable at {adapter_url}: {exc}"
            ) from exc

        if resp.status_code == 404:
            raise DeviceAdapterEndpointNotFoundError(resp.text)
        if resp.status_code >= 400:
            raise DeviceAdapterHTTPStatusError(resp.status_code, resp.text)

        if not resp.content:
            return {"success": True, "data": None, "message": None}
        try:
            return resp.json()
        except ValueError:
            return {"success": True, "data": resp.text, "message": None}

    async def invoke_multipart(
        self,
        conn_info: dict[str, Any],
        path: str,
        *,
        files: Mapping[str, tuple[str, bytes, str]],
        data: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if (
            self._proxy_skills
            and path in {"/api/skills/local/apply", "/api/v1/file/skill-package"}
        ):
            adapter_url = self._resolve_adapter_url(conn_info)
            if adapter_url is not None:
                return await self._proxy_multipart(
                    adapter_url,
                    conn_info,
                    path,
                    files=files,
                    data=data,
                    headers=headers,
                    timeout=timeout,
                )

        uploaded = files.get("file")
        if uploaded is None:
            return {"success": False, "error": "invalid_package"}
        package = uploaded[1]
        skill_name = data.get("skill_name") or data.get("skills_name") or ""
        digest = hashlib.sha256(package).hexdigest()
        result = {
            "skill_name": skill_name,
            "action": "replaced",
            "content_digest": f"sha256:{digest}",
            "target_path": f"/runtime/skills-local/{skill_name}",
        }
        if path == "/api/v1/file/skill-package":
            return {
                "success": True,
                "skill_name": skill_name,
                "action": "replaced",
                "sha256": digest,
                "target_path": result["target_path"],
            }
        if path == "/api/skills/local/apply":
            return {"success": True, "data": result}
        return {"success": False, "error": "unsupported_route"}

    async def stream(
        self,
        conn_info: dict[str, Any],
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        *,
        timeout: float | None = None,
    ) -> DeviceAdapterStreamResponse:
        prefix = "/api/resource-materializations/"
        suffix = "/content"
        resource_id = None
        if method == "GET" and path.startswith(prefix) and path.endswith(suffix):
            resource_id = path[len(prefix) : -len(suffix)]
        registered = (
            self._materialized_contents.get(resource_id) if resource_id else None
        )
        if registered is not None:
            content, filename, content_type = registered

            async def content_body() -> AsyncIterator[bytes]:
                yield content

            async def close() -> None:
                return None

            disposition = (params or {}).get("disposition", "inline")
            return DeviceAdapterStreamResponse(
                status_code=200,
                headers={
                    "content-type": content_type,
                    "content-length": str(len(content)),
                    "content-disposition": f'{disposition}; filename="{filename}"',
                },
                body=content_body(),
                close=close,
            )

        async def error_body() -> AsyncIterator[bytes]:
            yield b'{"detail":"resource_not_materialized"}'

        async def close() -> None:
            return None

        return DeviceAdapterStreamResponse(
            status_code=409,
            headers={"content-type": "application/json"},
            body=error_body(),
            close=close,
        )
