"""build_injector must validate http_client config on EVERY profile."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from agentclaw.community.di import DeployProfile, build_injector


def _with_block(block):
    return patch(
        "agentclaw.community.di.modules.config_module._user_config",
        return_value={"http_client": block},
    )


def test_valid_http_client_block_boots():
    with _with_block({"max_connections": 42, "overrides": {"baas": {"http2": True}}}):
        assert build_injector(profile=DeployProfile.TEST) is not None


@pytest.mark.parametrize(
    "block",
    [
        {"max_conections": 250},                      # unknown policy key
        {"overrides": {"bass": {"http2": True}}},      # unknown qualifier
        {"overrides": {"baas": {"htttp2": True}}},     # unknown key in an override
    ],
)
def test_invalid_http_client_block_fails_the_build(block):
    """The eager check only runs on pre/prod, and a dev boot would otherwise
    swallow the raise inside discover_lifecycle_participants and start with no
    real HttpClient bindings — deferring failure to the first outbound request.
    build_injector resolving the config is what closes that on every column."""
    with _with_block(block), pytest.raises(ValueError):
        build_injector(profile=DeployProfile.TEST)
