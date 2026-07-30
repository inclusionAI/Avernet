"""Public enums for the engine-runtime groups.

Only value sets that are **genuinely closed at the source** live here. A
fabricated enum over an open vocabulary is worse than a plain string: on a
response field it turns an additive upstream change into a public 500, because
serialisation raises on the first value we did not anticipate.

Deliberately **not** enums, each checked against its source:

- **Engine names.** ``_get_engine_types()`` reads the ``ENGINE_TYPES``
  environment variable and falls back to a default list
  (``core/workspace/constants.py``), so the set is deployment configuration.
  The bots category types ``engine`` as ``str`` and validates at runtime
  against that function; the engine-runtime groups do the same.
- **Capability names.** The engine's ``Capability`` enum is closed but
  explicitly documented as "adding new entries is safe", so a strict response
  enum would break on a backward-compatible engine release.
- **Approval mode on responses.** See :class:`ApprovalMode` — request-only.
- **Engine status internals** (``process``, ``transition``) — assembled ad hoc
  by ``EngineManager.status()``; open dicts by construction.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class _DocumentedEnum(str, Enum):
    """A ``str`` enum that publishes per-member documentation.

    OpenAPI has no native slot for describing individual enum members, so the
    meanings would otherwise live only in prose and be lost to a client
    generator. ``x-enum-descriptions`` is the de-facto convention that
    generators (openapi-generator, NSwag, others) read to emit doc comments on
    the generated members.

    Attached to the **enum's own schema component** rather than to each field
    that references it, so one declaration covers every use.
    """

    #: member value -> human-readable meaning. Subclasses must cover every member.
    __descriptions__: dict[str, str] = {}

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Any:
        schema = handler(core_schema)
        if cls.__descriptions__:
            schema["x-enum-descriptions"] = dict(cls.__descriptions__)
        return schema


class SocketKind(_DocumentedEnum):
    """Which WebSocket a :class:`Socket` entry describes."""

    CHAT = "chat"
    TERMINAL = "terminal"

    __descriptions__ = {
        "chat": "Converse with the bot. Present for every engine that serves a "
        "chat socket.",
        "terminal": "Interactive shell on the bot's device. Present only when "
        "the engine declares the web-shell capability.",
    }


class ApprovalMode(_DocumentedEnum):
    """How much the bot may do without asking first.

    **Request-only.** Response fields carrying a mode are typed ``str``: the
    engine validates against six values while advertising three, does not
    canonicalise between them, echoes back the *requested* mode rather than the
    committed one, and the local/singlebox stub answers ``"auto"`` — a value
    outside every one of those sets. Validating a response against this enum
    would turn a stub's quirk into a public 500.

    The three members are the set the engine advertises via
    ``GET /api/approvals/modes``; the aliases it also accepts (``always``,
    ``on_miss``, ``off``) are deliberately not published, so one mode never has
    two public spellings.
    """

    APPROVE = "approve"
    ON_MISS = "on-miss"
    NEVER = "never"

    __descriptions__ = {
        "approve": "Ask before every action.",
        "on-miss": "Ask only when the bot's policy cannot decide on its own.",
        "never": "Never ask; act autonomously.",
    }


class MessageRole(_DocumentedEnum):
    """Who or what produced a message in a session.

    The one engine-sourced vocabulary safe on a response: it is a hard
    ``Literal`` in the engine's own model
    (``src/engine/.../core/session/models.py``), not a convention.
    """

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"

    __descriptions__ = {
        "user": "Sent by the caller.",
        "assistant": "Produced by the bot.",
        "system": "Instruction or context injected outside the conversation.",
        "tool_use": "The bot invoking a tool.",
        "tool_result": "The result returned to the bot by a tool.",
    }


__all__ = ["ApprovalMode", "MessageRole", "SocketKind"]
