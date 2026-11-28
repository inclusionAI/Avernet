"""Real signed Backend↔Evolution flow: authorize separately, then explicitly Apply."""

from contextlib import asynccontextmanager

import httpx
import pytest
from agentclaw.community.core.apply_engine.completion import ApplyOutcome
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_genome.models import BotRef, RevisionStatus
from avernet_evolution.adapters.jobs import build_job_app
from avernet_evolution.core.evolution_run.worker import Worker
from avernet_evolution.core.evolution_verify import Split
from avernet_evolution.plugins.job_http import HttpWorkerTransport

from tests.community.adapters.http.openapi_v1.test_evolution_service_wiring import (
    BINDING,
    BOT,
    OWNER,
    RESOURCE,
    SKILL,
    SKILL_STRATEGY,
    URL,
    SkillChecklist,
    _register_skill_fixture,
    wired,
)
from tests.community.core.bot_genome.example_blobs import (
    SUPPORT_BOT,
    SUPPORT_BOT_CANDIDATE,
    spec_of,
)
from tests.community.core.bot_genome.service_fixtures import World
from tests.community.core.bot_genome.test_capture import FakeReader


@asynccontextmanager
async def candidate_ready(tmp_path):
    world = World(tmp_path)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))

    async def register(platform):
        verification = platform.verification
        await _register_skill_fixture(platform)
        platform.verification = verification
        # A deployment with no business validation cases is honestly inconclusive.
        # Regression/safety still use the real snapshot executor; no fabricated grades.
        cases_for = verification.cases_for

        async def without_validation(bot, splits):
            return [
                case
                for case in await cases_for(bot, splits)
                if case.split is not Split.VALIDATION
            ]

        verification.cases_for = without_validation

    async with wired(tmp_path, world=world, extend_platform=register) as parts:
        client, runtime, _, collabs, _bots, _callbacks, _backend = parts
        collabs.level = PermissionLevel.ADMIN
        baseline = await client.post(
            f"/openapi/v1/bots/{BOT}/genome/baseline:initialize"
        )
        assert baseline.status_code == 201, baseline.text
        parent = baseline.json()["data"]["revision"]
        binding = {
            **BINDING,
            "strategy": SKILL_STRATEGY,
            "allowed_genes": [f"skills/{SKILL}"],
        }
        response = await client.put(
            URL + "/bindings", json={"bindings": [binding]}, headers={"If-Match": '""'}
        )
        assert response.status_code == 200, response.text
        response = await client.post(
            URL + "/runs",
            json={"binding_id": binding["id"]},
            headers={"Idempotency-Key": "manual-test-run"},
        )
        assert response.status_code == 202, response.text
        run = response.json()["data"]["run"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(build_job_app(runtime.channel))
        ) as worker_http:
            worker = Worker(
                transport=HttpWorkerTransport("http://job.test", client=worker_http),
                strategies={SKILL_STRATEGY: SkillChecklist},
                worker_id="manual-test-worker",
            )
            assert await worker.run_once(runtime.channel.tokens.issue(worker.worker_id))
            assert worker.last_error is None, worker.last_error
        [candidate] = (await client.get(URL + f"/runs/{run}/candidates")).json()[
            "data"
        ]["candidates"]
        cid = candidate["candidate_id"]
        await runtime.verifier.settle(cid, binding["verification_profile"], run_id=run)
        path = URL + f"/candidates/{cid}"
        report = await client.get(path)
        assert report.status_code == 200, report.text
        yield world, parts, path, report.json()["data"], parent


def authorization_body(report):
    return {
        "revision_id": report["revision_id"],
        "parent_revision_id": report["parent_revision_id"],
        "reason": "Manually reviewed exact Skill diff; test delivery, not a benchmark pass",
    }


