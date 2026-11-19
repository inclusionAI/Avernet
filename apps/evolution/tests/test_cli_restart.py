"""Real local CLI/HTTP process replacement, not a real Bot or remote sandbox."""

import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

import httpx
from test_service import BASE, BINDING, client_headers

from avernet_evolution.core.evolution_run.job_protocol import JobTokenIssuer
from avernet_evolution.local import BOT, STRATEGY


def eventually(check, *, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError("local CLI did not reach expected state")


def test_cli_hard_restart_recovers_expired_claim_without_backend(settings, tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    env = {k: v for k, v in os.environ.items() if not k.startswith("EVOLUTION_")}
    env.update(
        {
            "EVOLUTION_DATABASE": str(settings.database),
            "EVOLUTION_SIGNING_KEY_FILE": str(settings.signing_key_file),
            "EVOLUTION_LOCAL_TOKEN_FILE": str(settings.local_token_file),
            "EVOLUTION_PORT": str(port),
            "EVOLUTION_LEASE_SECONDS": "3",
            "EVOLUTION_HEARTBEAT_S": "1",
            "EVOLUTION_POLL_INTERVAL_S": "0.1",
        }
    )
    # When run from the standalone installed package, no PYTHONPATH is needed.
    # Compatibility test runs may supply only absolute source paths instead.
    if env.get("PYTHONPATH"):
        from pathlib import Path

        env["PYTHONPATH"] = os.pathsep.join(
            str(Path(p).resolve()) for p in env["PYTHONPATH"].split(os.pathsep)
        )

    @contextmanager
    def service(consumer):
        with (tmp_path / f"cli-{consumer}.log").open("wb") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "avernet_evolution.bootstrap", "serve"],
                env=env | {"EVOLUTION_CONSUMER": str(consumer).lower()},
                stdout=log,
                stderr=log,
            )
            try:
                eventually(lambda: httpx.get(endpoint + "/healthz", timeout=1).status_code == 200)
                yield process
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    with httpx.Client(
        base_url=endpoint, headers=client_headers(settings), params={"entity_id": BOT.owner_id}, timeout=5
    ) as client:
        with service(False) as process:
            tag = client.get(BASE + "/bindings").headers["etag"]
            assert (
                client.put(
                    BASE + "/bindings", json={"bindings": [BINDING]}, headers={"If-Match": tag}
                ).status_code
                == 200
            )
            run_id = client.post(
                BASE + "/runs", json={"binding_id": "identity"}, headers={"Idempotency-Key": "hard-restart"}
            ).json()["data"]["run"]
            prefix = "/openapi/v1/evolution/jobs"
            claim = client.post(
                prefix + "/jobs:claim",
                json={
                    "worker_id": "abandoned-worker",
                    "strategy_refs": [STRATEGY],
                    "token": JobTokenIssuer(settings.signing_key()).issue("abandoned-worker"),
                },
            ).json()
            process.kill()  # No graceful shutdown; the lease stays in persistent storage.
            process.wait(timeout=10)
        with service(True):

            def completed():
                run = client.get(BASE + f"/runs/{run_id}").json()["data"]["run"]
                return run if run["status"] == "completed" else None

            run = eventually(completed)
            assert run["attempt"] == 2 and run["candidate_count"] == 1
            evidence = client.get(BASE + f"/runs/{run_id}/evidence").json()["data"]
            assert [job["attempt"] for job in evidence["jobs"]] == [1, 2]
            assert (
                client.post(
                    prefix + f"/jobs/{claim['job_id']}:complete",
                    json={
                        "fencing_token": claim["fencing_token"],
                        "summary": {},
                    },
                ).status_code
                == 401
            )
            candidates = client.get(BASE + f"/runs/{run_id}/candidates").json()["data"]["candidates"]
            assert len(candidates) == 1 and candidates[0]["verdict"] == "pending"
