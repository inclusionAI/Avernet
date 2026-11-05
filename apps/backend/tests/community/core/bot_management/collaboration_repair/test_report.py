"""Collaboration repair report aggregation — the plan's fixed semantics.

``report_status`` derives the whole-report status from per-item statuses only:
``failed`` / ``unknown`` / ``skipped_dependency`` are bad. An empty report is a programming error
because the service always produces the fixed three components.
"""

from __future__ import annotations

import pytest

from agentclaw.community.kernel.collaboration_repair import (
    COMPONENT_ORDER,
    RepairItem,
    report_status,
)


def test_unknown_is_not_success() -> None:
    items = (
        RepairItem("ownership", "succeeded"),
        RepairItem("profile", "unknown", "delivery_timeout"),
    )
    assert report_status(items) == "partial_failed"
    assert (
        report_status((RepairItem("ownership", "failed", "owner_drift"),)) == "failed"
    )


def test_empty_report_is_rejected() -> None:
    with pytest.raises(ValueError, match="items"):
        report_status(())


@pytest.mark.parametrize(
    "status",
    ["failed", "unknown", "skipped_dependency"],
)
def test_each_bad_status_without_success_is_failed(status: str) -> None:
    items = (RepairItem("profile", status, "delivery_timeout"),)
    assert report_status(items) == "failed"


def test_bad_alongside_succeeded_is_partial_failed() -> None:
    items = (
        RepairItem("ownership", "succeeded"),
        RepairItem("managers", "skipped_dependency"),
    )
    assert report_status(items) == "partial_failed"


def test_full_success_report_all_three_components() -> None:
    items = (
        RepairItem("ownership", "succeeded"),
        RepairItem("profile", "succeeded"),
        RepairItem("managers", "succeeded"),
    )
    assert report_status(items) == "succeeded"


def test_component_order_declares_exactly_three_components() -> None:
    """The kernel order IS the report shape, so its length is a contract.

    The service builds its report with ``zip(items, COMPONENT_ORDER,
    strict=True)``, which fails at runtime for every repair if a fourth
    component is ever added here without the service and schema catching
    up. Pinning the length turns that runtime failure into a CI failure.
    """
    assert COMPONENT_ORDER == (
        "ownership",
        "profile",
        "managers",
    )
    assert len(COMPONENT_ORDER) == 3
