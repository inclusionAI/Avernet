"""Diagnostics preserve legacy counts and expose only finite metric labels."""

from concurrent.futures import ThreadPoolExecutor
import logging

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.diagnostics import (
    SyncDiagnostics,
    SyncEvent,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SyncKey,
    SyncOutcome,
    SyncWorkKind,
)


@pytest.mark.parametrize(
    "code,classified",
    [
        ("read_timeout", "read_timeout"),
        ("principal_mint_failed", "principal_mint_failed"),
        ("unclassified_delivery", "unclassified_delivery"),
        ("http_403", "http_403"),
        ("http_599", "http_599"),
        ("http_999", "unclassified_delivery"),
        ("http_403\nsecret", "unclassified_delivery"),
        ("http_４０３", "unclassified_delivery"),
        ("secret payload", "unclassified_delivery"),
        ("", "none"),
    ],
)
def test_error_codes_classified_without_raw_error_text(caplog, code, classified):
    diagnostics = SyncDiagnostics()
    with caplog.at_level(logging.INFO):
        diagnostics.record(
            SyncEvent.SEND_REJECTED,
            key=SyncKey("tenant", "dev", 7),
            outcome=SyncOutcome.FAILED,
            attempt=2,
            work_kind=SyncWorkKind.OWNERSHIP,
            request_id="bcs-request",
            elapsed_ms=1234,
            error_code=code,
        )
    assert f"error_code={classified}" in caplog.text
    assert "kind=ownership" in caplog.text
    assert "tenant=tenant env=dev bot_pk=7" in caplog.text
    assert "attempt=2" in caplog.text
    assert "elapsed_ms=1234" in caplog.text
    assert "request_id=bcs-request" in caplog.text
    assert "reason=none" in caplog.text
    assert "secret" not in caplog.text
    assert "payload" not in caplog.text
    assert diagnostics.counted == {("send_rejected", "failed"): 1}
    assert diagnostics.counted_by_kind == {("ownership", "send_rejected", "failed"): 1}


def test_legacy_default_and_kind_counters_share_lock_without_duplicate_increment():
    diagnostics = SyncDiagnostics()

    def record(index):
        diagnostics.record(
            SyncEvent.SEND_CONFIRMED,
            key=SyncKey(f"tenant-{index}", "dev", index),
            outcome=SyncOutcome.CONFIRMED,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(record, range(100)))
    assert diagnostics.counted == {("send_confirmed", "confirmed"): 100}
    assert diagnostics.counted_by_kind == {
        ("managers", "send_confirmed", "confirmed"): 100
    }
    copy = diagnostics.counted_by_kind
    copy.clear()
    assert len(diagnostics.counted_by_kind) == 1


def test_invalid_kind_does_not_increment():
    diagnostics = SyncDiagnostics()
    with pytest.raises(ValueError):
        diagnostics.record(
            SyncEvent.SEND_CONFIRMED,
            key=SyncKey("tenant", "dev", 7),
            outcome=SyncOutcome.CONFIRMED,
            work_kind="unbounded-label",
        )
    assert diagnostics.counted == {}
    assert diagnostics.counted_by_kind == {}
