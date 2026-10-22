"""Opt-in composition and lifecycle must not start DWS for unrelated engines."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

from engine.community.di import dws_deployment as deployment
from engine.community.di.dws_lifecycle import hosting_lifecycle


def values(tmp_path):
    return {
        "runtime": {
            "enabled": True,
            "storage_qualified": True,
            "state_dir": str(tmp_path / "state"),
            "bot_type": "personal",
            "engine": "openclaw",
        },
        "binding": {
            "binding_id": "binding",
            "bot_id": "bot",
            "owner_id": "owner",
            "organization_id": "org",
            "account_id": "account",
            "environment": "test",
            "agent_id": "main",
        },
        "identity": {
            "organization_id": "org",
            "user_id": "account",
            "self_open_id": "open-id",
        },
        "dws_config_dir": str(tmp_path / "dws"),
        "authorization_hosts": ["example.com"],
        "gateway_url": "ws://127.0.0.1:18789",
        "gateway_device_key": str(tmp_path / "device.json"),
        "gateway_token": "offline-placeholder",
        "mcporter_config": str(tmp_path / "mcp.json"),
        "management_backend_url": "https://backend.example.test",
    }


def config(tmp_path, **changes):
    return deployment.HostingDeployment.model_validate_json(
        json.dumps({**values(tmp_path), **changes})
    )


def test_private_config_loading_and_no_default_io(monkeypatch, tmp_path):
    monkeypatch.delenv("DWS_HOSTING_CONFIG", raising=False)
    assert deployment.load_deployment() is None
    monkeypatch.setenv("DWS_HOSTING_CONFIG", "relative.json")
    with pytest.raises(ValueError):
        deployment.load_deployment()
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values(tmp_path)))
    path.chmod(0o600)
    monkeypatch.setenv("DWS_HOSTING_CONFIG", str(path))
    assert deployment.load_deployment().binding.bot_id == "bot"
    path.chmod(0o644)
    with pytest.raises((ValueError, PermissionError)):
        deployment.load_deployment()
    path.chmod(0o600)
    path.write_bytes(b"x" * 65537)
    with pytest.raises(ValueError, match="too_large"):
        deployment.load_deployment()


@pytest.mark.parametrize(
    "changes",
    [
        {"engine": "hermes"},
        {"bot_type": "service"},
        {"storage_qualified": False},
        {"state_dir": "relative"},
    ],
)
def test_invalid_scope_cannot_construct_providers(monkeypatch, tmp_path, changes):
    value = values(tmp_path)
    value["runtime"].update(changes)
    provider = Mock(side_effect=AssertionError("provider touched"))
    monkeypatch.setattr(deployment, "DwsProcess", provider)
    with pytest.raises(ValueError):
        deployment.build_deployed_management(config(tmp_path, **value), provider)
    provider.assert_not_called()


def test_composition_is_explicit_and_no_network(monkeypatch, tmp_path):
    value = config(tmp_path)
    factory = Mock()
    disabled = value.model_copy(
        update={"runtime": value.runtime.model_copy(update={"enabled": False})}
    )
    assert deployment.build_deployed_management(disabled, factory) is None
    factory.assert_not_called()
    monkeypatch.delenv("DWS_MANAGEMENT_KEY", raising=False)
    for name in [
        "DwsProcess",
        "DwsCliConnection",
        "OpenClawHostingEngine",
        "DwsCliSender",
        "build_dws_runtime",
        "build_management_endpoint",
    ]:
        monkeypatch.setattr(deployment, name, Mock())
    result = deployment.build_deployed_management(value, factory)
    assert result is deployment.build_management_endpoint.return_value
    deployment.build_dws_runtime.assert_called_once()
    factory.assert_called_once()
    verifier = deployment.build_management_endpoint.call_args.args[-1]
    assert isinstance(verifier, deployment.BackendManagementVerifier)


def test_invalid_backend_fails_before_creating_providers(monkeypatch, tmp_path):
    provider = Mock(side_effect=AssertionError("provider touched"))
    monkeypatch.setattr(deployment, "DwsProcess", provider)
    with pytest.raises(ValueError, match="trusted_management_backend_url_required"):
        deployment.build_deployed_management(
            config(tmp_path, management_backend_url=""), provider
        )
    provider.assert_not_called()


def test_deployment_identity_and_absolute_credentials_required(tmp_path):
    value = values(tmp_path)
    value["identity"]["user_id"] = "someone-else"
    with pytest.raises(ValueError, match="identity_mismatch"):
        deployment.build_deployed_management(config(tmp_path, **value), Mock())
    with pytest.raises(ValueError, match="absolute_dws_config"):
        deployment.build_deployed_management(
            config(tmp_path, dws_config_dir="relative"), Mock()
        )


@pytest.mark.parametrize(
    "engine", ["hermes", "aicoding", "claude_code", "deepseek_harness", "teclaw"]
)
async def test_other_engine_lifecycle_does_not_even_load_config(engine):
    app = FastAPI()
    app.state.dws_management_factory = Mock(side_effect=AssertionError("called"))
    async with hosting_lifecycle(app, engine):
        assert app.state.dws_management is None
    app.state.dws_management_factory.assert_not_called()


async def test_lifecycle_unconfigured_failed_and_enabled():
    app = FastAPI()
    async with hosting_lifecycle(app, "openclaw"):
        assert app.state.dws_management is None
    app.state.dws_management_factory = Mock(side_effect=RuntimeError("private-detail"))
    async with hosting_lifecycle(app, "openclaw"):
        assert app.state.dws_management is None
    service = SimpleNamespace(restore=AsyncMock(), close=AsyncMock())
    endpoint = SimpleNamespace(service=service)
    app.state.dws_management_factory = Mock(return_value=endpoint)
    async with hosting_lifecycle(app, "openclaw"):
        assert app.state.dws_management is endpoint
    service.restore.assert_awaited_once()
    service.close.assert_awaited_once()
    assert app.state.dws_management is None
    service.close.side_effect = RuntimeError("private-detail")
    async with hosting_lifecycle(app, "openclaw"):
        assert app.state.dws_management is endpoint
    service.restore.side_effect = RuntimeError("private-detail")
    service.close.side_effect = RuntimeError("private-detail")
    async with hosting_lifecycle(app, "openclaw"):
        assert app.state.dws_management is None
