import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("runner_launch", SOURCE / "clawevolve_runner_launch.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def payload(**updates):
    result = dict(schemaVersion="clawevolve.runner-launch.v1", taskId="EV-1", stepId="STEP-1",
                  stage="clawevolve-hardening", invocationId="STEP-1:hitl:HITL-1", runtimeMaintenance=False,
                  args="--task-id EV-1 --step-id STEP-1 --goal '中文 $() 引号 \\\"' ")
    result.update(updates)
    content = json.dumps(result, ensure_ascii=False).encode()
    return content, hashlib.sha256(content).hexdigest()


def test_preserves_exact_unicode_and_quoted_args():
    content, digest = payload()
    assert module.validate_launch(content, digest) == json.loads(content)


@pytest.mark.parametrize("update", [
    {"taskId": "EV-wrong"}, {"stepId": "STEP-wrong"}, {"invocationId": "bad\nID"},
    {"args": "--task-id EV-1 --step-id STEP-1 --step-id STEP-1"},
    {"args": "--task-id EV-1 --step-id STEP-1\nwhoami"},
    {"args": "x" * (64 * 1024 + 1)}, {"args": "'broken"},
    {"runtimeMaintenance": "false"}, {"schemaVersion": "unknown"}, {"stage": "$(whoami)"},
    {"unexpected": "value"},
])
def test_rejects_invalid_identity_or_arguments(update):
    with pytest.raises(ValueError):
        module.validate_launch(*payload(**update))


def test_rejects_tampering_and_oversize():
    content, digest = payload()
    with pytest.raises(ValueError, match="checksum"):
        module.validate_launch(content.replace(b"STEP-1", b"STEP-2"), digest)
    with pytest.raises(ValueError):
        module.validate_launch(b"x" * (module.MAX_BYTES + 1), digest)


@pytest.fixture
def download_server():
    content, digest = payload()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/launch")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(content)

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", content, digest
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_download_and_exec_preserves_args_and_hitl_identity(download_server, tmp_path):
    url, content, digest = download_server
    helper = tmp_path / "clawevolve_runner_launch.py"
    helper.write_bytes((SOURCE / helper.name).read_bytes())
    shutil.copytree(SOURCE.parent / "platform/clawevolve_runtime", tmp_path / "platform/clawevolve_runtime")
    (tmp_path / "clawevolve_async_runner.sh").write_text(
        '#!/bin/bash\nprintf "%s\\n" "$CLAWEVOLVE_RUNTIME_MAINTENANCE" "$@"\n')
    result = subprocess.run([sys.executable, str(helper), url + "/launch", digest], capture_output=True,
                            text=True, env=os.environ | {"SECBAAS_SANDBOX_BACKEND": "local_proc"})
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:5] == ["false", "--stage", "clawevolve-hardening", "--invocation-id", "STEP-1:hitl:HITL-1"]
    assert lines[5] == "--args-base64"
    assert base64.b64decode(lines[6]).decode() == json.loads(content)["args"]


def test_bad_digest_and_redirect_never_enter_runner(download_server, tmp_path):
    url, _, digest = download_server
    env = os.environ | {"SECBAAS_SANDBOX_BACKEND": "local_proc"}
    for address, checksum in ((url + "/launch", "0" * 64), (url + "/redirect", digest)):
        result = subprocess.run(["bash", str(SOURCE / "clawevolve_async_runner.sh"),
                                 "--launch-url", address, "--launch-sha256", checksum],
                                capture_output=True, text=True, env=env)
        assert result.returncode == 2
        assert "frozen runner launch" in result.stderr
        assert not list(tmp_path.iterdir())


def test_frozen_and_inline_arguments_are_exclusive(tmp_path):
    result = subprocess.run(["bash", str(SOURCE / "clawevolve_async_runner.sh"),
                             "--launch-url", "https://example.com/launch", "--launch-sha256", "0" * 64,
                             "--stage", "init"], capture_output=True, text=True,
                            env=os.environ | {"SECBAAS_SANDBOX_BACKEND": "local_proc"})
    assert result.returncode == 2
    assert "cannot be combined" in result.stderr


def test_release_bundles_the_launch_reader():
    assert 'cp "${SCRIPT_DIR}/clawevolve_runner_launch.py" "${RELEASE_BUILD_DIR}/clawevolve_runner_launch.py"' in (
        SOURCE / "package_clawevolve_skills.sh").read_text()


@pytest.fixture(autouse=True)
def local_bot_paths(tmp_path_factory, monkeypatch):
    state = tmp_path_factory.mktemp("runner-bot")
    workspace = state / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state))
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(workspace))
