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
import time
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

        original_promote = cli._promote_pending_session

        def promote(target_paths) -> None:
            pending = cli.AtomicJsonStore(target_paths.pending_session).load()
            order.append(f"promote:{pending['bot_uuid']}")
            original_promote(target_paths)

        with (
            mock.patch.object(
                cli,
                "_post_registration",
                return_value={"bot_uuid": "bot-new", "bot_token": "token-new"},
            ),
            mock.patch.object(cli, "_connector_process_matches", return_value=True),
            mock.patch.object(cli, "_wait_for_process_exit", return_value=True),
            mock.patch.object(cli.os, "kill", side_effect=stop) as kill,
            mock.patch.object(cli, "_promote_pending_session", side_effect=promote),
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
        self.assertEqual(["stop:bot-123", "promote:bot-new"], order)
        self.assertEqual("bot-new", session["bot_uuid"])
        self.assertEqual("token-new", store.load()["bot_token"])

    def test_replace_stop_timeout_preserves_pending_and_retry_skips_post(self) -> None:
        self._write_session()
        paths = cli.connector_paths(self.hermes_home)
        original = paths.session.read_bytes()
        cli.AtomicJsonStore(paths.pid).save({"pid": 1234})
        response = {"bot_uuid": "bot-pending", "bot_token": "pending-secret"}

        with (
            mock.patch.object(cli, "_post_registration", return_value=response) as post,
            mock.patch.object(cli, "_connector_process_matches", return_value=True),
            mock.patch.object(cli, "_wait_for_process_exit", return_value=False),
            mock.patch.object(cli.os, "kill"),
        ):
            with self.assertRaises(RuntimeError) as raised:
                cli.register_bot(
                    human_token="human-secret",
                    bot_name="Hermes Bot",
                    bcs_endpoint="http://127.0.0.1:21000",
                    bcs_url="ws://127.0.0.1:21000/ws/bot",
                    hermes_home=self.hermes_home,
                    replace=True,
                )

        message = str(raised.exception)
        self.assertIn(str(paths.pending_session), message)
        self.assertIn("bot-pending", message)
        self.assertNotIn("pending-secret", message)
        self.assertEqual(original, paths.session.read_bytes())
        self.assertEqual(
            "bot-pending",
            cli.AtomicJsonStore(paths.pending_session).load()["bot_uuid"],
        )
        self.assertEqual(0o600, paths.pending_session.stat().st_mode & 0o777)
        post.assert_called_once()

        with (
            mock.patch.object(cli, "_post_registration") as retry_post,
            mock.patch.object(cli, "_connector_process_matches", return_value=True),
            mock.patch.object(cli, "_wait_for_process_exit", return_value=True),
            mock.patch.object(cli.os, "kill") as retry_kill,
        ):
            session = cli.register_bot(
                human_token="",
                bot_name="Hermes Bot",
                bcs_endpoint="http://127.0.0.1:21000",
                bcs_url="ws://127.0.0.1:21000/ws/bot",
                hermes_home=self.hermes_home,
                replace=True,
            )

        retry_post.assert_not_called()
        retry_kill.assert_called_once_with(1234, cli.signal.SIGTERM)
        self.assertEqual("bot-pending", session["bot_uuid"])
        self.assertEqual("bot-pending", cli.AtomicJsonStore(paths.session).load()["bot_uuid"])
        self.assertFalse(paths.pending_session.exists())

    def test_replace_identity_failure_keeps_old_session_and_pending(self) -> None:
        self._write_session()
        paths = cli.connector_paths(self.hermes_home)
        original = paths.session.read_bytes()
        cli.AtomicJsonStore(paths.pid).save({"pid": 1234})
        with (
            mock.patch.object(
                cli,
                "_post_registration",
                return_value={"bot_uuid": "bot-new", "bot_token": "new-secret"},
            ),
            mock.patch.object(
                cli, "_connector_process_matches", side_effect=(True, False)
            ),
            mock.patch.object(cli.os, "kill") as kill,
        ):
            with self.assertRaises(RuntimeError) as raised:
                cli.register_bot(
                    human_token="human-secret",
                    bot_name="Hermes Bot",
                    bcs_endpoint="http://127.0.0.1:21000",
                    bcs_url="ws://127.0.0.1:21000/ws/bot",
                    hermes_home=self.hermes_home,
                    replace=True,
                )

        self.assertIn(str(paths.pending_session), str(raised.exception))
        self.assertIn("bot-new", str(raised.exception))
        self.assertNotIn("new-secret", str(raised.exception))
        self.assertEqual(original, paths.session.read_bytes())
        self.assertEqual("bot-new", cli.AtomicJsonStore(paths.pending_session).load()["bot_uuid"])
        kill.assert_not_called()

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

    def test_register_cli_rejects_existing_profile_for_different_bot_name(
        self,
    ) -> None:
        paths = cli.connector_paths(self.hermes_home)
        cli.AtomicJsonStore(paths.session).save(
            {
                "bot_uuid": "bot-existing",
                "bot_token": "bot-secret",
                "bcs_url": "ws://127.0.0.1:21000/ws/bot",
                "bot_name": "existing-bot",
            }
        )

        for replace_args in ([], ["--replace"]):
            with self.subTest(replace=bool(replace_args)):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(Path(cli.__file__).resolve()),
                        "register",
                        "--human-token-stdin",
                        "--bot-name",
                        "requested-bot",
                        "--hermes-home",
                        str(self.hermes_home),
                        *replace_args,
                    ],
                    input="unused-human-token\n",
                    capture_output=True,
                    text=True,
                )

                self.assertNotEqual(0, result.returncode)
                self.assertIn("already registered as existing-bot", result.stderr)
                self.assertIn("choose another profile", result.stderr)
                self.assertNotIn("registered bot-existing", result.stdout)

    def test_register_reuses_legacy_profile_without_bot_name(self) -> None:
        paths = cli.connector_paths(self.hermes_home)
        legacy = {
            "bot_uuid": "bot-existing",
            "bot_token": "bot-secret",
            "bcs_url": "ws://127.0.0.1:21000/ws/bot",
        }
        cli.AtomicJsonStore(paths.session).save(legacy)

        with mock.patch.object(cli, "_post_registration") as post:
            session = cli.register_bot(
                human_token="unused-human-token",
                bot_name="requested-bot",
                bcs_endpoint="http://127.0.0.1:21000",
                bcs_url="ws://127.0.0.1:21000/ws/bot",
                hermes_home=self.hermes_home,
            )

        self.assertEqual(legacy, session)
        post.assert_not_called()

    def test_register_rejects_pending_profile_for_wrong_or_missing_bot_name(
        self,
    ) -> None:
        self._write_session()
        paths = cli.connector_paths(self.hermes_home)

        for replace in (False, True):
            for pending_name in (None, "other-bot"):
                with self.subTest(replace=replace, pending_name=pending_name):
                    pending = {
                        "bot_uuid": "bot-pending",
                        "bot_token": "pending-secret",
                        "bcs_url": "ws://127.0.0.1:21000/ws/bot",
                    }
                    if pending_name is not None:
                        pending["bot_name"] = pending_name
                    cli.AtomicJsonStore(paths.pending_session).save(pending)

                    expected = (
                        "missing bot_name" if pending_name is None else "other-bot"
                    )
                    with self.assertRaisesRegex(ValueError, expected):
                        cli.register_bot(
                            human_token="",
                            bot_name="Hermes Bot",
                            bcs_endpoint="http://127.0.0.1:21000",
                            bcs_url="ws://127.0.0.1:21000/ws/bot",
                            hermes_home=self.hermes_home,
                            replace=replace,
                        )

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

    def test_wait_for_process_exit_treats_zombie_as_stopped(self) -> None:
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        try:
            deadline = time.monotonic() + 1
            while True:
                state = subprocess.run(
                    ["ps", "-p", str(process.pid), "-o", "stat="],
                    check=False,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                if state.startswith("Z"):
                    break
                if time.monotonic() >= deadline:
                    self.fail(f"child did not become a zombie: {state!r}")
                time.sleep(0.01)
            self.assertTrue(cli._wait_for_process_exit(process.pid, timeout=0.2))
        finally:
            process.wait(timeout=1)

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

    def test_installer_create_profile_requires_named_profile(self) -> None:
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            "validate_named_profile '' 1"
        )
        result = subprocess.run(
            ["/bin/bash", "-c", command], capture_output=True, text=True
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--create-profile requires --profile", result.stderr)

    def test_installer_creates_missing_named_profile_from_default(self) -> None:
        bin_dir = Path(self.tempdir.name) / "profile-bin"
        home = Path(self.tempdir.name) / "home"
        profile_home = home / ".hermes" / "profiles" / "reviewer"
        bin_dir.mkdir()
        hermes = bin_dir / "hermes"
        hermes.write_text(
            "#!/bin/sh\n"
            "test \"$1 $2 $3 $4\" = 'profile create reviewer --clone-from' || exit 9\n"
            "test \"$5\" = 'default' || exit 10\n"
            "mkdir -p \"$HOME/.hermes/profiles/reviewer\"\n"
            "printf 'model: inherited\\n' > \"$HOME/.hermes/profiles/reviewer/config.yaml\"\n",
            encoding="utf-8",
        )
        hermes.chmod(0o700)
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            'ensure_hermes_profile reviewer "$HOME/.hermes/profiles/reviewer" 1'
        )
        result = subprocess.run(
            ["/bin/bash", "-c", command],
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin"},
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((profile_home / "config.yaml").is_file())

    def test_installer_accepts_underscore_and_hyphen_profile_names(self) -> None:
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            'validate_named_profile "$1" 1'
        )
        for profile in ("reviewer_1", "reviewer-one"):
            with self.subTest(profile=profile):
                result = subprocess.run(
                    ["/bin/bash", "-c", command, "profile-test", profile],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)

    def test_installer_rejects_reserved_profile_names(self) -> None:
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            'validate_named_profile "$1" 1'
        )
        for profile in ("default", "hermes", "test", "tmp", "root", "sudo"):
            with self.subTest(profile=profile):
                result = subprocess.run(
                    ["/bin/bash", "-c", command, "profile-test", profile],
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn(f"profile name is reserved: {profile}", result.stderr)

    def test_installer_rejects_profile_names_over_64_characters(self) -> None:
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            'validate_named_profile "$1" 1'
        )
        result = subprocess.run(
            ["/bin/bash", "-c", command, "profile-test", "a" * 65],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "profile must match [a-z0-9][a-z0-9_-]{0,63}", result.stderr
        )

    def test_installer_keeps_existing_configured_profile(self) -> None:
        profile_home = Path(self.tempdir.name) / "reviewer"
        profile_home.mkdir()
        (profile_home / "config.yaml").write_text("model: inherited\n", encoding="utf-8")
        bin_dir = Path(self.tempdir.name) / "profile-bin"
        bin_dir.mkdir()
        call_log = Path(self.tempdir.name) / "hermes-calls"
        hermes = bin_dir / "hermes"
        hermes.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" > \"$HERMES_CALL_LOG\"\n"
            "exit 9\n",
            encoding="utf-8",
        )
        hermes.chmod(0o700)
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            'ensure_hermes_profile reviewer "$1" 1'
        )
        result = subprocess.run(
            ["/bin/bash", "-c", command, "profile-test", str(profile_home)],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "HERMES_CALL_LOG": str(call_log),
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            },
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(call_log.exists())

    def test_installer_rejects_different_bot_name_for_registered_profile(self) -> None:
        session = Path(self.tempdir.name) / "session.json"
        session.write_text(
            json.dumps(
                {
                    "bot_uuid": "bot-existing",
                    "bot_token": "secret",
                    "bcs_url": "ws://127.0.0.1:21000/ws/bot",
                    "bot_name": "hermes2",
                }
            ),
            encoding="utf-8",
        )
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            'reject_profile_bot_name_mismatch "$1" hermes4 reviewer'
        )
        result = subprocess.run(
            ["/bin/bash", "-c", command, "conflict", str(session)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("reviewer is already registered as hermes2", result.stderr)

    def test_installer_allows_legacy_registered_profile_without_bot_name(self) -> None:
        session = Path(self.tempdir.name) / "session.json"
        session.write_text(
            json.dumps(
                {
                    "bot_uuid": "bot-existing",
                    "bot_token": "secret",
                    "bcs_url": "ws://127.0.0.1:21000/ws/bot",
                }
            ),
            encoding="utf-8",
        )
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            'reject_profile_bot_name_mismatch "$1" hermes4 reviewer'
        )
        result = subprocess.run(
            ["/bin/bash", "-c", command, "missing-name", str(session)],
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, result.returncode, result.stderr)

    def test_installer_installs_dependencies_without_pip_index_under_nounset(
        self,
    ) -> None:
        fake_python = Path(self.tempdir.name) / "fake-python"
        recorded_args = Path(self.tempdir.name) / "pip-args"
        fake_python.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$PIP_ARGS_FILE\"\n",
            encoding="utf-8",
        )
        fake_python.chmod(0o700)
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            "unset PIP_INDEX_URL USE_CN_MIRROR; "
            'install_connector_dependencies "$1"'
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PIP_INDEX_URL", "USE_CN_MIRROR"}
        }
        env["PIP_ARGS_FILE"] = str(recorded_args)

        result = subprocess.run(
            ["/bin/bash", "-c", command, "dependency-test", str(fake_python)],
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            ["-m", "pip", "install", "websockets>=14,<16"],
            recorded_args.read_text(encoding="utf-8").splitlines(),
        )

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
                help_text = (
                    "usage: hermes dashboard [--isolated]"
                    if supported
                    else "usage: hermes dashboard"
                )
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

    def test_installer_selects_versioned_python_when_python3_is_too_old(self) -> None:
        bin_dir = Path(self.tempdir.name) / "python-bin"
        bin_dir.mkdir()
        old_python = bin_dir / "python3"
        new_python = bin_dir / "python3.12"
        old_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        new_python.write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = \"-c\" ]; then exit 0; fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        old_python.chmod(0o700)
        new_python.chmod(0o700)
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            "resolve_python"
        )

        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"},
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(str(new_python), result.stdout.strip())

    def test_installer_static_security_order_and_resume_contract(self) -> None:
        script = INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn("--token", script)
        self.assertNotIn("--human-token ", script)
        self.assertRegex(
            script,
            r"printf\s+'%s\\n'\s+\"\$human_token\"\s*\|[\s\\]*\n?\s*\"\$PYTHON_CMD\"",
        )
        self.assertIn("--human-token-stdin", script)
        self.assertIn("session.pending.json", script)
        self.assertIn('"$replace" == "1" && "$pending_valid" == "1"', script)
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
            "--create-profile",
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
        bcs = self._start_fake_bcs(bcs_port)
        connector = Path(cli.__file__).resolve()
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{Path(sys.executable).parent}:{os.environ['PATH']}",
        }
        try:
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
            self.assertEqual(
                "session.list",
                (home / "bcn" / "session-list.rpc").read_text(encoding="utf-8"),
            )

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
            self._stop_fake_process(bcs)

    def test_start_rejects_dashboard_that_never_replies_to_rpc(self) -> None:
        home = Path(self.tempdir.name) / "silent-dashboard-profile"
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
        bin_dir = Path(self.tempdir.name) / "silent-dashboard-bin"
        bin_dir.mkdir()
        self._write_fake_hermes(bin_dir, respond_to_rpc=False)
        bcs = self._start_fake_bcs(bcs_port)
        connector = Path(cli.__file__).resolve()
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{Path(sys.executable).parent}:{os.environ['PATH']}",
        }
        try:
            start = subprocess.run(
                [
                    sys.executable,
                    str(connector),
                    "start",
                    "--hermes-home",
                    str(home),
                    "--health-wait",
                    "2",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )
            self.assertNotEqual(0, start.returncode)
            self.assertIn("ready", start.stderr.lower())
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
            self._stop_fake_process(bcs)

    def test_start_and_manual_run_startup_race_leaves_one_connector(self) -> None:
        home = Path(self.tempdir.name) / "launch-race-profile"
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
        bin_dir = Path(self.tempdir.name) / "launch-race-bin"
        bin_dir.mkdir()
        hermes = self._write_fake_hermes(bin_dir)
        bcs = self._start_fake_bcs(bcs_port)
        connector = Path(cli.__file__).resolve()
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{Path(sys.executable).parent}:{os.environ['PATH']}",
        }
        launch_path = home / "bcn" / "launch.lock"
        launch_handle = launch_path.open("a", encoding="utf-8")
        launch_path.chmod(0o600)
        cli.fcntl.flock(launch_handle.fileno(), cli.fcntl.LOCK_EX)
        manual = None
        start = None
        try:
            manual = subprocess.Popen(
                [sys.executable, str(connector), "run", "--hermes-home", str(home)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            start = subprocess.Popen(
                [
                    sys.executable,
                    str(connector),
                    "start",
                    "--hermes-home",
                    str(home),
                    "--health-wait",
                    "5",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            time.sleep(0.2)
            cli.fcntl.flock(launch_handle.fileno(), cli.fcntl.LOCK_UN)
            launch_handle.close()
            start_stdout, start_stderr = start.communicate(timeout=15)
            self.assertEqual(0, start.returncode, start_stderr)
            self.assertIn("running (pid", start_stdout)

            connector_record = cli.AtomicJsonStore(paths.pid).load({})
            dashboard_record = cli.AtomicJsonStore(paths.dashboard_pid).load({})
            self.assertIsInstance(connector_record.get("pid"), int)
            self.assertIsInstance(dashboard_record.get("pid"), int)
            ps = subprocess.run(
                ["ps", "-ww", "-axo", "pid=,command="],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            connector_processes = [
                line
                for line in ps
                if str(connector) in line and str(home) in line and " run " in line
            ]
            dashboard_processes = [
                line
                for line in ps
                if str(hermes) in line and f"--port {dashboard_port}" in line
            ]
            self.assertEqual(1, len(connector_processes), connector_processes)
            self.assertEqual(1, len(dashboard_processes), dashboard_processes)
            self.assertEqual(0o600, paths.launch_lock.stat().st_mode & 0o777)
        finally:
            if not launch_handle.closed:
                cli.fcntl.flock(launch_handle.fileno(), cli.fcntl.LOCK_UN)
                launch_handle.close()
            subprocess.run(
                [sys.executable, str(connector), "stop", "--hermes-home", str(home)],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
                check=False,
            )
            if manual is not None:
                try:
                    manual.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    manual.terminate()
                    manual.communicate(timeout=5)
            if start is not None and start.poll() is None:
                start.terminate()
                start.communicate(timeout=5)
            self._stop_fake_process(bcs)

    def test_installer_resume_command_preserves_selected_options(self) -> None:
        pip_index = "https://mirror-user:mirror-pass@packages.example/simple"
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            "AVERNET_RAW_BASE_URL=https://source.example/connectors; "
            f"PIP_INDEX_URL={pip_index}; "
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
        self.assertNotIn(pip_index, result.stdout)
        self.assertIn(
            'PIP_INDEX_URL="${PIP_INDEX_URL:?export PIP_INDEX_URL before resuming}"',
            result.stdout,
        )
        for preserved in (
            "AVERNET_RAW_BASE_URL=https://source.example/connectors",
            "https://source.example/install-hermes.sh",
            "--bot-name reviewer",
            "--profile review",
            "--bcs-endpoint https://bcs.example",
            "--bcs-ws-url wss://bcs.example/ws/bot",
            "--workspace /tmp/work\\ space",
            "--china-mirror",
        ):
            self.assertIn(preserved, result.stdout)

    def test_installer_recovers_pending_replacement_with_printed_command(
        self,
    ) -> None:
        source_dir = Path(self.tempdir.name) / "replacement-source"
        source_dir.mkdir()
        fake_connector = source_dir / "hermes_bcn.py"
        fake_connector.write_text(
            "import json\n"
            "import os\n"
            "import sys\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "command = args[0]\n"
            "if command in {'start', 'status'}:\n"
            "    if os.environ.get('FAIL_STAGE') == command:\n"
            "        raise SystemExit(42)\n"
            "    raise SystemExit(0)\n"
            "if command != 'register':\n"
            "    raise SystemExit(2)\n"
            "if '--profile' in args:\n"
            "    home = Path.home() / '.hermes' / 'profiles' / args[args.index('--profile') + 1]\n"
            "else:\n"
            "    home = Path(args[args.index('--hermes-home') + 1])\n"
            "state = home / 'bcn'\n"
            "pending = state / 'session.pending.json'\n"
            "session = state / 'session.json'\n"
            "if '--replace' not in args:\n"
            "    if not session.exists():\n"
            "        raise SystemExit(2)\n"
            "    current = json.loads(session.read_text(encoding='utf-8'))\n"
            "    print('registered ' + current['bot_uuid'])\n"
            "    raise SystemExit(0)\n"
            "if pending.exists():\n"
            "    os.replace(pending, session)\n"
            "    print('registered bot-pending')\n"
            "    raise SystemExit(0)\n"
            "state.mkdir(parents=True, exist_ok=True)\n"
            "payload = {\n"
            "    'bot_uuid': 'bot-pending',\n"
            "    'bot_token': 'pending-secret',\n"
            "    'bcs_url': args[args.index('--bcs-url') + 1],\n"
            "    'bot_name': args[args.index('--bot-name') + 1],\n"
            "}\n"
            "pending.write_text(json.dumps(payload), encoding='utf-8')\n"
            "pending.chmod(0o600)\n"
            "print('error: simulated post-registration replacement failure', file=sys.stderr)\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )

        bin_dir = Path(self.tempdir.name) / "replacement-bin"
        bin_dir.mkdir()
        hermes = bin_dir / "hermes"
        hermes.write_text(
            "#!/bin/sh\n"
            "test \"$1 $2\" = 'dashboard --help' || exit 9\n"
            "printf '%s\\n' 'usage: hermes dashboard --isolated'\n",
            encoding="utf-8",
        )
        hermes.chmod(0o700)
        python_bin = bin_dir / "python"
        python_bin.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = '-m' ] && [ \"$2\" = 'venv' ]; then\n"
            "  mkdir -p \"$3/bin\"\n"
            "  cp \"$0\" \"$3/bin/python\"\n"
            "  chmod 700 \"$3/bin/python\"\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1\" = '-m' ] && [ \"$2\" = 'pip' ]; then\n"
            "  [ \"${FAIL_STAGE:-}\" = 'pip' ] && exit 41\n"
            "  exit 0\n"
            "fi\n"
            "exec \"$TEST_PYTHON\" \"$@\"\n",
            encoding="utf-8",
        )
        python_bin.chmod(0o700)

        base_env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"FAIL_STAGE", "PIP_INDEX_URL", "USE_CN_MIRROR"}
        }
        marker = "Resume with:\n"
        for failure_stage in ("pip", "start", "status"):
            with self.subTest(failure_stage=failure_stage):
                home = Path(self.tempdir.name) / f"replacement-home-{failure_stage}"
                profile_home = home / ".hermes" / "profiles" / "review"
                profile_home.mkdir(parents=True)
                (profile_home / "config.yaml").write_text(
                    "model: fake\n", encoding="utf-8"
                )
                env = {
                    **base_env,
                    "HOME": str(home),
                    "XDG_DATA_HOME": str(
                        Path(self.tempdir.name) / f"replacement-data-{failure_stage}"
                    ),
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "PYTHON_BIN": str(python_bin),
                    "TEST_PYTHON": sys.executable,
                    "AVERNET_RAW_BASE_URL": source_dir.as_uri(),
                    "BCS_INSTALLER_URL": INSTALLER.as_uri(),
                }
                first = subprocess.run(
                    [
                        "/bin/bash",
                        str(INSTALLER),
                        "--human-token-stdin",
                        "--bot-name",
                        "replacement-bot",
                        "--profile",
                        "review",
                        "--replace",
                    ],
                    input="human-token\n",
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=30,
                )

                self.assertNotEqual(0, first.returncode)
                self.assertIn(marker, first.stderr)
                resume_command = (
                    first.stderr.split(marker, 1)[1].strip().splitlines()[0]
                )
                self.assertIn("--replace", resume_command)

                promoted = subprocess.run(
                    ["/bin/bash", "-c", resume_command],
                    capture_output=True,
                    text=True,
                    env={**env, "FAIL_STAGE": failure_stage},
                    timeout=30,
                )
                paths = cli.connector_paths(profile_home)
                self.assertNotEqual(0, promoted.returncode)
                self.assertIn(marker, promoted.stderr)
                post_promotion_resume = (
                    promoted.stderr.split(marker, 1)[1].strip().splitlines()[0]
                )
                self.assertNotIn("--replace", post_promotion_resume)
                self.assertEqual(
                    "bot-pending",
                    cli.AtomicJsonStore(paths.session).load()["bot_uuid"],
                )
                self.assertFalse(paths.pending_session.exists())

                retry = subprocess.run(
                    ["/bin/bash", "-c", post_promotion_resume],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=30,
                )
                self.assertEqual(0, retry.returncode, retry.stderr)
                self.assertIn("running for bot bot-pending", retry.stdout)

    def test_installer_pending_recovery_requires_matching_bot_name(self) -> None:
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            'recoverable_pending_uuid "$1" replacement-bot'
        )
        cases = (
            ("matching", "replacement-bot", 0, "bot-pending"),
            ("missing", None, 1, ""),
            ("different", "other-bot", 1, ""),
        )

        for name, stored_name, expected_code, expected_output in cases:
            with self.subTest(name=name):
                pending = Path(self.tempdir.name) / f"pending-{name}.json"
                payload = {
                    "bot_uuid": "bot-pending",
                    "bot_token": "pending-secret",
                    "bcs_url": "ws://127.0.0.1:21000/ws/bot",
                }
                if stored_name is not None:
                    payload["bot_name"] = stored_name
                pending.write_text(json.dumps(payload), encoding="utf-8")

                result = subprocess.run(
                    ["/bin/bash", "-c", command, "pending-recovery", str(pending)],
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(expected_code, result.returncode, result.stderr)
                self.assertEqual(expected_output, result.stdout.strip())

    def test_installer_failure_output_does_not_expose_authenticated_pip_index(
        self,
    ) -> None:
        fake_python = Path(self.tempdir.name) / "mirror-python"
        recorded_args = Path(self.tempdir.name) / "mirror-args"
        fake_python.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$PIP_ARGS_FILE\"\n",
            encoding="utf-8",
        )
        fake_python.chmod(0o700)
        pip_index = "https://mirror-user:mirror-pass@packages.example/simple"
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            'install_connector_dependencies "$1"; '
            "build_resume_command https://source.example/install-hermes.sh "
            "https://source.example/connectors --bot-name reviewer --profile review; "
            "REGISTERED_UUID=bot-test; trap on_exit EXIT; false"
        )
        result = subprocess.run(
            ["/bin/bash", "-c", command, "mirror-failure", str(fake_python)],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PIP_INDEX_URL": pip_index,
                "PIP_ARGS_FILE": str(recorded_args),
            },
        )

        self.assertNotEqual(0, result.returncode)
        self.assertNotIn(pip_index, result.stdout + result.stderr)
        self.assertIn(
            'PIP_INDEX_URL="${PIP_INDEX_URL:?export PIP_INDEX_URL before resuming}"',
            result.stderr,
        )
        self.assertEqual(
            [
                "-m",
                "pip",
                "install",
                "--index-url",
                pip_index,
                "websockets>=14,<16",
            ],
            recorded_args.read_text(encoding="utf-8").splitlines(),
        )

    def test_installer_main_preserves_create_profile_in_resume_command(self) -> None:
        home = Path(self.tempdir.name) / "resume-home"
        profile_home = home / ".hermes" / "profiles" / "review"
        profile_home.mkdir(parents=True)
        (profile_home / "config.yaml").write_text("model: fake\n", encoding="utf-8")
        resume_args_file = Path(self.tempdir.name) / "resume-args"
        bin_dir = Path(self.tempdir.name) / "resume-bin"
        bin_dir.mkdir()
        hermes = bin_dir / "hermes"
        hermes.write_text(
            "#!/bin/sh\n"
            "test \"$1 $2\" = 'dashboard --help' || exit 9\n"
            "printf '%s\\n' 'usage: hermes dashboard --isolated'\n",
            encoding="utf-8",
        )
        hermes.chmod(0o700)
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            "preflight_install_target() { :; }; "
            'build_resume_command() { shift 2; printf \'%s\\n\' "$@" '
            '> "$RESUME_ARGS_FILE"; exit 0; }; '
            'main "$@"'
        )
        result = subprocess.run(
            [
                "/bin/bash",
                "-c",
                command,
                "resume-main",
                "--human-token-stdin",
                "--bot-name",
                "reviewer",
                "--profile",
                "review",
                "--create-profile",
            ],
            input="human-token\n",
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "HOME": str(home),
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "PYTHON_BIN": sys.executable,
                "AVERNET_RAW_BASE_URL": CONNECTOR_DIR.as_uri(),
                "BCS_INSTALLER_URL": INSTALLER.as_uri(),
                "RESUME_ARGS_FILE": str(resume_args_file),
            },
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            [
                "--bot-name",
                "reviewer",
                "--profile",
                "review",
                "--bcs-endpoint",
                "http://127.0.0.1:21000",
                "--bcs-ws-url",
                "ws://127.0.0.1:21000/ws/bot",
                "--create-profile",
            ],
            resume_args_file.read_text(encoding="utf-8").splitlines(),
        )

    def test_installer_main_resumes_same_registered_bot_without_registration(
        self,
    ) -> None:
        home = Path(self.tempdir.name) / "idempotent-home"
        profile_home = home / ".hermes" / "profiles" / "review"
        profile_home.mkdir(parents=True)
        (profile_home / "config.yaml").write_text("model: fake\n", encoding="utf-8")
        bcs_port = self._free_port()
        session = profile_home / "bcn" / "session.json"
        session.parent.mkdir()
        session.write_text(
            json.dumps(
                {
                    "bot_uuid": "bot-test",
                    "bot_token": "bot-secret",
                    "bcs_url": f"ws://127.0.0.1:{bcs_port}/ws/bot",
                    "bot_name": "reviewer",
                }
            ),
            encoding="utf-8",
        )
        bin_dir = Path(self.tempdir.name) / "idempotent-bin"
        bin_dir.mkdir()
        self._write_fake_hermes(bin_dir)
        data_home = Path(self.tempdir.name) / "idempotent-data"
        install_dir = data_home / "avernet" / "hermes-bcn"
        venv_bin = install_dir / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        installed_python = venv_bin / "python"
        installed_python.write_text(
            "#!/bin/sh\nexec \"$TEST_PYTHON\" \"$@\"\n", encoding="utf-8"
        )
        installed_python.chmod(0o700)
        installed_connector = install_dir / "hermes_bcn.py"
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            "preflight_install_target() { :; }; "
            "install_connector_dependencies() { :; }; "
            'main "$@"; '
            "printf '|'; "
            "if IFS= read -r remaining; then printf '%s' \"$remaining\"; "
            "else printf 'EOF'; fi"
        )
        env = {
            **os.environ,
            "HOME": str(home),
            "XDG_DATA_HOME": str(data_home),
            "PATH": f"{bin_dir}:{Path(sys.executable).parent}:{os.environ['PATH']}",
            "PYTHON_BIN": sys.executable,
            "TEST_PYTHON": sys.executable,
            "AVERNET_RAW_BASE_URL": CONNECTOR_DIR.as_uri(),
            "BCS_INSTALLER_URL": INSTALLER.as_uri(),
        }
        bcs = self._start_fake_bcs(bcs_port)
        try:
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    command,
                    "idempotent-main",
                    "--human-token-stdin",
                    "--bot-name",
                    "reviewer",
                    "--profile",
                    "review",
                    "--bcs-endpoint",
                    "http://127.0.0.1:1",
                ],
                input="unused-human-token\n",
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
            connector_log = profile_home / "bcn" / "connector.log"
            log_output = (
                connector_log.read_text(encoding="utf-8")
                if connector_log.is_file()
                else ""
            )
            self.assertEqual(0, result.returncode, f"{result.stderr}\n{log_output}")
            self.assertIn(
                "Hermes BCN connector is running for bot bot-test.", result.stdout
            )
            self.assertTrue(
                result.stdout.endswith("|unused-human-token"), result.stdout
            )
            saved = json.loads(session.read_text(encoding="utf-8"))
            self.assertEqual("bot-test", saved["bot_uuid"])
            self.assertEqual("reviewer", saved["bot_name"])
        finally:
            if installed_connector.is_file():
                subprocess.run(
                    [
                        sys.executable,
                        str(installed_connector),
                        "stop",
                        "--profile",
                        "review",
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=15,
                    check=False,
                )
            self._stop_fake_process(bcs)

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

    def test_installer_resume_requires_pip_index_in_fresh_nounset_shell(
        self,
    ) -> None:
        source = Path(self.tempdir.name) / "resume-source.sh"
        source.write_text("exit 0\n", encoding="utf-8")
        pip_index = "https://mirror-user:mirror-pass@packages.example/simple"
        build = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            f"PIP_INDEX_URL={pip_index}; "
            f"build_resume_command {subprocess.list2cmdline([source.as_uri()])} "
            "https://source.example/connectors --bot-name reviewer --profile review; "
            'printf \'%s\\n\' "$RESUME_COMMAND"'
        )
        generated = subprocess.run(
            ["bash", "-c", build],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        env = {key: value for key, value in os.environ.items() if key != "PIP_INDEX_URL"}
        result = subprocess.run(
            ["bash", "-u", "-c", generated],
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("export PIP_INDEX_URL before resuming", result.stderr)
        self.assertNotIn(pip_index, result.stdout + result.stderr)

    def test_install_markdown_defines_executable_base_url_default_and_override(self) -> None:
        markdown = INSTALL_DOC.read_text(encoding="utf-8")
        self.assertNotIn("--token", markdown)
        self.assertIn("--human-token-stdin", markdown)
        self.assertIn('--bot-name "${BOT_NAME}"', markdown)
        self.assertIn('--profile "${HERMES_PROFILE}"', markdown)
        self.assertIn("--create-profile", markdown)
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
        cases = (
            (None, expected_default),
            ("https://mirror.test", "https://mirror.test"),
        )
        for override, expected in cases:
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
                    "bot_name": "Hermes Bot",
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
    def _write_fake_hermes(bin_dir: Path, *, respond_to_rpc: bool = True) -> Path:
        hermes = bin_dir / "hermes"
        hermes.write_text(
            f"""#!{sys.executable}
import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from websockets.asyncio.server import serve

if '--help' in sys.argv:
    print('usage: hermes dashboard --isolated')
    raise SystemExit(0)

port = int(sys.argv[sys.argv.index('--port') + 1])
stop = asyncio.Event()
respond_to_rpc = {respond_to_rpc!r}

async def handler(websocket):
    try:
        raw = await websocket.recv()
    except Exception:
        return
    request = json.loads(raw)
    if respond_to_rpc:
        marker = Path(os.environ['HERMES_HOME']) / 'bcn' / 'session-list.rpc'
        marker.write_text(request['method'], encoding='utf-8')
        await websocket.send(json.dumps({{
            'jsonrpc': '2.0',
            'id': request['id'],
            'result': {{'sessions': []}},
        }}))
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
    def _start_fake_bcs(port: int) -> subprocess.Popen:
        code = """
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
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        readable, _, _ = select.select([process.stdout], [], [], 5)
        if not readable or process.stdout.readline().strip() != "ready":
            CliTests._stop_fake_process(process)
            raise RuntimeError("fake BCN server did not become ready")
        return process

    @staticmethod
    def _stop_fake_process(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])


if __name__ == "__main__":
    unittest.main()
