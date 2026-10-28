#!/usr/bin/env python3
"""Live HTTP regression for retired service policies and retained Session guards."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid


def segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class Story:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.owner = "policyowner" + uuid.uuid4().hex
        self.bot = ""
        self.bot_token = ""
        self.groups: list[str] = []
        self.sessions: list[str] = []
        self.checks = 0

    def request(self, method: str, path: str, body=None, *, status: int = 200,
                bot_auth: bool = True):
        headers = {"Content-Type": "application/json",
                   "X-Mock-User-Id": self.owner, "X-Mock-Nick-Name": self.owner}
        if bot_auth:
            headers["X-BCS-Bot-Token"] = self.bot_token
        request = urllib.request.Request(
            self.base_url + path, method=method, headers=headers,
            data=None if body is None else json.dumps(body).encode(),
        )
        try:
            response = self.http.open(request, timeout=20)
        except urllib.error.HTTPError as failure:
            response = failure
        with response:
            code, raw = response.code, response.read().decode()
        # Registration URLs and responses contain credentials; do not log them.
        route = path.split("?", 1)[0]
        assert code == status, f"{method} {route}: expected {status}, got {code}"
        self.checks += 1
        return json.loads(raw) if raw else {}

    def assert_policy_absent(self, group: dict) -> None:
        assert "service_spec" not in group, "Retired policy leaked into Group response"
        assert "retired_service_spec" not in group, "Compatibility shim leaked into response"
        self.checks += 1

    def run(self) -> None:
        self.request("POST", "/me/ensure-human", {}, bot_auth=False)
        token = self.request("GET", "/register/token", bot_auth=False)["token"]
        registered = self.request(
            "POST", f"/register?token={segment(token)}&bot-name=Policy-story-driver",
            {}, bot_auth=False,
        )
        self.bot, self.bot_token = registered["bot_uuid"], registered["bot_token"]
        self.request("POST", "/bots/onboard", {
            "name": "Policy story driver", "summary": "Retired policy compatibility E2E",
            "domains": ["testing"], "skills": ["review"], "scopes": ["local"],
        })
        # Both legacy encodings remain accepted, without persisting a policy.
        for policy in ({"enabled": False, "max_concurrency": 1}, None):
            created = self.request("POST", "/groups", {
                "driver_bot": self.bot, "label": "Retired policy compatibility",
                "participants": [{"bot_uuid": self.bot, "role": "driver"}],
                "service_spec": policy,
            })
            group = created["id"]
            self.groups.append(group)
            self.assert_policy_absent(created)
            self.assert_policy_absent(self.request("GET", f"/groups/{group}"))

        group = self.groups[0]
        retired = self.request("PATCH", f"/groups/{group}/settings",
                               {"service_spec": {"enabled": True}}, status=410)
        assert retired["error"] == "service_policy_retired", retired
        # Neither the old disabled flag nor max_concurrency=1 blocks invocation.
        for index in range(2):
            invoked = self.request("POST", f"/services/{group}/sessions", {
                "session_title": f"Legacy policy ignored {index}", "input": {"run": index},
            }, status=202)
            sid = invoked["session_id"]
            self.sessions.append(sid)
            assert invoked["session_kind"] == "service_invocation", invoked
            assert invoked["status"] == "running", invoked
            stored = self.request("GET", f"/services/{group}/sessions/{sid}")
            assert stored["input"] == {"run": index}, stored
        assert self.sessions[0] != self.sessions[1], "Invocations unexpectedly reused a Session"

        # Retirement does not allow reactivating a running invocation.
        sid = self.sessions[0]
        conflict = self.request("POST", f"/services/{group}/sessions",
                                {"session_id": sid, "input": {"run": "denied"}}, status=409)
        assert "running" in conflict["error"], conflict
        assert self.request("GET", f"/services/{group}/sessions/{sid}")["input"] == {"run": 0}

        # A completed chat Session cannot be converted into a service invocation.
        chat = self.request("POST", f"/groups/{group}/sessions",
                            {"session_title": "Chat lifecycle guard"}, status=201)
        sid = chat["session_id"]
        self.sessions.append(sid)
        completed = self.request("POST", f"/sessions/{sid}/complete", {"output": {"ok": True}})
        assert completed["status"] == "completed", completed
        invalid = self.request("POST", f"/services/{group}/sessions",
                               {"session_id": sid}, status=400)
        assert "not service_invocation" in invalid["error"], invalid
        stored = self.request("GET", f"/sessions/{sid}")
        assert stored["status"] == "completed" and stored["output"] == {"ok": True}, stored
        print(f"Retired service policy story: {self.checks} HTTP/response checks passed", flush=True)

    def cleanup(self) -> None:
        failures = []
        resources = [f"/sessions/{sid}?bot_id={segment(self.bot)}" for sid in self.sessions]
        resources += [f"/groups/{gid}?bot_id={segment(self.bot)}" for gid in self.groups]
        if self.bot:
            resources.append(f"/bots/{self.bot}")
        for path in resources:
            try:
                self.request("DELETE", path, bot_auth=False)
            except Exception as error:
                failures.append(str(error))
        assert not failures, f"Story cleanup failed: {failures}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    story = Story(parser.parse_args().base_url)
    try:
        story.run()
    finally:
        story.cleanup()


if __name__ == "__main__":
    main()
