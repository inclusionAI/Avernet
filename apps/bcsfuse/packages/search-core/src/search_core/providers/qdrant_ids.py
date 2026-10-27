import uuid
from typing import Optional, Dict, Any

# Stable UUID namespace for deterministic point ID mapping
# This namespace ensures consistent mapping across all instances
# IMPORTANT: This is a FIXED project namespace constant - must NEVER be regenerated
# Same value as uuid.NAMESPACE_DNS, but explicitly documented as project constant
# Deterministic mapping guarantees same business ID always maps to same UUID
QDRANT_POINT_ID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _is_valid_qdrant_uuid(value: str) -> bool:
    """Check if a string is a valid UUID format for Qdrant point IDs.

    Qdrant local mode requires strict UUID format for point IDs.
    This function validates whether a given string can be used directly
    as a Qdrant point ID.

    Args:
        value: String to validate.

    Returns:
        True if the string is a valid UUID format.
    """
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _to_qdrant_point_id(external_id: str) -> str:
    """Map external ID to Qdrant point ID.

    If the external ID is already a valid UUID, it is returned as-is.
    Otherwise, a deterministic UUID v5 is generated from the external ID
    using a stable namespace.

    This ensures:
    1. Same external ID always maps to same Qdrant point ID
    2. Mapping is stable across process restarts
    3. Mapping does not depend on timestamp, random seed, or local path
    4. UUID IDs are preserved for backward compatibility

    Args:
        external_id: External ID (can be UUID or business ID).

    Returns:
        Qdrant point ID (always a valid UUID string).
    """
    if _is_valid_qdrant_uuid(external_id):
        # Already a valid UUID, use directly
        return external_id

    # Generate deterministic UUID v5 from external ID
    point_id = str(uuid.uuid5(QDRANT_POINT_ID_NAMESPACE, external_id))

    return point_id


def _prepare_payload(
    external_id: str, payload: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Prepare payload with external ID mapping.

    Ensures the external ID is stored in the payload so it can be
    retrieved later for returning logical IDs to callers.

    Args:
        external_id: External ID (business ID or UUID).
        payload: Original payload metadata.

    Returns:
        Payload with external_id field added.
    """
    result = dict(payload) if payload else {}
    result["_external_id"] = external_id
    return result


def _logical_id_from_result(point_id: str, payload: Dict[str, Any]) -> str:
    """Extract logical ID from Qdrant result.

    If the payload contains an external_id field, that is returned
    as the logical ID. Otherwise, the point ID is returned.

    This ensures:
    1. Business IDs are returned for vectors stored with business IDs
    2. UUID IDs are returned for vectors stored with UUID IDs
    3. Backward compatibility is maintained

    Args:
        point_id: Qdrant point ID (internal UUID).
        payload: Payload from Qdrant result.

    Returns:
        Logical ID (external_id or point_id).
    """
    if payload and "_external_id" in payload:
        return payload["_external_id"]
    return point_id
