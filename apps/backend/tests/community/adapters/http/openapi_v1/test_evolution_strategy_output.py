"""Final model output uses the real signed relay and current Bot permission gate."""

import pytest
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from avernet_evolution.core.evolution_strategy.output import (
    StrategyOutput,
    output_artifact_name,
)

from tests.community.adapters.http.openapi_v1.test_evolution_service_wiring import (
    BINDING,
    BOT,
    URL,
    wired,
)

from tests.community.core.bot_genome.example_blobs import SUPPORT_BOT, spec_of
from tests.community.core.bot_genome.service_fixtures import World
from tests.community.core.bot_genome.test_capture import FakeReader


@pytest.mark.asyncio
async def test_strategy_output_relay_and_permission_revocation(tmp_path):
    world = World(tmp_path)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))
    async with wired(tmp_path, world=world) as (client, runtime, calls, collabs, *_):
        collabs.level = PermissionLevel.ADMIN
        assert (
            await client.post(f"/openapi/v1/bots/{BOT}/genome/baseline:initialize")
        ).status_code == 201
        current = await client.get(URL + "/bindings")
        saved = await client.put(
            URL + "/bindings",
            json={"bindings": [BINDING]},
            headers={"If-Match": current.headers["etag"]},
        )
        assert saved.status_code == 200, saved.text
        run = await client.post(
            URL + "/runs",
            json={"binding_id": BINDING["id"]},
            headers={"Idempotency-Key": "output"},
        )
        assert run.status_code == 202, run.text
        run_id = run.json()["data"]["run"]
        path = URL + f"/runs/{run_id}/strategy-output"
        assert (await client.get(path)).json()["data"] == {"output": None}
        output = StrategyOutput(
            attempt=1,
            outcome="no_changes",
            reason="empty_edits",
            rationale="Not enough evidence",
        )
        runtime.store.put_artifact(
            run_id, output_artifact_name(1), output.display_bytes()
        )
        collabs.level = PermissionLevel.MEMBER
        response = await client.get(path)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["data"]["output"] == output.model_dump()
        request, grant = calls[-1]
        assert request.url.path.endswith(f"/runs/{run_id}/strategy-output")
        assert grant.bot.bot_id == runtime.store.get_run(run_id).bot.bot_id
        assert (
            await client.get(URL + "/runs/unknown/strategy-output")
        ).status_code == 404
        before = len(calls)
        collabs.level = PermissionLevel.NONE
        assert (await client.get(path)).status_code == 404
        assert len(calls) == before  # denied before reading protected upstream bytes
