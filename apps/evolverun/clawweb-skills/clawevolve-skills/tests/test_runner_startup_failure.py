"""Exercise real detached startup and collect its normal CW report requests."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import shlex
import shutil
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def reports():
    received = queue.Queue()
    statuses = queue.Queue()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.put((self.path, body))
            self.send_response(statuses.get_nowait() if not statuses.empty() else 200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    received.statuses = statuses
    yield f"http://127.0.0.1:{server.server_port}", received
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def installation(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    for name in ("clawevolve_async_runner.sh", "clawevolve_task_launcher.sh", "clawevolve_startup_failure.py"):
        source = ROOT / "scripts" / name
        if source.exists():
            shutil.copy2(source, release / name)
    shutil.copytree(ROOT / "platform", release / "platform")
    workspace = tmp_path / "bot" / "workspace"
    workspace.mkdir(parents=True)
    env = os.environ | {
        "CLAWEVOLVE_RUNNER_ENVIRONMENT": "local",
        "OPENCLAW_STATE_DIR": str(workspace.parent),
        "OPENCLAW_WORKSPACE_DIR": str(workspace),
        "OPENCLAW_WORKSPACE": str(workspace),
        "SKILL_BASE_DIR": str(workspace / "clawevolve-skills"),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
    }
    return release, workspace, env


def assert_failure(received, code):
    try:
        path, body = received.get(timeout=4)
    except queue.Empty:
        pytest.fail("startup exited without reporting failure to CW")
    assert path == "/api/evolve/internal/tasks/EV-TEST/steps/STEP-TEST/report"
    assert body["status"] == "failed"
    assert body["error"]["code"] == code
    assert body["error"]["retryable"] is True
    assert received.empty(), "startup must not report the same exit twice"
    return body


@pytest.mark.parametrize("failure", ["manifest", "command", "environment", "handler", "signal"])
def test_detached_failures_after_started_are_reported(installation, reports, failure):
    release, workspace, env = installation
    base, received = reports
    if failure in {"command", "environment", "signal"}:
        commands = release / "bin"
        commands.mkdir()
        if failure in {"environment", "signal"}:
            shim = commands / "python3"
            action = 'kill -TERM "$PPID"; exit 0' if failure == "signal" else "exit 73"
            shim.write_text("#!/bin/bash\n"
                f'if [[ "${{CLAWEVOLVE_DETACHED_BOOTSTRAP:-}}" == true && "$2" == shell-init ]]; then {action}; fi\n'
                f"exec {shlex.quote(sys.executable)} \"$@\"\n")
        else:
            shim = commands / "mkdir"
            shim.write_text("#!/bin/bash\n"
                'if [[ "$*" == *clawevolve-skills* ]]; then exit 37; fi\n'
                f"exec {shlex.quote(shutil.which('mkdir'))} \"$@\"\n")
        shim.chmod(0o755)
        env["PATH"] = str(commands) + os.pathsep + env["PATH"]
    if failure == "handler":
        runtime = Path(env["SKILL_BASE_DIR"])
        runtime.mkdir()
        (runtime / ".clawevolve-release-version").write_text("clawevolve-20260925-v1")
        (release / "RELEASE_VERSION").write_text(
            "format_version\t1\nrelease_version\tclawevolve-20260925-v1\n"
            "archive_file\tclawevolve-skills-20260925.tar\narchive_sha256\t" + "0" * 64 + "\n")
    args = base64.b64encode(f"--task-id EV-TEST --step-id STEP-TEST --clawweb-url {base}".encode()).decode()
    result = subprocess.run(["bash", str(release / "clawevolve_async_runner.sh"),
        "--stage", "clawevolve-hardening", "--args-base64", args],
        env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "started"
    assert_failure(received, "RUNNER_BOOTSTRAP_FAILED")


@pytest.mark.parametrize("failure", ["environment", "cwd", "exec"])
def test_launcher_reports_all_failures_before_handler_exec(installation, reports, failure):
    release, workspace, env = installation
    base, received = reports
    extra = []
    command = ["true"]
    if failure == "environment":
        env["OPENCLAW_WORKSPACE_DIR"] = str(workspace / "absent")
    elif failure == "cwd":
        extra = ["--run-cwd", str(workspace / "absent")]
    else:
        command = [str(workspace / "missing-handler")]
    result = subprocess.run(["bash", str(release / "clawevolve_task_launcher.sh"),
        "--task-id", "EV-TEST", "--step-id", "STEP-TEST",
        "--clawweb-url", base, "--log-file", str(workspace / "run.log"),
        *extra, "--", *command], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert_failure(received, "OPENCLAW_RUNTIME_MAINTENANCE_FAILED")


@pytest.mark.parametrize("exit_code", [0, 19])
def test_handler_owns_reporting_after_successful_exec(installation, reports, exit_code):
    release, workspace, env = installation
    base, received = reports
    result = subprocess.run(["bash", str(release / "clawevolve_task_launcher.sh"),
        "--task-id", "EV-TEST", "--step-id", "STEP-TEST",
        "--clawweb-url", base, "--log-file", str(workspace / "run.log"),
        "--", "bash", "-c", f"exit {exit_code}"],
        env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == exit_code, result.stderr
    assert received.empty(), "startup reporting must not overwrite business results"


@pytest.mark.parametrize("responses", [[503, 200], [503, 503, 503], [403]])
def test_report_retries_preserve_the_original_exit_status(installation, reports, responses):
    release, workspace, env = installation
    base, received = reports
    for status in responses:
        received.statuses.put(status)
    result = subprocess.run(["bash", str(release / "clawevolve_task_launcher.sh"),
        "--task-id", "EV-TEST", "--step-id", "STEP-TEST",
        "--clawweb-url", base, "--log-file", str(workspace / "run.log"),
        "--", str(workspace / "missing-handler")],
        env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode in {126, 127}, result.stderr
    payloads = [received.get_nowait() for _ in responses]
    assert received.empty()
    assert all(payload == payloads[0] for payload in payloads)
    assert payloads[0][1]["status"] == "failed"
    assert f"status {result.returncode}" in payloads[0][1]["error"]["message"]
    if responses[-1] != 200:
        assert "failure report was not acknowledged" in result.stderr
