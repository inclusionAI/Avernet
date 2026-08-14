"""Stateful LocalBaas — an in-memory BaaS double installed at the HTTP seam.

The DI world's one missing boundary: BaaS write paths have no local plugin
(``LocalHttpClient`` raises on any unstubbed call — see
``docs/singlebox-eval/findings/devices-baas-write-paths-unmocked.md``), and the
static per-test stubs (``_install_baas`` in the endpoint cases) cannot express
multi-workflow, multi-record flows. This double is installed with MockSeam
``set_override`` on the BaaS-qualified ``HttpClient``, so the **real**
``BaasService`` request-building/response-parsing code runs against it — more
production code live than a duck-typed service double, and the same seam every
existing endpoint test uses.

It models what the publish pipeline observes of BaaS:

* bots with per-bot workflow timelines; globally monotonic workflow ids;
* workflow status controllable from the test (``ACTIVE`` at issue →
  ``finish_workflow``/``finish_all`` flip to SUCCESS/FAILED — the BaaS-wait
  states the progress poll drives);
* bot deletion (``delete_bot``): a subsequent update replies 404 with the
  BaaS server's real ``{"detail": {"error_code": "BOT_NOT_FOUND"}}`` shape
  (``secbaas`` ``domain_exception_handler``), and the bot's publishes listing
  replies 404 (mapped to ``[]`` by ``BaasService.list_bot_publishes``);
* a journal of mutating calls for issue-count assertions.

Wire shapes mirror the real server: the ``{code, message, data}`` envelope for
success bodies, ``/api/v1/bots`` create, ``/{uuid}/update`` update,
``/api/v1/publishes/{id}/progress`` progress, ``/api/v1/bots/{uuid}/publishes``
timelines, the ``/devices`` provider probe (teclaw), and the teclaw
ws-info/http-info connection endpoints.
"""
from __future__ import annotations

from typing import Annotated, Any

import httpx

from agentclaw.community.plugin_api.http_client import QUALIFIER_BAAS, HttpClient


def _ok(data: Any, path: str) -> httpx.Response:
    return httpx.Response(
        200, json={"code": 0, "message": "ok", "data": data},
        request=httpx.Request("GET", f"http://local-baas{path}"),
    )


def _not_found(path: str, detail: dict | None = None) -> httpx.Response:
    return httpx.Response(
        404, json={"detail": detail or {}},
        request=httpx.Request("GET", f"http://local-baas{path}"),
    )


