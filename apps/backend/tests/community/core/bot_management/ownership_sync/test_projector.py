"""Pure ownership projection and its transport-free v1 value contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from enum import IntEnum

import pytest

from agentclaw.community.core.bot_management.ownership_sync.projector import (
    build_snapshot,
)
from agentclaw.community.kernel.bot_manager_sync import (
    DeliveryFailure,
    SnapshotUnavailable,
    SyncKey,
    SyncOutcome,
    SyncWorkKind,
)
from agentclaw.community.kernel.bot_ownership_sync import (
    OwnershipChange,
    OwnershipFacts,
    OwnershipSnapshot,
)
from agentclaw.community.plugin_api.bcs_ownership import BcsOwnershipError


def _facts() -> OwnershipFacts:
    return OwnershipFacts(SyncKey("tenant-a", "dev", 7), "bot", "stable", "owner", bot_type="service")


def test_owner_changes_do_not_change_bcs_address():
    facts = _facts()
    old_owner = build_snapshot(facts)
    new_owner = build_snapshot(replace(facts, owner_id="new-owner"))

    assert old_owner.bcs_bot_id == new_owner.bcs_bot_id == "bot:stable"
    assert old_owner.owner_user_id == "owner"
    assert new_owner.owner_user_id == "new-owner"
    assert old_owner.key == new_owner.key == facts.key
    assert facts == _facts()


@pytest.mark.parametrize("field", ["tenant", "env", "bot_id", "entity_id", "owner_id"])
@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "\t\n",
        " leading",
        "trailing ",
        "\tidentity",
        "identity\n",
        None,
        7,
        False,
        b"identity",
    ],
)
def test_invalid_identity_never_gets_a_fallback(field, value):
    facts = _facts()
    if field in {"tenant", "env"}:
        key_field = "avernet_tenant" if field == "tenant" else field
        facts = replace(facts, key=replace(facts.key, **{key_field: value}))
    else:
        facts = replace(facts, **{field: value})

    with pytest.raises(SnapshotUnavailable) as raised:
        build_snapshot(facts)

    assert raised.value.code == f"invalid_{field}"


class _IntLike(IntEnum):
    SEVEN = 7


@pytest.mark.parametrize("bot_pk", [0, -1, True, False, 7.0, "7", None, _IntLike.SEVEN])
def test_invalid_bot_pk_is_not_coerced(bot_pk):
    facts = _facts()
    facts = replace(facts, key=replace(facts.key, bot_pk=bot_pk))

    with pytest.raises(SnapshotUnavailable) as raised:
        build_snapshot(facts)

    assert raised.value.code == "invalid_bot_pk"


@pytest.mark.parametrize("bot_pk", [1, 7])
def test_positive_exact_integer_bot_pk_is_preserved(bot_pk):
    facts = _facts()
    facts = replace(facts, key=replace(facts.key, bot_pk=bot_pk))

    assert build_snapshot(facts) == OwnershipSnapshot(facts.key, "bot:stable", "owner")


@pytest.mark.parametrize(
    "value,field",
    [
        (_facts(), "owner_id"),
        (OwnershipSnapshot(_facts().key, "bot:stable", "owner"), "owner_user_id"),
        (
            OwnershipChange("bot:stable", None, None, False, "request-1"),
            "owner_user_id",
        ),
    ],
)
def test_ownership_values_are_frozen(value, field):
    with pytest.raises(FrozenInstanceError):
        setattr(value, field, "replacement")


@pytest.mark.parametrize("kind", list(DeliveryFailure))
def test_delivery_error_contains_only_classified_kind_and_code(kind):
    error = BcsOwnershipError(kind, "delivery_unavailable")

    assert error.kind is kind
    assert error.code == "delivery_unavailable"
    assert str(error) == f"{kind.value} delivery_unavailable"
    assert not hasattr(error, "response")
    assert not hasattr(error, "body")


def test_work_kinds_and_outcomes_preserve_existing_values():
    assert {kind.name: kind.value for kind in SyncWorkKind} == {
        "MANAGERS": "managers",
        "OWNERSHIP": "ownership",
    }
    assert {outcome.name: outcome.value for outcome in SyncOutcome} == {
        "CONFIRMED": "confirmed",
        "ACKNOWLEDGED_EMPTY": "acknowledged_empty",
        "DRIFT": "drift",
        "FAILED": "failed",
        "BOT_MISSING_NOOP": "bot_missing_noop",
    }


@pytest.mark.parametrize(
    "bot_type,bot_id,reference,expected",
    [
        ("personal", "bot", None, "bot:owner"),
        (None, "bot", None, "bot:owner"),
        ("desktop", "bot", None, "bot:owner"),
        ("service", "default", None, "default:owner"),
        ("service", "bot", None, "bot:stable"),
        ("service", "bot", "bot:original-owner", "bot:original-owner"),
    ],
)
def test_owner_and_managers_use_registration_identity(
    bot_type, bot_id, reference, expected
):
    from agentclaw.community.core.bot_collaborator.manager_sync.projector import (
        build_snapshot as managers,
    )
    from agentclaw.community.kernel.bot_manager_sync import SyncFacts

    facts = replace(
        _facts(), bot_type=bot_type, bot_id=bot_id, bcn_provider_bot_ref=reference
    )
    assert build_snapshot(facts).bcs_bot_id == expected
    manager_facts = SyncFacts(
        facts.key,
        facts.bot_id,
        facts.entity_id,
        facts.owner_id,
        (),
        facts.bot_type,
        facts.bcn_provider_bot_ref,
    )
    assert managers(manager_facts).bcs_bot_id == expected


@pytest.mark.parametrize("reference", ["other:old", "bot:", 123])
def test_invalid_retained_reference_never_sends(reference):
    with pytest.raises(SnapshotUnavailable, match="invalid_bcn_identity"):
        build_snapshot(replace(_facts(), bcn_provider_bot_ref=reference))
