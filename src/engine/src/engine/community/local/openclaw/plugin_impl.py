from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from engine.community.kernel.frames import EventFrame, ResponseFrame
from engine.community.plugin_api.openclaw.plugin import OpenClawPlugin


@dataclass
class _LocalWebShellSession:
    """Tiny in-memory WebShellSession test double."""
    closed: bool = False
    rows: int = 24
    cols: int = 80
    _buffer: bytes = b""

    async def read(self) -> bytes:
        data, self._buffer = self._buffer, b""
        return data

    async def write(self, data: bytes) -> None:
        self._buffer += data

    async def resize(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols

    async def close(self) -> None:
        self.closed = True


class _LocalTokenPool:
    """Refcount-only token pool shape used by OpenClawEngine tests."""

    def __init__(self) -> None:
        self.refcount: dict[str, int] = {}
        self.shutdown_called = False

    def register(self, token: str | None) -> None:
        if token:
            self.refcount[token] = self.refcount.get(token, 0) + 1

    async def release(self, token: str | None) -> None:
        if not token:
            return
        count = self.refcount.get(token, 0) - 1
        if count > 0:
            self.refcount[token] = count
        else:
            self.refcount.pop(token, None)

    async def shutdown(self) -> None:
        self.shutdown_called = True
        self.refcount.clear()


class LocalOpenClawPluginImpl(OpenClawPlugin):
    """Deterministic in-memory implementation of the OpenClaw aggregate port.

    This is intentionally small: it proves the OpenClaw Protocol seam can be
    satisfied without gateway/prod dependencies.  It is a test double, not the
    community runtime implementation.
    """

    def __init__(self) -> None:
        self.pool = _LocalTokenPool()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._cron: dict[str, dict[str, Any]] = {}
        self._mcp: dict[str, dict[str, Any]] = {}
        self._files: dict[str, bytes] = {}

    async def node_list(self) -> list[dict[str, Any]]:
        return []

    async def forward_request(
        self,
        request_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        return ResponseFrame.ok_response(request_id, {"method": method, "params": params or {}})

    async def forward_raw_frame(self, frame: dict[str, Any], token: str | None = None) -> None:
        return None

    async def approvals_get(self, session_key: str | None = None, token: str | None = None) -> dict[str, Any]:
        return {"ok": True, "payload": {"mode": "auto", "sessionKey": session_key}}

    async def approvals_set(self, session_key: str, mode: str, token: str | None = None) -> dict[str, Any]:
        return {"ok": True, "payload": {"mode": mode, "sessionKey": session_key}}

    async def models_list(self) -> list[dict[str, Any]]:
        return [{"id": "local-model", "name": "Local Model", "provider": "local"}]

    async def providers_list(self) -> list[dict[str, Any]]:
        return [{"id": "local", "name": "Local", "models": [{"id": "local-model"}]}]

    async def cron_list(self, include_disabled: bool = True, token: str | None = None) -> list[dict]:
        jobs = list(self._cron.values())
        return jobs if include_disabled else [j for j in jobs if not j.get("disabled")]

    async def cron_get(self, job_id: str, token: str | None = None) -> dict | None:
        return self._cron.get(job_id)

    async def cron_status(self, token: str | None = None) -> dict:
        return {"running": 0, "total": len(self._cron)}

    async def cron_add(self, params: dict, token: str | None = None) -> dict:
        job_id = str(params.get("id") or params.get("jobId") or f"job-{len(self._cron) + 1}")
        job = {**params, "id": job_id}
        self._cron[job_id] = job
        return job

    async def cron_update(self, job_id: str, patch: dict, token: str | None = None) -> dict:
        if job_id not in self._cron:
            raise RuntimeError(f"cron job not found: {job_id}")
        self._cron[job_id].update(patch)
        return self._cron[job_id]

    async def cron_remove(self, job_id: str, token: str | None = None) -> bool:
        return self._cron.pop(job_id, None) is not None

    async def cron_run(self, job_id: str, mode: str, token: str | None = None) -> dict:
        if job_id not in self._cron:
            raise RuntimeError(f"cron job not found: {job_id}")
        return {"jobId": job_id, "mode": mode, "ok": True}

    async def cron_runs(self, job_id: str, limit: int = 20, token: str | None = None) -> list[dict]:
        return []

    async def chat_stream(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        idempotency_key: str | None = None,
        attachments: list[Any] | None = None,
        token: str | None = None,
    ) -> AsyncGenerator[EventFrame, None]:
        self._history.setdefault(session_key, []).append({"role": "user", "content": message})
        yield EventFrame(event="message", payload={"role": "assistant", "content": "local noop"})

    async def chat_abort(self, session_key: str, run_id: str, token: str | None = None) -> dict[str, Any]:
        return {"success": True, "error": None, "payload": {"sessionKey": session_key, "runId": run_id}}

    async def sessions_list(
        self,
        token: str | None = None,
        offset: int = 0,
        limit: int = 50,
        agent_id: str | None = None,
        session_key: str | None = None,
    ) -> list[dict]:
        items = list(self._sessions.values())
        requested_session_key = session_key if session_key and session_key.strip() else None
        if requested_session_key is not None:
            # Keep local mode aligned with production: filter before pagination.
            items = [item for item in items if item.get("key") == requested_session_key]
        return items[offset: offset + limit]

    async def session_create(
        self,
        key: str,
        label: str | None = None,
        model: str | None = None,
        token: str | None = None,
    ) -> dict:
        session = {"id": key, "key": key, "label": label, "model": model}
        self._sessions[key] = session
        return session

    async def session_delete(self, key: str, token: str | None = None) -> bool:
        self._history.pop(key, None)
        return self._sessions.pop(key, None) is not None

    async def session_clear(self, key: str, token: str | None = None) -> None:
        self._history[key] = []

    async def chat_history(self, session_key: str, limit: int | None = None, token: str | None = None) -> list[dict]:
        items = self._history.get(session_key, [])
        return items if limit is None else items[-limit:]

    async def session_patch_then_get(
        self,
        key: str,
        label: str | None = None,
        model: str | None = None,
        token: str | None = None,
    ) -> dict:
        self._sessions.setdefault(key, {"id": key, "key": key})
        if label is not None:
            self._sessions[key]["label"] = label
        if model is not None:
            self._sessions[key]["model"] = model
        return self._sessions[key]

    async def session_reset(self, session_key: str, token: str | None = None) -> dict:
        self._history[session_key] = []
        return {"success": True, "payload": {"sessionKey": session_key}}

    async def upload(self, target_path: str, content: bytes) -> dict[str, Any]:
        if not target_path:
            raise ValueError("target_path is required")
        overwritten = target_path in self._files
        self._files[target_path] = content
        return {"target_path": target_path, "size": len(content), "overwritten": overwritten}

    async def read(self, file_path: str) -> bytes:
        if not file_path:
            return b""
        if file_path not in self._files:
            raise FileNotFoundError(file_path)
        return self._files[file_path]

    async def remove(self, target_path: str) -> dict[str, Any]:
        if target_path not in self._files:
            raise FileNotFoundError(target_path)
        self._files.pop(target_path)
        return {"target_path": target_path, "path_type": "file"}

    async def rmtree(self, target_path: str) -> str:
        prefix = target_path.rstrip("/") + "/"
        removed = [p for p in self._files if p.startswith(prefix)]
        if not removed:
            raise FileNotFoundError(target_path)
        for p in removed:
            self._files.pop(p, None)
        return target_path

    async def list_dir(
        self, dir_path: str, recursive: bool = False, exclude_dirs: set[str] | None = None
    ) -> dict[str, Any]:
        prefix = dir_path.rstrip("/") + "/"
        files = [
            {"name": p.removeprefix(prefix), "path": p, "relative_path": p.removeprefix(prefix), "is_dir": False, "size": len(c)}
            for p, c in self._files.items()
            if p.startswith(prefix)
        ]
        return {"dir_path": dir_path, "recursive": recursive, "files": files}

    async def get_default_config(self) -> dict[str, Any]:
        return {"path": "local://default-config", "config": {}}

    async def list_servers(self) -> list[dict[str, Any]]:
        return [{**v, "server_code": k} for k, v in sorted(self._mcp.items())]

    async def get_server(self, server_code: str) -> dict[str, Any] | None:
        entry = self._mcp.get(server_code)
        return None if entry is None else {**entry, "server_code": server_code}

    async def create_server(self, entry: dict[str, Any]) -> dict[str, Any]:
        code = str(entry["server_code"])
        if code in self._mcp:
            raise FileExistsError(code)
        self._mcp[code] = dict(entry)
        return {**self._mcp[code], "server_code": code}

    async def update_server(self, server_code: str, entry: dict[str, Any]) -> dict[str, Any]:
        if server_code not in self._mcp:
            raise FileNotFoundError(server_code)
        self._mcp[server_code] = {**entry, "server_code": server_code}
        return self._mcp[server_code]

    async def delete_server(self, server_code: str) -> bool:
        return self._mcp.pop(server_code, None) is not None

    async def get_server_status(self, server_code: str) -> dict[str, Any]:
        entry = self._mcp.get(server_code)
        return {"server_code": server_code, "status": "running" if entry and entry.get("enabled") else "stopped"}

    async def call_tool(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"tool_name": tool, "server_code": "", "content": [], "is_error": False}

    async def filter_servers(self, codes: list[str], timeout: int = 30) -> dict[str, Any]:
        return {"server_codes": list(codes), "command": [], "return_code": 0, "stdout": "", "stderr": ""}

    async def ensure_center_skills(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"ok": list(params.get("items") or []), "failed": []}

    async def sync_symlinks(self, params: dict[str, Any]) -> dict[str, Any]:
        symlinks = list(params.get("symlinks") or [])
        return {"total": len(symlinks), "created": [], "updated": [], "kept": [], "removed": [], "base_dir": "local://skills"}

    async def sync_bindpaths(self, params: dict[str, Any]) -> dict[str, Any]:
        symlinks = list(params.get("symlinks") or [])
        return {"total": len(symlinks), "created": [], "updated": [], "kept": [], "removed": []}

    async def clean_symlinks(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"directories_scanned": len(params.get("directories") or []), "removed": []}

    def check_token(self, token_str: str) -> bool:
        return True

    async def open_session(self) -> _LocalWebShellSession:
        await asyncio.sleep(0)
        return _LocalWebShellSession()


# Keep the conventional concrete name used by existing OpenClaw tests/helpers.
OpenClawPluginImpl = LocalOpenClawPluginImpl

__all__ = ["LocalOpenClawPluginImpl", "OpenClawPluginImpl"]
