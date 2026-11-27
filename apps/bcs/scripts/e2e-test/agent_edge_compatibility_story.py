#!/usr/bin/env python3
"""Trusted edge fixtures consumed by existing HTTP APIs and a real A2A peer.

SQLite writes construct phase-one type/state fixtures only: no public edge
management, approval, qualification feed, new invocation API or SDK is added.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import uuid
from urllib.parse import urlencode

from a2a_story import Peer
from caller_execution_story import free_port
from provider_scope_story import Story


def exercise(story, peer, database):
    owners = ["edgeownera", "edgeownerb", "edgeownerc"]
    for owner in owners:
        story.request("POST", "/me/ensure-human", {}, owner=owner)
    slug = "edge-type-" + uuid.uuid4().hex
    provider = story.request("POST", "/providers", {
        "slug": slug, "name": "Directed edge fixture", "protocol_version": "2.0",
        "webhook_url": peer.base + "/legacy-webhook-must-not-be-used",
        "auth": {"mode": "static_bearer"}, "collaboration_scope": "standard"})
    card = copy.deepcopy(peer.card)
    card["capabilities"]["streaming"] = False
    peer.mode = "poll_message"
    admin = provider["provider_admin_token"]
    bots = []
    for owner in owners:
        bot = story.request("POST", f"/a2a/providers/{slug}/bots", {
            "provider_bot_ref": "edge-" + uuid.uuid4().hex,
            "owners": [owner], "agent_card": card,
        }, token=admin, status=201)
        story.request("PUT", f"/bots/{bot['bot_id']}/visibility", {"visibility": "protected"}, owner=owner)
        bots.append(bot)
    a, b, c = [bot["bot_id"] for bot in bots]
    policy = story.request("GET", "/admin/message-delivery/policy")
    updated = copy.deepcopy(policy["policy"])
    updated["flow_enabled"]["direct_a2a"] = True
    for bot in (a, b, c):
        updated["bots"][bot] = {"mode": "enforce", "max_running": 1,
                                "min_send_interval_ms": 1, "max_queued": 8}
    story.request("PUT", "/admin/message-delivery/policy", {
        "expected_version": policy["version"], "policy": updated})

    def admission(source, target, env="local", status=200):
        return story.request("GET", f"/bots/{target}/admission?" + urlencode({
            "actor": source, "originator": source, "env": env}), status=status)

    def send(index, target, status):
        runtime = bots[index]["bot_runtime_token"]
        return story.request("POST", f"/bots/{target}/chat-async", {
            "message": "A2A question directed edge", "timeout_ms": 10_000,
        }, token=runtime, owner=owners[index], status=status)

    assert not admission(a, b)["allowed"]
    send(0, b, 403)
    assert not peer.requests
    with sqlite3.connect(database) as db:
        # One fixture write, not a new edge-management API or lifecycle protocol.
        db.executemany("INSERT INTO edge_grants (env,from_id,to_id,grant_kind,grant_ref_id,"
                       "rules,status,originator_policy_type,originator_policy_data) "
                       "VALUES ('local',?,?,'agent_invoke',0,NULL,?,'same_as_from',NULL)",
                       [(a, b, "approved"), (b, c, "approved"), (c, a, "revoked")])
    result = admission(a, b)
    assert result["allowed"] and not result["public_default"]
    assert len(result["grants"]) == 1
    assert result["grants"][0]["kind"] == "agent_invoke"
    assert result["grants"][0]["ref_id"] == 0
    for source, target in [(b, a), (a, c), (c, a)]:
        assert not admission(source, target)["allowed"]
    assert not admission(a, b, "other-environment")["allowed"]
    for index, target in [(1, a), (0, c), (2, a)]:
        send(index, target, 403)
    accepted = send(0, b, 202)
    runtime = bots[0]["bot_runtime_token"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        run = story.request("GET", f"/chat/runs/{accepted['run_id']}?wait_ms=500",
                            token=runtime)
        if run["is_terminal"]:
            assert run["state"] == "completed"
            assert run["response"]["content"] == "A2A-ANSWER-1"
            break
        time.sleep(0.05)
    else:
        raise AssertionError("Existing direct call did not complete")
    assert len(peer.requests) == 1 and peer.legacy_requests == 0
    with sqlite3.connect(database) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                              "AND name GLOB 'agent_invoke_*'").fetchall()
        assert "agent_invoke_identity_revision" not in {
            row[1] for row in db.execute("PRAGMA table_info(bcs_bots)")}
        assert db.execute("SELECT COUNT(*) FROM edge_grants WHERE from_id=? AND to_id=? "
                          "AND grant_kind='permission_profile'", (a, b)).fetchone()[0] == 0
        # Malformed fixture data must fail the real read, not partially allow.
        db.execute("UPDATE edge_grants SET rules='null' WHERE from_id=? AND to_id=?", (a, b))
    admission(a, b, status=500)
    send(0, b, 500)
    assert len(peer.requests) == 1
    print(f"AgentInvoke type compatibility E2E: {story.checks} HTTP assertions; "
          "one real A2A delivery; direction/state/error and legacy isolation passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    binary = str(Path(parser.parse_args().binary).resolve())
    peer = Peer()
    peer.start()
    try:
        with tempfile.TemporaryDirectory(prefix="bcs-agent-edge-type-") as directory:
            root = Path(directory)
            port = free_port()
            base = f"http://127.0.0.1:{port}"
            config = json.loads(subprocess.check_output([binary, "--print-config"]))
            config.update(bind="127.0.0.1", port=port, bcs_endpoint=base, store_messages=True,
                strict_container_validation=False, a2a_allow_anonymous=True,
                auth={"chain": ["local"], "allow_mock_headers": True, "mock_user_id": "edgeownera"},
                gateway_principal={"signing_key_env": "EDGE_TYPE_SIGNING_KEY"},
                database={"type": "sqlite", "sqlite": {"path": str(root / "bcs.db")}},
                security={"outbound_url": {"block_private_networks": True, "allow_loopback": True}},
                llm={"type": "none"}, bcsfuse={"enabled": False}, bots_base_dir=str(root / "bots"))
            config["logging"].update(console=True, default_level="warn")
            config["secret"] = {"provider": "env", "providers": {"env": {"prefix": "EDGE_TYPE_"}}}
            config["session_files"]["backend"] = {"data_dir": str(root / "files")}
            config["collaboration"]["templates"]["base_dir"] = str(root / "templates")
            (root / "catalog-labels.toml").write_bytes((Path(__file__).resolve().parents[2] / "configs/catalog-labels.toml").read_bytes())
            path = root / "config.json"
            path.write_text(json.dumps(config))
            env = dict(os.environ, SERVER_ENV="local", EDGE_TYPE_SIGNING_KEY=uuid.uuid4().hex,
                       EDGE_TYPE_BCN_GROUP_SESSION_WS_JWT=uuid.uuid4().hex)
            story = Story(base, "edgeownera")
            with (root / "server.log").open("w") as log:
                process = subprocess.Popen([binary, "-c", str(path)], cwd=root, env=env, stdout=log, stderr=log)
                try:
                    for _ in range(150):
                        assert process.poll() is None, "BCS startup failed"
                        try:
                            story.request("GET", "/health")
                            break
                        except (OSError, urllib.error.URLError):
                            time.sleep(0.1)
                    else:
                        raise AssertionError("BCS startup timed out")
                    exercise(story, peer, root / "bcs.db")
                finally:
                    if process.poll() is None:
                        process.send_signal(signal.SIGTERM)
                        process.wait(timeout=30)
                assert process.returncode == 0, "BCS failed to flush coverage on shutdown"
    finally:
        peer.close()


if __name__ == "__main__":
    main()
