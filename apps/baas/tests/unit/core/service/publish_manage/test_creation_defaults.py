"""Publish creation policy lives above the persistence boundary."""

import pytest

from secbaas.community.core.service.publish_manage._creation_defaults import (
    resolve_publish_creation,
)


@pytest.mark.parametrize("publish_type", ["CREATE", "RESTART", "UPDATE", "DESTROY"])
def test_resolve_missing_creation_values(publish_type):
    assert resolve_publish_creation(publish_type=publish_type, creator="u") == {
        "name": f"Publish-{publish_type}",
        "publisher": "u",
        "replica_desired": 1,
        "batch_capacity": 5,
        "batch_number": 1,
        "cooldown_seconds": 0,
    }


@pytest.mark.parametrize("numeric", [0, 3])
def test_explicit_empty_strings_and_zero_are_not_defaulted(numeric):
    assert resolve_publish_creation(
        publish_type="CREATE",
        creator="u",
        name="",
        publisher="",
        replica_desired=numeric,
        batch_capacity=numeric,
        batch_number=numeric,
        cooldown_seconds=numeric,
    ) == {
        "name": "",
        "publisher": "",
        "replica_desired": numeric,
        "batch_capacity": numeric,
        "batch_number": numeric,
        "cooldown_seconds": numeric,
    }


def test_only_missing_values_are_resolved_independently():
    values = resolve_publish_creation(
        publish_type="UPDATE",
        creator="u",
        name="release",
        batch_capacity=12,
    )
    assert values == {
        "name": "release",
        "publisher": "u",
        "replica_desired": 1,
        "batch_capacity": 12,
        "batch_number": 1,
        "cooldown_seconds": 0,
    }
