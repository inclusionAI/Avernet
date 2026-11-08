"""The structural checks on a patch before it is applied (README §5, §6.4).

Every function returns the full list of :class:`Reason` it found, so the
caller can refuse once with everything wrong. Codes: ``schema``,
``not_canonical``, ``missing_rationale``, ``missing_evidence``,
``gene_not_mutable``, ``pinned_item``, ``gene_not_allowed``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agentclaw.community.core.bot_genome import contract
from agentclaw.community.core.bot_genome.canonical import is_canonical_gene_path
from agentclaw.community.core.bot_genome.errors import Reason
from agentclaw.community.core.bot_genome.gene_paths import covers, op_gene_path, overlaps
from agentclaw.community.core.bot_genome.patch_models import FileEdit, GenomePatch, PatchOp
from agentclaw.community.core.bot_genome.spec_models import GenomePolicy

__all__ = [
    "EVIDENCE_OPTIONAL",
    "evidence_reasons",
    "policy_reasons",
    "shape_reasons",
]

#: README §6.4: ops the rationale alone may motivate. ``file.edit`` only when its target is a resource.
EVIDENCE_OPTIONAL = frozenset({"memory.retire", "persona.remove"})


def shape_reasons(patch: Any) -> list[Reason]:
    """The patch's JSON against the contract schema (``schema``), then non-canonical gene paths (``not_canonical``).

    Takes the JSON form (``GenomePatch.to_json()``, or what a caller sent):
    the contract schema is the oracle for the patterns and cross-field rules.
    """
    errors = sorted(contract.patch_validator().iter_errors(patch), key=lambda error: list(error.absolute_path))
    if errors:
        return [Reason("schema", _location(list(error.absolute_path)), error.message) for error in errors]
    reasons = []
    for index, op in enumerate(patch["ops"]):
        if op["op"] == "file.edit" and not is_canonical_gene_path(op["target"]):
            reasons.append(
                Reason("not_canonical", f"ops[{index}].target", f"{op['target']!r} is not in its canonical spelling")
            )
    return reasons


def evidence_reasons(patch: GenomePatch) -> list[Reason]:
    """``missing_rationale`` for a blank rationale; ``missing_evidence`` when an op needs evidence and none is given."""
    reasons = []
    if not patch.rationale.strip():
        reasons.append(Reason("missing_rationale", "rationale", "a patch needs a rationale"))
    if not patch.evidence:
        needing = [index for index, op in enumerate(patch.ops) if _needs_evidence(op)]
        if needing:
            reasons.append(
                Reason(
                    "missing_evidence",
                    "evidence",
                    f"ops {needing} need evidence; only persona.remove, memory.retire and file.edit on a resource "
                    "can rest on the rationale alone",
                )
            )
    return reasons


def _needs_evidence(op: PatchOp) -> bool:
    if op.op in EVIDENCE_OPTIONAL:
        return False
    if isinstance(op, FileEdit):
        return not op.target.startswith("resources/")
    return True


def policy_reasons(
    ops: Iterable[PatchOp], policy: GenomePolicy, *, allowed_genes: Iterable[str] | None = None
) -> list[Reason]:
    """Every op's gene path must be covered by ``mutable_genes``, overlap no pin, and, for a run, be in
    ``allowed_genes``.
    """
    mutable, pins = policy.mutable_genes, policy.pins
    allowed = None if allowed_genes is None else tuple(allowed_genes)
    reasons = []
    for index, op in enumerate(ops):
        gene = op_gene_path(op)
        location = f"ops[{index}]"
        if not any(covers(rule, gene) for rule in mutable):
            reasons.append(
                Reason("gene_not_mutable", location, f"{gene!r} is not covered by the policy's mutable_genes")
            )
        pinned = [pin for pin in pins if overlaps(pin, gene)]
        if pinned:
            reasons.append(Reason("pinned_item", location, f"{gene!r} overlaps the pin {pinned[0]!r}"))
        if allowed is not None and not any(covers(rule, gene) for rule in allowed):
            reasons.append(Reason("gene_not_allowed", location, f"{gene!r} is not covered by the run's allowed_genes"))
    return reasons


def _location(parts: list[Any]) -> str:
    """``ops[2].files["a.md"]``: the same spelling the other reasons use; ``$`` for the whole record."""
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        elif part.isidentifier():
            out += f".{part}" if out else part
        else:
            out += f"[{part!r}]"
    return out or "$"
