"""Transport-neutral service contract for Todo template capabilities."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.todos.template_capabilities import (
    TodoTemplateCapabilities,
)


@runtime_checkable
class TodoTemplateCapabilityServiceProtocol(Protocol):
    @abstractmethod
    def get_capabilities(self) -> TodoTemplateCapabilities: ...
