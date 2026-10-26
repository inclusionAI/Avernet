"""Recording is opt-in and stays enabled when remote delivery is paused."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SpaceKnowledgeCaptureConfig:
    enabled: bool = False
