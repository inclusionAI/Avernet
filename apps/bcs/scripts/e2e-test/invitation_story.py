#!/usr/bin/env python3
"""Follow shared Group/Session invitations through real, authenticated HTTP APIs.

Only the running BCS creates tokens, identities and membership. No database
fixtures, forged invitation tokens, service calls or unit-test profiles are used.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import time
import urllib.parse
import uuid

from group_session_ws_probe import _gateway_principal_token


PREFIX = "/openapi/v1/collaboration"


def segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class Story:
    def __init__(self, base_url: str) -> None:
        self.base = urllib.parse.urlsplit(base_url.rstrip("/"))
        self.signing_key = os.environ.get(
            "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE",
            "avernet-dev-signing-key-NOT-FOR-PROD",
        )
        suffix = uuid.uuid4().hex
        self.manager = "invitemanager" + suffix
        self.viewer = "inviteviewer" + suffix
        self.session_only = "invitesessiononly" + suffix
        self.bots: list[str] = []
        self.groups: list[str] = []
        self.sessions: list[str] = []
        self.checks = 0
        self.response_headers: dict[str, str] = {}

    def check(self, condition: bool, label: str) -> None:
        if not condition:
            raise AssertionError(label)
        self.checks += 1

    def request(self, label: str, user: str, method: str, path: str, body=None, *,
                status: int = 200, error: str = "", public: bool = True, headers=None):
        auth = {"X-Mock-User-Id": user, "X-Mock-Nick-Name": user}
        if public:
            auth = {"X-Avernet-Principal": _gateway_principal_token(
                user_id=user, username=user, tenant="bcs-e2e",
                signing_key=self.signing_key, issuer="gateway",
            )}
        connection_type = (http.client.HTTPSConnection if self.base.scheme == "https"
                           else http.client.HTTPConnection)
        connection = connection_type(self.base.netloc, timeout=20)
        try:
            connection.request(method, self.base.path + (PREFIX if public else "") + path,
                               body=None if body is None else json.dumps(body).encode(),
                               headers={"Content-Type": "application/json", **auth, **(headers or {})})
            response = connection.getresponse()
            raw = response.read()
            self.response_headers = dict(response.getheaders())
            # Neither paths nor bodies are printed: registration and invitation
            # requests carry credentials even when the response is an error.
            self.check(response.status == status,
                       f"{label}: HTTP {response.status}, expected {status}")
            result = json.loads(raw) if raw else {}
            if public:
                self.check(result.get("code") == status * 100, label + ": envelope code")
                result = result["data"]
            if error:
                self.check(result.get("error_code") == error, label + ": error category")
            return result
        finally:
            connection.close()

    def register(self) -> str:
        self.request("ensure inviter", self.manager, "POST", "/me/ensure-human", {}, public=False)
        token = self.request("registration token", self.manager, "GET", "/register/token", public=False)["token"]
        bot = self.request("register inviter Bot", self.manager, "POST",
                           "/register?" + urllib.parse.urlencode({"token": token, "bot-name": self.manager}),
                           {}, public=False)
        self.bots.append(bot["bot_uuid"])
        self.request("onboard inviter Bot", self.manager, "POST", "/bots/onboard", {
            "name": self.manager, "summary": "Invitation sharing E2E",
            "domains": ["testing"], "skills": ["review"], "scopes": ["local"],
        }, public=False, headers={"X-BCS-Bot-Token": bot["bot_token"]})
        return bot["bot_uuid"]

    def group(self, driver: str) -> str:
        group = self.request("create shared Group", self.manager, "POST", "/groups", {
            "group_kind": "normal", "name": "Invitation E2E", "originator": driver,
            "driver_bot_uuid": driver, "participants": [{"actor_id": driver, "role": "driver"}],
            "collaboration": {"strategy": "chat"},
        }, status=201)["group_id"]
        self.groups.append(group)
        self.request("keep shared Group private", self.manager, "PATCH", f"/groups/{group}",
                     {"visibility": "private"})
        return group

    def invite(self, kind: str, target: str, *, user: str = "", ttl: int | None = None) -> str:
        result = self.request("share " + kind, user or self.manager, "POST",
                              f"/{kind}s/{segment(target)}/invitations",
                              {} if ttl is None else {"expires_in_seconds": ttl}, status=201)
        self.check(result["target_type"] == kind and result["target_id"] == target,
                   "share token binds the requested target")
        return result["token"]

    def preview(self, user: str, token: str, kind: str, target: str, group: str, member: bool) -> None:
        result = self.request("preview " + kind, user, "GET", "/invitations/" + segment(token))
        self.check(result == {"target_type": kind, "target_id": target,
                              "group_id": group, "already_joined": member},
                   "preview contains exact target metadata and direct Human membership only")
        cache = {name.lower(): value for name, value in self.response_headers.items()}
        self.check(cache.get("cache-control") == "private, no-store", "preview is private and non-cacheable")

    def accept(self, user: str, token: str, kind: str, target: str, *, joined: bool, scope: str = "") -> None:
        result = self.request("explicitly accept " + kind, user, "POST",
                              "/invitations/" + segment(token) + "/accept",
                              {"message_view_scope": scope} if scope else {})
        self.check(result == {"target_type": kind, "target_id": target, "joined": joined,
                              "already_joined": not joined}, "accept reports idempotent target membership")

    def participants(self, kind: str, target: str) -> list:
        detail = self.request("read " + kind + " participants", self.manager, "GET",
                              f"/{kind}s/{segment(target)}")
        return detail["participants"]

    def run(self) -> None:
        driver = self.register()
        group = self.group(driver)
        session = self.request("create shared Session", self.manager, "POST",
                               f"/groups/{group}/sessions", {"title": "Invitation E2E"}, status=201)["session_id"]
        self.sessions.append(session)
        group_token = self.invite("group", group)
        session_token = self.invite("session", session)

        # Bot control grants detail access but is not direct Human membership.
        self.preview(self.manager, group_token, "group", group, group, False)
        group_before = self.participants("group", group)
        session_before = self.participants("session", session)
        self.request("nonmember cannot read private Group", self.viewer, "GET", f"/groups/{group}",
                     status=403, error="forbidden")
        self.request("nonmember cannot read private Session", self.viewer, "GET", f"/sessions/{session}",
                     status=403, error="forbidden")
        for _ in range(2):
            self.preview(self.viewer, group_token, "group", group, group, False)
            self.preview(self.viewer, session_token, "session", session, group, False)
        self.check(self.participants("group", group) == group_before, "Group preflight does not join")
        self.check(self.participants("session", session) == session_before, "Session preflight does not join")
        self.request("viewer cannot create Group invitations", self.viewer, "POST",
                     f"/groups/{group}/invitations", {}, status=403, error="forbidden")
        self.request("viewer cannot create Session invitations", self.viewer, "POST",
                     f"/sessions/{session}/invitations", {}, status=403, error="forbidden")

        self.accept(self.viewer, group_token, "group", group, joined=True, scope="participant")
        self.preview(self.viewer, group_token, "group", group, group, True)
        self.preview(self.viewer, session_token, "session", session, group, False)
        self.accept(self.viewer, group_token, "group", group, joined=False, scope="full")
        self.accept(self.viewer, session_token, "session", session, joined=True)
        human = "human_" + self.viewer
        self.request("choose Human posture and message scope", self.viewer, "PATCH",
                     f"/sessions/{session}/participants/{human}",
                     {"mode": "absent", "message_view_scope": "participant"})
        before = self.participants("session", session)
        self.preview(self.viewer, session_token, "session", session, group, True)
        self.accept(self.viewer, session_token, "session", session, joined=False, scope="full")
        after = self.participants("session", session)
        self.check(before == after, "member preview/retry preserves participant modes and scope")
        member = next(p for p in after if p["actor_id"] == human)
        self.check(member["mode"] == "absent" and member["message_view_scope"] == "participant",
                   "absence is still membership and preview does not reset message scope")
        self.invite("session", session, user=self.viewer)

        # A Session-only participant must not be auto-joined to its parent Group.
        self.accept(self.session_only, session_token, "session", session, joined=True, scope="participant")
        self.preview(self.session_only, session_token, "session", session, group, True)
        self.preview(self.session_only, group_token, "group", group, group, False)
        self.request("Session-only Human can read the parent", self.session_only, "GET", f"/groups/{group}")
        self.check(all(p["actor_id"] != "human_" + self.session_only for p in self.participants("group", group)),
                   "readable parent is not direct Group membership")

        # Removing membership must be visible on the next preflight request.
        self.request("remove Session member", self.manager, "DELETE", f"/sessions/{session}/participants/{human}")
        self.preview(self.viewer, session_token, "session", session, group, False)
        self.preview(self.viewer, group_token, "group", group, group, True)
        self.request("remove Group member", self.manager, "DELETE", f"/groups/{group}/participants/{human}")
        self.preview(self.viewer, group_token, "group", group, group, False)

        for method, suffix in (("GET", ""), ("POST", "/accept")):
            self.request("invalid invitation", self.viewer, method, "/invitations/invalid" + suffix,
                         {} if method == "POST" else None, status=400, error="invalid_request")
        self.request("missing Group cannot be shared", self.manager, "POST",
                     "/groups/missing-invitation-group/invitations", {}, status=404, error="group_not_found")
        self.request("missing Session cannot be shared", self.manager, "POST",
                     "/sessions/missing-invitation-session/invitations", {}, status=404, error="session_not_found")
        self.accept(self.viewer, group_token, "group", group, joined=True, scope="participant")
        self.preview(self.viewer, group_token, "group", group, group, True)
        expired = self.invite("group", group, ttl=1)
        time.sleep(2.1)  # The real server-issued token must expire; no signing-secret access.
        for method, suffix in (("GET", ""), ("POST", "/accept")):
            self.request("expired member invitation", self.viewer, method, "/invitations/" + segment(expired) + suffix,
                         {} if method == "POST" else None, status=410, error="invitation_expired")

        self.request("delete shared Session", self.manager, "DELETE", f"/sessions/{session}")
        self.sessions.remove(session)
        self.request("deleted Session preview", self.viewer, "GET", "/invitations/" + segment(session_token),
                     status=404, error="invitation_not_found")
        self.request("delete shared Group", self.manager, "DELETE", f"/groups/{group}")
        self.groups.remove(group)
        self.request("deleted Group preview", self.viewer, "GET", "/invitations/" + segment(group_token),
                     status=404, error="invitation_not_found")
        self.request("deleted Group accept", self.viewer, "POST", "/invitations/" + segment(group_token) + "/accept",
                     {}, status=404, error="invitation_not_found")

    def cleanup(self) -> None:
        failures = []
        resources = [(True, f"/sessions/{sid}") for sid in self.sessions]
        resources += [(True, f"/groups/{gid}") for gid in self.groups]
        resources += [(False, f"/bots/{bot}") for bot in self.bots]
        for public, path in resources:
            try:
                self.request("delete invitation fixture", self.manager, "DELETE", path, public=public)
            except Exception as error:
                failures.append(str(error))
        self.check(not failures, "invitation cleanup: " + "; ".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    story = Story(args.base_url)
    try:
        story.run()
    finally:
        story.cleanup()
    print(f"Invitation story: {story.checks} live HTTP assertions passed (including cleanup)", flush=True)


if __name__ == "__main__":
    main()
