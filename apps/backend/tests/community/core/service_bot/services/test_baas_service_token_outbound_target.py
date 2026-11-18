from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.caller_identity.credential import (
    CALLER_CREDENTIAL_REQUEST_INVALID,
    CALLER_OUTBOUND_INVALID,
    CALLER_OUTBOUND_UPDATE_FAILED,
    CALLER_TARGET_AMBIGUOUS,
    CALLER_TARGET_NOT_FOUND,
    CallerCredentialError,
    CallerToken,
)
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.service_bot.services import baas_service as module
from agentclaw.community.kernel.device_dto import (
    HeaderOperationRule,
    OutBoundOperationRule,
)


TARGET = "ARCA-SANDBOX-direct@001"


def binding(**kwargs):
    return DeviceBindingRecord(
        **{
            "entity_id": "ENTITY-1",
            "entity_type": "staff",
            "env": "dev",
            "apply_reason": None,
            "applied_by": "owner-1",
            "release_reason": None,
            "released_by": None,
            "released_at": None,
            "last_alive_at": None,
            "gmt_create": None,
            "gmt_modified": None,
            "id": 9,
            "status": "ACTIVE",
            "device_provider": "arca",
            "device_id": "logical-bot",
            "device_props": {"sandbox_id": TARGET, "token": "private-props-token"},
            **kwargs,
        }
    )


@pytest.fixture
def service():
    result = object.__new__(module.BaasService)
    result.list_devices_by_bot_uuid = MagicMock(
        return_value=[{"provider_device_id": "generic-device@template-2"}]
    )
    return result


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("arca", TARGET),
        ("baas", "generic-device@template-2"),
    ],
)
def test_resolves_provider_target_without_rewriting_template(
    service, provider, expected
):
    assert (
        service.resolve_token_outbound_device_id(binding(device_provider=provider))
        == expected
    )
    if provider == "arca":
        service.list_devices_by_bot_uuid.assert_not_called()
    else:
        service.list_devices_by_bot_uuid.assert_called_once_with(
            "logical-bot", timeout=3.0
        )


@pytest.mark.parametrize("unavailable", [None, binding(status="INACTIVE")])
def test_rejects_unavailable_binding_before_query(service, unavailable):
    with pytest.raises(ValueError) as error:
        service.resolve_token_outbound_device_id(unavailable)
    assert isinstance(error.value, module.BaasOutboundTargetError)
    assert error.value.reason == "binding_unavailable"
    service.list_devices_by_bot_uuid.assert_not_called()


@pytest.mark.parametrize("provider", [None, "", "unknown-private-provider\nsecret", 42])
def test_rejects_unknown_or_missing_provider_without_query(service, provider):
    target_binding = binding(device_provider=provider)
    if provider is None:
        del target_binding.device_provider
    with pytest.raises(ValueError) as error:
        service.resolve_token_outbound_device_id(target_binding)
    assert error.value.reason == "unsupported_provider"
    service.list_devices_by_bot_uuid.assert_not_called()


@pytest.mark.parametrize(
    "devices,reason",
    [
        ([], "target_not_found"),
        ([{"provider_device_id": TARGET}] * 2, "target_ambiguous"),
        ([{}], "invalid_target"),
    ],
)
def test_baas_requires_one_valid_device(service, devices, reason):
    service.list_devices_by_bot_uuid.return_value = devices
    with pytest.raises(ValueError) as error:
        service.resolve_token_outbound_device_id(binding(device_provider="baas"))
    assert error.value.reason == reason


@pytest.mark.parametrize("device_id", [None, ""])
def test_missing_baas_logical_id_does_not_query(service, device_id):
    with pytest.raises(ValueError) as error:
        service.resolve_token_outbound_device_id(
            binding(device_provider="baas", device_id=device_id)
        )
    assert error.value.reason == "target_not_found"
    service.list_devices_by_bot_uuid.assert_not_called()


