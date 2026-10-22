"""Gene paths: the contract's grammar for naming one part of a spec (README §2).

A gene path is a category then ``/``-separated segments. Segments are written
in exactly one spelling: every byte outside ``A-Z a-z 0-9 . _ -`` is
percent-encoded in uppercase hex, and ``.`` / ``..`` are written ``%2E`` /
``%2E%2E``. The same grammar names policy entries, a run's ``allowed_genes``,
every patch op's target, and diff output, so matching is plain string
comparison on whole segments.
"""
from __future__ import annotations

from agentclaw.community.core.bot_genome.canonical import encode_segment, is_canonical_gene_path
from agentclaw.community.core.bot_genome.patch_models import (
    FileEdit,
    MemoryAdd,
    MemoryRetire,
    MemorySetMode,
    MemoryUpdate,
    PatchOp,
    PersonaAdd,
    PersonaRemove,
    ResourceAdd,
    ResourceRemove,
    SkillAdd,
    SkillRemove,
    SkillUpdate,
)

GENE_CATEGORIES = ("persona", "skills", "memory", "resources", "tools", "engine_config", "script")

__all__ = [
    "GENE_CATEGORIES",
    "covers",
    "decode_segment",
    "encode_segment",
    "is_canonical_gene_path",
    "join",
    "memory_path",
    "mcp_path",
    "op_gene_path",
    "overlaps",
    "persona_path",
    "resource_path",
    "skill_path",
    "split",
]


def decode_segment(segment: str) -> str:
    """The name a canonical segment spells. Raises ``ValueError`` on a malformed escape.

    Examples: ``"SOUL.md"`` → ``"SOUL.md"``; ``"how%20to.md"`` → ``"how to.md"``;
    ``"a%2Fb"`` → ``"a/b"`` (a slash inside one name, as opposed to a path
    separator); ``"%4"`` → ``ValueError``.
    """
    out = bytearray()
    i = 0
    while i < len(segment):
        if segment[i] == "%":
            if i + 3 > len(segment):
                raise ValueError(f"truncated percent-escape in segment {segment!r}")
            out.append(int(segment[i + 1 : i + 3], 16))
            i += 3
        else:
            out += segment[i].encode("utf-8")
            i += 1
    return out.decode("utf-8")


def join(category: str, *names: str) -> str:
    """A gene path from a category and raw (unencoded) names: ``join("skills", "pdf", "SKILL.md")``."""
    if category not in GENE_CATEGORIES:
        raise ValueError(f"not a gene category: {category!r}")
    parts = [category]
    for name in names:
        for piece in name.split("/"):
            if piece == "":
                raise ValueError(f"empty segment in {name!r}")
            parts.append(encode_segment(piece))
    return "/".join(parts)


def split(path: str) -> tuple[str, tuple[str, ...]]:
    """``(category, decoded segments)`` of a canonical gene path. Raises ``ValueError`` otherwise."""
    if not is_canonical_gene_path(path):
        raise ValueError(f"not a canonical gene path: {path!r}")
    category, *segments = path.split("/")
    return category, tuple(decode_segment(segment) for segment in segments)


def covers(rule: str, path: str) -> bool:
    """``rule`` covers ``path`` when they are equal or ``path`` starts with ``rule + "/"`` (whole segments)."""
    return path == rule or path.startswith(rule + "/")


def overlaps(a: str, b: str) -> bool:
    """Two paths overlap when either covers the other (pins and risk overrides use this)."""
    return covers(a, b) or covers(b, a)


# ---------------------------------------------------------------------------------------------
# Builders: one spelling per entry kind (README §2 table, §6.1 "gene path checked").


def persona_path(file_type: str) -> str:
    return join("persona", file_type)


def skill_path(name: str, member: str | None = None) -> str:
    """``skills/<name>``, or ``skills/<name>/<member path>`` for a file inside a local skill."""
    return join("skills", name) if member is None else join("skills", name, member)


def resource_path(path: str, member: str | None = None) -> str:
    """``resources/<path without its trailing slash>``, plus the member path for a file inside a directory."""
    stripped = path.rstrip("/")
    return join("resources", stripped) if member is None else join("resources", stripped, member)


def memory_path(key: str | None = None) -> str:
    """``memory`` (the mode and every item) or ``memory/<key>``."""
    return "memory" if key is None else join("memory", key)


def mcp_path(server_code: str | None = None) -> str:
    """``tools/mcp`` or ``tools/mcp/<code>``; a server code is one segment, whatever it contains."""
    return "tools/mcp" if server_code is None else "tools/mcp/" + encode_segment(server_code)


def op_gene_path(op: PatchOp) -> str:
    """The gene path a patch op targets, which policy and ``allowed_genes`` are checked against (README §6.1).

    Not a kind of gene path: ``op`` is a patch op (a ``PersonaAdd``, a
    ``FileEdit``, …), and the answer is one of the ordinary paths above.
    ``file.edit`` names its target directly; every other op's path is built
    from its key field. Examples:
    ``FileEdit(target="persona/SOUL.md", …)`` → ``"persona/SOUL.md"``;
    ``SkillAdd(name="refund-policy", …)`` → ``"skills/refund-policy"``;
    ``ResourceRemove(path="data/faq.csv")`` → ``"resources/data/faq.csv"``;
    ``MemorySetMode(mode="merge")`` → ``"memory"``.
    """
    if isinstance(op, FileEdit):
        return op.target
    if isinstance(op, (PersonaAdd, PersonaRemove)):
        return persona_path(op.type)
    if isinstance(op, (SkillAdd, SkillUpdate, SkillRemove)):
        return skill_path(op.name)
    if isinstance(op, (ResourceAdd, ResourceRemove)):
        return resource_path(op.path)
    if isinstance(op, (MemoryAdd, MemoryUpdate)):
        return memory_path(op.item.key)
    if isinstance(op, MemoryRetire):
        return memory_path(op.key)
    if isinstance(op, MemorySetMode):
        return memory_path()
    raise TypeError(f"not a patch op: {type(op).__name__}")
