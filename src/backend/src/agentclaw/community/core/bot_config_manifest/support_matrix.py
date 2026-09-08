"""The (category × source protocol) support matrix — one table, two readers.

**Why this module exists.** Which (category, source) combinations actually work
used to be answered in three places that disagreed: per-category branches in the
validator, per-materialiser assumptions at apply time, and prose in the manuals.
Nothing was the authority, so every drift between them shipped — a documented
example the ``PUT`` refuses, a field accepted at ``PUT`` that fails at apply, a
digest rule that reads the wrong axis. The fix is not better prose. It is making
the question have exactly one answer, in code, that both the write path and the
read path (:mod:`~agentclaw.community.core.bot_config_manifest.capabilities`)
consult.

**Exhaustive by construction.** :data:`MATRIX` is built by iterating both enums
against :data:`_VERDICTS`, so a category or a protocol added to the vocabulary
without a verdict raises at *import*. It cannot resolve to a silent "supported"
and it cannot resolve to a silent "unsupported": both are how a matrix rots.

**The refusal a caller reads is the cell.** A cell is ``None`` (supported) or a
:class:`SourceRefusal` carrying the violation code *and* the message. The
validator emits that object and the capabilities endpoint publishes it, so the
two are the same string by construction rather than by review.

**``SourceKind`` is not ``SourceForm``.**
:class:`~agentclaw.community.core.bot_config_manifest.capabilities.SourceForm`
(``url``/``git``/``named``/``content``) describes *how an entry is spelled* and
stays exactly as it is — it is published contract on the ``constructs`` array.
``SourceKind`` describes *what protocol the content travels by*, which is the
axis support actually turns on. ``named`` is deliberately absent: a named source
declares a protocol, and the protocol's cell is the one that governs. That
identity is precisely what defect D5 got wrong — ``cli_tools`` reached by
``from:`` a git source was classified ``NAMED`` and charged the ``oss`` digest
rule — and keying every rule here on ``SourceKind`` is what leaves the mistake
nowhere to live.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
)


class SourceKind(StrEnum):
    """The protocol an entry's content travels by.

    Three members, and the two that fetch are the whole extensible axis:

    * ``CONTENT`` — inline in the document. No fetch, no credential, no URL.
    * ``OSS`` — an object addressed over https, signed or plain. A public CDN
      file and a private bucket object are the same protocol; the credential
      (or its absence) is what differs.
    * ``GIT`` — a repository resolved to one commit, delivered as a tree.
    """

    CONTENT = "content"
    OSS = "oss"
    GIT = "git"


@dataclass(frozen=True)
class SourceRefusal:
    """Why one (category, protocol) cell is closed, in the caller's words."""

    #: The violation code on the ``PUT`` response. Specific where a specific
    #: one says more than the generic ``unsupported_source``.
    code: str
    #: The message, complete on its own — no call site prefixes or rewords it.
    reason: str


_UNSUPPORTED_SOURCE = "unsupported_source"

_MCP_HAS_NO_SOURCE = SourceRefusal(
    _UNSUPPORTED_SOURCE,
    "an mcp entry is a registry reference ('server_code') and names no "
    "source; there is nothing for a protocol to fetch",
)

_ENGINE_CONFIG_HAS_NO_MATERIALISER = SourceRefusal(
    _UNSUPPORTED_SOURCE,
    "engine_config has no materialiser in this build, so nothing is "
    "delivered for it from any source",
)

_SKILL_IS_A_PACKAGE = SourceRefusal(
    "content_not_a_skill_package",
    "a skills entry is a package (SKILL.md + the files it names) — inline "
    "content cannot be one; declare a source with 'protocol: git' or "
    "'protocol: oss'",
)

_CLI_TOOL_IS_AN_EXECUTABLE = SourceRefusal(
    "content_not_an_executable",
    "a cli_tools entry is an executable file the platform distributes — "
    "inline text cannot be one; declare a source with 'protocol: git' or "
    "'protocol: oss'",
)

#: Every cell, written out. Listed rather than defaulted: a table whose
#: unwritten cells mean anything at all is a table that answers questions
#: nobody ruled on.
_VERDICTS: dict[tuple[ManifestCategory, SourceKind], SourceRefusal | None] = {
    # Identity files are single text bodies. Every protocol can carry one, and
    # inline is the common case.
    (ManifestCategory.IDENTITY, SourceKind.CONTENT): None,
    (ManifestCategory.IDENTITY, SourceKind.OSS): None,
    (ManifestCategory.IDENTITY, SourceKind.GIT): None,
    # Workspace files and trees. ``git`` is what this change opens (D1): a
    # tree arrives as a tree, with no packaging step in the middle.
    (ManifestCategory.RESOURCES, SourceKind.CONTENT): None,
    (ManifestCategory.RESOURCES, SourceKind.OSS): None,
    (ManifestCategory.RESOURCES, SourceKind.GIT): None,
    # A skill is a package; only a fetch can deliver one.
    (ManifestCategory.SKILLS, SourceKind.CONTENT): _SKILL_IS_A_PACKAGE,
    (ManifestCategory.SKILLS, SourceKind.OSS): None,
    (ManifestCategory.SKILLS, SourceKind.GIT): None,
    # A tool is an executable file; same reasoning, different noun.
    (ManifestCategory.CLI_TOOLS, SourceKind.CONTENT): _CLI_TOOL_IS_AN_EXECUTABLE,
    (ManifestCategory.CLI_TOOLS, SourceKind.OSS): None,
    (ManifestCategory.CLI_TOOLS, SourceKind.GIT): None,
    # Neither of these has a source axis at all, for two different reasons —
    # both stated, because "unsupported" without the reason is what sends a
    # caller looking for a workaround that does not exist.
    (ManifestCategory.MCP, SourceKind.CONTENT): _MCP_HAS_NO_SOURCE,
    (ManifestCategory.MCP, SourceKind.OSS): _MCP_HAS_NO_SOURCE,
    (ManifestCategory.MCP, SourceKind.GIT): _MCP_HAS_NO_SOURCE,
    (ManifestCategory.ENGINE_CONFIG, SourceKind.CONTENT): (
        _ENGINE_CONFIG_HAS_NO_MATERIALISER
    ),
    (ManifestCategory.ENGINE_CONFIG, SourceKind.OSS): (
        _ENGINE_CONFIG_HAS_NO_MATERIALISER
    ),
    (ManifestCategory.ENGINE_CONFIG, SourceKind.GIT): (
        _ENGINE_CONFIG_HAS_NO_MATERIALISER
    ),
}


