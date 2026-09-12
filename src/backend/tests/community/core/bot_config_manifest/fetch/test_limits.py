"""The fetch road's deployment allowlist and its cap vocabulary.

The allowlist is *config-borne, not environment-borne*: it is parsed out of
``application.yaml``'s ``user_config.bot_config_manifest`` block and handed
to ``SourceCredentialService`` by the composition root, so a deployment's
exemption appears in its own overlay diff and nowhere else. A typo in that
block must fail its reader loudly rather than silently widening or silently
refusing — which is what most of this file asserts.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from agentclaw.community.core.bot_config_manifest.fetch.limits import (
    FETCH_ENTRY_LIMITS,
    FetchCategory,
    transport_allowlist_from_config,
)


def test_the_allowlist_parses_from_the_user_config_block():
    # The yaml shape shipped in configs/application.yaml:
    # user_config.bot_config_manifest.fetch_transport_allowlist.
    allow = transport_allowlist_from_config({
        "bot_config_manifest": {"fetch_transport_allowlist": [
            "mirror.example", " mirror.internal ", "", "mirror.example",
        ]},
    })
    assert allow == frozenset({"mirror.example", "mirror.internal"})


def test_an_absent_block_or_key_means_no_exception():
    assert transport_allowlist_from_config({}) == frozenset()
    assert transport_allowlist_from_config(
        {"bot_config_manifest": None}
    ) == frozenset()
    assert transport_allowlist_from_config(
        {"bot_config_manifest": {"fetch_transport_allowlist": None}}
    ) == frozenset()


@pytest.mark.parametrize("settings", [
    {"bot_config_manifest": "mirror.example"},
    {"bot_config_manifest": {"fetch_transport_allowlist": "mirror.example"}},
    {"bot_config_manifest": {"fetch_transport_allowlist": ["mirror.example", 42]}},
])
def test_a_malformed_block_is_a_configuration_error(settings):
    # A typo in the yaml block must fail its reader loudly, never
    # silently refuse strictly (or worse: silently widen).
    with pytest.raises(ValueError):
        transport_allowlist_from_config(settings)


_SHIPPED_APP_YAML = (
    pathlib.Path(__file__).resolve().parents[5]
    / "src" / "agentclaw" / "community" / "configs" / "application.yaml"
)


def test_the_shipped_yaml_carries_a_neutral_empty_allowlist():
    # The knob must exist and must ship empty: whatever a deployment
    # exempts appears in its own overlay diff, never in community source.
    tree = yaml.safe_load(_SHIPPED_APP_YAML.read_text(encoding="utf-8"))
    settings = tree["user_config"]
    assert settings["bot_config_manifest"]["fetch_transport_allowlist"] == []
    assert transport_allowlist_from_config(settings) == frozenset()


def test_a_misnamed_category_taking_the_default_cap_is_structurally_impossible():
    """The category vocabulary is bound to the cap table by construction: a
    misspelled category would otherwise silently take the per-entry default's
    cap, and no test would ever notice the quiet wrong answer."""
    assert set(FETCH_ENTRY_LIMITS) == {c.value for c in FetchCategory}
    # And the str value of the enum keys the table — spelling and lookup
    # meet in the same place.
    assert FETCH_ENTRY_LIMITS[FetchCategory.IDENTITY] == 1024 * 1024
    assert FETCH_ENTRY_LIMITS[FetchCategory.SKILLS] == 100 * 1024 * 1024
