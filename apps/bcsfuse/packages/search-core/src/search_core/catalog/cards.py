"""Source envelope only: business JSON is stored without interpreting its fields."""

import re
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

NonBlank = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]


def is_label_filter(key: str) -> bool:
    return key.startswith("labels.") and bool(re.fullmatch(r"[\w-]+", key[7:]))


def label_filters(labels):
    return {f"labels.{key}": tuple(values) for key, values in labels.items()}


class SearchConfig(BaseModel):
    """Explicit local operations policy. Descriptive tags alone never force routing."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    match_modes: tuple[Literal["exact", "keyword", "semantic"], ...] = (
        "exact",
        "keyword",
        "semantic",
    )
    keyword_operator: Literal["any", "all"] = "any"
    action: Literal["prefer", "direct"] = "prefer"
    priority: int = Field(default=0, ge=-1000, le=1000, strict=True)


class CardSnapshot(BaseModel):
    """Internal storage metadata around an opaque business Card."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    agent_id: NonBlank
    resource_type: Literal["agent", "group"] = "agent"
    source_version: NonBlank
    labels: dict[str, list[str]] = Field(default_factory=dict)
    deleted: bool = False
    source_card: dict[str, JsonValue] = Field(default_factory=dict)
    search_config: SearchConfig = Field(default_factory=SearchConfig)


def business_card(snapshot: CardSnapshot) -> dict:
    """Read business data only, including legacy normalized business fields."""
    return (
        deepcopy(snapshot.source_card)
        if snapshot.source_card
        else snapshot.model_dump(
            mode="json",
            exclude={
                "agent_id",
                "resource_type",
                "source_version",
                "deleted",
                "search_config",
                "source_card",
                "labels",
            },
        )
    )
