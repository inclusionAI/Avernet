"""Offline collaboration-team density projection.

The replay consumes only an in-memory observation dictionary.  It never starts
bcs-cli, opens a socket, resolves a credential, or changes a business resource.
Missing observations remain NOT_RUN; malformed or negative observations become
FAIL.  Formal bot-group API rows require an explicit local projection event and
otherwise become EXECUTION_GAP rather than synthetic PASS.
"""
from __future__ import annotations

from typing import Any

from ..run_state import RunStateError
from .contract import (
    DENSITY_OBLIGATION_COUNT,
    PARSED_OBLIGATION_COUNT,
    PARENT_OBLIGATION_COUNT,
    SOURCE_FILE,
    density_plan,
)

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_EXECUTION_GAP = "EXECUTION_GAP"


def _status(event: object, predicate: bool, *, execution_gap: bool = False) -> str:
    if event is None:
        return STATUS_EXECUTION_GAP if execution_gap else STATUS_NOT_RUN
    return STATUS_PASS if predicate else STATUS_FAIL


def mock_observations() -> dict[str, Any]:
    """Return a deterministic, credential-free local replay fixture."""
    return {
        "parents": {f"CLI-COLLAB-TEAM.{name}": STATUS_PASS for name in (
            "protect-visibility", "friend-request-reject", "group-create-read",
            "group-add-member", "group-list", "group-fuse", "group-complete",
            "group-terminate")},
        "visibility": {"protected_success": True, "protected": "protected", "public": "public"},
        "friend_request": {"success": True},
        "pending": {"request_id": "request-local-1", "from_bot": "eng", "to_bot": "cs", "status": "pending"},
        "rejection": {"success": True},
        "friend_list": {"cs_present": False},
        "group_create": {"group_id": "group-local-1", "driver": "pm", "participants": ["pm", "eng"]},
        "group_read": {"id": "group-local-1", "driver_bot": "pm"},
        "group_member_read": {"group_id": "group-local-1", "contains_qa": True},
        "cli_group_list": {"returned": 1, "contains_group": True},
        "formal_api": {"http_status": 200, "contains_group": True, "item_count": 1},
        "fuse": {"recommendation": "local-replay-recommendation"},
        "completed": {"status": "completed", "cleanup_status": 200},
        "terminated": {"status": "completed", "cleanup_status": 200},
    }


def _atomic_status(obligation_id: str, observations: dict[str, Any]) -> tuple[str, str]:
    v = observations.get
    checks = {
        "D01": ("visibility", lambda x: x.get("protected_success") is True),
        "D02": ("visibility", lambda x: x.get("protected") == "protected"),
        "D03": ("friend_request", lambda x: x.get("success") is True),
        "D04": ("pending", lambda x: (x.get("from_bot"), x.get("to_bot"), x.get("status")) == ("eng", "cs", "pending")),
        "D05": ("pending", lambda x: bool(x.get("request_id"))),
        "D06": ("rejection", lambda x: x.get("success") is True),
        "D07": ("friend_list", lambda x: x.get("cs_present") is False),
        "D08": ("visibility", lambda x: x.get("public") == "public"),
        "D09": ("group_create", lambda x: bool(x.get("group_id"))),
        "D10": ("group_read", lambda x: x.get("id") == "group-local-1"),
        "D11": ("group_read", lambda x: x.get("driver_bot") == "pm"),
        "D12": ("group_member_read", lambda x: x.get("contains_qa") is True),
        "D13": ("cli_group_list", lambda x: type(x.get("returned")) is int and x.get("returned") > 0),
        "D14": ("cli_group_list", lambda x: x.get("contains_group") is True),
        "D15": ("formal_api", lambda x: x.get("http_status") == 200),
        "D16": ("formal_api", lambda x: x.get("contains_group") is True),
        "D17": ("formal_api", lambda x: x.get("item_count") == observations.get("cli_group_list", {}).get("returned")),
        "D18": ("fuse", lambda x: bool(x.get("recommendation"))),
        "D19": ("completed", lambda x: x.get("status") == "completed"),
        "D20": ("completed", lambda x: x.get("cleanup_status") == 200),
        "D21": ("terminated", lambda x: x.get("status") == "completed"),
        "D22": ("terminated", lambda x: x.get("cleanup_status") == 200),
    }
    key = obligation_id.rsplit(".", 1)[-1]
    source, predicate = checks[key]
    event = v(source)
    return _status(event, predicate(event) if isinstance(event, dict) else False,
                   execution_gap=source == "formal_api"), source


def project(observations: dict[str, Any]) -> dict[str, Any]:
    """Project a local observation dictionary into a 30-row honest receipt."""
    if not isinstance(observations, dict):
        raise RunStateError("collaboration-team replay observations must be an object")
    plan = density_plan()
    rows = []
    for obligation in plan["obligations"]:
        if obligation["level"] == "PARENT":
            parent_events = observations.get("parents", {})
            status = parent_events.get(obligation["id"], STATUS_NOT_RUN)
            event = "parents"
        else:
            status, event = _atomic_status(obligation["id"], observations)
        rows.append({
            "obligation_id": obligation["id"],
            "level": obligation["level"],
            "method": obligation["method"],
            "status": status,
            "source": {"file": SOURCE_FILE, "line_start": obligation["line_start"],
                       "line_end": obligation["line_end"], "quote": obligation["quote"],
                       "located": True},
            "evidence_event": event,
        })
    counts = {status: sum(row["status"] == status for row in rows)
              for status in (STATUS_PASS, STATUS_FAIL, STATUS_NOT_RUN, STATUS_EXECUTION_GAP)}
    overall = (STATUS_PASS if counts[STATUS_PASS] == PARSED_OBLIGATION_COUNT
               else STATUS_FAIL if counts[STATUS_FAIL] else "BLOCKED")
    return {
        "contract": plan["contract"],
        "parent": plan["parent"],
        "status": overall,
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": 0,
        "counts": counts,
        "obligations": rows,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "execution": "owned-local-offline-replay",
    }
