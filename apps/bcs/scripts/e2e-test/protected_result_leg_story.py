#!/usr/bin/env python3
"""Late enabled Manager receives an ordinary Worker result protectively.

The delegation chain starts with an ORDINARY Manager (the Worker is already
Caller-enabled, so the delegated dispatch rides the protected wire like the
caller_delegation_chain_story). Between the delegated ride and the Worker's
terminal callback the Manager's Caller configuration is flipped on: the §15.4
gate then refuses the ordinary result leg, so it must carry its own protected
material (the canonical result text, the Worker as the trusted ordinary
channel sender, the session-captured human identity parked before the queue
commit). A second delegated leg answers with an error callback, so the
failure-text result rides the SAME protected transport.
"""
from __future__ import annotations
import argparse, json, uuid

from caller_identity_story import WorkbenchSocket, segment
from caller_delegation_chain_story import DelegationChainStory


class ProtectedResultLegStory(DelegationChainStory):
    def setup(self) -> None:
        self.manager_caller_revisions = 0
        self.post("/control/reset", mock=True)
        self.post("/me/ensure-human")
        self.provider = self.post("/providers", {
            "name": "Protected result provider " + uuid.uuid4().hex,
            "webhook_url": self.args.mock_url + "/provider/webhook",
            "auth": {"mode": "static_bearer"}, "protocol_version": "2.0",
            "coordination": {"mode": "mcporter_mcp", "mcp_server": "bcs",
                             "mcporter_command": "mcporter"},
            "caller_capabilities": {"container_routing": "ensure",
                "supports_caller": True, "supports_isolation_binding": True,
                "supports_observe_inject": True, "token_visibility": "runtime_only"},
        })
        headers = {"Authorization": "Bearer " + self.provider["provider_admin_token"]}
        self.manager = self.post(f"/providers/{segment(self.provider['provider_id'])}/bots", {
            "name": "Late Caller Manager", "summary": "Ordinary Manager, later Caller-enabled",
            "owners": [self.args.human],
            "provider_bot_ref": "late-manager-" + uuid.uuid4().hex}, headers=headers)
        self.worker = self.post(f"/providers/{segment(self.provider['provider_id'])}/bots", {
            "name": "Protected result Worker", "summary": "Ordinary delegated Worker",
            "owners": [self.args.human],
            "provider_bot_ref": "late-worker-" + uuid.uuid4().hex,
            "domains": ["caller"], "skills": ["dispatch"], "scopes": ["local"]}, headers=headers)
        path = (f"/providers/{segment(self.provider['provider_id'])}/bots/"
                f"{segment(self.worker['provider_bot_ref'])}/caller")
        applied = self.request(path, {"snapshot": {"source_revision": 1,
            "caller_identity": {"enabled": True}}}, method="PATCH", headers=headers)
        assert applied["sync_status"] == "applied", applied
        for bot in (self.manager, self.worker):
            self.request(f"/bots/{segment(bot['bot_uuid'])}/visibility",
                         {"visibility": "public"}, method="PUT")
        self.enable_group_queue_policy()

    def apply_manager_caller(self, revision: int, enabled: bool) -> None:
        path = (f"/providers/{segment(self.provider['provider_id'])}/bots/"
                f"{segment(self.manager['provider_bot_ref'])}/caller")
        applied = self.request(path, {"snapshot": {"source_revision": revision,
            "caller_identity": {"enabled": enabled}}}, method="PATCH",
            headers={"Authorization": "Bearer " + self.provider["provider_admin_token"]})
        assert applied["sync_status"] == "applied", applied

    def enable_manager_caller(self) -> None:
        # Revision 1 is the first enablement; turn 2 disables at revision 2
        # and re-enables at revision 3 (caller_delegation handles the rest
        # of the chain shape below).
        revision = 1 if self.manager_caller_revisions == 0 else 3
        self.manager_caller_revisions += 1
        self.apply_manager_caller(revision, True)

    def disable_manager_caller(self) -> None:
        # Dropping the Caller snapshot lets the next Manager turn open the
        # supported ordinary run shape again (the delegation under a Caller
        # parent needs a parent execution token the scripted mock never
        # round-trips); re-enabling below restores the §15.4 shape before
        # the Worker settles.
        self.apply_manager_caller(2, False)

    def manager_headers(self):
        return {"X-BCN-Provider-Id": self.provider["provider_id"],
                "Authorization": "Bearer " + self.manager["bot_runtime_token"]}

    def worker_headers(self):
        return {"X-BCN-Provider-Id": self.provider["provider_id"],
                "Authorization": "Bearer " + self.worker["bot_runtime_token"]}

    def requests(self):
        return self.request("/control/provider/requests", mock=True)["requests"]

    def ride(self, bot, text, protected=None):
        def match(item):
            body = item["body"]
            if body.get("method") != "chat.send" or (body.get("to_bot") or {}).get("provider_bot_ref") != bot["provider_bot_ref"]:
                return False
            if text not in json.dumps(body.get("message", {}), ensure_ascii=False):
                return False
            if protected is True:
                return bool((body.get("extensions") or {}).get("caller_context"))
            if protected is False:
                return not (body.get("extensions") or {}).get("caller_context")
            return True
        return next((item for item in self.requests() if match(item)), None)

    def assign_tool_leg(self, parent_run_id, message) -> None:
        """One matched provider tool start/result pair delegating to the Worker."""
        tool_call_id = "toolu_" + uuid.uuid4().hex
        frame = json.dumps({
            "__bcs_coordination__": True, "v": 1, "tool": "bcs_assign_task",
            "status": "received", "arguments": {
                "target_bot": self.worker["bot_uuid"],
                "message": message, "timeout_s": 120,
            },
        })
        start = self.provider_event({
            "run_id": parent_run_id, "group_id": self.group_id,
            "bcs_session_id": self.session_id, "event": "agent",
            "payload": {"stream": "tool", "data": {
                "phase": "start", "toolCallId": tool_call_id, "name": "exec",
            }},
        }, headers=self.manager_headers())
        assert not start.get("error"), start
        result = self.provider_event({
            "run_id": parent_run_id, "group_id": self.group_id,
            "bcs_session_id": self.session_id, "event": "agent",
            "payload": {"stream": "tool", "data": {
                "phase": "result", "toolCallId": tool_call_id,
                "isError": False, "result": frame,
            }},
        }, headers=self.manager_headers())
        assert not result.get("error"), result

    def delegated_worker_ride(self, message):
        def is_worker_ride(item):
            body = item["body"]
            to_ref = (body.get("to_bot") or {}).get("provider_bot_ref", "")
            return (body.get("method") == "chat.send"
                    and to_ref == self.worker["provider_bot_ref"]
                    and (body.get("extensions") or {}).get("caller_context"))
        rides = self.wait("delegated protected ride toward the worker",
                          lambda: self.requests(),
                          lambda items: any(is_worker_ride(item) and message
                                           in json.dumps(item["body"], ensure_ascii=False)
                                           for item in items))
        return next(item for item in rides if is_worker_ride(item)
                    and message in json.dumps(item["body"], ensure_ascii=False))

    def run(self) -> None:
        socket_client = None
        try:
            self.setup()
            group = self.post("/groups", {
                "label": "Protected result delegation",
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

            self.initialize_context(self.manager_headers())
            socket_client.send({"type": "req", "id": "pr-lease", "method": "lease.acquire",
                                "params": {"groupId": self.group_id}})
            acquired = socket_client.receive("pr-lease")
            assert acquired.get("ok") is True, acquired
            lease = acquired["payload"]

            # ---- Turn 1: the ordinary Manager ride is NOT protected. ----
            self.post("/control/provider/clear", mock=True)
            socket_client.send({"type": "req", "id": "pr-speak", "method": "chat.send",
                                "params": {"group_id": self.group_id,
                                           "session_id": self.session_id,
                                           "bot_id": "human_" + self.args.human,
                                           "message": "delegate this onward for me",
                                           "mentions": [self.manager["bot_uuid"]],
                                           "leaseId": lease["leaseId"],
                                           "leaseEpoch": lease["epoch"]}})
            admitted = socket_client.receive("pr-speak")
            assert admitted.get("ok") is True, admitted
            def is_root_ride(item):
                body = item["body"]
                return (body.get("method") == "chat.send"
                        and (body.get("to_bot") or {}).get("provider_bot_ref")
                        == self.manager["provider_bot_ref"]
                        and "delegate this onward" in json.dumps(body))
            root_ride = self.wait("manager ordinary ride",
                                   lambda: self.requests(),
                                   lambda items: any(is_root_ride(item) for item in items))
            root_ride = next(item for item in root_ride if is_root_ride(item))
            parent_run_id = root_ride["body"]["id"]
            assert not (root_ride["body"].get("extensions") or {}).get("caller_execution_token")
            print("  ordinary Manager ride reached the provider; no Caller token exposed", flush=True)

            # ---- The delegated dispatch rides the protected wire toward the
            # still-ordinary-enabled Worker, exactly like the chain story. ----
            self.assign_tool_leg(parent_run_id, "worker handles the delegated leg")
            ride = self.delegated_worker_ride("worker handles the delegated leg")
            assert ride["authorization"] == "Bearer " + self.provider["bcs_to_provider_token"]
            assert ride["body"]["extensions"]["caller_execution_token"].get("token"), \
                "Worker proof is missing"
            # The worker's own container ensure precedes the delegated ride
            # exactly once; its run binding carries the message turn used to
            # poll delivery status below.
            worker_ensures = [item for item in self.requests()
                              if item["body"].get("method") == "caller.container.ensure"
                              and (item["body"].get("to_bot") or {}).get("provider_bot_ref")
                              == self.worker["provider_bot_ref"]]
            assert len(worker_ensures) == 1, "Worker ensure must precede the delegated send exactly once"
            binding = worker_ensures[0]["body"]["extensions"]["run"]

            # ---- LATE enablement: the Manager flips Caller on before the
            # Worker settles its answer, so the RESULT leg must park the
            # captured human identity and ride the protected transport. ----
            self.enable_manager_caller()
            print("  Manager Caller configuration applied before the Worker settled", flush=True)

            self.post("/control/provider/clear", mock=True)
            self.reply_chat_final(ride["body"]["id"], "worker finished the delegated leg",
                                  self.worker_headers())
            deliveries_path = ("/openapi/v1/collaboration/messages/"
                               + segment(binding["message_turn_id"]) + "/deliveries?session_id="
                               + segment(self.session_id))
            self.wait("completed Worker delivery", lambda: self.request(deliveries_path),
                      lambda rows: any(row["target_bot_id"] == self.worker["bot_uuid"]
                                       and row["status"] == "completed" for row in rows))
            self.reply_chat_final(parent_run_id, "manager delegated the work",
                                  self.manager_headers())

            # ---- The Worker result toward the caller-enabled Manager rides
            # PROTECTED: caller_context subject is the captured human, the
            # trusted ordinary channel sender is the WORKER, and the proof
            # never enters the model input. ----
            def is_result_ride(item):
                body = item["body"]
                return (body.get("method") == "chat.send"
                        and (body.get("to_bot") or {}).get("provider_bot_ref")
                        == self.manager["provider_bot_ref"]
                        and "worker finished the delegated leg" in json.dumps(body.get("message"))
                        and (body.get("extensions") or {}).get("caller_context"))
            results = self.wait("protected Worker result rides to the late Manager",
                                lambda: self.requests(),
                                lambda items: any(is_result_ride(item) for item in items))
            result_ride = next(item for item in results if is_result_ride(item))
            body = result_ride["body"]
            assert body["session_id"] == self.session_id, body
            assert body["id"] != parent_run_id and body["id"] != ride["body"]["id"], \
                "the result must run on its own lane"
            assert result_ride["authorization"] == "Bearer " + self.provider["bcs_to_provider_token"]
            context = body["extensions"]["caller_context"]
            assert context["subject"]["kind"] == "human", context
            token = body["extensions"]["caller_execution_token"]
            assert token.get("token") and token["token"] not in json.dumps(body["message"]), \
                "Proof leaked into model input"
            assert (body.get("from") or {}).get("actor_id") == self.worker["bot_uuid"], \
                "the trusted sender is the Worker, never the protected subject"
            ensures = [item for item in results[:results.index(result_ride)]
                       if item["body"].get("method") == "caller.container.ensure"
                       and (item["body"].get("to_bot") or {}).get("provider_bot_ref")
                       == self.manager["provider_bot_ref"]]
            assert len(ensures) == 1, "Exactly one manager ensure must precede the protected result"
            assert ensures[0]["body"]["extensions"]["bot_id"] == self.manager["bot_uuid"]
            print("  protected Worker result reached the late-enabled Manager", flush=True)
            self.reply_chat_final(body["id"], "manager finished delegated work",
                                  self.manager_headers())

            # ---- Turn 2: a second delegated leg answers with an ERROR
            # callback; the failure-text result rides the SAME protected
            # transport (§15.4 keeps its own material, not a bare notice).
            # The Manager's Caller snapshot is dropped first so the second
            # human turn opens the supported ordinary delegation shape;
            # caller re-enablement below then protects the failure leg. ----
            self.disable_manager_caller()
            self.post("/control/provider/clear", mock=True)
            socket_client.send({"type": "req", "id": "pr-speak2", "method": "chat.send",
                                "params": {"group_id": self.group_id,
                                           "session_id": self.session_id,
                                           "bot_id": "human_" + self.args.human,
                                           "message": "delegate the fragile leg as well",
                                           "mentions": [self.manager["bot_uuid"]],
                                           "leaseId": lease["leaseId"],
                                           "leaseEpoch": lease["epoch"]}})
            admitted2 = socket_client.receive("pr-speak2")
            assert admitted2.get("ok") is True, admitted2
            def is_second_root_ride(item):
                body2 = item["body"]
                return (body2.get("method") == "chat.send"
                        and (body2.get("to_bot") or {}).get("provider_bot_ref")
                        == self.manager["provider_bot_ref"]
                        and "delegate the fragile leg" in json.dumps(body2))
            second_ride = self.wait("manager rides again while ordinary",
                                     lambda: self.requests(),
                                     lambda items: any(is_second_root_ride(item) for item in items))
            second_root = next(item for item in second_ride if is_second_root_ride(item))
            assert not (second_root["body"].get("extensions") or {}).get("caller_context"), \
                "the second Manager ride must be ordinary after caller disablement"
            parent2_run_id = second_root["body"]["id"]
            self.enable_manager_caller()
            print("  Manager Caller configuration re-applied for the fragile leg", flush=True)
            self.assign_tool_leg(parent2_run_id, "worker leg that will fail")
            failing_ride = self.delegated_worker_ride("worker leg that will fail")
            self.post("/control/provider/clear", mock=True)
            self.provider_event({
                "run_id": failing_ride["body"]["id"], "state": "error",
                "group_id": self.group_id, "bcs_session_id": self.session_id,
                "message": {"text": "the delegated leg exploded"},
            }, headers=self.worker_headers())
            # Settle the parent turn first: the session serializes its lane,
            # so the failure result rides only after turn 2's run frees it.
            self.reply_chat_final(parent2_run_id, "manager acknowledged the failed leg",
                                  self.manager_headers())
            def is_failure_ride(item):
                body3 = item["body"]
                return (body3.get("method") == "chat.send"
                        and (body3.get("to_bot") or {}).get("provider_bot_ref")
                        == self.manager["provider_bot_ref"]
                        and "情况" in json.dumps(body3.get("message"), ensure_ascii=False)
                        and (body3.get("extensions") or {}).get("caller_context"))
            failures = self.wait("protected failure-text result rides to the Manager",
                                 lambda: self.requests(),
                                 lambda items: any(is_failure_ride(item) for item in items))
            failure_ride = next(item for item in failures if is_failure_ride(item))
            assert "情况" in json.dumps(failure_ride["body"]["message"], ensure_ascii=False)
            assert (failure_ride["body"].get("from") or {}).get("actor_id") == self.worker["bot_uuid"]
            print("  protected failure text reached the late-enabled Manager", flush=True)

            socket_client.send({"type": "req", "id": "pr-release", "method": "lease.release",
                                "params": {"groupId": self.group_id,
                                           "leaseId": lease["leaseId"],
                                           "leaseEpoch": lease["epoch"]}})
            released = socket_client.receive("pr-release")
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
        print("  Late-enabled Manager received Worker results protectively; "
              "identity park, worker sender and failure text verified", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--mock-url", required=True)
    parser.add_argument("--human", required=True)
    parser.add_argument("--context-delivery", choices=("send", "inject"), default="send")
    args = parser.parse_args()
    story = ProtectedResultLegStory(args)
    story.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
