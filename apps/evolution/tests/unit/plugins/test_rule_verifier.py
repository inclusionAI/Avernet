"""规则验证器：判定语义与"每个 revision 只读一次"的缓存。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from avernet_evolution.core.evolution_verify import CandidateRef, VerdictStatus
from avernet_evolution.plugins.verifiers import RULE_VALIDATION_MARKERS, build_rule_verifier

BOT = SimpleNamespace(owner_id="owner-1", bot_id="bot-1")


class _Genome:
    """最小的 Genome 端口：按 revision id 返回文件内容。"""

    def __init__(self, files: dict[str, dict[str, bytes]]) -> None:
        self.files = files
        self.snapshots: list[str] = []

    async def resolve_revision(self, bot, ref):
        del bot
        return SimpleNamespace(id=ref)

    async def snapshot_files(self, revision, bot=None):
        del bot
        self.snapshots.append(revision.id)
        return self.files[revision.id]


PARENT = "sha256:parent"
CANDIDATE = "sha256:candidate"


def _ref() -> CandidateRef:
    return CandidateRef(
        candidate_id=CANDIDATE,
        bot_owner_id=BOT.owner_id,
        bot_id=BOT.bot_id,
        revision=CANDIDATE,
        parent_revision=PARENT,
        run_id="run_1",
        bot=BOT,
    )


def _genome(parent_text: str, candidate_text: str) -> _Genome:
    return _Genome(
        {
            PARENT: {"persona/AGENTS.md": parent_text.encode()},
            CANDIDATE: {"persona/AGENTS.md": candidate_text.encode()},
        }
    )


@pytest.mark.asyncio
async def test_marker_hits_accept_and_read_each_revision_once() -> None:
    genome = _genome("旧内容\n", "## Core Truths\n" + "\n".join(RULE_VALIDATION_MARKERS) + "\n")
    service = build_rule_verifier(genome)
    await service.verify(_ref(), "rules-file@1")
    verdict = await service.settle(CANDIDATE, "rules-file@1", run_id="run_1")

    assert verdict.status is VerdictStatus.ACCEPT, verdict.reasons
    assert verdict.splits["validation"].cases == 3
    assert verdict.splits["validation"].mean_delta == 1.0
    # 5 条用例 × 2 个版本，但每个版本只 snapshot 一次。
    assert sorted(genome.snapshots) == [CANDIDATE, PARENT]


@pytest.mark.asyncio
async def test_unchanged_content_does_not_improve() -> None:
    same = "## Core Truths\n旧内容\n"
    service = build_rule_verifier(_genome(same, same))
    await service.verify(_ref(), "rules-file@1")
    verdict = await service.settle(CANDIDATE, "rules-file@1", run_id="run_1")

    assert verdict.status is VerdictStatus.REJECT
    assert verdict.splits["validation"].mean_delta == 0.0


@pytest.mark.asyncio
async def test_candidate_that_introduces_a_secret_is_rejected() -> None:
    service = build_rule_verifier(
        _genome(
            "## Core Truths\n",
            "## Core Truths\n" + "\n".join(RULE_VALIDATION_MARKERS) + "\nsk-live-abc123\n",
        )
    )
    await service.verify(_ref(), "rules-file@1")
    verdict = await service.settle(CANDIDATE, "rules-file@1", run_id="run_1")

    # 验证有增益，但 safety 新失败零容差 → 必须拒绝。
    assert verdict.status is VerdictStatus.REJECT
    assert "must_pass_failure" in verdict.reasons_categories


@pytest.mark.asyncio
async def test_profiles_expose_the_binding_names() -> None:
    service = build_rule_verifier(_genome("a", "b"))
    assert set(service.profiles) == {"rules-file@1", "default@1"}
    assert service.default_profile == "rules-file@1"


@pytest.mark.asyncio
async def test_grader_reads_the_observed_snapshot_not_another_bots_rollout() -> None:
    other = SimpleNamespace(owner_id=BOT.owner_id, bot_id="bot-2")

    class ScopedGenome:
        async def resolve_revision(self, bot, revision):
            return SimpleNamespace(bot=bot, id=revision)

        async def snapshot_files(self, revision, *, bot):
            assert revision.bot == bot
            text = RULE_VALIDATION_MARKERS[0] if bot == BOT else "no matching marker"
            return {"persona/AGENTS.md": text.encode()}

    service = build_rule_verifier(ScopedGenome())
    case = (await service.cases_for(BOT, ("validation",)))[0]
    first = await service.execute(BOT, CANDIDATE, case, 1)
    second = await service.execute(other, CANDIDATE, case, 1)
    assert (await service.grade(case, first)).score == 1.0
    assert (await service.grade(case, second)).score == 0.0
