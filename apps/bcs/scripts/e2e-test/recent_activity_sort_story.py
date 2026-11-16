#!/usr/bin/env python3
"""Live V1 recent-activity sort regression: only HTTP operations create state.

Covers the design D1/D4/D5 contract end to end: the V1 group list and session
list accept `sort=last_activity`, order by the D1 activity key (persisted
message `created_at`, then session `created_at`, falling back to the row's own
`created_at`) with the stable id tie-break, and surface `last_activity_at` on
every summary. Running it against the instrumented BCS also exercises the
`LastActivity` arms of both list services for the changed-line coverage gate.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from group_session_ws_probe import _gateway_principal_token


PREFIX = "/openapi/v1/collaboration"


def segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class Story:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.signing_key = os.environ.get(
            "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE",
            "avernet-dev-signing-key-NOT-FOR-PROD",
        )
        suffix = uuid.uuid4().hex
        self.manager = "activitymanager" + suffix
        self.bots: list[str] = []
        self.bot_tokens: dict[str, str] = {}
        self.groups: list[str] = []
        self.sessions: list[str] = []
        self.checks = 0

    def request(self, user: str, method: str, path: str, body=None, *,
                status: int = 200, public: bool = True, headers=None):
        auth = {"X-Mock-User-Id": user, "X-Mock-Nick-Name": user}
        if public:
            auth = {"X-Avernet-Principal": _gateway_principal_token(
                user_id=user, username=user, tenant="bcs-e2e",
                signing_key=self.signing_key, issuer="gateway",
            )}
        request = urllib.request.Request(
            self.base_url + (PREFIX if public else "") + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **auth, **(headers or {})},
        )
        try:
            response = self.http.open(request, timeout=20)
        except urllib.error.HTTPError as failure:
            response = failure
        with response:
            code, raw = response.code, response.read().decode()
        route = path.split("?", 1)[0]
        result = json.loads(raw) if raw else {}
        detail = result.get("message", "") if public else ""
        assert code == status, f"{method} {route}: expected {status}, got {code}: {detail} {raw[:200]}"
        data = result.get("data", {}) if public else result
        self.checks += 1
        return data

    def bot_request(self, bot: str, method: str, path: str, body=None, *, status: int = 200):
        return self.request(
            self.manager, method, path, body,
            status=status, public=False,
            headers={"X-BCS-Bot-Token": self.bot_tokens[bot]},
        )

    def register(self, name: str) -> str:
        self.request(self.manager, "POST", "/me/ensure-human", {}, public=False)
        token = self.request(self.manager, "GET", "/register/token", public=False)["token"]
        registered = self.request(
            self.manager, "POST", f"/register?token={segment(token)}&bot-name={segment(name)}",
            {}, public=False,
        )
        bot = registered["bot_uuid"]
        self.bots.append(bot)
        self.bot_tokens[bot] = registered["bot_token"]
        self.request(self.manager, "POST", "/bots/onboard", {
            "name": name, "summary": "Recent-activity sort E2E Bot",
            "domains": ["testing"], "skills": ["review"], "scopes": ["local"],
        }, public=False, headers={"X-BCS-Bot-Token": registered["bot_token"]})
        return bot

    def create_group(self, driver: str, name: str) -> str:
        group = self.request(self.manager, "POST", "/groups", {
            "group_kind": "normal", "name": name,
            "driver_bot_uuid": driver,
            "participants": [{"actor_id": driver, "role": "driver"}],
            "collaboration": {"strategy": "chat"},
        }, status=201)["group_id"]
        self.groups.append(group)
        return group

    def create_session(self, group: str, title: str) -> str:
        session = self.request(self.manager, "POST", f"/groups/{group}/sessions",
                               {"title": title}, status=201)["session_id"]
        self.sessions.append(session)
        return session

    def group_order(self, viewer: str, sort: str = "") -> tuple[list[str], dict[str, int]]:
        query = f"view_bot_id={viewer}&limit=100"
        if sort:
            query += f"&sort={sort}"
        page = self.request(self.manager, "GET", f"/groups?{query}")
        items = [item for item in page["items"] if item["group_id"] in self.groups]
        activity = {item["group_id"]: item["last_activity_at"] for item in items
                    if item.get("last_activity_at") is not None}
        self.checks += 1
        return [item["group_id"] for item in items], activity

    def session_order(self, viewer: str, group: str, sort: str = "") -> tuple[list[str], dict[str, int]]:
        query = f"view_bot_id={viewer}&limit=100"
        if sort:
            query += f"&sort={sort}"
        page = self.request(self.manager, "GET", f"/groups/{group}/sessions?{query}")
        items = [item for item in page["items"] if item["session_id"] in self.sessions]
        activity = {item["session_id"]: item["last_activity_at"] for item in items
                    if item.get("last_activity_at") is not None}
        self.checks += 1
        return [item["session_id"] for item in items], activity

    def wait_session_messages(self, viewer: str, session: str, count: int = 1) -> None:
        deadline = time.time() + 15
        while True:
            page = self.request(self.manager, "GET",
                                f"/sessions/{session}/messages?limit=20&view_bot_id={viewer}")
            messages = page if isinstance(page, list) else page.get("messages", [])
            if len(messages) >= count:
                return
            assert time.time() < deadline, f"session {session} history never reached {count} message(s)"
            time.sleep(0.5)

    def run(self) -> None:
        driver = self.register("Activity sort driver")
        # Distinct created_at keys drive both tie-break and fallback assertions;
        # created_at is epoch milliseconds, so space the writes out.
        group_a = self.create_group(driver, "Activity sort group A")
        time.sleep(1.1)
        group_b = self.create_group(driver, "Activity sort group B")
        time.sleep(1.1)

        # Default stays the historical created_at DESC order; the activity key
        # initially falls back to each group's own created_at (D1).
        order, activity = self.group_order(driver)
        assert order == [group_b, group_a], order
        order, activity = self.group_order(driver, sort="last_activity")
        assert order == [group_b, group_a], order
        assert activity[group_a] < activity[group_b], activity

        # A session write re-keys the group: group A jumps ahead of the newer
        # group B although no message was ever sent (session created_at source).
        session_a1 = self.create_session(group_a, "A / session 1")
        time.sleep(1.1)
        order, activity = self.group_order(driver, sort="last_activity")
        assert order == [group_a, group_b], order
        assert activity[group_a] > activity[group_b], activity

        # The default sort is unaffected by the activity key change.
        order, _ = self.group_order(driver)
        assert order == [group_b, group_a], order

        # Session list: default created_at order first, then a persisted
        # message re-keys the older session ahead on last_activity.
        session_a2 = self.create_session(group_a, "A / session 2")
        time.sleep(1.1)
        order, activity = self.session_order(driver, group_a)
        assert order == [session_a2, session_a1], order
        order, activity = self.session_order(driver, group_a, sort="last_activity")
        assert order == [session_a2, session_a1], order
        assert activity[session_a1] < activity[session_a2], activity

        # Posting re-keys the older session ahead. The group driver is already
        # a session participant (the V1 create auto-joins it).
        self.bot_request(driver, "POST", f"/sessions/{session_a1}/chat",
                         {"message": "activity-sort-probe"})
        self.wait_session_messages(driver, session_a1)
        order, activity = self.session_order(driver, group_a, sort="last_activity")
        assert order == [session_a1, session_a2], order
        assert activity[session_a1] > activity[session_a2], activity

        # Group level again: the message row also carries group_id, but a fresh
        # session in group B re-keys B ahead — the group D1 key merges both
        # sources with max().
        self.create_session(group_b, "B / session 1")
        time.sleep(1.1)
        order, activity = self.group_order(driver, sort="last_activity")
        assert order == [group_b, group_a], order
        assert activity[group_b] > activity[group_a], activity

        # Invalid sort values are rejected, not silently defaulted.
        self.request(self.manager, "GET", f"/groups?view_bot_id={driver}&sort=bogus",
                     status=400)
        self.request(self.manager, "GET",
                     f"/groups/{group_a}/sessions?view_bot_id={driver}&sort=bogus",
                     status=400)
        print(f"Recent-activity sort story: {self.checks} HTTP assertions passed", flush=True)

    def cleanup(self) -> None:
        failures = []
        for session in self.sessions:
            try:
                self.request(self.manager, "DELETE", f"/sessions/{session}")
            except Exception as error:
                failures.append(str(error))
        for group in self.groups:
            try:
                self.request(self.manager, "DELETE", f"/groups/{group}")
            except Exception as error:
                failures.append(str(error))
        for bot in self.bots:
            try:
                self.request(self.manager, "DELETE", f"/bots/{bot}", public=False)
            except Exception as error:
                failures.append(str(error))
        assert not failures, f"Story cleanup failed: {failures}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("BCS_API_BASE_URL", "http://127.0.0.1:21000"))
    args = parser.parse_args()
    story = Story(args.base_url)
    try:
        story.run()
    finally:
        story.cleanup()


if __name__ == "__main__":
    main()
