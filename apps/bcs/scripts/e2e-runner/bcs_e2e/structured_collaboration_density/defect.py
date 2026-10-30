"""Named honesty ledger for structured-collaboration density."""
from __future__ import annotations

from typing import Any

from ..run_state import RunStateError
from .contract import FIELD_OBLIGATION_COUNT, PARSED_OBLIGATION_COUNT

PREPROD_GAP_ID = "STRUCTURED_COLLAB_DENSITY_PREPROD_TRANSPORT_GAP"
PREPROD_GAP = {
    "id": PREPROD_GAP_ID,
    "status": "NOT_RUN",
    "detail": "remote structured-collaboration transport is not admitted to this offline replay",
    "resolution": "mainline may later inject an authorized live transport; this package does not open one",
}

# These are explicit upstream alternate branches, not silently normalized into
# the primary 400/200 parent assertions.  They are behavior variants, not a
# product defect claim; no product defect is invented where none is recorded.
DOCUMENTED_ALTERNATES = {
    "STRUCTURED_DENSITY.P13": {"status": "UPSTREAM_ALTERNATE", "http_status": 409, "source_line": 953},
    "STRUCTURED_DENSITY.P14": {"status": "UPSTREAM_ALTERNATE", "http_status": 409, "source_line": 965},
}
PRODUCT_DEFECTS: dict[str, dict[str, Any]] = {}


def defect_gap_ledger() -> dict[str, Any]:
    return {
        "product_defects": dict(PRODUCT_DEFECTS),
        "documented_alternates": dict(DOCUMENTED_ALTERNATES),
        "gaps": {PREPROD_GAP_ID: dict(PREPROD_GAP)},
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "field_atomic_count": FIELD_OBLIGATION_COUNT,
    }


def assert_honesty_ledger() -> None:
    if PRODUCT_DEFECTS:
        for defect_id, row in PRODUCT_DEFECTS.items():
            if not defect_id or not row.get("status") or not row.get("detail"):
                raise RunStateError("every product defect must be named and described")
    if PREPROD_GAP_ID != PREPROD_GAP["id"] or PREPROD_GAP["status"] != "NOT_RUN":
        raise RunStateError("preprod gap ledger changed")
    if set(DOCUMENTED_ALTERNATES) != {"STRUCTURED_DENSITY.P13", "STRUCTURED_DENSITY.P14"}:
        raise RunStateError("structured alternate branch ledger changed")
