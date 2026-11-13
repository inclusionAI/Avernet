#!/usr/bin/env python3
"""Live-stack coverage for the internal Session management handlers' edge paths.

Every request below targets the INTERNAL (non-`/openapi`) Session routes, which
are the moved handlers under
`crates/adapters/http/bcs-http/src/routes/sessions/`. The public
`/openapi/v1/collaboration` stories exercise a different handler set, so the
internal rejection ladders stay uncovered without this journey.

Scenario -> handler branch map (line ranges are the E2E-uncovered diff lines):

creation.rs
  unknown parent group (404), non-participant caller (403), reactivate unknown
  session (404), reactivate running session (409), ambiguous credentials (401),
  public creator role matrix (400 invalid_role / 201), cross-group reactivate
  (404) .................................................... 62-93, 102-121, 140-144, 164-171

participants.rs
  add: 401, role wire values (driver/observer/consultant/default), rejected
  roles (manager/worker -> 400), unknown Bot (404), Bot projection (400) and
  accepted full projection ......................... 80-113, 121-142
  remove: 401, non-owner 403, driver guard 403, non-participant store error
  400 ...................................................... 193-197, 298-322
  patch: 401, empty body 400, unknown mode 400, mode ladder (auto/muted),
  Bot-supplied projection 400, cross-Human projection application rejection,
  self scope self-enrolment, Human first-insert through the mode path, missing
  session/Bot errors ........... 397-411, 416-464, 469-500, 539-567, 592-661, 664

lifecycle.rs
  patch: empty-body read (200) / missing session (404) ........ 42-49
  complete: 401, missing session 404, non-driver 403, driver completion,
  already-completed short-circuit ........................ 87-96, 134-149
  delete: missing 404, unauthorized Bot 403, unauthorized Human 403, Human
  controller of the creator Bot 200 ............................. 209-240

history.rs
  missing session 404, non-participant Human read (currently allowed),
  Human participant projection merge, unauthorized/authorized `view_bot_id`
  (403/200), durable limit=0 -> default page limit (200), Human participant
  projection selection ................ 53-60, 80-89, 149-168, 199-231

chat.rs
  401, non-participant Bot 403, Human self-enrolment and ingress ..... 52-108

collections.rs / queries.rs
  collect/uncollect 401, Human caller without participant 400, self collect
  200, missing session 404; collected=true authorization 403, per-participant
  collected projection, temporary-participant visibility filter, missing
  session read 404 ................... 41-45, 52-60, 106, 129; 77-90, 208-218, 248-254

Structurally unreachable from a live stack (kept out on purpose):
  participants.rs 502-534 (the pre-application fallback only runs when
  `session_application` is absent; the bootstrap always injects it),
  history.rs 263-275/294-303 (Public callers are rejected before the resolver;
  local auth always yields a Human), lifecycle.rs 118-125 and history.rs 62-71
  (deleting a Group cascades its Sessions, so an orphan Session cannot exist).

Measured with `apps/bcs/scripts/changed_line_cov.py::measure()` (changed set =
`git diff -U0 99ca65a9 -- apps/bcs`; denominator = the CI run's instrumentable
changed lines): one isolated run of this story against the instrumented `bcs`
executes 1070/1388 = 77.09% of the changed lines, i.e. 396 lines the pre-story
CI run did not execute (participants +148, history +61, lifecycle +53,
creation +50, chat +34, collections +26, queries +24). Unioned with the CI
baseline the same measure gives 1125/1388 = 81.05%. Re-measure after any edit;
the story's own run alone already clears the >65% gate.
"""

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
        suffix = uuid.uuid4().hex[:10]
        self.manager = "sessionedge" + suffix
        self.outsider = "sessionedgeout" + suffix
        self.viewer = "sessionedgeview" + suffix
        self.bots: list[tuple[str, str]] = []
        self.groups: list[tuple[str, str]] = []
        self.sessions: list[tuple[str, str]] = []
        self.public_bots: list[tuple[str, str]] = []
        self.checks = 0

    # -- HTTP ---------------------------------------------------------
    def request(self, user, method, path, body=None, *, status=200, bot=None,
                conflicting=False, error=None, error_prefix=None, note=""):
        headers = {"Content-Type": "application/json",
                   "X-Mock-User-Id": user, "X-Mock-Nick-Name": user}
        if bot:
            headers["X-BCS-Bot-Token"] = bot
        if conflicting:
            # Two different Bot credentials are ambiguous; the auth chain
            # rejects them before any Human fallback, which is the only way to
            # reach the 401 arms while local mock auth is configured.
            headers["X-BCS-Bot-Token"] = "test-token-conflict-a"
            headers["X-Avernet-Bot-Token"] = "test-token-conflict-b"
        request = urllib.request.Request(
            self.base_url + path, method=method, headers=headers,
            data=None if body is None else json.dumps(body).encode(),
        )
        try:
            response = self.http.open(request, timeout=30)
        except urllib.error.HTTPError as failure:
            response = failure
        with response:
            code, raw = response.code, response.read().decode()
        # Registration responses carry credentials; never print tokens.
        route = path.split("?", 1)[0]
        payload = json.loads(raw) if raw else {}
        assert code == status, (
            f"{note or method} {route}: expected {status}, got {code}: "
            f"{json.dumps(payload)[:300]}"
        )
        if error is not None:
            assert payload.get("error") == error, (note or route, payload)
        if error_prefix is not None:
            assert str(payload.get("error", "")).startswith(error_prefix), (
                note or route, payload)
        self.checks += 1
        return payload

    def best_effort(self, user, method, path, body=None) -> int:
        """Cleanup request: returns the status code (0 on transport error)."""
        headers = {"Content-Type": "application/json",
                   "X-Mock-User-Id": user, "X-Mock-Nick-Name": user}
        request = urllib.request.Request(
            self.base_url + path, method=method, headers=headers,
            data=None if body is None else json.dumps(body).encode())
        try:
            with self.http.open(request, timeout=30) as response:
                response.read()
                return response.code
        except urllib.error.HTTPError as failure:
            return failure.code
        except urllib.error.URLError:
            return 0

    # -- Setup helpers ------------------------------------------------
    def ensure_human(self, user: str) -> None:
        self.request(user, "POST", "/me/ensure-human", {})

    def register(self, user: str, name: str) -> tuple[str, str]:
        self.ensure_human(user)
        token = self.request(user, "GET", "/register/token")["token"]
        registered = self.request(
            user, "POST",
            "/register?" + urllib.parse.urlencode({"token": token, "bot-name": name}),
            {},
        )
        bot, bot_token = registered["bot_uuid"], registered["bot_token"]
        self.request(user, "POST", "/bots/onboard", {
            "name": name, "summary": "Session management edge E2E Bot",
            "domains": ["testing"], "skills": ["review"], "scopes": ["local"],
        }, bot=bot_token)
        self.bots.append((user, bot))
        return bot, bot_token

    def create_group(self, user: str, driver: str, label: str) -> str:
        created = self.request(user, "POST", "/groups", {
            "driver_bot": driver,
            "participants": [{"bot_uuid": driver, "role": "driver"}],
            "label": label,
        })
        group = created["id"]
        self.groups.append((group, driver))
        return group

    def set_visibility(self, user: str, path: str, visibility: str) -> None:
        self.request(user, "PUT", path, {"visibility": visibility})
        if visibility == "public" and "/bots/" in path:
            bot = path.split("/bots/", 1)[1].split("/", 1)[0]
            self.public_bots.append((user, bot))

    def create_session(self, user: str, group: str, title: str) -> str:
        created = self.request(
            user, "POST", f"/groups/{group}/sessions", {"session_title": title},
            status=201)
        session = created["session_id"]
        self.track(session, "human_" + user)
        return session

    def track(self, session: str, delete_actor: str) -> None:
        self.sessions.append((session, delete_actor))

    def participant(self, session: str, actor: str, user: str | None = None) -> dict:
        detail = self.request(user or self.manager, "GET", f"/sessions/{session}")
        return next(
            item for item in detail["participants"]
            if item.get("bot_uuid") == actor or item.get("actor_id") == actor
        )

    def human(self, user: str) -> str:
        return "human_" + user

    # -- Journey ------------------------------------------------------
    def run(self) -> None:
        for user in (self.manager, self.outsider, self.viewer):
            self.ensure_human(user)
        driver, driver_token = self.register(self.manager, "Session edge driver")
        consultant, consultant_token = self.register(
            self.manager, "Session edge consultant")
        outsider_bot, outsider_token = self.register(
            self.outsider, "Session edge outsider Bot")
        # A public Bot lets the session-level member routes add it directly and
        # lets the public group in the creation matrix become visible.
        self.set_visibility(self.manager, f"/bots/{consultant}/visibility", "public")
        self.set_visibility(self.manager, f"/bots/{driver}/visibility", "public")

        group = self.create_group(self.manager, driver, "Session edge chat group")
        self.set_visibility(self.manager, f"/groups/{group}/visibility", "private")
        public_group = self.create_group(
            self.manager, driver, "Session edge public group")
        self.set_visibility(
            self.manager, f"/groups/{public_group}/visibility", "public")

        session = self.create_session(self.manager, group, "Session edge running")
        lifecycle = self.create_session(
            self.manager, group, "Session edge lifecycle")
        driver_session = self.request(
            driver, "POST", f"/groups/{group}/sessions",
            {"session_title": "Session edge driver-owned"}, bot=driver_token,
            status=201,
        )["session_id"]
        self.track(driver_session, driver)

        self.creation_edges(session, group, public_group)
        self.participant_edges(session, driver, driver_token, consultant,
                               outsider_bot)
        self.lifecycle_edges(session, lifecycle, group)
        self.history_edges(session, driver, driver_token, consultant, outsider_bot)
        self.chat_edges(session, driver, driver_token, outsider_bot,
                        outsider_token)
        self.collection_edges(session, group, consultant, consultant_token,
                              driver, driver_token, outsider_bot)
        self.delete_edges(session, driver_session, outsider_bot)

        print(f"Session management edges story: {self.checks} HTTP checks passed",
              flush=True)

    # -- creation.rs --------------------------------------------------
    def creation_edges(self, session: str, group: str, public_group: str) -> None:
        self.request(
            self.manager, "POST", "/groups/missing-group/sessions",
            {"session_title": "unknown group"}, status=404, error="not_found",
            note="create session rejects an unknown group")
        self.request(
            self.outsider, "POST", f"/groups/{group}/sessions",
            {"session_title": "no access"}, status=403, error="forbidden",
            note="create session rejects a non-participant Human")
        self.request(
            self.manager, "POST", f"/groups/{group}/sessions",
            {"session_id": "missing-session"}, status=404,
            error="missing-session",
            note="create session rejects reactivating an unknown session")
        self.request(
            self.manager, "POST", f"/groups/{group}/sessions",
            {"session_id": session}, status=409, error_prefix="session ",
            note="create session rejects reactivating a running session")
        self.request(
            self.manager, "POST", f"/groups/{group}/sessions",
            {"session_title": "ambiguous credentials"}, status=401,
            conflicting=True, error="unauthorized",
            note="create session rejects ambiguous Bot credentials")
        # Public groups validate the requested creator role for non-members.
        for role in ("driver", "chief"):
            self.request(
                self.outsider, "POST", f"/groups/{public_group}/sessions",
                {"caller_role": role}, status=400, error="invalid_role",
                note=f"public session rejects caller_role={role}")
        for role in ("manager", "worker", "consultant", "observer"):
            created = self.request(
                self.outsider, "POST", f"/groups/{public_group}/sessions",
                {"caller_role": role, "session_title": f"public role {role}"},
                status=201, note=f"public session accepts caller_role={role}")
            self.track(created["session_id"], self.human(self.outsider))
        self.request(
            self.manager, "POST", f"/groups/{public_group}/sessions",
            {"session_id": session}, status=404, error=session,
            note="reactivate rejects a session owned by another group")
        # `session_kind` translation covers both the explicit service kind and
        # the legacy "everything else is chat" fallback.
        for kind in ("service_invocation", "chat", "legacy"):
            created = self.request(
                self.manager, "POST", f"/groups/{group}/sessions",
                {"session_kind": kind, "session_title": f"kind {kind}"},
                status=201, note=f"create session accepts session_kind={kind}")
            expected = "service_invocation" if kind == "service_invocation" else "chat"
            assert created["session_kind"] == expected, created
            self.track(created["session_id"], self.human(self.manager))

    # -- participants.rs ----------------------------------------------
    def participant_edges(self, session, driver, driver_token, consultant,
                          outsider_bot) -> None:
        members = f"/sessions/{session}/members"
        self.request(
            self.manager, "POST", members, {"bot_uuid": consultant},
            status=401, conflicting=True, error="unauthorized",
            note="add member rejects ambiguous Bot credentials")
        for role, expected_role in (("driver", "driver"), ("observer", "observer"),
                                    ("consultant", "consultant"),
                                    ("unlisted", "consultant")):
            self.request(
                self.manager, "POST", members,
                {"bot_uuid": consultant, "role": role},
                note=f"add member accepts role wire value {role}")
            stored = self.participant(session, consultant)["role"]
            assert stored == expected_role, (role, stored)
            self.request(
                self.manager, "DELETE", f"{members}/{consultant}",
                note=f"remove member clears the {role} ladder entry")
        for role in ("manager", "worker"):
            self.request(
                self.manager, "POST", members,
                {"bot_uuid": consultant, "role": role},
                status=400, error="invalid_params",
                note=f"add member rejects role {role} in a Chat group")
        self.request(
            self.manager, "POST", members, {"bot_uuid": "missing-bot"},
            status=404, error="not_found",
            note="add member rejects an unknown Bot")
        self.request(
            self.manager, "POST", members,
            {"bot_uuid": consultant, "message_view_scope": "participant"},
            status=400, error="invalid_message_view_scope",
            note="add member rejects a Bot participant projection")
        projection = self.request(
            self.manager, "POST", members,
            {"bot_uuid": consultant, "message_view_scope": "full"},
            note="add member accepts the full Bot projection")
        added = self.participant(session, consultant)
        assert added["role"] == "consultant" and added["message_view_scope"] == "full", added
        assert projection["participants"], projection

        self.request(
            self.manager, "DELETE", f"{members}/{consultant}",
            status=401, conflicting=True, error="unauthorized",
            note="remove member rejects ambiguous Bot credentials")
        self.request(
            self.outsider, "DELETE", f"{members}/{consultant}",
            status=403,
            error="Caller is not authorized to remove this participant",
            note="remove member rejects a non-owner Human")
        self.request(
            self.manager, "DELETE", f"{members}/{driver}",
            status=403, error="cannot remove driver bot",
            note="remove member protects the group driver")
        self.request(
            driver, "DELETE", f"{members}/{outsider_bot}", status=400,
            bot=driver_token,
            error=f"participant {outsider_bot} not in session {session}",
            note="remove member reports a non-participant store error")

        patch = f"{members}/{consultant}"
        self.request(
            self.manager, "PATCH", patch, {"mode": "muted"},
            status=401, conflicting=True, error="unauthorized",
            note="patch member rejects ambiguous Bot credentials")
        self.request(
            self.manager, "PATCH", patch, {}, status=400,
            error="At least one of mode or message_view_scope must be provided",
            note="patch member requires a mutable field")
        self.request(
            self.manager, "PATCH", patch, {"mode": "vacation"},
            status=400, error="unknown mode: vacation",
            note="patch member rejects an unknown mode")
        for mode in ("auto", "muted"):
            updated = self.request(
                self.manager, "PATCH", patch, {"mode": mode},
                note=f"patch member accepts mode {mode}")
            assert self.participant(session, consultant)["mode"] == mode, updated
        self.request(
            driver, "PATCH", f"{members}/{driver}",
            {"message_view_scope": "full"}, status=400, bot=driver_token,
            error="invalid_message_view_scope",
            note="patch member rejects a Bot-supplied projection")
        # Another Human's projection is refused by the application layer.
        self.request(
            self.manager, "PATCH", f"{members}/{self.human(self.viewer)}",
            {"message_view_scope": "participant"}, status=403,
            error="forbidden",
            note="patch member rejects another Human's projection")
        # The Human updates its own projection before enrolment: the
        # application reports participant_not_found and the route self-adds
        # the Human as an Observer with the requested scope.
        enrolled = self.request(
            self.manager, "PATCH", f"{members}/{self.human(self.manager)}",
            {"message_view_scope": "participant"},
            note="patch member self-enrols the Human projection")
        assert enrolled.get("participants"), enrolled
        manager_view = self.participant(session, self.human(self.manager))
        assert manager_view["message_view_scope"] == "participant", manager_view
        # A Human first-insert through the mode path: not a participant yet,
        # so the route adds the Observer and then applies the mode.
        inserted = self.request(
            self.manager, "PATCH", f"{members}/{self.human(self.viewer)}",
            {"mode": "present"},
            note="patch member first-inserts a Human participant")
        viewer = self.participant(session, self.human(self.viewer))
        assert viewer["mode"] == "present", (inserted, viewer)
        self.request(
            self.manager, "PATCH", f"{members}/{self.human(self.viewer)}",
            {"mode": "absent"},
            note="patch member updates an existing Human mode")
        assert self.participant(session, self.human(self.viewer))["mode"] == "absent"
        self.request(
            self.manager, "PATCH",
            f"/sessions/missing-session/members/{self.human(self.viewer)}",
            {"mode": "present"}, status=404, error="missing-session",
            note="patch member reports a missing session")
        self.request(
            self.manager, "PATCH", f"{members}/missing-bot", {"mode": "muted"},
            status=404,
            error=f"participant missing-bot not in session {session}",
            note="patch member reports a missing non-Human participant")

    # -- lifecycle.rs -------------------------------------------------
    def lifecycle_edges(self, session, lifecycle, group) -> None:
        self.request(
            self.manager, "PATCH", "/sessions/missing-session", {},
            status=404, error="session not found",
            note="patch session reports a missing session")
        self.request(
            self.manager, "PATCH", f"/sessions/{lifecycle}", {},
            note="patch session without fields returns the session")
        self.request(
            self.manager, "POST", f"/sessions/{lifecycle}/complete", {},
            status=401, conflicting=True, error="unauthorized",
            note="complete session rejects ambiguous Bot credentials")
        self.request(
            self.manager, "POST", "/sessions/missing-session/complete", {},
            status=404, error="not_found",
            note="complete session reports a missing session")
        self.request(
            self.outsider, "POST", f"/sessions/{lifecycle}/complete", {},
            status=403, error="forbidden",
            note="complete session rejects a non-driver Human")
        completed = self.request(
            self.manager, "POST", f"/sessions/{lifecycle}/complete",
            {"output": {"approved": True}},
            note="complete session accepts the driver's Human controller")
        assert completed["status"] == "completed", completed
        repeated = self.request(
            self.manager, "POST", f"/sessions/{lifecycle}/complete", {},
            note="complete session short-circuits an already-completed session")
        assert repeated.get("already_completed") is True, repeated
        # Reactivating a completed chat session is rejected as InvalidRequest
        # ("session is not service_invocation"); this drives the route's
        # reactivate branch and pins the launch error text.
        self.request(
            self.manager, "POST", f"/groups/{group}/sessions",
            {"session_id": lifecycle}, status=400,
            error=f"{lifecycle}: session is not service_invocation",
            note="reactivate rejects a completed chat session")

    # -- history.rs ---------------------------------------------------
    def history_edges(self, session, driver, driver_token, consultant,
                      outsider_bot) -> None:
        self.request(
            self.manager, "GET", "/sessions/missing-session/messages?limit=20",
            status=404, error="session not found",
            note="session history reports a missing session")
        # WARNING: current chat-session history performs no session-membership
        # check on this path, so a non-participant Human gets 200. This pins
        # today's behavior; it is NOT a reviewed authorization decision (the
        # legacy group_history fallback answers 403 for the same caller).
        self.request(
            self.outsider, "GET", f"/sessions/{session}/messages?limit=20",
            note="chat session history is readable by a non-participant Human")
        self.request(
            self.manager, "GET", f"/sessions/{session}/messages?limit=20",
            note="session history projects the Human participant view")
        self.request(
            self.manager, "GET",
            f"/sessions/{session}/messages?limit=20&view_bot_id={outsider_bot}",
            status=403, error="forbidden",
            note="session history rejects an unauthorized view actor")
        self.request(
            self.manager, "GET",
            f"/sessions/{session}/messages?limit=20&view_bot_id={consultant}",
            note="session history accepts a controlled Bot view actor")
        # The durable history path (bcs-message `effective_limit`) maps limit=0
        # to the configured default page limit and answers 200. Only the legacy
        # group_history fallback rejects 0 with InvalidHistoryLimit, and the
        # participant state-machine snapshot has its own 0 check in a different
        # component. Use a Bot caller (no participant projection) and pin the
        # durable behavior so a future config flip is deliberate.
        zero_limit = self.request(
            driver, "GET", f"/sessions/{session}/messages?limit=0",
            bot=driver_token,
            note="session history maps limit=0 to the default page limit")
        assert isinstance(zero_limit, list), zero_limit
        # Enrol the viewer with its own projection, then read through it.
        self.request(
            self.manager, "PATCH",
            f"/sessions/{session}/members/{self.human(self.viewer)}",
            {"message_view_scope": "participant"},
            note="patch member updates an enrolled Human projection")
        assert self.participant(
            session, self.human(self.viewer))["message_view_scope"] == "participant"
        self.request(
            self.viewer, "GET", f"/sessions/{session}/messages?limit=20",
            note="session history honours the Human participant projection")

    # -- chat.rs ------------------------------------------------------
    # Ordering guard: chat_edges assumes participants_edges first-inserted the
    # Viewer as a Human participant and history_edges gave it a Participant
    # projection; it removes the Viewer and re-enrols it through chat.
    def chat_edges(self, session, driver, driver_token, outsider_bot,
                   outsider_token) -> None:
        self.request(
            self.manager, "POST", f"/sessions/{session}/chat",
            {"message": "ambiguous"}, status=401, conflicting=True,
            error="unauthorized",
            note="session chat rejects ambiguous Bot credentials")
        self.request(
            outsider_bot, "POST", f"/sessions/{session}/chat",
            {"message": "not a participant"}, status=403, bot=outsider_token,
            error="caller is not a session participant",
            note="session chat rejects a non-participant Bot")
        # The Viewer is a Human participant at this point; removing it first
        # makes the next chat a self-enrolment.
        self.request(
            self.manager, "DELETE",
            f"/sessions/{session}/members/{self.human(self.viewer)}",
            note="remove member removes the Viewer before self-enrolment")
        removed_list = self.request(
            self.viewer, "GET",
            f"/groups/{self.group_of(session)}/sessions?limit=20",
            note="a removed temporary participant sees no group sessions")
        assert removed_list["items"] == [], removed_list
        self.request(
            self.viewer, "POST", f"/sessions/{session}/chat",
            {"message": "hello from the viewer"},
            note="session chat self-enrols a Human caller")
        viewer_participant = self.participant(session, self.human(self.viewer))
        assert viewer_participant["actor_kind"] == "human", viewer_participant
        manager_chat = self.request(
            self.manager, "POST", f"/sessions/{session}/chat",
            {"message": "hello from the manager"},
            note="session chat accepts a participant")
        assert manager_chat["session_id"] == session, manager_chat
        assert {"delivered", "delivery_results", "group_id"} <= set(manager_chat), manager_chat
        self.request(
            driver, "GET", f"/sessions/{session}/messages?limit=5",
            bot=driver_token,
            note="session history accepts a Bot caller")

    # -- collections.rs / queries.rs ----------------------------------
    # Ordering guard: the Viewer is a self-enrolled session participant again
    # (chat_edges); the list below therefore has to contain this session.
    def collection_edges(self, session, group, consultant, consultant_token, driver,
                         driver_token, outsider_bot) -> None:
        self.request(
            self.manager, "POST", f"/sessions/{session}/collect", {},
            status=401, conflicting=True, error="unauthorized",
            note="collect rejects ambiguous Bot credentials")
        self.request(
            self.manager, "POST", f"/sessions/{session}/collect", {},
            status=400, error="invalid_params",
            note="collect requires a participant for Human callers")
        self.request(
            self.manager, "POST", f"/sessions/{session}/collect",
            {"participant": self.human(self.manager)},
            note="a Human collects through its own actor")
        self.request(
            self.manager, "POST", "/sessions/missing-session/collect",
            {"participant": self.human(self.manager)}, status=404,
            error="missing-session",
            note="collect reports a missing session")
        self.request(
            self.manager, "DELETE", f"/sessions/{session}/collect",
            status=401, conflicting=True, error="unauthorized",
            note="uncollect rejects ambiguous Bot credentials")
        self.request(
            self.manager, "DELETE", f"/sessions/{session}/collect", status=400,
            error="invalid_params",
            note="uncollect requires a participant for Human callers")
        self.request(
            self.manager, "DELETE",
            f"/sessions/{session}/collect?participant={segment(self.human(self.manager))}",
            note="a Human uncollects through its own actor")
        self.request(
            self.manager, "DELETE",
            f"/sessions/missing-session/collect?participant={segment(self.human(self.manager))}",
            status=404, error="missing-session",
            note="uncollect reports a missing session")
        empty_collected = self.request(
            self.manager, "GET",
            f"/groups/{group}/sessions?participant={consultant}&collected=true",
            note="collected list accepts a controlled Bot participant")
        assert empty_collected["items"] == [], empty_collected
        not_collected_yet = self.request(
            self.manager, "GET",
            f"/groups/{group}/sessions?participant={consultant}",
            note="per-participant projection reports an uncollected session")
        uncollected = next(
            item for item in not_collected_yet["items"]
            if item.get("session_id") == session)
        assert uncollected["collected"] is False, uncollected
        assert "collected_at" not in uncollected, uncollected
        self.request(
            self.manager, "GET",
            f"/groups/{group}/sessions?participant={outsider_bot}&collected=true",
            status=403, error="forbidden",
            note="collected list rejects an unowned Bot participant")
        # A Bot caller may only read its own collection set; asking for another
        # Bot's set fails the explicit "authorized Bot != requested Bot" guard.
        self.request(
            driver, "GET",
            f"/groups/{group}/sessions?participant={consultant}&collected=true",
            bot=driver_token, status=403, error="forbidden",
            note="collected list rejects a Bot asking for another Bot's set")
        # A collected session carries collected=true/collected_at on the
        # per-participant projection.
        self.request(
            consultant, "POST", f"/sessions/{session}/collect", {},
            bot=consultant_token,
            note="a participant Bot collects the session")
        collected_list = self.request(
            self.manager, "GET",
            f"/groups/{group}/sessions?participant={consultant}&collected=true",
            note="the collected list contains the collected session")
        assert any(
            item.get("session_id") == session
            for item in collected_list.get("items", [])
        ), collected_list
        listed = self.request(
            self.manager, "GET",
            f"/groups/{group}/sessions?participant={consultant}",
            note="session list projects per-participant collected state")
        assert all("collected" in item for item in listed["items"]), listed
        projected = next(
            item for item in listed["items"] if item.get("session_id") == session)
        assert projected["collected"] is True and "collected_at" in projected, projected
        self.request(
            self.manager, "GET", f"/groups/{group}/sessions?collected=true",
            status=400, error="invalid_params",
            note="session list requires a participant for the collected filter")
        joined = self.request(
            self.viewer, "GET", f"/groups/{group}/sessions?limit=20",
            note="a temporary participant sees the session it joined")
        joined_ids = {
            item.get("session_id") or item.get("id")
            for item in joined["items"]
        }
        assert session in joined_ids, joined
        self.request(
            self.manager, "GET", "/sessions/missing-session", status=404,
            error="session not found",
            note="session read reports a missing session")

    # -- delete_session ----------------------------------------------
    # NOTE: `delete_session` authorizes from the `?bot_id=` query parameter
    # only; the caller's authenticated identity/headers are not consulted. That
    # is why these scenarios vary the query parameter, not the mock user.
    def delete_edges(self, session, driver_session, outsider_bot) -> None:
        self.request(
            self.manager, "DELETE", "/sessions/missing-session?bot_id=missing-bot",
            status=404, error="not_found",
            note="delete session reports a missing session")
        self.request(
            self.manager, "DELETE",
            f"/sessions/{session}?bot_id={segment(outsider_bot)}",
            status=403, error="forbidden",
            note="delete session rejects an unrelated Bot")
        self.request(
            self.outsider, "DELETE",
            f"/sessions/{session}?bot_id=human_{segment(self.outsider)}",
            status=403, error="forbidden",
            note="delete session rejects an unrelated Human")
        # The session's creator is the driver Bot; the Human that controls it
        # is authorized through the live authority edge.
        controlled_delete = self.request(
            self.manager, "DELETE",
            f"/sessions/{driver_session}?bot_id=human_{segment(self.manager)}",
            note="delete session accepts the Human controller of the creator Bot")
        assert controlled_delete == {"deleted": True, "session_id": driver_session}, \
            controlled_delete
        self.request(
            self.manager, "GET", f"/sessions/{driver_session}", status=404,
            error="session not found",
            note="a Human-controlled delete removes the session")
        creator_delete = self.request(
            self.manager, "DELETE",
            f"/sessions/{session}?bot_id=human_{segment(self.manager)}",
            note="delete session accepts the session creator")
        assert creator_delete == {"deleted": True, "session_id": session}, \
            creator_delete

    # -- helpers used only for assertions -----------------------------
    def group_of(self, session: str) -> str:
        return session.split(":", 1)[0]

    def cleanup(self) -> None:
        failures: list[str] = []
        # Undo the Bot visibility change first so concurrent companion phases
        # never observe this story's Bots as public, even if deletion is slow.
        for user, bot in self.public_bots:
            code = self.best_effort(
                user, "PUT", f"/bots/{bot}/visibility", {"visibility": "private"})
            if code not in (200, 404):
                failures.append(f"restore {bot} to private: HTTP {code}")
        resources: list[tuple[str, str]] = []
        for session, actor in self.sessions:
            resources.append(
                (self.manager, f"/sessions/{session}?bot_id={segment(actor)}"))
        for group, driver in self.groups:
            resources.append(
                (self.manager, f"/groups/{group}?bot_id={segment(driver)}"))
        for user, bot in self.bots:
            resources.append((user, f"/bots/{bot}"))
        for user, path in resources:
            # 404 is expected: the journey deletes some resources itself and a
            # cascading group delete removes that group's sessions.
            code = self.best_effort(user, "DELETE", path)
            if code not in (200, 404):
                failures.append(f"DELETE {path.split('?', 1)[0]}: HTTP {code}")
        for failure in failures:
            print(f"⚠ cleanup failed: {failure}", flush=True)
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
