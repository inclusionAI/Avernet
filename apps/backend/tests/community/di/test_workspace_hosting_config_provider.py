"""Unit test for the neutral ConfigModule.workspace_hosting provider (B8 review).

WorkspaceHostingConfig (formerly the corp DimaConfig) is neutral now — a generic
coding-workspace hosting backend config (corp values via the ``dima`` yaml block,
like BaasConfig). The provider is non-strict: it defaults when the block is
absent, so a community boot (no ``dima`` block, client unbound) constructs cleanly.
"""
from __future__ import annotations

from agentclaw.community.di.modules import config_module
from agentclaw.community.di.modules.config_module import ConfigModule


def _wh(monkeypatch, user_config: dict):
    monkeypatch.setattr(config_module, "_user_config", lambda: dict(user_config))
    return ConfigModule().workspace_hosting()


def test_reads_dima_block(monkeypatch):
    out = _wh(
        monkeypatch,
        {
            "dima": {
                "base_url": "https://devapi",
                "access_key": "ak",
                "access_secret": "sk",
                "tenant": "t",
                "timeout": 5,
            }
        },
    )
    assert out.base_url == "https://devapi"
    assert out.access_key == "ak"
    assert out.access_secret == "sk"
    assert out.tenant == "t"
    assert out.timeout == 5


def test_absent_block_uses_neutral_defaults(monkeypatch):
    out = _wh(monkeypatch, {})
    assert out.base_url == ""
    assert out.access_key == ""
    assert out.access_secret == ""
    # Neutral default after OSS-0 #3 (was the corp "alipay" tenant).
    assert out.tenant == "default"


# ── ConfigModule.skill_scan (neutral; corp values via the ``skill_scan`` block) ──


def _skill_scan(monkeypatch, user_config: dict):
    monkeypatch.setattr(config_module, "_user_config", lambda: dict(user_config))
    return ConfigModule().skill_scan()


def test_skill_scan_reads_block(monkeypatch):
    out = _skill_scan(
        monkeypatch,
        {"skill_scan": {"enabled": False, "env": "pre", "max_concurrent_scans": 7}},
    )
    assert out.enabled is False
    assert out.env == "pre"
    assert out.max_concurrent_scans == 7


def test_skill_scan_absent_block_uses_defaults(monkeypatch):
    out = _skill_scan(monkeypatch, {})
    assert out.enabled is True
    assert out.storage_dir == "./data/skills_scan"
