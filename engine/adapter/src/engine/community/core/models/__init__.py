"""Core ``models`` domain — LLM catalogue protocol + shared Pydantic types.

Engine-agnostic types used by :class:`ModelsService` implementations under
``engines/<name>/models.py``. Kept side-by-side with ``core/cron``,
``core/skills``, etc.
"""
from engine.community.core.models.exceptions import DefaultModelNotConfiguredError
from engine.community.core.models.models import (
    DefaultModel,
    Model,
    ModelCapabilities,
    Provider,
    find_catalogue_model,
)
from engine.community.core.models.protocol import ModelsService

__all__ = [
    "DefaultModel",
    "DefaultModelNotConfiguredError",
    "Model",
    "ModelCapabilities",
    "ModelsService",
    "Provider",
    "find_catalogue_model",
]
