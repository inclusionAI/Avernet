"""``AcliRunner`` against a fake ``acli`` that replays the real client's JSON.

The fake records every argv and the ``HOME`` it ran under, so the tests pin
what reaches the process — the part a shell-free design is meant to guarantee
— not just what comes back.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest
from engine.community.core.acli.runner import (
    AcliError,
    AcliRunner,
    acli_home_for,
    mask_secrets,
    validate_install_dir,
    validate_name,
)

_FAKE = r'''#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_ACLI_LOG"], "a") as log:
    log.write(json.dumps({{"args": args, "home": os.environ.get("HOME"), "pid": os.getpid()}}) + "\n")
mode = os.environ.get("FAKE_ACLI_MODE", "ok")
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
if args[0] == "install":
    print()
    print(json.dumps({{"success": True, "name": args[1], "command": "di", "version": "1.0.9",
                       "cli": {{"name": args[1], "command": "di", "description": "DeepInsight"}}}}))
elif args[:2] == ["tool", "list"]:
    if mode == "list_no_tools":
        print(json.dumps({{"success": True}})); sys.exit(0)
    if mode == "list_entry":
        print(json.dumps({{"success": True, "tools": [json.loads(os.environ["FAKE_ACLI_ENTRY"])]}})); sys.exit(0)
    if mode == "list_null_tools":
        print(json.dumps({{"success": True, "tools": None}})); sys.exit(0)
    names = os.environ.get("FAKE_ACLI_INSTALLED", "").split(",")
    print(json.dumps({{"success": True, "tools": [{{"name": n}} for n in names if n]}}))
elif args[:2] == ["tool", "uninstall"]:
    if mode == "uninstall_fail":
        print(json.dumps({{"success": False, "error": "busy"}})); sys.exit(1)
    print(json.dumps({{"success": True, "name": args[2]}}))
'''


def _alive(pid: int) -> bool:
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


@pytest.fixture
def fake_acli(tmp_path, install_dir, monkeypatch):
    script = tmp_path / "acli"
    script.write_text(_FAKE.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_ACLI_LOG", str(log))

    def calls() -> list[dict]:
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    return AcliRunner(environ=os.environ, install_dir=install_dir, executable=str(script)), calls


@pytest.fixture
def install_dir(tmp_path) -> str:
    return str(tmp_path / "agentclaw" / "bin")


async def test_install_runs_fixed_argv_under_persisted_home(fake_acli, install_dir):
    runner, calls = fake_acli

    tool = await runner.install("deepinsight-cli")

    assert (tool.name, tool.command, tool.description, tool.version) == (
        "deepinsight-cli", "di", "DeepInsight", "1.0.9",
    )
    [call] = calls()
    assert call["args"] == [
        "install", "deepinsight-cli", "--install-dir", install_dir, "--yes", "--json",
    ]
    home = acli_home_for(Path(install_dir))
    assert call["home"] == str(home)
    assert home.is_dir()


async def test_install_failure_carries_masked_acli_error(fake_acli, install_dir, monkeypatch):
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "fail")

    with pytest.raises(AcliError) as caught:
        await runner.install("nope")

    assert caught.value.code == "install_failed"
    assert "no such cli" in caught.value.message
    assert "hunter2" not in caught.value.message


async def test_crash_without_json_reports_stderr_masked(fake_acli, install_dir, monkeypatch):
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "crash")

    with pytest.raises(AcliError) as caught:
        await runner.install("dataphin")

    assert caught.value.code == "install_failed"
    assert "abc123" not in caught.value.message
    assert "segfault" in caught.value.message


async def test_success_exit_without_json_is_bad_output(fake_acli, install_dir, monkeypatch):
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "garbage")

    with pytest.raises(AcliError) as caught:
        await runner.install("dataphin")

    assert caught.value.code == "bad_output"


async def test_unparseable_json_is_bad_output(fake_acli, install_dir, monkeypatch):
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "badjson")

    with pytest.raises(AcliError) as caught:
        await runner.install("dataphin")

    assert caught.value.code == "bad_output"


async def test_install_without_name_or_command_is_bad_output(tmp_path, install_dir, monkeypatch):
    script = tmp_path / "acli"
    script.write_text(f"#!{sys.executable}\nprint('{{\"success\": true}}')\n")
    script.chmod(0o755)

    with pytest.raises(AcliError) as caught:
        await AcliRunner(
            environ=os.environ, install_dir=install_dir, executable=str(script)
        ).install("dataphin")

    assert caught.value.code == "bad_output"


async def test_timeout_kills_and_reports(fake_acli, install_dir, monkeypatch):
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "sleep")
    monkeypatch.setattr("engine.community.core.acli.runner.INSTALL_TIMEOUT_SECONDS", 0.2)

    with pytest.raises(AcliError) as caught:
        await runner.install("dataphin")

    assert caught.value.code == "timeout"


async def test_timeout_kills_children_holding_the_pipes(
    fake_acli, install_dir, tmp_path, monkeypatch
):
    """A grandchild that inherits acli's stdout must not keep the request waiting."""
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "spawn")
    monkeypatch.setattr("engine.community.core.acli.runner.QUERY_TIMEOUT_SECONDS", 1.0)

    started = time.monotonic()
    with pytest.raises(AcliError) as caught:
        await runner.uninstall("x")

    assert caught.value.code == "timeout"
    assert time.monotonic() - started < 10
    child_pid = int((tmp_path / "calls.jsonl.child").read_text())
    await asyncio.sleep(0.2)
    assert not _alive(child_pid)