@pytest.mark.asyncio
async def test_manual_human_authorization_then_apply_and_lost_reply_recovery(tmp_path):
    async with candidate_ready(tmp_path) as (world, parts, path, report, parent):
        client, runtime, _, _collabs, _bots, _callbacks, _backend = parts
        assert report["verdict"] == "inconclusive", report
        assert report["manual_promotion"] == {"authorization": None, "promotion": None}
        body = authorization_body(report)
        authorized = await client.post(
            path + ":authorize-promotion",
            json=body,
            headers={"Idempotency-Key": "human-one"},
        )
        assert authorized.status_code == 200, authorized.text
        auth = authorized.json()["data"]["authorization"]
        assert (
            auth["basis"] == "human"
            and auth["checks_at_authorization"]["verdict"] == "inconclusive"
        )
        assert not world.applier.starts, "authorization is not Apply"
        bot = BotRef(RESOURCE, BOT, OWNER, "openclaw", "personal", "staff")
        assert world.registry.get_revision(bot, "active").revision.id == parent
        again = await client.post(
            path + ":authorize-promotion",
            json=body,
            headers={"Idempotency-Key": "human-one"},
        )
        assert again.json()["data"]["authorization"] == auth
        conflict = await client.post(
            path + ":authorize-promotion",
            json={**body, "reason": "different"},
            headers={"Idempotency-Key": "human-one"},
        )
        assert conflict.status_code == 409
        duplicate = await client.post(
            path + ":authorize-promotion",
            json=body,
            headers={"Idempotency-Key": "human-two"},
        )
        assert duplicate.status_code == 409
        applied = await client.post(
            path + ":apply", json={"authorization_id": auth["id"]}
        )
        assert applied.status_code == 200, applied.text
        promotion = applied.json()["data"]["promotion"]
        assert promotion["status"] == "applying" and len(world.applier.starts) == 1
        assert world.registry.get_revision(bot, "active").revision.id == parent
        world.settler.settle(
            env="dev",
            entity_id=RESOURCE,
            bot_id=BOT,
            promotion_id=promotion["id"],
            outcome=ApplyOutcome(
                promotion["apply_id"], ApplyStatus.SUCCEEDED, "applied"
            ),
        )
        # Real Bot now has the new content; recovery must precede drift/parent recheck.
        recovered = await client.post(
            path + ":apply", json={"authorization_id": auth["id"]}
        )
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["data"]["promotion"]["status"] == "applied"
        assert len(world.applier.starts) == 1
        refs = {r.name: r.revision for r in world.registry.list_refs(bot)}
        assert refs["active"] == report["revision_id"] and refs["previous"] == parent
        assert (
            world.registry.get_revision(bot, report["revision_id"]).revision.status
            is RevisionStatus.PROMOTED
        )
        final = (await client.get(path)).json()["data"]
        assert (
            final["verdict"] == "inconclusive"
            and final["manual_promotion"]["promotion"]["status"] == "applied"
        )
        evidence = runtime.store.events.for_run(report["run_id"])
        assert sum(e["kind"] == "promotion.human_authorized" for e in evidence) == 1


