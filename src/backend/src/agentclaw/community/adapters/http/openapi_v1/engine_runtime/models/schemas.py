"""Request/response models for the models group."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    """A model the bot's engine can route to."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "model_id": "openai/gpt-5.3",
                "name": "GPT-5.3",
                "provider": "openai",
            }
        }
    )

    model_id: str = Field(
        description="Normalised model identifier. Contains a slash for "
        "provider-qualified models (e.g. 'openai/gpt-5.3'); pass it verbatim in "
        "the path of the per-model endpoint."
    )
    name: str = Field(description="Display name; empty if the engine reports none.")
    provider: str = Field(description="Provider name; empty if not reported.")


__all__ = ["Model"]
