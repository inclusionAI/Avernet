"""Resume a bot session and page its formal Groups through the real CLI and BCS."""

import argparse
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import time

from http_provider_judge_mock import Handler
from recent_activity_sort_story import Story
from sqlite_story_server import sqlite_server


def assert_succeeded_delivery_is_not_replayable(story, subscription):
    path = f"/event-subscriptions/{subscription}/deliveries?status=succeeded&limit=1"
    deadline = time.monotonic() + 30
    while not (items := story.request(story.manager, "GET", path)["items"]):
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for group.created webhook delivery")
        time.sleep(0.25)
    delivery = items[0]["delivery_id"]
    error = story.request(story.manager, "POST", f"/event-deliveries/{delivery}:replay", {
        "replay_request_id": "retry-" + delivery, "expected_subscription_revision": 1,
    }, status=409)
    assert error["error_code"] == "event_delivery_not_replayable", error


def run(story, cli, sink):
    driver = story.register("Session page driver")
    peer = story.register("Session page peer")
    body = {
        "group_kind": "normal", "name": "Session page team", "driver_bot_uuid": driver,
        "participants": [{"actor_id": driver, "role": "driver"},
                         {"actor_id": peer, "role": "consultant"}],
        "collaboration": {"strategy": "chat"},
        "event_subscriptions": [{"name": "Team creation receipt", "event_filters": ["group.created"],
                                 "payload": {"mode": "full"}, "sink": {"type": "webhook", "url": sink}}],
    }
    team = story.request(story.manager, "POST", "/groups", body, status=201)
    team_id = team["group_id"]
    solo = story.create_group(driver, "Session page solo")
    dm = story.request(story.manager, "POST", "/groups", {
        "group_kind": "dm", "target_actor_id": driver, "driver_bot": driver, "participants": []}, public=False)["id"]

    with tempfile.TemporaryDirectory(dir=os.environ.get("BCS_CLI_MKTEMP_DIR")) as tmp:
        ws = story.base_url.replace("http://", "ws://", 1) + "/ws/bot"
        profile = Path(tmp) / ".bcs"
        profile.mkdir()
        session = {"token": story.bot_tokens[driver], "bot_uuid": driver, "bcs_url": ws}
        (profile / "session.json").write_text(json.dumps(session))
        env = dict(os.environ, BOT_DATA_DIR=tmp)
        for key in ("MOLTIS_BCS_URL", "BCS_API_BASE_URL", "BCN_BOT_TOKEN", "BCN_ACTOR_BOT_ID", "BCN_AUTH_MODE"):
            env.pop(key, None)

        def page(offset):
            output = subprocess.check_output([cli, "--json", "list-groups", "--batch-size", "1",
                                              "--offset", str(offset)], env=env, timeout=30)
            return json.loads(output)

        first = page(0)
        assert first["has_more"] and first["next_offset"] == 1
        # An explicit API environment setting overrides even a stale session URL.
        env["BCS_API_BASE_URL"] = story.base_url
        (profile / "session.json").write_text(json.dumps({**session, "bcs_url": "invalid"}))
        second = page(first["next_offset"])
        assert not second["has_more"]
        for offset, result in enumerate((first, second)):
            assert (result["offset"], result["returned"], result["total"]) == (offset, 1, 2), result
        rows = {r["group_id"]: r for result in (first, second) for r in result["items"]}
        assert set(rows) == {solo, team_id} and dm not in rows, rows
        members = [(p["bot_uuid"], p["role"]) for p in rows[team_id]["participants"]]
        assert members.count((driver, "driver")) == members.count((peer, "consultant")) == 1, members
    assert_succeeded_delivery_is_not_replayable(story, team["event_subscriptions"][0]["subscription_id"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--cli", required=True)
    args = parser.parse_args()
    # DMs cannot be deleted. Discard the isolated DB rather than leaving
    # principals and Groups behind in the shared Singlebox instance.
    with sqlite_server(args.binary) as (base_url, _, signing_key):
        fixture = Story(base_url)
        fixture.signing_key = signing_key
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as receiver:
            thread = Thread(target=receiver.serve_forever)
            thread.start()
            try:
                run(fixture, args.cli, f"http://127.0.0.1:{receiver.server_port}/event-webhook")
            finally:
                receiver.shutdown()
                thread.join()
