"""What a bot can be told to do — one function, two entry points.

**The rule this module enforces.** *This surface never accepts something it
cannot apply.* Anything the schema can express but no shipped code can act on is
reported unsupported and refused at ``PUT``. The feature flag over the routes is
not enough on its own: W1 parses the **whole** v1 vocabulary while only part of
it has a materializer behind it, and the gap is not confined to categories — a
**source form** with no resolver fails in exactly the same way.

So capabilities are answered **per accepted construct**, not per bot and not
only per category. Three kinds of construct appear here:

* ``category`` — one of the six under ``manifest``;
* ``section`` — a top-level section that is not a category (``script``);
* ``source`` — how an entry says where its content comes from.

**One function, and that is an acceptance criterion, not tidiness.** The read
path (``GET …/config-manifest/capabilities``) and the write path (``PUT``'s
refusal) call the same resolver, so ``/capabilities`` cannot claim support for
something the very next ``PUT`` refuses.

**It answers from the engine type and the bot type alone.** W13 validates a
manifest in the *first* leg of bot creation, before any ``ac_bots`` row exists,
so a resolver that needed a bot record could not be reused there and a second
implementation would appear. :func:`resolve_capabilities` takes the two values;
:func:`capabilities_for_bot` is a two-line adapter that reads them off a record.
Two entry points, one body.

**Constructs are enums, and a construct's kind is its type.** ``kind`` and
``name`` are not two free strings that happen to be used together: most of their
combinations are meaningless — there is no ``source`` called ``mcp``. So each
kind gets its own enum, those three enums *are* the construct vocabulary, and
``kind`` is derived from which enum a value belongs to. An illegal pair is not
rejected at runtime; it cannot be written. The wire shape is unchanged — a
construct still serialises as ``{kind, name, supported, reason}`` — but nothing
inside this package passes the two around separately.

**No third "unknown" state.** Support is a property of the bot record, never of
a live lookup (work-items §2.5, and the same reasoning ``bot_startup_script``
records for its own support check): ``is_teclaw`` is the engine authority and
``bot_type`` is a column. A device-binding read that happened to be failing must
never look like a verdict about what a bot supports.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Iterable

from agentclaw.community.core.workspace.constants import (
    DEFAULT_ENGINE_TYPE,
    SUPPORTED_ENGINE_TYPES,
)

class ConstructKind(StrEnum):
    """What sort of thing a construct is. Derived, never chosen at a call site."""

    CATEGORY = "category"
    SECTION = "section"
    SOURCE = "source"


class ManifestCategory(StrEnum):
    """The six categories under ``manifest``, in the order schema §1 lists them.

    Also the *closed* set the parser admits: an unknown key under ``manifest``
    is refused, not ignored.
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


class SourceForm(StrEnum):
    """How an entry names its content.

    Four forms. ``GIT`` covers both spellings of a git source — inline on an
    entry and declared under ``sources`` — because one resolver serves both
    (W7's declared-source dispatch).
    """

    URL = "url"
    GIT = "git"
    NAMED = "named"
    CONTENT = "content"


#: Any of the three. A value's own type says which kind it is, which is what
#: makes an ill-formed ``(kind, name)`` pair unwritable rather than merely
#: invalid.
Construct = ManifestCategory | ManifestSection | SourceForm

_KIND_BY_TYPE: dict[type, ConstructKind] = {
    ManifestCategory: ConstructKind.CATEGORY,
    ManifestSection: ConstructKind.SECTION,
    SourceForm: ConstructKind.SOURCE,
}


def kind_of(construct: Construct) -> ConstructKind:
    """The kind a construct belongs to, read off its type."""
    return _KIND_BY_TYPE[type(construct)]


def parse_category(value: object) -> ManifestCategory | None:
    """A ``manifest`` key as a category, or ``None`` when it is not one.

    The parser needs this because a submitted document may name anything;
    everything *inside* this package works in enums from there on.
    """
    if not isinstance(value, str):
        return None
    try:
        return ManifestCategory(value)
    except ValueError:
        return None


