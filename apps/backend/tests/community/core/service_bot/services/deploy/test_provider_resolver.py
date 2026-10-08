"""Unit tests for ``resolve_device_provider`` — the pure container-type mapper.

The container's source of truth is baas; this module only maps a baas-reported
device ``provider_type`` to a ``device_provider`` token. The I/O (querying baas)
lives on ``BaasService.resolve_container_provider`` and is tested separately.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    DEFAULT_DEVICE_PROVIDER,
    TECLAW_DEVICE_PROVIDER,
    TECLAW_PROVIDER_TYPE,
    resolve_device_provider,
)


@pytest.mark.unit
def test_teclaw_provider_type_maps_to_teclaw() -> None:
    assert resolve_device_provider(TECLAW_PROVIDER_TYPE) == TECLAW_DEVICE_PROVIDER


@pytest.mark.unit
@pytest.mark.parametrize("value", ["TECLAW", "teclaw", "TeClaw"])
def test_teclaw_match_is_case_insensitive(value: str) -> None:
    assert resolve_device_provider(value) == TECLAW_DEVICE_PROVIDER


@pytest.mark.unit
@pytest.mark.parametrize("provider_type", ["ARCA", "LOCAL", "SIGMA", "POOLAB"])
def test_non_teclaw_platforms_fall_back_to_baas(provider_type: str) -> None:
    assert resolve_device_provider(provider_type) == DEFAULT_DEVICE_PROVIDER


@pytest.mark.unit
def test_none_falls_back_to_default() -> None:
    # baas does not know the bot yet (e.g. an ARCA draft not provisioned via baas).
    assert resolve_device_provider(None) == DEFAULT_DEVICE_PROVIDER


@pytest.mark.unit
def test_empty_string_falls_back_to_default() -> None:
    assert resolve_device_provider("") == DEFAULT_DEVICE_PROVIDER
    assert resolve_device_provider("   ") == DEFAULT_DEVICE_PROVIDER


@pytest.mark.unit
def test_teclaw_provider_type_is_injectable() -> None:
    # The teclaw provider_type string can be overridden without editing the module.
    assert (
        resolve_device_provider("FOREIGN", teclaw_provider_type="FOREIGN")
        == TECLAW_DEVICE_PROVIDER
    )
    # ...and the real teclaw value no longer matches once overridden.
    assert (
        resolve_device_provider("TECLAW", teclaw_provider_type="FOREIGN")
        == DEFAULT_DEVICE_PROVIDER
    )


@pytest.mark.unit
def test_default_provider_is_overridable() -> None:
    assert resolve_device_provider("ARCA", default_provider="arca") == "arca"
    assert resolve_device_provider(None, default_provider="arca") == "arca"
