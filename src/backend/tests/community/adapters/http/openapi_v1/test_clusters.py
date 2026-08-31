"""Unit tests for the cluster ↔ engine rule (Track B, Task 2)."""

from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1.clusters import (
    cluster_for_engine,
    validate_engine_cluster,
)
from agentclaw.community.adapters.http.openapi_v1.responses import ClusterMismatchError


def test_teclaw_maps_to_andc():
    assert cluster_for_engine("teclaw") == "ANDC"


@pytest.mark.parametrize("engine", ["openclaw", "moltis", "claude_code", "", None])
def test_non_teclaw_maps_to_acra(engine):
    assert cluster_for_engine(engine) == "ACRA"


def test_validate_accepts_matching_pairs():
    validate_engine_cluster("teclaw", "ANDC")
    validate_engine_cluster("openclaw", "ACRA")


@pytest.mark.parametrize(
    "engine,cluster",
    [("teclaw", "ACRA"), ("openclaw", "ANDC")],
)
def test_validate_rejects_mismatched_pairs(engine, cluster):
    with pytest.raises(ClusterMismatchError):
        validate_engine_cluster(engine, cluster)
