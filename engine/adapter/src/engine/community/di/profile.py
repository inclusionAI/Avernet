"""Engine deployment profile — corp | community | test.

Read once at startup. ``community`` is the default for open-source users;
internal OCB deployments must set ``ENGINE_PROFILE=corp`` explicitly.

``singlebox`` (the backend's local all-in-one deploy profile) is accepted as an
alias for ``community``: when the engine is spawned by a singlebox-mode backend
it inherits ``DEPLOY_PROFILE=singlebox``, and the engine has no corp
dependencies in that mode, so it must wire the community column rather than
crash on an unknown profile.
"""
from __future__ import annotations

import os
from enum import Enum


class EngineProfile(Enum):
    CORP = "corp"
    COMMUNITY = "community"
    TEST = "test"

    @classmethod
    def detect(cls) -> "EngineProfile":
        raw = (
            os.environ.get("ENGINE_PROFILE")
            or os.environ.get("DEPLOY_PROFILE")
            or "community"
        ).strip().lower()
        # singlebox is a backend-only local profile; the engine has no corp
        # wiring there, so treat it as community instead of raising.
        if raw == "singlebox":
            raw = "community"
        try:
            return cls(raw)
        except ValueError:
            raise RuntimeError(
                f"Unknown ENGINE_PROFILE/DEPLOY_PROFILE={raw!r}; "
                "expected corp|community|test|singlebox"
            ) from None
