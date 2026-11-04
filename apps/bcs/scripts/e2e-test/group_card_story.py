#!/usr/bin/env python3
"""Group Card persistence through a real BCS process and read-only SQL assertions.

All business writes go through HTTP. An isolated database avoids inspecting or
mutating another story's state; the production binary needs no test-only API.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid


def assert_card(card: dict, group_id: str, name: str, strategy: str) -> None:
    prefix = {"manager_worker": "Manager–Worker 群：", "state_machine": "State Machine 群："}[strategy]
    assert card["name"] == name, "Card name differs from the Group label"
    assert card["description"] == prefix + name, "Card description is stale"
    assert card["skills"] == [{"id": "group.execute", "name": name,
                               "description": prefix + name, "tags": [strategy]}]
    assert card["supportedInterfaces"] == [{"url": f"local://groups/{group_id}",
                                            "protocolBinding": "LOCAL", "protocolVersion": "1.0"}]
    extension = card["capabilities"]["extensions"][0]
    assert extension["params"] == {"groupKind": strategy}
    assert card["defaultInputModes"] == card["defaultOutputModes"] == ["text/plain"]
    for field in ("version", "revision", "providerName", "agents", "composition"):
        assert field not in card, f"Dynamic field {field} leaked into the static template"


class Story:
    def __init__(self, base_url: str, db_path: Path) -> None:
        self.base_url = base_url
        self.db_path = db_path
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.owner = "groupcard" + uuid.uuid4().hex
        self.bot = ""
        self.token = ""
        self.checks = 0

    def request(self, method: str, path: str, body=None, *, status: int = 200):
        headers = {"Content-Type": "application/json", "X-Mock-User-Id": self.owner,
                   "X-Mock-Nick-Name": "Group Card operator"}
        if self.token:
            headers["X-BCS-Bot-Token"] = self.token
        request = urllib.request.Request(self.base_url + path, method=method, headers=headers,
                                         data=None if body is None else json.dumps(body).encode())
        try:
            response = self.http.open(request, timeout=20)
        except urllib.error.HTTPError as failure:
            response = failure
        with response:
            code, raw = response.code, response.read()
        # Registration responses and query parameters can contain credentials.
        detail = json.loads(raw).get("error", "") if path.startswith("/groups") and code != status else ""
        assert code == status, f"{method} {path.split('?', 1)[0]}: HTTP {code}, expected {status}: {detail}"
        self.checks += 1
        return json.loads(raw) if raw else {}

    def rows(self, table: str, group_id: str):
        assert table in ("bcs_groups", "bcs_group_agent_cards")
        with sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(f"SELECT * FROM {table} WHERE env = ? AND group_id = ?",
                              ("local", group_id)).fetchall()
        self.checks += 1
        return rows

    def card(self, group_id: str, name: str, strategy: str) -> dict:
        groups = self.rows("bcs_groups", group_id)
        assert len(groups) == 1, f"Expected one Group in local env, got {len(groups)}"
        assert (groups[0]["label"] or group_id) == name, (groups[0]["label"], name)
        cards = self.rows("bcs_group_agent_cards", group_id)
        assert len(cards) == 1, "Expected exactly one persisted Group Card"
        card = json.loads(cards[0]["card_template"])
        assert_card(card, group_id, name, strategy)
        return card

    def run(self) -> None:
        self.request("POST", "/me/ensure-human", {})
        token = self.request("GET", "/register/token")["token"]
        query = urllib.parse.urlencode({"token": token, "bot-name": "Group Card driver"})
        registered = self.request("POST", "/register?" + query, {})
        self.bot, self.token = registered["bot_uuid"], registered["bot_token"]
        self.request("POST", "/bots/onboard", {"name": "Group Card driver", "summary": "Card lifecycle E2E"})
        self.request("PUT", f"/bots/{self.bot}/visibility", {"visibility": "public"})
        for strategy, visibility in (("manager_worker", "private"), ("state_machine", "public"), ("chat", "private")):
            body = {"driver_bot": self.bot, "participants": [{"bot_uuid": self.bot}],
                    "group_strategy": strategy, "visibility": visibility, "label": "初始群"}
            if strategy == "manager_worker":
                body["participants"][0]["role"] = "manager"
            if strategy == "state_machine":
                body["label"] = "状态机群"
                body["collaboration_definition_yaml"] = f'''name: Card lifecycle
participants:
  driver:
    bot_id: "{self.bot}"
    required: true
runtime:
  kind: state_machine
  state_machine:
    graph_mode: acyclic
    nodes:
      confirmation:
        display_name: Confirm
        kind: human_input
        node_timeout_ms: 600000
        instruction: Confirm the group card.
        transitions:
          complete:
            targets: [answer]
      answer:
        display_name: Answer
        kind: bot_task
        assignee:
          type: bot_binding
          binding: driver
        instruction: Return the answer.
        final_output: true
'''
            created = self.request("POST", "/groups", body)
            group_id = created["id"]
            assert self.request("GET", f"/groups/{group_id}")["visibility"] == visibility
            if strategy == "chat":
                assert not self.rows("bcs_group_agent_cards", group_id), "Chat must not get a Group Card"
            else:
                self.card(group_id, body.get("label", group_id), strategy)
                # Exercise both retained label and general Group mutation routes.
                for label in ('改名 "\\ with Unicode 群', "", "最终名称"):
                    self.request("PUT", f"/groups/{group_id}/label", {"label": label})
                    self.card(group_id, label or group_id, strategy)
                self.request("PATCH", f"/groups/{group_id}", {"name": "PATCH 名称"})
                snapshot = self.card(group_id, "PATCH 名称", strategy)
                self.request("PATCH", f"/groups/{group_id}", {"context": "Only dynamic context changed"})
                assert self.card(group_id, "PATCH 名称", strategy) == snapshot
            self.request("DELETE", f"/groups/{group_id}?bot_id={self.bot}")
            assert not self.rows("bcs_groups", group_id), "Group deletion was not persisted"
            assert not self.rows("bcs_group_agent_cards", group_id), "Orphaned Group Card after deletion"
        self.request("DELETE", f"/bots/{self.bot}")
        print(f"Group Card lifecycle E2E: {self.checks} HTTP/SQL checks passed", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    with tempfile.TemporaryDirectory(prefix="bcs-group-card-") as directory:
        root = Path(directory)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        config_file = Path(__file__).resolve().parents[2] / "configs/bcs-config-local.toml"
        config = tomllib.loads(config_file.read_text())
        config.update({"bind": "127.0.0.1", "port": port, "bcs_endpoint": base_url,
                       "strict_container_validation": False,
                       "auth": {"chain": ["local"], "allow_mock_headers": True},
                       "database": {"type": "sqlite", "sqlite": {"path": str(root / "bcs.db")}},
                       "llm": {"type": "none"}, "bcsfuse": {"enabled": False}})
        config["manifest"]["bundles"] = []
        config["secret"] = {"provider": "env", "providers": {"env": {"prefix": "BCS_SECRET_"}}}
        config["bots_base_dir"] = str(root / "bots")
        config["session_files"]["backend"] = {"data_dir": str(root / "files")}
        config["collaboration"]["templates"]["base_dir"] = str(root / "templates")
        config_path = root / "bcs-config-local.json"
        config_path.write_text(json.dumps(config))
        env = dict(os.environ, SERVER_ENV="local", BCS_SECRET_BCN_GROUP_SESSION_WS_JWT=uuid.uuid4().hex,
                   AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE=uuid.uuid4().hex)
        # Retain LLVM_PROFILE_FILE so this real process contributes to the same
        # E2E profile pool as Singlebox. Do not execute any Rust unit-test binary.
        with (root / "server.log").open("w") as log:
            process = subprocess.Popen([binary, "-c", str(config_path)], cwd=root,
                                       env=env, stdout=log, stderr=log)
            try:
                story = Story(base_url, root / "bcs.db")
                for _ in range(150):
                    if process.poll() is not None:
                        raise RuntimeError("BCS exited during Group Card story startup")
                    try:
                        story.request("GET", "/health")
                        break
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.2)
                else:
                    raise RuntimeError("BCS startup timed out")
                story.run()
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        raise RuntimeError("BCS did not exit gracefully; coverage may be incomplete")


if __name__ == "__main__":
    main()
