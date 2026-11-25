#!/usr/bin/env python3
"""Drive a state-machine Run through its node lifecycle on public entries only.

One scenario walks, in order: a retryable bot failure (error callback) that
re-dispatched attempt 2 and completed with an artifact, a Human gate answered
through the native respond endpoint, a Judge-steered branch that skips the
untaken target, a node timeout that fails the Run (timeout scanner), a rerun
of the failed Run that executes to completion, and a second Run cancelled
mid-flight. Only HTTP routes, the bcs-cli, and the Provider callback intake
advance or inspect state.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import uuid
from pathlib import Path

from fixed_loop_story import Story, bot_node, segment


def cancel_probe_definition() -> dict:
    nodes = {"task": {**bot_node("Cancel probe task"), "final_output": True}}
    return {
        "name": "E2E cancel probe",
        "participants": {"worker": {"display_name": "Worker", "required": True}},
        "runtime": {"kind": "state_machine", "state_machine": {
            "version": 2, "graph_mode": "hierarchical", "nodes": nodes,
        }},
    }


def lifecycle_definition() -> dict:
    review = bot_node("Judge the draft")
    review["judge"] = {
        "type": "llm", "criteria": ["The draft is shippable."],
        "outcomes": ["approved", "revise"],
    }
    review["transitions"] = {
        "approved": {"targets": ["ship"]},
        "revise": {"targets": ["rework"]},
    }
    nodes = {
        "prep": {
            **bot_node("Prepare draft"), "max_attempts": 2,
            "transitions": {"complete": {"targets": ["gate"]}},
        },
        "gate": {
            "kind": "human_input", "display_name": "Human decision gate",
            "instruction": "Decide whether the prepared draft may proceed.",
            "node_timeout_ms": 600000,
            "transitions": {"complete": {"targets": ["review"]}},
        },
        "review": review,
        "rework": {
            **bot_node("Rework draft"),
            "transitions": {"complete": {"targets": ["ship"]}},
        },
        "ship": {
            **bot_node("Ship result"), "final_output": True,
            "node_timeout_ms": 6000,
        },
    }
    return {
        "name": "E2E run and node lifecycle",
        "participants": {"worker": {"display_name": "Worker", "required": True}},
        "runtime": {"kind": "state_machine", "state_machine": {
            "version": 2, "graph_mode": "hierarchical", "nodes": nodes,
        }},
    }


class RunNodeLifecycleStory(Story):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args)
        self.groups: list[str] = []

    # ------------------------------------------------------------------ utils

    def captured_requests(self) -> list[dict]:
        return self.request("/control/provider/requests", mock=True)["requests"]

    def clear_provider(self) -> None:
        self.post("/control/provider/clear", mock=True)

    def wait_delivery(self, description: str) -> dict:
        requests = self.wait(
            description, self.captured_requests,
            lambda rows: any(row["body"].get("method") == "chat.send" for row in rows),
        )
        deliveries = [row for row in requests if row["body"].get("method") == "chat.send"]
        assert len(deliveries) == 1, f"expected one {description} delivery, saw {len(deliveries)}"
        return deliveries[0]

    def assert_delivery(self, delivery: dict, node_id: str, attempt: int) -> None:
        # The dispatch delivery id encodes run, node, attempt:
        # smnode-<run_id>-<node_id>-<attempt>.
        assert delivery["body"]["id"].endswith(f"-{node_id}-{attempt}"), \
            f"expected delivery for {node_id} attempt {attempt}, saw {delivery['body']['id']}"

    def bot_callback(self, delivery: dict, body: dict) -> None:
        self.clear_provider()
        response = self.wait(
            "Provider callback acceptance", lambda: self.post(
                "/bot/events", {"run_id": delivery["body"]["id"], **body},
                headers={
                    "X-BCN-Provider-Id": self.provider["provider_id"],
                    "Authorization": "Bearer " + self.worker["bot_runtime_token"],
                }, status=(200, 409)),
            lambda data: data.get("error") != "transport_conflict",
        )
        assert not response.get("error"), response

    def provide_final(self, delivery: dict, text: str) -> None:
        request = delivery["body"]
        assert request["session_id"] == self.session_id, "delivery belongs to this session"
        assert delivery["authorization"] == "Bearer " + self.provider["bcs_to_provider_token"]
        self.bot_callback(delivery, {"state": "final", "message": {"text": text}})

    def provide_error(self, delivery: dict, text: str) -> None:
        self.bot_callback(delivery, {"state": "error", "message": {"text": text}})

    def node_detail(self, node_id: str) -> dict:
        return self.request(self.run_path + "/nodes/" + segment(node_id))["node"]

    def run_view(self) -> dict:
        return self.request(self.run_path)["run"]

    def graph(self) -> dict:
        return self.request(self.run_path + "/graph")

    def start_session_run(self, session_id: str, definition_path: Path) -> None:
        run = self.cli("run", str(definition_path), "--session", session_id,
                       "--binding", "worker=" + self.worker["bot_uuid"],
                       "--input", '{"question":"Drive the run and node lifecycle"}')
        assert run["run"]["run_id"], run
        self.run_id = run["run"]["run_id"]
        self.session_id = run["run"]["session_id"]
        self.run_path = "/state-machine-runs/" + segment(self.run_id)

    # ------------------------------------------------------------------ stages

    def stage_retryable_failure(self) -> None:
        first = self.wait_delivery("first bot task delivery (attempt 1)")
        self.assert_delivery(first, "prep", 0)
        self.provide_error(first, "worker failed attempt 1")
        # The error callback marks attempt 1 failed with action=retry, then the
        # runtime re-dispatches immediately: the node re-enters running on attempt 2.
        self.wait("node re-dispatch on attempt 2",
                  lambda: self.node_detail("prep"),
                  lambda node: node["status"] == "running" and node["attempt"] == 1)
        retry = self.wait_delivery("retried bot task delivery (attempt 2)")
        self.assert_delivery(retry, "prep", 1)
        self.provide_final(retry, "prepared-draft")
        node = self.wait("attempt 2 completion",
                         lambda: self.node_detail("prep"),
                         lambda node: node["status"] == "completed")
        assert node["attempt"] == 1, "the artifact must be recorded on the retried attempt"
        assert node["outcome"] == "complete", node
        assert node["artifact_text"] == "prepared-draft", node
        print("  Retry: bot error -> attempt 2 dispatched -> completed with artifact", flush=True)

    def stage_human_gate(self) -> None:
        pending = self.wait("human gate to surface",
                            lambda: self.request(self.run_path + "/pending-human-nodes"), bool)
        assert [item["node_id"] for item in pending] == ["gate"], pending
        outcome = self.post(self.run_path + "/nodes/gate/respond",
                            {"content": "human-gate-decision"})
        accepted = outcome["node"]
        assert accepted["status"] == "completed", accepted
        assert accepted["artifact_text"] == "human-gate-decision", accepted
        assert accepted["responded_by"], "the native human respond path must record the responder"
        assert self.request(self.run_path + "/pending-human-nodes") == []
        print("  Human gate: responded through the native /respond endpoint", flush=True)

    def stage_judge_branch(self, target: str) -> str:
        review = self.wait_delivery("judged bot task delivery")
        self.assert_delivery(review, "review", 0)
        self.provide_final(review, "review-candidate")
        nodes = self.wait(
            "judge verdict to route the branch", lambda: self.graph(),
            lambda graph: any(node["node_id"] == target and node["status"] == "running"
                              for node in graph["nodes"]))
        skipped = "ship" if target == "rework" else "rework"
        by_id = {node["node_id"]: node for node in nodes["nodes"]}
        assert by_id[skipped]["status"] == "skipped", f"untaken branch {skipped} must be skipped"
        detail = self.node_detail("review")
        assert detail["status"] == "completed" and detail["artifact_text"] == "review-candidate"
        edge = next((edge for edge in nodes["edges"]
                     if edge["source"] == "review" and edge["target"] == target), None)
        assert edge is not None, "the graph must expose the judge-routed edge"
        print(f"  Judge: verdict routed to {target}; {skipped} skipped", flush=True)
        return skipped

    def stage_timeout_failure(self) -> None:
        run = self.wait("timeout scanner to fail the run",
                        self.run_view, lambda run: run["status"] == "failed")
        assert "timed out after 6000ms" in (run.get("error") or ""), run
        ship = self.node_detail("ship")
        assert ship["status"] == "failed" and ship["attempt"] == 0, ship
        assert "timed out" in (ship.get("error") or ""), ship
        assert {node["node_id"]: node for node in self.graph()["nodes"]}["gate"]["status"] == "completed", \
            "earlier node states must survive the timeout failure"
        print("  Timeout: unresponsive node failed the run; run marked failed", flush=True)

    def stage_rerun(self, skipped_before: str) -> None:
        self.clear_provider()
        self.post("/control/judge/outcomes", {"outcomes": ["revise"]}, mock=True)
        source_run_id = self.run_id
        rerun = self.request(self.run_path + "/reruns", None, method="POST", status=201)
        assert rerun["idempotent_replay"] is False, rerun
        created = rerun["run"]
        assert created["run_id"] != source_run_id, created
        assert created["rerun_of"] == source_run_id, created
        assert created["status"] in ("pending", "running"), created
        self.run_id = created["run_id"]
        self.run_path = "/state-machine-runs/" + segment(self.run_id)
        queried = self.cli("query", "--run", self.run_id)  # public CLI query for the new run
        assert queried["run"]["run_id"] == self.run_id, queried

        prep = self.wait_delivery("rerun first bot task")
        self.assert_delivery(prep, "prep", 0)
        self.provide_final(prep, "rerun-prepared-draft")
        pending = self.wait("rerun human gate",
                            lambda: self.request(self.run_path + "/pending-human-nodes"), bool)
        assert [item["node_id"] for item in pending] == ["gate"], pending
        self.post(self.run_path + "/nodes/gate/respond", {"content": "rerun-gate-decision"})
        review = self.wait_delivery("rerun judged bot task")
        self.assert_delivery(review, "review", 0)
        self.provide_final(review, "rerun-review-candidate")
        rework = self.wait_delivery("rerun rework bot task")
        self.assert_delivery(rework, "rework", 0)
        assert {node["node_id"]: node for node in self.graph()["nodes"]}[skipped_before]["status"] == "running", \
            "the rerun must re-execute the branch skipped by the source run"
        self.provide_final(rework, "rerun-reworked-result")
        ship = self.wait_delivery("rerun final bot task")
        self.assert_delivery(ship, "ship", 0)
        self.provide_final(ship, "rerun-final-output")
        run = self.wait("rerun completion", self.run_view,
                        lambda run: run["status"] == "completed")
        assert run["output"] == "rerun-final-output", run
        assert run["rerun_of"] == source_run_id, run
        nodes = {node["node_id"]: node for node in self.graph()["nodes"]}
        for node_id in ("prep", "review", "rework", "ship"):
            assert nodes[node_id]["status"] == "completed", (node_id, nodes[node_id])
        assert self.node_detail("prep")["attempt"] == 0, "the rerun must restart attempts at zero"
        # Replaying the rerun request after completion returns the same rerun
        # without creating a duplicate.
        replay = self.request("/state-machine-runs/" + segment(source_run_id) + "/reruns",
                              None, method="POST", status=200)
        assert replay["idempotent_replay"] is True, replay
        assert replay["run"]["run_id"] == self.run_id, replay
        assert replay["run"]["status"] == "completed", replay
        print("  Rerun: failed run re-executed to completion with rerouted branch", flush=True)

    def stage_cancel_mid_flight(self, directory: Path) -> None:
        session = self.post(f"/groups/{segment(self.group_id)}/sessions",
                            {"session_title": "Run cancel session"})
        self.clear_provider()
        self.start_session_run(session["session_id"], directory / "cancel-probe.json")
        delivery = self.wait_delivery("cancelled run first delivery")
        self.assert_delivery(delivery, "task", 0)
        aborted = self.post(self.run_path + "/cancel", {"reason": "E2E mid-flight abort"})
        assert aborted["run"]["status"] == "aborted", aborted
        assert self.run_view()["status"] == "aborted"
        # The aborted run must not accept a late provider callback.
        self.post("/bot/events",
                  {"run_id": delivery["body"]["id"], "state": "error", "message": {"text": "late"}},
                  headers={"X-BCN-Provider-Id": self.provider["provider_id"],
                           "Authorization": "Bearer " + self.worker["bot_runtime_token"]},
                  status=(200, 409))
        assert self.run_view()["status"] == "aborted", "a late callback must not revive the run"
        print("  Cancel: second run aborted mid-flight and fenced from late events", flush=True)

    # ------------------------------------------------------------------- main

    def setup(self) -> None:
        self.post("/control/reset", mock=True)
        self.post("/me/ensure-human")
        self.provider = self.post("/providers", {
            "name": "Run lifecycle E2E provider",
            "webhook_url": (self.args.mock_sink_url or self.args.mock_url) + "/provider/webhook",
            "auth": {"mode": "static_bearer"}, "protocol_version": "2.0",
            "coordination": {"mode": "native_tool"},
        })
        self.worker = self.post(f"/providers/{segment(self.provider['provider_id'])}/bots", {
            "name": "Run lifecycle worker", "summary": "Scripted lifecycle worker",
            "owners": [self.args.human], "provider_bot_ref": "lifecycle-e2e-" + uuid.uuid4().hex,
            "domains": ["release"], "skills": ["review"], "scopes": ["local"],
        }, headers={"Authorization": "Bearer " + self.provider["provider_admin_token"]})
        self.request(f"/bots/{segment(self.worker['bot_uuid'])}/visibility",
                     {"visibility": "public"}, method="PUT",
                     headers={"X-BCS-Bot-Token": self.worker["bot_runtime_token"]})
        self.post("/friends/request", {"from_bot": self.args.driver, "to_bot": self.worker["bot_uuid"]})

    def exercise(self, directory: Path) -> None:
        (directory / "lifecycle.json").write_text(json.dumps(lifecycle_definition()), encoding="utf-8")
        (directory / "cancel-probe.json").write_text(
            json.dumps(cancel_probe_definition()), encoding="utf-8")
        validation = self.cli("validate", str(directory / "lifecycle.json"))
        assert validation["valid"] is True, validation

        group = self.post("/groups", {
            "driver_bot": self.args.driver, "label": "Run lifecycle E2E",
            "participants": [{"bot_uuid": self.args.driver},
                             {"bot_uuid": self.worker["bot_uuid"]},
                             {"bot_uuid": "human_" + self.args.human}],
        })
        self.group_id = group["id"]
        self.groups.append(self.group_id)

        session = self.post(f"/groups/{segment(self.group_id)}/sessions",
                            {"session_title": "Run lifecycle session"})
        self.clear_provider()
        self.post("/control/judge/outcomes", {"outcomes": ["approved"]}, mock=True)
        self.start_session_run(session["session_id"], directory / "lifecycle.json")

        self.stage_retryable_failure()
        self.stage_human_gate()
        skipped = self.stage_judge_branch("ship")
        self.stage_timeout_failure()
        self.stage_rerun(skipped)
        self.stage_cancel_mid_flight(directory)

    def cleanup(self) -> None:
        for group in self.groups:
            self.request(f"/groups/{segment(group)}?bot_id={segment(self.args.driver)}",
                         method="DELETE")
        if getattr(self, "worker", None):
            self.request(f"/providers/{segment(self.provider['provider_id'])}/bots/"
                         f"{segment(self.worker['bot_uuid'])}",
                         method="DELETE",
                         headers={"Authorization": "Bearer " + self.provider["provider_admin_token"]})
        self.post("/control/reset", mock=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("base-url", "mock-url", "cli", "driver", "human"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--mock-sink-url", required=False)
    story = RunNodeLifecycleStory(parser.parse_args())
    try:
        story.setup()
        with tempfile.TemporaryDirectory(prefix="bcs-lifecycle-e2e-") as directory:
            story.exercise(Path(directory))
    finally:
        story.cleanup()


if __name__ == "__main__":
    main()
