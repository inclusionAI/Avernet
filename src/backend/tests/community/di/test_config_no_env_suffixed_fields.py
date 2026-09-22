"""SOFAPy 1.3 overlays: no config block carries a prod/pre field pair.

The deployment overlay is selected and merged before any of this code reads
configuration, so each block exposes ONE canonical field per endpoint or
credential and nothing in the process re-picks between a ``*_prod`` and a
``*_pre`` sibling.

These are absence guards: they fail if a removed legacy field is reintroduced
on a dataclass, or if a shipped yaml config starts writing one again (a key a
provider no longer reads would be silently inert, which is worse than a
failure).
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from agentclaw.community.di import config as cfg

# dataclass -> (removed legacy fields, canonical fields that must remain)
_REMOVED_FIELDS: dict[type, tuple[tuple[str, ...], tuple[str, ...]]] = {
    cfg.BcsFuseConfig: (("base_url_pre",), ("base_url",)),
    cfg.EcbConfig: (("base_url_pre",), ("base_url",)),
    cfg.BaasConfig: (("api_base_url_pre",), ("api_base_url",)),
    cfg.MasaAgentEvalConfig: (("base_url_pre",), ("base_url",)),
    cfg.WorkspaceHostingConfig: (("aixcore_base_url_pre",), ("aixcore_base_url",)),
    cfg.GatewayConfig: (("base_url_pre",), ("base_url",)),
    cfg.BcnConfig: (
        (
            "base_url_pre",
            "provider_id_prod",
            "provider_id_pre",
            "provider_admin_token_prod",
            "provider_admin_token_pre",
        ),
        ("base_url", "provider_id", "provider_admin_token"),
    ),
    cfg.OpenApiBotConfig: (("base_url_pre",), ("base_url",)),
    cfg.BcsClientConfig: (
        ("base_url_pre", "task_callback_url_pre"),
        ("base_url", "task_callback_url"),
    ),
}


@pytest.mark.parametrize(
    ("dataclass_type", "removed", "canonical"),
    [(t, r, c) for t, (r, c) in _REMOVED_FIELDS.items()],
    ids=[t.__name__ for t in _REMOVED_FIELDS],
)
def test_dataclass_exposes_only_the_canonical_field(dataclass_type, removed, canonical):
    fields = {f.name for f in dataclasses.fields(dataclass_type)}
    assert fields.isdisjoint(removed), (
        f"{dataclass_type.__name__} reintroduced env-suffixed field(s): "
        f"{sorted(fields & set(removed))}"
    )
    assert set(canonical) <= fields


_CONFIG_DIR = Path(cfg.__file__).resolve().parents[1] / "configs"
_SHIPPED_CONFIGS = (
    "application.yaml",
    "application-community.yaml",
    "application-singlebox.yaml",
)
# ``daas_sdk_config_prod`` was the block-level shape of the same pattern.
_LEGACY_KEYS = frozenset(
    {
        "base_url_pre",
        "api_base_url_pre",
        "aixcore_base_url_pre",
        "task_callback_url_pre",
        "provider_id_prod",
        "provider_id_pre",
        "provider_admin_token_prod",
        "provider_admin_token_pre",
        "iframe_callback_url_pre",
        "daas_sdk_config_prod",
    }
)


def _walk_keys(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            yield str(key), here
            yield from _walk_keys(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_keys(value, f"{path}[{index}]")


@pytest.mark.parametrize("name", _SHIPPED_CONFIGS)
def test_shipped_yaml_writes_no_legacy_env_suffixed_key(name):
    """A ``*_pre`` key left in a shipped config would be inert, not overriding."""
    loaded = yaml.safe_load((_CONFIG_DIR / name).read_text(encoding="utf-8")) or {}
    offenders = [
        where for key, where in _walk_keys(loaded) if key in _LEGACY_KEYS
    ]
    assert not offenders, f"{name} still writes legacy env-suffixed key(s): {offenders}"
