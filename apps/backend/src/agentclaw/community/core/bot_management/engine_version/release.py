"""Complete branch equality; only strict REL calendar dates have an order."""

from __future__ import annotations

import re
from datetime import datetime

from .models import CurrentVersion, Detection, Comparison, ReleaseTarget

_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}\Z")
_REL = re.compile(r"REL[0-9]{8}\Z")


def valid_release(value: str) -> bool:
    return bool(_BRANCH.fullmatch(value)) and value != "0.1.153"


def release_target(value: str) -> ReleaseTarget:
    if not value:
        return {"status": "unavailable", "reason": "target_not_configured"}
    if not valid_release(value):
        return {"status": "unavailable", "reason": "target_invalid"}
    return {"status": "available", "release_id": value, "source": "backend_image_env"}


def compare(
    detection: Detection, current: CurrentVersion, target: ReleaseTarget
) -> Comparison:
    if target["status"] != "available":
        return "unknown"
    if detection["status"] == "legacy":
        return "legacy"
    if detection["status"] != "known":
        return "unknown"
    actual, expected = current["release_id"], target["release_id"]
    if actual == expected:
        return "same"
    if not (_REL.fullmatch(actual) and _REL.fullmatch(expected)):
        return "unknown"
    try:
        old_date = datetime.strptime(actual[3:], "%Y%m%d").date()
        target_date = datetime.strptime(expected[3:], "%Y%m%d").date()
    except ValueError:
        return "unknown"
    return "older" if old_date < target_date else "newer"
