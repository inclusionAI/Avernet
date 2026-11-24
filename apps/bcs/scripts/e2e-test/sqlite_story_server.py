"""Isolated production BCS process with disposable SQLite state and graceful profile flush."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import tomllib
import urllib.request
import uuid


@contextmanager
def sqlite_server(binary):
    binary = str(Path(binary).resolve())
    with tempfile.TemporaryDirectory(prefix="bcs-sqlite-story-") as directory:
        root = Path(directory)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        config_file = Path(__file__).resolve().parents[2] / "configs/bcs-config-local.toml"
        config = tomllib.loads(config_file.read_text())
        config.update({"bind": "127.0.0.1", "port": port, "bcs_endpoint": base_url,
                       "strict_container_validation": False,
                       "auth": {"chain": ["local"], "allow_mock_headers": True},
                       "database": {"type": "sqlite", "sqlite": {"path": str(root / "bcs.db")}},
                       "llm": {"type": "none"}, "bcsfuse": {"enabled": False}})
        config["manifest"]["bundles"] = []
        config["secret"] = {"provider": "env", "providers": {"env": {"prefix": "BCS_SECRET_"}}}
        config["bots_base_dir"] = str(root / "bots")
        config["session_files"]["backend"] = {"data_dir": str(root / "files")}
        config["collaboration"]["templates"]["base_dir"] = str(root / "templates")
        config_path = root / "bcs-config-local.json"
        (root / "catalog-labels.toml").write_bytes((Path(__file__).resolve().parents[2] / "configs/catalog-labels.toml").read_bytes())
        config_path.write_text(json.dumps(config))
        env = dict(os.environ, SERVER_ENV="local", BCS_SECRET_BCN_GROUP_SESSION_WS_JWT=uuid.uuid4().hex,
                   AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE=uuid.uuid4().hex)
        # Retain LLVM_PROFILE_FILE so this real process contributes to the same
        # E2E profile pool as Singlebox. Do not execute any Rust unit-test binary.
        with (root / "server.log").open("w") as log:
            process = subprocess.Popen([binary, "-c", str(config_path)], cwd=root,
                                       env=env, stdout=log, stderr=log)
            try:
                for _ in range(150):
                    if process.poll() is not None:
                        raise RuntimeError("BCS exited during story startup")
                    try:
                        with urllib.request.urlopen(base_url + "/health", timeout=2):
                            pass
                        break
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.2)
                else:
                    raise RuntimeError("BCS startup timed out")
                yield base_url, root / "bcs.db", env["AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE"]
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        raise RuntimeError("BCS did not exit gracefully; coverage may be incomplete")
