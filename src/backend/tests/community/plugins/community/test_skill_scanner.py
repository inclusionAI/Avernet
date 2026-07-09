"""Unit tests for the community ``NoopSkillScanner`` (B7).

Scanning is unavailable in the community build (no proprietary scan SDK).
"""
from __future__ import annotations

from agentclaw.community.plugins.community.skill_scanner import NoopSkillScanner


def test_is_not_available():
    assert NoopSkillScanner().is_available() is False


def test_create_sdk_returns_none():
    assert NoopSkillScanner().create_sdk() is None
