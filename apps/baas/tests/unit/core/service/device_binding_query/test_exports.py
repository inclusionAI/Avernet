"""Service-owned export shape/selection, after bounded repository reads."""

from unittest.mock import MagicMock

import pytest

from secbaas.community.core.repository.device_binding import DeviceBindingRepository
from secbaas.community.core.service.device_binding_query import (
    DeviceBindingQueryService,
)


@pytest.fixture
def service():
    repo = MagicMock(spec=DeviceBindingRepository)
    return DeviceBindingQueryService(
        MagicMock(), MagicMock(), repo, MagicMock(), MagicMock()
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('{"bolt_id":"b","sandbox_id":"s"}', ("b", "s")),
        ({"bolt_id": "b", "sandbox_id": "s"}, ("b", "s")),
        ('{"sandbox_id":"s"}', ("", "s")),
        (None, ("", "")),
        ("", ("", "")),
        ("invalid-json", ("", "")),
        ("null", ("", "")),
        ("[]", ("", "")),
        ('"scalar"', ("", "")),
        ([], ("", "")),
    ],
)
def test_export_decodes_legacy_shapes(service, raw, expected):
    service._binding_repo.list_export_rows.return_value = [("u", raw)]
    assert service.export_device_list(env="pre") == [("u", *expected)]
    service._binding_repo.list_export_rows.assert_called_once_with(
        env="pre",
        status="ACTIVE",
        device_provider="arca",
        offset=0,
        limit=200,
    )


@pytest.mark.parametrize("all_envs", [False, True])
def test_empty_export_uses_explicit_admin_scope(service, all_envs):
    service._binding_repo.list_export_rows.return_value = []
    result = service.export_device_all() if all_envs else service.export_device_list()
    assert result == []
    assert service._binding_repo.list_export_rows.call_args.kwargs["env"] == (
        None if all_envs else "pre"
    )


def test_export_read_failure_propagates(service):
    service._binding_repo.list_export_rows.side_effect = RuntimeError("db failed")
    with pytest.raises(RuntimeError, match="db failed"):
        service.export_device_list()
