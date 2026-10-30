"""Honesty ledger for the offline Group Context density projection."""
from __future__ import annotations

from typing import Any

from ..run_state import RunStateError
from .contract import FIELD_OBLIGATION_COUNT, PARSED_OBLIGATION_COUNT

GROUP_CONTEXT_PREPROD_GAP_ID = "GROUP_CONTEXT_DENSITY_PREPROD_TRANSPORT_GAP"
PREPROD_GAP = {
    "id": GROUP_CONTEXT_PREPROD_GAP_ID,
    "status": "NOT_RUN",
    "detail": "preprod Group Context product-CLI transport is not admitted to this offline replay",
    "resolution": "mainline may later inject an authorized live transport; this package never opens one",
}
PRODUCT_DEFECTS: dict[str, dict[str, Any]] = {}


def honesty_ledger() -> dict[str, Any]:
    return {
        "product_defects": dict(PRODUCT_DEFECTS),
        "gaps": {GROUP_CONTEXT_PREPROD_GAP_ID: dict(PREPROD_GAP)},
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "field_atomic_count": FIELD_OBLIGATION_COUNT,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }


def assert_honesty_ledger() -> None:
    if PRODUCT_DEFECTS:
        raise RunStateError("Group Context has no registered product defect for this projection")
    if PREPROD_GAP["id"] != GROUP_CONTEXT_PREPROD_GAP_ID or PREPROD_GAP["status"] != "NOT_RUN":
        raise RunStateError("Group Context preprod gap ledger changed")
