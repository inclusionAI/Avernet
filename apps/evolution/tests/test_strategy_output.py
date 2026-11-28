"""Protected output reads, durable worker writes and attempt isolation."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_service import BASE, BINDING, configure

from avernet_evolution.bootstrap import create_app
from avernet_evolution.composition import compose
from avernet_evolution.core.evolution_run.errors import RunNotFound
from avernet_evolution.core.evolution_strategy.output import StrategyOutput, output_artifact_name
from avernet_evolution.local import BOT, STRATEGY, build_local_platform


def output(attempt=1):
    return StrategyOutput(
        attempt=attempt, outcome="no_changes", reason="empty_edits", rationale="Insufficient evidence"
    )


@pytest.mark.asyncio
async def test_worker_output_persists_across_restart_and_checks_bot_ownership(settings):
    runtime = await compose(settings, await build_local_platform(settings))
    await runtime.api.put_bindings(BOT, [BINDING], expected_etag=None, actor=BOT.owner_id)
    run_id = (
        await runtime.api.submit_run(
            BOT, {"binding_id": "identity", "idempotency_key": "output"}, actor=BOT.owner_id
        )
    )["run"]
    claim = await runtime.runner.jobs.claim("output-worker", [STRATEGY])
    assert await runtime.api.strategy_output(BOT, run_id) == {"output": None}
    await runtime.channel.artifacts_put(
        claim.job.id, claim.job.fencing_token, output_artifact_name(1), output().display_bytes()
    )
    await runtime.close()
    runtime = await compose(settings, await build_local_platform(settings))
    try:
        assert (await runtime.api.strategy_output(BOT, run_id))["output"] == output().model_dump()
        for other in (replace(BOT, bot_id="other"), replace(BOT, owner_id="other")):
            with pytest.raises(RunNotFound):
                await runtime.api.strategy_output(other, run_id)
        runtime.store.conn.execute("UPDATE evolution_run SET attempt=2 WHERE run_id=?", (run_id,))
        runtime.store.conn.commit()
        assert await runtime.api.strategy_output(BOT, run_id) == {"output": None}
        # Unrelated artifacts and past attempts must not appear as current output.
        runtime.store.put_artifact(run_id, "private-other-artifact", b"do not display")
        runtime.store.put_artifact(run_id, output_artifact_name(2), output(2).display_bytes())
        assert (await runtime.api.strategy_output(BOT, run_id))["output"]["attempt"] == 2
    finally:
        await runtime.close()


def test_http_requires_authorization_and_handles_missing_historical_output(settings):
    with TestClient(create_app(settings)) as client:
        configure(client, settings)
        run_id = client.post(
            BASE + "/runs", json={"binding_id": "identity"}, headers={"Idempotency-Key": "output-http"}
        ).json()["data"]["run"]
        path = BASE + f"/runs/{run_id}/strategy-output"
        assert client.get(path).json()["data"] == {"output": None}
        runtime = client.app.state.runtime

        async def save():
            runtime.store.put_artifact(run_id, output_artifact_name(1), output().display_bytes())

        client.portal.call(save)
        response = client.get(path)
        assert response.status_code == 200
        assert response.json()["data"]["output"]["rationale"] == "Insufficient evidence"
        assert client.get(BASE + "/runs/missing/strategy-output").status_code == 404
        assert client.get(path.replace(BOT.bot_id, "other")).status_code == 403
        client.headers.clear()
        assert client.get(path).status_code == 403


def test_display_bounds_are_preserved_after_redaction_expands_short_secrets():
    from avernet_evolution.core.evolution_strategy.output import MAX_RAW_OUTPUT, ProposedEdit

    raw = "token=x " * (MAX_RAW_OUTPUT // 8)
    item = StrategyOutput(
        attempt=1,
        outcome="changes_proposed",
        reason="changed_markdown",
        rationale="token=x " * 250,
        edits=[ProposedEdit(path="a" * 1016 + " token=x", content="token=x " * 3000, changed=True)],
        raw_output=raw,
    )
    displayed = StrategyOutput.model_validate_json(item.display_bytes())
    assert displayed.raw_output_truncated
    assert len(displayed.raw_output) <= MAX_RAW_OUTPUT
    assert len(displayed.rationale) <= 2000
    assert len(displayed.edits[0].path) <= 1024
    assert len(displayed.edits[0].content) <= 24_000
    assert "token=x" not in displayed.model_dump_json()
    assert item.raw_output == raw  # display-only transformation
