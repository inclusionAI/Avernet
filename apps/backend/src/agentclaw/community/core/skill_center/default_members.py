"""Select the supply represented by historical Default membership rows."""

from collections.abc import Iterable, Mapping
from typing import Any


def effective_default_members(
    references: Iterable[tuple[int, str | None]], assets: Iterable[Mapping[str, Any]]
) -> tuple[dict[int, Mapping[str, Any]], dict[int, str]]:
    """Return raw-key-to-asset and confirmed unavailable raw-key reasons.

    A raw key is retained for exclusions. Center references use stable UUID and
    the highest published version; non-Center assets retain their raw identity.
    """
    catalog = list(assets)
    by_id = {int(asset["id"]): asset for asset in catalog}
    centers: dict[str, list[Mapping[str, Any]]] = {}
    for asset in catalog:
        uuid = asset.get("skill_uuid")
        if uuid and str(asset.get("git_path") or "").startswith("center://"):
            centers.setdefault(uuid, []).append(asset)
    result, skipped = {}, {}
    for raw_id, uuid in references:
        raw = by_id.get(raw_id)
        if (
            raw is not None
            and not uuid
            and str(raw.get("git_path") or "").startswith("center://")
        ):
            skipped[raw_id] = (
                "offline"
                if raw.get("offline_at") is not None
                or str(raw.get("status") or "").upper() in {"OFFLINE", "DELETED"}
                else "missing_center_identity"
            )
            continue
        center = centers.get(uuid, []) if uuid else []
        if center:
            center = [a for a in center if a.get("has_published_version") is not False]
            if not center:
                skipped[raw_id] = "no_published_version"
                continue
            asset = max(
                center, key=lambda a: (int(a.get("version") or 0), int(a["id"]))
            )
        elif (
            raw is not None
            and not str(raw.get("git_path") or "").startswith("center://")
            and (not uuid or raw.get("skill_uuid") == uuid)
        ):
            asset = raw
        else:
            skipped[raw_id] = "no_published_center" if uuid else "missing_asset"
            continue
        if not asset.get("git_path"):
            skipped[raw_id] = "missing_source"
            continue
        if asset.get("offline_at") is not None or str(
            asset.get("status") or ""
        ).upper() in {"OFFLINE", "DELETED"}:
            skipped[raw_id] = "offline"
            continue
        result[raw_id] = asset
    return result, skipped
