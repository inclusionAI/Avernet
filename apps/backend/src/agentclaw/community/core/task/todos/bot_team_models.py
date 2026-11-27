"""Transport-neutral product records for Bot teams backed by Todo templates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from agentclaw.community.core.task.todos.template_models import TodoCompletionGate


class BotTeamStatus(StrEnum):
    """Product lifecycle of a Bot team."""

    ACTIVE = "active"
    ARCHIVED = "archived"

    __descriptions__: ClassVar[dict[str, str]] = {
        "active": "The Bot team can be selected for new Todos.",
        "archived": "The Bot team is retained for history but cannot be selected for new Todos.",
    }

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Any:
        schema = handler(core_schema)
        values = list(schema.get("enum", []))
        schema["x-enum-descriptions"] = [
            cls.__descriptions__.get(value, "") for value in values
        ]
        names = {member.value: member.name for member in cls}
        schema["x-enum-varnames"] = [
            names.get(value, str(value)) for value in values
        ]
        schema["x-enumNames"] = list(schema["x-enum-varnames"])
        return schema


@dataclass(frozen=True)
class BotTeamNode:
    """One Bot-owned node in the V1 linear Bot team workflow."""

    code: str
    name: str
    bot_id: str
    approval_required: bool
    completion_gate: TodoCompletionGate


@dataclass(frozen=True)
class BotTeamDefinition:
    """Mutable product input used to publish one immutable revision."""

    name: str
    description: str
    nodes: tuple[BotTeamNode, ...]


@dataclass(frozen=True)
class BotTeamRecord:
    """Stable product metadata mapped from one Todo template identity."""

    bot_team_id: str
    space_id: int
    name: str
    description: str
    status: BotTeamStatus
    published_revision_id: str | None
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BotTeamRevisionRecord:
    """One published immutable Bot team definition revision."""

    bot_team_revision_id: str
    bot_team_id: str
    revision_no: int
    name: str
    description: str
    nodes: tuple[BotTeamNode, ...]
    definition_hash: str
    created_by: str
    published_by: str
    published_at: datetime
    created_at: datetime
    updated_at: datetime
    product_profile_id: int | None = None


@dataclass(frozen=True)
class BotTeamDetail:
    """Bot team metadata plus its ordered published revisions."""

    bot_team: BotTeamRecord
    revisions: tuple[BotTeamRevisionRecord, ...]


@dataclass(frozen=True)
class BotTeamPage:
    """Database-filtered Bot team page."""

    total: int
    items: tuple[BotTeamRecord, ...]