def _build_matrix() -> Mapping[
    tuple[ManifestCategory, SourceKind], SourceRefusal | None
]:
    """Every cell of the cartesian product, or an import-time failure.

    Both directions are checked. A missing verdict raises ``KeyError`` — the
    comprehension asks for a cell nobody ruled on. A *stale* verdict (for a
    category or protocol that no longer exists) raises too, because a leftover
    row silently governing nothing is how a table stops describing the code.
    """
    cells = {
        (category, kind): _VERDICTS[(category, kind)]
        for category in ManifestCategory
        for kind in SourceKind
    }
    stale = set(_VERDICTS) - set(cells)
    if stale:
        raise RuntimeError(
            "support matrix carries verdicts for cells that no longer exist: "
            + ", ".join(sorted(f"({c}, {k})" for c, k in stale))
        )
    return MappingProxyType(cells)


#: The contract. ``None`` means the combination is accepted at ``PUT`` and
#: delivered at apply; a :class:`SourceRefusal` means both refuse it, with that
#: code and that message.
MATRIX: Mapping[tuple[ManifestCategory, SourceKind], SourceRefusal | None] = (
    _build_matrix()
)

#: Where a ``digest`` is mandatory. The platform is distributing executable
#: content and an unpinned fetch takes whatever is at the URL at the time.
#:
#: ``git`` is absent on purpose and the reason is not leniency: a git source
#: resolves to a commit SHA, which is a recorded content identity doing exactly
#: the job a digest does, and a ``sha256`` over git-sourced bytes has no stable
#: meaning across the checkout / archive / canonical-zip roads. Whether the ref
#: may move between applies is ``mode: strict``'s question, not a digest's.
DIGEST_REQUIRED: frozenset[tuple[ManifestCategory, SourceKind]] = frozenset(
    {
        (ManifestCategory.SKILLS, SourceKind.OSS),
        (ManifestCategory.CLI_TOOLS, SourceKind.OSS),
    }
)

#: Fields that describe an archive, per protocol.
#:
#: They are ``oss``-only, and declaring one elsewhere is **refused** rather than
#: ignored. On git the platform holds a real tree and ``subpath`` selects it, so
#: ``unpack`` configures nothing; on inline content there is no object at all. A
#: field that looks like it configures something and does nothing is the exact
#: failure mode this change exists to remove.
ARCHIVE_FIELDS_BY_KIND: Mapping[SourceKind, frozenset[str]] = MappingProxyType(
    {
        SourceKind.CONTENT: frozenset(),
        SourceKind.OSS: frozenset({"unpack", "strip_components"}),
        SourceKind.GIT: frozenset(),
    }
)

#: Every archive field, whichever protocol allows it — the set a refusal is
#: drawn from, so adding a field to the vocabulary adds it here once.
ARCHIVE_FIELDS: frozenset[str] = frozenset(
    field for fields in ARCHIVE_FIELDS_BY_KIND.values() for field in fields
)


def refusal_for(
    category: ManifestCategory, kind: SourceKind
) -> SourceRefusal | None:
    """The cell. ``None`` when the combination is supported."""
    return MATRIX[(category, kind)]


def supports(category: ManifestCategory, kind: SourceKind) -> bool:
    """Whether this build accepts and delivers the combination."""
    return MATRIX[(category, kind)] is None


def digest_required(category: ManifestCategory, kind: SourceKind) -> bool:
    """Whether an entry in this cell must declare a ``digest``."""
    return (category, kind) in DIGEST_REQUIRED


def archive_field_refusal(kind: SourceKind, field: str) -> str | None:
    """Why ``field`` may not be written against a ``kind`` source, if it may not.

    Names both the field and the protocol: "unpack is not valid" alone sends
    the caller looking for a typo in the value.
    """
    if field in ARCHIVE_FIELDS_BY_KIND[kind]:
        return None
    if kind is SourceKind.GIT:
        return (
            f"'{field}' describes an archive and is not valid on a git source "
            "— the platform holds the repository's tree and 'subpath' selects "
            "within it, so there is nothing to unpack"
        )
    return (
        f"'{field}' describes an archive and is not valid on an inline "
        "'content' entry — nothing is fetched"
    )


__all__ = [
    "ARCHIVE_FIELDS",
    "ARCHIVE_FIELDS_BY_KIND",
    "DIGEST_REQUIRED",
    "MATRIX",
    "SourceKind",
    "SourceRefusal",
    "archive_field_refusal",
    "digest_required",
    "refusal_for",
    "supports",
]
