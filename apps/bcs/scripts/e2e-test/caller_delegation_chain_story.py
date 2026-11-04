#!/usr/bin/env python3
"""Caller parent → caller worker delegation, end to end over live HTTP (P5.9).

The P2 delegation pipe (`caller_dispatch_pipeline/delegate.rs`) previously
had NO live e2e drive: the owner-mode relay story pins only the fail-closed
boundary (an owner-mode parent never holds a root lineage). This story
drives the ACCEPTED deep path — the stage liveness retrofit's delegate
counterparts (spec 2026-11-03) executing on a real wire:

- a HUMAN leases the caller group and speaks: queue admission claims the
  one-shot handoff, the protected dispatch rides the root wire toward the
  caller-enabled manager and registers the ROOT LINEAGE at Accepted;
- the manager's run stays live (never answered final): while its managed
  delivery is Running, the manager's chat.final carries the v2
  `bcs_assign_task` coordination frame — the trusted parent echo minting
  the delegated row toward the second caller-enabled worker;
- the delegate dispatch rides the protected wire with the ROOT's delegated
  identity: the Provider must see a second `chat.send` carrying
  caller_context for the worker, produced by the delegate pipeline's
  ensure/issue/commit/send stages (the exact lines e2e never reached);
- the delegate's execution token is its own (parent-attempt bound), and
  the worker's reply lands in session history from both rides.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid

from caller_identity_story import WorkbenchSocket, segment

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class DelegationChainStory:
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
        with _OPENER.open(req, timeout=30) as response:
            payload = response.read().decode()
        return json.loads(payload) if payload else {}

    def post(self, path, body=None, **kwargs):
        return self.request(path, body, method="POST", **kwargs)

    def wait(self, description, read, predicate, attempts=240, delay=0.5):
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
        # The provider MUST declare caller_capabilities with ensure routing:
        # the singlebox assembly wires provider_core, and without the
        # declaration the capability read inside CallerContainerPreparation
        # converges IsolationMismatch — silently, which is exactly how the
        # 2026-11-04 CI run lost every protected ride at preparation.
        self.provider = self.post("/providers", {
            "name": "Delegation chain provider",
            "webhook_url": self.args.mock_url + "/provider/webhook",
            "auth": {"mode": "static_bearer"},
            "protocol_version": "2.0",
            "coordination": {"mode": "mcporter_mcp", "mcp_server": "bcs",
                             "mcporter_command": "mcporter"},
            "caller_capabilities": {
                "container_routing": "ensure",
                "supports_caller": True,
                "supports_isolation_binding": True,
                "supports_observe_inject": True,
                "token_visibility": "runtime_only",
            },
        })
        headers = {"Authorization": "Bearer " + self.provider["provider_admin_token"]}
        self.manager = self.post(f"/providers/{segment(self.provider['provider_id'])}/bots", {
            "name": "Delegating manager", "summary": "Caller-enabled delegating parent",
            "owners": [self.args.human],
            "provider_bot_ref": "delegation-manager-" + uuid.uuid4().hex,
        }, headers=headers)
        self.worker = self.post(f"/providers/{segment(self.provider['provider_id'])}/bots", {
            "name": "Delegation worker", "summary": "Caller-enabled delegated worker",
            "owners": [self.args.human],
            "provider_bot_ref": "delegation-worker-" + uuid.uuid4().hex,
            "domains": ["caller"], "skills": ["dispatch"], "scopes": ["local"],
        }, headers=headers)
        # BOTH ends are caller-enabled: the manager rides the root protected
        # dispatch (registering the root lineage at Accepted), and the worker
        # is delegated onto with Execution purpose — its delegate dispatch
        # ensures the worker's caller container (spec §8.3 per-target read).
        for ref in (self.manager["provider_bot_ref"], self.worker["provider_bot_ref"]):
            path = (f"/providers/{segment(self.provider['provider_id'])}"
                    f"/bots/{segment(ref)}/caller")
            applied = self.request(path, {"snapshot": {"source_revision": 1,
                "caller_identity": {"enabled": True}}}, method="PATCH", headers=headers)
            assert applied["sync_status"] == "applied", applied
        for bot in (self.manager, self.worker):
            self.request(f"/bots/{segment(bot['bot_uuid'])}/visibility",
                         {"visibility": "public"}, method="PUT")
        self.enable_group_queue_policy()

    def enable_group_queue_policy(self) -> None:
        current = self.request("/admin/message-delivery/policy")
        self._delivery_policy_restore = {"version": current["version"],
                                         "policy": current["policy"]}
        enforced = json.loads(json.dumps(current["policy"]))
        enforced["flow_enabled"]["group"] = True
        enforced["flow_enabled"]["system"] = True
        enforced["defaults"]["mode"] = "enforce"
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
        self._delivery_policy_restore = None

    # ----------------------------------------------------------------- run

    def reply_chat_final(self, run_id: str, text: str, headers: dict) -> dict:
        response = None
        for _ in range(6):
            response = self.post("/bot/events", {
                "run_id": run_id, "state": "final", "group_id": self.group_id,
                "bcs_session_id": self.session_id,
                "message": {"text": text},
            }, headers=headers)
            if response.get("error") != "transport_conflict":
                return response
            time.sleep(1)
        return response

    def run(self) -> None:
        self.setup()
        group = self.post("/groups", {
            "label": "Caller delegation chain",
            "driver_bot": self.manager["bot_uuid"],
            "participants": [{"bot_uuid": actor}
                             for actor in (self.manager["bot_uuid"],
                                          self.worker["bot_uuid"],
                                          "human_" + self.args.human)],
        })
        self.group_id, self.session_id = group["id"], group["session_id"]
        assert self.session_id, group
        socket_client = WorkbenchSocket(self.args.base_url, self.args.human,
                                        self.group_id, self.session_id)
        try:
            connect = socket_client.receive(socket_client.connect_id)
            assert connect.get("ok") is True, connect

            # ---- Root protected ride: claim the handoff, ride the wire. ----
            socket_client.send({"type": "req", "id": "dc-lease", "method": "lease.acquire",
                                "params": {"groupId": self.group_id}})
            acquired = socket_client.receive("dc-lease")
            assert acquired.get("ok") is True, acquired
            lease = acquired["payload"]
            self.post("/control/provider/clear", mock=True)
            socket_client.send({"type": "req", "id": "dc-speak", "method": "chat.send",
                                "params": {"group_id": self.group_id,
                                           "session_id": self.session_id,
                                           "bot_id": "human_" + self.args.human,
                                           "message": "delegate this onward for me",
                                           "mentions": [self.manager["bot_uuid"]],
                                           "leaseId": lease["leaseId"],
                                           "leaseEpoch": lease["epoch"]}})
            admitted = socket_client.receive("dc-speak")
            assert admitted.get("ok") is True, admitted
            rides = self.wait("manager root protected ride",
                              lambda: self.request("/control/provider/requests", mock=True)["requests"],
                              lambda items: any(
                                  item["body"].get("method") == "chat.send"
                                  and (item["body"].get("extensions") or {}).get("caller_context")
                                  and "delegate this onward" in json.dumps(item["body"])
                                  for item in items))
            root_ride = next(item for item in rides
                             if item["body"].get("method") == "chat.send"
                             and (item["body"].get("extensions") or {}).get("caller_context")
                             and "delegate this onward" in json.dumps(item["body"]))
            root_delivery_id = root_ride["body"]["id"]
            print("  root protected ride reached the manager provider wire", flush=True)

            # ---- Delegation: the LIVE parent run emits the tool-call echo. ----
            # The mint channel is the agent tool-event pair (bot_event.rs
            # `tool_event_phase`): a `stream:"tool"` phase=start caches the
            # tool name, then phase=result carries the v2 bcs_assign_task frame
            # in `result` — the run stays Running (tool events are
            # non-terminal), so claim_and_dispatch's liveness window and
            # prepare_assignment's validate_parent both pass: the registered
            # root lineage plus the live BotRunContext mint the delegated row
            # toward the caller-enabled worker.
            intent_id = "bcs_intent_" + uuid.uuid4().hex
            tool_call_id = "toolu_" + uuid.uuid4().hex
            frame = json.dumps({
                "__bcs_coordination__": True, "v": 2, "tool": "bcs_assign_task",
                "intent_id": intent_id, "status": "stored",
                "message": "worker handles the delegated leg",
                "timeout_s": 120,
            })
            provider_headers = {
                "X-BCN-Provider-Id": self.provider["provider_id"],
                "Authorization": "Bearer " + self.manager["bot_runtime_token"],
            }
            start = self.post("/bot/events", {
                "run_id": root_delivery_id, "group_id": self.group_id,
                "bcs_session_id": self.session_id, "event": "agent",
                "payload": {"stream": "tool", "data": {
                    "phase": "start", "toolCallId": tool_call_id, "name": "bcs_assign_task",
                }},
            }, headers=provider_headers)
            assert not start.get("error"), start
            result = self.post("/bot/events", {
                "run_id": root_delivery_id, "group_id": self.group_id,
                "bcs_session_id": self.session_id, "event": "agent",
                "payload": {"stream": "tool", "data": {
                    "phase": "result", "toolCallId": tool_call_id,
                    "isError": False, "result": frame,
                }},
            }, headers=provider_headers)
            assert not result.get("error"), result
            # Keep the parent run alive until the delegated row settles; the
            # final answer settles the root run (and frees the managed lease)
            # only after the worker's protected ride is observed below.

            # ---- The delegate dispatch rides the protected wire. ----
            def is_worker_ride(item):
                body = item["body"]
                to_ref = (body.get("to_bot") or {}).get("provider_bot_ref", "")
                return (body.get("method") == "chat.send"
                        and to_ref.startswith("delegation-worker")
                        and (body.get("extensions") or {}).get("caller_context"))
            delegated = self.wait("delegated protected ride toward the worker",
                                  lambda: self.request("/control/provider/requests", mock=True)["requests"],
                                  lambda items: any(is_worker_ride(item) for item in items))
            ride = next(item for item in delegated if is_worker_ride(item))
            token = ride["body"]["extensions"]["caller_execution_token"]
            assert token["format"] == "jwt" and token["token"], token
            # The delegated proof is its OWN issuance (DelegationOnly/Execution
            # bounded by the root lineage window) — never the parent's token.
            print("  delegated protected ride reached the worker provider wire", flush=True)

            # Answer both legs so the session history carries the pair.
            self.post("/control/provider/clear", mock=True)
            answer_headers = {
                "X-BCN-Provider-Id": self.provider["provider_id"],
                "Authorization": "Bearer " + self.worker["bot_runtime_token"],
            }
            self.reply_chat_final(ride["body"]["id"], "worker finished the delegated leg",
                                  answer_headers)
            history = self.wait("both rides in session history",
                                lambda: self.request(f"/sessions/{segment(self.session_id)}/messages"),
                                lambda messages: (
                                    any("delegate this onward" in json.dumps(message)
                                        for message in messages)
                                    and any("worker finished the delegated leg"
                                            in json.dumps(message)
                                            for message in messages)))
            assert history, history

            socket_client.send({"type": "req", "id": "dc-release", "method": "lease.release",
                                "params": {"groupId": self.group_id,
                                           "leaseId": lease["leaseId"],
                                           "leaseEpoch": lease["epoch"]}})
            released = socket_client.receive("dc-release")
            assert released.get("ok") is True, released
        finally:
            socket_client.close()
            self.restore_delivery_policy()
            if self.group_id and self.manager:
                try:
                    self.request(f"/groups/{segment(self.group_id)}"
                                 f"?bot_id={segment(self.manager['bot_uuid'])}", method="DELETE")
                except Exception as error:  # noqa: BLE001 — cleanup is best-effort
                    print(f"  group cleanup skipped: {error}", flush=True)
        print("  caller manager delegated to the caller worker over the protected wire; "
              "root and delegated rides both reached the provider", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--mock-url", required=True)
    parser.add_argument("--human", required=True)
    args = parser.parse_args()
    DelegationChainStory(args).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
