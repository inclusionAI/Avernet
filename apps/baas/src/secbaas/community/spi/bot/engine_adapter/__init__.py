"""BotEngineAdapter SPI — Protocol for aicoding / hermes / claude_code engine differences."""

from ._planned_id import extract_session_key_from_planned_id
from ._protocols import BotEngineAdapter

__all__ = ["BotEngineAdapter", "extract_session_key_from_planned_id"]
