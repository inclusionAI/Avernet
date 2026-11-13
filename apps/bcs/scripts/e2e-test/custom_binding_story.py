#!/usr/bin/env python3
"""Live custom/gRPC binding delivery; no direct core or DB mutations."""
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
    parser.add_argument("--grpc-peer-binary")
    parser.add_argument("--diagnostics-dir", type=Path)
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    label = "gRPC binding" if args.grpc_peer_binary else "Custom binding"
    peer = Peer("JSONRPC" if args.grpc_peer_binary else "HTTP+JSON")
    peer.start()
    interface_url = "https://grpc-e2e.example/a2a"
    peer.card["supportedInterfaces"] = ([{"protocolBinding": "GRPC", "protocolVersion": "1.0", "url": interface_url}]
        if args.grpc_peer_binary else [{"protocolBinding": "LOCAL_EXAMPLE", "protocolVersion": "1.0", "target": "echo"}])
    grpc_peer = None
    grpc_log = None
    try:
        with tempfile.TemporaryDirectory(prefix="bcs-custom-binding-e2e-") as directory:
            root = Path(directory)
            log_dir = args.diagnostics_dir.resolve() if args.diagnostics_dir else root
            log_dir.mkdir(parents=True, exist_ok=True)
            for name in ("server.log", "grpc-peer.log", "peer-state.json", "network-traces.json"):
                (log_dir / name).unlink(missing_ok=True)
            if args.diagnostics_dir:
                print(f"{label} diagnostics: {log_dir}", flush=True)
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
            server_args = ["--a2a-peer-url", peer.base + "/a2a"]
            if args.grpc_peer_binary:
                ready = root / "grpc-ready"
                grpc_log = (log_dir / "grpc-peer.log").open("w")
                grpc_peer = subprocess.Popen([str(Path(args.grpc_peer_binary).resolve()),
                    "--upstream", peer.base + "/a2a", "--ready-file", str(ready)],
                    stdout=grpc_log, stderr=subprocess.STDOUT)
                for _ in range(150):
                    assert grpc_peer.poll() is None, "gRPC mock peer startup failed"
                    if ready.exists() and ready.read_text():
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError("gRPC mock peer startup timed out")
                config["a2a_grpc"] = {"endpoint": ready.read_text(), "routes": [{
                    "interface_url": interface_url, "target_app": "grpc-e2e",
                    "service_name": "lf.a2a.v1.A2AService"}]}
                server_args = ["--trace-output", str(log_dir / "network-traces.json")]
                config["logging"].setdefault("modules", {})["bcs_a2a_http"] = "debug"
            path = root / "config.json"
            path.write_text(json.dumps(config))
            env = dict(os.environ, SERVER_ENV="local", CUSTOM_E2E_SIGNING_KEY=uuid.uuid4().hex,
                       CUSTOM_E2E_BCN_GROUP_SESSION_WS_JWT=uuid.uuid4().hex)
            story = A2aStory(base, "custombindingowner", peer,
                             cancel_via_workbench=bool(args.grpc_peer_binary))
            with (log_dir / "server.log").open("w") as log:
                process = subprocess.Popen([binary, "-c", str(path), *server_args],
                                           cwd=root, env=env, stdout=log, stderr=log)
                try:
                    for _ in range(150):
                        assert process.poll() is None, label + " BCS startup failed"
                        try:
                            story.request("GET", "/health")
                            break
                        except (OSError, urllib.error.URLError):
                            time.sleep(0.1)
                    else:
                        raise AssertionError(label + " BCS startup timed out")
                    story.run()
                    assert peer.requests and peer.cancels, label + " never received send/cancel"
                    assert peer.card_reads > 0, "URL Card registration/refresh not exercised"
                    assert peer.legacy_requests == 0, label + " fell back to webhook"
                    if args.grpc_peer_binary:
                        assert peer.rpc_methods == {"SendMessage", "SendStreamingMessage", "GetTask", "CancelTask", "SubscribeToTask"}, "gRPC story did not exercise every RPC"
                        assert grpc_peer.poll() is None, "gRPC mock peer exited during delivery"
                    print(f"{label} E2E: {story.checks} HTTP checks; streaming, polling, recovery and cancel passed")
                finally:
                    try:
                        # Capture before cleanup can cancel/delete outstanding work.
                        # Whitelist protocol identities/state; never persist request
                        # bodies, Provider tokens, headers or the generated config.
                        with peer.lock:
                            snapshot = {"mode": peer.mode, "rpc_methods": sorted(peer.rpc_methods),
                                "cancels": list(peer.cancels), "request_count": len(peer.requests),
                                "queried_tasks": sorted(peer.observed_task_queries),
                                "poll_queries": dict(peer.poll_queries),
                                "tasks": {key: value["status"]["state"] for key, value in peer.tasks.items()}}
                        (log_dir / "peer-state.json").write_text(json.dumps(snapshot, indent=2) + "\n")
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
                                raise AssertionError(label + " server did not flush coverage on shutdown")
                    assert process.returncode == 0, label + " server exited abnormally"
    finally:
        try:
            if grpc_peer is not None:
                if grpc_peer.poll() is None:
                    grpc_peer.send_signal(signal.SIGTERM)
                    try:
                        grpc_peer.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        grpc_peer.kill()
                        grpc_peer.wait()
                        raise AssertionError("gRPC mock peer did not shut down")
                assert grpc_peer.returncode == 0, "gRPC mock peer exited abnormally"
        finally:
            try:
                if grpc_log is not None:
                    grpc_log.close()
            finally:
                peer.close()


if __name__ == "__main__":
    main()
