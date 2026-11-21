"""Read-only projection of the platform's Todo template capabilities."""

from __future__ import annotations

from agentclaw.community.core.task.todos.template_capabilities import (
    TODO_TEMPLATE_CAPABILITY_REGISTRY,
    TodoTemplateCapabilities,
)
from agentclaw.community.core.task.todos.template_capability_protocol import (
    TodoTemplateCapabilityServiceProtocol,
)


class TodoTemplateCapabilityService(TodoTemplateCapabilityServiceProtocol):
    def get_capabilities(self) -> TodoTemplateCapabilities:
        return TODO_TEMPLATE_CAPABILITY_REGISTRY.capabilities
