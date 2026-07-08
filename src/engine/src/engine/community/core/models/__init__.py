"""Core ``models`` domain — LLM catalogue protocol + shared Pydantic types.

Engine-agnostic types used by :class:`ModelsService` implementations under
``engines/<name>/models.py``. Kept side-by-side with ``core/cron``,
``core/skills``, etc.
"""
from engine.community.core.models.models import Model, ModelCapabilities, Provider
from engine.community.core.models.protocol import ModelsService

__all__ = ["Model", "ModelCapabilities", "ModelsService", "Provider"]
