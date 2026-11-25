#!/usr/bin/env python3
"""Owner-mode master delegation through the session relay capture (spec L62).

The session-capture fallback additivity story, end to end over live HTTP:

- the manager bot runs in OWNER mode (never opts into caller) while the
  worker bot is caller-enabled, so the session is relay-capable — exactly
  the "master owner-mode, worker caller-ON" chain spec L62 describes;
- the mock Human leases the plain caller group and speaks over the
  workbench WS: the relay ledger captures the session's human ingress;
- the manager's live run — the session-context hand-off chat.send the group
  creation itself drives over the provider wire — replies with a terminal
  final @mentioning the caller worker. The routed Send target carries no
  caller marks and no orchestration lineage, so `prepare_assignment` falls
  through to the session-capture fallback and mints the delegation from the
  live capture. The minted row's protected delegate dispatch still requires
  a registered root lineage (caller_dispatch_pipeline/delegate.rs) which an
  owner-mode parent never has, so the end state stays fail-closed: the
  dispatch converges not-sent and the closed-world gate suppresses any
  ordinary rebroadcast toward the caller-enabled worker. The story pins
  both boundaries: silence without the capture, and mint-side silence with
  it until the dispatch side of spec L62 lands them
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import uuid

from caller_identity_story import WorkbenchSocket, segment


# CI hosts carry no proxy; developer Macs often carry a system SOCKS proxy
# that silently reroutes loopback HTTP. The story only ever talks to
# loopback services, so pin a no-proxy opener.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# The terminal final's @mention is what routes a Send target toward the
# caller worker; the fenced and the mint legs share the shape so the only
# difference is the session's relay capture state.
FENCED_FINAL_TEXT = "@CallerWorker handle the pre-capture request"
OWNER_FINAL_TEXT = "@CallerWorker please own the human's request end to end"


class OwnerModeRelayStory:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.provider = None
        self.manager = None
        self.worker = None
        self.group_id = None
        self.sister_id = None
        self.session_id = None
        self._delivery_policy_restore = None

    # ---------------------------------------------------------------- http

    def request(self, path, body=None, *, method="GET", mock=False, headers=None):
        url = (self.args.mock_url if mock else self.args.base_url) + path
        all_headers = {} if mock else {"X-Mock-User-Id": self.args.human}
        all_headers.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            all_headers.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
        with _OPENER.open(req, timeout=30) as response:
            payload = response.read().decode()
        return json.loads(payload) if payload else {}

    def post(self, path, body=None, **kwargs):
        return self.request(path, body, method="POST", **kwargs)

    def wait(self, description, read, predicate, attempts=120, delay=0.5):
        for _ in range(attempts):
            value = read()
            if predicate(value):
                return value
            time.sleep(delay)
        raise AssertionError(f"timed out waiting for: {description}")

    # --------------------------------------------------------------- setup

    def setup(self) -> None:
        self.post("/control/reset", mock=True)
        self.post("/me/ensure-human")
        # The same legacy mcporter surface the chat-final channel tolerates;
        # the manager's terminal reply is its only provenance carrier.
        self.provider = self.post("/providers", {
            "name": "Owner-mode relay provider",
            "webhook_url": self.args.mock_url + "/provider/webhook",
            "auth": {"mode": "static_bearer"},
            "protocol_version": "2.0",
            "coordination": {"mode": "mcporter_mcp", "mcp_server": "bcs",
                                "mcporter_command": "mcporter"},
        })
        headers = {"Authorization": "Bearer " + self.provider["provider_admin_token"]}
        # Owner mode = the manager never opts into caller; the worker does.
        self.manager = self.post(f"/providers/{segment(self.provider['provider_id'])}/bots", {
            "name": "OwnerMaster", "summary": "Owner-mode master bot",
            "owners": [self.args.human],
            "provider_bot_ref": "owner-master-" + uuid.uuid4().hex,
        }, headers=headers)
        self.worker = self.post(f"/providers/{segment(self.provider['provider_id'])}/bots", {
            "name": "CallerWorker", "summary": "Caller-enabled relay worker",
            "owners": [self.args.human],
            # No-space name: the final reply's @mention resolves by bot name.
            "provider_bot_ref": "caller-worker-" + uuid.uuid4().hex,
            "domains": ["caller"], "skills": ["dispatch"], "scopes": ["local"],
        }, headers=headers)
        for bot in (self.manager, self.worker):
            self.request(f"/bots/{segment(bot['bot_uuid'])}/visibility",
                         {"visibility": "public"}, method="PUT")
        # Worker opts into caller: the session becomes relay-capable and the
        # human speech in this session lands in the relay ledger.
        path = f"/providers/{segment(self.provider['provider_id'])}/bots/{segment(self.worker['provider_bot_ref'])}/caller"
        applied = self.request(path, {"snapshot": {"source_revision": 1,
            "caller_identity": {"enabled": True}}}, method="PATCH", headers=headers)
        assert applied["sync_status"] == "applied", applied
        self.enable_group_queue_policy()

    def enable_group_queue_policy(self) -> None:
        current = self.request("/admin/message-delivery/policy")
        self._delivery_policy_restore = {"version": current["version"],
                                         "policy": current["policy"]}
        enforced = json.loads(json.dumps(current["policy"]))
        enforced["flow_enabled"]["group"] = True
        enforced["flow_enabled"]["system"] = True
        enforced["defaults"]["mode"] = "enforce"
        # Keep the session-context chat.send run open while the final that
        # answers it routes toward the worker.
        enforced["defaults"]["max_running"] = 8
        body = {"expected_version": current["version"], "policy": enforced}
        updated = self.request("/admin/message-delivery/policy", body, method="PUT")
        assert updated["version"] == current["version"] + 1, updated

    def restore_delivery_policy(self) -> None:
        if not self._delivery_policy_restore:
            return
        try:
            self.request("/admin/message-delivery/policy",
                         {"expected_version": self._delivery_policy_restore["version"] + 1,
                          "policy": self._delivery_policy_restore["policy"]}, method="PUT")
        except Exception as error:  # noqa: BLE001 — restore is best-effort
            print(f"  policy restore skipped: {error}", flush=True)

    # ----------------------------------------------------------------- run

    def manager_live_run(self, expect_after: str | None = None) -> str:
        """The session-context hand-off chat.send the group creation itself
        sends is the manager's live managed run the final answers."""
        def to_manager(item):
            body = item["body"]
            return (body.get("method") == "chat.send"
                    and (body.get("to_bot") or {}).get("provider_bot_ref", "").startswith("owner-master")
                    and (expect_after is None or body.get("id") != expect_after))
        self.wait("manager session-context chat.send over the provider wire",
                  lambda: self.request("/control/provider/requests", mock=True)["requests"],
                  lambda items: any(to_manager(item) for item in items), attempts=240)
        rides = self.request("/control/provider/requests", mock=True)["requests"]
        return next(item["body"]["id"] for item in rides if to_manager(item))

    def speak_as_human(self, socket_client) -> None:
        """Lease and speak: the human turn rides queue admission and the
        relay ledger captures the session's human ingress (the capture the
        owner-mode fallback mints from)."""
        socket_client.send({"type": "req", "id": "om-lease", "method": "lease.acquire",
                            "params": {"groupId": self.group_id}})
        acquired = socket_client.receive("om-lease")
        assert acquired.get("ok") is True, acquired
        lease = acquired["payload"]
        socket_client.send({"type": "req", "id": "om-speak", "method": "chat.send",
                            "params": {"group_id": self.group_id,
                                       "session_id": self.session_id,
                                       "bot_id": "human_" + self.args.human,
                                       "message": "coordinate this request for me",
                                       "mentions": [self.manager["bot_uuid"]],
                                       "leaseId": lease["leaseId"],
                                       "leaseEpoch": lease["epoch"]}})
        admitted = socket_client.receive("om-speak")
        assert admitted.get("ok") is True, admitted
        socket_client.send({"type": "req", "id": "om-lease-release", "method": "lease.release",
                            "params": {"groupId": self.group_id,
                                       "leaseId": lease["leaseId"],
                                       "leaseEpoch": lease["epoch"]}})
        socket_client.receive("om-lease-release")

    def reply_chat_final(self, run_id: str, text: str,
                         group_id: str | None = None, session_id: str | None = None) -> dict:
        """The manager bot's terminal chat.final in the provider callback
        wire shape. The intake may reject during transport negotiation; the
        caller retries that explicit conflict."""
        for _ in range(6):
            response = self.post("/bot/events", {
                "run_id": run_id, "state": "final",
                "group_id": group_id or self.group_id,
                "bcs_session_id": session_id or self.session_id,
                "message": {"text": text},
            }, headers={"X-BCN-Provider-Id": self.provider["provider_id"],
                        "Authorization": "Bearer " + self.manager["bot_runtime_token"]})
            if response.get("error") != "transport_conflict":
                return response
            time.sleep(1)
        return response

    def run(self) -> None:
        self.setup()
        participants = [{"bot_uuid": self.manager["bot_uuid"]},
                        {"bot_uuid": self.worker["bot_uuid"]},
                        {"bot_uuid": "human_" + self.args.human}]
        group = self.post("/groups", {
            "label": "Owner-mode master relay",
            "driver_bot": self.manager["bot_uuid"],
            "participants": participants,
        })
        self.group_id, self.session_id = group["id"], group["session_id"]
        assert self.session_id, group
        socket_client = WorkbenchSocket(self.args.base_url, self.args.human,
                                        self.group_id, self.session_id)
        try:
            connect = socket_client.receive(socket_client.connect_id)
            assert connect.get("ok") is True, connect
            first_run = self.manager_live_run()

            # Negative fence in a sister group whose session NEVER hears the
            # human: the relay ledger holds no live capture there, so the
            # owner-mode master's @worker final must stay closed — the
            # fallback returns before the capture gate and no dispatch may
            # leave toward the caller worker (fail-closed, nothing rides).
            sister = self.post("/groups", {
                "label": "Owner-mode relay sister fence",
                "driver_bot": self.manager["bot_uuid"],
                "participants": participants,
            })
            self.sister_id = sister["id"]
            fenced_run = self.manager_live_run(expect_after=first_run)
            fenced = self.reply_chat_final(fenced_run, FENCED_FINAL_TEXT,
                                           group_id=self.sister_id, session_id=sister["session_id"])
            assert not fenced.get("error"), fenced
            time.sleep(2)
            early = self.request("/control/provider/requests", mock=True)["requests"]
            assert not any(
                (item["body"].get("to_bot") or {}).get("provider_bot_ref", "").startswith("caller-worker")
                for item in early), "no dispatch may ride before the session captures identity"

            # The human speaks in the FIRST group's session: the relay ledger
            # captures the human's ingress (the hand-off run predates the
            # speech, so it carries no caller marks and no registered
            # lineage — the exact owner-mode shape the fallback serves).
            self.speak_as_human(socket_client)
            self.post("/control/provider/clear", mock=True)

            # The owner-mode master's terminal final @mentions the caller
            # worker: the routed Send target has no marks and no lineage, so
            # the session-capture fallback mints the delegation and the
            # dispatch rides the protected wire toward the caller worker.
            # With the capture live the master's @worker final drives the
            # session-capture fallback: prepare_assignment mints the
            # delegation onto the routed Send target. The delegate dispatch
            # still requires the owner-mode parent to carry a registered
            # root lineage (caller_dispatch_pipeline/delegate.rs), so the
            # protected ride converges not-sent before the network and the
            # caller worker's closed-world gate suppresses any ordinary
            # rebroadcast: mint-side coverage with a fail-closed end state.
            settled = self.wait("owner-mode final settlement",
                                lambda: self.reply_chat_final(first_run, OWNER_FINAL_TEXT),
                                lambda body: body.get("error") != "transport_conflict")
            assert not settled.get("error"), settled
            time.sleep(2)
            after = self.request("/control/provider/requests", mock=True)["requests"]
            assert not any(
                (item["body"].get("to_bot") or {}).get("provider_bot_ref", "").startswith("caller-worker")
                for item in after), "a minted delegation must not leak through the lineage-gated dispatch"
            print("  owner-mode master minted the delegation; the lineage-gated dispatch stayed fail-closed", flush=True)
        finally:
            socket_client.close()
            self.restore_delivery_policy()
            # Keep the human's active-group footprint flat: repeated story
            # iterations share one DB, and the group-create validation caps
            # any actor at 20 active groups.
            for group_id in (self.group_id, self.sister_id):
                if group_id and self.manager:
                    try:
                        self.request(f"/groups/{segment(group_id)}"
                                     f"?bot_id={segment(self.manager['bot_uuid'])}", method="DELETE")
                    except Exception as error:  # noqa: BLE001 — cleanup is best-effort
                        print(f"  group cleanup skipped: {error}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--mock-url", required=True)
    parser.add_argument("--human", required=True)
    args = parser.parse_args()
    OwnerModeRelayStory(args).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
