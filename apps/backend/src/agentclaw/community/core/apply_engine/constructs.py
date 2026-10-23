"""The construct vocabulary: what a desired state can declare, by name.

Two kinds of construct, each its own enum, so that a construct's kind is its
type and an ill-formed ``(kind, name)`` pair cannot be written: a *category*
(one of the six under a Manifest document's ``manifest`` key, and the keys of
``DesiredState`` with the same names) and a *section* (a top-level section
that is not a category: ``script``). The names keep the Manifest's spelling
because the Manifest's schema, its capability verdicts and the apply report's
wire shape all use them; what moved here is ownership, not the words.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ApplyConstruct",
    "Construct",
    "ConstructKind",
    "ManifestCategory",
    "ManifestSection",
    "kind_of",
    "parse_category",
]


class ConstructKind(StrEnum):
    """What sort of thing a construct is. Derived, never chosen at a call site."""

    CATEGORY = "category"
    SECTION = "section"


class ManifestCategory(StrEnum):
    """The six categories, in the order Manifest schema §1 lists them.

    Also the *closed* set the Manifest parser admits: an unknown key under
    ``manifest`` is refused, not ignored.
    """

    MCP = "mcp"
    RESOURCES = "resources"
    SKILLS = "skills"
    ENGINE_CONFIG = "engine_config"
    IDENTITY = "identity"
    CLI_TOOLS = "cli_tools"


class ManifestSection(StrEnum):
    """A top-level section that is not a category."""

    SCRIPT = "script"


#: Either of the two. A value's own type says which kind it is.
Construct = ManifestCategory | ManifestSection
#: The apply engine's name for the same union, kept for the report types.
ApplyConstruct = Construct

_KIND_BY_TYPE: dict[type, ConstructKind] = {
    ManifestCategory: ConstructKind.CATEGORY,
    ManifestSection: ConstructKind.SECTION,
}


def kind_of(construct: Construct) -> ConstructKind:
    """The kind a construct belongs to, read off its type."""
    return _KIND_BY_TYPE[type(construct)]


def parse_category(value: object) -> ManifestCategory | None:
    """A ``manifest`` key as a category, or ``None`` when it is not one.

    The Manifest parser needs this because a submitted document may name
    anything; everything inside the engine works in enums from there on.
    """
    if not isinstance(value, str):
        return None
    try:
        return ManifestCategory(value)
    except ValueError:
        return None
