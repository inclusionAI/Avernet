"""What a bot can be told to do — one function, two entry points.

**The rule this module enforces.** *This surface never accepts something it
cannot apply.* Anything the schema can express but no shipped code can act on is
reported unsupported and refused at ``PUT``. The feature flag over the routes is
not enough on its own: W1 parses the **whole** v1 vocabulary while only part of
it has a materializer behind it, and the gap is not confined to categories — a
**(category, protocol) pair** with no resolver fails in exactly the same way,
which is what ``source_matrix`` answers.

So capabilities are answered **per accepted construct**, not per bot and not
only per category. Two kinds of construct appear here:

* ``category`` — one of the six under ``manifest``;
* ``section`` — a top-level section that is not a category (``script``).

A third kind, ``source``, published one row per source *spelling*. It is gone:
no spelling ever carried a verdict of its own in any configuration, so the
rows could not disagree, and which (category, protocol) pairs are open — the
question that was actually being asked — is answered by ``source_matrix`` on
``SourceKind``.

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
kind gets its own enum, those enums *are* the construct vocabulary, and
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
from typing import TYPE_CHECKING, Any, Callable, Iterable

from agentclaw.community.core.workspace.constants import (
    DEFAULT_ENGINE_TYPE,
    SUPPORTED_ENGINE_TYPES,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    # One-way dependency, deferred so it stays one way: ``support_matrix`` is
    # built ON this module's ``ManifestCategory``, so importing it here at
    # module scope would close the cycle. ``resolve_capabilities`` imports it
    # inside the call, where the module is already initialised.
    from agentclaw.community.core.bot_config_manifest.support_matrix import (
        SourceKind,
    )

class ConstructKind(StrEnum):
    """What sort of thing a construct is. Derived, never chosen at a call site."""

    CATEGORY = "category"
    SECTION = "section"


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


#: Either of the two. A value's own type says which kind it is, which is what
#: makes an ill-formed ``(kind, name)`` pair unwritable rather than merely
#: invalid.
#:
#: ``SourceForm`` used to be a third member, publishing one row per source
#: *spelling* (``oss``/``git``/``named``/``content``). Review asked what
#: ``named`` was doing there, and the check settled it: across all 50
#: engine/bot-type/teclaw configurations no form ever carried a verdict of
#: its own — the only refusal is bot-wide ("desktop bots are outside this
#: feature's scope") and hits all four identically. So the axis published four
#: rows that could never disagree, and the spelling/protocol split it forced
#: on the parser is the same one that produced the D5 bug recorded in
#: ``support_matrix.py``. Which (category, protocol) pairs are open was always
#: the real question, and ``source_matrix`` answers it on ``SourceKind``.
Construct = ManifestCategory | ManifestSection

_KIND_BY_TYPE: dict[type, ConstructKind] = {
    ManifestCategory: ConstructKind.CATEGORY,
    ManifestSection: ConstructKind.SECTION,
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
#: Public because the support matrix's ``engine_config`` cells carry it too.
#: One string, not two agreeing ones: the category row and the cell describe the
#: same fact, and a caller who reads both must not have to reconcile two
#: wordings — which is precisely the drift this feature's matrix exists to end.
REASON_ENGINE_CONFIG = (
    "engine_config was moved out of the first wave, so no materializer writes it"
)
_REASON_ENGINE_CONFIG = REASON_ENGINE_CONFIG
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
class SourceCell:
    """One (category, protocol) combination's verdict.

    The cartesian view the flat ``constructs`` array cannot express. A client
    reads this to decide what to write **before** writing it, for a combination
    rather than for a construct — which is the question callers actually have,
    and the one a 422 used to be the only way to answer.
    """

    category: ManifestCategory
    protocol: SourceKind
    supported: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "protocol": self.protocol.value,
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
    #: Every cell of (category × protocol), intersected with this bot's own
    #: verdicts. Additive: ``constructs`` keeps its exact shape and meaning.
    source_matrix: tuple[SourceCell, ...] = ()

    def cell(
        self, category: ManifestCategory, protocol: SourceKind
    ) -> SourceCell | None:
        """The verdict for one combination, or ``None`` if it has no row."""
        for entry in self.source_matrix:
            if entry.category is category and entry.protocol is protocol:
                return entry
        return None

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
            "source_matrix": [cell.as_dict() for cell in self.source_matrix],
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
    # Imported here, not at module scope: ``support_matrix`` imports this
    # module for ``ManifestCategory``. The dependency runs one way — the table
    # is built ON the vocabulary — and a top-level import here would close the
    # cycle. The same lazy-import reasoning ``legal_identity_types`` records.
    from agentclaw.community.core.bot_config_manifest.support_matrix import (
        SourceKind,
        refusal_for,
    )

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
        # Whether a *form* can be resolved at all. Which (category, protocol)
        # COMBINATIONS are open is a different question, answered by
        # ``source_matrix`` below — these flat rows structurally cannot express
        # a pair, which is what forced the per-category narrowing this change
        # removed. Every form resolves; the matrix says where.
    }

    def cell(category: ManifestCategory, protocol: SourceKind) -> SourceCell:
        """A cell, narrowed by this bot's own refusals.

        Two independent verdicts, intersected: the deployment-wide one (a
        desktop bot takes no category at all, so every cell closes) and the
        table's. The table's reason is passed through **verbatim** — the
        validator emits that same string, and a caller who compares the two
        must not find them worded differently.
        """
        if desktop:
            return SourceCell(category, protocol, False, _REASON_DESKTOP)
        if unknown_engine:
            return SourceCell(category, protocol, False, _REASON_UNKNOWN_ENGINE)
        category_verdict = blocked[category]
        if category_verdict:
            return SourceCell(category, protocol, False, category_verdict)
        refusal = refusal_for(category, protocol)
        if refusal is not None:
            return SourceCell(category, protocol, False, refusal.reason)
        return SourceCell(category, protocol, True, "")

    return ManifestCapabilities(
        engine_type=engine,
        bot_type=bot,
        schema_versions=SUPPORTED_SCHEMA_VERSIONS,
        source_matrix=tuple(
            cell(category, protocol)
            for category in ManifestCategory
            for protocol in SourceKind
        ),
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
    """Every construct, categories first, then sections."""
    yield from ManifestCategory
    yield from ManifestSection


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
