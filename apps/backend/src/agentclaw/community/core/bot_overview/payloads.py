"""Engine-specific payload types for the Bot overview.

Each engine contributes **one** Pydantic model and adds it to
:data:`EngineSpecificPayload`. The union is discriminated on ``engine`` so the
generated OpenAPI document carries a typed ``oneOf`` with a discriminator
mapping, and a client can generate one typed class per engine.

Adding an engine:

1. Define ``<Engine>EnginePayload`` below with ``engine: Literal[...]`` naming
   every ``active_engine`` value it serves.
2. Add it to the ``Union`` in :data:`EngineSpecificPayload`.
3. Register the engine's provider in ``di/modules/bot_overview_module.py``.

Only non-user data belongs here: the overview is served without
authentication.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class AICodingEnginePayload(BaseModel):
    """Engine-specific overview fields for AICoding and Claude Code Bots."""

    # Intentionally empty: the engine owner adds fields here. claude_code Bots
    # that run in an AICoding image share this type, so the discriminator
    # accepts both persisted engine names.

    model_config = ConfigDict(extra="forbid")

    engine: Literal["aicoding", "claude_code"] = Field(
        description="The Bot's engine; selects this payload variant."
    )


EngineSpecificPayload = Annotated[
    Union[AICodingEnginePayload],
    Field(discriminator="engine"),
]
"""Discriminated union of every engine's payload type."""


__all__ = ["AICodingEnginePayload", "EngineSpecificPayload"]
