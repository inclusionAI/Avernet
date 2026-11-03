"""Risk tiers of a patch (README §6.3, ``risk-tiers.json``).

Per op: the first rule whose ``op`` matches, whose ``target`` covers the
op's gene path and whose ``when`` holds gives the default tier; the
``rewrite`` raise lifts every op that changed a rewritten file to at least
``T2``; ``policy.risk_overrides`` lift by overlap. The patch's tier is the
highest over its ops.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from agentclaw.community.core.bot_genome import contract
from agentclaw.community.core.bot_genome.gene_paths import covers, overlaps
from agentclaw.community.core.bot_genome.patch.ops import FileChange, OpTouch, PatchOutcome
from agentclaw.community.core.bot_genome.rewrite import rewrite_flagged, to_lines
from agentclaw.community.core.bot_genome.spec_models import GenomePolicy

__all__ = [
    "TIERS",
    "apply_overrides",
    "apply_raises",
    "highest",
    "op_tier",
    "patch_assessment",
    "rewritten_files",
]

TIERS = ("T0", "T1", "T2", "T3")


def highest(tiers: Iterable[str]) -> str:
    return max(tiers, key=TIERS.index, default="T0")


def op_tier(touch: OpTouch, rules: list[dict[str, Any]]) -> str:
    """The default tier of one op: the first matching rule in file order."""
    for rule in rules:
        if rule["op"] != touch.op or not covers(rule["target"], touch.gene):
            continue
        when = rule.get("when", {})
        if "description_only" in when and when["description_only"] != touch.description_only:
            continue
        if "mode" in when and when["mode"] != touch.mode:
            continue
        return rule["tier"]
    raise ValueError(f"risk-tiers.json has no rule for {touch.op} on {touch.gene!r}")


def rewritten_files(changed_files: Mapping[str, FileChange], threshold: Mapping[str, Any]) -> tuple[str, ...]:
    """The gene paths of files the patch rewrote: measured once each, base → result (README §6.3)."""
    flagged = []
    for gene, change in changed_files.items():
        if not change.text:
            flagged.append(gene)  # a replacement across kinds: no line measure applies, always flagged
            continue
        if change.before is None or change.after is None:
            continue
        try:
            a, b = to_lines(change.before.decode("utf-8")), to_lines(change.after.decode("utf-8"))
        except UnicodeDecodeError:
            flagged.append(gene)
            continue
        if rewrite_flagged(
            a,
            b,
            numerator=threshold["numerator"],
            denominator=threshold["denominator"],
            cell_limit=threshold["lcs_cell_limit"],
        ):
            flagged.append(gene)
    return tuple(sorted(flagged))


def apply_raises(tier: str, flags: Iterable[str], raises: list[dict[str, Any]]) -> str:
    """An op flagged ``rewrite`` (or ``guardrail_touch``) is at least the raise's ``min_tier``."""
    wanted = set(flags)
    return highest([tier, *(raise_["min_tier"] for raise_ in raises if raise_["kind"] in wanted)])


def apply_overrides(tier: str, gene: str, overrides: Mapping[str, str]) -> str:
    """``policy.risk_overrides`` lift an op's tier by overlap; a lower override has no effect."""
    return highest([tier, *(value for path, value in overrides.items() if overlaps(path, gene))])


def patch_assessment(
    outcome: PatchOutcome, policy: GenomePolicy, tiers: dict[str, Any] | None = None
) -> tuple[str, tuple[str, ...]]:
    """``(the patch's tier, the gene paths of rewritten files)``."""
    tiers = tiers or contract.risk_tiers()
    rewritten = rewritten_files(outcome.changed_files, tiers["rewrite_threshold"])
    rewritten_set = set(rewritten)
    op_tiers = []
    for touch in outcome.touched:
        tier = op_tier(touch, tiers["rules"])
        flags = ["rewrite"] if rewritten_set.intersection(touch.files) else []
        tier = apply_raises(tier, flags, tiers["raises"])
        op_tiers.append(apply_overrides(tier, touch.gene, policy.risk_overrides))
    return highest(op_tiers), rewritten
