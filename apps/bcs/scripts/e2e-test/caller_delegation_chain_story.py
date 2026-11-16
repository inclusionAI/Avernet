#!/usr/bin/env python3
"""Human → Owner Manager → Caller Worker over the live Provider boundary.

A leased human message gives the ordinary Manager run a delegation-only
lineage, not a Caller execution token. A matched tool start/result pair on
that live run assigns work through the supported inline v1 contract. The
Worker must receive a new protected dispatch and return its result through
the existing callback and delivery-status APIs. No fabricated v2 stored-intent
receipt, internal store mutation or test-only production entrypoint is used.

Run separately with --context-delivery send and inject. Send completes an
initialization turn; Inject must stay pending without a run and be consumed
by the first human turn. Both modes exercise the full protected delegation.
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
        self.context_marker = "delegation-context-" + uuid.uuid4().hex
        self.context_message_id = None
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

    def wait(self, description, read, predicate, attempts=150, delay=0.2):
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
        # Capability discovery is part of real container preparation, not
        # supplied by a test-only composition root.
        self.provider = self.post("/providers", {
            "name": "Delegation chain provider " + uuid.uuid4().hex,
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
        self.manager = self.register_manager(headers)
        self.worker = self.post(f"/providers/{segment(self.provider['provider_id'])}/bots", {
            "name": "Delegation worker", "summary": "Caller-enabled delegated worker",
            "owners": [self.args.human],
            "provider_bot_ref": "delegation-worker-" + uuid.uuid4().hex,
            "domains": ["caller"], "skills": ["dispatch"], "scopes": ["local"],
        }, headers=headers)
        # The Owner manager retains an ordinary callback-streaming run.
        # Only the Worker opts into Caller; the human lease supplies the
        # delegation-only lineage at the Manager's ordinary send-start.
        path = (f"/providers/{segment(self.provider['provider_id'])}"
                f"/bots/{segment(self.worker['provider_bot_ref'])}/caller")
        applied = self.request(path, {"snapshot": {"source_revision": 1,
            "caller_identity": {"enabled": True}}}, method="PATCH", headers=headers)
        assert applied["sync_status"] == "applied", applied
        for bot in (self.manager, self.worker):
            self.request(f"/bots/{segment(bot['bot_uuid'])}/visibility",
                         {"visibility": "public"}, method="PUT")
        self.enable_group_queue_policy()

    def register_manager(self, headers):
        return self.post(f"/providers/{segment(self.provider['provider_id'])}/bots", {
            "name": "Delegating manager", "summary": "Owner-mode delegating parent",
            "owners": [self.args.human],
            "provider_bot_ref": "delegation-manager-" + uuid.uuid4().hex,
        }, headers=headers)

    def enable_group_queue_policy(self) -> None:
        current = self.request("/admin/message-delivery/policy")
        self._delivery_policy_restore = {"version": current["version"],
                                         "policy": current["policy"]}
        enforced = json.loads(json.dumps(current["policy"]))
        enforced["flow_enabled"]["group"] = True
        enforced["flow_enabled"]["system"] = True
        enforced["flow_enabled"]["task"] = True
        enforced["defaults"]["mode"] = "enforce"
        enforced["defaults"]["max_running"] = 8
        enforced["pause_dispatch"] = False
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

    def provider_event(self, body, headers):
        # The mock records the request before BCS consumes the JSON ACK.
        # Retry only the explicit negotiation conflict, never a missing run.
        for _ in range(60):
            try:
                response = self.post("/bot/events", body, headers=headers)
            except urllib.error.HTTPError as error:
                response = json.loads(error.read())
                if error.code != 409 or response.get("error") != "transport_conflict":
                    raise AssertionError(f"Provider callback failed: {error.code} {response}") from error
            if response.get("error") != "transport_conflict":
                assert not response.get("error"), response
                return response
            time.sleep(0.1)
        raise AssertionError("Provider callback transport negotiation timed out")

    def reply_chat_final(self, run_id: str, text: str, headers: dict) -> dict:
        return self.provider_event({
            "run_id": run_id, "state": "final", "group_id": self.group_id,
            "bcs_session_id": self.session_id, "message": {"text": text},
        }, headers)

    def is_manager_send(self, item):
        body = item["body"]
        return (body.get("method") == "chat.send"
                and (body.get("to_bot") or {}).get("provider_bot_ref")
                == self.manager["provider_bot_ref"])

    def context_deliveries(self):
        return self.request("/openapi/v1/collaboration/messages/"
                            + segment(self.context_message_id) + "/deliveries?session_id="
                            + segment(self.session_id))

    def initialize_context(self, headers):
        history = self.wait("persisted manager context",
            lambda: self.request(f"/sessions/{segment(self.session_id)}/messages"),
            lambda rows: any(self.context_marker in row.get("content", "") for row in rows))
        self.context_message_id = next(row["id"] for row in history
                                       if self.context_marker in row.get("content", ""))
        if self.args.context_delivery == "inject":
            rows = self.wait("manager pending context", self.context_deliveries,
                lambda rows: any(row["target_bot_id"] == self.manager["bot_uuid"]
                                 and row["status"] == "pending_context" for row in rows))
            manager = next(row for row in rows if row["target_bot_id"] == self.manager["bot_uuid"])
            assert manager["kind"] == "inject" and manager["run_id"] is None, manager
            captured = self.request("/control/provider/requests", mock=True)["requests"]
            assert not any(self.is_manager_send(item) for item in captured), \
                "Inject must remain silent before the first user message"
            print("  Inject persisted without a run; waiting for the first user message", flush=True)
            return
        captured = self.wait("manager initialization Send",
            lambda: self.request("/control/provider/requests", mock=True)["requests"],
            lambda items: any(self.is_manager_send(item) for item in items))
        initial = next(item for item in captured if self.is_manager_send(item))
        assert self.context_marker in json.dumps(initial["body"]["message"])
        self.reply_chat_final(initial["body"]["id"], "manager ready", headers)
        self.wait("manager initialization completed", self.context_deliveries,
                  lambda rows: any(row["target_bot_id"] == self.manager["bot_uuid"]
                                   and row["status"] == "completed" for row in rows))
        print("  Send started and completed a separate initialization run", flush=True)

    def run(self) -> None:
        socket_client = None
        try:
            self.setup()
            group = self.post("/groups", {
                "label": "Caller delegation chain",
                "driver_bot": self.manager["bot_uuid"],
                "group_strategy": "manager_worker",
                "create_initial_session": False,
                "participants": [
                    {"bot_uuid": self.manager["bot_uuid"], "role": "manager"},
                    {"bot_uuid": self.worker["bot_uuid"], "role": "worker"},
                    {"bot_uuid": "human_" + self.args.human, "role": "observer"},
                ],
            })
            self.group_id = group["id"]
            session = self.post(f"/groups/{segment(self.group_id)}/sessions",
                                {"group_context_delivery": self.args.context_delivery,
                                 "input": self.context_marker})
            self.session_id = session["id"]
            assert self.session_id, session
            socket_client = WorkbenchSocket(self.args.base_url, self.args.human,
                                            self.group_id, self.session_id)
            connect = socket_client.receive(socket_client.connect_id)
            assert connect.get("ok") is True, connect

            # Send starts an initialization run; Inject remains pending until
            # the first human message carries it into the Manager turn.
            provider_headers = {
                "X-BCN-Provider-Id": self.provider["provider_id"],
                "Authorization": "Bearer " + self.manager["bot_runtime_token"],
            }
            self.initialize_context(provider_headers)

            # Lease admission parks the identity for the ordinary Manager.
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
            rides = self.wait("manager ordinary ride",
                              lambda: self.request("/control/provider/requests", mock=True)["requests"],
                              lambda items: any(
                                  item["body"].get("method") == "chat.send"
                                  and (item["body"].get("to_bot") or {}).get("provider_bot_ref")
                                  == self.manager["provider_bot_ref"]
                                  and "delegate this onward" in json.dumps(item["body"])
                                  for item in items))
            root_ride = next(item for item in rides
                             if item["body"].get("method") == "chat.send"
                             and (item["body"].get("to_bot") or {}).get("provider_bot_ref")
                             == self.manager["provider_bot_ref"]
                             and "delegate this onward" in json.dumps(item["body"]))
            if self.args.context_delivery == "inject":
                message = json.dumps(root_ride["body"]["message"], ensure_ascii=False)
                assert self.context_marker in message, "First user turn lost injected context"
                assert "bcs_assign_task" in message, "Manager instructions were lost"
                assert len([item for item in rides if self.is_manager_send(item)]) == 1, \
                    "Inject must not start an extra initialization turn"
            parent_run_id = root_ride["body"]["id"]
            assert not (root_ride["body"].get("extensions") or {}).get("caller_execution_token")
            print("  ordinary Manager ride reached the provider; no Caller token exposed", flush=True)

            # A v1 inline receipt is the supported public contract when
            # there is no external MCP intent store. Keep the result nameless:
            # authorization must use the matching start's cached tool name.
            tool_call_id = "toolu_" + uuid.uuid4().hex
            frame = json.dumps({
                "__bcs_coordination__": True, "v": 1, "tool": "bcs_assign_task",
                "status": "received", "arguments": {
                    "target_bot": self.worker["bot_uuid"],
                    "message": "worker handles the delegated leg", "timeout_s": 120,
                },
            })
            start = self.provider_event({
                "run_id": parent_run_id, "group_id": self.group_id,
                "bcs_session_id": self.session_id, "event": "agent",
                "payload": {"stream": "tool", "data": {
                    "phase": "start", "toolCallId": tool_call_id, "name": "exec",
                }},
            }, headers=provider_headers)
            assert not start.get("error"), start
            result = self.provider_event({
                "run_id": parent_run_id, "group_id": self.group_id,
                "bcs_session_id": self.session_id, "event": "agent",
                "payload": {"stream": "tool", "data": {
                    "phase": "result", "toolCallId": tool_call_id,
                    "isError": False, "result": frame,
                }},
            }, headers=provider_headers)
            assert not result.get("error"), result
            # Keep the parent run alive until the delegated row settles; the
            # final answer settles the root run (and frees the managed lane)
            # only after the worker's protected ride is observed below.

            # ---- The delegate dispatch rides the protected wire. ----
            def is_worker_ride(item):
                body = item["body"]
                to_ref = (body.get("to_bot") or {}).get("provider_bot_ref", "")
                return (body.get("method") == "chat.send"
                        and to_ref == self.worker["provider_bot_ref"]
                        and (body.get("extensions") or {}).get("caller_context"))
            delegated = self.wait("delegated protected ride toward the worker",
                                  lambda: self.request("/control/provider/requests", mock=True)["requests"],
                                  lambda items: any(is_worker_ride(item) for item in items))
            ride = next(item for item in delegated if is_worker_ride(item))
            token = ride["body"]["extensions"]["caller_execution_token"]
            assert token["format"] == "jwt" and token["token"], "Worker proof is missing"
            body = ride["body"]
            assert body["id"] != parent_run_id, "Child must have an independent run"
            assert body["session_id"] == self.session_id
            assert ride["authorization"] == "Bearer " + self.provider["bcs_to_provider_token"]
            assert "worker handles the delegated leg" in json.dumps(body["message"])
            assert token["token"] not in json.dumps(body["message"]), "Proof leaked into model input"
            ensures = [item for item in delegated[:delegated.index(ride)]
                       if item["body"].get("method") == "caller.container.ensure"
                       and (item["body"].get("to_bot") or {}).get("provider_bot_ref")
                       == self.worker["provider_bot_ref"]]
            assert len(ensures) == 1, "Worker ensure must precede the protected send exactly once"
            ensure = ensures[0]["body"]
            assert ensure["session_id"] == self.session_id
            binding = ensure["extensions"]["run"]
            assert binding["run_id"] == body["id"]
            assert binding["parent_run_id"] == parent_run_id
            assert binding["root_run_id"] == parent_run_id
            assert ensure["extensions"]["bot_id"] == self.worker["bot_uuid"]
            assert ensure["extensions"]["caller_subject"] == body["extensions"]["caller_context"]["subject"]
            print("  delegated protected ride reached the worker provider wire", flush=True)

            # Complete the Worker through its authenticated callback, then
            # free the Manager's lane for the independent TaskResult delivery.
            self.post("/control/provider/clear", mock=True)
            answer_headers = {
                "X-BCN-Provider-Id": self.provider["provider_id"],
                "Authorization": "Bearer " + self.worker["bot_runtime_token"],
            }
            self.reply_chat_final(ride["body"]["id"], "worker finished the delegated leg",
                                  answer_headers)
            deliveries_path = ("/openapi/v1/collaboration/messages/"
                               + segment(binding["message_turn_id"]) + "/deliveries?session_id="
                               + segment(self.session_id))
            self.wait("completed Worker delivery", lambda: self.request(deliveries_path),
                      lambda rows: any(row["target_bot_id"] == self.worker["bot_uuid"]
                                       and row["status"] == "completed" for row in rows))
            self.reply_chat_final(parent_run_id, "manager delegated the work", provider_headers)
            if self.args.context_delivery == "inject":
                self.wait("injected initialization consumed", self.context_deliveries,
                          lambda rows: any(row["target_bot_id"] == self.manager["bot_uuid"]
                                           and row["status"] == "consumed" for row in rows))
            def is_result_ride(item):
                body = item["body"]
                return (body.get("method") == "chat.send"
                        and (body.get("to_bot") or {}).get("provider_bot_ref")
                        == self.manager["provider_bot_ref"]
                        and "worker finished the delegated leg" in json.dumps(body.get("message")))
            results = self.wait("Worker result delivered to Manager",
                                lambda: self.request("/control/provider/requests", mock=True)["requests"],
                                lambda items: any(is_result_ride(item) for item in items))
            result_ride = next(item for item in results if is_result_ride(item))
            assert result_ride["body"]["session_id"] == self.session_id
            assert not (result_ride["body"].get("extensions") or {}).get("caller_execution_token")
            self.reply_chat_final(result_ride["body"]["id"], "manager finished delegated work",
                                  provider_headers)

            socket_client.send({"type": "req", "id": "dc-release", "method": "lease.release",
                                "params": {"groupId": self.group_id,
                                           "leaseId": lease["leaseId"],
                                           "leaseEpoch": lease["epoch"]}})
            released = socket_client.receive("dc-release")
            assert released.get("ok") is True, released
        finally:
            if socket_client:
                try:
                    socket_client.close()
                except OSError:
                    socket_client.conn.close()
            self.restore_delivery_policy()
            if self.group_id and self.manager:
                try:
                    self.request(f"/groups/{segment(self.group_id)}"
                                 f"?bot_id={segment(self.manager['bot_uuid'])}", method="DELETE")
                except Exception as error:  # noqa: BLE001 — cleanup is best-effort
                    print(f"  group cleanup skipped: {error}", flush=True)
        print("  Owner Manager delegated to Caller Worker; protected dispatch, "
              "parent binding, completion and result delivery verified", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--mock-url", required=True)
    parser.add_argument("--human", required=True)
    parser.add_argument("--context-delivery", choices=("send", "inject"), required=True)
    args = parser.parse_args()
    DelegationChainStory(args).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
