#!/usr/bin/env python3
"""Caller-config storage legs against an isolated BCS process.

Locks the §10.7.2 bounded compatibility of the caller-configuration sync
against a real server: a row whose `bot_info` was double-encoded by a
legacy writer (the column holds a JSON *string* wrapping the JSON object)
must sync, warn, and heal the row through the ordinary Apply write, while
every deeper or non-object encoding must stay the frozen fail-closed
storage error and leave the row byte-identical. Corrupt shapes are
injected with SQL because no API can produce them (the same rationale as
delivery_fault_isolation_story).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid

STORAGE_ERROR = "Internal error: caller configuration storage is unreadable"


def segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class Story:
    def __init__(self, base_url: str, owner: str, db_path: Path) -> None:
        self.base_url = base_url
        self.owner = owner
        self.db_path = db_path
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.checks = 0

    def request(self, path: str, body=None, *, method="GET", headers=None, status=None):
        request = urllib.request.Request(
            self.base_url + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "X-Mock-User-Id": self.owner, **(headers or {})})
        try:
            response = self.http.open(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            code, raw = response.code, response.read().decode()
        expected = code in status if isinstance(status, tuple) else (code == status if status else 200 <= code < 300)
        assert expected, f"{method} {path}: HTTP {code} (expected {status}): {raw}"
        self.checks += 1
        return json.loads(raw) if raw else None

    def raw_bot_info(self, bot_uuid: str) -> str:
        with sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True, timeout=30) as db:
            found = db.execute("SELECT bot_info FROM bcs_bots WHERE bot_uuid = ?", (bot_uuid,)).fetchone()
        assert found and found[0], f"{bot_uuid} has no bot_info text"
        return found[0]

    def set_bot_info(self, bot_uuid: str, text: str) -> None:
        with sqlite3.connect(self.db_path, timeout=30) as db:
            db.execute("UPDATE bcs_bots SET bot_info = ? WHERE bot_uuid = ?", (text, bot_uuid))
        self.checks += 1

    def caller_path(self, ref: str) -> str:
        return f"/providers/{segment(self.provider_id)}/bots/{segment(ref)}/caller"

    def caller_headers(self) -> dict:
        return {"Authorization": "Bearer " + self.admin_token}


def wait_healthy(story: Story, process: subprocess.Popen, log_path: Path) -> None:
    for _ in range(200):
        if process.poll() is not None:
            raise RuntimeError("test BCS exited during startup; " + log_path.read_text()[-2000:])
        try:
            story.request("/health")
            return
        except (OSError, urllib.error.URLError, AssertionError):
            time.sleep(0.2)
    raise RuntimeError("test BCS startup timed out")


def stop(process: subprocess.Popen) -> None:
    process.send_signal(signal.SIGTERM)
    process.wait(timeout=30)


def setup_provider(story: Story) -> None:
    story.request("/me/ensure-human", {}, method="POST")
    provider = story.request("/providers", {
        "name": "Caller storage E2E provider",
        "webhook_url": story.base_url + "/unused-webhook",
        "auth": {"mode": "static_bearer"},
        "protocol_version": "2.0",
        "coordination": {"mode": "native_tool"},
        "caller_capabilities": {
            "container_routing": "ensure",
            "supports_caller": True,
            "supports_isolation_binding": True,
            "supports_observe_inject": True,
            "token_visibility": "runtime_only",
        },
    }, method="POST")
    story.provider_id = provider["provider_id"]
    story.admin_token = provider["provider_admin_token"]

    story.bots = {}
    for leg in ("heal", "invalid", "array", "nested", "inner-invalid", "null-caller"):
        ref = f"caller-storage-{leg}-" + uuid.uuid4().hex
        registered = story.request(f"/providers/{segment(story.provider_id)}/bots", {
            "name": f"Caller storage {leg} bot",
            "summary": "Scripted caller storage leg",
            "owners": [story.owner],
            "provider_bot_ref": ref,
            "domains": ["caller"],
            "skills": ["dispatch"],
            "scopes": ["local"],
        }, method="POST", headers=story.caller_headers())
        story.bots[leg] = {"ref": ref, "bot_uuid": registered["bot_uuid"]}


def corrupt_rows(story: Story) -> dict[str, str]:
    """Double-encode once for the heal leg; break every other shape harder."""
    healthy = story.raw_bot_info(story.bots["heal"]["bot_uuid"])
    wrapped = json.dumps(healthy)
    shapes = {
        "heal": wrapped,
        "invalid": "not-json",
        "array": "[1, 2]",
        "nested": json.dumps(wrapped),
        "inner-invalid": json.dumps("{oops"),
        "null-caller": json.dumps(json.dumps({"summary": "x", "caller": None})),
    }
    for leg, text in shapes.items():
        story.set_bot_info(story.bots[leg]["bot_uuid"], text)
    # Every corrupted row must read back exactly what we wrote.
    for leg, text in shapes.items():
        assert story.raw_bot_info(story.bots[leg]["bot_uuid"]) == text, f"{leg} seed mismatch"
    return shapes


def heal_leg(story: Story) -> None:
    path = story.caller_path(story.bots["heal"]["ref"])
    headers = story.caller_headers()
    # Projected read over the wrapped row: `$.caller` is a missing path inside
    # a string root, so the bot still reads "never configured", not an error.
    fresh = story.request(path, headers=headers)
    assert fresh["snapshot"] is None, fresh
    snapshot = {"source_revision": 1, "caller_identity": {"enabled": True}}
    applied = story.request(path, {"snapshot": snapshot}, method="PATCH", headers=headers)
    assert applied["sync_status"] == "applied", applied
    written = json.loads(story.raw_bot_info(story.bots["heal"]["bot_uuid"]))
    assert isinstance(written, dict), "healed bot_info must be a plain object"
    assert written["caller"]["source_revision"] == 1, written
    assert written["summary"] == "Scripted caller storage leg", written
    readback = story.request(path, headers=headers)
    assert readback["snapshot"]["source_revision"] == 1, readback
    print("  double-encoded bot_info: unwrapped once and healed by the sync write", flush=True)


def fail_closed_legs(story: Story, shapes: dict[str, str]) -> None:
    snapshot = {"source_revision": 1, "caller_identity": {"enabled": True}}
    for leg, text in shapes.items():
        if leg == "heal":
            continue
        failed = story.request(story.caller_path(story.bots[leg]["ref"]),
                               {"snapshot": snapshot}, method="PATCH",
                               headers=story.caller_headers(), status=500)
        assert failed["error"] == STORAGE_ERROR, failed
        assert story.raw_bot_info(story.bots[leg]["bot_uuid"]) == text, \
            f"{leg}: refused sync must leave the row byte-identical"
    print("  deeper/non-object encodings: frozen storage errors, rows untouched", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    with tempfile.TemporaryDirectory(prefix="bcs-caller-config-storage-") as directory:
        root = Path(directory)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        config_file = Path(__file__).resolve().parents[2] / "configs/bcs-config-local.toml"
        config = tomllib.loads(config_file.read_text())
        config.update({"bind": "127.0.0.1", "port": port, "bcs_endpoint": base_url,
                       "strict_container_validation": False,
                       "auth": {"chain": ["local"], "allow_mock_headers": True},
                       "database": {"type": "sqlite", "sqlite": {"path": str(root / "bcs.db")}},
                       "llm": {"type": "none"}, "bcsfuse": {"enabled": False},
                       "security": {"outbound_url": {"block_private_networks": True, "allow_loopback": True}}})
        config["manifest"]["bundles"] = []
        config["secret"] = {"provider": "env", "providers": {"env": {"prefix": "BCS_SECRET_"}}}
        config["bots_base_dir"] = str(root / "bots")
        config["session_files"]["backend"] = {"data_dir": str(root / "files")}
        config["collaboration"]["templates"]["base_dir"] = str(root / "templates")
        config_path = root / "bcs-config-local.json"
        config_path.write_text(json.dumps(config))
        env = dict(os.environ, SERVER_ENV="local",
                   BCS_SECRET_BCN_GROUP_SESSION_WS_JWT=uuid.uuid4().hex,
                   AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE=uuid.uuid4().hex)
        story = Story(base_url, "callerstorage" + uuid.uuid4().hex, root / "bcs.db")
        log_path = root / "server.log"
        # Retain LLVM_PROFILE_FILE so the isolated BCS contributes to the
        # singlebox E2E profile pool. No Rust unit-test binary is executed.
        with log_path.open("w") as log:
            process = subprocess.Popen([binary, "-c", str(config_path)], cwd=root,
                                        env=env, stdout=log, stderr=log)
            try:
                wait_healthy(story, process, log_path)
                setup_provider(story)
                shapes = corrupt_rows(story)
                heal_leg(story)
                fail_closed_legs(story, shapes)
                stop(process)
            except BaseException:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                print(f"caller config storage story failed\n{log_path.read_text()[-2000:]}", flush=True)
                raise
        server_log = log_path.read_text()
        assert "double-encoded" in server_log, "the compat layer must warn when unwrapping"
    print(f"Caller config storage story: {story.checks} HTTP/SQL checks passed", flush=True)


if __name__ == "__main__":
    main()
