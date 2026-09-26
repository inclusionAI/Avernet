"""Typed config parsing for platform-managed MCP Header secret references."""

import pytest

from agentclaw.community.di import DeployProfile, build_injector
from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.mcp_runtime_credentials_config_module import (
    McpRuntimeCredentialsConfigModule,
)


def _read(monkeypatch, value):
    monkeypatch.setattr(
        config_module,
        "read_user_config",
        lambda: value,
    )
    return McpRuntimeCredentialsConfigModule().mcp_runtime_credentials()


def test_missing_block_is_an_empty_optional_mapping(monkeypatch):
    assert _read(monkeypatch, {}).header_secrets == {}


def test_header_secret_references_are_parsed_without_resolving_values(monkeypatch):
    config = _read(
        monkeypatch,
        {
            "mcp_runtime_credentials": {
                "header_secrets": {
                    "mcp.example": {
                        "authorization": "example-secret",
                        "x-api-key": "another-secret",
                    }
                }
            }
        },
    )

    assert config.header_secrets == {
        "mcp.example": {
            "authorization": "example-secret",
            "x-api-key": "another-secret",
        }
    }


@pytest.mark.parametrize(
    "block, message",
    [
        ("invalid", "must be a mapping"),
        ({"unknown": {}}, "contains unknown keys"),
        ({"header_secrets": []}, "header_secrets must be a mapping"),
        ({"header_secrets": {"": {"h": "s"}}}, "empty server_code"),
        (
            {"header_secrets": {" mcp.example": {"h": "s"}}},
            "server_code must not contain surrounding whitespace",
        ),
        ({"header_secrets": {"mcp.example": {}}}, "must be a non-empty mapping"),
        ({"header_secrets": {"mcp.example": {"": "s"}}}, "empty header name"),
        (
            {"header_secrets": {"mcp.example": {" h": "s"}}},
            "Header names must not contain surrounding whitespace",
        ),
        (
            {
                "header_secrets": {
                    "mcp.example": {
                        "Authorization": "first-secret",
                        "authorization": "second-secret",
                    }
                }
            },
            "duplicate case-insensitive Header name",
        ),
        ({"header_secrets": {"mcp.example": {"h": ""}}}, "must name a secret"),
        (
            {"header_secrets": {"mcp.example": {"h": " secret"}}},
            "secret name must not contain surrounding whitespace",
        ),
    ],
)
def test_invalid_reference_shapes_fail_during_config_construction(
    monkeypatch, block, message
):
    with pytest.raises(ValueError, match=message):
        _read(monkeypatch, {"mcp_runtime_credentials": block})


def test_invalid_reference_shape_fails_application_boot(monkeypatch):
    monkeypatch.setattr(
        config_module,
        "read_user_config",
        lambda: {"mcp_runtime_credentials": {"header_secrets": []}},
    )

    with pytest.raises(
        ValueError,
        match="mcp_runtime_credentials.header_secrets must be a mapping",
    ):
        build_injector(profile=DeployProfile.TEST)
