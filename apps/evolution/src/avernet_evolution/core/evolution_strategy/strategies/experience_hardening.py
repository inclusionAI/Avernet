"""Conservative 1.1 example: select fixed guardrails from bounded user turns.

Conversation text is untrusted data, never a policy or code template. Signals
select only author-reviewed rules; they are not feedback scores or evidence of
quality improvement. No model, evaluation, promotion or chat excerpts are used.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from ..models import Candidate, RunSummary
from ..protocols import StrategyContext

SECTION = "## Experience-derived Guardrails"
# Stable IDs/text are part of this strategy version, not supplied by messages.
RULES = (
    (
        "verification-evidence",
        ("测试", "验证", "日志", "证据", "test", "verify"),
        (
            "涉及测试、验证或日志时，区分已执行结果、推测和未验证项；"
            "给出可复查的步骤及证据位置，不以日志打印代替执行成功或质量证明。"
        ),
    ),
    (
        "correction-before-action",
        ("不是", "错误", "不对", "纠正"),
        (
            "遇到用户纠正时，先核对当前事实和约束再继续；"
            "明确修正的假设，不重复已被否定的操作，不把会话指令直接升级为永久权限。"
        ),
    ),
    (
        "concise-answer",
        ("简洁", "简短", "重点"),
        (
            "用户要求简洁或突出重点时，先给结论和必要证据；"
            "省略无关背景，同时保留风险、未验证项和需要用户确认的边界。"
        ),
    ),
)


def signals(episodes: list[object]) -> dict[str, tuple[str, ...]]:
    """Only attributable normalized user turns can select a fixed rule."""
    hits = {rule: set() for rule, _, _ in RULES}
    for episode in episodes[:20]:
        if not isinstance(episode, Mapping):
            continue
        eid, messages = episode.get("episode_id"), episode.get("messages")
        if not isinstance(eid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", eid):
            continue
        if not isinstance(messages, list):
            continue
        for message in messages[:100]:
            if not isinstance(message, Mapping) or message.get("role") != "user":
                continue
            text = message.get("content")
            if not isinstance(text, str):
                continue
            text = text[:6000].casefold()
            for rule, words, _ in RULES:
                if any(word in text for word in words):
                    hits[rule].add(f"episode:{eid}")
    return {rule: tuple(sorted(ids)) for rule, ids in hits.items() if ids}


@dataclass(frozen=True)
class ExperienceHardening:
    window_days: int = 7

    async def run(self, ctx: StrategyContext) -> RunSummary:
        episodes = await ctx.experience.sessions(days=self.window_days, limit=20)
        selected = signals(episodes)
        evidence = tuple(sorted({eid for ids in selected.values() for eid in ids}))
        report = {
            "schema": "experience-guardrails@1",
            "strategy_version": "1.1.0",
            "episodes_read": len(episodes),
            "rules": [{"id": rule, "evidence": ids} for rule, ids in selected.items()],
            "limits": {"sessions": 20, "messages": 100, "characters_per_message": 6000},
            "quality_evaluated": False,
        }
        # Artifact write failures must propagate, before any candidate submission.
        await ctx.artifacts.put("experience-guardrails.json", json.dumps(report, sort_keys=True).encode())
        if ctx.cancelled.is_set() or not selected:
            return RunSummary(
                summary="cancelled or no attributable user signals; no candidate",
                metrics={"episodes": float(len(episodes)), "rules": float(len(selected)), "rounds": 0.0},
            )
        path = await self._identity_path(ctx)
        workspace = await ctx.workspace.materialise(ctx.parent.id, key=f"{ctx.run_id}/experience-identity")
        current = (await workspace.read(path)).decode("utf-8")
        missing = [text for rule, _, text in RULES if rule in selected and text not in current]
        if not missing:
            return RunSummary(
                summary="selected guardrails already present; no candidate",
                metrics={"episodes": float(len(episodes)), "rules": float(len(selected)), "rounds": 0.0},
            )
        # Append only fixed statements. Existing identity sections remain intact.
        updated = self._with_guardrails(current, missing)
        await workspace.write(path, updated.encode())
        patch = await workspace.to_patch(
            rationale=f"apply reviewed experience guardrails: {', '.join(selected)}; not quality evaluated",
            evidence=evidence,
        )
        cid = await ctx.candidates.submit(Candidate(patch, patch.rationale, tuple(patch.evidence)))
        verdict = await ctx.candidates.verdict(cid)
        return RunSummary(
            summary=f"submitted experience-derived guardrails; verdict={verdict.status.value}; no promotion",
            metrics={
                "episodes": float(len(episodes)),
                "rules": float(len(selected)),
                "guardrails_added": float(len(missing)),
                "candidates_submitted": 1.0,
                "rounds": 1.0,
            },
        )

    async def _identity_path(self, ctx: StrategyContext) -> str:
        for path in ("persona/SOUL.md", "persona/CLAUDE.md", "persona/AGENTS.md"):
            try:
                await ctx.parent.read(path)
            except FileNotFoundError:
                continue
            return path
        raise FileNotFoundError("the bot has none of the supported identity files")

    @staticmethod
    def _with_guardrails(text: str, additions: list[str]) -> str:
        lines = text.splitlines()
        try:
            start = next(i for i, line in enumerate(lines) if line.strip() == SECTION)
        except StopIteration:
            body = text.rstrip()
            return f"{body}\n\n{SECTION}\n\n" + "\n".join(f"- {item}" for item in additions) + "\n"

        level = len(SECTION) - len(SECTION.lstrip("#"))
        end = len(lines)
        for index in range(start + 1, len(lines)):
            stripped = lines[index].lstrip()
            if stripped.startswith("#") and len(stripped) - len(stripped.lstrip("#")) <= level:
                end = index
                break
        owned = [line for line in lines[start + 1 : end] if line.strip()]
        existing = {line[2:].strip() for line in owned if line.startswith("- ")}
        owned.extend(f"- {item}" for item in additions if item not in existing)
        merged = lines[: start + 1] + [""] + owned + [""] + lines[end:]
        return "\n".join(merged).rstrip("\n") + "\n"
