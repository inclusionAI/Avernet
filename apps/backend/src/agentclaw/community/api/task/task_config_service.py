"""Public re-export of the task_config Service API contract.

Adapters import the Protocol from here; the contract is defined in its owning
core module (``core/task/task_config/task_config_service_protocol.py``) so the
concrete service can inherit it without a ``core -> api`` waiver.
"""

from __future__ import annotations

from agentclaw.community.core.task.task_config.task_config_service_protocol import (
    TaskConfigServiceProtocol,
)

__all__ = ["TaskConfigServiceProtocol"]
