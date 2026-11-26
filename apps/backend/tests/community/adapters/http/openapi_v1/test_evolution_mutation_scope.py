"""Opt-in generic RSI: Backend/worker/verification/review seams, no live model or Apply."""

import json

import httpx
import pytest
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from avernet_evolution.adapters.backend_access import fingerprint
from avernet_evolution.adapters.backend_data import HEADER as DATA_HEADER
from avernet_evolution.adapters.backend_data import issue_data_proof
from avernet_evolution.adapters.jobs import build_job_app
from avernet_evolution.core.evolution_run.data_scope import run_data_scope
from avernet_evolution.core.evolution_run.worker import Worker
from avernet_evolution.core.evolution_strategy import Completion, registration_from_json
from avernet_evolution.core.evolution_strategy.strategies import BehaviorRefinement
from avernet_evolution.core.evolution_verify import (
    Case,
    CaseOrigin,
    CaseOriginKind,
    ExecutorKind,
    Grade,
    JudgingSpec,
    RolloutRecord,
    Split,
)
from avernet_evolution.plugins.job_http import HttpWorkerTransport
from avernet_evolution.plugins.verifiers.composed import (
    VerificationMethod,
    build_composed_verifier,
    markdown_secret_scan,
)
from avernet_genome.models import BotRef

from tests.community.adapters.http.openapi_v1.test_evolution_service_wiring import (
    BINDING,
    BOT,
    KEY,
    OWNER,
    RESOURCE,
    SKILL_MD,
    URL,
    wired,
)
from tests.community.core.bot_genome.example_blobs import SUPPORT_BOT, spec_of
from tests.community.core.bot_genome.service_fixtures import World
from tests.community.core.bot_genome.test_capture import FakeReader

STRATEGY = "fixture/behavior-refinement@1.0.0"
PROFILE = "fixture-composed@1"
MARKER = "Before responding, cite the supporting session evidence."
SELECTORS = {
    "persona": {"gene": "persona", "include": ["SOUL.md"], "operations": ["update"]},
    "skills": {
        "gene": "skills",
        "origin": "local",
        "include": "all",
        "operations": ["update"],
    },
}


class FixtureModel:
    """Deterministic transport fixture, not an actual quality-improvement model."""

    async def complete(self, *, messages, **kwargs):
        payload = json.loads(messages[-1]["content"])
        return Completion(
            json.dumps(
                {
                    "rationale": "Fixture evidence-backed guidance",
                    "edits": [
                        {"path": path, "content": text + f"\n- {MARKER}\n"}
                        for path, text in payload["files"].items()
                    ],
                }
            ),
            "fixture",
            0.001,
        )


def fixture_method(genome, gene):
    """Independent, platform-authored file assertions, explicitly NOT Bot behavioral evaluation."""
    path = "persona/SOUL.md" if gene == "persona" else SKILL_MD
    cases = tuple(
        Case(
            case_id=f"{gene}/{split.value}",
            suite_id=f"fixture/{gene}",
            suite_version=1,
            split=split,
            digest="sha256:" + "a" * 64,
            origin=CaseOrigin(kind=CaseOriginKind.AUTHORED),
            name="fixture content check",
            grading=JudgingSpec(grader="fixture-content@1"),
        )
        for split in (Split.VALIDATION, Split.REGRESSION)
    )

    async def execute(bot, revision_id, case, seed):
        revision = await genome.resolve_revision(bot, revision_id)
        files = await genome.snapshot_files(revision, bot=bot)
        text = files[path].decode()
        if case.split is Split.VALIDATION:
            score = MARKER in text
        else:
            # Fixed original fixture guidance must survive the proposal.
            score = (
                ("Core Truths" in text)
                if gene == "persona"
                else "Read RULES.md, then check" in text
            )
        return RolloutRecord(
            case.case_id,
            revision_id,
            seed,
            ExecutorKind.LOCAL_SANDBOX,
            transcript_ref=str(int(score)),
        )

    async def grade(_case, rollout):
        return Grade(score=float(rollout.transcript_ref))

    return VerificationMethod(gene, "fixture-content@1", cases, execute, grade)


