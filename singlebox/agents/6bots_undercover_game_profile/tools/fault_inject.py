#!/usr/bin/env python3
"""Fault injection for the 谁是卧底 profile (issue #2610).

Plays the human seat through the same BCS HTTP calls as the side panel, waits for a
speech or vote Run, kills one player Bot whose Node has not been dispatched yet, and
measures whether the referee re-opens the phase without any human message.

* Recovered: a new Run appears in the Session within ``--sla`` seconds of the failure.
* Stuck: no new Run appears within ``--stuck-after`` seconds.

Game setup only: when no Run exists ``--start-nudge`` seconds after 开始 (the referee dealt
but did not open round 1), the human says 开始 again, at most twice per game. Recovery from a
failed Run never gets a human message.

The killed OpenClaw gateway is restarted with the singlebox start command, its original
environment and working directory as soon as the Run fails, like a process supervisor would.
Run state is read from the local BCS SQLite database in read-only mode.

Local singlebox only: unauthenticated BCS requests act as the mock human ``human_001``.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import signal
import sqlite3
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HUMAN = "human_001"
PROFILE = Path(__file__).resolve().parents[1]
REPO = PROFILE.parents[2]
DEFAULT_DB = REPO / "singlebox/.dependencies/standalone/bcs_data/bcs.db"
SPEECHES = ["日常生活里挺常见的一样东西", "很多人家里都能找到它", "用的时候不用想太多", "不同场合都能见到"]


def log(message: str) -> None:
    print(time.strftime("%H:%M:%S"), message, flush=True)


class Bcs:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + urllib.parse.quote(path, safe="/?=&:"), data=data,
                                         method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode() or "null"
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"{method} {path} -> HTTP {error.code}: {error.read().decode()[:300]}") from None
        try:
            return json.loads(raw)
        except ValueError:
            return raw

    def new_game(self, referee: str, players: list[str], label: str) -> str:
        group = self.call("POST", "/groups", {
            "label": label, "context": "谁是卧底 · 6人局（5 Bot + 我）· 卧底1名 · 词语难度中等",
            "driver_bot": referee, "group_strategy": "manager_worker", "create_initial_session": True,
            "participants": [{"bot_uuid": referee, "role": "manager"}] + [{"bot_uuid": p, "role": "worker"} for p in players],
        })
        group_id = group.get("group_id") or group.get("id")
        sessions = self.call("GET", f"/groups/{group_id}/sessions")
        items = sessions.get("items", sessions) if isinstance(sessions, dict) else sessions
        session_id = items[0].get("session_id") or items[0].get("id")
        self.call("PATCH", f"/sessions/{session_id}/members/{HUMAN}", {"mode": "present"})
        return session_id

    def say(self, session_id: str, text: str) -> None:
        self.call("POST", f"/sessions/{session_id}/chat", {"message": text})

    def session_status(self, session_id: str) -> str:
        session = self.call("GET", f"/sessions/{session_id}")
        session = session.get("session", session) if isinstance(session, dict) else {}
        return str(session.get("status", "")).lower()

    def pending_human_nodes(self, run_id: str) -> list[dict]:
        nodes = self.call("GET", f"/state-machine-runs/{run_id}/pending-human-nodes")
        return nodes if isinstance(nodes, list) else nodes.get("items", [])

    def respond(self, run_id: str, node_id: str, content: str) -> None:
        try:
            self.call("POST", f"/state-machine-runs/{run_id}/nodes/{node_id}/respond", {"content": content})
        except RuntimeError as error:
            # The response is stored before the next Node is dispatched; a dispatch
            # failure after it is reported back here but does not undo the answer.
            log(f"respond {node_id}: {error}")


class Runs:
    """Read-only view of the BCS SQLite database."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def query(self, sql: str, params: tuple) -> list[tuple]:
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=10) as db:
            return db.execute(sql, params).fetchall()

    def runs(self, session_id: str) -> list[dict]:
        rows = self.query("SELECT run_id, status, created_at_ms, completed_at_ms FROM bcs_state_machine_runs "
                          "WHERE session_id = ? ORDER BY created_at_ms", (session_id,))
        return [dict(zip(("run_id", "status", "created_at_ms", "completed_at_ms"), row)) for row in rows]

    def nodes(self, run_id: str) -> list[dict]:
        rows = self.query("SELECT node_id, status, assignee_bot_id FROM bcs_state_machine_node_runs WHERE run_id = ?",
                          (run_id,))
        return [dict(zip(("node_id", "status", "assignee"), row)) for row in rows]

    def run_result(self, run_id: str) -> str | None:
        rows = self.query("SELECT status FROM bcs_collaboration_delivery_checkpoints WHERE operation_key = ?",
                          (f"smrun:{run_id}:run-result",))
        return rows[0][0] if rows else None


