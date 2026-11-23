#!/usr/bin/env python3
"""Named chat with an isolated pre-release BCS and an HTTPS JSON-RPC peer."""
import argparse
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import signal
import ssl
import subprocess
import tempfile
import time
from urllib.error import URLError
import uuid

from a2a_story import Peer
from caller_execution_story import free_port
from provider_scope_story import Story


ALIAS = "coverage-fixture"
HEADER = "x-e2e-agent-user"


def peer_tls(root):
    ca, key, cert = (root / name for name in ("ca.pem", "peer.key", "peer.pem"))
    extensions = root / "tls.conf"
    extensions.write_text(
        "[req]\ndistinguished_name=dn\n[dn]\n"
        "[ca]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign\n"
        "[peer]\nbasicConstraints=critical,CA:FALSE\n"
        "subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth\n"
    )

    def openssl(*args):
        subprocess.run(["openssl", *args], cwd=root, check=True, capture_output=True)

    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=Singlebox A2A CA", "-config", str(extensions), "-extensions", "ca",
            "-keyout", "ca.key", "-out", str(ca))
    openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1",
            "-config", str(extensions), "-keyout", str(key), "-out", "peer.csr")
    openssl("x509", "-req", "-in", "peer.csr", "-CA", str(ca), "-CAkey", "ca.key",
            "-CAcreateserial", "-days", "1", "-extfile", str(extensions), "-extensions", "peer",
            "-out", str(cert))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context


@contextmanager
def bcs_server(binary, story, root, config, env):
    path = root / "bcs-config-pre.json"
    (root / "catalog-labels.toml").write_bytes((Path(__file__).resolve().parents[2] / "configs/catalog-labels.toml").read_bytes())
    path.write_text(json.dumps(config))
    log_path = root / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen([binary, "-c", str(path)], cwd=root, env=env,
                                   stdout=log, stderr=log)
        try:
            for _ in range(150):
                assert process.poll() is None, "A2A fixture BCS exited during startup"
                try:
                    story.request("GET", "/health")
                    break
                except (OSError, URLError):
                    time.sleep(0.1)
            else:
                raise AssertionError("A2A fixture BCS startup timed out")
            yield
        except Exception:
            print(log_path.read_text()[-3000:], flush=True)
            raise
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise AssertionError("A2A fixture BCS did not flush coverage")
        assert process.returncode == 0, "A2A fixture BCS exited abnormally"


