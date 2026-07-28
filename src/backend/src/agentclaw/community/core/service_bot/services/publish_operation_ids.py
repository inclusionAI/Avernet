"""Correlation identifiers for publish-operation ledger rows."""

from __future__ import annotations

import hashlib
import re

from agentclaw.community.core.service_bot.repository.models import PublishOperationKind


def to_baas_request_id(readable: str) -> str:
    """Fold a readable correlation string into BaaS's request-id contract."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", readable)
    if len(safe) < 32:
        safe = f"{safe}_{hashlib.md5(readable.encode()).hexdigest()}"
    return safe[:64]


def operation_request_id(
    publish_id: int,
    operation_kind: PublishOperationKind,
    stage: str,
    attempt: int,
) -> str:
    """Build the deterministic correlation id for one ledger attempt."""
    parts = [f"pub_{publish_id}", str(operation_kind)]
    if stage:
        parts.append(stage)
    parts.append(f"a{attempt}")
    return to_baas_request_id("_".join(parts))
