#!/usr/bin/env python3
"""Exercise sibling Bot registration and runtime isolation through a live BCS.

The public Singlebox has no proprietary AgentPass verifier. This journey uses
issued Bot tokens and signed Gateway principals; it does not fake parent-token
verification or call application services in-process.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class Story:
    def __init__(self, base_url, human):
        self.base_url = base_url.rstrip("/")
        self.human = human
        self.checks = 0

    def request(
        self, label, method, path, status=200, body=None, token=None, headers=None
    ):
        request_headers = {
            "Content-Type": "application/json",
            "X-Mock-User-Id": self.human,
            "X-Mock-Nick-Name": "Subbots E2E operator",
        }
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        request_headers.update(headers or {})
        request = urllib.request.Request(
            self.base_url + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=request_headers,
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            # Do not print response bodies or URLs: registration returns secrets
            # and scoped registration carries its credential in the query string.
            self.check(
                response.status == status,
                f"{label}: HTTP {response.status}, expected {status}",
            )
            payload = json.load(response)
            return payload, response.headers

    def check(self, condition, label):
        if not condition:
            raise AssertionError(label)
        self.checks += 1

    def self_view(self, bot, parent, actor=None, status=200):
        headers = {} if actor is None else {"X-BCN-Actor-Bot-Id": actor}
        payload, response_headers = self.request(
            "Bot self lookup",
            "GET",
            "/api/v1/collaboration/bots/me",
            status=status,
            token=bot["token"],
            headers=headers,
        )
        self.check(
            response_headers.get("Cache-Control") == "no-store",
            "self lookup disables caching",
        )
        if status == 200:
            data = payload["data"]
            self.check(
                data["registration_status"] == "registered", "Bot remains registered"
            )
            self.check(
                data["identity"]["agent_code"] == parent,
                "metadata update preserves parent identity",
            )
            self.check(
                data["bot"]["bot_id"] == bot["id"],
                "self lookup selects the credential's Bot",
            )
            self.check(
                data["bot"]["provider_bot_ref"] == bot["ref"],
                "self lookup preserves composed ref",
            )
            self.check(
                bot["token"] not in json.dumps(payload),
                "self projection contains no runtime token",
            )

    def signed_bot(self, bot, parent):
        key = os.environ.get(
            "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE",
            "avernet-dev-signing-key-NOT-FOR-PROD",
        )
        now = int(time.time())
        claims = {
            "iss": "gateway",
            "aud": "bcs",
            "iat": now - 1,
            "exp": now + 300,
            "principals": [
                {
                    "type": "bot",
                    "tenant": "e2e",
                    "bot": {
                        "bot_uuid": bot["id"],
                        "owner_id": self.human,
                        "app_id": 0,
                        "agent_code": parent,
                        "tenant": "e2e",
                    },
                }
            ],
        }

        def encode(value):
            return base64.urlsafe_b64encode(
                json.dumps(value, separators=(",", ":")).encode()
            ).rstrip(b"=")

        message = (
            encode({"alg": "HS256", "typ": "JWT", "kid": "bare"})
            + b"."
            + encode(claims)
        )
        signature = base64.urlsafe_b64encode(
            hmac.new(key.encode(), message, hashlib.sha256).digest()
        ).rstrip(b"=")
        return (message + b"." + signature).decode()

    def run(self):
        suffix = uuid.uuid4().hex[:12]
        parent = f"poolab:e2e:{suffix}"
        mode = "agentpass-subbots"
        provider, _ = self.request(
            "create subbots Provider",
            "POST",
            "/providers",
            body={
                "name": "E2E sibling Bots",
                "slug": f"e2e-subbots-{suffix}",
                "webhook_url": "https://provider.example.com/bcs/webhook",
                "auth": {"mode": mode},
                "protocol_version": "2.0",
                "coordination": {"mode": "native_tool"},
            },
        )
        provider_id, admin = provider["provider_id"], provider["provider_admin_token"]
        path = f"/providers/{provider_id}"
        public, _ = self.request(
            "discover subbots mode", "GET", f"/providers/by-slug/e2e-subbots-{suffix}"
        )
        self.check(
            public["auth_mode"] == mode, "Provider discovery reports subbots mode"
        )

        registration = {
            "name": "E2E Reviewer",
            "summary": "Review changes",
            "owners": [self.human],
            "provider_bot_ref": f"{parent}:reviewer",
        }
        for invalid in ("parent-without-slug", ":a", f"{parent}:", f"{parent}:Uppercase",
                        "bad/parent:a", f"{parent}:" + "x" * 33):
            self.request(
                "reject invalid complete ref before registration",
                "POST",
                path + "/bots",
                400,
                body={**registration, "provider_bot_ref": invalid},
                token=admin,
            )
        first, _ = self.request(
            "register reviewer with Provider credential",
            "POST",
            path + "/bots",
            body=registration,
            token=admin,
        )
        self.check(
            first["agent_code"] == parent and first["bot_slug"] == "reviewer",
            "registration returns parent and slug",
        )
        self.check(
            first["auth_mode"] == mode
            and first["provider_bot_ref"] == f"{parent}:reviewer",
            "server preserves client-composed ref",
        )
        a = {
            "id": first["bot_uuid"],
            "token": first["bot_runtime_token"],
            "ref": first["provider_bot_ref"],
        }
        self.request(
            "reject duplicate reviewer",
            "POST",
            path + "/bots",
            409,
            body=registration,
            token=admin,
        )

        scoped, _ = self.request(
            "owner issues scoped registration credential",
            "GET",
            "/register/token?" + urllib.parse.urlencode({"provider_id": provider_id}),
        )
        query = {
            "token": scoped["token"],
            "bot-name": "E2E Coder",
            "mode": "plugin",
            "provider_bot_ref": f"{parent}:coder",
        }
        self.request(
            "removed bot_slug cannot complete a malformed ref",
            "POST",
            "/register?"
            + urllib.parse.urlencode(
                {**query, "provider_bot_ref": "parent-without-slug", "bot_slug": "coder"}
            ),
            400,
        )
        for invalid in (":a", f"{parent}:", f"{parent}:Uppercase", "bad/parent:a"):
            self.request(
                "scoped registration rejects malformed complete ref", "POST",
                "/register?" + urllib.parse.urlencode({**query, "provider_bot_ref": invalid}),
                400,
            )
        second, _ = self.request(
            "register coder with scoped credential",
            "POST",
            "/register?" + urllib.parse.urlencode(query),
        )
        identity = second["registration"]
        self.check(
            identity["agent_code"] == parent and identity["bot_slug"] == "coder",
            "scoped registration returns child identity",
        )
        self.check(
            identity["auth_mode"] == mode
            and identity["provider_bot_ref"] == f"{parent}:coder",
            "both paths parse the same identity",
        )
        b = {
            "id": second["bot_uuid"],
            "token": second["bot_token"],
            "ref": identity["provider_bot_ref"],
        }
        self.check(
            a["id"] != b["id"] and a["token"] != b["token"],
            "siblings receive distinct UUIDs and tokens",
        )
        self.request(
            "scoped duplicate fails at registration",
            "POST",
            "/register?" + urllib.parse.urlencode(query),
            409,
        )
        print(
            "PASS: both registration paths validate slugs and preserve sibling identities"
        )

        for bot, sibling in ((a, b), (b, a)):
            self.self_view(bot, parent)
            self.self_view(bot, parent, actor=bot["id"])
            self.self_view(bot, parent, actor=sibling["id"], status=401)
            self.request(
                "legacy auth accepts matching Actor",
                "GET",
                "/groups/my",
                token=bot["token"],
                headers={"X-BCN-Actor-Bot-Id": bot["id"]},
            )
            self.request(
                "legacy auth rejects sibling Actor despite mock user",
                "GET",
                "/groups/my",
                401,
                token=bot["token"],
                headers={"X-BCN-Actor-Bot-Id": sibling["id"]},
            )
        self.request(
            "Actor alone cannot fall back to human",
            "GET",
            "/groups/my",
            401,
            headers={"X-BCN-Actor-Bot-Id": a["id"]},
        )
        self.request(
            "malformed Actor is rejected",
            "GET",
            "/groups/my",
            401,
            token=a["token"],
            headers={"X-BCN-Actor-Bot-Id": ""},
        )
        changed, _ = self.request(
            "update reviewer metadata",
            "PATCH",
            path + "/bots/" + urllib.parse.quote(a["ref"], safe=""),
            body={"name": "E2E Senior Reviewer", "domains": ["audit"], "scopes": []},
            token=admin,
        )
        self.check(
            changed["bot_uuid"] == a["id"] and changed["name"] == "E2E Senior Reviewer",
            "update retains Bot UUID",
        )
        self.self_view(a, parent)

        self.request(
            "onboarding metadata preserves parent identity",
            "POST",
            "/bots/onboard",
            body={"name": "E2E Reviewer", "summary": "Review changes"},
            token=a["token"],
            headers={"X-BCN-Actor-Bot-Id": a["id"]},
        )
        self.self_view(a, parent)
        # Parent discovery validates pagination before invoking the deployment
        # verifier; a concrete Bot token must never produce a parent identity.
        self.request(
            "parent discovery requires credentials",
            "GET",
            "/api/v1/collaboration/agents/me",
            401,
        )
        for query_string in ("limit=0", "limit=101", "cursor=", "unknown=value"):
            self.request(
                "parent discovery rejects invalid query",
                "GET",
                "/api/v1/collaboration/agents/me?" + query_string,
                400,
                token=a["token"],
            )
        unavailable, _ = self.request(
            "public build fails closed without AgentPass verifier",
            "GET",
            "/api/v1/collaboration/agents/me",
            502,
            token=a["token"],
        )
        self.check(
            unavailable["data"]["error_code"] == "agent_identity_unavailable",
            "missing verifier cannot authenticate a parent",
        )

        event = {
            "run_id": f"missing-subbots-run-{suffix}",
            "state": "final",
            "message": {"text": "finished"},
        }
        callback_headers = {
            "X-BCN-Provider-Id": provider_id,
            "X-BCN-Actor-Bot-Id": a["id"],
        }
        result, _ = self.request(
            "authenticated callback reaches run lookup",
            "POST",
            "/bot/events",
            404,
            body=event,
            token=a["token"],
            headers=callback_headers,
        )
        self.check(
            result["error"] == "run_not_found",
            "callback validates identity before resolving run",
        )
        self.request(
            "callback rejects sibling Actor",
            "POST",
            "/bot/events",
            401,
            body=event,
            token=a["token"],
            headers={**callback_headers, "X-BCN-Actor-Bot-Id": b["id"]},
        )
        self.request(
            "callback accepts Bot token without Actor",
            "POST",
            "/bot/events",
            404,
            body=event,
            token=a["token"],
            headers={"X-BCN-Provider-Id": provider_id},
        )
        print(
            "PASS: static-token Actor constraints apply to self lookup, legacy APIs and callbacks"
        )

        # A signed Bot principal must still be checked against live persisted
        # state; an unsigned Actor header cannot change the signed identity.
        principal = {
            "X-Avernet-Principal": self.signed_bot(a, parent),
            "X-BCN-Actor-Bot-Id": b["id"],
        }
        openapi_path = "/openapi/v1/collaboration/public-groups"
        self.request(
            "Gateway principal uses signed Bot", "GET", openapi_path, headers=principal
        )
        self.request("disable Provider", "POST", path + "/disable", token=admin)
        self.self_view(a, parent, status=401)
        self.self_view(b, parent, status=401)
        self.request(
            "disabled Provider invalidates signed Bot",
            "GET",
            openapi_path,
            401,
            headers=principal,
        )
        self.request(
            "disabled Provider rejects callbacks",
            "POST",
            "/bot/events",
            401,
            body=event,
            token=a["token"],
            headers=callback_headers,
        )
        self.request("enable Provider", "POST", path + "/enable", token=admin)
        self.self_view(a, parent)
        self.self_view(b, parent)
        self.request(
            "enabled Provider restores signed Bot",
            "GET",
            openapi_path,
            headers=principal,
        )

        self.request(
            "delete reviewer",
            "DELETE",
            path + "/bots/" + urllib.parse.quote(a["ref"], safe=""),
            token=admin,
        )
        self.self_view(a, parent, status=401)
        self.self_view(b, parent)
        self.request(
            "deleted Bot invalidates signed principal",
            "GET",
            openapi_path,
            401,
            headers=principal,
        )
        self.request(
            "deleted slug cannot be reclaimed",
            "POST",
            path + "/bots",
            409,
            body=registration,
            token=admin,
        )
        self.request(
            "scoped path cannot reclaim deleted slug",
            "POST",
            "/register?" + urllib.parse.urlencode({**query, "provider_bot_ref": f"{parent}:reviewer"}),
            409,
        )
        self.request(
            "delete coder",
            "DELETE",
            path + "/bots/" + urllib.parse.quote(b["ref"], safe=""),
            token=admin,
        )
        self.self_view(b, parent, status=401)
        self.request("retire test Provider", "POST", path + "/disable", token=admin)
        print(
            f"PASS: disable/re-enable and deletion enforce persisted identity ({self.checks} assertions)"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--human", required=True)
    args = parser.parse_args()
    Story(args.base_url, args.human).run()


if __name__ == "__main__":
    main()
