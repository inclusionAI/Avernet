"""Deployment constraints and consumer-facing plugin failures."""

import ast
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_service import BASE, configure

from avernet_evolution.bootstrap import create_app
from avernet_evolution.composition import compose
from avernet_evolution.config import Settings
from avernet_evolution.local import STRATEGY, build_local_platform
from avernet_evolution.process_lock import ProcessLock

ROOT = Path(__file__).resolve().parents[1]


def test_no_backend_dependency_and_transport_free_core():
    for base in (ROOT / "src", ROOT / "tests", ROOT.parents[1] / "packages/genome-contracts/src"):
        for path in base.rglob("*.py"):
            assert len(path.read_text().splitlines()) <= 1000, path
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    imports = [node.module or ""]
                elif isinstance(node, ast.Import):
                    imports = [item.name for item in node.names]
                else:
                    continue
                assert not any(i.startswith("agentclaw") for i in imports), path
                if path.is_relative_to(ROOT / "src/avernet_evolution/core"):
                    assert not any(
                        i.split(".")[0] in {"fastapi", "starlette", "httpx", "aiohttp", "os"} for i in imports
                    ), path


def test_process_lock_rejects_second_authority_and_releases(settings):
    lock = ProcessLock(settings.database)
    try:
        with pytest.raises(RuntimeError, match="another Evolution process"):
            ProcessLock(settings.database)
    finally:
        lock.close()
    ProcessLock(settings.database).close()


def test_production_has_no_permissive_local_fallback(settings):
    values = settings.model_dump()
    for overrides in (
        {"database": Path(":memory:")},
        {"host": "0.0.0.0"},
        {"profile": "production"},
        {"heartbeat_s": 40},
        {"consumre": False},
    ):
        with pytest.raises(ValueError):
            Settings(**(values | overrides))


@pytest.mark.asyncio
async def test_production_rejects_unisolated_provider(settings):
    platform = await build_local_platform(settings)
    try:
        production = settings.model_copy(
            update={"profile": "production", "platform_factory": "platform:factory"}
        )
        with pytest.raises(ValueError, match="isolated sandbox"):
            await compose(production, platform)
    finally:
        await platform.close()


@pytest.mark.asyncio
async def test_strategy_version_cannot_change_on_restart(settings):
    runtime = await compose(settings, await build_local_platform(settings))
    await runtime.close()
    platform = await build_local_platform(settings)
    original = await platform.registry.list()
    changed = replace(
        original[0], registration=replace(original[0].registration, params_schema={"type": "object"})
    )
    # Simulate trusted deployment loading changed metadata under the same version.
    platform.registry._records[(changed.registration.id, changed.registration.version)] = changed
    try:
        with pytest.raises(ValueError, match="immutable deployment identity"):
            await compose(settings, platform)
    finally:
        await platform.close()


def test_worker_http_contract_claim_auth_and_metadata(settings):
    prefix = "/openapi/v1/evolution/jobs"
    with TestClient(create_app(settings)) as client:
        configure(client, settings)
        run_id = client.post(
            BASE + "/runs", json={"binding_id": "identity"}, headers={"Idempotency-Key": "worker"}
        ).json()["data"]["run"]
        runtime = client.app.state.runtime
        token = runtime.channel.tokens.issue("worker-http")
        assert client.post(prefix + "/jobs:claim", json={}).status_code == 422
        assert (
            client.post(
                prefix + "/jobs:claim",
                json={"worker_id": "worker-http", "token": "invalid", "strategy_refs": [STRATEGY]},
            ).status_code
            == 401
        )
        grant = client.post(
            prefix + "/jobs:claim",
            json={"worker_id": "worker-http", "token": token, "strategy_refs": [STRATEGY]},
        )
        assert grant.status_code == 200, grant.text
        payload = grant.json()
        job_id, fence = payload["job_id"], payload["fencing_token"]
        assert (
            client.post(prefix + f"/jobs/{job_id}/files", json={"fencing_token": "wrong"}).status_code == 401
        )
        files = client.post(prefix + f"/jobs/{job_id}/files", json={"fencing_token": fence})
        assert files.status_code == 200 and files.json()["files"]
        assert (
            client.post(
                prefix + f"/jobs/{job_id}:complete",
                json={"fencing_token": fence, "summary": {"summary": "fixture-worker"}},
            ).status_code
            == 204
        )
        assert client.get(BASE + f"/runs/{run_id}").json()["data"]["run"]["status"] == "completed"


@pytest.mark.asyncio
async def test_runner_failure_is_observable_without_logging_provider_secrets(caplog):
    from avernet_evolution.core.evolution_run.runner import EvolutionRunner

    runner = EvolutionRunner(host=None, scheduler=None, poll_interval_s=0.01)

    async def fail_tick():
        runner._stop.set()
        raise RuntimeError("private-provider-credential-must-not-be-logged")

    runner.tick = fail_tick
    await runner._loop()
    assert "evolution.runner.tick_failed error_type=RuntimeError" in caplog.text
    assert "private-provider-credential" not in caplog.text


def test_environment_config_rejects_unknown_keys(settings, monkeypatch):
    import os

    for name in os.environ:
        if name.startswith("EVOLUTION_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("EVOLUTION_DATABASE", str(settings.database))
    monkeypatch.setenv("EVOLUTION_SIGNING_KEY_FILE", str(settings.signing_key_file))
    monkeypatch.setenv("EVOLUTION_LOCAL_TOKEN_FILE", str(settings.local_token_file))
    monkeypatch.setenv("EVOLUTION_CONSUMER", "false")
    assert Settings.from_env().consumer is False
    monkeypatch.setenv("EVOLUTION_CONSUMRE", "false")
    with pytest.raises(ValueError, match="extra_forbidden"):
        Settings.from_env()
