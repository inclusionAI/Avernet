"""Service transition matrices and post-CAS diagnostics, independent of ORM."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from secbaas.community.core.service.file_transfer import _ticket_status as bot
from secbaas.community.core.service.session_file_sharing import (
    _ticket_status as session,
)

# Expected source sets are a specification, not computed from production tables.
BOT_SOURCES = {
    "CREATED": {"CREATED"},
    "UPLOADING": {"CREATED", "UPLOADING"},
    "UPLOAD_COMPLETED": {"CREATED", "UPLOADING", "UPLOAD_COMPLETED"},
    "PULLING": {"UPLOAD_COMPLETED", "PULLING"},
    "PUSHING": {"CREATED", "PUSHING"},
    "DONE": {"UPLOAD_COMPLETED", "PULLING", "PUSHING", "DONE"},
    "FAILED": {
        "CREATED",
        "UPLOADING",
        "UPLOAD_COMPLETED",
        "PULLING",
        "PUSHING",
        "FAILED",
    },
    "CANCELLED": {"CREATED", "UPLOADING", "PULLING", "CANCELLED"},
    "DELETED": {"DONE", "FAILED", "CANCELLED", "DELETED"},
}
SESSION_SOURCES = {
    "CREATED": {"CREATED"},
    "UPLOADING": {"CREATED", "UPLOADING"},
    "DONE": {"CREATED", "UPLOADING", "DONE"},
    "FAILED": {"UPLOADING", "FAILED"},
    "CANCELLED": {"CREATED", "UPLOADING", "CANCELLED"},
    "DELETED": {"DONE", "FAILED", "CANCELLED", "DELETED"},
}


@pytest.mark.parametrize(
    "policy,source,target,expected",
    [
        (policy, source, target, frozenset(allowed))
        for policy, matrix in [(bot, BOT_SOURCES), (session, SESSION_SOURCES)]
        for source in matrix
        for target, allowed in matrix.items()
    ],
    ids=lambda value: (
        value.__name__.split(".")[-2] if hasattr(value, "__name__") else str(value)
    ),
)
def test_all_transition_pairs(policy, source, target, expected):
    repository = MagicMock()
    repository.compare_and_set_status.return_value = int(source in expected)
    repository.get_by_transfer_id.return_value = SimpleNamespace(
        status=source, error_message=None
    )
    if source in expected:
        policy.update_ticket_status(repository, "f1", target)
        repository.get_by_transfer_id.assert_not_called()
    else:
        with pytest.raises(policy.TransferStateConflictError):
            policy.update_ticket_status(repository, "f1", target)
        repository.get_by_transfer_id.assert_called_once_with("f1")
    repository.compare_and_set_status.assert_called_once_with(
        "f1", target, None, expected_statuses=expected
    )


@pytest.fixture(params=[bot, session], ids=["bot", "session"])
def policy(request):
    return request.param


def test_missing_ticket_is_service_error(policy):
    repository = MagicMock()
    repository.compare_and_set_status.return_value = 0
    repository.get_by_transfer_id.return_value = None
    with pytest.raises(policy.TransferNotFoundError):
        policy.update_ticket_status(repository, "missing", "DONE")
    repository.get_by_transfer_id.assert_called_once_with("missing")


@pytest.mark.parametrize(
    "requested,stored,succeeds",
    [
        (None, None, True),
        (None, "old", True),
        ("", "", True),
        ("", None, False),
        ("new", "new", True),
        ("new", "old", False),
    ],
)
def test_zero_changed_rows_requires_persisted_message(
    policy, requested, stored, succeeds
):
    repository = MagicMock()
    repository.compare_and_set_status.return_value = 0
    repository.get_by_transfer_id.return_value = SimpleNamespace(
        status="FAILED", error_message=stored
    )
    if succeeds:
        policy.update_ticket_status(repository, "f1", "FAILED", requested)
    else:
        with pytest.raises(policy.TransferStateConflictError):
            policy.update_ticket_status(repository, "f1", "FAILED", requested)
    assert repository.compare_and_set_status.call_count == 1
    repository.get_by_transfer_id.assert_called_once_with("f1")


@pytest.mark.parametrize("stage", ["write", "commit"])
def test_database_failure_is_not_misreported_as_state_conflict(policy, stage):
    repository = MagicMock()
    failure = RuntimeError(f"{stage} failed")
    # A decorated repository returns only after its plugin context has committed;
    # a commit error reaches the Service exactly like a write error.
    repository.compare_and_set_status.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        policy.update_ticket_status(repository, "f1", "DONE")
    assert caught.value is failure
    repository.get_by_transfer_id.assert_not_called()


def test_diagnostic_can_observe_later_concurrent_state_without_retry(policy):
    repository = MagicMock()
    repository.compare_and_set_status.return_value = 0
    repository.get_by_transfer_id.return_value = SimpleNamespace(
        status="DELETED", error_message=None
    )
    with pytest.raises(policy.TransferStateConflictError, match="DELETED to DONE"):
        policy.update_ticket_status(repository, "f1", "DONE")
    assert repository.compare_and_set_status.call_count == 1