@pytest.mark.parametrize(
    "props", [None, {}, "private-props-token", {"sandbox_id": None}]
)
def test_arca_missing_target_does_not_query(service, props):
    with pytest.raises(ValueError) as error:
        service.resolve_token_outbound_device_id(binding(device_props=props))
    assert error.value.reason == "target_not_found"
    service.list_devices_by_bot_uuid.assert_not_called()


@pytest.mark.parametrize("provider", ["arca", "baas"])
@pytest.mark.parametrize(
    "target",
    [
        "",
        42,
        "ARCA-SANDBOX-direct",
        "ARCA-SANDBOX-direct@",
        "@template",
        "ARCA-SANDBOX-direct@a@b",
        "ARCA-SANDBOX-direct/secret@template",
        "ARCA-SANDBOX-direct@../secret",
        "ARCA-SANDBOX-direct@a\nsecret",
        "ARCA-SANDBOX-direct@a\x00secret",
        "ARCA-SANDBOX-direct@a?secret",
        "ARCA-SANDBOX-direct@a%2fsecret",
        "ARCA-SANDBOX-" + "a" * 256 + "@t",
    ],
)
def test_rejects_invalid_targets(service, provider, target):
    service.list_devices_by_bot_uuid.return_value = [{"provider_device_id": target}]
    with pytest.raises(ValueError) as error:
        service.resolve_token_outbound_device_id(
            binding(device_provider=provider, device_props={"sandbox_id": target})
        )
    assert error.value.reason == (
        "target_not_found" if provider == "arca" and target == "" else "invalid_target"
    )
    if provider == "arca":
        service.list_devices_by_bot_uuid.assert_not_called()


def test_arca_rejects_generic_paas_id(service):
    with pytest.raises(ValueError) as error:
        service.resolve_token_outbound_device_id(
            binding(device_props={"sandbox_id": "generic@t"})
        )
    assert error.value.reason == "invalid_target"


def test_arca_does_not_require_logical_baas_id(service):
    assert service.resolve_token_outbound_device_id(binding(device_id=None)) == TARGET


@pytest.mark.parametrize(
    "mode,reason,error_type",
    [
        ("success", "resolved", "none"),
        ("provider", "unsupported_provider", "BaasOutboundTargetError"),
        ("target", "invalid_target", "BaasOutboundTargetError"),
        ("transport", "query_failed", "RuntimeError"),
    ],
)
def test_resolution_logs_provider_reason_and_transport_error_identity(
    service, monkeypatch, mode, reason, error_type
):
    log = MagicMock()
    monkeypatch.setattr(module, "logger", log)
    target_binding = binding()
    if mode == "provider":
        target_binding.device_provider = "private-provider\nsecret"
    elif mode == "target":
        target_binding.device_props["sandbox_id"] = "private-target/secret@t"
    elif mode == "transport":
        target_binding.device_provider = "baas"
        failure = RuntimeError("private-transport-token")
        service.list_devices_by_bot_uuid.side_effect = failure
    if mode == "success":
        assert service.resolve_token_outbound_device_id(target_binding) == TARGET
    else:
        with pytest.raises(Exception) as error:
            service.resolve_token_outbound_device_id(target_binding)
        if mode == "transport":
            assert error.value is failure
    rendered = "\n".join(call.args[0] % call.args[1:] for call in log.method_calls)
    assert "token_outbound_target_resolution_started" in rendered
    assert (
        "token_outbound_target_resolution_succeeded"
        if mode == "success"
        else "token_outbound_target_resolution_failed"
    ) in rendered
    assert "binding_id=9" in rendered
    assert "provider=" in rendered
    assert f"reason={reason}" in rendered
    assert f"error_type={error_type}" in rendered
    if mode == "provider":
        assert target_binding.device_provider in rendered
    for value in (TARGET, "logical-bot", "sandbox_id"):
        assert value not in rendered