async def test_cancelled_request_kills_acli(fake_acli, install_dir, monkeypatch):
    runner, calls = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "sleep")

    task = asyncio.create_task(runner.uninstall("x"))
    for _ in range(100):
        if calls():
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not _alive(calls()[0]["pid"])


async def test_unwritable_install_dir_is_a_refusal_not_a_crash(fake_acli, tmp_path):
    good, calls = fake_acli
    blocker = tmp_path / "file"
    blocker.write_text("")
    runner = AcliRunner(
        environ=os.environ, install_dir=str(blocker / "x" / "bin"), executable=good._executable
    )

    with pytest.raises(AcliError) as caught:
        await runner.install("dataphin")
    assert caught.value.code == "install_failed"
    with pytest.raises(AcliError) as caught:
        await runner.uninstall("dataphin")
    assert caught.value.code == "uninstall_failed"
    assert calls() == []


async def test_missing_acli_is_unavailable(tmp_path, install_dir):
    runner = AcliRunner(
        environ=os.environ, install_dir=install_dir, executable=str(tmp_path / "no-such-acli")
    )

    with pytest.raises(AcliError) as caught:
        await runner.install("dataphin")

    assert caught.value.code == "acli_unavailable"


async def test_uninstall_removes_a_tool_acli_installed(fake_acli, install_dir, monkeypatch):
    runner, calls = fake_acli
    monkeypatch.setenv("FAKE_ACLI_INSTALLED", "yuntu-cli,deepinsight-cli")

    result = await runner.uninstall("deepinsight-cli")

    assert result.uninstalled is True
    list_call, uninstall_call = calls()
    assert list_call["args"] == ["tool", "list", "--json"]
    assert uninstall_call["args"] == ["tool", "uninstall", "deepinsight-cli", "--yes", "--json"]
    assert {list_call["home"], uninstall_call["home"]} == {str(acli_home_for(Path(install_dir)))}


async def test_uninstall_of_unknown_tool_is_success_without_uninstall_call(
    fake_acli, install_dir, monkeypatch
):
    runner, calls = fake_acli
    monkeypatch.setenv("FAKE_ACLI_INSTALLED", "yuntu-cli")

    result = await runner.uninstall("dataphin")

    assert result.uninstalled is False
    assert [c["args"][:2] for c in calls()] == [["tool", "list"]]


async def test_uninstall_refuses_a_listing_without_tools(fake_acli, install_dir, monkeypatch):
    """A malformed listing must not read as "not installed" and skip the uninstall."""
    runner, calls = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "list_no_tools")

    with pytest.raises(AcliError) as caught:
        await runner.uninstall("dataphin")

    assert caught.value.code == "bad_output"
    assert [c["args"][:2] for c in calls()] == [["tool", "list"]]


