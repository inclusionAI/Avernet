"""Public management inputs. Identity and credentials are deliberately absent."""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Settings(Input):
    """Owner-confirmed natural-language hosting instructions."""

    rules: Annotated[
        str,
        Field(
            min_length=1,
            max_length=10000,
            description="Natural-language reply, ignore and escalation rules.",
        ),
    ]
    window: Literal["always", "night", "custom"] = Field(
        default="always",
        description="Hosting schedule: always; night 20:00-08:30; or custom instructions.",
    )
    custom_window: Annotated[
        str,
        Field(
            max_length=1000,
            description="Required natural-language schedule when window is custom.",
        ),
    ] = ""
    timezone: Annotated[
        str,
        Field(
            max_length=100,
            description="IANA timezone used to interpret message timestamps and schedule.",
        ),
    ] = "Asia/Shanghai"
    persona: Annotated[
        str,
        Field(
            max_length=2000,
            description="Optional reply style and persona instructions.",
        ),
    ] = ""
    label_suffix: Annotated[
        str,
        Field(
            max_length=80,
            description="Optional text appended to the mandatory AI label; cannot remove the label.",
        ),
    ] = ""


class SettingsUpdate(Input):
    """Optimistic update; stale versions never overwrite saved owner rules."""

    expected_version: Annotated[
        int,
        Field(
            ge=0,
            description="Version returned by GET settings; zero for the first save.",
        ),
    ]
    settings: Settings = Field(
        description="Complete owner-confirmed settings to save atomically."
    )


class EnabledUpdate(Input):
    """Explicit consent to start or stop automatic hosting."""

    enabled: bool = Field(
        description="True starts after authorization and rule checks; false stops and fences unsent work."
    )
    expected_version: Annotated[
        int,
        Field(
            ge=0,
            description="Saved rule version required to enable; disabling ignores stale versions.",
        ),
    ]


class TakeoverUpdate(Input):
    """Pause or resume automatic handling for one bound direct conversation."""

    conversation_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=512,
            description="DWS direct-conversation ID in this owner's hosting binding.",
        ),
    ]
    taken_over: bool = Field(
        description="True fences future automatic replies for the conversation; false resumes."
    )
