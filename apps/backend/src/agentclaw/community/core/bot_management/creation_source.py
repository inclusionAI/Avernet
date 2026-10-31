"""Trusted Bot creation provenance and its execution-input association.

The Manifest layer validates its own binding and document. This layer owns
whether Bot provenance requires that binding, and the audit facts it records.
The persisted payload layout is retained for tasks accepted before this split.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_management.manifest_seam import create_job_idempotency_key


def validate_creation_source(spec: dict[str, Any]) -> None:
    """Refuse corrupt provenance before creation or configuration side effects."""
    origin = spec.get("expert_origin")
    if origin is None:
        return
    binding = spec.get("manifest_binding")
    if not isinstance(binding, dict) or not binding:
        raise ValueError("expert creation is missing its Manifest binding")
    if (not isinstance(origin, dict)
            or origin.get("manifest_digest") != binding.get("manifest_digest")
            or origin.get("digest_encoding") != binding.get("digest_encoding")):
        raise ValueError("expert origin does not match its Manifest binding")


def bind_creation_source(
    spec: dict[str, Any], *, tenant: str, env: str, entity_id: str, bot_id: str
) -> dict[str, Any]:
    """Freeze source facts alongside a generic Manifest execution binding."""
    origin = spec.get("expert_origin")
    if origin is None:
        return spec
    if not isinstance(origin, dict):
        raise ValueError("invalid expert creation source")
    origin = {**origin, "creation_task_key": create_job_idempotency_key(
        tenant=tenant, entity_id=entity_id, bot_id=bot_id,
    )}
    bound = {**spec, "expert_origin": origin, "manifest_binding": {
        "entity_id": entity_id, "bot_id": bot_id, "tenant": tenant, "env": env,
        "manifest_digest": origin.get("manifest_digest"),
        "digest_encoding": origin.get("digest_encoding"),
    }}
    validate_creation_source(bound)
    return bound
