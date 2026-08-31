"""The gateway must read secbaas's ``policy`` column exactly as secbaas does.

These cases are the contract: each one names a form the column really takes in
production and the reading that keeps a migrated app's reach identical to what
it had. Reading wider than secbaas would grant access secbaas never did — the
one mistake a credential migration must not make.
"""

from __future__ import annotations

import json

import pytest

from gateway.community.core.baas_migration import (
    WILDCARD,
    parse_allowed_bots,
    split_bot_reference,
)


@pytest.mark.parametrize(
    "policy",
    [
        pytest.param(None, id="null-column"),
        pytest.param("", id="empty-string"),
        pytest.param("   ", id="blank"),
        pytest.param("not json at all", id="unparseable"),
        pytest.param("[1, 2, 3]", id="not-an-object"),
        pytest.param('"a string"', id="json-scalar"),
        pytest.param("{}", id="object-without-allowed-bots"),
        pytest.param('{"allowed_bots": "bot-1"}', id="allowed-bots-not-a-list"),
        pytest.param('{"allowed_bots": null}', id="allowed-bots-null"),
        pytest.param('{"allowed_bots": []}', id="explicitly-empty"),
        pytest.param('{"allowed_bots": ["NONE"]}', id="legacy-none-sentinel"),
    ],
)
def test_deny_all_forms_read_as_no_bots(policy: str | None) -> None:
    """Every ambiguous or malformed form is fail-closed, as upstream.

    Note this includes the *unparseable* ones. Raising there would be defensible
    in isolation and is wrong here: it turns a key whose policy granted nothing
    into a migration failure the caller cannot fix, when the faithful answer —
    no bots — is exactly what secbaas itself would enforce on every request.
    """
    assert parse_allowed_bots(policy) == []


def test_none_sentinel_is_filtered_without_discarding_real_bots() -> None:
    policy = json.dumps({"allowed_bots": ["NONE", "bot-1:u1"]})
    assert parse_allowed_bots(policy) == ["bot-1:u1"]


def test_whitelist_is_preserved_in_order() -> None:
    policy = json.dumps({"allowed_bots": ["b1:u1", "b2:u2", "b3:u3"]})
    assert parse_allowed_bots(policy) == ["b1:u1", "b2:u2", "b3:u3"]


def test_wildcard_wins_and_collapses_the_list() -> None:
    """``"*"`` anywhere is allow-all, matching upstream's explicit precedence."""
    policy = json.dumps({"allowed_bots": ["b1:u1", WILDCARD, "b2:u2"]})
    assert parse_allowed_bots(policy) == [WILDCARD]


def test_non_string_entries_are_dropped() -> None:
    """A list holding a number cannot name a bot; it must not reach a grant."""
    policy = json.dumps({"allowed_bots": ["b1:u1", 7, None, {"b": 1}]})
    assert parse_allowed_bots(policy) == ["b1:u1"]


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("bot-1:u1", ("bot-1", "u1")),
        ("default:012345", ("default", "012345")),
        # Only the FIRST colon splits: an entity id may contain one.
        ("bot-1:u1:extra", ("bot-1", "u1:extra")),
    ],
)
def test_split_bot_reference_accepts_both_halves(
    reference: str, expected: tuple[str, str]
) -> None:
    assert split_bot_reference(reference) == expected


@pytest.mark.parametrize(
    "reference",
    [
        pytest.param("bot-1", id="no-colon"),
        pytest.param("", id="empty"),
        pytest.param(":u1", id="missing-bot-id"),
        pytest.param("bot-1:", id="missing-entity-id"),
        pytest.param(":", id="both-halves-empty"),
    ],
)
def test_split_bot_reference_rejects_half_a_reference(reference: str) -> None:
    """A grant needs a bot AND the person lending access; half of one is neither."""
    assert split_bot_reference(reference) is None
