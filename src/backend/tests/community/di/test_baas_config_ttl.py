"""ConfigModule.baas default_ttl_minutes decouple test (B8 T5).

BaasService no longer injects ArcaSandboxConfig — its TTL now comes from
``BaasConfig.default_ttl_minutes``. The provider must:
  1. prefer the ``baas`` block's ``default_ttl_minutes``,
  2. else fall back to the legacy corp ``arca_sandbox.default_ttl_minutes``
     (so corp keeps its exact per-env value with no YAML change),
  3. else the dataclass default (community / no arca block).
"""
from __future__ import annotations

from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule


def _baas(monkeypatch, user_config: dict):
    monkeypatch.setattr(
        config_module, "_user_config", lambda: dict(user_config)
    )
    return ConfigModule().baas()


def test_ttl_from_baas_block_wins(monkeypatch):
    out = _baas(
        monkeypatch,
        {
            "baas": {"default_ttl_minutes": 60},
            "arca_sandbox": {"default_ttl_minutes": 1440},
        },
    )
    assert out.default_ttl_minutes == 60


def test_ttl_falls_back_to_arca_sandbox_block(monkeypatch):
    # corp parity: no baas.default_ttl_minutes, value comes from arca_sandbox.
    out = _baas(monkeypatch, {"arca_sandbox": {"default_ttl_minutes": 1440}})
    assert out.default_ttl_minutes == 1440


def test_ttl_default_when_neither_block(monkeypatch):
    out = _baas(monkeypatch, {"baas": {"tenant": "community"}})
    assert out.default_ttl_minutes == 10080  # neutral dataclass default
