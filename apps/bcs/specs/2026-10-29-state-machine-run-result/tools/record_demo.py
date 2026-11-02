#!/usr/bin/env python3
"""Wait for an eligible worker in a Chat session, inject once, and show timestamps.

This intentionally kills and restarts one selected DSH worker. It never selects
or prompts the manager. Raw evidence is local and can contain Bot and run IDs.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.request

from inject_player import credentials, process


def get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def running_workers(homes):
    workers = {}
    for home in homes:
        bot = credentials(home)
        matches = []
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                process(int(proc.name), home)
                matches.append(int(proc.name))
            except (OSError, ValueError, IndexError, UnicodeError):
                pass
        if len(matches) != 1 or bot in workers:
            raise ValueError('Each worker home must have exactly one DSH web process and a unique Bot identity')
        workers[bot] = (matches[0], home)
    return workers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True)
    parser.add_argument('--worker-home', action='append', required=True, type=Path)
    parser.add_argument('--sqlite', required=True, type=Path)
    parser.add_argument('--referee-state-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--base-url', default='http://127.0.0.1:21000')
    parser.add_argument('--wait-seconds', type=int, default=600)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('evidence output already exists')
    homes = [p.expanduser().resolve() for p in args.worker_home]
    if len(set(homes)) != len(homes):
        raise ValueError('duplicate worker home')
    workers = running_workers(homes)
    print('Waiting for an eligible player node. This command will kill and restart that player.', flush=True)
    deadline = time.monotonic() + args.wait_seconds
    while time.monotonic() < deadline:
        with sqlite3.connect(f'file:{args.sqlite}?mode=ro', uri=True) as db:
            rows = db.execute("SELECT run_id FROM bcs_state_machine_runs WHERE session_id = ? AND status = 'running' AND record_status = 'active'", (args.session,)).fetchall()
        if len(rows) == 1:
            run_id = rows[0][0]
            view = get(f'{args.base_url.rstrip("/")}/state-machine-runs/{run_id}')
            eligible = [n for n in view['nodes'] if n['status'] == 'running' and n.get('assignee_bot_id') in workers
                        and (n['node_id'].startswith('speak_') or n['node_id'].startswith('vote_'))]
            if eligible:
                print(f"Selected run {run_id}, node {eligible[0]['node_id']}; validating player and injecting now.", flush=True)
                cmd = [sys.executable, str(Path(__file__).with_name('inject_player.py')), '--base-url', args.base_url,
                       '--session', args.session, '--run', run_id, '--sqlite', str(args.sqlite),
                       '--referee-state-dir', str(args.referee_state_dir), '--output', str(args.output)]
                for pid, home in workers.values():
                    cmd.extend(['--worker', f'{pid}={home}'])
                child = subprocess.Popen(cmd, stdout=subprocess.DEVNULL)
                seen = set()
                while child.poll() is None:
                    try:
                        evidence = json.loads(args.output.read_text())
                        for key in ('T0_kill_ms', 'T1_terminal_ms', 'T2_send_started_ms', 'T3_manager_started_ms', 'T4_new_run_running_ms'):
                            if evidence.get(key) is not None and key not in seen:
                                print(f'{key}: {evidence[key]}', flush=True)
                                seen.add(key)
                    except (OSError, ValueError):
                        pass
                    time.sleep(.2)
                if child.returncode:
                    raise RuntimeError('Injection was not completed; inspect the error above and evidence file if created')
                evidence = json.loads(args.output.read_text())
                for key in ('T0_kill_ms', 'T1_terminal_ms', 'T2_send_started_ms', 'T3_manager_started_ms', 'T4_new_run_running_ms'):
                    if evidence.get(key) is not None and key not in seen:
                        print(f'{key}: {evidence[key]}', flush=True)
                if evidence.get('T4_new_run_running_ms') is None:
                    raise RuntimeError('No running successor observed within the observation window')
                print(f"Kill to running successor: {evidence['T4_new_run_running_ms'] - evidence['T0_kill_ms']} ms; passed_60s={evidence['passed_60s']}", flush=True)
                return
        time.sleep(.5)
    raise TimeoutError('No eligible player node observed before timeout')


if __name__ == '__main__':
    main()
