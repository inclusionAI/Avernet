"""A reference strategy built on the port: harden an identity file from experience.

It is deliberately small, but it exercises every step 03-strategy.zh-CN.md asks a
strategy to perform:

1. read the bot's own sessions (``ctx.experience.sessions``),
2. decide a text change (here: append missing "core truths" to the identity file),
3. edit only the sandbox (``ctx.workspace.materialise`` + ``write``),
4. turn the edit into a genome patch (``ws.to_patch``),
5. submit it as a candidate (``ctx.candidates.submit``),
6. watch the verdict by id (``ctx.candidates.verdict``), bounded by the budget,
7. return a ``RunSummary``.

It never promotes: recording, verification, the gate and promotion stay
platform-owned (DR-2).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..models import Candidate, RunSummary, VerdictStatus
from ..protocols import StrategyContext

#: Identity files a bot may carry, in the order this strategy prefers them.
IDENTITY_CANDIDATES = ("persona/SOUL.md", "persona/CLAUDE.md", "persona/AGENTS.md")

#: The heading the strategy owns; everything under it is replaced.
SECTION_HEADING = "## Core Truths"

DEFAULT_TRUTHS = (
    "**先读后改，不动猜测。** 读懂现有代码与测试再动手；一个模糊的编辑比不编辑更危险。",
    "**引用证据，不凭空断言。** 提及数字、配置或行为时，说明来自哪个文件哪一行。",
    "**最小改动，先测再提。** 一个会话只解决一个问题，改完必须本地跑通相关测试。",
    "**答完即止。** 问题被回答后不要追加未经询问的自我介绍或环境说明。",
    "**不假装能力。** 不会的工具或 API 不要编造调用；先查文档或承认不知道。",
)


@dataclass(frozen=True)
class IdentityHardening:
    """``EvolutionStrategy`` that appends uncovered core truths to an identity file."""

    truths: tuple[str, ...] = DEFAULT_TRUTHS
    window_days: int = 7
    max_verdict_polls: int = 5
    poll_s: float = 5.0

    async def run(self, ctx: StrategyContext) -> RunSummary:
        # An ungranted capability raises CapabilityNotGranted on first use (03 §4.3),
        # so no explicit pre-check is needed.
        episodes = await ctx.experience.sessions(days=self.window_days, limit=50)
        await ctx.log.info("read experience", episodes=len(episodes))
        if ctx.cancelled.is_set():
            return RunSummary(
                summary="cancelled before proposing",
                metrics={"episodes": float(len(episodes)), "rounds": 0.0},
            )

        path = await self._identity_path(ctx)
        workspace = await ctx.workspace.materialise(
            ctx.parent.id, key=f"{ctx.run_id}/identity"
        )
        current = (await workspace.read(path)).decode("utf-8")

        missing = [truth for truth in self.truths if truth not in current]
        if not missing:
            return RunSummary(
                summary=f"{path} already carries every core truth",
                metrics={"rounds": 0.0, "missing": 0.0},
            )

        await workspace.write(path, self._with_truths(current, missing).encode("utf-8"))
        patch = await workspace.to_patch(
            rationale=f"append {len(missing)} core truths to {path} from {len(episodes)} recent sessions",
            evidence=self._evidence(episodes),
        )
        candidate = Candidate(
            patch=patch,
            rationale=patch.rationale,
            evidence=tuple(patch.evidence),
            self_metrics={"missing_truths": float(len(missing))},
        )
        candidate_id = await ctx.candidates.submit(candidate)
        await ctx.log.info("candidate submitted", candidate_id=candidate_id, path=path)

        verdict = await ctx.candidates.verdict(candidate_id)
        polls = 0
        while (
            verdict.status is VerdictStatus.PENDING and polls < self.max_verdict_polls
        ):
            if ctx.cancelled.is_set():
                break
            polls += 1
            await asyncio.sleep(self.poll_s)
            verdict = await ctx.candidates.verdict(candidate_id)

        return RunSummary(
            summary=f"submitted 1 candidate for {path}; verdict={verdict.status.value}",
            metrics={
                "rounds": 1.0,
                "candidates_submitted": 1.0,
                "missing_truths": float(len(missing)),
                "verdict_polls": float(polls),
            },
        )

    async def _identity_path(self, ctx: StrategyContext) -> str:
        for path in IDENTITY_CANDIDATES:
            try:
                await ctx.parent.read(path)
            except FileNotFoundError:
                continue
            return path
        raise FileNotFoundError(
            "the bot has none of the identity files this strategy edits: "
            + ", ".join(IDENTITY_CANDIDATES)
        )

    @staticmethod
    def _evidence(episodes: list[object]) -> tuple[str, ...]:
        ids = []
        for episode in episodes:
            episode_id = getattr(episode, "episode_id", None)
            if isinstance(episode_id, str) and episode_id:
                ids.append(f"episode:{episode_id}")
        return tuple(ids[:100])

    @staticmethod
    def _with_truths(text: str, truths: list[str]) -> str:
        lines = text.splitlines()
        try:
            start = next(
                i for i, line in enumerate(lines) if line.strip() == SECTION_HEADING
            )
        except StopIteration:
            body = "\n".join(text.rstrip().splitlines())
            return (
                f"{body}\n\n{SECTION_HEADING}\n\n"
                + "\n".join(f"- {t}" for t in truths)
                + "\n"
            )

        end = len(lines)
        level = len(SECTION_HEADING) - len(SECTION_HEADING.lstrip("#"))
        for index in range(start + 1, len(lines)):
            stripped = lines[index].lstrip()
            if stripped.startswith("#"):
                heading_level = len(stripped) - len(stripped.lstrip("#"))
                if heading_level <= level:
                    end = index
                    break

        kept = [line for line in lines[start + 1 : end] if line.strip()]
        kept.extend(f"- {truth}" for truth in truths)
        merged = lines[: start + 1] + [""] + kept + [""] + lines[end:]
        return "\n".join(merged).rstrip("\n") + "\n"
