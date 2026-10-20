"""``AcliRunner`` against a fake ``acli`` that replays the real client's JSON.

The fake records every argv and the ``HOME`` it ran under, so the tests pin
what reaches the process — the part a shell-free design is meant to guarantee
— not just what comes back.
"""
from __future__ import annotations

import asyncio
import os
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
from engine.community.core.acli.tests.fake_acli import alive as _alive

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
    monkeypatch.setattr("engine.community.core.acli.runner.CHANGE_TIMEOUT_SECONDS", 0.2)

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
        await runner.find_installed("x", failure_code="uninstall_failed")

    assert caught.value.code == "timeout"
    assert time.monotonic() - started < 10
    child_pid = int((tmp_path / "calls.jsonl.child").read_text())
    await asyncio.sleep(0.2)
    assert not _alive(child_pid)


async def test_cancelled_request_kills_acli(fake_acli, install_dir, monkeypatch):
    runner, calls = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "sleep")

    task = asyncio.create_task(runner.find_installed("x", failure_code="uninstall_failed"))
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
        await runner.find_installed("dataphin", failure_code="uninstall_failed")
    assert caught.value.code == "uninstall_failed"
    assert calls() == []


async def test_missing_acli_is_unavailable(tmp_path, install_dir):
    runner = AcliRunner(
        environ=os.environ, install_dir=install_dir, executable=str(tmp_path / "no-such-acli")
    )

    with pytest.raises(AcliError) as caught:
        await runner.install("dataphin")

    assert caught.value.code == "acli_unavailable"


async def test_find_installed_lists_once_under_persisted_home(fake_acli, install_dir, monkeypatch):
    runner, calls = fake_acli
    monkeypatch.setenv("FAKE_ACLI_INSTALLED", "yuntu-cli,deepinsight-cli:di")

    tool = await runner.find_installed("deepinsight-cli", failure_code="install_failed")

    assert (tool.name, tool.command) == ("deepinsight-cli", "di")
    [call] = calls()
    assert call["args"] == ["tool", "list", "--json"]
    assert call["home"] == str(acli_home_for(Path(install_dir)))


async def test_list_tools_describes_every_installed_tool(fake_acli, install_dir, monkeypatch):
    runner, calls = fake_acli
    monkeypatch.setenv("FAKE_ACLI_INSTALLED", "yuntu-cli,deepinsight-cli:di")

    tools = await runner.list_tools()

    assert [(t.name, t.command) for t in tools] == [("yuntu-cli", ""), ("deepinsight-cli", "di")]
    assert [c["args"] for c in calls()] == [["tool", "list", "--json"]]


async def test_a_failed_listing_is_list_failed(fake_acli, install_dir, monkeypatch):
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "fail")

    with pytest.raises(AcliError) as caught:
        await runner.list_tools()

    assert caught.value.code == "list_failed"


async def test_find_installed_of_unknown_tool_is_none(fake_acli, install_dir, monkeypatch):
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_INSTALLED", "yuntu-cli")

    assert await runner.find_installed("dataphin", failure_code="install_failed") is None


async def test_find_installed_by_alias_returns_the_market_name(fake_acli, install_dir, monkeypatch):
    """Cleaning up an install requested as ``di`` must find ``deepinsight-cli``."""
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_INSTALLED", "deepinsight-cli:di,dataphin")

    tool = await runner.find_installed("di", failure_code="uninstall_failed")

    assert tool.name == "deepinsight-cli"


async def test_a_listing_without_tools_is_bad_output(fake_acli, install_dir, monkeypatch):
    """A malformed listing must not read as "not installed" and skip the uninstall."""
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "list_no_tools")

    with pytest.raises(AcliError) as caught:
        await runner.find_installed("dataphin", failure_code="uninstall_failed")

    assert caught.value.code == "bad_output"


@pytest.mark.parametrize("entry", ['{}', '{"name": ""}', '"dataphin"', '{"name": 3}'])
async def test_a_listing_entry_without_a_name_is_bad_output(
    fake_acli, install_dir, monkeypatch, entry
):
    """A nameless entry must not read as some other tool and skip the uninstall."""
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "list_entry")
    monkeypatch.setenv("FAKE_ACLI_ENTRY", entry)

    with pytest.raises(AcliError) as caught:
        await runner.find_installed("dataphin", failure_code="uninstall_failed")

    assert caught.value.code == "bad_output"


async def test_a_null_listing_is_empty(fake_acli, install_dir, monkeypatch):
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "list_null_tools")

    assert await runner.find_installed("dataphin", failure_code="uninstall_failed") is None


async def test_a_failed_listing_carries_the_callers_failure_code(fake_acli, install_dir, monkeypatch):
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_MODE", "fail")

    with pytest.raises(AcliError) as caught:
        await runner.find_installed("dataphin", failure_code="install_failed")

    assert caught.value.code == "install_failed"


async def test_remove_runs_one_uninstall_and_trusts_its_exit(fake_acli, install_dir, monkeypatch):
    runner, calls = fake_acli
    monkeypatch.setenv("FAKE_ACLI_INSTALLED", "dataphin")

    await runner.remove("dataphin")

    assert [c["args"] for c in calls()] == [["tool", "uninstall", "dataphin", "--yes", "--json"]]


async def test_remove_failure_raises(fake_acli, install_dir, monkeypatch):
    runner, _ = fake_acli
    monkeypatch.setenv("FAKE_ACLI_INSTALLED", "dataphin")
    monkeypatch.setenv("FAKE_ACLI_MODE", "uninstall_fail")

    with pytest.raises(AcliError) as caught:
        await runner.remove("dataphin")

    assert (caught.value.code, caught.value.message) == ("uninstall_failed", "busy")


async def test_on_spawn_receives_each_acli_pid(fake_acli, install_dir):
    runner, calls = fake_acli
    pids: list[int] = []

    await runner.install("dataphin", on_spawn=pids.append)

    assert pids == [calls()[0]["pid"]]


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
        await runner.remove("../x")
    with pytest.raises(AcliError):
        await runner.find_installed("-x", failure_code="uninstall_failed")

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
