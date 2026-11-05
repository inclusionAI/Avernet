#!/usr/bin/env python3
"""Live custom-binding admission and delivery; no direct core or DB mutations."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import urllib.error
import uuid
from a2a_story import A2aStory, Peer
from caller_execution_story import free_port


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    peer = Peer()
    peer.start()
    peer.card["supportedInterfaces"] = [{"protocolBinding": "LOCAL_EXAMPLE", "protocolVersion": "1.0", "target": "echo"}]
    try:
        with tempfile.TemporaryDirectory(prefix="bcs-custom-binding-e2e-") as directory:
            root = Path(directory)
            base = f"http://127.0.0.1:{free_port()}"
            config = json.loads(subprocess.check_output([binary, "--print-config"]))
            config.update(bind="127.0.0.1", port=int(base.rsplit(":", 1)[1]), bcs_endpoint=base,
                store_messages=True, strict_container_validation=False, a2a_allow_anonymous=True,
                auth={"chain": ["local"], "allow_mock_headers": True, "mock_user_id": "custombindingowner"},
                gateway_principal={"signing_key_env": "CUSTOM_E2E_SIGNING_KEY"},
                database={"type": "sqlite", "sqlite": {"path": str(root / "bcs.db")}},
                security={"outbound_url": {"block_private_networks": True, "allow_loopback": True}},
                llm={"type": "none"}, bcsfuse={"enabled": False}, bots_base_dir=str(root / "bots"))
            config["logging"].update(console=True, default_level="warn")
            config["secret"] = {"provider": "env", "providers": {"env": {"prefix": "CUSTOM_E2E_"}}}
            config["session_files"]["backend"] = {"data_dir": str(root / "files")}
            config["collaboration"]["templates"]["base_dir"] = str(root / "templates")
            path = root / "config.json"
            path.write_text(json.dumps(config))
            env = dict(os.environ, SERVER_ENV="local", CUSTOM_E2E_SIGNING_KEY=uuid.uuid4().hex,
                       CUSTOM_E2E_BCN_GROUP_SESSION_WS_JWT=uuid.uuid4().hex)
            story = A2aStory(base, "custombindingowner", peer)
            with (root / "server.log").open("w") as log:
                process = subprocess.Popen([binary, "-c", str(path), "--a2a-peer-url", peer.base + "/a2a"],
                                           cwd=root, env=env, stdout=log, stderr=log)
                try:
                    for _ in range(150):
                        assert process.poll() is None, "Custom binding BCS startup failed"
                        try:
                            story.request("GET", "/health")
                            break
                        except (OSError, urllib.error.URLError):
                            time.sleep(0.1)
                    else:
                        raise AssertionError("Custom binding BCS startup timed out")
                    story.run()
                    assert peer.requests and peer.cancels, "Custom plugin never received send/cancel"
                    assert peer.card_reads > 0, "URL Card registration/refresh not exercised"
                    assert peer.legacy_requests == 0, "Custom binding fell back to webhook"
                    print(f"Custom binding E2E: {story.checks} HTTP checks; streaming, polling, recovery and cancel passed")
                finally:
                    try:
                        if process.poll() is None:
                            story.cleanup()
                    finally:
                        if process.poll() is None:
                            process.send_signal(signal.SIGTERM)
                            try:
                                process.wait(timeout=30)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
                                raise AssertionError("Custom binding server did not flush coverage on shutdown")
                    assert process.returncode == 0, "Custom binding server exited abnormally"
    finally:
        peer.close()


if __name__ == "__main__":
    main()