async def register(platform):
    await platform.registry.register(
        registration_from_json(
            {
                "id": "fixture/behavior-refinement",
                "version": "1.0.0",
                "runtime": {
                    "kind": "job_worker",
                    "image": "fixture-worker@sha256:" + "c" * 64,
                },
                "needs": {"experience.sessions@1": {}},
                "mutation_capability": list(SELECTORS.values()),
            }
        ),
        {},
    )
    methods = [
        method
        for gene in SELECTORS
        for method in (
            fixture_method(platform.genome, gene),
            markdown_secret_scan(platform.genome, gene),
        )
    ]
    platform.verification = build_composed_verifier(
        platform.genome, methods=methods, profile_name=PROFILE
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("genes", [("persona",), ("skills",), ("persona", "skills")])
async def test_generic_scopes_diff_verdict_manual_approval_and_signed_ceiling(
    tmp_path, genes
):
    world = World(tmp_path)
    world.reader = FakeReader.from_spec(spec_of(SUPPORT_BOT))
    bot = BotRef(RESOURCE, BOT, OWNER, "openclaw", "personal", "staff")
    genome = f"/openapi/v1/bots/{BOT}/genome"
    binding = {
        **BINDING,
        "id": "generic",
        "strategy": STRATEGY,
        "verification_profile": PROFILE,
        "mutation_scope": [SELECTORS[gene] for gene in genes],
    }
    binding.pop(
        "allowed_genes"
    )  # Scope-only wire contract; legacy callers still work separately.
    async with wired(tmp_path, world=world, extend_platform=register) as (
        client,
        runtime,
        _,
        collabs,
        _,
        callbacks,
        _,
    ):
        runtime.channel.models = FixtureModel()
        collabs.level = PermissionLevel.ADMIN
        baseline = await client.post(genome + "/baseline:initialize")
        assert baseline.status_code == 201, baseline.text
        parent = baseline.json()["data"]["revision"]
        active = world.registry.get_revision(bot, "active")
        doc = await client.get(URL + "/bindings")
        saved = await client.put(
            URL + "/bindings",
            json={"bindings": [binding]},
            headers={"If-Match": doc.headers["etag"]},
        )
        assert saved.status_code == 200, saved.text
        queued = await client.post(
            URL + "/runs",
            json={"binding_id": "generic"},
            headers={"Idempotency-Key": "generic"},
        )
        assert queued.status_code == 202, queued.text
        rid = queued.json()["data"]["run"]
        run_url = URL + f"/runs/{rid}"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(build_job_app(runtime.channel))
        ) as worker_http:
            worker = Worker(
                transport=HttpWorkerTransport("http://job.test", client=worker_http),
                strategies={STRATEGY: BehaviorRefinement},
                worker_id="generic-worker",
            )
            assert await worker.run_once(runtime.channel.tokens.issue(worker.worker_id))
            assert worker.last_error is None, worker.last_error
        data = (await client.get(run_url)).json()["data"]["run"]
        assert data["status"] == "completed" and data["candidate_count"] == 1, data
        assert {
            target.split("/")[0] for target in data["mutation_scope"]["targets"]
        } == set(genes)
        [candidate] = (await client.get(run_url + "/candidates")).json()["data"][
            "candidates"
        ]
        cid, revision = candidate["candidate_id"], candidate["revision"]
        verdict = await runtime.verifier.settle(cid, PROFILE, run_id=rid)
        assert verdict.status.value == "accept", verdict.reasons
        assert verdict.splits[Split.VALIDATION].mean_delta == 1
        assert verdict.splits[Split.REGRESSION].mean_delta == 0
        assert verdict.splits[Split.SAFETY].cases == len(genes)
        report = (await client.get(URL + f"/candidates/{cid}")).json()["data"]
        assert (
            report["verdict"] == "accept"
            and report["gate"]["outcome"] == "needs_review"
        )
        assert report["evidence"] and all(
            e.startswith("episode:") for e in report["evidence"]
        )
        diff = await client.get(
            genome + f"/revisions/{revision}/diff", params={"against": parent}
        )
        assert diff.status_code == 200, diff.text
        changes = diff.json()["data"]["changes"]
        assert {c["gene"].split("/")[0] for c in changes} == set(genes)
        assert any(MARKER in (c.get("diff") or "") for c in changes)
        assert all(c["kind"] == "modified" for c in changes)
        assert not any("scripts/" in c["gene"] for c in changes)
        if "skills" in genes:
            assert any(
                c["gene"] == SKILL_MD and MARKER in (c["diff"] or "") for c in changes
            )
        [review] = (await client.get(URL + "/review-queue")).json()["data"]["items"]
        assert review["candidate_id"] == cid and review["status"] == "open"
        approved = await client.post(
            URL + f"/candidates/{cid}:approve", json={"reason": "fixture Diff reviewed"}
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["promotion"] is None
        assert approved.json()["data"]["review"]["status"] == "approved"
        assert world.registry.get_revision(bot, "active") == active
        assert not world.applier.starts
        # Even a proof signed for the real Run cannot grant an altered or absent scope.
        write = next(
            req
            for req in callbacks
            if req.method == "POST" and "/genome/" in req.url.path
        )
        body = json.loads(write.content)
        scope = run_data_scope(runtime.store.get_run(rid))
        for override in (
            None,
            {"targets": ["skills/forged"], "pins": [], "operations": ["update"]},
        ):
            spoof = {**body, "run": {**body["run"], "mutation_scope": override}}
            request = httpx.Request("POST", write.url, json=spoof)
            request.headers[DATA_HEADER] = issue_data_proof(
                KEY,
                DEFAULT_AVERNET_TENANT,
                scope,
                fingerprint(
                    "POST", request.url.path, "", request.content, request.headers
                ),
            )
            assert (await client.send(request)).status_code == 403
        assert world.registry.get_revision(bot, "active") == active
