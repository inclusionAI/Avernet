"""Request/response models for the models group."""

from __future__ import annotations

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
        description="Model identifier. Provider-qualified ids contain a slash "
        "(e.g. 'openai/gpt-5.3'); pass the value verbatim in the path of the "
        "single-model endpoint."
    )
    name: str = Field(description="Display name; may be empty.")
    provider: str = Field(description="Provider name; may be empty.")


__all__ = ["Model"]
