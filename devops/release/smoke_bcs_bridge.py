"""Exercise the real installer against local release archives, without user configuration."""
from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import os
import socket
import sqlite3
import tomllib
import subprocess
import tempfile
import threading
import urllib.request
from pathlib import Path

from bcs_bridge_release import metadata


def smoke(directory: Path, lifecycle: bool) -> None:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:
            if getattr(self.server, "tamper", False) and self.path.endswith(".tar.gz"):
                payload = bytearray((directory.resolve() / Path(self.path).name).read_bytes())
                payload[-1] ^= 1
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                super().do_GET()

    handler = functools.partial(Handler, directory=str(directory.resolve()))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="bridge install smoke ") as temp:
            root = Path(temp)
            install = root / "bin with spaces"
            env = {**os.environ, "BCS_BRIDGE_DOWNLOAD_URL": f"http://127.0.0.1:{server.server_port}",
                   "BCS_BRIDGE_UNMANAGED_INSTALL": str(install), "BCS_BRIDGE_NO_MODIFY_PATH": "1",
                   "BCS_BRIDGE_DISABLE_UPDATE": "1", "PATH": "/usr/bin:/bin"}
            installer = directory.resolve() / "bcs-bridge-installer.sh"
            # No Rust or Homebrew executables are on PATH; install twice to
            # exercise the same fixed-version upgrade/rollback mechanism.
            for _ in range(2):
                subprocess.run(["/bin/sh", str(installer)], env=env, check=True, timeout=60)
                binary = install / "bcs-bridge"
                version = subprocess.check_output([str(binary), "--version"], text=True).strip()
                if version != f"bcs-bridge {metadata()['version']}":
                    raise ValueError(f"Installed binary has wrong version: {version}")
                subprocess.run([str(binary), "--help"], check=True, stdout=subprocess.DEVNULL)
            check_initializer(binary, root)
            before = hashlib.sha256(binary.read_bytes()).hexdigest()
            server.tamper = True
            failure = subprocess.run(["/bin/sh", str(installer)], env=env, timeout=60)
            if failure.returncode == 0 or hashlib.sha256(binary.read_bytes()).hexdigest() != before:
                raise ValueError("Corrupt download was accepted or changed the installed binary")
            if lifecycle:
                check_lifecycle(binary, root)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def check_initializer(binary: Path, root: Path) -> None:
    database = root / "initializer.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("CREATE TABLE chats(id TEXT, ext TEXT, deleted_at INTEGER); CREATE TABLE sub_chats(session_id TEXT, chat_id TEXT);")
    config = root / "initializer.toml"
    settings = root / "settings.json"
    config.write_text('provider_id="release-smoke"\nmode="plugin"\n[plugin]\nurl="ws://127.0.0.1:21000/ws/bot"\n[[bot]]\nengine="qwenwork"\nprovider_bot_ref="worker"\nbot_id="release-smoke-bot"\ntoken="local-smoke-placeholder"\ncwd="/tmp"\n')
    settings.write_text('{"theme":"preserved"}')
    args = [str(binary), "--config", str(config), "init", "--engine", "qwenwork",
            "--option", f"database_path={database}", "--option", f"settings_path={settings}"]
    subprocess.run(args, check=True, timeout=15, stdout=subprocess.DEVNULL)
    first = config.read_bytes()
    subprocess.run(args, check=True, timeout=15, stdout=subprocess.DEVNULL)
    if config.read_bytes() != first:
        raise ValueError("Packaged initializer is not idempotent")
    engine = tomllib.loads(first.decode())["bot"][0]["engine_options"]
    hooks = json.loads(settings.read_text())
    if (engine["secret"] == engine["hook_secret"] or hooks["theme"] != "preserved"
            or "QueryEnd" not in hooks["hooks"]):
        raise ValueError("Packaged initializer lost settings or required credentials/Hooks")


def check_lifecycle(binary: Path, root: Path) -> None:
    # CI-only: refuse to touch a workstation that already has an instance.
    status = subprocess.check_output([str(binary), "status"], text=True)
    if "running" in status.lower() or "starting" in status.lower():
        raise ValueError("Lifecycle smoke test requires an unused bridge instance")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    engine = root / "idle-engine.sh"
    engine.write_text("#!/bin/sh\nexit 0\n")
    engine.chmod(0o700)
    config = root / "bridge.toml"
    # JSON quoting is compatible with TOML basic strings, including spaces.
    config.write_text(
        f'provider_id="release-smoke"\nmode="gateway"\nlisten="127.0.0.1:{port}"\n'
        f'state_path={json.dumps(str(root / "state.sqlite3"))}\n'
        '[[bot]]\nprovider_bot_ref="worker"\nengine="claude-code"\n'
        f'engine_bin={json.dumps(str(engine))}\ncwd={json.dumps(str(root))}\n'
    )
    config.chmod(0o600)
    env = {**os.environ, "BCS_BRIDGE_PROVIDER_TOKENS": json.dumps({"release-smoke": "local-release-smoke"})}
    started = False
    try:
        subprocess.run([str(binary), "--config", str(config), "start", "--daemon"], env=env, check=True, timeout=30)
        started = True
        status = subprocess.check_output([str(binary), "status"], text=True)
        if "running" not in status.lower():
            raise ValueError(f"Bridge did not start: {status}")
        payload = {"id": "smoke-ping", "method": "bot.ping", "to_bot": {"provider_id": "release-smoke", "provider_bot_ref": "worker"}}
        request = urllib.request.Request(f"http://127.0.0.1:{port}/webhook", json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer local-release-smoke"})
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                raise ValueError("Bridge ping failed")
    finally:
        if started:
            subprocess.run([str(binary), "stop"], check=True, timeout=30)
    status = subprocess.check_output([str(binary), "status"], text=True)
    if "running" in status.lower() or "starting" in status.lower():
        raise ValueError("Bridge did not stop")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--lifecycle", action="store_true", help="Use only on an otherwise unused CI runner")
    args = parser.parse_args()
    smoke(args.directory, args.lifecycle)