@pytest.fixture
def caller(service):
    service._bot_repo = MagicMock()
    service._bot_repo.get_by_id_and_entity.return_value = {
        "bot_type": "service",
        "status": "ACTIVE",
        "owner_id": "owner-1",
    }
    service._device_binding_repo = MagicMock()
    service._device_binding_repo.get_by_id.return_value = binding()
    service._outbound_rule_provider = MagicMock()
    service._outbound_rule_provider.build_caller_rule.return_value = (
        OutBoundOperationRule(
            header_operation_rules=[
                HeaderOperationRule(
                    domains=["https://mcp.example"],
                    action="set",
                    header_name="x-caller-token",
                    value="caller-token",
                )
            ]
        )
    )
    service.append_caller_outbound_rule = MagicMock(return_value=True)
    kwargs = dict(
        bot_id="default",
        owner_user_id="owner-1",
        caller_user_id="caller-1",
        caller_token=CallerToken(
            access_token="caller-token",
            subject_user_id="caller-1",
            expires_at=datetime.now(),
            fingerprint="ignored",
        ),
        stage="draft",
        publish_id=None,
        entity_id="ENTITY-1",
        binding_id=9,
    )
    return service, kwargs


@pytest.mark.parametrize(
    "target_binding,devices,expected",
    [
        (None, [], CALLER_TARGET_NOT_FOUND),
        (binding(status="INACTIVE"), [], CALLER_TARGET_NOT_FOUND),
        (binding(device_provider=None), [], CALLER_TARGET_NOT_FOUND),
        (binding(device_provider="unknown"), [], CALLER_TARGET_NOT_FOUND),
        (binding(device_props={}), [], CALLER_TARGET_NOT_FOUND),
        (
            binding(device_props={"sandbox_id": "bad/target@t"}),
            [],
            CALLER_TARGET_NOT_FOUND,
        ),
        (binding(device_provider="baas"), [], CALLER_TARGET_NOT_FOUND),
        (binding(device_provider="baas"), [{}], CALLER_TARGET_NOT_FOUND),
        (binding(device_provider="baas"), [{}, {}], CALLER_TARGET_AMBIGUOUS),
    ],
)
def test_caller_preserves_target_error_contract(
    caller, target_binding, devices, expected
):
    service, kwargs = caller
    service._device_binding_repo.get_by_id.return_value = target_binding
    service.list_devices_by_bot_uuid.return_value = devices
    with pytest.raises(CallerCredentialError) as error:
        service.update_caller_identity(**kwargs)
    assert error.value.code == expected
    service.append_caller_outbound_rule.assert_not_called()


@pytest.mark.parametrize("gate", ["auth", "type", "status", "owner", "missing"])
def test_caller_keeps_gates_before_target_resolution(caller, gate):
    service, kwargs = caller
    bot = service._bot_repo.get_by_id_and_entity.return_value
    if gate == "auth":
        kwargs["caller_user_id"] = "other-caller"
    elif gate == "missing":
        service._bot_repo.get_by_id_and_entity.return_value = None
    else:
        bot[{"type": "bot_type", "status": "status", "owner": "owner_id"}[gate]] = (
            "other"
        )
    with pytest.raises(CallerCredentialError) as error:
        service.update_caller_identity(**kwargs)
    assert error.value.code == (
        CALLER_CREDENTIAL_REQUEST_INVALID if gate == "auth" else CALLER_TARGET_NOT_FOUND
    )
    service._device_binding_repo.get_by_id.assert_not_called()
    service.list_devices_by_bot_uuid.assert_not_called()
    service.append_caller_outbound_rule.assert_not_called()
    if gate != "auth":
        service._bot_repo.get_by_id_and_entity.assert_called_once_with(
            "default", "ENTITY-1"
        )