@pytest.mark.asyncio
async def test_current_privilege_and_live_drift_rechecked_before_apply(tmp_path):
    async with candidate_ready(tmp_path) as (world, parts, path, report, _parent):
        client, _runtime, _, collabs, *_ = parts
        collabs.level = PermissionLevel.MEMBER
        denied = await client.post(
            path + ":authorize-promotion",
            json=authorization_body(report),
            headers={"Idempotency-Key": "human-one"},
        )
        assert denied.status_code in (403, 404)
        collabs.level = PermissionLevel.ADMIN
        authorized = await client.post(
            path + ":authorize-promotion",
            json=authorization_body(report),
            headers={"Idempotency-Key": "human-one"},
        )
        assert authorized.status_code == 200, authorized.text
        auth = authorized.json()["data"]["authorization"]
        collabs.level = PermissionLevel.MEMBER
        assert (
            await client.post(path + ":apply", json={"authorization_id": auth["id"]})
        ).status_code in (403, 404)
        collabs.level = PermissionLevel.ADMIN
        world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT_CANDIDATE))
        refused = await client.post(
            path + ":apply", json={"authorization_id": auth["id"]}
        )
        assert refused.status_code == 409, refused.text
        assert not world.applier.starts


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["authorization", "apply"])
@pytest.mark.parametrize(
    "failure",
    [
        "reject",
        "new_failure",
        "recorded_floor",
        "static_floor",
        "incident",
        "paused",
        "frozen",
        "disabled",
    ],
)
async def test_human_authorization_cannot_override_known_failures(
    tmp_path, monkeypatch, failure, stage
):
    from dataclasses import replace
    from unittest.mock import AsyncMock

    from avernet_evolution.core.evolution_promote.models import FloorCheck
    from avernet_evolution.core.evolution_verify.models import (
        FloorRef,
        SplitResult,
        VerdictStatus,
    )

    async with candidate_ready(tmp_path) as (world, parts, path, report, _parent):
        client, runtime, *_ = parts
        manual = runtime.api.manual
        auth = None
        if stage == "apply":
            authorized = await client.post(
                path + ":authorize-promotion",
                json=authorization_body(report),
                headers={"Idempotency-Key": "before-condition-changed"},
            )
            assert authorized.status_code == 200, authorized.text
            auth = authorized.json()["data"]["authorization"]
        if failure in {"reject", "new_failure", "recorded_floor"}:
            original = manual.checks.verdicts.load(
                report["run_id"],
                report["candidate_id"],
                runtime.store.get_run(report["run_id"]).verification_profile,
            )
            assert original is not None
            changes = (
                {"status": VerdictStatus.REJECT}
                if failure == "reject"
                else {"floor": FloorRef(passed=False)}
                if failure == "recorded_floor"
                else {
                    "splits": {
                        Split.SAFETY: SplitResult(
                            newly_failing=["known-safety-failure"]
                        )
                    }
                }
            )
            monkeypatch.setattr(
                manual.checks.verdicts,
                "load",
                lambda *args: replace(original, **changes),
            )
        elif failure == "static_floor":
            manual.checks.floor = AsyncMock(
                return_value=FloorCheck(result="fail", problems=("locked gene",))
            )
        elif failure == "incident":
            manual.checks.incident = AsyncMock(return_value=True)
        else:
            field = {
                "paused": "paused",
                "frozen": "bot_frozen",
                "disabled": "strategy_disabled",
            }[failure]
            monkeypatch.setattr(manual.kills, field, AsyncMock(return_value=True))
        response = (
            await client.post(path + ":apply", json={"authorization_id": auth["id"]})
            if auth
            else await client.post(
                path + ":authorize-promotion",
                json=authorization_body(report),
                headers={"Idempotency-Key": "known-failure"},
            )
        )
        assert response.status_code == 409, response.text
        assert not world.applier.starts
        assert runtime.store.conn.execute(
            "SELECT COUNT(*) FROM evolution_manual_authorization"
        ).fetchone()[0] == (1 if auth else 0)


@pytest.mark.asyncio
async def test_authorization_audit_failure_rolls_back_and_concurrent_keys_do_not_duplicate(
    tmp_path, monkeypatch
):
    import asyncio

    async with candidate_ready(tmp_path) as (world, parts, path, report, _parent):
        client, runtime, *_ = parts
        original = runtime.store.events.write_human_authorized

        def fail(**kwargs):
            raise RuntimeError("simulated audit persistence failure")

        monkeypatch.setattr(runtime.store.events, "write_human_authorized", fail)
        response = await client.post(
            path + ":authorize-promotion",
            json=authorization_body(report),
            headers={"Idempotency-Key": "audit-failure"},
        )
        assert response.status_code == 500, response.text
        assert (
            runtime.store.conn.execute(
                "SELECT COUNT(*) FROM evolution_manual_authorization"
            ).fetchone()[0]
            == 0
        )
        monkeypatch.setattr(runtime.store.events, "write_human_authorized", original)
        responses = await asyncio.gather(
            *[
                client.post(
                    path + ":authorize-promotion",
                    json=authorization_body(report),
                    headers={"Idempotency-Key": key},
                )
                for key in ["racing-one", "racing-two"]
            ]
        )
        assert sorted(r.status_code for r in responses) == [200, 409]
        assert (
            runtime.store.conn.execute(
                "SELECT COUNT(*) FROM evolution_manual_authorization"
            ).fetchone()[0]
            == 1
        )
        assert not world.applier.starts


