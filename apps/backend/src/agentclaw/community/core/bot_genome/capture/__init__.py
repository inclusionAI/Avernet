"""Capture: a bot's current state as a spec, read through a narrow ``BotStateReader``."""

from agentclaw.community.core.bot_genome.capture.capture import (
    BotStateReader,
    CapturedFile,
    CapturedResource,
    CapturedSkill,
    CapturedSpec,
    capture_spec,
)

__all__ = ["BotStateReader", "CapturedFile", "CapturedResource", "CapturedSkill", "CapturedSpec", "capture_spec"]
