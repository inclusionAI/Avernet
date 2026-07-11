from __future__ import annotations

import io
import json
import os
import re
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
            mock.patch.object(
                cli, "_connector_process_matches", side_effect=(False, True)
            ),
            mock.patch.object(
                cli,
                "_wait_for_process_start_marker",
                return_value="Sat Jul 11 21:30:00 2026",
                create=True,
            ),
            mock.patch.object(cli.subprocess, "Popen", return_value=process),
            mock.patch.object(cli.time, "sleep"),
        ):
            self.assertEqual(
                process.pid,
                cli.start_connector(self.hermes_home, health_wait=0),
            )
        record = json.loads(paths.pid.read_text(encoding="utf-8"))
        self.assertEqual(process.pid, record["pid"])
        self.assertEqual("Sat Jul 11 21:30:00 2026", record["start_marker"])

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

    def test_concurrent_start_is_locked_and_spawns_once(self) -> None:
        self._write_session()
        second_spawn = threading.Event()
        calls: list[int] = []

        def popen(*_args, **_kwargs):
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                second_spawn.wait(timeout=0.2)
            else:
                second_spawn.set()
            return FakeProcess(pid=43000 + len(calls))

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
                    "Sat Jul 11 21:30:00 2026", record["start_marker"]
                )
                self.assertEqual(0o600, record_path.stat().st_mode & 0o777)

                await dashboard.stop()
                kill.assert_called_once_with(process.pid, cli.signal.SIGTERM)
                self.assertFalse(record_path.exists())

        cli.asyncio.run(exercise())

    def test_dashboard_identity_accepts_exact_shebang_interpreter_argv(self) -> None:
        executable = "/tmp/hermes/bin/hermes"
        expected = (
            executable,
            "dashboard",
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
            mock.patch.object(cli.subprocess, "Popen", return_value=process),
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
            mock.patch.object(cli.subprocess, "Popen", return_value=process),
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

    def test_installer_resume_command_preserves_selected_options(self) -> None:
        command = (
            f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
            "export AVERNET_RAW_BASE_URL=https://source.example/connectors; "
            "export PIP_INDEX_URL=https://packages.example/simple; "
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

    def test_install_markdown_defines_executable_base_url_default_and_override(self) -> None:
        markdown = INSTALL_DOC.read_text(encoding="utf-8")
        match = re.search(
            r'^(BCS_INSTALL_BASE_URL="\$\{BCS_INSTALL_BASE_URL:-[^}]+\}")$',
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
