"""Publish-flow error types.

Kept in a leaf module so both the facade (``publish_flow_service``) and the
extracted collaborators (``ext_state``, the runners, the task handlers) can raise
the same error type without importing the facade — which would create a cycle.
The facade re-exports ``PublishFlowServiceError`` for backward compatibility.
"""
from __future__ import annotations


class PublishFlowServiceError(Exception):
    """发布流程服务错误."""

    pass

