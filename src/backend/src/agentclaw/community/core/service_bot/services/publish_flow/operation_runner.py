"""Publish operation runner — the step engine for crash-safe BaaS mutations.

Every BaaS mutation in the publish pipeline goes through this runner as
``open (intent) -> acquire (workflow) -> finalize``. The intent row is persisted
BEFORE the BaaS call; the returned workflow id is persisted after; a crash-resume
reads the ledger and picks up at the first incomplete step (adopt-by-query for
existing-bot mutations, or a bounded-orphan for creations). Approval is delegated
to BaaS server-side auto-approval, so there is no approve step — the progress poll
drives the workflow to terminal.

See specs/2026-07-15-publish-service-idempotency/plan.md.

Task 4 lands the deterministic request-id helper below; Task 5 adds the
``PublishOperationRunner`` class in this module.
"""
from __future__ import annotations


def operation_request_id(
    publish_id: int,
    operation_kind: str,
    stage: str,
    attempt: int,
) -> str:
    """Deterministic, correlation-only request id for a logical operation.

    Form: ``pub{publish_id}.{kind}[.{stage}].a{attempt}`` (an empty ``stage`` is
    omitted rather than left as a double dot). Stable across re-runs of the same
    operation and distinct across different operations/attempts, so a BaaS log
    line traces back to the exact ledger step that issued it. BaaS treats this as
    an opaque string — it is never a dedup/idempotency key (verified: request_id
    is correlation-only server-side). Fits ``varchar(128)``: the kinds are ≤22
    chars, so even a 12-digit publish_id stays well under the limit.
    """
    parts = [f"pub{publish_id}", operation_kind]
    if stage:
        parts.append(stage)
    parts.append(f"a{attempt}")
    return ".".join(parts)