@dataclass
class Gateway:
    """A killed OpenClaw gateway, restartable the way singlebox started it."""
    bot: str
    profile: str
    port: int
    environ: dict[str, str]
    cwd: str
    log_path: Path
    restarted_at: float | None = None
    online_after: float | None = None

    @classmethod
    def kill(cls, bot: str, profile: str, port: int, log_dir: Path) -> "Gateway":
        pid = listening_pid(port)
        if pid is None:
            raise RuntimeError(f"no gateway listening on port {port} for {bot}")
        proc = Path(f"/proc/{pid}")
        # OpenClaw rewrites its process title, so only the environment and working
        # directory are taken from the live process; the command is singlebox's.
        environ = dict(item.split("=", 1) for item in (proc / "environ").read_bytes().decode().split("\0") if "=" in item)
        gateway = cls(bot, profile, port, environ, os.readlink(proc / "cwd"), log_dir / f"restart-{profile}.log")
        os.kill(pid, signal.SIGKILL)
        return gateway

    def restart(self) -> None:
        if self.restarted_at is not None:
            return
        command = ["openclaw", "--profile", self.profile, "gateway", "run", "--port", str(self.port)]
        with open(self.log_path, "ab") as out:
            subprocess.Popen(command, env=self.environ, cwd=self.cwd, stdin=subprocess.DEVNULL,
                             stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        self.restarted_at = time.time()

    def check_online(self) -> bool:
        if self.restarted_at is not None and self.online_after is None and listening_pid(self.port):
            self.online_after = time.time() - self.restarted_at
            log(f"{self.bot} is listening again after {self.online_after:.1f}s")
        return self.online_after is not None


def listening_pid(port: int) -> int | None:
    output = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True).stdout
    match = re.search(r"pid=(\d+)", output)
    return int(match.group(1)) if match else None


@dataclass
class Injection:
    number: int
    session_id: str
    run_id: str
    stage: str
    victim: str
    killed_at: float
    gateway: Gateway
    failed_at_ms: int | None = None
    reopened_run: str | None = None
    reopened_at_ms: int | None = None
    run_result: str | None = None
    outcome: str = "pending"
    notes: list[str] = field(default_factory=list)


def stage_of(nodes: list[dict]) -> str:
    ids = {node["node_id"] for node in nodes}
    return "vote" if "tally" in ids else "speak" if "collect" in ids else "other"


def pick_victim(nodes: list[dict], players: set[str]) -> str | None:
    """A player Bot whose Node is still pending, so its dispatch fails immediately."""
    if stage_of(nodes) == "vote":
        starter = next((n for n in nodes if n["node_id"] == "vote_start"), None)
        if not starter or starter["status"] != "running":
            return None  # vote Nodes are already dispatched
        excluded = {starter["assignee"]}
    else:
        excluded = {n["assignee"] for n in nodes if n["status"] in ("running", "completed")}
    candidates = sorted({n["assignee"] for n in nodes if n["status"] == "pending" and n["assignee"] in players} - excluded)
    return random.choice(candidates) if candidates else None


