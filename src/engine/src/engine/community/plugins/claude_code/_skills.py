"""_SkillsPortMixin — skill lifecycle + sync (relay RPC).

All skill ops are forwarded to the relay (``skills.*``). The corp
``engines/claude_code/skills.py`` also performs local filesystem rsync/symlink
work for ``sync_symlinks`` / ``ensure_center``; that local-OS work moves into
the adapter (or a local plugin) in the ACL split, and the community port only
owns the relay RPC shape. Returning the raw ``{success, payload|error}`` /
dict / list / bool shapes the adapter consumes.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("claude-code-community-port")


def _resp_dict(resp: Any) -> dict[str, Any]:
    if resp.ok:
        return {"success": True, "payload": resp.payload or {}}
    err = resp.error
    return {"success": False,
            "error": {"code": err.code if err else "UNKNOWN",
                      "message": err.message if err else "Unknown error"}}


class _SkillsPortMixin:
    """Domain mixin: skills.{list,get,install,uninstall,update,enable,disable,
    execute,validate,discover,sync_symlinks,sync_bindpaths,clean_symlinks,
    ensure_center}."""

    async def skills_list(self, token: str | None = None) -> list[dict]:
        resp = await (await self._relay()).send_request("skills.list", {})
        if not resp.ok:
            return []
        payload = resp.payload or {}
        skills = payload.get("skills", []) if isinstance(payload, dict) else payload
        return [s for s in skills if isinstance(s, dict)] if isinstance(skills, list) else []

    async def skills_get(self, skill_id: str,
                         token: str | None = None) -> dict | None:
        resp = await (await self._relay()).send_request(
            "skills.get", {"skillId": skill_id})
        if not resp.ok or not resp.payload:
            return None
        return resp.payload if isinstance(resp.payload, dict) else None

    async def skills_install(self, config: dict,
                             token: str | None = None) -> dict:
        resp = await (await self._relay()).send_request("skills.install", config)
        if not resp.ok:
            raise RuntimeError(
                f"skills.install failed: "
                f"{resp.error.message if resp.error else 'unknown'}")
        return resp.payload if isinstance(resp.payload, dict) else config

    async def skills_uninstall(self, skill_id: str,
                               token: str | None = None) -> bool:
        resp = await (await self._relay()).send_request(
            "skills.uninstall", {"skillId": skill_id})
        if not resp.ok:
            return False
        payload = resp.payload
        return bool(payload.get("removed") if isinstance(payload, dict) else True)

    async def skills_update(self, skill_id: str, patch: dict,
                            token: str | None = None) -> dict:
        params = dict(patch)
        params["skillId"] = skill_id
        resp = await (await self._relay()).send_request("skills.update", params)
        if not resp.ok:
            raise RuntimeError(
                f"skills.update failed: "
                f"{resp.error.message if resp.error else 'unknown'}")
        return resp.payload if isinstance(resp.payload, dict) else params

    async def skills_enable(self, skill_id: str,
                            token: str | None = None) -> bool:
        resp = await (await self._relay()).send_request(
            "skills.update", {"skillId": skill_id, "enabled": True})
        return bool(resp.ok)

    async def skills_disable(self, skill_id: str,
                             token: str | None = None) -> bool:
        resp = await (await self._relay()).send_request(
            "skills.update", {"skillId": skill_id, "enabled": False})
        return bool(resp.ok)

    async def skills_execute(self, skill_id: str, args: dict | None = None,
                             token: str | None = None) -> dict:
        params: dict[str, Any] = {"skillId": skill_id, "args": args or {}}
        resp = await (await self._relay()).send_request("skills.execute", params)
        return _resp_dict(resp)

    async def skills_validate(self, config: dict,
                              token: str | None = None) -> dict:
        resp = await (await self._relay()).send_request("skills.validate", config)
        if resp.ok:
            return resp.payload if isinstance(resp.payload, dict) else {}
        return _resp_dict(resp)

    async def skills_discover(self, source: str,
                              token: str | None = None) -> list[dict]:
        resp = await (await self._relay()).send_request(
            "skills.discover", {"source": source})
        if not resp.ok:
            return []
        payload = resp.payload or {}
        skills = payload.get("skills", []) if isinstance(payload, dict) else payload
        return [s for s in skills if isinstance(s, dict)] if isinstance(skills, list) else []

    async def _skills_passthrough(self, method: str, params: dict[str, Any]) -> dict:
        resp = await (await self._relay()).send_request(method, params)
        if resp.ok:
            return resp.payload if isinstance(resp.payload, dict) else {"success": True}
        return _resp_dict(resp)

    async def skills_sync_symlinks(self, token: str | None = None) -> dict:
        return await self._skills_passthrough("skills.sync_symlinks", {})

    async def skills_sync_bindpaths(self, token: str | None = None) -> dict:
        return await self._skills_passthrough("skills.sync_bindpaths", {})

    async def skills_clean_symlinks(self, token: str | None = None) -> dict:
        return await self._skills_passthrough("skills.clean_symlinks", {})

    async def skills_ensure_center(self, token: str | None = None) -> dict:
        return await self._skills_passthrough("skills.ensure_center", {})
