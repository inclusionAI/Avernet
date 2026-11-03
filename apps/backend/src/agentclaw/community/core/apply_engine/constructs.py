"""The construct vocabulary: the seven things a desired state can declare, by name.

A **construct** is one independently converged area of a bot: its MCP
servers, its resource files, its skills, its engine configuration, its
identity files, its CLI tools, its startup script. Each has one materialiser,
one declaration in ``DesiredState``, one capability verdict per engine and
one section of the apply report, all keyed by the construct's name.

Two kinds, each its own enum, so that a construct's kind is its type and an
ill-formed ``(kind, name)`` pair cannot be written:

* a :class:`Category` — a collection of named entries (six of them);
* a :class:`Section` — a single value with no entries of its own (``script``).

The spellings are the wire's: a desired-state compiler (the Manifest's, the
genome's) maps its own grammar onto these names, the capability verdicts are
reported under them, and the apply report keys its results by them.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ApplyConstruct",
    "Construct",
    "ConstructKind",
    "Category",
    "Section",
    "kind_of",
    "parse_category",
]


class ConstructKind(StrEnum):
    """What sort of thing a construct is. Derived, never chosen at a call site."""

    CATEGORY = "category"
    SECTION = "section"


class Category(StrEnum):
    """The six entry-bearing constructs. A closed set: a compiler maps onto these or refuses.

    The order is the one the schema documents list them in, not the apply
    order (that is ``order.APPLY_ORDER``).
    """

    #: MCP servers the bot may use, each by ``server_code``, with an optional override of its configuration.
    MCP = "mcp"
    #: Files and directories under the bot's resources root (``data/``), each by workspace-relative path.
    RESOURCES = "resources"
    #: Skills installed for the bot, each by name; today a local package, later a Center or repository skill.
    SKILLS = "skills"
    #: The engine's own configuration mapping (validated, not yet materialised: #2858).
    ENGINE_CONFIG = "engine_config"
    #: Identity (persona) files such as ``SOUL.md`` or ``CLAUDE.md``, each by type.
    IDENTITY = "identity"
    #: Command-line tools placed in the bot's ``~/.local/bin``, each by name.
    CLI_TOOLS = "cli_tools"


class Section(StrEnum):
    """The single-valued constructs: no entries, one value per bot."""

    #: The startup script's body, run when the bot's container starts.
    SCRIPT = "script"


#: Either of the two. A value's own type says which kind it is.
Construct = Category | Section
#: The apply engine's name for the same union, kept for the report types.
ApplyConstruct = Construct

_KIND_BY_TYPE: dict[type, ConstructKind] = {
    Category: ConstructKind.CATEGORY,
    Section: ConstructKind.SECTION,
}


def kind_of(construct: Construct) -> ConstructKind:
    """The kind a construct belongs to, read off its type."""
    return _KIND_BY_TYPE[type(construct)]


def parse_category(value: object) -> Category | None:
    """A ``manifest`` key as a category, or ``None`` when it is not one.

    The Manifest parser needs this because a submitted document may name
    anything; everything inside the engine works in enums from there on.
    """
    if not isinstance(value, str):
        return None
    try:
        return Category(value)
    except ValueError:
        return None