def run_story(binary, root):
    user_value = uuid.uuid4().hex
    observed = []
    empty_roots = root / "empty-roots"
    empty_roots.mkdir()

    def observe(method, headers):
        values = headers.get_all(HEADER, [])
        observed.append((method, len(values), values == [user_value]))

    peer = Peer("JSONRPC", observe_rpc=observe)
    peer.expect_identity = False  # This isolated server has no application-identity plugin.
    peer.server.socket = peer_tls(root).wrap_socket(peer.server.socket, server_side=True)
    endpoint = peer.base.replace("http://", "https://", 1) + "/a2a"
    peer.card["supportedInterfaces"][0]["url"] = endpoint
    port = free_port()
    story = Story(f"http://127.0.0.1:{port}", "a2atestcoverageowner")
    config = json.loads(subprocess.check_output([binary, "--print-config"]))
    config.update(bind="127.0.0.1", port=port, bcs_endpoint=story.base, store_messages=True,
                  strict_container_validation=False, a2a_allow_anonymous=True,
                  auth={"chain": ["local"], "allow_mock_headers": True, "mock_user_id": story.owner},
                  gateway_principal={"signing_key_env": "A2A_E2E_SIGNING_KEY"},
                  database={"type": "sqlite", "sqlite": {"path": str(root / "bcs.db")}},
                  security={"outbound_url": {"block_private_networks": True, "allow_loopback": True}},
                  secret={"provider": "env", "providers": {"env": {"prefix": "A2A_E2E_"}}},
                  llm={"type": "none"}, bcsfuse={"enabled": False}, bots_base_dir=str(root / "bots"))
    config["logging"].update(console=True, default_level="warn")
    config["session_files"]["backend"] = {"data_dir": str(root / "files")}
    config["collaboration"]["templates"]["base_dir"] = str(root / "templates")
    env = dict(os.environ, SERVER_ENV="pre", BCS_DEBUG="false",
               SSL_CERT_FILE=str(root / "ca.pem"), SSL_CERT_DIR=str(empty_roots),
               A2A_E2E_SIGNING_KEY=uuid.uuid4().hex, A2A_E2E_BCN_GROUP_SESSION_WS_JWT=uuid.uuid4().hex,
               A2A_E2E_FIXTURE_USER=user_value)
    prefix = "/a2a/providers/coverage-provider/bots"
    chat_path = prefix + "/target/chat-async"
    body = {"message": "A2A question fixture", "test_user_ref": ALIAS}

    def register(ref, admin):
        card = copy.deepcopy(peer.card)
        card["name"] = "Coverage " + ref
        registration = {
            "provider_bot_ref": ref, "owners": [story.owner], "agent_card": card,
        }
        return story.request("POST", prefix, registration, token=admin, status=201)

    def terminal(accepted, bearer):
        run_path = "/a2a/chat/runs/" + accepted["run_id"] + "?wait_ms=500"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = story.request("GET", run_path, token=bearer, status=200)
            if result["is_terminal"]:
                return result
        raise AssertionError("A2A fixture run did not reach a terminal state")

    peer.start()
    try:
        with bcs_server(binary, story, root, config, env):
            story.request("POST", "/me/ensure-human", {})
            provider = story.request("POST", "/providers", {
                "slug": "coverage-provider", "name": "A2A test-user coverage",
                "webhook_url": endpoint, "auth": {"mode": "static_bearer"},
                "protocol_version": "2.0", "collaboration_scope": "same_owner",
            })
            admin = provider["provider_admin_token"]
            caller = register("caller", admin)
            target = register("target", admin)
            token = caller["bot_runtime_token"]
            story.request("POST", chat_path, body, token=token, status=403)

        config["a2a_test_user"] = {
            "accept_new": True, "alias": ALIAS, "binding_ref": "coverage-v1",
            "caller_bot_id": caller["bot_id"], "provider_id": provider["provider_id"],
            "target_bot_id": target["bot_id"], "interface_url": endpoint,
            "header_name": HEADER, "user_id_secret": "fixture-user",
        }
        with bcs_server(binary, story, root, config, env):
            policy_path = "/admin/message-delivery/policy"
            current = story.request("GET", policy_path)
            policy = current["policy"]
            policy["flow_enabled"]["direct_a2a"] = True
            policy["bots"][target["bot_id"]] = {
                "mode": "enforce", "max_running": 1, "min_send_interval_ms": 1, "max_queued": 10,
            }
            story.request("PUT", policy_path, {"expected_version": current["version"], "policy": policy})

            other = target["bot_runtime_token"]
            story.request("POST", chat_path, body, token=other, status=403)
            story.request("POST", prefix + "/caller/chat-async", body, token=token, status=403)
            story.request("POST", chat_path, dict(body, test_user_ref="bad alias"), token=token, status=400)
            story.request("POST", chat_path, dict(body, test_user_ref="other-alias"), token=token, status=400)
            story.request("POST", chat_path, dict(body, session_id="ordinary-session"), token=token, status=400)
            assert not observed, "An invalid fixture selection contacted the peer"
            with peer.lock:
                peer.mode = "recover"
            accepted = story.request("POST", chat_path, body, token=token, status=202)
            assert accepted["session_id"] == "a2a-test:" + accepted["run_id"]
            result = terminal(accepted, token)
            assert result["state"] == "completed" and result["response"]["content"] == "A2A-ANSWER-1"
            assert observed == [("SendStreamingMessage", 1, True), ("GetTask", 0, False)], observed

            with peer.lock:
                peer.mode = "stream"
            ordinary_body = {"message": "A2A question ordinary"}
            ordinary = story.request("POST", chat_path, ordinary_body, token=token, status=202)
            assert terminal(ordinary, token)["state"] == "completed"
            assert observed[2:] == [("SendStreamingMessage", 0, False), ("GetTask", 0, False)], observed

            updated_card = copy.deepcopy(peer.card)
            updated_card["name"] = "Coverage target"
            updated_card["supportedInterfaces"][0]["url"] = endpoint + "?changed=1"
            control = f"/providers/{provider['provider_id']}/bots/{target['bot_id']}/a2a"
            story.request("PUT", control, {"agent_card": updated_card}, token=admin)
            previous_calls = len(observed)
            rejected = story.request("POST", chat_path, body, token=token, status=202)
            assert terminal(rejected, token)["state"] == "failed"
            assert len(observed) == previous_calls, "The changed endpoint received a request"
    finally:
        peer.close()
    print(f"A2A test-user E2E: {story.checks} live boundary checks passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="bcs-a2a-test-user-") as directory:
        run_story(str(args.binary.resolve()), Path(directory))
