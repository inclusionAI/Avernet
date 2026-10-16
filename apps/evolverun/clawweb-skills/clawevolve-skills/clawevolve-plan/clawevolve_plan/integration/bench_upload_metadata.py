"""Bench upload response helpers; no network or runtime state."""
from typing import Any

def _body(result: dict[str, Any]) -> dict[str, Any]:
    body = result.get("body")
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    return data if isinstance(data, dict) else body


def _body_list(result: dict[str, Any]) -> list[Any]:
    body = result.get("body")
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        return []
    for key in ("items", "templates"):
        if isinstance(body.get(key), list):
            return body[key]
    data = body.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "templates"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _imported_template_names(upload_result: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in _body_list(upload_result):
        if not isinstance(item, dict):
            continue
        if not item.get("imported"):
            continue
        name = str(item.get("templateName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _scanned_template_names(upload_result: dict[str, Any]) -> list[str]:
    """Return every template name recognized by the upload scan.

    Scan actions describe whether ClawWeb imported, skipped, or conflicted with a
    template. They do not change the identity of the local package. Exact-set
    validation therefore uses all recognized names, while publish selection
    remains action-aware.
    """
    names: list[str] = []
    for item in _body_list(upload_result):
        if not isinstance(item, dict):
            continue
        name = str(item.get("templateName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _template_hashes(result: dict[str, Any]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for item in _body_list(result):
        if not isinstance(item, dict):
            continue
        name = str(item.get("templateName") or "").strip()
        source_hash = str(item.get("sourceHash") or "").strip().lower()
        if name and source_hash:
            hashes[name] = source_hash
    return hashes


def _publish_candidate_names(upload_result: dict[str, Any]) -> list[str]:
    """Names that should be published after uploads/scan.

    A re-run after a successful scan but failed publish can return action=skip
    because the identical draft already exists. Those skipped items still need a
    batch-publish attempt, otherwise the flow gets stuck at uploaded_no_imports.
    Conflicts are excluded because ClawWeb intentionally did not import them.
    """
    names: list[str] = []
    for item in _body_list(upload_result):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "")
        if action == "conflict":
            continue
        if not item.get("imported") and action != "skip":
            continue
        name = str(item.get("templateName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _created_owner_user_id(create_result: dict[str, Any], fallback: str) -> str:
    return str(_body(create_result).get("ownerUserId") or fallback)


def _published_template_names(list_result: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in _body_list(list_result):
        if not isinstance(item, dict):
            continue
        name = str(item.get("templateName") or "").strip()
        if name and name not in names:
            names.append(name)
    return names
