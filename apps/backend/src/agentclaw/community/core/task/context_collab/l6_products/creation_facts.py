"""Application decoder for lower creation facts and optional L6 selection.

ACCS does not define a ``ContextSpec`` aggregate.  The L4 Collet owns scope and
initiator, the L4 Task owns objective/executor, and L6 contributes only the
selected product/profile facts.  This module deliberately exposes dictionaries
and field-level helpers rather than introducing another aggregate DTO.
"""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    normalize_executor_refs,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)

_CREATION_FACT_KEYS = frozenset(
    {
        "objective",
        "resource_refs",
        "method_kind",
        "executor_ref",
        "executor_refs",
        "product_kind",
        "workflow_kind",
        "product_profile_ref",
        "product_instance",
    }
)


def creation_payload(
    *,
    method_kind: str,
    executor_ref: AgentRef | None,
    objective: str | None,
    resource_refs: tuple[ResourceRef, ...] | list[ResourceRef],
    product_kind: str | None,
    workflow_kind: str | None,
    product_profile_ref: VersionedProfileRef | None = None,
) -> dict:
    """Build the creation-event payload from the owning L4/L6 fields."""
    return {
        "objective": objective,
        "resource_refs": [ref.to_dict() for ref in resource_refs],
        "method_kind": MethodKind.from_wire(method_kind).value,
        "executor_ref": executor_ref.to_dict() if executor_ref else None,
        "product_kind": product_kind,
        "workflow_kind": workflow_kind,
        "product_profile_ref": (
            {
                "profile_id": product_profile_ref.profile_id,
                "version": product_profile_ref.version,
            }
            if product_profile_ref is not None
            else None
        ),
    }


def decode_creation_facts(data: dict[str, Any]) -> dict:
    """Validate and normalize persisted creation facts without creating a DTO."""
    if not isinstance(data, dict):
        raise ContextCollabValidationError(
            f"creation facts must be an object, got: {data!r}"
        )
    if data == {}:
        return {}  # Bare L4 Collet: no Work or composition facts.
    unknown = set(data) - _CREATION_FACT_KEYS
    if unknown:
        raise ContextCollabValidationError(
            f"creation facts have unknown keys: {sorted(unknown)}"
        )
    objective = data.get("objective")
    if objective is not None and (
        not isinstance(objective, str) or not objective.strip()
    ):
        raise ContextCollabValidationError("creation facts objective must be non-empty")
    if "executor_refs" in data:
        if (
            objective is None
            or set(data) != {"objective", "executor_refs"}
            or not isinstance(data["executor_refs"], list)
        ):
            raise ContextCollabValidationError(
                "invalid method-independent L4 creation facts"
            )
        if any(not isinstance(ref, dict) for ref in data["executor_refs"]):
            raise ContextCollabValidationError(
                "creation executor_refs must contain objects"
            )
        refs = normalize_executor_refs(
            tuple(AgentRef.from_dict(ref) for ref in data["executor_refs"])
        )
        return {
            "objective": objective,
            "executor_refs": [ref.to_dict() for ref in refs],
        }
    raw_resources = data.get("resource_refs")
    if not isinstance(raw_resources, list):
        raise ContextCollabValidationError(
            "creation facts resource_refs must be a list"
        )
    resources = [ResourceRef.from_dict(item) for item in raw_resources]
    method_kind = MethodKind.from_wire(data.get("method_kind")).value
    raw_executor = data.get("executor_ref")
    if raw_executor is not None and not isinstance(raw_executor, dict):
        raise ContextCollabValidationError(
            "creation facts executor_ref must be an object or null"
        )
    executor_ref = AgentRef.from_dict(raw_executor) if raw_executor else None
    raw_profile = data.get("product_profile_ref")
    if raw_profile is not None and not isinstance(raw_profile, dict):
        raise ContextCollabValidationError(
            "creation facts product_profile_ref must be an object or null"
        )
    profile_ref = (
        VersionedProfileRef(
            profile_id=raw_profile["profile_id"], version=raw_profile["version"]
        )
        if raw_profile is not None
        else None
    )
    product_kind = data.get("product_kind")
    workflow_kind = data.get("workflow_kind")
    for key, value in (
        ("product_kind", product_kind),
        ("workflow_kind", workflow_kind),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ContextCollabValidationError(
                f"creation facts {key} must be a non-empty string or null"
            )
    if profile_ref is not None and product_kind != profile_ref.profile_id:
        raise ContextCollabValidationError(
            "creation facts product_profile_ref does not match product_kind"
        )
    if objective is None and profile_ref is None:
        raise ContextCollabValidationError(
            "taskless product selection requires a pinned profile"
        )
    normalized = {
        "objective": objective,
        "resource_refs": [ref.to_dict() for ref in resources],
        "method_kind": method_kind,
        "executor_ref": executor_ref.to_dict() if executor_ref else None,
        "product_kind": product_kind,
        "workflow_kind": workflow_kind,
        "product_profile_ref": (
            {"profile_id": profile_ref.profile_id, "version": profile_ref.version}
            if profile_ref is not None
            else None
        ),
    }
    if "product_instance" in data:
        normalized["product_instance"] = data["product_instance"]
    return normalized


def creation_method_kind(facts: dict) -> str | None:
    return decode_creation_facts(facts).get("method_kind")


def creation_product_kind(facts: dict) -> str | None:
    return decode_creation_facts(facts).get("product_kind")


def creation_workflow_kind(facts: dict) -> str | None:
    return decode_creation_facts(facts).get("workflow_kind")


def creation_profile_ref(facts: dict) -> VersionedProfileRef | None:
    raw = decode_creation_facts(facts).get("product_profile_ref")
    return VersionedProfileRef(**raw) if raw is not None else None
