"""Versioned owner management values; no credentials or engine topology."""

import json
import unicodedata
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from engine.community.kernel.dws_hosting import Value


class HostingSettings(Value):
    rules: Annotated[str, Field(max_length=10000)] = ""
    window: Literal["always", "night", "custom"] = "always"
    custom_window: Annotated[str, Field(max_length=1000)] = ""
    timezone: Annotated[str, Field(max_length=100)] = "Asia/Shanghai"
    persona: Annotated[str, Field(max_length=2000)] = ""
    label_suffix: Annotated[str, Field(max_length=80)] = ""

    @model_validator(mode="after")
    def valid(self):
        ZoneInfo(self.timezone)
        if self.window == "custom" and not self.custom_window.strip():
            raise ValueError("custom_window_required")
        if any(
            unicodedata.category(c).startswith("C")
            or unicodedata.category(c) in {"Zl", "Zp"}
            for c in self.label_suffix
        ):
            raise ValueError("invalid_label_suffix")
        return self

    def instructions(self) -> str:
        return json.dumps(
            {
                "hosting_settings_version": 1,
                "settings": self.model_dump(),
                "time_policy": "Use incoming message timestamps in the configured timezone. night means 20:00-08:30. Outside the window ignore; uncertain -> escalate.",
            },
            ensure_ascii=False,
        )


def snapshot_suffix(rule_text: str) -> str:
    try:
        value = json.loads(rule_text)
    except ValueError:
        return ""
    if not isinstance(value, dict) or value.get("hosting_settings_version") != 1:
        return ""
    return HostingSettings.model_validate(value["settings"]).label_suffix


class SettingsUpdate(Value):
    expected_version: Annotated[int, Field(ge=0)]
    settings: HostingSettings


class EnabledUpdate(Value):
    enabled: bool
    expected_version: Annotated[int, Field(ge=0)]


class TakeoverUpdate(Value):
    conversation_id: Annotated[str, Field(min_length=1, max_length=512)]
    taken_over: bool


class PageQuery(Value):
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 20


class EmptyCommand(Value):
    pass