@pytest.mark.asyncio
async def test_run_callback_without_manual_purpose_cannot_apply(tmp_path):
    from avernet_evolution.core.evolution_run.data_scope import (
        data_scope,
        run_data_scope,
    )

    async with candidate_ready(tmp_path) as (world, parts, path, report, _parent):
        client, runtime, *_ = parts
        response = await client.post(
            path + ":authorize-promotion",
            json=authorization_body(report),
            headers={"Idempotency-Key": "purpose-bound"},
        )
        assert response.status_code == 200, response.text
        auth = response.json()["data"]["authorization"]
        run = runtime.store.get_run(report["run_id"])
        with data_scope(run_data_scope(run)):
            fields = (
                "id",
                "basis",
                "candidate_id",
                "run_id",
                "revision_id",
                "parent_revision_id",
                "strategy",
                "user_id",
                "reason",
                "authorized_at",
            )
            with pytest.raises(PermissionError):
                await runtime.api.manual.delivery.http.request(
                    run.bot,
                    "POST",
                    "/genome/manual-promotions",
                    body={key: auth[key] for key in fields},
                )
        assert not world.applier.starts


@pytest.mark.asyncio
async def test_failed_apply_requires_new_authorization_and_keeps_original_verdict(
    tmp_path,
):
    async with candidate_ready(tmp_path) as (world, parts, path, report, _parent):
        client, runtime, *_ = parts
        body = authorization_body(report)
        first = await client.post(
            path + ":authorize-promotion",
            json=body,
            headers={"Idempotency-Key": "first-authorization"},
        )
        auth = first.json()["data"]["authorization"]
        world.applier.fail = RuntimeError("synthetic delivery failure")
        failed = await client.post(
            path + ":apply", json={"authorization_id": auth["id"]}
        )
        assert failed.status_code == 200, failed.text
        assert failed.json()["data"]["promotion"]["status"] == "apply_failed"
        world.applier.fail = None
        replay = await client.post(
            path + ":apply", json={"authorization_id": auth["id"]}
        )
        assert replay.json()["data"]["promotion"]["status"] == "apply_failed"
        assert len(world.applier.starts) == 1
        retry = await client.post(
            path + ":authorize-promotion",
            json=body,
            headers={"Idempotency-Key": "second-authorization"},
        )
        assert retry.status_code == 200, retry.text
        second = retry.json()["data"]["authorization"]
        assert second["id"] != auth["id"]
        assert second["checks_at_authorization"]["verdict"] == report["verdict"]
        # Overlapping requests with the same new authorization claim only one Apply.
        import asyncio

        responses = await asyncio.gather(
            *[
                client.post(path + ":apply", json={"authorization_id": second["id"]})
                for _ in range(2)
            ]
        )
        assert all(r.status_code == 200 for r in responses)
        assert all(
            r.json()["data"]["promotion"]["status"] == "applying" for r in responses
        )
        assert len(world.applier.starts) == 2
        final = (await client.get(path)).json()["data"]
        assert final["verdict"] == report["verdict"] == "inconclusive"
        assert (
            sum(
                e["kind"] == "promotion.human_authorized"
                for e in runtime.store.events.for_run(report["run_id"])
            )
            == 2
        )
