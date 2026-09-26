import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INITIALIZER = ROOT / "clawevolve_message_initializer.sh"


class _ReportHandler(BaseHTTPRequestHandler):
    reports: list[dict] = []

    def do_POST(self):  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        self.__class__.reports.append(json.loads(self.rfile.read(size)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *_args):
        return


def _run(tmp_path: Path, runner_body: str, *extra_args: str):
    release = tmp_path / "release"
    release.mkdir()
    shutil.copy2(INITIALIZER, release / INITIALIZER.name)
    runner = release / "clawevolve_async_runner.sh"
    runner.write_text(runner_body, encoding="utf-8")
    runner.chmod(0o755)
    launcher = release / "clawevolve_task_launcher.sh"
    launcher.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        "while [[ $# -gt 0 ]]; do [[ \"$1\" == -- ]] && { shift; break; }; shift; [[ $# -gt 0 && \"$1\" != --* ]] && shift || true; done\n"
        "exec \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_id = fake_bin / "id"
    fake_id.write_text(
        "#!/usr/bin/env bash\n[[ \"${1:-}\" == '-u' ]] && { echo 1000; exit; }\n"
        "[[ \"${1:-}\" == '-un' ]] && { echo admin; exit; }\nexec /usr/bin/id \"$@\"\n",
        encoding="utf-8",
    )
    fake_id.chmod(0o755)
    fake_setsid = fake_bin / "setsid"
    fake_setsid.write_text("#!/usr/bin/env bash\nexec \"$@\"\n", encoding="utf-8")
    fake_setsid.chmod(0o755)
    _ReportHandler.reports = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ReportHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CLAWEVOLVE_RESULTS_ROOT": str(tmp_path / "results"),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(name, None)
        result = subprocess.run(
            [
                "bash", str(release / INITIALIZER.name),
                "--task-id", "EV-1", "--step-id", "STEP-1",
                "--clawweb-url", f"http://127.0.0.1:{server.server_port}",
                *extra_args,
            ],
            text=True,
            capture_output=True,
            env=env,
            timeout=15,
        )
        if result.returncode == 0:
            deadline = time.monotonic() + 10
            while len(_ReportHandler.reports) < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
    finally:
        server.shutdown()
        thread.join(timeout=5)
    return result, list(_ReportHandler.reports)


def test_initializer_reports_running_and_succeeded(tmp_path: Path):
    result, reports = _run(
        tmp_path,
        "#!/usr/bin/env bash\nprintf '%s\\n' '{\"ok\":true,\"status\":\"initialized\",\"result\":\"installed\",\"release_version\":\"clawevolve-20260820-v1\"}'\n",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "started"
    assert [item["status"] for item in reports] == ["running", "succeeded"]
    assert reports[-1]["output"]["releaseVersion"] == "clawevolve-20260820-v1"
    assert reports[-1]["output"]["user"] == "admin"
    assert (tmp_path / "results/EV-1/skill-init/output/clawevolve-skill-init.log").is_file()


def test_initializer_reports_runner_failure(tmp_path: Path):
    result, reports = _run(
        tmp_path,
        "#!/usr/bin/env bash\nprintf '%s\\n' '{\"ok\":false,\"error\":\"manifest missing\"}' >&2\nexit 1\n",
    )
    assert result.returncode == 1
    assert [item["status"] for item in reports] == ["running", "failed"]
    assert reports[-1]["error"]["code"] == "ARCA_SKILL_INIT_FAILED"
    assert "manifest missing" in reports[-1]["error"]["message"]


def test_initializer_rejects_unknown_arguments(tmp_path: Path):
    result, reports = _run(
        tmp_path,
        "#!/usr/bin/env bash\nexit 0\n",
        "--api-key", "must-not-be-accepted",
    )
    assert result.returncode == 2
    assert reports == []
    assert "unknown argument" in result.stderr


def test_initializer_rejects_invalid_runtime_maintenance(tmp_path: Path):
    result, reports = _run(
        tmp_path,
        "#!/usr/bin/env bash\nexit 0\n",
        "--runtime-maintenance", "sometimes",
    )
    assert result.returncode == 2
    assert reports == []
    assert "runtime-maintenance must be true or false" in result.stderr
