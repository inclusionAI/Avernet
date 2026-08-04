"""Public enums for the engine-runtime groups.

Only value sets that are **genuinely closed at the source** get an enum here.

IMPORTANT — everything a class or field docstring says in this package is
published verbatim into the OpenAPI document that external tenants read.
Docstrings are therefore caller-facing prose only. Rationale, upstream defects,
internal component and route names, and deployment-tier details belong in ``#``
comments like these, which are not published.

Deliberately **not** enums, each checked against its source:

- Engine names: ``_get_engine_types()`` reads the ``ENGINE_TYPES`` environment
  variable (``core/workspace/constants.py``), so the set is deployment
  configuration. Validated at runtime, as the bots category does.
- Capability names: the engine's ``Capability`` enum is closed but documented as
  "adding new entries is safe", so a strict response enum would break on a
  backward-compatible engine release.
- Approval mode on responses: the engine's read path has no closed set. It
  accepts six spellings while advertising three, never canonicalises between
  them, and the local/singlebox stub answers ``"auto"``. See ``ApprovalMode``.
- Engine status internals (``process``, ``transition``): open dicts assembled ad
  hoc by ``EngineManager.status()``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class _DocumentedEnum(str, Enum):
    """Base for published enums; not itself part of the API."""

    # member value -> caller-facing meaning. Every member must be covered; the
    # schema-documentation gate fails the build otherwise.
    __descriptions__: dict[str, str] = {}

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Any:
        schema = handler(core_schema)
        if not cls.__descriptions__:
            return schema
        # OpenAPI has no native slot for per-member documentation, and the two
        # dominant generators disagree on the extension:
        #   openapi-generator reads x-enum-descriptions / x-enum-varnames as
        #     ARRAYS positionally parallel to `enum`
        #   NSwag reads x-enumNames
        # Emit all three so a generated client actually carries the docs. Member
        # order is declaration order in Pydantic, which is what makes the
        # parallel-array form well-defined.
        values = list(schema.get("enum", []))
        schema["x-enum-descriptions"] = [
            cls.__descriptions__.get(v, "") for v in values
        ]
        names = {m.value: m.name for m in cls}
        schema["x-enum-varnames"] = [names.get(v, str(v)) for v in values]
        schema["x-enumNames"] = list(schema["x-enum-varnames"])
        return schema


class SocketKind(_DocumentedEnum):
    """Which WebSocket a connection entry describes."""

    # One member today. A terminal socket was implemented and then removed: the
    # engine offers an interactive PTY and openclaw declares the capability, but
    # the spec excludes "arbitrary command execution and interactive shell on a
    # tenant's device ... at any scope" from v1. Kept as an enum over a list so
    # a second socket is additive rather than a shape change.

    CHAT = "chat"

    __descriptions__ = {"chat": "Converse with the bot."}


class ApprovalMode(_DocumentedEnum):
    """How much the bot may do without asking first."""

    # Request-only. Response fields carrying a mode are typed `str`, because the
    # engine's read path has no closed set: it accepts six spellings while
    # advertising three (`api/approvals/router.py`), never canonicalises between
    # them, echoes back the requested mode rather than the committed one, and
    # the local/singlebox stub answers "auto". Validating a response against
    # this enum would turn any of those into a public 500.
    #
    # The three members are exactly what the engine advertises. Its additional
    # accepted spellings are deliberately unpublished so one mode never has two
    # public names — which is also why they are not listed in this docstring.

    APPROVE = "approve"
    ON_MISS = "on-miss"
    NEVER = "never"

    __descriptions__ = {
        "approve": "Ask before every action.",
        "on-miss": "Ask only when the bot's policy cannot decide on its own.",
        "never": "Never ask; act autonomously.",
    }


class MessageRole(_DocumentedEnum):
    """Who or what produced a message in a session."""

    # Safe on a response: the engine declares it as a hard Literal in its own
    # session model, so the set is closed at the source rather than by
    # convention.

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"

    __descriptions__ = {
        "user": "Sent by the caller.",
        "assistant": "Produced by the bot.",
        "system": "Instruction or context supplied outside the conversation.",
        "tool_use": "The bot invoking a tool.",
        "tool_result": "The result a tool returned to the bot.",
    }


__all__ = ["ApprovalMode", "MessageRole", "SocketKind"]
