"""Publish-flow error types.

Kept in a leaf module so both the facade (``publish_flow_service``) and the
extracted collaborators (``ext_state``, the runners, the task handlers) can raise
the same error type without importing the facade — which would create a cycle.
The facade re-exports ``PublishFlowServiceError`` for backward compatibility.
"""
from __future__ import annotations


class PublishFlowServiceError(Exception):
    """Publish-flow service error."""

    pass


class DraftRestoreRetryableError(PublishFlowServiceError):
    """Draft restore failed in an in-doubt/transient external-workflow window.

    The operation ledger must remain non-terminal so the durable task retries the
    same attempt and ``acquire_workflow`` can adopt an already-issued BaaS
    workflow instead of opening a new attempt and submitting the mutation twice.
    """

    pass
