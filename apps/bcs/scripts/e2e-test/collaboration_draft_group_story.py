#!/usr/bin/env python3
"""Open an immutable shared Draft, edit a copy, and launch via the browser API.

Uses the live server's local Human auth plugin and the coverage Provider mock.
No database fixtures, in-process service calls, or external model are needed.
"""

import argparse
import hashlib
import http.client
import json
from pathlib import Path
import urllib.parse
import uuid


class Story:
    def __init__(self, base_url, human, mock_sink_url):
        self.base = urllib.parse.urlsplit(base_url.rstrip("/"))
        self.human = human
        self.mock_sink_url = mock_sink_url.rstrip("/")
        self.prefix = "Draft launch " + uuid.uuid4().hex[:10]
        self.bots = []
        self.provider = None
        self.driver = None
        self.group = None
        self.draft = None
        self.checks = 0

    def check(self, condition, label):
        if not condition:
            raise AssertionError(label)
        self.checks += 1

    def request(self, label, method, path, *, body=None, headers=(), status=200,
                error=None, envelope=False):
        # http.client preserves duplicate headers so malformed references and
        # stale browser identity are tested at the actual HTTP boundary.
        connection = (http.client.HTTPSConnection if self.base.scheme == "https"
                      else http.client.HTTPConnection)(self.base.netloc, timeout=30)
        payload = None if body is None else json.dumps(body).encode()
        try:
            connection.putrequest(method, self.base.path + path)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("X-Mock-User-Id", self.human)
            connection.putheader("X-Mock-Nick-Name", "Draft launch operator")
            for key, value in headers:
                connection.putheader(key, value)
            if payload is not None:
                connection.putheader("Content-Length", str(len(payload)))
            connection.endheaders(payload)
            response = connection.getresponse()
            raw = response.read()
            # Registration URLs and response bodies contain credentials.
            self.check(response.status == status,
                       f"{label}: HTTP {response.status}, expected {status}")
            result = json.loads(raw) if raw else {}
            if envelope:
                self.check(response.getheader("Cache-Control") == "no-store",
                           label + ": response must not be cached")
                self.check(result["code"] == status * 100, label + ": envelope status")
                result = result["data"]
            if error:
                self.check(result["error_code"] == error, label + ": error code")
            return result
        finally:
            connection.close()

    def browser(self, label, method, path, **kwargs):
        return self.request(label, method, "/collaboration/user" + path,
                            envelope=True, **kwargs)

    def register(self, role):
        token = self.request("issue registration token", "GET", "/register/token")["token"]
        name = self.prefix + " " + role
        query = urllib.parse.urlencode({"token": token, "bot-name": name})
        bot = self.request("register disposable " + role, "POST", "/register?" + query)
        self.bots.append(bot)
        self.request("onboard " + role, "POST", "/bots/onboard",
                     headers=self.credentials(bot), body={"name": name, "summary": role})
        return bot

    @staticmethod
    def credentials(bot):
        return [("Authorization", "Bearer " + bot["bot_token"])]

    def collaborator(self, provider, role):
        bot = self.request("register connected " + role, "POST",
                           f"/providers/{provider['provider_id']}/bots",
                           headers=[("Authorization", "Bearer " + provider["provider_admin_token"])],
                           body={"name": self.prefix + " " + role, "summary": role,
                                 "owners": [self.human], "provider_bot_ref": str(uuid.uuid4())})
        bot["bot_token"] = bot["bot_runtime_token"]
        self.bots.append(bot)
        return bot

    def run(self):
        self.request("ensure Human", "POST", "/me/ensure-human", body={})
        author = self.register("author")
        self.provider = self.request("register scripted Provider", "POST", "/providers", body={
            "name": self.prefix, "webhook_url": self.mock_sink_url + "/provider/webhook",
            "auth": {"mode": "static_bearer"}, "protocol_version": "2.0",
            "coordination": {"mode": "native_tool"},
        })
        driver, reviewer = (self.collaborator(self.provider, role) for role in ("driver", "reviewer"))
        self.driver = driver["bot_uuid"]
        # The assistant authors the card, but the user chooses a different Bot
        # to lead the group and one of that Bot's eligible collaborators.
        invite = self.request("driver requests collaboration", "POST", "/friends/request",
                              headers=self.credentials(driver), status=201,
                              body={"from_bot": driver["bot_uuid"], "to_bot": reviewer["bot_uuid"]})
        self.request("reviewer accepts collaboration", "POST",
                     f"/friends/requests/{invite['data']['id']}/accept",
                     headers=self.credentials(reviewer), body={"bot_uuid": reviewer["bot_uuid"]})

        fixture = Path(__file__).resolve().parents[2] / "tests/fixtures/collaboration-definition-draft/indentation-regression.json"
        source = json.loads(fixture.read_text())["revision"]["definition_yaml"]
        self.check(len(source.encode()) == 2174, "use the unchanged incident sample")
        digest = hashlib.sha256(source.encode()).hexdigest()
        self.draft = "/collaboration/drafts/" + str(uuid.uuid4())
        saved = self.request("assistant uploads incident YAML", "PUT", self.draft,
                             headers=self.credentials(author), status=201,
                             body={"definition_yaml": source, "title": self.prefix})
        self.check(saved["revision"] == 1 and saved["content_hash"] == digest, "saved immutable source")
        reference = self.request("assistant obtains share card", "GET", self.draft + "/revisions/1/reference",
                                 headers=self.credentials(author))
        self.check(set(reference) == {"schemaVersion", "draftId", "revision", "readToken"},
                   "reference remains compatible with the deployed CLI")
        self.check(reference["readToken"] == "A" * 43, "legacy field is a fixed non-secret placeholder")
        revision = "/api/v1" + self.draft + "/revisions/1"
        opened = self.request("V1 host opens shared card", "GET", revision, envelope=True)
        self.check(opened["definition_yaml"].encode() == source.encode(), "V1 preview preserves all original bytes")
        self.check(opened["content_hash"] == digest and opened["revision"] == 1, "V1 preview pins hash and revision")
        validated = self.request("V1 host validates stored revision", "POST", revision + "/validate",
                                 envelope=True)
        self.check(validated["validation"]["valid"] is True, "shared source is valid")
        self.check(validated["content_hash"] == digest and validated["revision"] == 1, "validation binds the same revision")

        for method, suffix in (("GET", ""), ("POST", "/validate")):
            self.request("missing revision", method, revision.replace("/revisions/1", "/revisions/2") + suffix,
                         status=404, error="DRAFT_REFERENCE_UNAVAILABLE", envelope=True)
        self.request("invalid revision", "GET", revision.rsplit("/", 1)[0] + "/invalid",
                     status=400, error="DRAFT_INPUT", envelope=True)
        self.request("validation cannot replace the stored YAML", "POST", revision + "/validate",
                     body={"definition_yaml": "replacement"}, status=400,
                     error="DRAFT_INPUT", envelope=True)

        user = self.browser("read current Human", "GET", "/me")
        self.check(user["user_id"] == self.human and user["display_name"] == "Draft launch operator",
                   "browser identity comes from the configured Human auth chain")
        expected = [("X-BCS-Expected-User", user["user_id"])]
        query = urllib.parse.urlencode({"offset": 0, "limit": 20, "kind": "bot", "name": self.prefix})
        mine = self.browser("select an owned originator", "GET", "/bots/mine?" + query, headers=expected)
        self.check({bot["bot_id"] for bot in mine["items"]} == {bot["bot_uuid"] for bot in self.bots},
                   "own Bot selector returns the user's registered Bots")
        candidates = self.browser("load originator collaborators", "GET",
                                  f"/bots/{driver['bot_uuid']}/candidates?purpose=collaboration&offset=0&limit=20&name="
                                  + urllib.parse.quote(self.prefix), headers=expected)
        self.check(reviewer["bot_uuid"] in {item["bot"]["bot_id"] for item in candidates["items"]},
                   "friend appears as an eligible collaborator")

        edited = source.replace("name: 写稿审稿发表协作", "name: Human edited collaboration", 1)
        body = {
            "group_kind": "normal", "name": self.prefix, "context": "Verify draft launch",
            "originator": driver["bot_uuid"], "driver_bot_uuid": driver["bot_uuid"],
            "participants": [{"actor_id": driver["bot_uuid"], "role": "driver"},
                             {"actor_id": reviewer["bot_uuid"], "role": "consultant"}],
            "collaboration": {"strategy": "state_machine", "definition": {"content_yaml": edited},
                              "participant_bindings": [
                                  {"binding": role, "actor_ids": [bot["bot_uuid"]]}
                                  for role, bot in (("writer", driver), ("reviewer", reviewer), ("publisher", driver))]},
        }
        for label, headers, error in (
            ("missing browser context", [], "browser_context_required"),
            ("cross-site submission", expected + [("Sec-Fetch-Site", "cross-site")], "browser_context_required"),
            ("account changed", [("X-BCS-Expected-User", "another-user")], "user_context_changed"),
            ("duplicate expected user", expected + expected, "user_context_changed"),
        ):
            self.browser(label, "POST", "/groups", body=body, headers=headers, status=403, error=error)
        for header in (self.credentials(author)[0], ("Actor", driver["bot_uuid"]),
                       ("X-Avernet-Principal", "invalid")):
            self.browser("Bot credentials cannot impersonate a Human", "POST", "/groups",
                         body=body, headers=expected + [header], status=401, error="unauthenticated")

        created = self.browser("Human creates edited collaboration", "POST", "/groups",
                               body=body, headers=expected, status=201)
        self.group = created["group_id"]
        self.check(bool(created.get("initial_session_id")), "creation supplies the group navigation Session")
        group = self.request("read persisted group", "GET", "/groups/" + self.group)
        self.check(group["driver_bot"] == driver["bot_uuid"], "chosen Bot leads the collaboration")
        definition = self.request("read persisted collaboration", "GET",
                                  "/groups/" + self.group + "/collaboration-definition")
        self.check(definition["definition_yaml"] == edited, "group stores the user's edited YAML")
        self.check({role: value["bot_ids"] for role, value in definition["participant_bindings"].items()}
                   == {"writer": [self.driver], "publisher": [self.driver], "reviewer": [reviewer["bot_uuid"]]},
                   "group persists the chosen role bindings, without substituting the author Bot")
        reopened = self.request("historical card remains unchanged", "GET", revision, envelope=True)
        self.check(reopened["definition_yaml"] == source, "editing the group does not mutate the Draft")
        self.request("author revokes the shared revision", "DELETE", self.draft + "/revisions/1/share",
                     headers=self.credentials(author), status=204)
        for method, suffix in (("GET", ""), ("POST", "/validate")):
            self.request("revoked V1 reference is unavailable", method, revision + suffix,
                         status=404, error="DRAFT_REFERENCE_UNAVAILABLE", envelope=True)

    def cleanup(self):
        errors = []
        resources = []
        if self.group:
            resources.append(("group", "/groups/" + self.group + "?bot_id=" + self.driver, []))
        if self.draft:
            resources.append(("Draft", self.draft, self.credentials(self.bots[0])))
        for bot in reversed(self.bots):
            if "provider_bot_ref" in bot:
                resources.append(("Bot", f"/providers/{self.provider['provider_id']}/bots/{bot['provider_bot_ref']}",
                                  [("Authorization", "Bearer " + self.provider["provider_admin_token"])]))
            else:
                resources.append(("Bot", "/bots/" + bot["bot_uuid"], []))
        for label, path, headers in resources:
            try:
                self.request("delete disposable " + label, "DELETE", path, headers=headers,
                             status=204 if label == "Draft" else 200)
            except Exception as error:
                errors.append(str(error))
        if self.provider:
            try:
                self.request("disable disposable Provider", "POST", f"/providers/{self.provider['provider_id']}/disable",
                             headers=[("Authorization", "Bearer " + self.provider["provider_admin_token"])])
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise AssertionError("Cleanup failed: " + "; ".join(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--human", required=True)
    parser.add_argument("--mock-sink-url", required=True)
    args = parser.parse_args()
    story = Story(args.base_url, args.human, args.mock_sink_url)
    try:
        story.run()
    finally:
        story.cleanup()
    print(f"Draft group launch story: {story.checks} live HTTP assertions passed (including cleanup)")
