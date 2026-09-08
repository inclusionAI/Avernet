"""Agent implementations (spec-driven, not hardcoded rosters).

- ``LLMBackedAgent`` — stateless, OpenAI-compatible chat completions.
- ``AvernetAgent`` — stateful, Avernet gateway bot lifecycle.
"""

from ._avernet import AvernetAgent
from ._base import LLMBackedAgent

__all__ = ["AvernetAgent", "LLMBackedAgent"]
