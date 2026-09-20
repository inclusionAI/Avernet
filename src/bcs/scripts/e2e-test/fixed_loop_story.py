#!/usr/bin/env python3
"""Live Loop story. Only public HTTP/CLI operations advance or inspect a Run."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def bot_node(name: str) -> dict:
    return {
        "kind": "bot_task", "display_name": name,
        "assignee": {"type": "bot_binding", "binding": "worker"},
        "instruction": "Use the preceding input and return the requested editorial result.",
    }


def definition(approved: bool) -> dict:
    review = bot_node("Editor review")
    review["judge"] = {
        "type": "llm", "criteria": ["The editor approves the content."],
        "outcomes": ["revise", "approved"],
    }
    nodes = {
        "cycle": {
            "kind": "loop", "display_name": "Editorial loop",
            "loop": {
                "mode": "fixed", "max_iterations": 3 if approved else 2,
                "entry_node": "input", "result_node": "review",
                "continue_outcomes": ["revise"], "break_outcomes": ["approved"],
                "exhausted_outcome": "exhausted",
                "nodes": {
                    "input": {
                        "kind": "human_input", "display_name": "Author input",
                        "instruction": "Revise the content using the previous editorial result.",
                        "node_timeout_ms": 60000,
                        "transitions": {"complete": {"targets": ["review"]}},
                    },
                    "review": review,
                },
            },
            "transitions": {
                "approved": {"targets": ["polish"], "display_name": "Ready to polish"},
                "exhausted": {"targets": ["rewrite"], "display_name": "Rewrite required"},
            },
        },
        "polish": bot_node("Polish approved content"),
        "rewrite": bot_node("Rewrite unapproved content"),
        "summary": {**bot_node("Summarize result"), "final_output": True},
    }
    for branch in ("polish", "rewrite"):
        nodes[branch]["transitions"] = {"complete": {"targets": ["summary"]}}
    return {
        "name": "E2E editorial workflow",
        "participants": {"worker": {"display_name": "Editor", "required": True}},
        "runtime": {"kind": "state_machine", "state_machine": {
            "version": 2, "graph_mode": "hierarchical", "nodes": nodes,
        }},
    }


class Story:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.token = os.environ["BCS_E2E_DRIVER_TOKEN"]
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.groups: list[str] = []
        self.bot: dict = {}
        self.provider: dict = {}

    def request(self, path: str, body=None, *, method: str = "GET",
                mock: bool = False, headers=None, status=None):
        url = (self.args.mock_url if mock else self.args.base_url) + path
        auth = {} if mock else {"X-Mock-User-Id": self.args.human}
        request = urllib.request.Request(
            url, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **auth, **(headers or {})},
        )
        try:
            response = self.http.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            code, raw = response.code, response.read().decode()
        expected = code in status if isinstance(status, tuple) else code == status if status else 200 <= code < 300
        assert expected, f"{method} {path}: HTTP {code}: {raw}"
        return json.loads(raw) if raw else None

    def post(self, path: str, body=None, **kwargs):
        return self.request(path, {} if body is None else body, method="POST", **kwargs)

    def cli(self, *args: str, text: bool = False):
        command = [self.args.cli, *([] if text else ["--json"]), "collaborate", "--token", self.token, *args]
        coverage_log = os.environ.get("BCS_CLI_COVERAGE_LOG")
        if coverage_log:
            with open(coverage_log, "a", encoding="utf-8") as output:
                output.write(f"collaboration {args[0]}\n")
        result = subprocess.run(command, env={**os.environ, "MOLTIS_BCS_URL": self.args.base_url},
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"CLI {args[0]}: {result.stderr.replace(self.token, '[redacted]')}"
        return result.stdout if text else json.loads(result.stdout)

    def wait(self, description, read, predicate):
        deadline = time.monotonic() + 20
        value = None
        while time.monotonic() < deadline:
            value = read()
            if predicate(value):
                return value
            time.sleep(0.1)
        raise AssertionError(f"Timed out waiting for {description}")

    def setup(self) -> None:
        self.post("/control/reset", mock=True)
        self.post("/me/ensure-human")
        self.provider = self.post("/providers", {
            "name": "Loop E2E provider", "webhook_url": self.args.mock_url + "/provider/webhook",
            "auth": {"mode": "static_bearer"}, "protocol_version": "2.0",
            "coordination": {"mode": "native_tool"},
        })
        provider_id = self.provider["provider_id"]
        self.bot = self.post(f"/providers/{segment(provider_id)}/bots", {
            "name": "Loop E2E editor", "summary": "Scripted editorial provider",
            "owners": [self.args.human], "provider_bot_ref": "loop-e2e-" + uuid.uuid4().hex,
            "domains": ["release"], "skills": ["review"], "scopes": ["local"],
        }, headers={"Authorization": "Bearer " + self.provider["provider_admin_token"]})
        self.request(f"/bots/{segment(self.bot['bot_uuid'])}/visibility", {"visibility": "public"}, method="PUT")
        self.post("/friends/request", {"from_bot": self.args.driver, "to_bot": self.bot["bot_uuid"]})

    def start(self, approved: bool, directory: Path) -> None:
        self.post("/control/provider/clear", mock=True)
        self.post("/control/judge/outcomes", {"outcomes": ["revise", "approved" if approved else "revise"]}, mock=True)
        doc = definition(approved)
        path = directory / "editorial.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        validation = self.cli("validate", str(path))
        assert validation["valid"] is True, validation
        group_body = {
            "driver_bot": self.args.driver, "label": "Loop E2E " + ("approved" if approved else "exhausted"),
            "participants": [{"bot_uuid": actor} for actor in (self.args.driver, self.bot["bot_uuid"], "human_" + self.args.human)],
        }
        if approved:
            group_body.update({
                "group_strategy": "state_machine", "collaboration_definition_yaml": json.dumps(doc),
                "start_initial_run": False,
                "participant_bindings": {"worker": {"source": "manual", "bot_ids": [self.bot["bot_uuid"]]}},
            })
        group = self.post("/groups", group_body)
        self.group_id = group["id"]
        self.groups.append(self.group_id)
        if approved:
            run = self.post(f"/groups/{segment(self.group_id)}/state-machine-runs", {"input": {"question": "Review the draft"}})
        else:
            session = self.post(f"/groups/{segment(self.group_id)}/sessions", {"session_title": "One-shot editorial trial"})
            session_id = session["session_id"]
            run = self.cli("run", str(path), "--session", session_id,
                           "--binding", "worker=" + self.bot["bot_uuid"], "--input", '{"question":"Review the draft"}')
        self.run_id, self.session_id = run["run"]["run_id"], run["run"]["session_id"]
        self.run_path = "/state-machine-runs/" + segment(self.run_id)

    def pending(self) -> dict:
        self.wait("Human input", lambda: self.request(self.run_path + "/pending-human-nodes"), bool)
        pending = self.cli("query", "--run", self.run_id, "--pending")
        assert len(pending) == 1, pending
        return pending[0]

    def finish_bot(self, content: str, previous: str) -> None:
        captured = self.wait("Provider chat.send", lambda: self.request("/control/provider/requests", mock=True)["requests"],
                             lambda items: any(item["body"].get("method") == "chat.send" for item in items))
        requests = [item for item in captured if item["body"].get("method") == "chat.send"]
        assert len(requests) == 1, "Unexpected concurrent or duplicate Provider delivery"
        request = requests[0]
        assert request["authorization"] == "Bearer " + self.provider["bcs_to_provider_token"]
        assert request["body"]["bcn_group_id"] == self.session_id
        assert previous in json.dumps(request["body"]["message"]), "Provider prompt lost the preceding output"
        self.post("/control/provider/clear", mock=True)
        # The mock records chat.send before BCS has consumed its HTTP ACK.
        # Callback intake rejects events while transport negotiation is still
        # in progress; retry that explicit rejection, never a timeout/lost ACK.
        response = self.wait("Provider callback transport negotiation", lambda: self.post(
            "/bot/events", {"run_id": request["body"]["id"], "state": "final", "message": {"text": content}},
            headers={"X-BCN-Provider-Id": self.provider["provider_id"], "Authorization": "Bearer " + self.bot["bot_runtime_token"]},
            status=(200, 409)), lambda body: body.get("error") != "transport_conflict")
        assert not response.get("error"), response

    def exercise(self, approved: bool, directory: Path) -> None:
        self.start(approved, directory)
        first = self.pending()
        assert first["loop_context"]["iteration"] == 1
        assert first["loop_context"].get("previous_result") is None
        self.cli("respond", "--run", self.run_id, "--node", first["node_id"], "--content", "first-human-input")
        self.finish_bot("first-editor-result", "first-human-input")
        second = self.pending()
        assert second["loop_context"]["iteration"] == 2
        assert second["response_ref"] != first["response_ref"]
        assert second["loop_context"]["previous_result"]["output"] == "first-editor-result"
        assert second["loop_context"]["previous_result"]["outcome"] == "revise"
        # A stale execution must not consume the next Human input.
        self.post(self.run_path + "/nodes/" + segment(first["node_id"]) + "/respond", {"content": "stale input"}, status=409)
        assert self.pending()["response_ref"] == second["response_ref"]
        rendered = self.cli("query", "--run", self.run_id, "--pending", text=True)
        assert "first-editor-result" in rendered and second["node_id"] in rendered
        self.cli("respond", "--run", self.run_id, "--node", second["node_id"], "--content", "second-human-input")
        self.finish_bot("second-editor-result", "second-human-input")
        branch = "polish" if approved else "rewrite"
        self.finish_bot(branch + "-result", "second-editor-result")
        self.finish_bot("final-editorial-output", branch + "-result")
        self.verify(approved)
        print(f"  Loop {'approved' if approved else 'exhausted'}: input isolation, routing, metadata and history verified", flush=True)

    def verify(self, approved: bool) -> None:
        self.wait("completed workflow", lambda: self.request(self.run_path), lambda run: run["run"]["status"] == "completed")
        run = self.cli("query", "--run", self.run_id)
        assert run["run"]["output"] == "final-editorial-output"
        graph = self.cli("query", "--run", self.run_id, "--graph")
        nodes = {node["node_id"]: node for node in graph["nodes"]}
        assert nodes["polish" if approved else "rewrite"]["status"] == "completed"
        assert nodes["rewrite" if approved else "polish"]["status"] == "skipped"
        if approved:
            future = [node for node in nodes.values() if (node.get("execution") or {}).get("iteration") == 3]
            assert len(future) == 2 and all(node["status"] == "skipped" for node in future)
        result = next(node for node in nodes.values() if (node.get("execution") or {}).get("iteration") == 2
                      and node["execution"]["definition_node_id"] == "review")
        assert result["outcome"] == ("approved" if approved else "revise")
        edge = next(edge for edge in graph["edges"] if edge["source"] == result["node_id"] and edge["target"] == ("polish" if approved else "rewrite"))
        assert edge["loop_route"]["logical_outcome"] == ("approved" if approved else "exhausted")
        history_path = f"/sessions/{segment(self.session_id)}/messages"
        history = self.request(history_path)
        outputs = [message for message in history if (message.get("metadata") or {}).get("state_machine", {}).get("event") == "output"]
        assert len(outputs) == 6, "Each completed execution must publish exactly one output"
        for node in run["nodes"]:
            node_id = node["node_id"]
            detail = self.cli("query", "--run", self.run_id, "--node", node_id)
            assert nodes[node_id]["status"] == node["status"]
            assert detail.get("execution") == nodes[node_id].get("execution") == run.get("node_execution_metadata", {}).get(node_id)
            matching = [message for message in outputs if message["metadata"]["state_machine"]["node_id"] == node_id]
            if node["status"] == "skipped":
                assert not matching
            else:
                assert len(matching) == 1
                assert matching[0]["content"] == node["artifact_text"]
                assert matching[0]["metadata"]["state_machine"].get("execution") == detail.get("execution")
        # Reloading the persisted graph must not change snapshots or republish.
        assert self.cli("query", "--run", self.run_id, "--graph") == graph
        assert self.request(history_path) == history
        if not approved:
            session = self.request(f"/sessions/{segment(self.session_id)}")
            assert session["status"] == "running", "One-shot completion must preserve the Chat session"
            published = [message for message in history if message.get("content") == "final-editorial-output"
                         and (message.get("metadata") or {}).get("state_machine", {}).get("event") != "output"]
            assert len(published) == 1, "One-shot final result must reach chat exactly once"

    def cleanup(self) -> None:
        for group in self.groups:
            self.request(f"/groups/{segment(group)}?bot_id={segment(self.args.driver)}", method="DELETE")
        if self.bot:
            self.request(f"/providers/{segment(self.provider['provider_id'])}/bots/{segment(self.bot['bot_uuid'])}",
                         method="DELETE", headers={"Authorization": "Bearer " + self.provider["provider_admin_token"]})
        self.post("/control/reset", mock=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("base-url", "mock-url", "cli", "driver", "human"):
        parser.add_argument("--" + name, required=True)
    story = Story(parser.parse_args())
    try:
        story.setup()
        with tempfile.TemporaryDirectory(prefix="bcs-loop-e2e-") as directory:
            story.exercise(True, Path(directory))
            story.exercise(False, Path(directory))
    finally:
        story.cleanup()


if __name__ == "__main__":
    main()