@pytest.mark.parametrize(
    "failure", ["rule", "append_false", "append_exception", "transport"]
)
def test_caller_retains_rule_append_and_transport_failures(caller, failure):
    service, kwargs = caller
    if failure == "rule":
        service._outbound_rule_provider.build_caller_rule.return_value = None
    elif failure == "append_false":
        service.append_caller_outbound_rule.return_value = False
    elif failure == "append_exception":
        service.append_caller_outbound_rule.side_effect = RuntimeError("private-append")
    else:
        service._device_binding_repo.get_by_id.return_value.device_provider = "baas"
        original = RuntimeError("private-transport")
        service.list_devices_by_bot_uuid.side_effect = original
    with pytest.raises(Exception) as error:
        service.update_caller_identity(**kwargs)
    if failure == "transport":
        assert error.value is original
    else:
        assert isinstance(error.value, CallerCredentialError)
        assert error.value.code == (
            CALLER_OUTBOUND_INVALID
            if failure == "rule"
            else CALLER_OUTBOUND_UPDATE_FAILED
        )


@pytest.mark.parametrize(
    "target",
    [
        "ARCA-SANDBOX-direct@template",
        "ARCA-SANDBOX-direct@１２",
        "ARCA-SANDBOX-direct@١٢",
        "ARCA-SANDBOX-direct@1:8080",
        "ARCA-SANDBOX-direct:8080@1",
        "ARCA-SANDBOX-direct@-1",
        "ARCA-SANDBOX-direct@1.2",
        "ARCA-SANDBOX-direct@+1",
    ],
)
def test_arca_requires_ascii_numeric_template(service, target):
    with pytest.raises(ValueError) as error:
        service.resolve_token_outbound_device_id(
            binding(device_props={"sandbox_id": target})
        )
    assert error.value.reason == "invalid_target"
    service.list_devices_by_bot_uuid.assert_not_called()


@pytest.mark.parametrize(
    "logical_id",
    [
        "bot/path",
        "../bot",
        "bot\nsecret",
        "bot\x00secret",
        "bot#fragment",
        "bot?query",
        "bot%2fpath",
        "bot@template",
        "bot:8080",
        "bot with space",
        42,
    ],
)
def test_baas_rejects_unsafe_logical_id_before_query(service, logical_id):
    with pytest.raises(ValueError) as error:
        service.resolve_token_outbound_device_id(
            binding(device_provider="baas", device_id=logical_id)
        )
    assert error.value.reason == "invalid_target"
    service.list_devices_by_bot_uuid.assert_not_called()


@pytest.mark.parametrize("selected", ["DEVICE-2", " 副本/A:2 "])
def test_select_current_baas_replica_by_uuid_within_binding(service, selected):
    devices = [
        {"device_uuid": "DEVICE-1", "status": "ACTIVE", "provider_device_id": "ARCA-SANDBOX-1@2"},
        {"device_uuid": selected, "status": "ACTIVE", "provider_device_id": "ARCA-SANDBOX-2@2"},
    ]
    service._active_token_replica_devices = MagicMock(return_value=devices)
    assert service.resolve_token_outbound_device_id(
        binding(device_provider="baas"), device_uuid=selected,
    ) == "ARCA-SANDBOX-2@2"
    service._active_token_replica_devices.assert_called_once_with("logical-bot")


@pytest.mark.parametrize("devices,reason", [
    ([{"device_uuid": "other", "status": "ACTIVE"}], "target_not_found"),
    ([{"device_uuid": "selected", "status": "RELEASED"}], "target_not_found"),
    ([{"device_uuid": "selected"}], "target_not_found"),
    ([{"device_uuid": "selected", "status": "ACTIVE"}] * 2, "target_ambiguous"),
])
def test_reject_unavailable_or_ambiguous_replica(service, devices, reason):
    service._active_token_replica_devices = MagicMock(return_value=devices)
    with pytest.raises(module.BaasOutboundTargetError) as exc:
        service.resolve_token_outbound_device_id(binding(device_provider="baas"), device_uuid="selected")
    assert exc.value.reason == reason