class LocalBaas:
    """Stateful in-memory BaaS. Install with :meth:`install`."""

    def __init__(self) -> None:
        self._next_wid = 9000
        self._next_bot = 0
        self.bots: dict[str, list[int]] = {}      # bot_uuid -> [workflow ids]
        self.workflows: dict[int, dict] = {}      # wid -> workflow row
        self.journal: list[tuple[str, str]] = []  # (kind, target) of mutations
        self._bot_status: dict[str, str] = {}     # bot_uuid -> get_bot status override

    # ── test controls ────────────────────────────────────────────────────
    def finish_workflow(self, wid: int, status: str = "SUCCESS") -> None:
        self.workflows[wid]["status"] = status

    def set_bot_status(self, bot_uuid: str, status: str) -> None:
        """Override what ``GET /bots/{uuid}`` reports for this bot (default is
        ``ACTIVE``). Lets a test drive a bot to a not-live state (``FAILED`` /
        ``STOPPED``) so the online reuse decision reaches the provider-aware
        ``RETIRE_THEN_FIRST_RELEASE`` / ``FIRST_RELEASE`` cleanup path instead of
        always ``UPGRADE``."""
        self._bot_status[bot_uuid] = status

    def finish_all(self, status: str = "SUCCESS") -> None:
        """Flip every non-terminal workflow to ``status``."""
        for w in self.workflows.values():
            if w["status"] == "ACTIVE":
                w["status"] = status

    def delete_bot(self, bot_uuid: str) -> None:
        """The bot is gone server-side: updates 404 BOT_NOT_FOUND; its
        publishes listing 404s (client maps to [])."""
        del self.bots[bot_uuid]

    # ── inspection ───────────────────────────────────────────────────────
    def bot_count(self) -> int:
        return len(self.bots)

    def workflows_of(self, bot_uuid: str) -> list[dict]:
        return [self.workflows[w] for w in self.bots.get(bot_uuid, [])]

    def creates(self) -> list[str]:
        return [t for k, t in self.journal if k == "create"]

    def updates_of(self, bot_uuid: str) -> int:
        return sum(1 for k, t in self.journal if k == "update" and t == bot_uuid)

    def latest_workflow(self, bot_uuid: str) -> dict:
        return self.workflows[self.bots[bot_uuid][-1]]

    # ── internals ────────────────────────────────────────────────────────
    def _issue(self, bot_uuid: str, publish_type: str) -> int:
        self._next_wid += 1
        wid = self._next_wid
        self.workflows[wid] = {
            "id": wid, "bot_id": bot_uuid, "publish_type": publish_type,
            "status": "ACTIVE", "gmt_create": "t",
        }
        self.bots.setdefault(bot_uuid, []).append(wid)
        return wid

    def _get(self, path: str, **_kw) -> httpx.Response:
        parts = path.strip("/").split("/")
        if path.endswith("/progress") and "/publishes/" in path:
            wid = int(parts[-2])
            w = self.workflows.get(wid)
            if w is None:
                return _not_found(path)
            return _ok(
                {"publish_id": wid, "status": w["status"], "device_details": [],
                 "overall_progress": {}, "failed_devices": []},
                path,
            )
        if path.endswith("/publishes") and "/bots/" in path:
            bot_uuid = parts[-2]
            if bot_uuid not in self.bots:
                return _not_found(path)  # client maps 404 -> []
            rows = [dict(self.workflows[w]) for w in self.bots[bot_uuid]]
            rows.reverse()  # newest first, like the real endpoint
            return _ok(rows, path)
        if "/devices" in path:
            # Per-bot device listing (list_devices_by_bot_uuid consumers) —
            # reports a teclaw-provider device for the probed bot.
            bot_uuid = parts[-2] if len(parts) >= 2 else ""
            return _ok(
                [{"items": [{"provider_type": "TECLAW", "device_uuid": bot_uuid}]}],
                path,
            )
        if "/ws-info" in path or "/http-info" in path:
            return _ok(
                {"ws_url": "ws://localhost:8890/api/openclaw/ws",
                 "http_url": "http://localhost:8890/invoke-http",
                 "token": "test-token", "target": "t", "expires_at": 0},
                path,
            )
        if len(parts) >= 2 and parts[-2] == "bots":
            # GET /api/v1/bots/{bot_uuid} — the get_bot status read the online
            # reuse decision uses. A bot still tracked here is live (ACTIVE);
            # a destroyed/unknown bot is gone (404), which the decision treats
            # as "create fresh".
            bot_uuid = parts[-1]
            if bot_uuid not in self.bots:
                return _not_found(path)
            status = self._bot_status.get(bot_uuid, "ACTIVE")
            return _ok({"bot_uuid": bot_uuid, "status": status}, path)
        return _ok({}, path)

    def _post(self, path: str, **_kw) -> httpx.Response:
        parts = path.strip("/").split("/")
        if path.endswith("/api/v1/bots"):
            self._next_bot += 1
            bot_uuid = f"BOT-{self._next_bot}"
            self.bots.setdefault(bot_uuid, [])
            wid = self._issue(bot_uuid, "CREATE")
            self.journal.append(("create", bot_uuid))
            return _ok({"bot_uuid": bot_uuid, "publish_id": wid}, path)
        if path.endswith("/update"):
            bot_uuid = parts[-2]
            if bot_uuid not in self.bots:
                # The real server shape: DomainError(BotNotFoundError) → 404
                # {"detail": {"error_code": "BOT_NOT_FOUND", ...}}.
                return _not_found(path, {
                    "error_code": "BOT_NOT_FOUND",
                    "message": f"Bot not found: {bot_uuid}",
                })
            wid = self._issue(bot_uuid, "UPDATE")
            self.journal.append(("update", bot_uuid))
            return _ok({"bot_uuid": bot_uuid, "publish_id": wid}, path)
        if path.endswith("/approve"):
            return _ok({"status": "APPROVED"}, path)
        if path.endswith("/destroy") or path.endswith("/stop"):
            bot_uuid = parts[-2]
            # The real server creates a DESTROY publish and returns its workflow
            # id; retire_superseded_bot requires that id to confirm the destroy
            # was initiated. Mint one (not polled by any test) and remove the bot.
            self._next_wid += 1
            wid = self._next_wid
            self.journal.append(("destroy", bot_uuid))
            self.bots.pop(bot_uuid, None)
            self._bot_status.pop(bot_uuid, None)
            return _ok({"bot_uuid": bot_uuid, "publish_id": wid}, path)
        return _ok({}, path)

    def install(self, world) -> "LocalBaas":
        client = world.get(Annotated[HttpClient, QUALIFIER_BAAS])
        client.set_override("get", self._get)
        client.set_override("post", self._post)
        return self
