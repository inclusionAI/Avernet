from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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
sys.path.insert(0, str(CONNECTOR_DIR))

import hermes_bcn as cli  # noqa: E402


class FakeProcess:
    def __init__(self, pid: int = 43210, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


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
        paths.pid.parent.mkdir(parents=True)
        paths.pid.write_text(
            json.dumps({"pid": 1234, "hermes_home": str(self.hermes_home)}),
            encoding="utf-8",
        )
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
        with (
            mock.patch.object(cli, "_connector_process_matches", return_value=False),
            mock.patch.object(cli.subprocess, "Popen", return_value=process),
            mock.patch.object(cli.time, "sleep"),
        ):
            self.assertEqual(
                process.pid,
                cli.start_connector(self.hermes_home, health_wait=0),
            )
        record = json.loads(paths.pid.read_text(encoding="utf-8"))
        self.assertEqual(process.pid, record["pid"])

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


if __name__ == "__main__":
    unittest.main()
