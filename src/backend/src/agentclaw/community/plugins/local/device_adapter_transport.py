"""InMemoryDeviceAdapterTransport -- local impl of DeviceAdapterTransport.

A stateful in-memory emulation of a bot's engine cron adapter. Used by
injection tests (and local boots without a live adapter) so the *real*
``CronRelayService`` runs end-to-end: device resolution, permission
checks and response shaping all execute against production code, with
only the HTTP boundary replaced by this fake.

State is partitioned per device (keyed off ``conn_info``) so a relay
fanning out across multiple bots sees an isolated cron store per bot,
mirroring the real one-adapter-per-bot topology. The injector binds
this as a singleton, so within one test all calls share state; each
test gets a fresh injector and therefore an empty store.

Response shapes mirror the real adapter's envelopes. ``bot_id`` /
``bot_name`` are intentionally absent — the relay adds those.
"""
from __future__ import annotations

from typing import Any, Optional

from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
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


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.SIMULATOR,
    rationale="stateful in-memory cron adapter; relay runs end-to-end over it",
)
class InMemoryDeviceAdapterTransport(MockSeam, DeviceAdapterTransport):
    """In-memory cron adapter answering ``/api/cron*`` paths."""

    def __init__(self) -> None:
        # device_key -> {cron_id -> cron item}
        self._crons: dict[str, dict[str, dict[str, Any]]] = {}
        self._seq = 0

    # ── helpers ──────────────────────────────────────────────────────────

    def _store(self, conn_info: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return self._crons.setdefault(_device_key(conn_info), {})

    def _new_id(self) -> str:
        self._seq += 1
        return f"cron_{self._seq:03d}"

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
            },
            "session_target": f"session:{cron_id}:user:default",
            "state": self._default_state(),
            "notify": body.get("notify") or {"enabled": False, "user_ids": []},
            "created_at_ms": _BASE_MS,
            "updated_at_ms": _BASE_MS,
        }

    # ── transport ────────────────────────────────────────────────────────

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
                    if "command" in body:
                        item["payload"]["message"] = body["command"]
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

        return {"success": False, "message": f"unhandled path {path}", "error_code": 404}
