"""Opt-in reference strategy: propose a harmless Skill in a Genome candidate only."""

from __future__ import annotations

import re
from collections.abc import Mapping

from avernet_genome.gene_paths import skill_path
from avernet_genome.patch_models import GenomePatch, SkillAdd

from ..models import Candidate, RunSummary
from ..protocols import StrategyContext

SKILL_NAME = "rsi-candidate-smoke"
SKILL_FILE = skill_path(SKILL_NAME, "SKILL.md")
SKILL_TEXT = f"""---
name: {SKILL_NAME}
description: Summarize one test reply without changing its facts.
---

# Reply check

- Preserve dates and numbers exactly.
- Do not add unsupported claims.
- Return only the revised sentence.
"""
_EPISODE_ID = re.compile(r"[A-Za-z0-9_-]{1,120}")


class SkillCandidateSmoke:
    """Submit a fixed, disposable sample, never install or enable a runtime Skill."""

    async def run(self, ctx: StrategyContext) -> RunSummary:
        if ctx.cancelled.is_set():
            return RunSummary(summary="cancelled before proposing")
        try:
            await ctx.parent.read(SKILL_FILE)
        except FileNotFoundError:
            pass
        else:
            return RunSummary(summary="sample Skill already exists in the parent; no candidate submitted")

        episodes = await ctx.experience.sessions(days=7, limit=20)
        evidence = []
        for episode in episodes:
            episode_id = (
                episode.get("episode_id")
                if isinstance(episode, Mapping)
                else getattr(episode, "episode_id", None)
            )
            if isinstance(episode_id, str) and _EPISODE_ID.fullmatch(episode_id):
                evidence.append(f"episode:{episode_id}")
        if ctx.cancelled.is_set():
            return RunSummary(summary="cancelled before proposing")
        if not evidence:
            return RunSummary(summary="no recent exported session evidence; no candidate submitted")

        patch = GenomePatch(
            patch_schema=1,
            base=ctx.parent.id,
            ops=[SkillAdd(op="skill.add", name=SKILL_NAME, files={"SKILL.md": SKILL_TEXT})],
            rationale="Propose a fixed reply-check sample to exercise candidate-only Skill verification against recent Bot sessions.",
            evidence=sorted(set(evidence)),
        )
        candidate_id = await ctx.candidates.submit(
            Candidate(patch=patch, rationale=patch.rationale, evidence=tuple(patch.evidence))
        )
        await ctx.log.info("candidate submitted", candidate_id=candidate_id, path=SKILL_FILE)
        return RunSummary(summary="submitted one candidate-only Skill", metrics={"candidates_submitted": 1.0})
