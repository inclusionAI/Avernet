"""Strict parse of ``user_config.community_identity``.

Parity pins for the mode knob, following the teclaw delivery-mode parser
tests: the two valid modes, the fail-the-boot spellings, and the URL rules.
"""
from __future__ import annotations

import pytest

from agentclaw.community.di.community_identity_config import (
    CommunityIdentityConfig,
    CommunityPassportMode,
    community_identity_from_block,
)


def test_missing_block_is_the_self_issued_default():
    assert community_identity_from_block(None) == CommunityIdentityConfig()
    assert community_identity_from_block({}) == CommunityIdentityConfig()


def test_explicit_self_issued_mode_parses():
    config = community_identity_from_block({"passport_mode": "self_issued"})
    assert config.passport_mode is CommunityPassportMode.SELF_ISSUED
    assert config.consent_base_url == ""


def test_null_mode_key_reads_as_unset():
    config = community_identity_from_block({"passport_mode": None})
    assert config.passport_mode is CommunityPassportMode.SELF_ISSUED


def test_manual_consent_requires_a_base_url():
    with pytest.raises(ValueError, match="consent_base_url"):
        community_identity_from_block({"passport_mode": "manual_consent"})


def test_invalid_mode_fails_the_boot():
    with pytest.raises(ValueError, match="passport_mode"):
        community_identity_from_block({"passport_mode": "sometimes"})


def test_non_string_mode_fails_the_boot():
    with pytest.raises(ValueError, match="passport_mode"):
        community_identity_from_block({"passport_mode": 42})


def test_non_mapping_block_fails_the_boot():
    with pytest.raises(ValueError, match="community_identity"):
        community_identity_from_block("manual_consent")


@pytest.mark.parametrize("url", ["http://backend-simulator:31231", "https://sso.example.test"])
def test_manual_consent_with_valid_base_url_accepts_both_schemes(url):
    config = community_identity_from_block(
        {"passport_mode": "manual_consent", "consent_base_url": url}
    )
    assert config.passport_mode is CommunityPassportMode.MANUAL_CONSENT
    assert config.consent_base_url == url


def test_base_url_is_trimmed():
    config = community_identity_from_block(
        {"passport_mode": "manual_consent", "consent_base_url": "  http://x:1\t"}
    )
    assert config.consent_base_url == "http://x:1"


def test_base_url_with_wrong_scheme_fails_the_boot():
    with pytest.raises(ValueError, match="http:// or https://"):
        community_identity_from_block(
            {"passport_mode": "manual_consent", "consent_base_url": "ftp://x"}
        )


def test_blank_base_url_in_manual_consent_fails_the_boot():
    with pytest.raises(ValueError, match="consent_base_url"):
        community_identity_from_block(
            {"passport_mode": "manual_consent", "consent_base_url": "   "}
        )


def test_bad_base_url_is_refused_even_in_self_issued_mode():
    with pytest.raises(ValueError, match="http:// or https://"):
        community_identity_from_block(
            {"passport_mode": "self_issued", "consent_base_url": "not-a-url"}
        )


def test_mode_spelling_is_case_and_whitespace_tolerant():
    config = community_identity_from_block(
        {"passport_mode": " Manual_Consent ", "consent_base_url": "http://x:1"}
    )
    assert config.passport_mode is CommunityPassportMode.MANUAL_CONSENT


def test_non_string_base_url_fails_the_boot():
    with pytest.raises(ValueError, match="consent_base_url"):
        community_identity_from_block(
            {"passport_mode": "manual_consent", "consent_base_url": 8080}
        )


def test_shipped_default_matches_the_self_issued_default():
    """The neutral base yaml's block must parse to the default config."""
    import pathlib

    import yaml

    base = (
        pathlib.Path(__file__).resolve().parents[3]
        / "src" / "agentclaw" / "community" / "configs" / "application.yaml"
    )
    block = yaml.safe_load(base.read_text(encoding="utf-8"))["user_config"][
        "community_identity"
    ]
    assert community_identity_from_block(block) == CommunityIdentityConfig()