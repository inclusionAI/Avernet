"""Exercise actual subprocess, auth callback and DWS argv through the connection port."""

import asyncio
import json
import os
import sys
from contextlib import aclosing
from pathlib import Path

import pytest

from engine.community.kernel.dws_connection import DwsError
from engine.community.plugin_api.dws_connection import DwsConnection
from engine.community.plugins.dws_hosting.client import DwsCliConnection
from engine.community.plugins.dws_hosting.process import DwsProcess

from .test_connection_wire import IDENTITY, START


def process(tmp_path, mode="ok", **options):
    return DwsProcess(
        (sys.executable, str(Path(__file__).with_name("dws_cli_fixture.py")), mode),
        tmp_path / mode,
        {"HOME": str(tmp_path), "DWS_ACCESS_TOKEN": "never-forward"},
        shutdown_seconds=0.05,
        **options,
    )


def connection(tmp_path, mode="ok", **options) -> DwsConnection:
    return DwsCliConnection(
        process(tmp_path, mode),
        IDENTITY,
        authorization_hosts=frozenset({"auth.example.invalid"}),
        history_timezone="Asia/Shanghai",
        **options,
    )


async def test_auth_listen_history_connection_contract(tmp_path):
    client = connection(tmp_path)
    challenges = []

    async def challenge(value):
        challenges.append(value)

    async with client.exclusive():
        assert (await client.authorize(challenge)).authenticated
        assert challenges[0].user_code == "TEST-CODE"
        async with aclosing(client.listen()) as stream:
            notices = [await anext(stream), await anext(stream)]
            assert {n.kind for n in notices} == {"ready", "conversation"}
            assert (await client.health()).connected
            assert await client.discover(START, START + 60_000) == ("chat",)
            assert (
                await client.history("chat", START, START + 60_000)
            ).observed_ids == ("m1",)
        assert client._consumer_pid == 0
    argv = [
        json.loads(line)
        for line in (tmp_path / "ok" / "argv.jsonl").read_text().splitlines()
    ]
    login = next(args for args in argv if args[:2] == ["auth", "login"])
    assert "--no-browser" in login and "--device" in login
    for args in argv:
        assert "stop" not in args and "send" not in args
        if args[0] in ("event", "chat"):
            assert args[-2:] == ["--profile", "org:self"]
    history = next(args for args in argv if args[:2] == ["chat", "+chat-messages"])
    assert "--page-all" in history and "--no-reactions" in history
    assert "--mark-read" not in history


async def test_exclusive_owner_and_private_directory(tmp_path):
    first, second = connection(tmp_path), connection(tmp_path)
    async with first.exclusive():
        with pytest.raises(DwsError, match="connection_busy"):
            async with second.exclusive():
                pytest.fail("must not acquire twice")
    async with second.exclusive():
        pass
    assert (tmp_path / "ok" / "hosting.lock").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.invalid/?user_code=x",
        "https://auth.example.invalid:444/?user_code=x",
        "https://user@auth.example.invalid/?user_code=x",
        "https://auth.example.invalid/?user_code=a&user_code=b",
        "https://auth.example.invalid:bad/?user_code=x",
        "https://[bad/?user_code=x",
    ],
)
def test_authorization_url_is_validated(tmp_path, url):
    with pytest.raises(DwsError, match="invalid_authorization_url"):
        connection(tmp_path)._challenge(url)


