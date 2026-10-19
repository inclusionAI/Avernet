#!/usr/bin/env python3
"""Live V1 authorization regression: only HTTP operations create/read/write state."""

from __future__ import annotations

import argparse
import json
import os
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
        self.manager = "sessionmanager" + suffix
        self.owner = "sessionowner" + suffix
        self.outsider = "sessionoutsider" + suffix
        self.bots: list[tuple[str, str]] = []
        self.groups: list[str] = []
        self.sessions: list[str] = []
        self.checks = 0

    def request(self, user: str, method: str, path: str, body=None, *,
                status: int = 200, error: str = "", public: bool = True,
                headers=None):
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
        # Do not print token-bearing registration URLs/responses on failure.
        route = path.split("?", 1)[0]
        result = json.loads(raw) if raw else {}
        detail = result.get("message", "") if public else ""
        assert code == status, f"{method} {route}: expected {status}, got {code}: {detail}"
        data = result.get("data", {}) if public else result
        if error:
            assert data.get("error_code") == error, (route, data)
        self.checks += 1
        return data

    def register(self, user: str, name: str) -> str:
        self.request(user, "POST", "/me/ensure-human", {}, public=False)
        token = self.request(user, "GET", "/register/token", public=False)["token"]
        registered = self.request(
            user, "POST", f"/register?token={segment(token)}&bot-name={segment(name)}",
            {}, public=False,
        )
        bot = registered["bot_uuid"]
        self.bots.append((user, bot))
        self.request(user, "POST", "/bots/onboard", {
            "name": name, "summary": "Session authorization E2E Bot",
            "domains": ["testing"], "skills": ["review"], "scopes": ["local"],
        }, public=False, headers={"X-BCS-Bot-Token": registered["bot_token"]})
        return bot

    def participant(self, session: str, actor: str) -> dict:
        detail = self.request(self.manager, "GET", f"/sessions/{session}")
        return next(item for item in detail["participants"] if item["actor_id"] == actor)

    def patch(self, user: str, session: str, actor: str, body: dict, **kwargs):
        return self.request(user, "PATCH", f"/sessions/{session}/participants/{actor}", body, **kwargs)

    def run(self) -> None:
        driver = self.register(self.manager, "Session story driver")
        owned = self.register(self.owner, "Session story owned Bot")
        self.request(self.outsider, "POST", "/me/ensure-human", {}, public=False)
        # A manager can invite a public Bot, but does not own it.
        self.request(self.owner, "PUT", f"/bots/{owned}/visibility",
                     {"visibility": "public"}, public=False)
        group = self.request(self.manager, "POST", "/groups", {
            "group_kind": "normal", "name": "Session owner authorization E2E",
            "driver_bot_uuid": driver,
            "participants": [{"actor_id": driver, "role": "driver"}],
            "collaboration": {"strategy": "chat"},
        }, status=201)["group_id"]
        self.groups.append(group)
        self.request(self.manager, "PATCH", f"/groups/{group}", {"visibility": "private"})
        session = self.request(self.manager, "POST", f"/groups/{group}/sessions",
                               {"title": "Session-only participation"}, status=201)["session_id"]
        self.sessions.append(session)
        self.request(self.owner, "GET", f"/groups/{group}", status=403, error="forbidden")
        self.request(self.manager, "POST", f"/sessions/{session}/participants", {"bot_uuid": owned})
        detail = self.request(self.owner, "GET", f"/groups/{group}")
        assert all(p["actor_id"] != owned for p in detail["participants"]), detail
        self.request(self.outsider, "GET", f"/groups/{group}", status=403, error="forbidden")

        # Owners change their existing Bot, not someone else's Bot or Session.
        for mode in ("muted", "auto"):
            updated = self.patch(self.owner, session, owned, {"mode": mode})
            assert updated["mode"] == mode, updated
            assert self.participant(session, owned)["mode"] == mode
        self.patch(self.owner, session, driver, {"mode": "muted"}, status=403, error="forbidden")
        self.patch(self.owner, session, owned, {"mode": "present"},
                   status=400, error="invalid_participant_mode")
        self.patch(self.owner, session, owned, {"mode": "muted", "message_view_scope": "full"},
                   status=403, error="message_view_scope_forbidden")
        assert self.participant(session, owned)["mode"] == "auto", "Denied patch partially wrote mode"
        self.patch(self.owner, session, owned, {"message_view_scope": "participant"},
                   status=400, error="invalid_message_view_scope")
        self.request(self.owner, "PATCH", f"/sessions/{session}", {"title": "denied"},
                     status=403, error="forbidden")
        self.request(self.owner, "DELETE", f"/sessions/{session}", status=403, error="forbidden")
        self.request(self.owner, "PATCH", f"/groups/{group}", {"name": "denied"},
                     status=403, error="forbidden")
        self.request(self.owner, "DELETE", f"/groups/{group}", status=403, error="forbidden")
        self.request(self.owner, "POST", f"/sessions/{session}/participants", {"bot_uuid": driver},
                     status=403, error="forbidden")
        self.patch(self.manager, session, "missing-bot", {"mode": "auto"},
                   status=404, error="participant_not_found")
        self.patch(self.owner, session, "missing-bot", {"mode": "auto"},
                   status=403, error="forbidden")

        # Self-service Human joins via its owned Bot; strangers cannot self-add.
        human = "human_" + self.owner
        stranger = "human_" + self.outsider
        self.patch(self.outsider, session, stranger, {"mode": "present"},
                   status=403, error="forbidden")
        updated = self.patch(self.owner, session, human, {"mode": "present"})
        assert updated["mode"] == "present" and updated["message_view_scope"] == "full", updated
        self.patch(self.owner, session, human, {"mode": "muted"},
                   status=400, error="invalid_participant_mode")
        self.patch(self.manager, session, human, {"mode": "absent"},
                   status=403, error="forbidden")
        self.patch(self.outsider, session, human, {"message_view_scope": "full"},
                   status=403, error="message_view_scope_forbidden")
        for scope, mode in (("participant", "absent"), ("full", "present")):
            updated = self.patch(self.owner, session, human,
                                 {"mode": mode, "message_view_scope": scope})
            assert (updated["mode"], updated["message_view_scope"]) == (mode, scope), updated
            stored = self.participant(session, human)
            assert (stored["mode"], stored["message_view_scope"]) == (mode, scope), stored
        self.patch(self.manager, session, human, {"message_view_scope": "participant"})
        assert self.participant(session, human)["message_view_scope"] == "participant"
        self.patch(self.owner, session, human, {"message_view_scope": "full"})
        self.patch(self.manager, session, owned, {"mode": "muted", "message_view_scope": "full"})
        assert self.participant(session, owned)["mode"] == "muted"

        # Collection and history stay actor-scoped while users manage their view.
        self.request(self.owner, "GET", f"/groups/{group}/sessions?view_bot_id={owned}&limit=20")
        self.request(self.owner, "GET", f"/groups/{group}/sessions?view_bot_id={driver}&limit=20",
                     status=403, error="forbidden")
        self.request(self.owner, "GET", f"/sessions/{session}/messages?limit=20")
        self.request(self.owner, "POST", f"/sessions/{session}/collect", {"participant": owned})
        self.request(self.owner, "DELETE", f"/sessions/{session}/collect?participant={owned}")
        self.request(self.outsider, "POST", f"/sessions/{session}/collect", {"participant": owned},
                     status=403, error="forbidden")

        # Revoking the Bot leaves the Human relation; revoking both denies reads.
        self.request(self.manager, "DELETE", f"/sessions/{session}/participants/{owned}")
        self.request(self.owner, "GET", f"/groups/{group}")
        self.patch(self.owner, session, owned, {"mode": "auto"}, status=403, error="forbidden")
        self.request(self.manager, "DELETE", f"/sessions/{session}/participants/{human}")
        self.request(self.owner, "GET", f"/groups/{group}", status=403, error="forbidden")
        self.request(self.owner, "GET", f"/sessions/{session}", status=403, error="forbidden")
        self.request(self.manager, "GET", f"/groups/{group}")
        print(f"Session owner story: {self.checks} HTTP assertions passed", flush=True)

    def cleanup(self) -> None:
        failures = []
        resources = [(self.manager, f"/sessions/{sid}", True) for sid in self.sessions]
        resources += [(self.manager, f"/groups/{gid}", True) for gid in self.groups]
        resources += [(user, f"/bots/{bot}", False) for user, bot in self.bots]
        for user, path, public in resources:
            try:
                self.request(user, "DELETE", path, public=public)
            except Exception as error:
                failures.append(str(error))
        assert not failures, f"Story cleanup failed: {failures}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    story = Story(args.base_url)
    try:
        story.run()
    finally:
        story.cleanup()


if __name__ == "__main__":
    main()
