"""Unit tests for the community ``CommunitySkillCenterClient`` (B7).

Skill Center marketplace is unsupported in the community build: every method
raises ``SkillCenterUnsupportedError`` so a caller fails loudly rather than
silently receiving a fake result.
"""
from __future__ import annotations

import pytest

from agentclaw.community.plugins.community.skill_center_client import (
    CommunitySkillCenterClient,
    SkillCenterUnsupportedError,
)


def _client() -> CommunitySkillCenterClient:
    return CommunitySkillCenterClient()


def test_upload_and_publish_raises():
    with pytest.raises(SkillCenterUnsupportedError):
        _client().upload_and_publish({"skillCode": "s1"})


def test_query_publish_status_raises():
    with pytest.raises(SkillCenterUnsupportedError):
        _client().query_publish_status("s1")


def test_list_versions_raises():
    with pytest.raises(SkillCenterUnsupportedError):
        _client().list_versions("s1")


def test_search_market_skills_raises():
    with pytest.raises(SkillCenterUnsupportedError):
        _client().search_market_skills(keyword="x")


def test_get_market_tags_raises():
    with pytest.raises(SkillCenterUnsupportedError):
        _client().get_market_tags()


def test_get_download_url_raises():
    with pytest.raises(SkillCenterUnsupportedError):
        _client().get_download_url("s1", "1")


def test_get_file_structure_raises():
    with pytest.raises(SkillCenterUnsupportedError):
        _client().get_file_structure("s1")


def test_get_file_content_raises():
    with pytest.raises(SkillCenterUnsupportedError):
        _client().get_file_content("s1", "SKILL.md")


def test_delete_skill_raises():
    with pytest.raises(SkillCenterUnsupportedError):
        _client().delete_skill("s1")
