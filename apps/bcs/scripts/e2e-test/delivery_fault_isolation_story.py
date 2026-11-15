#!/usr/bin/env python3
"""A real BCS scheduler keeps moving when individual queue rows are faulty.

Reproduces the production meltdown class against an isolated BCS process: an
undecodable delivery row (as left behind by a bad write or an old schema) sits
in front of healthy rows in the queued, expiry and run-deadline scans, and the
process is restarted with it still present. Healthy rows must settle, the bad
rows must be left untouched (isolated, not rewritten) and BCS must stay up.

Fault rows are injected with SQL because no API can produce them; healthy rows
use the same SQL shape so their progress is attributable to the scheduler only.
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

ENV = "local"
TERMINAL = {"completed", "failed", "cancelled", "expired", "rejected_capacity"}
COLUMNS = ("delivery_id,env,source_message_id,target_bot_id,session_id,group_id,source_session_seq,"
           "flow_kind,kind,status,state_version,may_have_been_sent,available_at_ms,created_at_ms,"
           "updated_at_ms,attempt_no,semantic_projection_json,expire_at_ms,run_deadline_at_ms")


class Story:
    def __init__(self, base_url: str, db_path: Path, log_path: Path) -> None:
        self.base_url = base_url
        self.db_path = db_path
        self.log_path = log_path
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.owner = "deliveryfault" + uuid.uuid4().hex
        self.prefix = uuid.uuid4().hex[:8]
        self.bot = ""
        self.token = ""
        self.checks = 0

    def request(self, method: str, path: str, body=None, *, status: int = 200):
        headers = {"Content-Type": "application/json", "X-Mock-User-Id": self.owner,
                   "X-Mock-Nick-Name": "Delivery fault operator"}
        if self.token:
            headers["X-BCS-Bot-Token"] = self.token
        request = urllib.request.Request(self.base_url + path, method=method, headers=headers,
                                         data=None if body is None else json.dumps(body).encode())
        try:
            response = self.http.open(request, timeout=20)
        except urllib.error.HTTPError as failure:
            response = failure
        with response:
            code, raw = response.code, response.read()
        assert code == status, f"{method} {path.split('?', 1)[0]}: HTTP {code}, expected {status}"
        self.checks += 1
        return json.loads(raw) if raw else {}

    def register_bot(self) -> None:
        self.request("POST", "/me/ensure-human", {})
        token = self.request("GET", "/register/token")["token"]
        query = urllib.parse.urlencode({"token": token, "bot-name": "Delivery fault target"})
        registered = self.request("POST", "/register?" + query, {})
        # The Bot never opens its WebSocket, so the scheduler sees it offline.
        self.bot, self.token = registered["bot_uuid"], registered["bot_token"]

    def insert(self, name: str, *, bot: str = "", flow_kind: str = "group", status: str = "queued",
               sent: bool = False, expire_at_ms: int | None = None,
               run_deadline_at_ms: int | None = None) -> str:
        delivery_id = f"{self.prefix}-{name}"
        now = int(time.time() * 1000)
        values = (delivery_id, ENV, f"m-{delivery_id}", bot or self.bot, f"s-{delivery_id}", f"g-{self.prefix}",
                  1, flow_kind, "send", status, 1, int(sent), 1, now, now, int(sent), "{}",
                  expire_at_ms, run_deadline_at_ms)
        with sqlite3.connect(self.db_path, timeout=30) as db:
            db.execute(f"INSERT INTO bcs_message_deliveries ({COLUMNS}) VALUES "
                       f"({','.join('?' * len(values))})", values)
        self.checks += 1
        return delivery_id

    def row(self, delivery_id: str) -> tuple[str, str, int]:
        with sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True, timeout=30) as db:
            found = db.execute("SELECT status, flow_kind, state_version FROM bcs_message_deliveries "
                               "WHERE env = ? AND delivery_id = ?", (ENV, delivery_id)).fetchone()
        assert found, f"{delivery_id} disappeared"
        return found

    def wait_until(self, what: str, predicate, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                self.checks += 1
                return
            time.sleep(0.5)
        raise AssertionError(f"Timed out waiting for {what}\n{self.log_tail()}")

    def settles(self, delivery_id: str, statuses=TERMINAL, timeout: float = 30) -> str:
        self.wait_until(f"{delivery_id} to leave its stuck state", lambda: self.row(delivery_id)[0] in statuses, timeout)
        return self.row(delivery_id)[0]

    def untouched(self, delivery_id: str, status: str) -> None:
        assert self.row(delivery_id) == (status, "not-a-flow-kind", 1), \
            f"{delivery_id} was rewritten instead of isolated: {self.row(delivery_id)}"
        self.checks += 1

    def log_tail(self) -> str:
        return self.log_path.read_text(errors="replace")[-4000:] if self.log_path.exists() else ""

    def faults_before_restart(self) -> list[tuple[str, str]]:
        self.register_bot()
        past = int(time.time() * 1000) - 60_000
        # Overdue rows go to another Bot so their in-flight rows do not use
        # up the offline Bot's running capacity.
        other = f"bot_{self.prefix}_other"
        # Queued lane heads: the schedule snapshot lists the corrupt head, the
        # row read fails, and only that row may be set aside.
        bad_head = self.insert("bad-head", flow_kind="not-a-flow-kind")
        good_head = self.insert("good-head")
        # Expiry scan: a corrupt overdue row must not hide healthy expired work.
        bad_expired = self.insert("bad-expired", bot=other, flow_kind="not-a-flow-kind", expire_at_ms=past)
        good_expired = self.insert("good-expired", bot=other, expire_at_ms=past)
        # Control scan: a corrupt overdue maybe-sent row next to a healthy one.
        bad_running = self.insert("bad-running", bot=other, flow_kind="not-a-flow-kind", status="dispatching",
                                  sent=True, run_deadline_at_ms=past)
        good_running = self.insert("good-running", bot=other, status="running", sent=True,
                                   run_deadline_at_ms=past)

        assert self.settles(good_expired) == "expired", "healthy expired row did not expire"
        status = self.settles(good_running, {"unknown", "cancelling", "cancel_unknown"} | TERMINAL)
        print(f"  ✓ overdue running row converged to {status} beside a corrupt row", flush=True)
        # Offline lane heads are failed after the 10 s startup recovery grace.
        assert self.settles(good_head, timeout=45) == "failed", "offline healthy head was not settled"
        print("  ✓ healthy queued, expired and overdue rows settled beside corrupt rows", flush=True)
        self.request("GET", "/health", status=200)
        faults = [(bad_head, "queued"), (bad_expired, "queued"), (bad_running, "dispatching")]
        for delivery_id, status in faults:
            self.untouched(delivery_id, status)
        print("  ✓ corrupt rows were isolated, not rewritten, and BCS stayed up", flush=True)
        return faults

    def after_restart(self, faults: list[tuple[str, str]]) -> None:
        # Startup recovery scans every unfinished row, including the corrupt ones.
        self.request("GET", "/health", status=200)
        past = int(time.time() * 1000) - 60_000
        fresh = self.insert("fresh-expired", bot=f"bot_{self.prefix}_other", expire_at_ms=past)
        assert self.settles(fresh) == "expired", "scheduler did not resume after restart"
        for delivery_id, status in faults:
            self.untouched(delivery_id, status)
        print("  ✓ restart recovery skipped corrupt rows and the queue resumed", flush=True)


def wait_healthy(story: Story, process: subprocess.Popen) -> None:
    for _ in range(150):
        if process.poll() is not None:
            raise RuntimeError("BCS exited during delivery fault story startup\n" + story.log_tail())
        try:
            story.request("GET", "/health")
            return
        except (OSError, urllib.error.URLError, AssertionError):
            time.sleep(0.2)
    raise RuntimeError("BCS startup timed out\n" + story.log_tail())


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        raise RuntimeError(f"BCS exited early with status {process.returncode}")
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise RuntimeError("BCS did not exit gracefully; coverage may be incomplete")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    with tempfile.TemporaryDirectory(prefix="bcs-delivery-fault-") as directory:
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
                       "llm": {"type": "none"}, "bcsfuse": {"enabled": False}})
        config["manifest"]["bundles"] = []
        config["secret"] = {"provider": "env", "providers": {"env": {"prefix": "BCS_SECRET_"}}}
        config["bots_base_dir"] = str(root / "bots")
        config["session_files"]["backend"] = {"data_dir": str(root / "files")}
        config["collaboration"]["templates"]["base_dir"] = str(root / "templates")
        config_path = root / "bcs-config-local.json"
        config_path.write_text(json.dumps(config))
        env = dict(os.environ, SERVER_ENV="local", BCS_SECRET_BCN_GROUP_SESSION_WS_JWT=uuid.uuid4().hex,
                   AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE=uuid.uuid4().hex)
        story = Story(base_url, root / "bcs.db", root / "server.log")
        # Retain LLVM_PROFILE_FILE so both real processes contribute to the
        # Singlebox E2E profile pool. Do not execute any Rust unit-test binary.
        with story.log_path.open("w") as log:
            faults = None
            for phase in ("before restart", "after restart"):
                process = subprocess.Popen([binary, "-c", str(config_path)], cwd=root,
                                           env=env, stdout=log, stderr=log)
                try:
                    wait_healthy(story, process)
                    if faults is None:
                        faults = story.faults_before_restart()
                    else:
                        story.after_restart(faults)
                    stop(process)
                except BaseException:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    print(f"delivery fault story failed {phase}\n{story.log_tail()}", flush=True)
                    raise
    print(f"Delivery fault isolation story: {story.checks} HTTP/SQL checks passed", flush=True)


if __name__ == "__main__":
    main()