@pytest.mark.parametrize("entry", ['{}', '{"name": ""}', '"dataphin"', '{"name": 3}'])
async def test_uninstall_refuses_a_listing_entry_without_a_name(
    fake_acli, install_dir, monkeypatch, entry
):
    """A nameless entry must not read as some other tool and skip the uninstall."""
    runner, calls = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "list_entry")
    monkeypatch.setenv("FAKE_ACLI_ENTRY", entry)

    with pytest.raises(AcliError) as caught:
        await runner.uninstall("dataphin")

    assert caught.value.code == "bad_output"
    assert [c["args"][:2] for c in calls()] == [["tool", "list"]]


async def test_uninstall_reads_a_null_listing_as_empty(fake_acli, install_dir, monkeypatch):
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "list_null_tools")

    result = await runner.uninstall("dataphin")

    assert result.uninstalled is False


async def test_uninstall_failure_raises(fake_acli, install_dir, monkeypatch):
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_INSTALLED", "dataphin")
    monkeypatch.setenv("FAKE_ACLI_MODE", "uninstall_fail")

    with pytest.raises(AcliError) as caught:
        await runner.uninstall("dataphin")

    assert caught.value.code == "uninstall_failed"
    assert caught.value.message == "busy"


@pytest.mark.parametrize(
    "name", ["", "-rf", "../x", "a b", "x;rm", "a" * 300, None, "di@1.0.9", "@scope", "a/b", "@antcli/yuntu-cli"]
)
def test_bad_names_are_refused(name):
    with pytest.raises(AcliError) as caught:
        validate_name(name)
    assert caught.value.code == "invalid_input"


@pytest.mark.parametrize("name", ["dataphin", "deepinsight-cli", "yuntu-cli", "a_b.c"])
def test_market_names_are_accepted(name):
    assert validate_name(name) == name


@pytest.mark.parametrize(
    "path", ["relative/bin", "/home/admin/../etc/bin", "/home/admin/.agentclaw/lib", None]
)
def test_bad_install_dirs_are_refused(path):
    with pytest.raises(AcliError):
        validate_install_dir(path)


@pytest.mark.parametrize("path", ["relative/bin", "/home/admin/.agentclaw/lib"])
def test_a_misconfigured_install_dir_fails_at_start(path):
    """The install directory is deployment config, so a bad one stops the runner being built."""
    with pytest.raises(ValueError):
        AcliRunner(environ=os.environ, install_dir=path)


async def test_bad_input_starts_no_process(fake_acli, install_dir):
    runner, calls = fake_acli

    with pytest.raises(AcliError):
        await runner.install("-x")
    with pytest.raises(AcliError):
        await runner.uninstall("../x")

    assert calls() == []


def test_mask_secrets_hides_values_keeps_keys():
    text = "Authorization: Bearer abc.def token=xyz password: 'p4ss' ok=1"
    masked = mask_secrets(text)
    assert "abc.def" not in masked and "xyz" not in masked and "p4ss" not in masked
    assert "ok=1" in masked and "token=" in masked


def test_runner_leaves_process_home_untouched(fake_acli, install_dir):
    before = os.environ.get("HOME")
    validate_install_dir(install_dir)
    assert os.environ.get("HOME") == before


class _StuckProcess:
    pid = 999_999_999

    async def wait(self) -> int:
        await asyncio.sleep(60)
        return 0


async def test_kill_group_tolerates_gone_and_unreapable_processes(monkeypatch):
    """An already-gone group, and a process that never reaps, must not hang or raise."""
    def gone(pid, sig):
        raise ProcessLookupError(pid)

    monkeypatch.setattr("engine.community.core.acli.runner.os.killpg", gone)
    monkeypatch.setattr("engine.community.core.acli.runner._REAP_TIMEOUT_SECONDS", 0.1)

    await AcliRunner._kill_group(_StuckProcess())