def play_human(bcs: Bcs, run_id: str, answered: set[str]) -> None:
    for node in bcs.pending_human_nodes(run_id):
        node_id = node.get("node_id", "")
        key = f"{run_id}/{node_id}"
        if key in answered:
            continue
        instruction = node.get("instruction", "")
        if node_id.startswith("vote"):
            seats = re.findall(r"(\d+)号", (re.search(r"可以投的人：(.+?)，不能投自己", instruction) or [None, ""])[1])
            content = f"我投{random.choice(seats)}号" if seats else "我弃权"
        else:
            content = random.choice(SPEECHES)
        bcs.respond(run_id, node_id, content)
        answered.add(key)
        log(f"human answered {node_id} in {run_id[:11]}: {content}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bcs", default="http://127.0.0.1:21000")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--injections", type=int, default=10)
    parser.add_argument("--sla", type=float, default=60.0, help="seconds from Run failure to the re-opened Run")
    parser.add_argument("--stuck-after", type=float, default=300.0)
    parser.add_argument("--start-nudge", type=float, default=120.0,
                        help="seconds without any Run after 开始 before the human repeats it (setup only)")
    parser.add_argument("--stages", default="speak,vote", help="comma-separated stages to inject into")
    parser.add_argument("--out", type=Path, default=Path("fault-injection.csv"))
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    random.seed(args.seed)

    profile = json.loads((PROFILE / "bots.json").read_text(encoding="utf-8"))
    names = [bot["name"] for bot in profile["bots"]]
    ports = {name: profile["port_start"] + index * profile["port_step"] for index, name in enumerate(names)}
    profiles = {bot["name"]: bot["profile"] for bot in profile["bots"]}
    referee, players = names[0], names[1:]
    stages = set(args.stages.split(","))
    bcs, runs = Bcs(args.bcs), Runs(args.db)
    log_dir = args.out.resolve().parent
    results: list[Injection] = []
    answered: set[str] = set()
    session_id, games, started = None, 0, time.time()
    current: Injection | None = None
    injected_runs: set[str] = set()
    start_said, start_nudges, nudges_total = 0.0, 0, 0

    while len([r for r in results if r.outcome != "pending"]) < args.injections:
        if session_id is None or (current is None and bcs.session_status(session_id) not in ("running", "")):
            games += 1
            session_id = bcs.new_game(referee, players, f"故障注入-{games}")
            log(f"game {games}: session {session_id}")
            time.sleep(20)  # the referee answers the Group context with its opening first
            bcs.say(session_id, "开始")
            start_said, start_nudges = time.time(), 0
        session_runs = runs.runs(session_id)
        if not session_runs and start_nudges < 2 and time.time() - start_said > args.start_nudge:
            bcs.say(session_id, "开始")
            start_said, start_nudges, nudges_total = time.time(), start_nudges + 1, nudges_total + 1
            log(f"game {games}: no Run {args.start_nudge:.0f}s after 开始; the human says 开始 again (setup only)")
        active = next((run for run in reversed(session_runs) if run["status"] == "running"), None)
        if active:
            play_human(bcs, active["run_id"], answered)

        # Inject only into a stage's first Run: a re-opened Run already spent one of the
        # two re-opens per stage, so a second fault there would test RETRY_LIMIT instead.
        reopened = len(session_runs) > 1 and session_runs[-2]["status"] == "failed"
        if current is None and active and active["run_id"] not in injected_runs and not reopened:
            nodes = runs.nodes(active["run_id"])
            stage = stage_of(nodes)
            victim = pick_victim(nodes, set(players)) if stage in stages else None
            if victim:
                gateway = Gateway.kill(victim, profiles[victim], ports[victim], log_dir)
                injected_runs.add(active["run_id"])
                current = Injection(len(results) + 1, session_id, active["run_id"], stage, victim, time.time(), gateway)
                results.append(current)
                log(f"#{current.number} killed {victim} (port {ports[victim]}) during {stage} run {active['run_id']}")

        if current:
            current.gateway.check_online()
            run = next(run for run in session_runs if run["run_id"] == current.run_id)
            if current.failed_at_ms is None and run["status"] in ("failed", "aborted", "completed"):
                current.failed_at_ms = run["completed_at_ms"]
                current.gateway.restart()
                if run["status"] != "failed":
                    current.notes.append(f"run ended {run['status']} before the killed Bot was reached")
                log(f"#{current.number} run {run['status']}; restarted {current.victim}")
            if current.failed_at_ms is not None:
                later = [r for r in session_runs if r["created_at_ms"] > current.failed_at_ms]
                waited = time.time() - current.failed_at_ms / 1000
                if later:
                    current.reopened_run = later[0]["run_id"]
                    current.reopened_at_ms = later[0]["created_at_ms"]
                    latency = (current.reopened_at_ms - current.failed_at_ms) / 1000
                    current.outcome = "recovered" if latency <= args.sla else "late"
                elif waited > args.stuck_after:
                    current.outcome = "stuck"
                if current.outcome != "pending":
                    current.run_result = runs.run_result(current.run_id)
                    log(f"#{current.number} {current.outcome}: new run {current.reopened_run} "
                        f"RunResult={current.run_result}")
                    current = None
            elif time.time() - current.killed_at > args.stuck_after:
                current.gateway.restart()
                current.outcome = "not_failed"
                current.notes.append("Run never failed after the kill")
                current = None
        time.sleep(2)

    with open(args.out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["n", "session", "run_id", "stage", "victim", "failed_at_ms", "reopened_run",
                         "latency_s", "outcome", "run_result", "notes"])
        for r in results:
            latency = "" if r.reopened_at_ms is None else f"{(r.reopened_at_ms - r.failed_at_ms) / 1000:.1f}"
            writer.writerow([r.number, r.session_id, r.run_id, r.stage, r.victim, r.failed_at_ms, r.reopened_run,
                             latency, r.outcome, r.run_result, "; ".join(r.notes)])
    latencies = [(r.reopened_at_ms - r.failed_at_ms) / 1000 for r in results if r.reopened_at_ms]
    summary = {
        "injections": len(results), "games": games, "recovered_within_sla": sum(r.outcome == "recovered" for r in results),
        "stuck": sum(r.outcome == "stuck" for r in results), "late": sum(r.outcome == "late" for r in results),
        "median_latency_s": round(statistics.median(latencies), 1) if latencies else None,
        "max_latency_s": round(max(latencies), 1) if latencies else None,
        "start_nudges": nudges_total, "elapsed_min": round((time.time() - started) / 60, 1), "csv": str(args.out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
