"""Local claude_code test double.

In-memory / no-network implementation of the ``ClaudeCodePlugin`` aggregate
port. It is a deterministic test double, not a real runtime — it never opens a
relay connection, spawns a Node subprocess, or touches the filesystem. Used by
the Rule-25 conformance test (``tests/contracts/test_claude_code_local_plugin``)
to prove the Protocol seam can be satisfied without gateway/prod dependencies.

Mirrors the OpenClaw local mock pattern (``plugins/local/openclaw/plugin_impl``):
each domain port method returns canned native-shape dicts / list[dict] / bool /
EventFrame. Stateful ports (session, mcp, skills, cron, file) keep an in-memory
dict so round-trip assertions work.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from engine.community.kernel.frames import EventFrame
from engine.community.plugin_api.claude_code.plugin import ClaudeCodePlugin


class LocalClaudeCodePluginImpl(ClaudeCodePlugin):
    """Deterministic in-memory implementation of the claude_code aggregate port.

    Proves the Protocol seam can be satisfied without relay/prod dependencies.
    It is a test double, not the community runtime implementation.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._mcp: dict[str, dict[str, Any]] = {}
        self._skills: dict[str, dict[str, Any]] = {}
        self._cron: dict[str, dict[str, Any]] = {}
        self._files: dict[str, bytes] = {}

    # ----------------------------------------------------------------- chat

    async def chat_stream(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        cwd: str | None = None,
        model: str | None = None,
        permission_mode: str | None = None,
        attachments: list[Any] | None = None,
        token: str | None = None,
    ) -> AsyncGenerator[EventFrame, None]:
        self._history.setdefault(session_key, []).append(
            {"role": "user", "content": message}
        )
        yield EventFrame(
            event="message",
            payload={"role": "assistant", "content": "local mock reply"},
        )
        yield EventFrame(event="result", payload={"stop_reason": "end_turn"})

    async def chat_abort(
        self,
        session_key: str,
        run_id: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "error": None,
            "payload": {"sessionKey": session_key, "runId": run_id},
        }

    async def chat_inject(
        self,
        session_key: str,
        message: str,
        label: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        self._history.setdefault(session_key, []).append(
            {"role": "user", "content": message, "label": label}
        )
        return {"success": True, "error": None, "payload": {"injected": True}}

    async def resolve_exec_approval(
        self,
        session_key: str,
        run_id: str,
        decision: str,
        message: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "error": None,
            "payload": {"runId": run_id, "decision": decision},
        }

    async def resolve_interaction(
        self,
        session_key: str,
        run_id: str,
        response: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "error": None,
            "payload": {"runId": run_id, "response": response},
        }

    async def resolve_mode_transition(
        self,
        session_key: str,
        run_id: str,
        decision: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "error": None,
            "payload": {"runId": run_id, "decision": decision},
        }

    # -------------------------------------------------------------- session

    async def sessions_list(
        self,
        token: str | None = None,
        offset: int = 0,
        limit: int = 50,
        agent_id: str | None = None,
    ) -> list[dict]:
        items = list(self._sessions.values())
        if agent_id is not None:
            items = [s for s in items if s.get("agentId") == agent_id]
        return items[offset: offset + limit]

    async def session_create(
        self,
        key: str,
        label: str | None = None,
        model: str | None = None,
        cwd: str | None = None,
        token: str | None = None,
    ) -> dict:
        session = {"key": key, "label": label, "model": model, "cwd": cwd}
        self._sessions[key] = session
        return session

    async def session_delete(
        self,
        key: str,
        token: str | None = None,
    ) -> bool:
        self._history.pop(key, None)
        return self._sessions.pop(key, None) is not None

    async def session_reset(
        self,
        key: str,
        token: str | None = None,
    ) -> dict:
        self._history[key] = []
        return {"success": True, "payload": {"sessionKey": key}}

    async def session_get_history(
        self,
        key: str,
        limit: int = 100,
        token: str | None = None,
    ) -> list[dict]:
        items = self._history.get(key, [])
        return items[-limit:]

    async def session_clear(
        self,
        key: str,
        token: str | None = None,
    ) -> dict:
        self._history[key] = []
        return {"success": True, "payload": {"sessionKey": key}}

    # ------------------------------------------------------------------ mcp

    async def mcp_list_servers(
        self,
        token: str | None = None,
    ) -> list[dict]:
        return [{**v, "server_code": k} for k, v in sorted(self._mcp.items())]

    async def mcp_get_server(
        self,
        server_code: str,
        token: str | None = None,
    ) -> dict | None:
        entry = self._mcp.get(server_code)
        return None if entry is None else {**entry, "server_code": server_code}

    async def mcp_create_server(
        self,
        config: dict,
        token: str | None = None,
    ) -> dict:
        code = str(config.get("server_code") or config.get("name"))
        self._mcp[code] = dict(config)
        return {**self._mcp[code], "server_code": code}

    async def mcp_update_server(
        self,
        server_code: str,
        patch: dict,
        token: str | None = None,
    ) -> dict:
        self._mcp.setdefault(server_code, {}).update(patch)
        return {**self._mcp[server_code], "server_code": server_code}

    async def mcp_delete_server(
        self,
        server_code: str,
        token: str | None = None,
    ) -> bool:
        return self._mcp.pop(server_code, None) is not None

    async def mcp_start_server(
        self,
        server_code: str,
        token: str | None = None,
    ) -> dict:
        return {"success": True, "error": None, "payload": {"status": "running"}}

    async def mcp_stop_server(
        self,
        server_code: str,
        token: str | None = None,
    ) -> dict:
        return {"success": True, "error": None, "payload": {"status": "stopped"}}

    async def mcp_restart_server(
        self,
        server_code: str,
        token: str | None = None,
    ) -> dict:
        return {"success": True, "error": None, "payload": {"status": "running"}}

    async def mcp_get_server_status(
        self,
        server_code: str,
        token: str | None = None,
    ) -> dict:
        return {"server_code": server_code, "status": "running"}

    async def mcp_list_tools(
        self,
        server_code: str,
        token: str | None = None,
    ) -> list[dict]:
        return []

    async def mcp_call_tool(
        self,
        server_code: str,
        tool_name: str,
        arguments: dict | None = None,
        token: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        return {
            "success": True,
            "error": None,
            "payload": {"tool": tool_name, "server_code": server_code, "content": []},
        }

    async def mcp_list_resources(
        self,
        server_code: str,
        token: str | None = None,
    ) -> list[dict]:
        return []

    async def mcp_read_resource(
        self,
        server_code: str,
        resource_uri: str,
        token: str | None = None,
    ) -> dict:
        return {"success": True, "error": None, "payload": {"uri": resource_uri, "content": ""}}

    async def mcp_list_prompts(
        self,
        server_code: str,
        token: str | None = None,
    ) -> list[dict]:
        return []

    async def mcp_get_prompt(
        self,
        server_code: str,
        prompt_name: str,
        arguments: dict | None = None,
        token: str | None = None,
    ) -> dict:
        return {"success": True, "error": None, "payload": {"prompt": prompt_name}}

    async def mcp_filter_servers(
        self,
        query: str | None = None,
        token: str | None = None,
    ) -> list[dict]:
        servers = await self.mcp_list_servers(token=token)
        if not query:
            return servers
        return [s for s in servers if query.lower() in str(s.get("server_code", "")).lower()]

    async def mcp_apply_server_filter(
        self,
        server_codes: list[str],
        timeout_seconds: int = 30,
        token: str | None = None,
    ) -> dict:
        return {
            "serverCodes": list(server_codes or []),
            "command": [],
            "returnCode": 0,
            "stdout": "",
            "stderr": "",
        }

    # --------------------------------------------------------------- skills

    async def skills_list(
        self,
        token: str | None = None,
    ) -> list[dict]:
        return [{**v, "id": k} for k, v in sorted(self._skills.items())]

    async def skills_get(
        self,
        skill_id: str,
        token: str | None = None,
    ) -> dict | None:
        entry = self._skills.get(skill_id)
        return None if entry is None else {**entry, "id": skill_id}

    async def skills_install(
        self,
        config: dict,
        token: str | None = None,
    ) -> dict:
        skill_id = str(config.get("id") or config.get("name"))
        self._skills[skill_id] = dict(config)
        return {**self._skills[skill_id], "id": skill_id}

    async def skills_uninstall(
        self,
        skill_id: str,
        token: str | None = None,
    ) -> bool:
        return self._skills.pop(skill_id, None) is not None

    async def skills_update(
        self,
        skill_id: str,
        patch: dict,
        token: str | None = None,
    ) -> dict:
        self._skills.setdefault(skill_id, {}).update(patch)
        return {**self._skills[skill_id], "id": skill_id}

    async def skills_enable(
        self,
        skill_id: str,
        token: str | None = None,
    ) -> bool:
        if skill_id in self._skills:
            self._skills[skill_id]["enabled"] = True
            return True
        return False

    async def skills_disable(
        self,
        skill_id: str,
        token: str | None = None,
    ) -> bool:
        if skill_id in self._skills:
            self._skills[skill_id]["enabled"] = False
            return True
        return False

    async def skills_execute(
        self,
        skill_id: str,
        args: dict | None = None,
        token: str | None = None,
    ) -> dict:
        return {
            "success": True,
            "error": None,
            "payload": {"skillId": skill_id, "result": "mock"},
        }

    async def skills_validate(
        self,
        config: dict,
        token: str | None = None,
    ) -> dict:
        return {"valid": True, "errors": []}

    async def skills_discover(
        self,
        source: str,
        token: str | None = None,
    ) -> list[dict]:
        return [{"id": "demo", "source": source}]

    async def skills_sync_symlinks(
        self,
        token: str | None = None,
    ) -> dict:
        return {"ok": True, "total": 0, "created": [], "updated": [], "removed": []}

    async def skills_sync_bindpaths(
        self,
        token: str | None = None,
    ) -> dict:
        return {"ok": True, "total": 0, "created": [], "updated": [], "removed": []}

    async def skills_clean_symlinks(
        self,
        token: str | None = None,
    ) -> dict:
        return {"ok": True, "removed": [], "scanned": 0}

    async def skills_ensure_center(
        self,
        token: str | None = None,
    ) -> dict:
        return {"ok": True, "ensured": []}

    # ----------------------------------------------------------------- cron

    async def cron_list_jobs(
        self,
        token: str | None = None,
    ) -> list[dict]:
        return [{**v, "id": k} for k, v in sorted(self._cron.items())]

    async def cron_get_job(
        self,
        job_id: str,
        token: str | None = None,
    ) -> dict | None:
        entry = self._cron.get(job_id)
        return None if entry is None else {**entry, "id": job_id}

    async def cron_get_status(
        self,
        token: str | None = None,
    ) -> dict:
        return {"running": 0, "total": len(self._cron)}

    async def cron_get_runs(
        self,
        job_id: str,
        limit: int = 20,
        token: str | None = None,
    ) -> list[dict]:
        return []

    async def cron_get_running_jobs(
        self,
        token: str | None = None,
    ) -> list[dict]:
        return []

    async def cron_add_job(
        self,
        job: dict,
        token: str | None = None,
    ) -> dict:
        job_id = str(job.get("id") or job.get("name") or f"job-{len(self._cron) + 1}")
        stored = {**job, "id": job_id}
        self._cron[job_id] = stored
        return stored

    async def cron_update_job(
        self,
        job_id: str,
        patch: dict,
        token: str | None = None,
    ) -> dict:
        self._cron.setdefault(job_id, {}).update(patch)
        return {**self._cron[job_id], "id": job_id}

    async def cron_remove_job(
        self,
        job_id: str,
        token: str | None = None,
    ) -> bool:
        return self._cron.pop(job_id, None) is not None

    async def cron_run_job(
        self,
        job_id: str,
        token: str | None = None,
    ) -> dict:
        return {"success": True, "error": None, "payload": {"jobId": job_id, "runId": "mock-run"}}

    # --------------------------------------------------------------- models

    async def models_list(
        self,
        token: str | None = None,
    ) -> list[dict]:
        return [{"id": "claude-sonnet-4-5", "name": "Claude", "provider": "anthropic"}]

    async def models_list_providers(
        self,
        token: str | None = None,
    ) -> list[dict]:
        return [{"id": "anthropic", "name": "Claude"}]

    # ----------------------------------------------------------------- file

    async def file_upload(
        self,
        path: str,
        content_bytes: bytes | None = None,
        token: str | None = None,
    ) -> dict:
        data = content_bytes or b""
        self._files[path] = data
        return {"path": path, "size": len(data)}

    async def file_read(
        self,
        path: str,
        token: str | None = None,
    ) -> dict:
        return {"path": path, "content": self._files.get(path, b"").decode("utf-8", "replace")}

    async def file_remove(
        self,
        path: str,
        token: str | None = None,
    ) -> bool:
        return self._files.pop(path, None) is not None

    async def file_rmtree(
        self,
        path: str,
        token: str | None = None,
    ) -> bool:
        prefix = path.rstrip("/") + "/"
        removed = [p for p in list(self._files) if p.startswith(prefix)]
        for p in removed:
            self._files.pop(p, None)
        return len(removed) > 0

    async def file_list_dir(
        self,
        path: str,
        token: str | None = None,
    ) -> list[dict]:
        prefix = path.rstrip("/") + "/"
        return [
            {"name": p.removeprefix(prefix), "path": p, "type": "file"}
            for p in sorted(self._files)
            if p.startswith(prefix)
        ]

    # ------------------------------------------------------------- commands

    async def commands_list(
        self,
        scope: str | None = None,
        token: str | None = None,
    ) -> list[dict]:
        return []

    async def commands_get(
        self,
        command_id: str,
        token: str | None = None,
    ) -> dict | None:
        return None

    # ---------------------------------------------------------------- relay

    async def relay_forward_request(
        self,
        method: str,
        params: dict | None = None,
        request_id: str | None = None,
        token: str | None = None,
    ) -> dict:
        return {"success": True, "error": None, "payload": {"method": method}}

    async def relay_forward_raw_frame(
        self,
        frame: dict,
        token: str | None = None,
    ) -> dict:
        return {"success": True, "error": None, "payload": {"ack": True}}


__all__ = ["LocalClaudeCodePluginImpl"]
