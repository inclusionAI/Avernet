"""The closed, versioned capability catalog (03-strategy.zh-CN.md §2.3, §4).

A strategy declares ``needs`` against this catalog; the platform resolves, per bot
engine, which provider serves each entry. Changing the catalog is a reviewed
platform change, so it is a fixed table here rather than a plugin point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .params_schema import validate as validate_against_schema


class Engine(StrEnum):
    """The engines Avernet runs bots on."""

    OPENCLAW = "openclaw"
    CLAUDE_CODE = "claude-code"
    HERMES = "hermes"
    TECLAW = "teclaw"

    @classmethod
    def of(cls, value: str) -> Engine:
        """Parse a platform engine string; ``claude_code`` is the platform's spelling."""

        normalized = (value or "").strip().lower().replace("_", "-")
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(f"unknown engine {value!r}") from exc


class AnyEngine(StrEnum):
    """One provider serves every engine."""

    ANY = "*"


ProviderEngine = Engine | AnyEngine


class ContextPart(StrEnum):
    """The ``StrategyContext`` field a catalog entry fills."""

    CANDIDATES = "candidates"
    MODELS = "models"
    EXPERIENCE = "experience"
    AGENTS = "agents"
    EVALUATION = "evaluation"
    LEDGER = "ledger"


class CapabilityStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


@dataclass(frozen=True)
class CapabilityCall:
    """One method on the context part; ``operation`` marks long calls (§6)."""

    name: str
    operation: bool = False


@dataclass(frozen=True)
class Capability:
    name: str
    version: int
    context_part: ContextPart
    always_granted: bool
    calls: tuple[CapabilityCall, ...]
    arguments_schema: dict[str, Any] | None = None
    providers: dict[ProviderEngine, str] = field(default_factory=dict)
    status: CapabilityStatus = CapabilityStatus.ACTIVE

    @property
    def key(self) -> str:
        """``name@version``, the spelling used in ``needs`` and ``granted``."""

        return f"{self.name}@{self.version}"

    def serves(self, engine: Engine) -> str | None:
        """The provider id serving this entry for ``engine``, if any."""

        return self.providers.get(engine) or self.providers.get(AnyEngine.ANY)

    def validate_arguments(self, arguments: dict[str, Any] | None) -> list[str]:
        if self.arguments_schema is None:
            return []
        return validate_against_schema(
            arguments if arguments is not None else {}, self.arguments_schema
        )


_AGENTS_ARGUMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["definitions"],
    "additionalProperties": False,
    "properties": {
        "definitions": {
            "type": "object",
            "minProperties": 1,
            "additionalProperties": {
                "type": "object",
                "required": ["engine", "path"],
                "additionalProperties": False,
                "properties": {
                    "engine": {"type": "string", "enum": [e.value for e in Engine]},
                    "path": {"type": "string", "minLength": 1},
                    "digest": {"type": "string"},
                },
            },
        }
    },
}

#: The catalog of §4. ``always_granted`` entries are never declared in ``needs``.
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        name="candidates",
        version=1,
        context_part=ContextPart.CANDIDATES,
        always_granted=True,
        calls=(CapabilityCall("submit"), CapabilityCall("verdict")),
        providers={AnyEngine.ANY: "platform/candidates"},
    ),
    Capability(
        name="models",
        version=1,
        context_part=ContextPart.MODELS,
        always_granted=True,
        calls=(CapabilityCall("complete"),),
        providers={AnyEngine.ANY: "platform/models"},
    ),
    Capability(
        name="experience.sessions",
        version=1,
        context_part=ContextPart.EXPERIENCE,
        always_granted=False,
        calls=(CapabilityCall("sessions"),),
        providers={Engine.OPENCLAW: "engine-adapter/openclaw-session-export"},
    ),
    Capability(
        name="experience.feedback",
        version=1,
        context_part=ContextPart.EXPERIENCE,
        always_granted=False,
        calls=(CapabilityCall("feedback"),),
        providers={Engine.OPENCLAW: "engine-adapter/openclaw-session-export"},
    ),
    Capability(
        name="agents",
        version=1,
        context_part=ContextPart.AGENTS,
        always_granted=False,
        calls=(CapabilityCall("start", operation=True),),
        arguments_schema=_AGENTS_ARGUMENTS_SCHEMA,
        providers={Engine.OPENCLAW: "agents@1/openclaw"},
    ),
    Capability(
        name="evaluation.train",
        version=1,
        context_part=ContextPart.EVALUATION,
        always_granted=False,
        calls=(
            CapabilityCall("add_train_cases"),
            CapabilityCall("start_train", operation=True),
        ),
        providers={AnyEngine.ANY: "platform/train-evaluator"},
    ),
    Capability(
        name="ledger.read",
        version=1,
        context_part=ContextPart.LEDGER,
        always_granted=False,
        calls=(CapabilityCall("export"),),
        providers={AnyEngine.ANY: "platform/ledger"},
        status=CapabilityStatus.DEPRECATED,
    ),
)


class Catalog:
    """Lookup over the closed catalog, mirroring the platform's service (§9)."""

    def __init__(self, entries: tuple[Capability, ...] = CAPABILITIES) -> None:
        self._entries = {entry.key: entry for entry in entries}

    def list(self) -> list[Capability]:
        return sorted(
            self._entries.values(), key=lambda entry: (entry.name, entry.version)
        )

    def get(self, key: str) -> Capability | None:
        """A catalog entry by ``name@version``; ``None`` when the catalog does not carry it."""

        return self._entries.get(key)

    def provider(self, key: str, engine: Engine) -> str | None:
        entry = self.get(key)
        return entry.serves(engine) if entry else None

    def always_granted(self) -> list[str]:
        return sorted(
            key for key, entry in self._entries.items() if entry.always_granted
        )

    def granted_for(self, needs: dict[str, Any] | None) -> list[str]:
        """``granted`` as a strategy sees it: always-granted entries plus declared ones."""

        declared = sorted((needs or {}).keys())
        return sorted(set(self.always_granted()) | set(declared))

    def context_part(self, key: str) -> ContextPart | None:
        entry = self.get(key)
        return entry.context_part if entry else None


DEFAULT_CATALOG = Catalog()
