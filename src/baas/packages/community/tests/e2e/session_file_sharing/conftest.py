"""Fixtures for Session File Sharing E2E tests.

Session E2E conftest is lighter than Bot File Transfer conftest:
only 2 fixtures needed (StubFileTransferBackend + mock SessionTicketRepository)
because Session transfers have no paas_device_id, direction, device_path,
or poller.  See .planning/phases/80-testing/80-CONTEXT.md D-03.

All E2E tests use dispatcher-direct invocation (no HTTP round-trip) per
CONTEXT.md Claude's Discretion — the HTTP layer is already covered by
TEST-03 Router tests.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from tests.utils.stub_file_transfer_backend import StubFileTransferBackend


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def stub_oss_backend() -> StubFileTransferBackend:
    """In-memory OSS simulator for E2E Session File Sharing tests.

    Function-scoped (the default) so ``_storage`` is reset between tests,
    preventing cross-test state leakage.
    """
    return StubFileTransferBackend()


@pytest.fixture
def mock_session_ticket_repo() -> MagicMock:
    """MagicMock for SessionTicketRepository — configure return_value per test."""
    from secbaas.core.repository.session_file_ticket import SessionTicketRepository

    return MagicMock(spec=SessionTicketRepository)


# ── Helper functions ──────────────────────────────────────────────────


def _make_session_ticket_record(**overrides) -> MagicMock:
    """Create a MagicMock(spec=SessionTicketRecord) with Session defaults.

    SessionTicketRecord has NO direction, device_path, paas_device_id, or
    download_url fields — these are Bot-specific fields absent from the
    Session schema.

    Args:
        **overrides: Any SessionTicketRecord field to override.

    Returns:
        A MagicMock that passes isinstance(..., SessionTicketRecord) checks
        and supports attribute access for all 14 SessionTicketRecord fields.
    """
    from secbaas.core.repository.session_file_ticket import SessionTicketRecord

    now = datetime.now()
    return MagicMock(
        spec=SessionTicketRecord,
        **{
            "id": 1,
            "gmt_create": now,
            "gmt_modified": now,
            "transfer_id": "tf-001",
            "tenant": "t1",
            "session_id": "sess-001",
            "status": "CREATED",
            "staging_subdir": None,
            "filename": "test.txt",
            "fileservice_staging_path": "file-transfers/test/t1/sess-001/tf-001/test.txt",
            "error_message": None,
            "multipart_session_id": None,
            "env": "test",
            "operator": "test-user",
            **overrides,
        },
    )