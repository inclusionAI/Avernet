"""Merge the supplied Worker/Profile sync fields without losing stored data."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from src.application.ports.worker_profile_content_store import WorkerProfileContentStore
from src.application.ports.worker_registry_store import WorkerRegistryStore
from src.domain.exceptions import WorkerNotFoundException
from src.domain.models.worker import Worker
from src.domain.models.worker_profile_content import WorkerProfileContent

_PROFILE_SYNC_MAX_ATTEMPTS = 5
_WORKER_SYNC_MAX_ATTEMPTS = 5


def merge_worker_fields(
    current: dict[str, Any],
    incoming: dict[str, Any],
    supplied: set[str],
) -> dict[str, Any]:
    """Apply only fields present in a sync request to an existing Worker."""
    merged = deepcopy(current)
    merged["identity"]["name"] = incoming["name"]
    if "description" in supplied:
        merged["identity"]["description"] = incoming["description"]
    for field in ("responsibilities", "domains", "capabilities", "skills"):
        if field in supplied:
            merged[field] = incoming[field]
    if "availability" in supplied:
        merged["state"]["availability"] = incoming["availability"]
    if "trust_level" in supplied:
        merged["state"]["trust_level"] = incoming["trust_level"]
    return merged


def save_merged_worker(
    store: WorkerRegistryStore,
    incoming: dict[str, Any],
    supplied: set[str],
    *,
    worker_id: str,
    active_profile_key: str | None,
) -> Worker:
    """Retry a sparse Worker merge after an atomic version conflict."""
    for _ in range(_WORKER_SYNC_MAX_ATTEMPTS):
        current = store.get_by_id(worker_id)
        if current is None:
            raise WorkerNotFoundException(worker_id)
        worker_data = merge_worker_fields(current.model_dump(), incoming, supplied)
        if active_profile_key is not None:
            worker_data["active_profile_key"] = active_profile_key
        try:
            return store.update(Worker.model_validate(worker_data))
        except ValueError as error:
            if not str(error).startswith("Version conflict:"):
                raise
    raise RuntimeError("worker changed during sync; retry the request")


def merge_profile_fields(
    current: dict[str, Any] | None,
    incoming: dict[str, Any],
    supplied: set[str],
    *,
    worker_id: str,
    profile_id: str,
    worker_name: str,
    description: str | None,
    description_supplied: bool,
) -> dict[str, Any]:
    """Merge maps by key; an explicitly empty map clears all its keys."""
    merged = deepcopy(current) if current is not None else {}
    merged["worker_id"] = worker_id
    merged["profile_id"] = profile_id
    if current is None:
        merged["created_at"] = datetime.now(timezone.utc).isoformat()
    merged["updated_at"] = datetime.now(timezone.utc).isoformat()

    if current is None or "display_name" in supplied:
        merged["display_name"] = incoming.get("display_name") if "display_name" in supplied else worker_name
    if current is None or "soul_md" in supplied:
        merged["soul_md"] = incoming.get("soul_md")
    if current is None or description_supplied:
        merged["description"] = description

    for field in ("contents", "metadata"):
        if field in supplied:
            patch = incoming[field]
            merged[field] = {**(merged.get(field) or {}), **patch} if patch else {}
        elif current is None:
            merged[field] = {}
    if "skill_sets" in supplied or current is None:
        merged["skill_sets"] = incoming.get("skill_sets", [])

    if "summary" in supplied:
        contents = merged["contents"]
        summary = incoming.get("summary")
        if summary is None:
            contents.pop("ecb_summary", None)
        else:
            contents["ecb_summary"] = {
                key: value for key, value in summary.items() if value is not None
            }
    return merged


def save_merged_profile(
    store: WorkerProfileContentStore,
    incoming: dict[str, Any],
    supplied: set[str],
    *,
    worker_id: str,
    profile_id: str,
    worker_name: str,
    description: str | None,
    description_supplied: bool,
    fallback_profile: str | None,
) -> tuple[dict[str, Any], bool]:
    """Retry a sparse merge against the latest committed Profile version."""
    for _ in range(_PROFILE_SYNC_MAX_ATTEMPTS):
        current = store.get(worker_id, profile_id)
        profile_data = merge_profile_fields(
            current.model_dump() if current is not None else None,
            incoming,
            supplied,
            worker_id=worker_id,
            profile_id=profile_id,
            worker_name=worker_name,
            description=description,
            description_supplied=description_supplied,
        )
        generated_fallback = False
        if (
            current is None
            and "profile" not in profile_data["contents"]
            and fallback_profile
        ):
            profile_data["contents"]["profile"] = fallback_profile
            generated_fallback = True
        saved = store.save_if_version(
            WorkerProfileContent.model_validate(profile_data),
            current.version if current is not None else None,
        )
        if saved is not None:
            return profile_data, generated_fallback
    raise RuntimeError("profile changed during sync; retry the request")


def profile_analysis_source_matches(
    snapshot: dict[str, Any], current: WorkerProfileContent,
) -> bool:
    """Reject analysis results based on source data that has since changed."""
    previous = WorkerProfileContent.model_validate(snapshot).model_dump(mode="json")
    latest = current.model_dump(mode="json")
    source_fields = (
        "display_name", "description", "soul_md", "agents_md", "tools_md",
        "boot_md", "heartbeat_md", "skill_sets", "metadata",
    )
    if any(previous[field] != latest[field] for field in source_fields):
        return False
    return previous["contents"] == latest["contents"]
