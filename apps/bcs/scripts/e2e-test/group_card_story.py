#!/usr/bin/env python3
"""Group Card persistence through a real BCS process and read-only SQL assertions.

All business writes go through HTTP. An isolated database avoids inspecting or
mutating another story's state; the production binary needs no test-only API.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid


def assert_card(card: dict, group_id: str, name: str, strategy: str, context: str) -> None:
    assert card["name"] == name, "Card name differs from the Group label"
    assert card["description"] == context, "Card description differs from the Group context"
    assert card["skills"] == [{"id": "group.execute", "name": name,
                               "description": context, "tags": [strategy]}]
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
        assert_card(card, group_id, name, strategy, groups[0]["context"] or "")
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
                    "group_strategy": strategy, "visibility": visibility, "label": "初始群", "context": "初始目标与背景"}
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
                # Chat/DM Groups reject normalized non-empty catalog labels.
                self.request("POST", "/groups", {**body, "group_kind": "dm",
                    "catalog_labels": {"lifecycle": ["trial"]}}, status=400)
            else:
                self.card(group_id, body.get("label", group_id), strategy)
                # Exercise both retained label and general Group mutation routes.
                for label in ('改名 "\\ with Unicode 群', "", "最终名称"):
                    self.request("PUT", f"/groups/{group_id}/label", {"label": label})
                    self.card(group_id, label or group_id, strategy)
                self.request("PATCH", f"/groups/{group_id}", {"name": "PATCH 名称"})
                self.card(group_id, "PATCH 名称", strategy)
                for context in ("Updated collaboration goal", ""):
                    self.request("PATCH", f"/groups/{group_id}", {"context": context})
                    assert self.card(group_id, "PATCH 名称", strategy)["description"] == context
                # Managed catalog labels: replace, read back, then clear.
                assert created["catalog_labels"] == {}
                patched = self.request("PATCH", f"/groups/{group_id}",
                                       {"catalog_labels": {"lifecycle": ["running"]}})
                assert patched["catalog_labels"] == {"lifecycle": ["running"]}
                assert self.request("GET", f"/groups/{group_id}")["catalog_labels"] == {"lifecycle": ["running"]}
                # An unknown key must be rejected as a whole; labels stay intact.
                self.request("PATCH", f"/groups/{group_id}",
                             {"catalog_labels": {"lifecycle": ["offline"], "stage": ["x"]}}, status=400)
                assert self.request("GET", f"/groups/{group_id}")["catalog_labels"] == {"lifecycle": ["running"]}
                cleared = self.request("PATCH", f"/groups/{group_id}", {"catalog_labels": {}})
                assert cleared["catalog_labels"] == {}
            self.request("DELETE", f"/groups/{group_id}?bot_id={self.bot}")
            assert not self.rows("bcs_groups", group_id), "Group deletion was not persisted"
            assert not self.rows("bcs_group_agent_cards", group_id), "Orphaned Group Card after deletion"
        self.request("DELETE", f"/bots/{self.bot}")
        print(f"Group Card lifecycle E2E: {self.checks} HTTP/SQL checks passed", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    args = parser.parse_args()
    from sqlite_story_server import sqlite_server
    with sqlite_server(args.binary) as (base_url, db_path, _):
        Story(base_url, db_path).run()


if __name__ == "__main__":
    main()
