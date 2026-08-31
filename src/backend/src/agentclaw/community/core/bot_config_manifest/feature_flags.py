"""Feature flags for the bot config manifest surface.

The public routes ship **dark** until the first materializers land (W5 / M3):
``PUT/GET/DELETE …/config-manifest`` are public endpoints on an assembled
surface, and a caller discovering route-shaped 404s is strictly worse than
not having validated the shape yet. Same pattern, same rationale as
``core/skill_center/feature_flags.py`` (``SkillCenterFlags`` reads ``SC_*``).

No module-level cached snapshot, on purpose: the architecture gate forbids
lazy DCL singletons, and the read is one ``os.environ.get`` on a path the
router already touches — caching it would only pin the first read against
per-request overrides in tests.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

#: Routes answer 404 unless this is "1". Test environments set it in their
#: fixtures; singlebox can opt in; prod stays dark until the apply engine is
#: real (M3).
_ENV_FLAG = "BCM_API_ENABLED"


@dataclass(frozen=True)
class BotConfigManifestFlags:
    """只读开关快照。core 服务不受任何开关影响——受遮蔽的只有 HTTP.face。"""

    api_enabled: bool = False

    @classmethod
    def from_env(cls) -> "BotConfigManifestFlags":
        return cls(
            api_enabled=os.environ.get(_ENV_FLAG, "").lower()
            in ("1", "true", "yes")
        )


def get_bot_config_manifest_flags() -> BotConfigManifestFlags:
    """Read the flag from the environment on every call (see module docstring)."""
    return BotConfigManifestFlags.from_env()
