#!/usr/bin/env python3
"""Provider initializes managers; owner subsequently revokes/transfers authority.

Only live HTTP registration and collaboration APIs are used. No database access
or test-only domain calls: these assertions exercise the same user operations
and authorization boundaries as a Provider integration and workbench clients.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

from a2a_story import Peer
from group_session_ws_probe import _gateway_principal_token
from provider_scope_story import Story


class ManagerStory(Story):
    def authority(self, method, bot, action, *, user=None, status=200):
        user = user or self.owner
        principal = _gateway_principal_token(
            user_id=user, username=user, tenant="bcs-e2e",
            signing_key=os.environ["AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE"],
            issuer="gateway",
        )
        path = f"/api/v1/collaboration/bots/{bot}/{action}"
        req = urllib.request.Request(self.base + path, method=method, headers={
            "Content-Type": "application/json", "X-Avernet-Principal": principal,
        })
        try:
            response = self.http.open(req, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            code, raw = response.code, response.read()
        assert code == status, f"{method} {action}: expected {status}, received {code}"
        self.checks += 1
        return json.loads(raw).get("data") if raw else None

    def register(self, provider, body, *, status=200):
        result = self.request("POST", f"/providers/{provider['provider_id']}/bots", body,
            token=provider["provider_admin_token"], status=status)
        if status == 200 and not any(bot["bot_uuid"] == result["bot_uuid"] for _, bot in self.bots):
            self.bots.append((provider, result))
        return result

    def assert_managers(self, bot, expected, *, user=None):
        page = self.authority("GET", bot, "managers?limit=100", user=user)
        assert page["total"] == len(expected), "initial manager total differs"
        assert set(page["items"]) == set(expected), "manager identity set differs"

    def run(self):
        tag = uuid.uuid4().hex[:12]
        with_peer = Peer()
        with_peer.start()
        try:
            provider = self.request("POST", "/providers", {
                "name": "Initial managers " + tag, "webhook_url": with_peer.base + "/unused",
                "auth": {"mode": "static_bearer"}, "protocol_version": "2.0",
            })
            manager = "manager-" + tag
            case_ids = ["Carol-" + tag, "carol-" + tag, "Jose-" + tag, "José-" + tag]
            expected = [manager, *case_ids]
            body = {"name": "Managed " + tag, "provider_bot_ref": "managed-" + tag,
                "owners": [" " + self.owner + " ", manager, manager, self.owner, *case_ids]}
            result = self.register(provider, body)
            bot = result["bot_uuid"]
            self.assert_managers(bot, expected)
            assert self.authority("GET", bot, "ownership")["owner_user_id"] == self.owner
            # A manager can manage, but never perform owner-only transfers.
            self.assert_managers(bot, expected, user=manager)
            transfer = "ownership-transfers/human_" + urllib.parse.quote(case_ids[0], safe="")
            self.authority("PUT", bot, transfer, user=manager, status=403)
            self.authority("DELETE", bot, "managers/" + manager)
            self.assert_managers(bot, case_ids)
            replay = self.register(provider, body)
            assert replay["bot_uuid"] == bot
            self.assert_managers(bot, case_ids)
            self.authority("GET", bot, "managers", user=manager, status=403)
            self.authority("PUT", bot, transfer, status=201)
            self.authority("POST", bot, transfer + "/accept", user=case_ids[0])
            self.register(provider, body)
            assert self.authority("GET", bot, "ownership", user=case_ids[0])["owner_user_id"] == case_ids[0]

            # A second Bot shares an already registered manager Human, rather than
            # renaming it or changing the first Bot's independent manager set.
            shared = self.register(provider, {"name": "Shared " + tag,
                "provider_bot_ref": "shared-" + tag, "owners": [self.owner, manager]})
            self.assert_managers(shared["bot_uuid"], [manager])
            many = [f"batch-{tag}-{i}" for i in range(100)]
            batch = self.register(provider, {"name": "Batch " + tag,
                "provider_bot_ref": "batch-" + tag, "owners": [self.owner, *many]})
            self.assert_managers(batch["bot_uuid"], many)

            # Invalid identities fail before creation and do not reserve the ref.
            for users in [[], ["", manager], [self.owner, " "], [self.owner, "human_bad"],
                          [self.owner, "bad\nidentity"], [self.owner, "x" * 129],
                          [self.owner] * 102]:
                self.register(provider, {"name": "Invalid " + tag,
                    "provider_bot_ref": "invalid-" + tag, "owners": users}, status=400)
            valid = self.register(provider, {"name": "Invalid " + tag,
                "provider_bot_ref": "invalid-" + tag, "owners": [self.owner, manager]})
            self.assert_managers(valid["bot_uuid"], [manager])

            # The Provider ID A2A branch commits the same authority snapshot with
            # its inline Card. Duplicate A2A registration remains a conflict.
            card = copy.deepcopy(with_peer.card)
            card["name"] = "Managed A2A " + tag
            a2a_body = {"provider_bot_ref": "a2a-managers-" + tag, "agent_card": card,
                "owners": [self.owner, manager, *case_ids]}
            a2a = self.register(provider, a2a_body)
            self.assert_managers(a2a["bot_uuid"], expected)
            self.register(provider, a2a_body, status=409)
            self.request("GET", "/bots/" + a2a["bot_uuid"] + "/agent-card")
            print(f"Provider initial managers: {self.checks} live API assertions passed")
        finally:
            with_peer.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    story = ManagerStory(args.base_url, args.owner)
    try:
        story.run()
    finally:
        story.cleanup()
