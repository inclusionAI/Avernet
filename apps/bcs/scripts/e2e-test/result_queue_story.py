#!/usr/bin/env python3
"""Publish a one-shot result through the public API and real delivery scheduler."""

from __future__ import annotations

import argparse
import copy
import json
import uuid

from fixed_loop_story import Story, bot_node, segment


class ResultQueueStory(Story):
    def __init__(self, args):
        super().__init__(args)
        self.bots = []
        self.original_policy = None

    def setup(self):
        self.post("/control/reset", mock=True)
        self.post("/me/ensure-human")
        self.provider = self.post("/providers", {
            "name": "Result queue E2E", "webhook_url": (self.args.mock_sink_url or self.args.mock_url) + "/provider/webhook",
            "auth": {"mode": "static_bearer"}, "protocol_version": "2.0",
            "coordination": {"mode": "native_tool"},
        })
        for role in ("driver", "observer"):
            bot = self.post(f"/providers/{segment(self.provider['provider_id'])}/bots", {
                "name": "Result queue " + role, "summary": "One-shot result recipient",
                "owners": [self.args.human], "provider_bot_ref": uuid.uuid4().hex,
                "domains": ["release"], "skills": ["review"], "scopes": ["local"],
            }, headers={"Authorization": "Bearer " + self.provider["provider_admin_token"]})
            self.bots.append(bot)
            self.request(f"/bots/{segment(bot['bot_uuid'])}/visibility", {"visibility": "public"}, method="PUT")
        self.driver, self.observer = self.bots
        self.args.driver = self.driver["bot_uuid"]
        self.post("/friends/request", {"from_bot": self.args.driver, "to_bot": self.observer["bot_uuid"]})
        self.original_policy = self.request("/admin/message-delivery/policy")["policy"]
        policy = copy.deepcopy(self.original_policy)
        policy["flow_enabled"].update(group=True, system=True)
        for bot in self.bots:
            policy["bots"][bot["bot_uuid"]] = {
                "mode": "enforce", "max_running": 1, "min_send_interval_ms": 1, "max_queued": 20,
            }
        self.test_policy = policy

    def replace_policy(self, policy):
        current = self.request("/admin/message-delivery/policy")
        self.request("/admin/message-delivery/policy", {
            "expected_version": current["version"], "policy": policy,
        }, method="PUT")

    def captured(self):
        return self.request("/control/provider/requests", mock=True)["requests"]

    def callback(self, request, bot, content):
        response = self.wait("Provider callback acceptance", lambda: self.post("/bot/events", {
            "run_id": request["body"]["id"], "state": "final", "message": {"text": content},
        }, headers={"X-BCN-Provider-Id": self.provider["provider_id"],
                    "Authorization": "Bearer " + bot["bot_runtime_token"]}, status=(200, 409)),
            lambda body: body.get("error") != "transport_conflict")
        assert not response.get("error"), "Provider callback was rejected"

    def deliveries(self, message_id):
        return self.request("/openapi/v1/collaboration/messages/" + segment(message_id)
                            + "/deliveries?session_id=" + segment(self.session_id))

    def exercise_result(self):
        group = self.post("/groups", {
            "driver_bot": self.args.driver, "label": "Queued one-shot publication",
            "participants": [{"bot_uuid": bot["bot_uuid"]} for bot in self.bots]
                            + [{"bot_uuid": "human_" + self.args.human}],
        })
        self.group_id = group["id"]
        self.groups.append(self.group_id)
        session = self.post(f"/groups/{segment(self.group_id)}/sessions", {"session_title": "Result admission"})
        self.session_id = session["session_id"]
        self.replace_policy(self.test_policy)
        self.post("/control/provider/clear", mock=True)
        definition = {
            "name": "Queued one-shot result", "participants": {"worker": {"required": True}},
            "runtime": {"kind": "state_machine", "state_machine": {
                "nodes": {"answer": {**bot_node("Final answer"), "final_output": True}},
            }},
        }
        run = self.post(f"/sessions/{segment(self.session_id)}/state-machine-runs", {
            "definition_yaml": json.dumps(definition), "input": {"question": "Prepare the result"},
            "participant_bindings": {"worker": {"source": "manual", "bot_ids": [self.args.driver]}},
        }, headers={"Authorization": "Bearer " + self.driver["bot_runtime_token"]})
        run_id = run["run"]["run_id"]
        node_requests = self.wait("one-shot node delivery", self.captured,
                                  lambda rows: any(row["body"].get("method") == "chat.send" for row in rows))
        node = next(row for row in node_requests if row["body"].get("method") == "chat.send")
        self.post("/control/provider/clear", mock=True)
        self.callback(node, self.driver, "queued-final-result")
        result_id = "state-machine-result:" + run_id
        rows = self.wait("durable result admission", lambda: self.deliveries(result_id), lambda rows: len(rows) == 2)
        by_bot = {row["target_bot_id"]: row for row in rows}
        assert by_bot[self.args.driver]["kind"] == "send", "The initiating owner must receive the result"
        assert by_bot[self.observer["bot_uuid"]]["kind"] == "inject"
        result_requests = self.wait("scheduled result delivery", self.captured,
                                    lambda rows: any(row["body"].get("method") == "chat.send" for row in rows))
        result = next(row for row in result_requests if row["body"].get("method") == "chat.send")
        assert "queued-final-result" in json.dumps(result["body"]["message"])
        assert result["authorization"] == "Bearer " + self.provider["bcs_to_provider_token"]
        assert result["body"]["session_id"] == self.session_id
        self.callback(result, self.driver, "Result received")
        self.wait("completed result delivery", lambda: self.deliveries(result_id),
                  lambda rows: any(row["target_bot_id"] == self.args.driver and row["status"] == "completed" for row in rows))
        self.post("/control/provider/clear", mock=True)
        self.post(f"/sessions/{segment(self.session_id)}/chat", {
            "message": "@Result queue observer please continue from the final result",
        })
        followups = self.wait("observer receives the result as context", self.captured,
                             lambda rows: any(row["body"].get("method") == "chat.send" for row in rows))
        followup = next(row for row in followups if row["body"].get("method") == "chat.send")
        assert "queued-final-result" in json.dumps(followup["body"]["message"]), "Bound result context was lost"
        self.callback(followup, self.observer, "Observer received the result")
        self.wait("consumed result context", lambda: self.deliveries(result_id),
                  lambda rows: any(row["target_bot_id"] == self.observer["bot_uuid"] and row["status"] == "consumed" for row in rows))
        history_path = f"/sessions/{segment(self.session_id)}/messages"
        for history in (self.request(history_path), self.request(history_path)):
            published = [row for row in history if row.get("id") == result_id]
            assert len(published) == 1 and published[0]["content"] == "queued-final-result"
        view = self.request("/state-machine-runs/" + segment(run_id))
        assert view["run"]["status"] == "completed"
        print("Queued result: owner Send, observer Inject, bound context and unique history verified", flush=True)

    def cleanup(self):
        try:
            for group in self.groups:
                self.request(f"/groups/{segment(group)}?bot_id={segment(self.args.driver)}", method="DELETE")
        finally:
            if self.original_policy is not None:
                self.replace_policy(self.original_policy)
            for bot in self.bots:
                self.request(f"/providers/{segment(self.provider['provider_id'])}/bots/{segment(bot['bot_uuid'])}",
                             method="DELETE", headers={"Authorization": "Bearer " + self.provider["provider_admin_token"]})
            self.post("/control/reset", mock=True)


def main():
    parser = argparse.ArgumentParser()
    for name in ("base-url", "mock-url", "human"):
        parser.add_argument("--" + name, required=True)
    # BCS-facing sink URL for the provider webhook registration. Falls back to
    # --mock-url when the harness and BCS share one network viewpoint.
    parser.add_argument("--mock-sink-url", required=False)
    story = ResultQueueStory(parser.parse_args())
    try:
        story.setup()
        story.exercise_result()
    finally:
        story.cleanup()


if __name__ == "__main__":
    main()
