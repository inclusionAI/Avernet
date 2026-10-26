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


class DefaultModel(Model):
    """The model the bot's engine uses when a session names none."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "model_id": "openai/gpt-5.3",
                "name": "GPT-5.3",
                "provider": "openai",
                "fallbacks": ["anthropic/claude-sonnet-4-5"],
            }
        }
    )

    fallbacks: list[str] = Field(
        default_factory=list,
        description="Models the engine tries, in order, when the default "
        "fails a request. Empty for engines without a fallback chain.",
    )


__all__ = ["DefaultModel", "Model"]
