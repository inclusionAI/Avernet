#!/usr/bin/env python3
"""Live nested Loop stories driven only through public HTTP and bcs-cli."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import uuid
from pathlib import Path

from fixed_loop_story import Story, bot_node, segment


def nested_bot_definition() -> dict:
    review = bot_node("Review draft")
    review["judge"] = {
        "type": "llm", "criteria": ["Whether the draft is ready"],
        "outcomes": ["revise", "approved"],
    }
    assess = bot_node("Assess revision")
    assess["judge"] = {
        "type": "llm", "criteria": ["Whether the overall result is ready"],
        "outcomes": ["repeat", "done"],
    }
    draft = bot_node("Revise draft")
    draft["transitions"] = {"complete": {"targets": ["review"]}}
    prepare = bot_node("Prepare revision")
    prepare["transitions"] = {"complete": {"targets": ["inner"]}}
    return {
        "name": "Nested Bot review E2E",
        "participants": {"worker": {"required": True}},
        "runtime": {"kind": "state_machine", "state_machine": {
            "version": 2, "graph_mode": "hierarchical", "nodes": {
                "outer": {
                    "kind": "loop", "display_name": "Overall revision",
                    "loop": {
                        "mode": "fixed", "max_iterations": 2,
                        "entry_node": "prepare", "result_node": "assess",
                        "continue_outcomes": ["repeat"], "break_outcomes": ["done"],
                        "exhausted_outcome": "capped", "nodes": {
                            "prepare": prepare,
                            "inner": {
                                "kind": "loop", "display_name": "Local review",
                                "loop": {
                                    "mode": "fixed", "max_iterations": 3,
                                    "entry_node": "draft", "result_node": "review",
                                    "continue_outcomes": ["revise"],
                                    "break_outcomes": ["approved"],
                                    "exhausted_outcome": "exhausted",
                                    "nodes": {"draft": draft, "review": review},
                                },
                                "transitions": {
                                    "approved": {"targets": ["assess"]},
                                    "exhausted": {"targets": ["assess"]},
                                },
                            },
                            "assess": assess,
                        },
                    },
                    "transitions": {
                        "done": {"targets": ["publish"]},
                        "capped": {"targets": ["publish"]},
                    },
                },
                "publish": {**bot_node("Publish"), "final_output": True},
            },
        }},
    }


def human_node(name: str, instruction: str) -> dict:
    return {
        "kind": "human_input", "display_name": name,
        "instruction": instruction, "node_timeout_ms": 60000,
    }


def nested_human_definition() -> dict:
    inner_review = human_node("Inner review", "Review the innermost result.")
    middle_gate = human_node("Middle gate", "Confirm the middle iteration.")
    middle_gate["transitions"] = {"complete": {"targets": ["inner"]}}
    middle_result = human_node("Middle result", "Confirm the middle result.")
    outer_gate = human_node("Outer gate", "Confirm the outer iteration.")
    outer_gate["transitions"] = {"complete": {"targets": ["middle"]}}
    outer_result = human_node("Outer result", "Decide whether another outer iteration is required.")
    outer_result["judge"] = {
        "type": "llm", "criteria": ["Whether the overall review is complete"],
        "outcomes": ["repeat", "done"],
    }
    return {
        "name": "Three-level Human review E2E",
        "participants": {"worker": {"required": True}},
        "runtime": {"kind": "state_machine", "state_machine": {
            "version": 2, "graph_mode": "hierarchical",
            "nodes": {
                "outer": {
                    "kind": "loop", "display_name": "Outer review",
                    "loop": {
                        "mode": "fixed", "max_iterations": 3,
                        "entry_node": "outer_gate", "result_node": "outer_result",
                        "continue_outcomes": ["repeat"], "break_outcomes": ["done"],
                        "exhausted_outcome": "outer_exhausted", "nodes": {
                            "outer_gate": outer_gate,
                            "middle": {
                                "kind": "loop", "display_name": "Middle review",
                                "loop": {
                                    "mode": "fixed", "max_iterations": 2,
                                    "entry_node": "middle_gate", "result_node": "middle_result",
                                    "continue_outcomes": ["complete"], "break_outcomes": [],
                                    "exhausted_outcome": "middle_exhausted", "nodes": {
                                        "middle_gate": middle_gate,
                                        "inner": {
                                            "kind": "loop", "display_name": "Inner review",
                                            "loop": {
                                                "mode": "fixed", "max_iterations": 2,
                                                "entry_node": "inner_review", "result_node": "inner_review",
                                                "continue_outcomes": ["complete"], "break_outcomes": [],
                                                "exhausted_outcome": "inner_exhausted",
                                                "nodes": {"inner_review": inner_review},
                                            },
                                            "transitions": {"inner_exhausted": {"targets": ["middle_result"]}},
                                        },
                                        "middle_result": middle_result,
                                    },
                                },
                                "transitions": {"middle_exhausted": {"targets": ["outer_result"]}},
                            },
                            "outer_result": outer_result,
                        },
                    },
                    "transitions": {
                        "done": {"targets": ["publish"]},
                        "outer_exhausted": {"targets": ["publish"]},
                    },
                },
                "publish": {**bot_node("Publish Human review"), "final_output": True},
            },
        }},
    }


class NestedStory(Story):
    def setup(self) -> None:
        self.post("/control/reset", mock=True)
        self.post("/me/ensure-human")
        self.provider = self.post("/providers", {
            "name": "Nested Loop E2E provider", "webhook_url": self.args.mock_url + "/provider/webhook",
            "auth": {"mode": "static_bearer"}, "protocol_version": "2.0",
            "coordination": {"mode": "native_tool"},
        })
        provider_id = self.provider["provider_id"]
        self.bot = self.post(f"/providers/{segment(provider_id)}/bots", {
            "name": "Nested Loop E2E worker", "summary": "Scripted nested-loop provider",
            "owners": [self.args.human], "provider_bot_ref": "nested-loop-e2e-" + uuid.uuid4().hex,
            "domains": ["release"], "skills": ["review"], "scopes": ["local"],
        }, headers={"Authorization": "Bearer " + self.provider["provider_admin_token"]})
        # Provider-admin publication mints the declared owner's Human
        # authority edge (trusted delegation), so the story's owner can
        # publish visibility with the regular human headers — this keeps
        # the admin-minted edge under live e2e coverage.
        self.request(f"/bots/{segment(self.bot['bot_uuid'])}/visibility", {"visibility": "public"}, method="PUT")
        self.args.driver = self.bot["bot_uuid"]
        self.token = self.bot["bot_runtime_token"]

    def start_definition(self, document: dict, name: str, directory: Path) -> None:
        self.post("/control/provider/clear", mock=True)
        path = directory / f"{name}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        validation = self.cli("validate", str(path))
        assert validation["valid"] is True, validation
        group = self.post("/groups", {
            "driver_bot": self.args.driver, "label": "Nested Loop E2E " + name,
            "participants": [{"bot_uuid": actor} for actor in dict.fromkeys((
                self.args.driver, self.bot["bot_uuid"], "human_" + self.args.human,
            ))],
            "group_strategy": "state_machine",
            "collaboration_definition_yaml": json.dumps(document),
            "start_initial_run": False,
            "participant_bindings": {
                "worker": {"source": "manual", "bot_ids": [self.bot["bot_uuid"]]},
            },
        })
        self.group_id = group["id"]
        self.groups.append(self.group_id)
        run = self.post(
            f"/groups/{segment(self.group_id)}/state-machine-runs",
            {"input": {"question": name + "-input"}},
        )
        self.run_id = run["run"]["run_id"]
        self.session_id = run["run"]["session_id"]
        self.run_path = "/state-machine-runs/" + segment(self.run_id)

    def replay_terminal_callback(self, delivery_id: str, content: str) -> None:
        before = self.wait(
            "next Provider request", lambda: self.request("/control/provider/requests", mock=True)["requests"], bool,
        )
        response = self.post(
            "/bot/events", {"run_id": delivery_id, "state": "final", "message": {"text": content}},
            headers={
                "X-BCN-Provider-Id": self.provider["provider_id"],
                "Authorization": "Bearer " + self.bot["bot_runtime_token"],
            }, status=(200, 409, 410),
        )
        time.sleep(0.2)
        after = self.request("/control/provider/requests", mock=True)["requests"]
        assert [item["body"]["id"] for item in after] == [item["body"]["id"] for item in before]
        assert not response.get("created", False), response

    def exercise_bot_story(self, directory: Path) -> None:
        self.post("/control/judge/outcomes", {
            "outcomes": ["revise", "approved", "repeat", "revise", "revise", "revise", "done"],
        }, mock=True)
        self.start_definition(nested_bot_definition(), "nested-bot-story", directory)
        steps = [
            ("outer-1-prepare", "nested-bot-story-input"),
            ("outer-1-inner-1-draft", "outer-1-prepare"),
            ("outer-1-inner-1-review", "outer-1-inner-1-draft"),
            ("outer-1-inner-2-draft", "outer-1-inner-1-review"),
            ("outer-1-inner-2-review", "outer-1-inner-2-draft"),
            ("outer-1-assess", "outer-1-inner-2-review"),
            ("outer-2-prepare", "outer-1-assess"),
            ("outer-2-inner-1-draft", "outer-2-prepare"),
            ("outer-2-inner-1-review", "outer-2-inner-1-draft"),
            ("outer-2-inner-2-draft", "outer-2-inner-1-review"),
            ("outer-2-inner-2-review", "outer-2-inner-2-draft"),
            ("outer-2-inner-3-draft", "outer-2-inner-2-review"),
            ("outer-2-inner-3-review", "outer-2-inner-3-draft"),
            ("outer-2-assess", "outer-2-inner-3-review"),
            ("nested-bot-published", "outer-2-assess"),
        ]
        early_break_delivery = None
        for index, (content, previous) in enumerate(steps):
            delivery_id = self.finish_bot(content, previous)
            if index == 4:
                early_break_delivery = delivery_id
                self.replay_terminal_callback(delivery_id, "stale-inner-review")
        self.verify_bot_story()
        assert early_break_delivery
        print("  Nested Bot: 17/15/2 routing, paths, history and stale callback verified", flush=True)

    def verify_bot_story(self) -> None:
        self.wait("nested Bot completion", lambda: self.request(self.run_path),
                  lambda run: run["run"]["status"] == "completed")
        graph = self.cli("query", "--run", self.run_id, "--graph")
        assert set(graph["loops"]) == {"outer", "outer/inner"}
        assert len(graph["nodes"]) == 17
        completed = [node for node in graph["nodes"] if node["status"] == "completed"]
        skipped = [node for node in graph["nodes"] if node["status"] == "skipped"]
        assert len(completed) == 15 and len(skipped) == 2
        for node in skipped:
            path = node["execution"]["loop_path"]
            assert [(frame["loop_id"], frame["iteration"]) for frame in path] == [
                ("outer", 1), ("inner", 3),
            ]
        self.verify_history(graph, 15)

    def exercise_human_story(self, directory: Path) -> None:
        self.post("/control/judge/outcomes", {"outcomes": ["repeat", "done"]}, mock=True)
        self.start_definition(nested_human_definition(), "nested-human-story", directory)
        first_inner: dict[tuple[int, int], tuple[str, str]] = {}
        stale = None
        for index in range(20):
            pending_nodes = self.wait(
                "nested Human input", lambda: self.request(self.run_path + "/pending-human-nodes"), bool,
            )
            assert len(pending_nodes) == 1, pending_nodes
            pending = pending_nodes[0]
            execution = pending["execution"]
            path = execution["loop_path"]
            assert 1 <= len(path) <= 3
            context = pending.get("loop_context")
            if context is not None:
                assert context["loop_path"] == path
            if execution["definition_node_id"] == "inner_review":
                parent = (path[0]["iteration"], path[1]["iteration"])
                if path[2]["iteration"] == 1:
                    assert context.get("previous_result") is None
                    first_inner[parent] = (pending["node_id"], f"human-result-{index}")
                else:
                    previous = context["previous_result"]
                    assert previous["execution_node_id"] == first_inner[parent][0]
                    assert previous["output"] == first_inner[parent][1]
            content = f"human-result-{index}"
            self.post(self.run_path + "/nodes/" + segment(pending["node_id"]) + "/respond",
                      {"content": content})
            if stale is None and execution["definition_node_id"] == "inner_review":
                stale = pending["node_id"]
                self.post(self.run_path + "/nodes/" + segment(stale) + "/respond",
                          {"content": "stale-inner-response"}, status=409)
        assert len(first_inner) == 4
        assert stale
        self.finish_bot("nested-human-published", "human-result-19")
        self.verify_human_story()
        print("  Nested Human: three levels, parent isolation, stale reply and outer subtree skip verified", flush=True)

    def verify_human_story(self) -> None:
        self.wait("nested Human completion", lambda: self.request(self.run_path),
                  lambda run: run["run"]["status"] == "completed")
        # Runs containing HumanInput are deliberately Human-authenticated;
        # the Bot runtime token used by bcs-cli must not bypass that boundary.
        graph = self.request(self.run_path + "/graph")
        assert set(graph["loops"]) == {"outer", "outer/middle", "outer/middle/inner"}
        assert len(graph["nodes"]) == 31
        completed = [node for node in graph["nodes"] if node["status"] == "completed"]
        skipped = [node for node in graph["nodes"] if node["status"] == "skipped"]
        assert len(completed) == 21 and len(skipped) == 10
        assert all(node["execution"]["loop_path"][0]["iteration"] == 3 for node in skipped)
        self.verify_history(graph, 21)

    def verify_history(self, graph: dict, expected_outputs: int) -> None:
        history = self.request(f"/sessions/{segment(self.session_id)}/messages")
        outputs = [message for message in history
                   if (message.get("metadata") or {}).get("state_machine", {}).get("event") == "output"]
        assert len(outputs) == expected_outputs
        by_node = {}
        for message in outputs:
            metadata = message["metadata"]["state_machine"]
            by_node.setdefault(metadata["node_id"], []).append(message)
        for node in graph["nodes"]:
            matches = by_node.get(node["node_id"], [])
            if node["status"] == "completed":
                assert len(matches) == 1
                assert matches[0]["metadata"]["state_machine"].get("execution") == node.get("execution")
            else:
                assert not matches


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("base-url", "mock-url", "cli", "driver", "human"):
        parser.add_argument("--" + name, required=True)
    story = NestedStory(parser.parse_args())
    try:
        story.setup()
        with tempfile.TemporaryDirectory(prefix="bcs-nested-loop-e2e-") as directory:
            story.exercise_bot_story(Path(directory))
            story.exercise_human_story(Path(directory))
    finally:
        story.cleanup()


if __name__ == "__main__":
    main()
