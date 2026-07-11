from __future__ import annotations

import io
import json
import os
import re
import select
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


CONNECTOR_DIR = Path(__file__).resolve().parents[1]
INSTALLER = (
    CONNECTOR_DIR.parents[1]
    / "docs"
    / "install-instructions"
    / "install-hermes.sh"
)
INSTALL_DOC = INSTALLER.with_suffix(".md")
sys.path.insert(0, str(CONNECTOR_DIR))

import hermes_bcn as cli  # noqa: E402


class FakeProcess:
    def __init__(self, pid: int = 43210, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


class FakeAsyncProcess:
    def __init__(self, pid: int = 54321) -> None:
        self.pid = pid
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.hermes_home = Path(self.tempdir.name) / "profile"

    def test_profile_and_explicit_home_resolution(self) -> None:
        with mock.patch.object(cli.Path, "home", return_value=Path("/home/tester")):
            self.assertEqual(
                Path("/home/tester/.hermes/profiles/reviewer"),
                cli.resolve_hermes_home(profile="reviewer"),
            )
        self.assertEqual(
            self.hermes_home.absolute(),
            cli.resolve_hermes_home(hermes_home=self.hermes_home),
        )

    def test_register_validates_then_persists_protected_credentials(self) -> None:
        response = {"bot_uuid": "bot-123", "bot_token": "bot-secret"}
        with mock.patch.object(cli, "_post_registration", return_value=response) as post:
            session = cli.register_bot(
                human_token="human-secret",
                bot_name="Hermes Bot",
                bcs_endpoint="http://127.0.0.1:21000",
                bcs_url="ws://127.0.0.1:21000/ws/bot",
                hermes_home=self.hermes_home,
            )

        path = self.hermes_home / "bcn" / "session.json"
        self.assertEqual(session, json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(0o600, path.stat().st_mode & 0o777)
        self.assertEqual("bot-123", session["bot_uuid"])
        self.assertEqual("bot-secret", session["bot_token"])
        self.assertTrue(session["dashboard_token"])
        post.assert_called_once_with(
            "http://127.0.0.1:21000", "human-secret", "Hermes Bot"
        )

    def test_register_rejects_malformed_response_without_writing(self) -> None:
        with mock.patch.object(
            cli, "_post_registration", return_value={"bot_uuid": "bot-123"}
        ):
            with self.assertRaisesRegex(ValueError, "bot_token"):
                cli.register_bot(
                    human_token="human-secret",
                    bot_name="Hermes Bot",
                    bcs_endpoint="http://127.0.0.1:21000",
                    bcs_url="ws://127.0.0.1:21000/ws/bot",
                    hermes_home=self.hermes_home,
                )
        self.assertFalse((self.hermes_home / "bcn" / "session.json").exists())

    def test_replace_registration_failure_preserves_running_session(self) -> None:
        self._write_session()
        session_path = cli.connector_paths(self.hermes_home).session
        original = session_path.read_bytes()
        with (
            mock.patch.object(
                cli, "_post_registration", side_effect=RuntimeError("registration failed")
            ),
            mock.patch.object(cli.os, "kill") as kill,
        ):
            with self.assertRaisesRegex(RuntimeError, "registration failed"):
                cli.register_bot(
                    human_token="human-secret",
                    bot_name="Hermes Bot",
                    bcs_endpoint="http://127.0.0.1:21000",
                    bcs_url="ws://127.0.0.1:21000/ws/bot",
                    hermes_home=self.hermes_home,
                    replace=True,
                )

        kill.assert_not_called()
        self.assertEqual(original, session_path.read_bytes())

    def test_replace_stops_running_connector_before_saving_new_session(self) -> None:
        self._write_session()
        paths = cli.connector_paths(self.hermes_home)
        cli.AtomicJsonStore(paths.pid).save({"pid": 1234})
        store = cli.AtomicJsonStore(paths.session)
        persist = store.save
        order: list[str] = []

        def stop(pid: int, signum: int) -> None:
            self.assertEqual((1234, cli.signal.SIGTERM), (pid, signum))
            order.append(f"stop:{store.load()['bot_uuid']}")
            stale = store.load()
            stale["bot_token"] = "late-old-token"
            persist(stale)

        original_save = cli.AtomicJsonStore.save

        def save(target, value) -> None:
            if target.path == paths.session:
                order.append(f"save:{value['bot_uuid']}")
            original_save(target, value)

        with (
            mock.patch.object(
                cli,
                "_post_registration",
                return_value={"bot_uuid": "bot-new", "bot_token": "token-new"},
            ),
            mock.patch.object(cli, "_connector_process_matches", return_value=True),
            mock.patch.object(cli, "_wait_for_process_exit", return_value=True),
            mock.patch.object(cli.os, "kill", side_effect=stop) as kill,
            mock.patch.object(cli.AtomicJsonStore, "save", new=save),
        ):
            session = cli.register_bot(
                human_token="human-secret",
                bot_name="Hermes Bot",
                bcs_endpoint="http://127.0.0.1:21000",
                bcs_url="ws://127.0.0.1:21000/ws/bot",
                hermes_home=self.hermes_home,
                replace=True,
            )

        kill.assert_called_once_with(1234, cli.signal.SIGTERM)
        self.assertEqual(["stop:bot-123", "save:bot-new"], order)
        self.assertEqual("bot-new", session["bot_uuid"])
        self.assertEqual("token-new", store.load()["bot_token"])

    def test_register_cli_reads_human_token_from_stdin_not_argv(self) -> None:
        argv = [
            "register",
            "--human-token-stdin",
            "--bot-name",
            "Hermes Bot",
            "--hermes-home",
            str(self.hermes_home),
        ]
        with (
            mock.patch.object(cli.sys, "stdin", io.StringIO("human-secret\n")),
            mock.patch.object(
                cli,
                "register_bot",
                return_value={"bot_uuid": "bot-123"},
            ) as register,
        ):
            self.assertEqual(0, cli.main(argv))

        self.assertNotIn("human-secret", argv)
        self.assertEqual("human-secret", register.call_args.kwargs["human_token"])

    def test_dashboard_settings_are_reused_or_replaced_when_port_is_busy(self) -> None:
        session = {
            "dashboard_port": 24567,
            "dashboard_token": "dashboard-secret",
        }
        with mock.patch.object(cli, "_loopback_port_available", return_value=True):
            self.assertEqual(
                (24567, "dashboard-secret"),
                cli.ensure_dashboard_settings(session, self.hermes_home),
            )

        with (
            mock.patch.object(cli, "_loopback_port_available", return_value=False),
            mock.patch.object(cli, "_free_loopback_port", return_value=25678),
        ):
            self.assertEqual(
                (25678, "dashboard-secret"),
                cli.ensure_dashboard_settings(session, self.hermes_home),
            )
        saved = json.loads(
            (self.hermes_home / "bcn" / "session.json").read_text(encoding="utf-8")
        )
        self.assertEqual(25678, saved["dashboard_port"])

    def test_start_is_idempotent_for_live_recorded_connector(self) -> None:
        paths = cli.connector_paths(self.hermes_home)
        marker = "Sat Jul 11 21:30:00 2026"
        paths.pid.parent.mkdir(parents=True)
        paths.pid.write_text(
            json.dumps(
                {
                    "pid": 1234,
                    "hermes_home": str(self.hermes_home),
                    "start_marker": marker,
                }
            ),
            encoding="utf-8",
        )
        self._publish_ready_health(self.hermes_home, 1234, marker)
        with (
            mock.patch.object(cli, "_connector_process_matches", return_value=True),
            mock.patch.object(cli.subprocess, "Popen") as popen,
        ):
            self.assertEqual(1234, cli.start_connector(self.hermes_home))
        popen.assert_not_called()

    def test_start_repairs_stale_pid_before_spawning(self) -> None:
        self._write_session()
        paths = cli.connector_paths(self.hermes_home)
        paths.pid.write_text(
            json.dumps({"pid": 999999, "hermes_home": str(self.hermes_home)}),
            encoding="utf-8",
        )
        process = FakeProcess()

        def popen(*_args, **_kwargs):
            self._publish_ready_health(
                self.hermes_home,
                process.pid,
                "Sat Jul 11 21:30:00 2026",
            )
            return process

        with (
            mock.patch.object(
                cli, "_connector_process_matches", side_effect=(False, True)
            ),
            mock.patch.object(
                cli,
                "_wait_for_process_start_marker",
                return_value="Sat Jul 11 21:30:00 2026",
                create=True,
            ),
            mock.patch.object(cli.subprocess, "Popen", side_effect=popen),
            mock.patch.object(cli.time, "sleep"),
        ):
            self.assertEqual(
                process.pid,
                cli.start_connector(self.hermes_home, health_wait=0),
            )
        record = json.loads(paths.pid.read_text(encoding="utf-8"))
        self.assertEqual(process.pid, record["pid"])
        self.assertEqual("Sat Jul 11 21:30:00 2026", record["start_marker"])

    def test_start_requires_matching_ready_health_and_cleans_timeout(self) -> None:
        self._write_session()
        paths = cli.connector_paths(self.hermes_home)
        process = FakeProcess()
        marker = "Sat Jul 11 21:30:00 2026"
        cli.AtomicJsonStore(paths.health).save(
            {
                "pid": process.pid,
                "start_marker": marker,
                "dashboard_ready": True,
                "bcs_ready": False,
                "ready": False,
            }
        )
        with (
            mock.patch.object(cli, "_connector_process_matches", return_value=True),
            mock.patch.object(
                cli, "_wait_for_process_start_marker", return_value=marker
            ),
            mock.patch.object(cli.subprocess, "Popen", return_value=process),
            mock.patch.object(cli, "_wait_for_process_exit", return_value=True),
            mock.patch.object(cli.os, "kill") as kill,
        ):
            with self.assertRaisesRegex(RuntimeError, "ready"):
                cli.start_connector(self.hermes_home, health_wait=0)

        kill.assert_called_once_with(process.pid, cli.signal.SIGTERM)
        self.assertFalse(paths.pid.exists())
        self.assertFalse(paths.health.exists())

    def test_start_accepts_matching_ready_health_record(self) -> None:
        self._write_session()
        paths = cli.connector_paths(self.hermes_home)
        process = FakeProcess()
        marker = "Sat Jul 11 21:30:00 2026"

        def publish_health(_delay) -> None:
            cli.AtomicJsonStore(paths.health).save(
                {
                    "pid": process.pid,
                    "start_marker": marker,
                    "dashboard_ready": True,
                    "bcs_ready": True,
                    "ready": True,
                }
            )

        with (
            mock.patch.object(cli, "_connector_process_matches", return_value=True),
            mock.patch.object(
                cli, "_wait_for_process_start_marker", return_value=marker
            ),
            mock.patch.object(cli.subprocess, "Popen", return_value=process),
            mock.patch.object(cli.time, "sleep", side_effect=publish_health),
        ):
            self.assertEqual(
                process.pid,
                cli.start_connector(self.hermes_home, health_wait=0.2),
            )

        self.assertEqual(0o600, paths.health.stat().st_mode & 0o777)

    def test_process_identity_requires_exact_script_argv_and_start_marker(self) -> None:
        script = str(Path(cli.__file__).resolve())
        home = str(self.hermes_home.resolve())
        record = {
            "pid": 1234,
            "hermes_home": home,
            "script": script,
            "start_marker": "Sat Jul 11 21:30:00 2026",
        }
        exact_argv = (sys.executable, script, "run", "--hermes-home", home)
        with (
            mock.patch.object(
                cli,
                "_process_start_marker",
                return_value=record["start_marker"],
                create=True,
            ),
            mock.patch.object(
                cli, "_process_argv", return_value=exact_argv, create=True
            ),
        ):
            self.assertTrue(cli._connector_process_matches(record))

        with (
            mock.patch.object(
                cli,
                "_process_start_marker",
                return_value="Sat Jul 11 21:31:00 2026",
                create=True,
            ),
            mock.patch.object(
                cli, "_process_argv", return_value=exact_argv, create=True
            ),
        ):
            self.assertFalse(cli._connector_process_matches(record))

        lookalike_argv = (
            sys.executable,
            f"{script}.old",
            "run",
            "--hermes-home",
            home,
        )
        with (
            mock.patch.object(
                cli,
                "_process_start_marker",
                return_value=record["start_marker"],
                create=True,
            ),
            mock.patch.object(
                cli, "_process_argv", return_value=lookalike_argv, create=True
            ),
        ):
            self.assertFalse(cli._connector_process_matches(record))

    def test_process_identity_accepts_recorded_installed_script_for_replace(self) -> None:
        installed_script = "/opt/avernet/hermes_bcn.py"
        home = str(self.hermes_home.resolve())
        marker = "Sat Jul 11 21:30:00 2026"
        record = {
            "pid": 1234,
            "hermes_home": home,
            "script": installed_script,
            "start_marker": marker,
        }
        with (
            mock.patch.object(cli, "_process_start_marker", return_value=marker),
            mock.patch.object(
                cli,
                "_process_argv",
                return_value=(
                    sys.executable,
                    installed_script,
                    "run",
                    "--hermes-home",
                    home,
                ),
            ),
        ):
            self.assertTrue(
                cli._connector_process_matches(
                    record, expected_home=self.hermes_home.resolve()
                )
            )

    def test_status_distinguishes_running_stale_and_stopped(self) -> None:
        paths = cli.connector_paths(self.hermes_home)
        self.assertEqual(("stopped", 1), cli.connector_status(self.hermes_home))
        paths.pid.parent.mkdir(parents=True)
        paths.pid.write_text("not-json", encoding="utf-8")
        self.assertEqual(("stale", 2), cli.connector_status(self.hermes_home))
        paths.pid.write_text(
            json.dumps({"pid": 1234, "hermes_home": str(self.hermes_home)}),
            encoding="utf-8",
        )
        with mock.patch.object(cli, "_connector_process_matches", return_value=False):
            self.assertEqual(("stale", 2), cli.connector_status(self.hermes_home))
        with mock.patch.object(cli, "_connector_process_matches", return_value=True):
            self.assertEqual(("running", 0), cli.connector_status(self.hermes_home))

    def test_stop_never_signals_process_that_does_not_match_record(self) -> None:
        paths = cli.connector_paths(self.hermes_home)
        paths.pid.parent.mkdir(parents=True)
        paths.pid.write_text(
            json.dumps({"pid": 1234, "hermes_home": str(self.hermes_home)}),
            encoding="utf-8",
        )
        with (
            mock.patch.object(cli, "_connector_process_matches", return_value=False),
            mock.patch.object(cli.os, "kill") as kill,
        ):
            self.assertFalse(cli.stop_connector(self.hermes_home))
        kill.assert_not_called()
        self.assertFalse(paths.pid.exists())

    def test_stop_rechecks_identity_immediately_before_sigterm(self) -> None:
        paths = cli.connector_paths(self.hermes_home)
        paths.pid.parent.mkdir(parents=True)
        paths.pid.write_text(
            json.dumps(
                {
                    "pid": 1234,
                    "hermes_home": str(self.hermes_home.resolve()),
                    "script": str(Path(cli.__file__).resolve()),
                    "start_marker": "Sat Jul 11 21:30:00 2026",
                }
            ),
            encoding="utf-8",
        )
        with (
            mock.patch.object(
                cli, "_connector_process_matches", side_effect=(True, False)
            ) as matches,
            mock.patch.object(cli.os, "kill") as kill,
        ):
            self.assertFalse(cli.stop_connector(self.hermes_home))
        self.assertEqual(2, matches.call_count)
        kill.assert_not_called()

    def test_stop_recovers_orphan_when_connector_pid_is_missing_stale_or_mismatched(
        self,
    ) -> None:
        cases = {
            "missing": None,
            "stale": "not-json",
            "mismatched": json.dumps({"pid": 1234}),
        }
        for name, connector_record in cases.items():
            with self.subTest(name=name):
                home = Path(self.tempdir.name) / name
                paths = cli.connector_paths(home)
                paths.dashboard_pid.parent.mkdir(parents=True)
                if connector_record is not None:
                    paths.pid.write_text(connector_record, encoding="utf-8")
                paths.dashboard_pid.write_text(
                    json.dumps(
                        {
                            "pid": 2345,
                            "hermes_home": str(home.resolve()),
                            "port": 24567,
                            "start_marker": "Sat Jul 11 21:30:00 2026",
                            "argv": ["/usr/local/bin/hermes", "dashboard"],
                        }
                    ),
                    encoding="utf-8",
                )
                with (
                    mock.patch.object(
                        cli, "_connector_process_matches", return_value=False
                    ),
                    mock.patch.object(
                        cli,
                        "_dashboard_process_matches",
                        side_effect=(True, True),
                    ),
                    mock.patch.object(
                        cli, "_wait_for_process_exit", return_value=True
                    ),
                    mock.patch.object(cli.os, "kill") as kill,
                ):
                    self.assertTrue(cli.stop_connector(home))
                kill.assert_called_once_with(2345, cli.signal.SIGTERM)
                self.assertFalse(paths.dashboard_pid.exists())

    def test_stop_with_stale_records_never_signals_unrelated_dashboard(self) -> None:
        paths = cli.connector_paths(self.hermes_home)
        paths.pid.parent.mkdir(parents=True)
        paths.pid.write_text(json.dumps({"pid": 1234}), encoding="utf-8")
        paths.dashboard_pid.write_text(
            json.dumps(
                {
                    "pid": 2345,
                    "hermes_home": str(self.hermes_home.resolve()),
                    "port": 24567,
                    "start_marker": "Sat Jul 11 21:30:00 2026",
                    "argv": ["/usr/local/bin/hermes", "dashboard"],
                }
            ),
            encoding="utf-8",
        )
        with (
            mock.patch.object(cli, "_connector_process_matches", return_value=False),
            mock.patch.object(cli, "_dashboard_process_matches", return_value=False),
            mock.patch.object(cli.os, "kill") as kill,
        ):
            self.assertFalse(cli.stop_connector(self.hermes_home))
        kill.assert_not_called()
        self.assertFalse(paths.dashboard_pid.exists())

    def test_concurrent_start_is_locked_and_spawns_once(self) -> None:
        self._write_session()
        second_spawn = threading.Event()
        calls: list[int] = []

        def popen(*_args, **_kwargs):
            calls.append(len(calls) + 1)
            process = FakeProcess(pid=43000 + len(calls))
            self._publish_ready_health(
                self.hermes_home,
                process.pid,
                "Sat Jul 11 21:30:00 2026",
            )
            second_spawn.set()
            return process

        results: list[int] = []

        def start() -> None:
            results.append(cli.start_connector(self.hermes_home, health_wait=0))

        with (
            mock.patch.object(cli, "_connector_process_matches", return_value=True),
            mock.patch.object(
                cli,
                "_wait_for_process_start_marker",
                return_value="Sat Jul 11 21:30:00 2026",
                create=True,
            ),
            mock.patch.object(cli.subprocess, "Popen", side_effect=popen),
        ):
            threads = [threading.Thread(target=start) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(1, len(calls))
        self.assertEqual([43001, 43001], sorted(results))

    def test_direct_run_uses_nonblocking_singleton_lock_without_lifecycle_deadlock(
        self,
    ) -> None:
        self._write_session()
        paths = cli.connector_paths(self.hermes_home)
        with cli._run_ownership_lock(paths):
            with (
                mock.patch.object(cli.asyncio, "run") as async_run,
                mock.patch.object(cli.sys, "stderr", io.StringIO()),
            ):
                self.assertEqual(
                    1,
                    cli.main(["run", "--hermes-home", str(self.hermes_home)]),
                )
            async_run.assert_not_called()

        with cli._lifecycle_lock(paths):
            with cli._run_ownership_lock(paths):
                pass
        self.assertEqual(0o600, paths.run_lock.stat().st_mode & 0o777)

    def test_dashboard_supervisor_resets_backoff_after_ready_restart(self) -> None:
        async def exercise() -> None:
            class ExitedProcess:
                returncode = 1

                async def wait(self) -> int:
                    return 1

            class Dashboard:
                def __init__(self) -> None:
                    self.process = ExitedProcess()

                async def start(self):
                    self.process = ExitedProcess()
                    return self.process

            stop = cli.asyncio.Event()
            delays: list[float] = []
            probes = 0

            async def probe() -> None:
                nonlocal probes
                probes += 1
                if probes == 1:
                    raise ConnectionError("not ready")

            async def skip_delay(awaitable, timeout):
                delays.append(timeout)
                awaitable.close()
                if len(delays) == 3:
                    stop.set()
                    return True
                raise cli.asyncio.TimeoutError

            with mock.patch.object(cli.asyncio, "wait_for", new=skip_delay):
                await cli._supervise_dashboard(
                    Dashboard(),
                    stop,
                    readiness_probe=probe,
                    reconnect_delays=(1, 2, 4),
                )

            self.assertEqual([1, 2, 1], delays)

        cli.asyncio.run(exercise())

    def test_owned_dashboard_persists_identity_and_cleans_record_on_stop(self) -> None:
        async def exercise() -> None:
            process = FakeAsyncProcess()
            dashboard = cli._OwnedDashboard(
                self.hermes_home.resolve(), 24567, "dashboard-secret"
            )
            with (
                mock.patch.object(
                    cli.asyncio,
                    "create_subprocess_exec",
                    return_value=process,
                ) as spawn,
                mock.patch.object(
                    cli.shutil, "which", return_value="/tmp/hermes/bin/hermes"
                ),
                mock.patch.object(
                    cli,
                    "_wait_for_process_start_marker",
                    return_value="Sat Jul 11 21:30:00 2026",
                    create=True,
                ),
                mock.patch.object(
                    cli, "_dashboard_process_matches", return_value=True, create=True
                ),
                mock.patch.object(cli.os, "kill") as kill,
            ):
                await dashboard.start()
                record_path = cli.connector_paths(self.hermes_home).dashboard_pid
                record = json.loads(record_path.read_text(encoding="utf-8"))
                self.assertEqual(process.pid, record["pid"])
                self.assertEqual(24567, record["port"])
                self.assertEqual(
                    [
                        "/tmp/hermes/bin/hermes",
                        "dashboard",
                        "--isolated",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "24567",
                        "--no-open",
                    ],
                    record["argv"],
                )
                self.assertEqual(
                    "Sat Jul 11 21:30:00 2026", record["start_marker"]
                )
                self.assertEqual(0o600, record_path.stat().st_mode & 0o777)
                spawn.assert_awaited_once_with(
                    "/tmp/hermes/bin/hermes",
                    "dashboard",
                    "--isolated",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "24567",
                    "--no-open",
                    env=mock.ANY,
                    stdout=cli.asyncio.subprocess.DEVNULL,
                    stderr=cli.asyncio.subprocess.DEVNULL,
                )

                await dashboard.stop()
                kill.assert_called_once_with(process.pid, cli.signal.SIGTERM)
                self.assertFalse(record_path.exists())

        cli.asyncio.run(exercise())

    def test_dashboard_identity_accepts_exact_shebang_interpreter_argv(self) -> None:
        executable = "/tmp/hermes/bin/hermes"
        expected = (
            executable,
            "dashboard",
            "--isolated",
            "--host",
            "127.0.0.1",
            "--port",
            "24567",
            "--no-open",
        )
        record = {
            "pid": 2345,
            "hermes_home": str(self.hermes_home.resolve()),
            "port": 24567,
            "start_marker": "Sat Jul 11 21:30:00 2026",
            "argv": list(expected),
        }
        with (
            mock.patch.object(
                cli, "_process_start_marker", return_value=record["start_marker"]
            ),
            mock.patch.object(
                cli, "_process_argv", return_value=(sys.executable, *expected)
            ),
        ):
            self.assertTrue(cli._dashboard_process_matches(record))

    def test_dashboard_identity_rejects_missing_isolation_and_extra_flags(self) -> None:
        safe = [
            "/tmp/hermes/bin/hermes",
            "dashboard",
            "--isolated",
            "--host",
            "127.0.0.1",
            "--port",
            "24567",
            "--no-open",
        ]
        unsafe_argvs = (
            [arg for arg in safe if arg != "--isolated"],
            [*safe, "--insecure"],
        )
        for argv in unsafe_argvs:
            with self.subTest(argv=argv):
                record = {
                    "pid": 2345,
                    "hermes_home": str(self.hermes_home.resolve()),
                    "port": 24567,
                    "start_marker": "Sat Jul 11 21:30:00 2026",
                    "argv": argv,
                }
                with (
                    mock.patch.object(
                        cli,
                        "_process_start_marker",
                        return_value=record["start_marker"],
                    ),
                    mock.patch.object(cli, "_process_argv", return_value=tuple(argv)),
                ):
                    self.assertFalse(cli._dashboard_process_matches(record))

    def test_orphan_recovery_rejects_record_from_another_profile(self) -> None:
        paths = cli.connector_paths(self.hermes_home)
        paths.dashboard_pid.parent.mkdir(parents=True)
        paths.dashboard_pid.write_text(
            json.dumps(
                {
                    "pid": 2345,
                    "hermes_home": "/tmp/different-profile",
                    "port": 24567,
                    "start_marker": "Sat Jul 11 21:30:00 2026",
                    "argv": [
                        "/usr/local/bin/hermes",
                        "dashboard",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "24567",
                        "--no-open",
                    ],
                }
            ),
            encoding="utf-8",
        )
        with (
            mock.patch.object(cli, "_dashboard_process_matches", return_value=True),
            mock.patch.object(cli.os, "kill") as kill,
        ):
            self.assertFalse(cli._recover_orphan_dashboard(paths.dashboard_pid))
        kill.assert_not_called()
        self.assertFalse(paths.dashboard_pid.exists())

    def test_stale_connector_recovery_terminates_only_matching_dashboard(self) -> None:
        self._write_session()
        paths = cli.connector_paths(self.hermes_home)
        paths.pid.write_text("stale", encoding="utf-8")
        paths.dashboard_pid.write_text(
            json.dumps(
                {
                    "pid": 2345,
                    "hermes_home": str(self.hermes_home.resolve()),
                    "port": 24567,
                    "start_marker": "Sat Jul 11 21:30:00 2026",
                    "argv": ["/usr/local/bin/hermes", "dashboard"],
                }
            ),
            encoding="utf-8",
        )
        process = FakeProcess()

        def popen(*_args, **_kwargs):
            self._publish_ready_health(
                self.hermes_home,
                process.pid,
                "Sat Jul 11 21:31:00 2026",
            )
            return process

        with (
            mock.patch.object(cli, "_connector_process_matches", return_value=True),
            mock.patch.object(
                cli, "_dashboard_process_matches", side_effect=(True, True), create=True
            ),
            mock.patch.object(
                cli, "_wait_for_process_exit", return_value=True, create=True
            ),
            mock.patch.object(
                cli,
                "_wait_for_process_start_marker",
                return_value="Sat Jul 11 21:31:00 2026",
                create=True,
            ),
            mock.patch.object(cli.subprocess, "Popen", side_effect=popen),
            mock.patch.object(cli.os, "kill") as kill,
        ):
            cli.start_connector(self.hermes_home, health_wait=0)

        kill.assert_called_once_with(2345, cli.signal.SIGTERM)
        self.assertFalse(paths.dashboard_pid.exists())

    def test_stale_connector_recovery_does_not_signal_unrelated_dashboard(self) -> None:
        self._write_session()
        paths = cli.connector_paths(self.hermes_home)
        paths.pid.write_text("stale", encoding="utf-8")
        paths.dashboard_pid.write_text(
            json.dumps({"pid": 2345}), encoding="utf-8"
        )
        process = FakeProcess()

        def popen(*_args, **_kwargs):
            self._publish_ready_health(
                self.hermes_home,
                process.pid,
                "Sat Jul 11 21:31:00 2026",
            )
            return process

        with (
            mock.patch.object(cli, "_connector_process_matches", return_value=True),
            mock.patch.object(
                cli, "_dashboard_process_matches", return_value=False, create=True
            ),
            mock.patch.object(
                cli,
                "_wait_for_process_start_marker",
                return_value="Sat Jul 11 21:31:00 2026",
                create=True,
            ),
            mock.patch.object(cli.subprocess, "Popen", side_effect=popen),
            mock.patch.object(cli.os, "kill") as kill,
        ):
            cli.start_connector(self.hermes_home, health_wait=0)

        kill.assert_not_called()
        self.assertFalse(paths.dashboard_pid.exists())

    def test_installer_mirror_honors_explicit_pip_index(self) -> None:
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            "resolve_pip_index"
        )
        env = {
            **os.environ,
            "USE_CN_MIRROR": "1",
            "PIP_INDEX_URL": "https://packages.example/simple",
        }
        result = subprocess.run(
            ["bash", "-c", command],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual("https://packages.example/simple", result.stdout.strip())

    def test_installer_preflights_isolated_dashboard_capability(self) -> None:
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            "preflight_dashboard_isolation"
        )
        for supported in (True, False):
            with self.subTest(supported=supported):
                bin_dir = Path(self.tempdir.name) / f"bin-{supported}"
                bin_dir.mkdir()
                hermes = bin_dir / "hermes"
                help_text = "usage: hermes dashboard [--isolated]" if supported else "usage: hermes dashboard"
                hermes.write_text(
                    f"#!/usr/bin/env bash\nprintf '%s\\n' {help_text!r}\n",
                    encoding="utf-8",
                )
                hermes.chmod(0o700)
                result = subprocess.run(
                    ["bash", "-c", command],
                    capture_output=True,
                    text=True,
                    env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
                )
                self.assertEqual(0 if supported else 1, result.returncode)
                if not supported:
                    self.assertIn("--isolated", result.stderr)

    def test_installer_reads_stdin_only_when_registration_is_needed(self) -> None:
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            "human_token=''; read_registration_token \"$1\" 1; "
            "printf '%s|' \"$human_token\"; "
            "if IFS= read -r remaining; then printf '%s' \"$remaining\"; "
            "else printf 'EOF'; fi"
        )
        for needed, expected in (("0", "|human-secret"), ("1", "human-secret|EOF")):
            with self.subTest(registration_needed=needed):
                result = subprocess.run(
                    ["bash", "-c", command, "stdin-test", needed],
                    input="human-secret\n",
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(expected, result.stdout)

    def test_installer_preflight_creates_real_venv_and_cleans_artifacts(self) -> None:
        target = Path(self.tempdir.name) / "data" / "avernet" / "hermes-bcn"
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            "preflight_install_target \"$1\""
        )
        subprocess.run(
            ["bash", "-c", command, "preflight-test", str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertFalse(target.exists())

    def test_installer_static_security_order_and_resume_contract(self) -> None:
        script = INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn("--token", script)
        self.assertNotIn("--human-token ", script)
        self.assertRegex(
            script,
            r"printf\s+'%s\\n'\s+\"\$human_token\"\s*\|[\s\\]*\n?\s*python3",
        )
        self.assertIn("--human-token-stdin", script)
        self.assertLess(
            script.index("preflight_install_target \"$install_dir\""),
            script.index("registration=\"$("),
        )
        self.assertLess(
            script.index("  preflight_dashboard_isolation\n"),
            script.index("registration=\"$("),
        )
        for preserved in (
            "AVERNET_RAW_BASE_URL",
            "--workspace",
            "--china-mirror",
            "--profile",
            "--hermes-home",
            "--bot-name",
            "--bcs-endpoint",
            "--bcs-ws-url",
        ):
            self.assertIn(preserved, script)

    def test_installer_uses_strict_startup_readiness_wait(self) -> None:
        script = INSTALLER.read_text(encoding="utf-8")
        self.assertRegex(script, r'"\$connector" start .*--health-wait [1-9][0-9]*')
        self.assertLess(script.index('"$connector" start'), script.index('"$connector" status'))

    def test_start_with_unreachable_bcs_fails_and_cleans_owned_processes(self) -> None:
        home = Path(self.tempdir.name) / "unreachable-bcs-profile"
        home.mkdir(parents=True)
        (home / "config.yaml").write_text("model: fake\n", encoding="utf-8")
        dashboard_port = self._free_port()
        bcs_port = self._free_port()
        session_path = cli.connector_paths(home).session
        cli.AtomicJsonStore(session_path).save(
            {
                "bot_uuid": "bot-test",
                "bot_token": "bot-token",
                "bcs_url": f"ws://127.0.0.1:{bcs_port}/ws/bot",
                "dashboard_port": dashboard_port,
                "dashboard_token": "dashboard-token",
            }
        )
        bin_dir = Path(self.tempdir.name) / "fake-bin"
        bin_dir.mkdir()
        self._write_fake_hermes(bin_dir)
        connector = Path(cli.__file__).resolve()
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{Path(sys.executable).parent}:{os.environ['PATH']}",
        }
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(connector),
                    "start",
                    "--hermes-home",
                    str(home),
                    "--health-wait",
                    "1",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("ready", result.stderr.lower())
            paths = cli.connector_paths(home)
            self.assertFalse(paths.pid.exists())
            self.assertFalse(paths.dashboard_pid.exists())
            self.assertFalse(paths.health.exists())
        finally:
            subprocess.run(
                [sys.executable, str(connector), "stop", "--hermes-home", str(home)],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
                check=False,
            )

    def test_start_waits_for_bcs_handshake_and_stops_cleanly(self) -> None:
        home = Path(self.tempdir.name) / "ready-profile"
        home.mkdir(parents=True)
        (home / "config.yaml").write_text("model: fake\n", encoding="utf-8")
        dashboard_port = self._free_port()
        bcs_port = self._free_port()
        paths = cli.connector_paths(home)
        cli.AtomicJsonStore(paths.session).save(
            {
                "bot_uuid": "bot-test",
                "bot_token": "bot-token",
                "bcs_url": f"ws://127.0.0.1:{bcs_port}/ws/bot",
                "dashboard_port": dashboard_port,
                "dashboard_token": "dashboard-token",
            }
        )
        bin_dir = Path(self.tempdir.name) / "ready-bin"
        bin_dir.mkdir()
        self._write_fake_hermes(bin_dir)
        bcs_code = """
import asyncio
import json
import signal
import sys
from websockets.asyncio.server import serve

stop = asyncio.Event()

async def handler(websocket):
    frame = json.loads(await websocket.recv())
    await websocket.send(json.dumps({
        'type': 'res',
        'id': frame['id'],
        'ok': True,
        'payload': {
            'bot_uuid': 'bot-test',
            'token': 'rotated-token',
            'protocol_version': 2,
        },
    }))
    await websocket.wait_closed()

async def main():
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    server = await serve(handler, '127.0.0.1', int(sys.argv[1]))
    print('ready', flush=True)
    await stop.wait()
    server.close()
    await server.wait_closed()

asyncio.run(main())
"""
        bcs = subprocess.Popen(
            [sys.executable, "-c", bcs_code, str(bcs_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        connector = Path(cli.__file__).resolve()
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{Path(sys.executable).parent}:{os.environ['PATH']}",
        }
        try:
            readable, _, _ = select.select([bcs.stdout], [], [], 5)
            self.assertTrue(readable, "fake BCN server did not become ready")
            self.assertEqual("ready", bcs.stdout.readline().strip())
            start = subprocess.run(
                [
                    sys.executable,
                    str(connector),
                    "start",
                    "--hermes-home",
                    str(home),
                    "--health-wait",
                    "3",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )
            self.assertEqual(0, start.returncode, start.stderr)
            health = cli.AtomicJsonStore(paths.health).load({})
            self.assertTrue(health["ready"])
            self.assertTrue(health["dashboard_ready"])
            self.assertTrue(health["bcs_ready"])
            self.assertEqual(0o600, paths.health.stat().st_mode & 0o777)

            status = subprocess.run(
                [sys.executable, str(connector), "status", "--hermes-home", str(home)],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )
            self.assertEqual((0, "running"), (status.returncode, status.stdout.strip()))

            stopped = subprocess.run(
                [sys.executable, str(connector), "stop", "--hermes-home", str(home)],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )
            self.assertEqual((0, "stopped"), (stopped.returncode, stopped.stdout.strip()))
            self.assertFalse(paths.pid.exists())
            self.assertFalse(paths.dashboard_pid.exists())
            self.assertFalse(paths.health.exists())
        finally:
            subprocess.run(
                [sys.executable, str(connector), "stop", "--hermes-home", str(home)],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
                check=False,
            )
            bcs.terminate()
            try:
                bcs.wait(timeout=5)
            except subprocess.TimeoutExpired:
                bcs.kill()
                bcs.wait(timeout=5)
            if bcs.stdout is not None:
                bcs.stdout.close()
            if bcs.stderr is not None:
                bcs.stderr.close()

    def test_installer_resume_command_preserves_selected_options(self) -> None:
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            "AVERNET_RAW_BASE_URL=https://source.example/connectors; "
            "PIP_INDEX_URL=https://packages.example/simple; "
            "build_resume_command https://source.example/install-hermes.sh "
            '"$AVERNET_RAW_BASE_URL" --bot-name reviewer --profile review '
            "--bcs-endpoint https://bcs.example --bcs-ws-url wss://bcs.example/ws/bot "
            "--workspace '/tmp/work space' --china-mirror; "
            'printf "%s" "$RESUME_COMMAND"'
        )
        result = subprocess.run(
            ["bash", "-c", command],
            check=True,
            capture_output=True,
            text=True,
        )
        for preserved in (
            "AVERNET_RAW_BASE_URL=https://source.example/connectors",
            "PIP_INDEX_URL=https://packages.example/simple",
            "https://source.example/install-hermes.sh",
            "--bot-name reviewer",
            "--profile review",
            "--bcs-endpoint https://bcs.example",
            "--bcs-ws-url wss://bcs.example/ws/bot",
            "--workspace /tmp/work\\ space",
            "--china-mirror",
        ):
            self.assertIn(preserved, result.stdout)

    def test_installer_resume_environment_reaches_pipeline_rhs(self) -> None:
        probe = Path(self.tempdir.name) / "resume-probe.sh"
        probe.write_text(
            "printf '%s|%s|%s\\n' \"${AVERNET_RAW_BASE_URL:-}\" "
            '"${PIP_INDEX_URL:-}" "$*"\n',
            encoding="utf-8",
        )
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            "AVERNET_RAW_BASE_URL=https://source.example/connectors; "
            "PIP_INDEX_URL=https://packages.example/simple; "
            f"build_resume_command {subprocess.list2cmdline([probe.as_uri()])} "
            '"$AVERNET_RAW_BASE_URL" --bot-name reviewer --profile review; '
            'eval "$RESUME_COMMAND"'
        )
        result = subprocess.run(
            ["bash", "-c", command],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            "https://source.example/connectors|https://packages.example/simple|"
            "--bot-name reviewer --profile review",
            result.stdout.strip(),
        )

    def test_install_markdown_defines_executable_base_url_default_and_override(self) -> None:
        markdown = INSTALL_DOC.read_text(encoding="utf-8")
        self.assertNotIn("--token", markdown)
        self.assertIn("--human-token-stdin", markdown)
        self.assertIn(
            'mktemp "${TMPDIR:-/tmp}/install-hermes.XXXXXX"', markdown
        )
        self.assertIn("trap 'rm -f \"$installer\"' EXIT", markdown)
        self.assertNotIn("/tmp/install-hermes.sh", markdown)
        self.assertLess(markdown.index("mktemp "), markdown.index("trap 'rm -f"))
        self.assertLess(markdown.index("trap 'rm -f"), markdown.index("curl -fsSL"))
        self.assertIn("```bash\n(\n", markdown)
        match = re.search(
            r'^\s*(BCS_INSTALL_BASE_URL="\$\{BCS_INSTALL_BASE_URL:-[^}]+\}")$',
            markdown,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(match)
        assignment = match.group(1)
        expected_default = (
            "https://raw.githubusercontent.com/inclusionAI/Avernet/dev/"
            "src/bcs/docs/install-instructions"
        )
        for override, expected in ((None, expected_default), ("https://mirror.test", "https://mirror.test")):
            env = {**os.environ}
            if override is None:
                env.pop("BCS_INSTALL_BASE_URL", None)
            else:
                env["BCS_INSTALL_BASE_URL"] = override
            result = subprocess.run(
                ["bash", "-c", f'{assignment}; printf "%s" "$BCS_INSTALL_BASE_URL"'],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(expected, result.stdout)

    def test_install_markdown_bootstrap_cleans_temp_file(self) -> None:
        markdown = INSTALL_DOC.read_text(encoding="utf-8")
        block = re.search(r"```bash\n(.*?)```", markdown, flags=re.DOTALL)
        self.assertIsNotNone(block)
        source_dir = Path(self.tempdir.name) / "source"
        source_dir.mkdir()
        (source_dir / "install-hermes.sh").write_text(
            "IFS= read -r token\n[[ \"$token\" == human-secret ]]\n",
            encoding="utf-8",
        )
        for name, base_url, expected_code in (
            ("success", source_dir.as_uri(), 0),
            ("download-failure", (source_dir / "missing").as_uri(), 37),
        ):
            with self.subTest(name=name):
                temp_dir = Path(self.tempdir.name) / f"tmp-{name}"
                temp_dir.mkdir()
                result = subprocess.run(
                    ["bash", "-c", block.group(1)],
                    capture_output=True,
                    text=True,
                    env={
                        **os.environ,
                        "BCS_INSTALL_BASE_URL": base_url,
                        "TMPDIR": str(temp_dir),
                        "HUMAN_TOKEN": "human-secret",
                        "BOT_NAME": "Hermes Bot",
                        "HERMES_PROFILE": "review",
                        "BCS_HTTP_ENDPOINT": "http://127.0.0.1:21000",
                        "BCS_WS_URL": "ws://127.0.0.1:21000/ws/bot",
                    },
                )
                if expected_code == 0:
                    self.assertEqual(0, result.returncode, result.stderr)
                else:
                    self.assertNotEqual(0, result.returncode)
                self.assertEqual([], list(temp_dir.iterdir()))

    def _write_session(self) -> None:
        path = self.hermes_home / "bcn" / "session.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "bot_uuid": "bot-123",
                    "bot_token": "bot-secret",
                    "bcs_url": "ws://127.0.0.1:21000/ws/bot",
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _publish_ready_health(home: Path, pid: int, start_marker: str) -> None:
        cli.AtomicJsonStore(cli.connector_paths(home).health).save(
            {
                "pid": pid,
                "start_marker": start_marker,
                "dashboard_ready": True,
                "bcs_ready": True,
                "ready": True,
            }
        )

    @staticmethod
    def _write_fake_hermes(bin_dir: Path) -> Path:
        hermes = bin_dir / "hermes"
        hermes.write_text(
            f"""#!{sys.executable}
import asyncio
import signal
import sys
from websockets.asyncio.server import serve

if '--help' in sys.argv:
    print('usage: hermes dashboard --isolated')
    raise SystemExit(0)

port = int(sys.argv[sys.argv.index('--port') + 1])
stop = asyncio.Event()

async def handler(websocket):
    await websocket.wait_closed()

async def main():
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    server = await serve(handler, '127.0.0.1', port)
    await stop.wait()
    server.close()
    await server.wait_closed()

asyncio.run(main())
""",
            encoding="utf-8",
        )
        hermes.chmod(0o700)
        return hermes

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])


if __name__ == "__main__":
    unittest.main()
