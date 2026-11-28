"""Consumer contracts: job capabilities, Bot-scoped experience and verdict ownership."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from avernet_evolution.adapters.jobs import build_job_app
from avernet_evolution.core.evolution_run import (
    InProcessHost,
    JobChannel,
    JobUnauthorized,
    RunStatus,
    TriggerRecord,
    Worker,
    build_run_context,
)
from avernet_evolution.core.evolution_strategy import CapabilityNotGranted
from avernet_evolution.core.evolution_strategy.strategies.identity_hardening import (
    IdentityHardening,
)

from .conftest import BOT
from .test_worker import BINDING, REF, TOKEN_ISSUER, WorkspaceOnlyStrategy


async def _grant(services, channel, bot=BOT):
    await services.bindings.put_bindings(bot, [BINDING], expected_etag=None, actor=bot.owner_id)
    await services.runs.submit(bot, BINDING.id, idempotency_key="job", trigger=TriggerRecord(kind="manual"))
    grant = await channel.claim("worker", TOKEN_ISSUER.issue("worker"), [REF])
    return grant["job_id"], grant["fencing_token"]


def _channel(services, **kwargs):
    return JobChannel(
        store=services.store,
        jobs=services.jobs,
        genome=services.genome,
        tokens=TOKEN_ISSUER,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_experience_factory_uses_authenticated_bot_and_propagates_failure(
    services,
):
    class Factory:
        def __init__(self):
            self.calls = []

        def for_bot(self, bot):
            self.calls.append(bot)
            return SimpleNamespace(sessions=AsyncMock(return_value=[{"bot": bot.bot_id}]))

    factory = Factory()
    channel = _channel(services, experience_factory=factory)
    for bot in (
        BOT,
        replace(BOT, bot_id="other", entity_id="other-entity", owner_id="other-owner"),
    ):
        args = await _grant(services, channel, bot)
        assert await channel.experience_sessions(*args, days=7) == [{"bot": bot.bot_id}]
        await channel.complete(*args, {})
    assert factory.calls == [
        BOT,
        replace(BOT, bot_id="other", entity_id="other-entity", owner_id="other-owner"),
    ]

    def broken(bot):
        raise OSError("experience storage unavailable")

    factory.for_bot = broken
    # New run, no cross-Bot fallback when a configured provider fails.
    args = await _grant(services, channel, replace(BOT, bot_id="third"))
    with pytest.raises(OSError, match="unavailable"):
        await channel.experience_sessions(*args, days=7)


@pytest.mark.asyncio
async def test_raw_http_cannot_bypass_granted_capabilities(services):
    provider = SimpleNamespace(feedback=AsyncMock(return_value=[]))
    channel = _channel(services, experience=provider)
    job, fence = await _grant(services, channel)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_job_app(channel)),
        base_url="http://platform.test",
    ) as client:
        response = await client.post(
            f"/jobs/{job}/experience/feedback", json={"fencing_token": fence, "days": 7}
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "capability_not_granted"
    provider.feedback.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_verifier", [False, True])
async def test_verdict_rejects_unknown_or_foreign_candidate(services, with_verifier):
    verifier = SimpleNamespace(strategy_view=AsyncMock()) if with_verifier else None
    channel = _channel(services, verifier=verifier)
    args = await _grant(services, channel)
    run = services.store.get_run(
        services.store.conn.execute("SELECT run_id FROM evolution_job WHERE job_id=?", (args[0],)).fetchone()[
            0
        ]
    )
    # A candidate exists, but on another run.
    services.store.reserve_candidate("foreign-run", "foreign-candidate")
    for candidate_id in ("missing", "foreign-candidate"):
        with pytest.raises(JobUnauthorized):
            await channel.verdict(*args, candidate_id)
    if verifier:
        verifier.strategy_view.assert_not_called()
    ctx = await build_run_context(
        run,
        store=services.store,
        genome=services.genome,
        budget=services.budget,
        operations=services.operations,
    )
    with pytest.raises(KeyError, match="this run"):
        await ctx.candidates.verdict("foreign-candidate")


@pytest.mark.asyncio
async def test_pending_verdict_carries_committed_revision(services):
    channel = _channel(services)
    args = await _grant(services, channel)
    run_id = services.store.conn.execute(
        "SELECT run_id FROM evolution_job WHERE job_id=?", (args[0],)
    ).fetchone()[0]
    services.store.reserve_candidate(run_id, "candidate")
    services.store.record_candidate_revision(run_id, "candidate", "sha256:revision")
    verdict = await channel.verdict(*args, "candidate")
    assert verdict["status"] == "pending" and verdict["revision"] == "sha256:revision"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["worker", "in_process"])
async def test_strategy_function_factory_is_invoked(services, mode):
    called = []

    def factory():
        called.append(True)
        return WorkspaceOnlyStrategy()

    channel = _channel(services)
    await services.bindings.put_bindings(BOT, [BINDING], expected_etag=None, actor=BOT.owner_id)
    run_id = await services.runs.submit(
        BOT, BINDING.id, idempotency_key="factory", trigger=TriggerRecord(kind="manual")
    )
    if mode == "worker":
        host = Worker(transport=channel, strategies={REF: factory}, worker_id="worker")
        await host.run_once(TOKEN_ISSUER.issue("worker"))
    else:

        def ctx_factory(run, **kwargs):
            return build_run_context(
                run,
                store=services.store,
                genome=services.genome,
                budget=services.budget,
                operations=services.operations,
                **kwargs,
            )

        host = InProcessHost(
            strategies={REF: factory},
            store=services.store,
            jobs=services.jobs,
            operations=services.operations,
            budget=services.budget,
            genome=services.genome,
            ctx_factory=ctx_factory,
        )
        await host.run_once("worker")
    assert called == [True]
    assert (await services.runs.get(BOT, run_id)).status is RunStatus.COMPLETED


def test_http_dictionary_episodes_preserve_evidence():
    assert IdentityHardening._evidence(
        [
            {"episode_id": "ep-http"},
            SimpleNamespace(episode_id="ep-typed"),
            {"episode_id": None},
            {},
            {"episode_id": 3},
        ]
    ) == ("episode:ep-http", "episode:ep-typed")


@pytest.mark.asyncio
async def test_in_process_capability_check_precedes_provider(services):
    channel = _channel(services)
    args = await _grant(services, channel)
    run_id = services.store.conn.execute(
        "SELECT run_id FROM evolution_job WHERE job_id=?", (args[0],)
    ).fetchone()[0]
    provider = SimpleNamespace(feedback=AsyncMock(return_value=[]))
    ctx = await build_run_context(
        services.store.get_run(run_id),
        store=services.store,
        genome=services.genome,
        budget=services.budget,
        operations=services.operations,
        experience_provider=provider,
    )
    with pytest.raises(CapabilityNotGranted):
        await ctx.experience.feedback(days=7)
    provider.feedback.assert_not_called()


@pytest.mark.asyncio
async def test_submit_response_is_scoped_to_current_run(services):
    from avernet_genome.patch_models import GenomePatch

    from avernet_evolution.core.evolution_strategy import Candidate, candidate_id_of

    channel = _channel(services)
    job, fence = await _grant(services, channel)
    patch = GenomePatch.model_validate(
        {
            "patch_schema": 1,
            "base": services.genome.revision_id,
            "ops": [{"op": "persona.add", "type": "SOUL.md", "content": "Be clear."}],
            "rationale": "clarify",
            "evidence": ["episode:fixture"],
        }
    )
    candidate_id = candidate_id_of(
        Candidate(patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence))
    )
    services.store.reserve_candidate("foreign-run", candidate_id)
    services.store.record_candidate_revision("foreign-run", candidate_id, "sha256:foreign")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_job_app(channel)),
        base_url="http://platform.test",
    ) as client:
        response = await client.post(
            f"/jobs/{job}/candidates",
            json={"fencing_token": fence, "patch": patch.model_dump(mode="json")},
        )
    assert response.status_code == 200
    assert response.json() == {
        "candidate_id": candidate_id,
        "revision": "sha256:" + "b" * 64,
    }
    assert services.store.candidates("foreign-run")[0].revision == "sha256:foreign"


@pytest.mark.asyncio
@pytest.mark.parametrize("stored,expected", [("reject", "reject"), ("accept", "pending")])
async def test_no_verifier_preserves_rejection_but_never_fabricates_acceptance(services, stored, expected):
    channel = _channel(services)
    args = await _grant(services, channel)
    run_id = services.store.conn.execute(
        "SELECT run_id FROM evolution_job WHERE job_id=?", (args[0],)
    ).fetchone()[0]
    services.store.upsert_candidate(run_id, "candidate", "", seq=1, verdict=stored)
    assert (await channel.verdict(*args, "candidate"))["status"] == expected
    ctx = await build_run_context(
        services.store.get_run(run_id),
        store=services.store,
        genome=services.genome,
        budget=services.budget,
        operations=services.operations,
    )
    assert (await ctx.candidates.verdict("candidate")).status.value == expected