def test_unrelated_stderr_not_a_challenge(tmp_path):
    client = connection(tmp_path)
    assert client._challenge("Please open a browser") is None
    assert client._challenge("https://auth.example.invalid/login") is None


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("wrong-account", "identity_mismatch"),
        ("auth-fail", "authorization_failed"),
        ("logged-out", "authorization_required"),
        ("fail", "authorization_failed"),
        ("auth-hang", "authorization_timeout"),
    ],
)
async def test_failed_authorization_never_returns_identity(tmp_path, mode, expected):
    async def challenge(_):
        pass

    client = connection(tmp_path, mode, authorization_seconds=0.25)
    with pytest.raises(DwsError, match=expected):
        await client.authorize(challenge)


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("fail", "command_failed"),
        ("invalid", "invalid_output"),
        ("large", "invalid_output"),
        ("output", "output_limit"),
        ("hang", "command_timeout"),
    ],
)
async def test_subprocess_limits_and_redacted_errors(tmp_path, mode, expected):
    cli = process(tmp_path, mode, timeout=0.25, line_limit=1024, output_limit=512)
    with pytest.raises(DwsError, match=expected) as exc:
        await cli.capture(())
    assert "private diagnostic" not in str(exc.value)
    pid = int((cli.config_dir / "pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_cancel_process_reaps_it_and_does_not_leak_pumps(tmp_path):
    cli = process(tmp_path, "hang")
    task = asyncio.create_task(cli.capture(()))
    async with asyncio.timeout(3):
        while not (cli.config_dir / "pid").exists():
            await asyncio.sleep(0.01)
    pid = int((cli.config_dir / "pid").read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_environment_does_not_forward_ambient_tokens(tmp_path):
    cli = process(tmp_path, "env")
    result = json.loads(await cli.capture(()))
    assert "DWS_ACCESS_TOKEN" not in result
    assert result["DWS_CONFIG_DIR"] == str(cli.config_dir)


@pytest.mark.parametrize(
    "mode,expected", [("wrong-event", "unexpected_event"), ("ended", "listener_ended")]
)
async def test_listener_errors_do_not_silently_finish(tmp_path, mode, expected):
    client = connection(tmp_path, mode)
    with pytest.raises(DwsError, match=expected):
        async with aclosing(client.listen()) as stream:
            async for _ in stream:
                pass
    assert client._consumer_pid == 0


async def test_missing_executable_redacted(tmp_path):
    cli = DwsProcess((str(tmp_path / "absent"),), tmp_path / "config", {})
    with pytest.raises(DwsError, match="command_failed"):
        await cli.capture(())


@pytest.mark.parametrize("use_uvloop", [False, True])
def test_private_subprocess_on_server_event_loop(tmp_path, use_uvloop):
    loop_factory = (
        pytest.importorskip("uvloop").new_event_loop
        if use_uvloop
        else asyncio.new_event_loop
    )
    config_dir = tmp_path / "config"
    literal = "literal;$(not-a-shell-command)"
    code = (
        "import json,os,sys; "
        "open('private-file','w').close(); os.mkdir('private-dir'); "
        "print(json.dumps({'argv':sys.argv[1:], 'pid':os.getpid()}))"
    )
    cli = DwsProcess((sys.executable, "-c", code), config_dir, {})

    async def check():
        async with cli.open((literal,)) as child:
            stdout, _ = await child.communicate()
            assert child.returncode == 0
            assert json.loads(stdout) == {"argv": [literal], "pid": child.pid}

    previous = os.umask(0o022)
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(check())
        assert config_dir.joinpath("private-file").stat().st_mode & 0o777 == 0o600
        assert config_dir.joinpath("private-dir").stat().st_mode & 0o777 == 0o700
        assert os.umask(0o022) == 0o022
    finally:
        os.umask(previous)


def test_invalid_config_rejected(tmp_path):
    with pytest.raises(ValueError):
        DwsProcess(("dws",), tmp_path / "config", {})
    with pytest.raises(ValueError):
        process(tmp_path, timeout=0)
    with pytest.raises(ValueError):
        connection(tmp_path, listener_seconds=0)
    with pytest.raises(ValueError):
        DwsCliConnection(
            process(tmp_path),
            IDENTITY.model_copy(update={"user_id": "user,other"}),
            authorization_hosts=frozenset({"example.invalid"}),
            history_timezone="UTC",
        )


async def test_cancel_during_spawn_waits_for_child_cleanup(tmp_path, monkeypatch):
    real_spawn = asyncio.create_subprocess_exec
    spawned = asyncio.Event()
    release = asyncio.Event()
    children = []

    async def slow_spawn(*args, **kwargs):
        child = await real_spawn(*args, **kwargs)
        children.append(child)
        spawned.set()
        await release.wait()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_spawn)
    task = asyncio.create_task(process(tmp_path, "hang").capture(()))
    await asyncio.wait_for(spawned.wait(), 3)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children[0].returncode is not None


async def test_offline_cli_to_core_to_durable_batch(tmp_path):
    from engine.community.core.dws_hosting.connection import DwsIntake
    from engine.community.kernel.dws_connection import IntakePolicy
    from engine.community.plugins.dws_hosting.recovery import SqliteHostingRecoveryStore
    from engine.community.plugins.dws_hosting.state import SqliteHostingStateStore

    from .test_state_contract import identity

    state = SqliteHostingStateStore(tmp_path / "state")
    state.bind(identity())
    state.confirm_rule("binding", "rule")
    state.set_enabled("binding", True, now_ms=START)
    client = connection(tmp_path)
    recovery = SqliteHostingRecoveryStore(tmp_path / "state")
    intake = DwsIntake(
        identity(),
        client,
        state,
        recovery,
        clock_ms=lambda: START + 5000,
        policy=IntakePolicy(poll_seconds=0.01),
    )
    task = asyncio.create_task(intake.run())
    try:
        async with asyncio.timeout(5):
            while True:
                if task.done():
                    await task
                    pytest.fail("intake unexpectedly stopped")
                turn = state.claim_ready("binding", now_ms=START + 10_000)
                if turn is not None:
                    break
                await asyncio.sleep(0.01)
        assert turn.messages[0].text == "hello"
        assert turn.messages[0].message_id == "m1"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert state.claim_ready("binding", now_ms=START + 20_000) is None
