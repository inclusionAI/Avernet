"""The package's contract copies are the contract (one source of truth, README §1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentclaw.community.core.bot_genome import contract

_SOURCE = Path(__file__).resolve().parents[4] / "api-contracts" / "genome" / "v1"


@pytest.mark.parametrize("name", contract.CONTRACT_FILES)
def test_copy_is_byte_identical(name: str) -> None:
    assert (contract.CONTRACT_DIR / name).read_bytes() == (_SOURCE / name).read_bytes(), (
        f"{name}: copy api-contracts/genome/v1/{name} over core/bot_genome/contract/{name}"
    )


def test_validators_load_and_cross_reference() -> None:
    revision, patch = contract.schemas()
    assert revision["$id"] != patch["$id"]
    assert contract.patch_validator().is_valid({"patch_schema": 1}) is False
    errors = contract.schema_errors(contract.revision_validator(), {"genome_schema": 2})
    assert errors and errors[0].startswith("$:") or any("genome_schema" in e for e in errors)


def test_definition_validator_reaches_a_definition() -> None:
    policy = contract.definition_validator(contract.REVISION_SCHEMA_FILE, "policy")
    assert policy.is_valid({"mutable_genes": ["persona"], "pins": [], "risk_overrides": {}})
    assert not policy.is_valid({"mutable_genes": ["policy"], "pins": [], "risk_overrides": {}})


def test_risk_tiers_have_a_rule_per_op() -> None:
    _, patch = contract.schemas()
    ops = patch["definitions"]["op"]["properties"]["op"]["enum"]
    assert {rule["op"] for rule in contract.risk_tiers()["rules"]} == set(ops)
