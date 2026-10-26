"""Model-catalogue domain errors."""
from __future__ import annotations


class DefaultModelNotConfiguredError(RuntimeError):
    """The engine could not name a default model.

    Raised by :meth:`ModelsService.get_default_model` when the engine's own
    config carries no default (OpenClaw with ``agents.defaults.model`` unset)
    or the engine cannot be asked (relay predates ``models.default``). Every
    engine is expected to resolve one, so this is an engine-side
    misconfiguration, not a normal "absent" state — the HTTP layer reports it
    as a refusal (``success: false``, ``error: "default_model_not_configured"``).
    """


__all__ = ["DefaultModelNotConfiguredError"]
