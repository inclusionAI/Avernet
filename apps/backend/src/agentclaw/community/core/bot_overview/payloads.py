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


class AICodingTemplateFactoryIdentity(BaseModel):
    """Which template-factory template the Bot was created from."""

    model_config = ConfigDict(extra="forbid")

    template_key: str = Field(
        description="Factory template key, e.g. 'normalCC'."
    )
    template_uid: str = Field(
        description="Stable uid of the resolved template snapshot."
    )
    template_version: str | None = Field(
        default=None,
        description="Template version; null when the snapshot does not "
        "record one.",
    )


class AICodingEnginePayload(BaseModel):
    """Engine-specific overview fields for AICoding and Claude Code Bots.

    Filled from the Bot record's coding-template fields. Everything here is
    non-user data about the Bot's shape and workspace; credentials that ride
    along in the template config (CodeFuse tokens, Yuque team tokens) are
    never surfaced. claude_code Bots that run in an AICoding image share this
    type, so the discriminator accepts both persisted engine names.
    """

    model_config = ConfigDict(extra="forbid")

    engine: Literal["aicoding", "claude_code"] = Field(
        description="The Bot's engine; selects this payload variant."
    )
    template_type: str | None = Field(
        default=None,
        description="Coding template type the Bot was created from, e.g. "
        "'applicationCoding' or 'personalCoding'; null when the Bot was not "
        "created from a coding template.",
    )
    template_version: str | None = Field(
        default=None,
        description="Version of the template the Bot was created from; "
        "null for hand-written coding configs.",
    )
    bot_type: str | None = Field(
        default=None,
        description="The Bot's shape, e.g. 'personal' or 'service'; null "
        "when not persisted on the Bot record.",
    )
    template_factory: AICodingTemplateFactoryIdentity | None = Field(
        default=None,
        description="Identity of the template-factory template this Bot was "
        "created from; null for hand-written coding configs.",
    )
    hosted_workspace_enabled: bool = Field(
        default=False,
        description="Whether the hosted workspace capability is enabled on "
        "the Bot (template_config capabilities.dima_workspace).",
    )
    devflow_workflow: str | None = Field(
        default=None,
        description="Name of the DevFlow workflow configured on the Bot; "
        "null when none is configured.",
    )
    code_repos: list[str] = Field(
        default_factory=list,
        description="Repository URLs the Bot works on, from the coding "
        "template's code-repo fields; empty when none are declared.",
    )
    yuque_kb_repos: list[str] = Field(
        default_factory=list,
        description="Yuque knowledge-base URLs the Bot reads; per-repo team "
        "tokens are never included.",
    )


EngineSpecificPayload = Annotated[
    Union[AICodingEnginePayload],
    Field(discriminator="engine"),
]
"""Discriminated union of every engine's payload type."""


__all__ = [
    "AICodingEnginePayload",
    "AICodingTemplateFactoryIdentity",
    "EngineSpecificPayload",
]
