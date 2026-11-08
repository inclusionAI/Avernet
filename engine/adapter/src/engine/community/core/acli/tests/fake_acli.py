"""A fake ``acli`` that replays the real client's JSON and records every call.

Each call is logged with its argv, the ``HOME`` it ran under and its pid, and
installs are remembered in a state file, so tests see what reached the
process and what it left behind.
"""
from __future__ import annotations

import os

FAKE_ACLI_SCRIPT = r'''#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_ACLI_LOG"], "a") as log:
    log.write(json.dumps({{"args": args, "home": os.environ.get("HOME"), "pid": os.getpid()}}) + "\n")
mode = os.environ.get("FAKE_ACLI_MODE", "ok")
_STATE = os.environ["FAKE_ACLI_LOG"] + ".installed"
def _installed():
    if os.path.exists(_STATE):
        return [e for e in open(_STATE).read().split(",") if e]
    return [e for e in os.environ.get("FAKE_ACLI_INSTALLED", "").split(",") if e]
def _save(entries):
    open(_STATE, "w").write(",".join(entries))
if mode == "sleep":
    import time; time.sleep(5)
if mode == "spawn":
    import subprocess, time
    child = subprocess.Popen(["sleep", "30"])
    with open(os.environ["FAKE_ACLI_LOG"] + ".child", "w") as f:
        f.write(str(child.pid))
    time.sleep(30)
if mode == "badjson":
    print("{{not: json"); sys.exit(0)
if mode == "garbage":
    print("not json at all"); sys.exit(0)
if mode == "crash":
    sys.stderr.write("segfault token=abc123\n"); sys.exit(2)
if mode == "fail":
    print(json.dumps({{"success": False, "error": "no such cli password=hunter2"}})); sys.exit(1)
_DELAYED = os.environ["FAKE_ACLI_LOG"] + ".delayed"
def _delay(before):
    # FAKE_ACLI_DELAY: the first change sleeps that long, before or (default)
    # after it lands, so a test can cancel either side of the change.
    seconds = float(os.environ.get("FAKE_ACLI_DELAY", "0"))
    if not seconds or os.path.exists(_DELAYED):
        return
    if before != (os.environ.get("FAKE_ACLI_DELAY_FIRST") == "1"):
        return
    open(_DELAYED, "w").close()
    import time; time.sleep(seconds)
if args[0] == "install":
    if mode == "slow_install":
        import time; time.sleep(1)
    _delay(True)
    _save([e for e in _installed() if e.partition(":")[0] != args[1]] + [args[1] + ":di"])
    _delay(False)
    print()
    print(json.dumps({{"success": True, "name": args[1], "command": "di", "version": "1.0.9",
                       "cli": {{"name": args[1], "command": "di", "description": "DeepInsight"}}}}))
elif args[:2] == ["tool", "list"]:
    if mode == "list_no_tools":
        print(json.dumps({{"success": True}})); sys.exit(0)
    if mode == "list_entry":
        print(json.dumps({{"success": True, "tools": [json.loads(os.environ["FAKE_ACLI_ENTRY"])]}})); sys.exit(0)
    if mode == "relist_fail" and os.path.exists(_STATE):
        print(json.dumps({{"success": False, "error": "registry busy"}})); sys.exit(1)
    if mode == "list_null_tools":
        print(json.dumps({{"success": True, "tools": None}})); sys.exit(0)
    tools = []
    for entry in _installed():
        name, _, command = entry.partition(":")
        tools.append({{"name": name, "command": command}} if command else {{"name": name}})
    print(json.dumps({{"success": True, "tools": tools}}))
elif args[:2] == ["tool", "uninstall"]:
    if mode == "uninstall_fail":
        print(json.dumps({{"success": False, "error": "busy"}})); sys.exit(1)
    _delay(True)
    if mode != "uninstall_noop":
        _save([e for e in _installed() if e.partition(":")[0] != args[2]])
    _delay(False)
    print(json.dumps({{}}))
'''


def alive(pid: int) -> bool:
    """Running, not merely a zombie awaiting a reaper that may never come."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as stat_file:
            return stat_file.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False
