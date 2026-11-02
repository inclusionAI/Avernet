#!/usr/bin/env python3
"""Exercise private abnormal Run results through real HTTP and delivery queues."""

from __future__ import annotations

import argparse
import json
import uuid

from fixed_loop_story import bot_node, segment
from result_queue_story import ResultQueueStory


class RunFailureStory(ResultQueueStory):
    def result(self, run_id, bot=None, status=200):
        bot = bot or self.driver
        return self.request("/state-machine-runs/" + segment(run_id) + "/result",
                            headers={"Authorization": "Bearer " + bot["bot_runtime_token"]},
                            status=status)

    def exercise_failure(self, mode, queued):
        policy = self.test_policy.copy()
        policy["flow_enabled"] = {**policy["flow_enabled"], "group": queued, "system": queued}
        # Group creation sends a greeting in its initial session. Keep it out
        # of this scenario's single-slot delivery queue.
        self.replace_policy({**policy, "flow_enabled": {
            **policy["flow_enabled"], "group": False, "system": False}})
        self.post("/control/provider/clear", mock=True)
        group = self.post("/groups", {
            "driver_bot": self.args.driver, "label": f"Run {mode} queue={queued}",
            "participants": [{"bot_uuid": bot["bot_uuid"]} for bot in self.bots]
                            + [{"bot_uuid": "human_" + self.args.human}],
        })
        self.group_id = group["id"]
        self.groups.append(self.group_id)
        session = self.post(f"/groups/{segment(self.group_id)}/sessions", {"session_title": "Failure recovery"})
        self.session_id = session["session_id"]
        self.replace_policy(policy)
        secret = "private-input-" + uuid.uuid4().hex
        reference = uuid.uuid4().hex
        first = {**bot_node("Prepare private state"), "transitions": {"complete": {"targets": ["fail"]}}}
        target = {**bot_node("Fail after preparation"), "max_attempts": 1}
        if mode == "timeout":
            target = {"kind": "human_input", "display_name": "Wait for author",
                      "instruction": "Provide the next input", "node_timeout_ms": 1000}
        target["transitions"] = {"complete": {"targets": ["finish"]}}
        doc = {"name": "Abnormal terminal E2E", "participants": {"worker": {"required": True}},
               "runtime": {"kind": "state_machine", "state_machine": {"nodes": {
                   "prepare": first, "fail": target, "finish": {**bot_node("Finish"), "final_output": True}}}}}
        run = self.post(f"/sessions/{segment(self.session_id)}/state-machine-runs", {
            "definition_yaml": json.dumps(doc), "input": {"secret": secret, "bcs_client_reference": reference},
            "participant_bindings": {"worker": {"source": "manual", "bot_ids": [self.args.driver]}},
        }, headers={"Authorization": "Bearer " + self.driver["bot_runtime_token"]})
        run_id = run["run"]["run_id"]
        path = "/state-machine-runs/" + segment(run_id)
        def node_requests(node_id):
            return [row for row in self.captured()
                    if row["body"].get("method") == "chat.send"
                    and row["body"].get("id", "").startswith(f"smnode-{run_id}-{node_id}-")]

        running = self.result(run_id)
        assert running["client_reference"] == reference and running.get("terminal_result") is None
        self.result(run_id, self.observer, status=403)
        self.result("missing-" + uuid.uuid4().hex, status=404)
        self.request(path + "/result", status=401)
        prepare = self.wait("preparation dispatch", lambda: node_requests("prepare"), bool)
        prepare_request = prepare[0]
        self.post("/control/provider/clear", mock=True)
        self.callback(prepare_request, self.driver, secret)
        if mode == "timeout":
            expected, reason = "failed", "human_timeout"
        else:
            rows = self.wait("second node dispatch", lambda: node_requests("fail"), bool)
            node = rows[0]
            self.post("/control/provider/clear", mock=True)
            if mode == "scope_closed":
                self.request(f"/sessions/{segment(self.session_id)}?bot_id={segment(self.args.driver)}", method="DELETE",
                             headers={"Authorization": "Bearer " + self.driver["bot_runtime_token"]})
                self.wait("scope closure aborts active Run", lambda: self.request(path),
                          lambda value: value["run"]["status"] == "aborted")
                self.result(run_id, status=403)
                assert not any("[State Machine Run Result]" in json.dumps(row["body"].get("message"))
                               for row in self.captured()), "Closed scope delivered a private Send"
                print(f"Run scope closed, queue={queued}: active Run aborted, result access and Send suppressed", flush=True)
                return
            if mode == "abort":
                self.post(path + "/cancel", {"reason": secret})
                expected, reason = "aborted", "user_aborted"
            else:
                # A final without visible output is a genuine node failure.
                self.callback(node, self.driver, "")
                expected, reason = "failed", "node_failed"
        result = self.wait("safe terminal result", lambda: self.result(run_id),
                           lambda value: value["status"] == expected)
        terminal = result["terminal_result"]
        assert terminal["status"] == expected and terminal["reason_category"] == reason, terminal
        assert terminal["completed_node_ids"] == ["prepare"], terminal
        if mode != "abort":
            assert terminal["failed_node_id"] == "fail" and terminal["attempt"] == 0, terminal
        assert secret not in json.dumps(result), "Private input or diagnostic leaked into the result"
        message_id = "run-result:" + run_id
        rows = self.wait("private result admission", lambda: self.deliveries(message_id), bool)
        assert len(rows) == 1 and rows[0]["target_bot_id"] == self.args.driver and rows[0]["kind"] == "send", rows
        def result_requests():
            return [row for row in self.captured() if row["body"].get("method") == "chat.send"
                    and "[State Machine Run Result]" in json.dumps(row["body"].get("message"))
                    and run_id in json.dumps(row["body"].get("message"))]

        delivered = self.wait("private result Send", result_requests, bool)
        assert len(delivered) == 1 and secret not in json.dumps(delivered[0]["body"])
        self.callback(delivered[0], self.driver, "Private failure result received")
        self.wait("completed private Send", lambda: self.deliveries(message_id),
                  lambda rows: len(rows) == 1 and rows[0]["status"] == "completed")
        for _ in range(2):
            assert self.result(run_id) == result
            history = self.request(f"/sessions/{segment(self.session_id)}/messages")
            assert not any(m.get("id") == message_id for m in history), "Private result entered public history"
        # Reading again and replaying cancellation must not create another result.
        if mode == "abort":
            self.post(path + "/cancel", {"reason": "duplicate cancellation"})
            assert self.result(run_id) == result
        print(f"Run {mode}, queue={queued}: creator-only result, typed reason, completed nodes and private Send verified", flush=True)


def main():
    parser = argparse.ArgumentParser()
    for name in ("base-url", "mock-url", "human"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--mock-sink-url")
    story = RunFailureStory(parser.parse_args())
    try:
        story.setup()
        for queued in (False, True):
            for mode in ("failure", "timeout", "abort", "scope_closed"):
                story.exercise_failure(mode, queued)
    finally:
        story.cleanup()


if __name__ == "__main__":
    main()