def test_direct_arca_uuid_requires_matching_active_binding_metadata(service):
    record = binding(device_props={"sandbox_id": TARGET, "device_uuid": "selected"})
    assert service.resolve_token_outbound_device_id(record, device_uuid="selected") == TARGET
    for rejected in (record, binding()):
        with pytest.raises(module.BaasOutboundTargetError) as exc:
            service.resolve_token_outbound_device_id(rejected, device_uuid="foreign")
        assert exc.value.reason == "target_not_found"
    service.list_devices_by_bot_uuid.assert_not_called()


@pytest.mark.parametrize("outcome", ["current", "foreign", "inactive", "swapped", "http_error"])
def test_select_replica_only_from_current_bot_record(outcome, caplog):
    import httpx

    caplog.set_level("INFO")
    service = object.__new__(module.BaasService)
    service._tenant = "example"
    requested_paths = []

    def inventory(request):
        requested_paths.append(request.url.path)
        assert request.url.params["tenant"] == "example"
        if request.url.path == "/api/v1/bots/logical-bot/device-status":
            data = {"bot_uuid": "logical-bot", "bot_id": 31,
                    "bot_status": "STOPPED" if outcome == "inactive" else "ACTIVE"}
        else:
            assert request.url.path == "/api/v1/bots/31/detail-by-id"
            if outcome == "http_error":
                return httpx.Response(500, text="test-private-response")
            data = {"id": 31, "status": "ACTIVE", "bot_uuid": "foreign" if outcome == "swapped" else "logical-bot",
                    "config": {"token": "test-private-response"}, "devices": [
                        {"device_uuid": "DEVICE-1", "status": "ACTIVE", "provider_device_id": "ARCA-SANDBOX-first@1"},
                        {"device_uuid": "DEVICE-2", "status": "ACTIVE", "provider_device_id": TARGET},
                    ]}
        return httpx.Response(200, json={"code": 0, "data": data})

    with httpx.Client(transport=httpx.MockTransport(inventory), base_url="https://example.test") as client:
        service._http = client
        if outcome == "current":
            assert service.resolve_token_outbound_device_id(binding(device_provider="baas"), device_uuid="DEVICE-2") == TARGET
        elif outcome == "http_error":
            with pytest.raises(httpx.HTTPStatusError):
                service.resolve_token_outbound_device_id(binding(device_provider="baas"), device_uuid="DEVICE-2")
            assert "token_replica_inventory_failed" in caplog.text
        else:
            selection = "historical-device" if outcome == "foreign" else "DEVICE-2"
            with pytest.raises(module.BaasOutboundTargetError, match="target_not_found"):
                service.resolve_token_outbound_device_id(binding(device_provider="baas"), device_uuid=selection)
    assert len(requested_paths) == (1 if outcome == "inactive" else 2)
    assert "token_replica_inventory_started" in caplog.text
    assert "token_replica_inventory_succeeded" in caplog.text
    assert "test-private-response" not in caplog.text


def test_no_uuid_keeps_original_inventory_exception(service):
    error = module.BaasServiceError("legacy inventory failure")
    service.list_devices_by_bot_uuid.side_effect = error
    with pytest.raises(module.BaasServiceError) as exc:
        service.resolve_token_outbound_device_id(binding(device_provider="baas"))
    assert exc.value is error
    service.list_devices_by_bot_uuid.assert_called_once_with("logical-bot", timeout=3.0)


@pytest.mark.parametrize("provider", ["baas", "arca"])
def test_explicit_empty_device_never_falls_back_or_matches_empty_metadata(service, provider):
    record = binding(device_provider=provider, device_props={"device_uuid": "", "sandbox_id": TARGET})
    with pytest.raises(module.BaasOutboundTargetError, match="target_not_found"):
        service.resolve_token_outbound_device_id(record, device_uuid="")
    service.list_devices_by_bot_uuid.assert_not_called()
