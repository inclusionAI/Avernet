"""LLM provider SPI: prompt → completion."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any, Protocol


class LLMProvider(Protocol):
    """Abstraction over a model invocation (prompt → completion)."""

    def complete(self, prompt: str, **kwargs: Any) -> str: ...

    def stream(self, prompt: str, **kwargs: Any) -> Iterator[str]: ...


class LLMProviderPlugin(ABC):
    """Base class for LLM provider plugin implementations."""

    @abstractmethod
    def complete(self, prompt: str, **kwargs: Any) -> str:
        raise NotImplementedError

    def stream(self, prompt: str, **kwargs: Any) -> Iterator[str]:
        yield self.complete(prompt, **kwargs)