#: The schema versions this build parses. A document naming anything else is
#: refused rather than best-effort parsed.
SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (1,)

#: The bot type this whole feature excludes (work-items §2.5). Desktop bots are
#: out of scope, so the check exists only to refuse them.
_DESKTOP_BOT_TYPE = "desktop"

# ── Why each first-wave construct is unsupported ────────────────────────────
#
# Every entry here is the same shape: the vocabulary can express it and no
# shipped code can act on it. Anyone adding to the vocabulary adds a line here
# or adds the code that applies it — "let this surface accept something nothing
# applies" is never the third option.
_REASON_ENGINE_CONFIG = (
    "engine_config was moved out of the first wave, so no materializer writes it"
)
_REASON_TECLAW_SCRIPT = (
    "teclaw bots are provisioned without a start sequence, so a script would "
    "never execute"
)
_REASON_DESKTOP_SCRIPT = (
    "desktop bots build their start command outside the shared sequence"
)
_REASON_DESKTOP = (
    "desktop bots are outside this feature's scope; no manifest category is "
    "delivered to one"
)
_REASON_UNKNOWN_ENGINE = (
    "unrecognised engine type: nothing here can say how a category would be "
    "delivered to it"
)


@dataclass(frozen=True)
class Capability:
    """Whether one construct can be accepted, and why not when it cannot."""

    construct: Construct
    supported: bool
    reason: str

    @property
    def kind(self) -> ConstructKind:
        """Read off the construct's type, never stored — so it cannot disagree."""
        return kind_of(self.construct)

    def as_dict(self) -> dict[str, Any]:
        """The wire shape. Public contract — flat, and stable across versions."""
        return {
            "kind": self.kind.value,
            "name": self.construct.value,
            "supported": self.supported,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ManifestCapabilities:
    """Every construct's verdict for one (engine type, bot type) pair."""

    engine_type: str
    bot_type: str
    schema_versions: tuple[int, ...]
    constructs: tuple[Capability, ...]

    def find(self, construct: Construct) -> Capability | None:
        """The verdict for one construct, or ``None`` if it has no row."""
        for capability in self.constructs:
            if capability.construct is construct:
                return capability
        return None

    def supports(self, construct: Construct) -> bool:
        """Whether the construct is supported.

        A construct with no row is **not** supported — the conservative half of
        this module's rule: something nobody ruled on is something nothing
        applies. Unreachable while every enum member gets a row below, which a
        test holds.
        """
        capability = self.find(construct)
        return capability is not None and capability.supported

    def reason_for(self, construct: Construct) -> str:
        """Why the construct is unsupported; ``""`` when it is supported."""
        capability = self.find(construct)
        if capability is None:
            return f"no verdict for {kind_of(construct).value} '{construct.value}'"
        return capability.reason

    def as_payload(self) -> dict[str, Any]:
        """The ``GET …/capabilities`` body."""
        return {
            "engine_type": self.engine_type,
            "bot_type": self.bot_type,
            "schema_versions": list(self.schema_versions),
            "constructs": [c.as_dict() for c in self.constructs],
        }


def resolve_capabilities(
    *,
    active_engine: str | None,
    bot_type: str | None,
    is_teclaw: Callable[[str | None], bool],
) -> ManifestCapabilities:
    """Verdicts for every construct, from the engine and bot type alone.

    Args:
        active_engine: The bot's engine. ``None``/empty is read as the platform
            default, matching how the rest of the codebase resolves an
            unset engine — a bot mid-creation is not an unknown engine.
        bot_type: ``personal`` / ``service`` / ``desktop`` / … Only ``desktop``
            changes an answer.
        is_teclaw: The canonical engine test, passed in rather than imported.
            ``TeclawProvisionService.is_teclaw`` keys on a configured set, and a
            hand-rolled ``== "teclaw"`` here would be a divergent copy that the
            next teclaw-like engine would have to remember to update. Taking it
            as an argument also keeps this a pure function, which is what lets
            W13 call it with no injector in reach.
    """
    engine = (active_engine or DEFAULT_ENGINE_TYPE).strip() or DEFAULT_ENGINE_TYPE
    bot = (bot_type or "").strip()

    teclaw = is_teclaw(engine)
    desktop = bot == _DESKTOP_BOT_TYPE
    # An engine the platform does not list is not a bot we can reason about.
    # Checked against the static vocabulary rather than the env-narrowed
    # ``_get_engine_types()``: that one describes what a *deployment* offers,
    # and a document's acceptability must not turn on an operator's env var.
    unknown_engine = not teclaw and engine not in SUPPORTED_ENGINE_TYPES

    def capability(construct: Construct, blocked_reason: str | None) -> Capability:
        """Deployment-wide refusals win over per-construct ones."""
        if desktop:
            return Capability(construct, False, _REASON_DESKTOP)
        if unknown_engine:
            return Capability(construct, False, _REASON_UNKNOWN_ENGINE)
        if blocked_reason:
            return Capability(construct, False, blocked_reason)
        return Capability(construct, True, "")

    blocked: dict[Construct, str | None] = {
        ManifestCategory.MCP: None,
        # Materialised since W6, through the one write chain
        # (`ResourceFileService`'s dispatcher) with tree-replacement
        # semantics for directory entries — see the resources materialiser.
        ManifestCategory.RESOURCES: None,
        ManifestCategory.SKILLS: None,
        ManifestCategory.ENGINE_CONFIG: _REASON_ENGINE_CONFIG,
        ManifestCategory.IDENTITY: None,
        # Materialised since W9, through ``CliToolService`` — the one
        # component the management API also installs through. Always
        # platform-managed, independent of the teclaw switch, as ``mcp`` is.
        ManifestCategory.CLI_TOOLS: None,
        ManifestSection.SCRIPT: _script_reason(teclaw=teclaw, desktop=desktop),
        # Materialised since W5 (skills/identity) and renamed-since-W7: the
        # declared-source dispatch in ``EntryFetcher.fetch_declared`` resolves
        # both forms for the categories that fetch. The one (category, form)
        # pair still undelivered — resources × git/named, the URL-only road
        # W6 shipped — is refused per entry at schema validation, with a
        # reason that names the category, because a blanket row here cannot.
        SourceForm.URL: None,
        SourceForm.CONTENT: None,
        SourceForm.GIT: None,
        SourceForm.NAMED: None,
    }

    return ManifestCapabilities(
        engine_type=engine,
        bot_type=bot,
        schema_versions=SUPPORTED_SCHEMA_VERSIONS,
        # Every member of every construct enum, in declaration order. Built by
        # iterating the enums rather than by listing them again, so a construct
        # added to the vocabulary without a verdict is a KeyError here — at
        # import of the first call — instead of a silent "unsupported".
        constructs=tuple(
            capability(construct, blocked[construct])
            for construct in _all_constructs()
        ),
    )


def _all_constructs() -> Iterable[Construct]:
    """Every construct, categories first, then sections, then source forms."""
    yield from ManifestCategory
    yield from ManifestSection
    yield from SourceForm


def _script_reason(*, teclaw: bool, desktop: bool) -> str | None:
    """Why ``script`` is refused for this bot, or ``None`` when it is not."""
    if teclaw:
        return _REASON_TECLAW_SCRIPT
    if desktop:
        # Named for its own reason rather than falling through to the blanket
        # desktop one: this refusal predates the manifest (#926 refuses a
        # startup script on a desktop bot today) and survives if desktop ever
        # comes into scope for the rest of the vocabulary.
        return _REASON_DESKTOP_SCRIPT
    return None


def capabilities_for_bot(
    bot: dict[str, Any], is_teclaw: Callable[[str | None], bool]
) -> ManifestCapabilities:
    """The same answer, for a bot that already has a record.

    The whole body is the two-field read: the moment this does anything else,
    the "one function, two entry points" guarantee is gone and W13's preflight
    and this surface can disagree.
    """
    return resolve_capabilities(
        active_engine=bot.get("active_engine"),
        bot_type=bot.get("bot_type"),
        is_teclaw=is_teclaw,
    )
