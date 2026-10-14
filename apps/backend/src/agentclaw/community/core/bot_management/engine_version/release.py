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


def detect(response: object) -> tuple[Detection, CurrentVersion]:
    if not isinstance(response, dict) or response.get("success") is not True:
        return {"status": "read_failed", "reason": "probe_invalid_response"}, {}
    data = response.get("data")
    if not isinstance(data, dict):
        return {"status": "read_failed", "reason": "probe_invalid_response"}, {}
    stdout, stderr, code = data.get("stdout"), data.get("stderr"), data.get("exit_code")
    if (
        not isinstance(stdout, str)
        or not isinstance(stderr, str)
        or type(code) is not int
    ):
        return {"status": "read_failed", "reason": "probe_invalid_response"}, {}
    if stderr or code not in {0, 1} or (code == 1 and stdout):
        return {"status": "read_failed", "reason": "probe_command_failed"}, {}
    # Remove one protocol line ending, never whitespace or repeated newlines.
    value = stdout.removesuffix("\n")
    if stdout.endswith("\r\n"):
        value = value.removesuffix("\r")
    if not value or value == "0.1.153":
        current: CurrentVersion = (
            {"raw_version": value, "source": "engine_env"} if value else {}
        )
        return {
            "status": "legacy",
            "reason": "legacy_default" if value else "version_missing",
        }, current
    if not valid_release(value):
        return {"status": "read_failed", "reason": "probe_invalid_output"}, {}
    return {"status": "known"}, {"release_id": value, "source": "engine_env"}


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
