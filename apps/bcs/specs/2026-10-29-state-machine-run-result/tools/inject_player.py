#!/usr/bin/env python3
"""Inject a real failure into one active DSH worker and record observable times.

The session/run must already be running. The process is selected only after
matching its configured Bot credential, a live executing node, process command,
DSH_HOME, and start time. A killed process is restarted with its captured local
environment; credentials are never written to the evidence file.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import re
import subprocess
import time
import urllib.error
import urllib.request

import yaml


def now():
    return int(time.time() * 1000)


def get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def credentials(home):
    value = yaml.safe_load((home / ".credentials.yaml").read_text())["refs"]["BCN_BOT_SESSION"]
    return json.loads(value)["botUuid"]


def process(pid, home):
    root = Path("/proc") / str(pid)
    stat = (root / "stat").read_text().split()
    argv = (root / "cmdline").read_bytes().decode().split("\0")[:-1]
    env = dict(item.split("=", 1) for item in (root / "environ").read_bytes().decode().split("\0") if "=" in item)
    cwd = str((root / "cwd").resolve())
    if not argv or "dsh" not in Path(argv[1] if Path(argv[0]).name == "node" and len(argv) > 1 else argv[0]).name \
            or "web" not in argv or env.get("DSH_HOME") != str(home):
        raise ValueError("PID does not match the selected DSH home and web process")
    return stat[21], argv, env, cwd


def save(path, evidence):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:21000")
    parser.add_argument("--session", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--worker", action="append", required=True, metavar="PID=DSH_HOME")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--observation-seconds", type=int, default=720)
    parser.add_argument("--sqlite", required=True, type=Path, help="local SQLite database for delivery and manager trace times")
    parser.add_argument("--referee-state-dir", type=Path, help="referee undercover-game directory to verify successor scope")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("evidence output already exists")
    workers = {}
    for item in args.worker:
        pid_text, home_text = item.split("=", 1)
        pid, home = int(pid_text), Path(home_text).expanduser().resolve()
        if pid <= 1 or home in [entry[1] for entry in workers.values()]:
            raise ValueError("duplicate or invalid worker")
        identity = credentials(home)
        if identity in workers:
            raise ValueError("duplicate Bot identity")
        process(pid, home)
        workers[identity] = (pid, home)
    url = args.base_url.rstrip("/")
    run = get(f"{url}/state-machine-runs/{args.run}")
    if run["run"]["session_id"] != args.session or run["run"]["status"] != "running":
        raise ValueError("Run is not active in the requested session")
    candidates = [n for n in run["nodes"] if n["status"] == "running" and n.get("assignee_bot_id") in workers]
    if not candidates:
        raise ValueError("no selected player is executing a node")
    node = candidates[0]
    bot = node["assignee_bot_id"]
    if bot == run["run"].get("created_by"):
        raise ValueError("refusing to kill the Run creator")
    session = get(f"{url}/sessions/{args.session}")
    if session.get("group_id") != run["run"].get("group_id") or not any(
        p.get("bot_uuid") == bot and p.get("actor_kind") == "bot" and p.get("role") == "worker"
        for p in session.get("participants", [])
    ):
        raise ValueError("selected Bot is not a worker in the Run session")
    original_round = run["run"].get("input", {}).get("round")
    manager = run["run"].get("created_by")
    kind = "speak" if node["node_id"].startswith("speak_") else "vote" if node["node_id"].startswith("vote_") else None
    if kind is None or not manager or not isinstance(original_round, int):
        raise ValueError("cannot uniquely identify the game phase")
    expected_scope = None
    if args.referee_state_dir:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", args.session)
        state = json.loads((args.referee_state_dir / (safe + ".json")).read_text())
        current = state.get("recovery", {}).get("current", {})
        if current.get("run_id") != args.run or current.get("kind") != kind or current.get("state") != "submitted":
            raise ValueError("Run is not the referee's current submitted stage")
        expected_scope = current.get("scope")
    pid, home = workers[bot]
    start, argv, env, cwd = process(pid, home)
    latest = get(f"{url}/state-machine-runs/{args.run}")
    if latest["run"]["status"] != "running" or not any(n["node_id"] == node["node_id"] and n["status"] == "running" and n["attempt"] == node["attempt"] and n.get("assignee_bot_id") == bot for n in latest["nodes"]):
        raise ValueError("worker node changed before injection")
    if process(pid, home)[0] != start:
        raise ValueError("PID was reused before injection")
    with sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True) as db:
        active = db.execute("SELECT COUNT(*) FROM bcs_state_machine_node_runs n JOIN bcs_state_machine_runs r ON r.run_id=n.run_id AND r.env=n.env WHERE n.assignee_bot_id=? AND n.status='running' AND r.status='running'", (bot,)).fetchone()[0]
    if active != 1:
        raise ValueError("selected worker is active in another Run")
    pidfd = os.pidfd_open(pid)
    if process(pid, home)[0] != start:
        os.close(pidfd)
        raise ValueError("PID changed during verification")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    evidence = {"session_id": args.session, "run_id": args.run, "node_id": node["node_id"],
                "node_attempt": node["attempt"], "worker_id": bot, "pid": pid,
                "T0_kill_ms": None, "T1_terminal_ms": None, "T2_send_started_ms": None,
                "T3_manager_started_ms": None, "T4_new_run_running_ms": None,
                "worker_restart_started_ms": None, "worker_restart_pid": None,
                "worker_restart_web_ready_ms": None, "worker_restart_online_ms": None, "passed_60s": False}
    save(args.output, evidence)
    evidence["T0_kill_ms"] = now()
    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
    os.close(pidfd)
    save(args.output, evidence)
    # Let the killed server release its listening socket before restarting.
    time.sleep(0.5)
    log_path = args.output.with_suffix(".worker-restart.log")
    with log_path.open("ab", buffering=0) as log:
        evidence["worker_restart_started_ms"] = now()
        child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, start_new_session=True)
        evidence["worker_restart_pid"] = child.pid
    save(args.output, evidence)
    end = time.monotonic() + args.observation_seconds
    while time.monotonic() < end:
        try:
            if evidence["worker_restart_web_ready_ms"] is None and child.poll() is None and "--port" in argv:
                port = int(argv[argv.index("--port") + 1])
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
                    evidence["worker_restart_web_ready_ms"] = now()
                except urllib.error.HTTPError:
                    evidence["worker_restart_web_ready_ms"] = now()
                except (OSError, urllib.error.URLError):
                    pass
            if evidence["worker_restart_web_ready_ms"] is not None and evidence["worker_restart_online_ms"] is None \
                    and get(f"{url}/bots/{bot}").get("status") == "online":
                evidence["worker_restart_online_ms"] = now()
            view = get(f"{url}/state-machine-runs/{args.run}")
            if view["run"]["status"] in ("failed", "aborted"):
                evidence["T1_terminal_ms"] = view["run"].get("completed_at") or now()
            if args.sqlite and args.sqlite.is_file():
                with sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True) as db:
                    row = db.execute("SELECT send_started_at_ms, status FROM bcs_message_deliveries WHERE source_message_id = ? AND target_bot_id = ? AND kind = 'send'", ("run-result:" + args.run, manager)).fetchone()
                    if row:
                        evidence["T2_send_started_ms"], evidence["delivery_status"] = row
                    row = db.execute("SELECT MIN(created_at) FROM bcs_messages WHERE session_id = ? AND sender_id = ? AND message_type = 'tool_call' AND created_at >= ? AND content LIKE ?", (args.session, manager, evidence["T0_kill_ms"], "%" + args.run + "%")).fetchone()
                    if row and row[0] is not None:
                        evidence["T3_manager_started_ms"] = row[0]
            if args.referee_state_dir and evidence["T1_terminal_ms"] is not None:
                safe = re.sub(r"[^A-Za-z0-9_.-]", "_", args.session)
                state_path = args.referee_state_dir / (safe + ".json")
                state = json.loads(state_path.read_text())
                current = state.get("recovery", {}).get("current", {})
                successor = current.get("run_id")
                if current.get("state") == "submitted" and current.get("kind") == kind \
                        and current.get("scope", "").startswith(str(original_round) + ":") and (not expected_scope or current.get("scope") == expected_scope) and successor and successor != args.run:
                    new = get(f"{url}/state-machine-runs/{successor}")
                    if new["run"]["session_id"] == args.session and new["run"]["status"] == "running":
                        evidence["T4_new_run_running_ms"] = now()  # conservative observation time
                        evidence["successor_run_id"] = successor
            save(args.output, evidence)
            if all(evidence[k] is not None for k in ("T1_terminal_ms", "T2_send_started_ms", "T3_manager_started_ms", "T4_new_run_running_ms", "worker_restart_online_ms")):
                break
        except (OSError, urllib.error.URLError, ValueError, sqlite3.Error):
            pass
        time.sleep(1)
    evidence["observed_until_ms"] = now()
    evidence["passed_60s"] = all(evidence[k] is not None for k in ("T1_terminal_ms", "T2_send_started_ms", "T3_manager_started_ms", "T4_new_run_running_ms")) \
        and evidence["T4_new_run_running_ms"] - evidence["T0_kill_ms"] <= 60_000
    save(args.output, evidence)
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == "__main__":
    main()
