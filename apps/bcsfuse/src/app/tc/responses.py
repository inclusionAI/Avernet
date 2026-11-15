"""Flat v1 response projections retained for existing TC clients."""

from app.adapters.http.schemas.worker_management_schemas import ProfileResponse


def worker_response(catalog, row):
    worker = catalog.worker(row).model_dump(mode="json")
    identity, state = worker["identity"], worker["state"]
    return {
        "success": True,
        "id": worker["id"],
        "type": worker["type"],
        "name": identity["name"],
        "handle": identity["handle"],
        "description": identity["description"],
        "lifecycle_state": worker["lifecycle_state"],
        "runtime_state": state["runtime_state"],
        "source_type": worker["source_type"],
        "version": row.revision,
        "responsibilities": worker["responsibilities"],
        "domains": worker["domains"],
        "availability": state["availability"],
        "trust_level": state["trust_level"],
        "created_at": worker["created_at"],
        "updated_at": worker["updated_at"],
    }


def profile_response(catalog, row):
    data = catalog.profile(row).model_dump(mode="json")
    # Existing diagnostics read contents.short_profile; expose raw description as a view.
    if data.get("description") and not data["contents"].get("profile"):
        data["contents"]["short_profile"] = data["description"]
    return {
        **ProfileResponse.model_validate(data).model_dump(mode="json"),
        "description": data["description"],
    }
