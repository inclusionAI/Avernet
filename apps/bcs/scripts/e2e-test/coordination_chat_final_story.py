#!/usr/bin/env python3
"""Chat-final coordination claim channel, end to end over live HTTP.

Provider manager bots have no agent-event tool_call_end echo, so their only
provenance carrier for a coordination frame is the terminal chat reply
text. This story drives the real settlement chain into the channel:

- a mcporter-surface provider (the legacy name-free carrier shape the
  channel's tool allowlist tolerates) contributes the manager bot and a
  caller-enabled worker, so the session is relay-capable;
- the mock Human leases the plain caller group and speaks over the
  workbench WS: the relay ledger captures the session's human ingress
  and BCS chat.sends the manager bot over the provider wire;
- the manager bot's terminal final replies with a v2 stored-receipt frame
  for an intent that was never stored: the channel claims live (G1 gated
  on the capture), the shared claim core refuses the unknown intent
  downstream and the settlement absorbs it as a silent no-op — exactly
  the specified behavior for LLM-mediated carrier noise;
- the fenced variants pin the negative gates: without the magic in the
  reply text and without a caller relay capture the stored-intent shape
  never dispatches anything toward bots.
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
import urllib.parse

from caller_identity_story import WorkbenchSocket, segment


# CI hosts carry no proxy; developer Macs often carry a system SOCKS proxy
# that silently reroutes loopback HTTP. The story only ever talks to
# loopback services, so pin a no-proxy opener.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ChatFinalStory:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.provider = None
        self.manager = None
        self.worker = None
        self.group_id = None
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
        try:
            with _OPENER.open(req, timeout=30) as response:
                payload = response.read().decode()
        except urllib.error.HTTPError:
            raise
        return json.loads(payload) if payload else {}

    @staticmethod
    def _read(response):
        return response.read().decode()

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
        # The LEGACY tool surface: mcporter tolerates name-free carriers —
        # the chat-final channel passes Value::Null as the tool data.
        self.provider = self.post("/providers", {
            "name": "Chat-final coordination provider",
            "webhook_url": self.args.mock_url + "/provider/webhook",
            "auth": {"mode": "static_bearer"},
            "protocol_version": "2.0",
            "coordination": {"mode": "mcporter_mcp", "mcp_server": "bcs",
                                "mcporter_command": "mcporter"},
        })
        headers = {"Authorization": "Bearer " + self.provider["provider_admin_token"]}
        self.manager = self.post(f"/providers/{segment(self.provider['provider_id'])}/bots", {
            "name": "Chat-final manager", "summary": "Legacy-surface manager bot",
            "owners": [self.args.human],
            "provider_bot_ref": "chat-final-manager-" + uuid.uuid4().hex,
        }, headers=headers)
        self.worker = self.post(f"/providers/{segment(self.provider['provider_id'])}/bots", {
            "name": "Chat-final worker", "summary": "Caller-enabled relay-capable worker",
            "owners": [self.args.human],
            "provider_bot_ref": "chat-final-worker-" + uuid.uuid4().hex,
            "domains": ["caller"], "skills": ["dispatch"], "scopes": ["local"],
        }, headers=headers)
        for bot in (self.manager, self.worker):
            self.request(f"/bots/{segment(bot['bot_uuid'])}/visibility",
                         {"visibility": "public"}, method="PUT")
        # Worker opts into caller: the session becomes relay-capable and the
        # plain group turns into a caller group (lease-fenced human speech).
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
        # The session-context chat.send stays open (the story only answers
        # the human turn's final): give the queue parallel room so the
        # human turn delivers without waiting for that close.
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

    def speak_to_manager_and_capture(self, socket_client) -> str:
        """Lease, speak, and capture: the human turn to the manager bot both
        records the relay ledger capture (caller-enabled worker in the group)
        and drives the manager's ordinary chat.send over the provider wire."""
        socket_client.send({"type": "req", "id": "cf-lease", "method": "lease.acquire",
                            "params": {"groupId": self.group_id}})
        acquired = socket_client.receive("cf-lease")
        assert acquired.get("ok") is True, acquired
        lease = acquired["payload"]
        self.post("/control/provider/clear", mock=True)
        socket_client.send({"type": "req", "id": "cf-speak", "method": "chat.send",
                            "params": {"group_id": self.group_id,
                                       "session_id": self.session_id,
                                       "bot_id": "human_" + self.args.human,
                                       "message": "please coordinate the handover",
                                       "mentions": [self.manager["bot_uuid"]],
                                       "leaseId": lease["leaseId"],
                                       "leaseEpoch": lease["epoch"]}})
        admitted = socket_client.receive("cf-speak")
        assert admitted.get("ok") is True, admitted
        rides = self.wait("manager chat.send over the provider wire",
                          lambda: self.request("/control/provider/requests", mock=True)["requests"],
                          lambda items: any(item["body"].get("method") == "chat.send"
                                            and "please coordinate the handover" in json.dumps(item["body"])
                                            for item in items))
        socket_client.send({"type": "req", "id": "cf-lease-release", "method": "lease.release",
                            "params": {"groupId": self.group_id,
                                       "leaseId": lease["leaseId"],
                                       "leaseEpoch": lease["epoch"]}})
        socket_client.receive("cf-lease-release")
        return next(item["body"]["id"] for item in rides
                    if item["body"].get("method") == "chat.send"
                    and "please coordinate the handover" in json.dumps(item["body"]))

    def reply_chat_final(self, run_id: str, text: str) -> dict:
        """The manager bot's terminal chat.final — the only provenance its
        provider harness ever reports."""
        return self.post("/bot/events", {
            "run_id": run_id, "state": "final", "group_id": self.group_id,
            "bcs_session_id": self.session_id,
            "message": {"content": [{"type": "text", "text": text}]},
        }, headers={"X-BCN-Provider-Id": self.provider["provider_id"],
                    "Authorization": "Bearer " + self.manager["bot_runtime_token"]},
            status=(200, 409))

    def assert_no_new_bot_dispatch(self) -> None:
        requests = self.request("/control/provider/requests", mock=True)["requests"]
        task_or_protected = [item for item in requests
                             if (item["body"].get("extensions") or {}).get("caller_context")
                             or (item["body"].get("method") == "chat.send"
                                 and "please coordinate the handover" not in json.dumps(item["body"]))]
        assert not task_or_protected, f"no dispatch may leave the claim: {task_or_protected}"

    def run(self) -> None:
        self.setup()
        group = self.post("/groups", {
            "label": "Chat-final coordination claim",
            "driver_bot": self.manager["bot_uuid"],
            "participants": [{"bot_uuid": self.manager["bot_uuid"]},
                             {"bot_uuid": self.worker["bot_uuid"]},
                             {"bot_uuid": "human_" + self.args.human}],
        })
        self.group_id, self.session_id = group["id"], group["session_id"]
        assert self.session_id, group
        socket_client = WorkbenchSocket(self.args.base_url, self.args.human,
                                         self.group_id, self.session_id)
        try:
            connect = socket_client.receive(socket_client.connect_id)
            assert connect.get("ok") is True, connect
            run_id = self.speak_to_manager_and_capture(socket_client)

            unknown_intent = "bcs_intent_" + "f" * 32
            v2_frame = json.dumps({
                "__bcs_coordination__": True, "v": 2, "tool": "bcs_assign_task",
                "intent_id": unknown_intent, "status": "stored",
            })
            settled = self.wait("chat final settlement",
                                lambda: self.reply_chat_final(run_id, v2_frame),
                                lambda body: body.get("error") != "transport_conflict")
            assert not settled.get("error"), settled
            self.assert_no_new_bot_dispatch()
            print("  chat-final claim of an unknown intent settles as a silent no-op", flush=True)

            # Negative gate: no magic in the text — no scan at all. The
            # same live run settles again (idempotent terminal intake).
            plain = self.reply_chat_final(run_id, "普通最终回复,没有协调帧")
            assert not plain.get("error"), plain
            self.assert_no_new_bot_dispatch()
            print("  plain final text never enters the channel", flush=True)
        finally:
            socket_client.close()
            self.restore_delivery_policy()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--mock-url", required=True)
    parser.add_argument("--human", required=True)
    args = parser.parse_args()
    ChatFinalStory(args).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
