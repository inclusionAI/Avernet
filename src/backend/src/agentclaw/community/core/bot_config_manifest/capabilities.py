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

**No third "unknown" state.** Support is a property of the bot record, never of
a live lookup (work-items §2.5, and the same reasoning ``bot_startup_script``
records for its own support check): ``is_teclaw`` is the engine authority and
``bot_type`` is a column. A device-binding read that happened to be failing must
never look like a verdict about what a bot supports.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agentclaw.community.core.workspace.constants import (
    DEFAULT_ENGINE_TYPE,
    SUPPORTED_ENGINE_TYPES,
)

#: Construct kinds. Strings rather than an enum because they cross the HTTP
#: boundary verbatim and a client reads them as strings either way.
KIND_CATEGORY = "category"
KIND_SECTION = "section"
KIND_SOURCE = "source"

#: The six categories under ``manifest``, in the order schema §1 lists them.
#: This is also the *closed* set the parser admits: an unknown key under
#: ``manifest`` is refused, not ignored.
CATEGORIES: tuple[str, ...] = (
    "mcp",
    "resources",
    "skills",
    "engine_config",
    "identity",
    "cli_tools",
)

#: Top-level sections that are not categories.
SECTION_SCRIPT = "script"

#: How an entry names its content. Four forms, and the distinction is
#: load-bearing for support: two of them have no resolver in the first wave.
#: ``git`` covers both spellings of a git source — inline on an entry and
#: declared under ``sources`` — because one resolver would serve both and
#: neither has it yet.
SOURCE_URL = "url"
SOURCE_GIT = "git"
SOURCE_NAMED = "named"
SOURCE_CONTENT = "content"

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
_REASON_CLI_TOOLS = (
    "cli_tools is deferred (W9): nothing materializes a tool, puts it on PATH, "
    "or carries it in an artifact"
)
_REASON_ENGINE_CONFIG = (
    "engine_config was moved out of the first wave, so no materializer writes it"
)
_REASON_NAMED_SOURCE = (
    "named sources (`from`) are resolved by the named-and-git source work item "
    "(W7), which has not landed"
)
_REASON_GIT_SOURCE = (
    "git sources are resolved by the named-and-git source work item (W7), which "
    "has not landed"
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

    kind: str
    name: str
    supported: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        """The wire shape. Public contract — flat, and stable across versions."""
        return {
            "kind": self.kind,
            "name": self.name,
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

    def find(self, kind: str, name: str) -> Capability | None:
        """The verdict for one construct, or ``None`` if it is not a construct."""
        for capability in self.constructs:
            if capability.kind == kind and capability.name == name:
                return capability
        return None

    def supports(self, kind: str, name: str) -> bool:
        """Whether the construct is supported.

        An unrecognised construct is **not** supported. That default is the
        conservative half of this module's rule: a name nobody has ruled on is a
        name nothing applies.
        """
        capability = self.find(kind, name)
        return capability is not None and capability.supported

    def reason_for(self, kind: str, name: str) -> str:
        """Why the construct is unsupported; ``""`` when it is supported."""
        capability = self.find(kind, name)
        if capability is None:
            return f"unknown {kind}: {name}"
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

    def verdict(blocked_reason: str | None) -> tuple[bool, str]:
        """Deployment-wide refusals win over per-construct ones."""
        if desktop:
            return False, _REASON_DESKTOP
        if unknown_engine:
            return False, _REASON_UNKNOWN_ENGINE
        if blocked_reason:
            return False, blocked_reason
        return True, ""

    def capability(kind: str, name: str, blocked_reason: str | None) -> Capability:
        supported, reason = verdict(blocked_reason)
        return Capability(kind=kind, name=name, supported=supported, reason=reason)

    blocked_by_category: dict[str, str | None] = {
        "mcp": None,
        # Accepted with no materializer *yet* (W6) — which is exactly what the
        # feature flag over these routes is for, and why that flag lifts at W8
        # rather than at W5. An accepted document sits inert until then.
        "resources": None,
        "skills": None,
        "engine_config": _REASON_ENGINE_CONFIG,
        "identity": None,
        "cli_tools": _REASON_CLI_TOOLS,
    }

    script_reason: str | None = None
    if teclaw:
        script_reason = _REASON_TECLAW_SCRIPT
    elif desktop:
        # Named for its own reason rather than falling through to the blanket
        # desktop one: this refusal predates the manifest (#926 refuses a
        # startup script on a desktop bot today) and survives if desktop ever
        # comes into scope for the rest of the vocabulary.
        script_reason = _REASON_DESKTOP_SCRIPT

    constructs: list[Capability] = [
        capability(KIND_CATEGORY, name, blocked_by_category[name])
        for name in CATEGORIES
    ]
    constructs.append(capability(KIND_SECTION, SECTION_SCRIPT, script_reason))
    constructs.extend(
        [
            capability(KIND_SOURCE, SOURCE_URL, None),
            capability(KIND_SOURCE, SOURCE_CONTENT, None),
            capability(KIND_SOURCE, SOURCE_GIT, _REASON_GIT_SOURCE),
            capability(KIND_SOURCE, SOURCE_NAMED, _REASON_NAMED_SOURCE),
        ]
    )

    return ManifestCapabilities(
        engine_type=engine,
        bot_type=bot,
        schema_versions=SUPPORTED_SCHEMA_VERSIONS,
        constructs=tuple(constructs),
    )


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
