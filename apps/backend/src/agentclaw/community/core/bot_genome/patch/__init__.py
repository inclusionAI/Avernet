"""Genome Patches: shape and policy checks, application to a base spec, risk assessment.

The registry's ``commit_patch`` runs them in this order: ``shape_reasons`` →
``evidence_reasons`` → ``policy_reasons`` → ``apply_patch`` →
``patch_assessment``; the contract's own per-entry predicates
(``spec_checks``) run on the outcome in between.
"""

from agentclaw.community.core.bot_genome.patch.ops import FileChange, OpTouch, PatchOutcome, apply_patch
from agentclaw.community.core.bot_genome.patch.policy import evidence_reasons, policy_reasons, shape_reasons
from agentclaw.community.core.bot_genome.patch.risk import patch_assessment

__all__ = [
    "FileChange",
    "OpTouch",
    "PatchOutcome",
    "apply_patch",
    "evidence_reasons",
    "patch_assessment",
    "policy_reasons",
    "shape_reasons",
]
