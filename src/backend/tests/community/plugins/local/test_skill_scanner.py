"""Unit tests for the local ``LocalSkillScanner`` (B7).

Offline/test double for the skill-scan capability: scanning is unavailable
(no scanner SDK), so ``is_available`` is False and ``create_sdk`` yields None.
"""
from __future__ import annotations

from agentclaw.community.plugins.local.skill_scanner import LocalSkillScanner


def test_is_unavailable_offline():
    assert LocalSkillScanner().is_available() is False


def test_create_sdk_is_none():
    assert LocalSkillScanner().create_sdk() is None
