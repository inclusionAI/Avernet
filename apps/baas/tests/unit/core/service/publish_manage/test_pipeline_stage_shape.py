"""Pipeline shape rules for batch generation.

Two invariants the operator depends on, regardless of batch capacity:

1. A gated pipeline never exceeds four stages
   (PREPUB -> GRAY -> PROD_FIRST_BATCH -> PROD_OTHER_BATCH).
2. With fewer than four devices the pipeline compacts to one stage per device,
   with exactly one device in each.

These are pinned here because changing ``batch_capacity`` or the stage table
can silently break them.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from secbaas.community.api.publish_manage import (
    PublishConfig,
    PublishType,
)
from secbaas.community.core.service.publish_manage import DefaultPublishService

GATED_STAGES = ("PREPUB", "GRAY", "PROD_FIRST_BATCH", "PROD_OTHER_BATCH")
MAX_STAGES = 4


def _svc() -> DefaultPublishService:
    return DefaultPublishService(
        bot_repo=MagicMock(),
        device_repo=MagicMock(),
        rel_repo=MagicMock(),
        session_repo=MagicMock(),
        publish_repo=MagicMock(),
        batch_repo=MagicMock(),
        publish_record_repo=MagicMock(),
        template_service=MagicMock(),
        bot_service=MagicMock(),
        device_service=MagicMock(),
    )


def _batches(publish_type: PublishType, replica_desired: int):
    return _svc()._generate_batches(
        publish_type, PublishConfig(replica_desired=replica_desired)
    )


def _devices_per_stage(batches) -> dict[str, int]:
    out: dict[str, int] = {}
    for b in batches:
        out[b.stage] = out.get(b.stage, 0) + b.batch_capacity
    return out


def _stage_names(batches) -> set[str]:
    return {b.stage for b in batches}


class TestMaxFourStages:
    @pytest.mark.parametrize("replicas", [1, 2, 3, 4, 5, 10, 25, 50, 100, 500, 1000])
    def test_never_exceeds_four_stages(self, replicas):
        batches = _batches(PublishType.CREATE, replicas)
        assert len(_stage_names(batches)) <= MAX_STAGES

    @pytest.mark.parametrize("replicas", [1, 4, 100])
    def test_only_known_gated_stages_are_used(self, replicas):
        batches = _batches(PublishType.CREATE, replicas)
        assert _stage_names(batches) <= set(GATED_STAGES)

    def test_large_fleet_uses_all_four(self):
        batches = _batches(PublishType.CREATE, 1000)
        assert len(_stage_names(batches)) == MAX_STAGES


class TestSmallFleetOneDevicePerStage:
    @pytest.mark.parametrize("replicas", [1, 2, 3])
    def test_one_device_per_stage(self, replicas):
        batches = _batches(PublishType.CREATE, replicas)

        per_stage = _devices_per_stage(batches)
        assert len(per_stage) == replicas, "one stage per device"
        assert set(per_stage.values()) == {1}, "exactly one device per stage"
        assert all(b.batch_capacity == 1 for b in batches)

    def test_single_device_uses_prod_first_batch(self):
        batches = _batches(PublishType.CREATE, 1)
        assert [b.stage for b in batches] == ["PROD_FIRST_BATCH"]

    def test_three_devices_drop_the_canary_stage(self):
        """Compaction keeps the most production-relevant stages."""
        batches = _batches(PublishType.CREATE, 3)
        assert _stage_names(batches) == {
            "GRAY",
            "PROD_FIRST_BATCH",
            "PROD_OTHER_BATCH",
        }

    def test_four_devices_use_the_full_pipeline(self):
        batches = _batches(PublishType.CREATE, 4)
        assert _stage_names(batches) == set(GATED_STAGES)
        assert set(_devices_per_stage(batches).values()) == {1}


class TestOneBatchPerStage:
    """Each stage must emit exactly one batch, whatever the fleet size."""

    @pytest.mark.parametrize("replicas", [5, 10, 25, 100])
    def test_exactly_one_batch_per_stage(self, replicas):
        """Up to 100 devices: 4 stages split evenly, so each fits in one batch."""
        batches = _batches(PublishType.CREATE, replicas)

        per_stage: dict[str, int] = {}
        for b in batches:
            per_stage[b.stage] = per_stage.get(b.stage, 0) + 1
        assert set(per_stage.values()) == {1}, f"stage batch counts: {per_stage}"

    def test_above_capacity_per_stage_splits(self):
        """A stage holding more than batch_capacity still splits, by design."""
        batches = _batches(PublishType.CREATE, 1000)

        per_stage: dict[str, int] = {}
        for b in batches:
            per_stage[b.stage] = per_stage.get(b.stage, 0) + 1
        # 1000 devices / 4 stages = 250 per stage, against a 100 capacity.
        assert set(per_stage.values()) == {3}
        assert all(b.batch_capacity <= 100 for b in batches)

    @pytest.mark.parametrize("replicas", [10, 100])
    def test_batch_count_equals_stage_count(self, replicas):
        batches = _batches(PublishType.CREATE, replicas)
        assert len(batches) == len(_stage_names(batches))

    def test_no_stage_is_split_by_capacity(self):
        """100 devices in 4 stages is 4 batches, not 25 or 28."""
        batches = _batches(PublishType.CREATE, 100)
        assert len(batches) == 4


class TestEveryDeviceIsAccountedFor:
    @pytest.mark.parametrize("replicas", [1, 2, 3, 4, 7, 25, 100, 250])
    def test_stage_totals_sum_to_replica_desired(self, replicas):
        batches = _batches(PublishType.CREATE, replicas)
        assert sum(b.batch_capacity for b in batches) == replicas

    def test_batch_indices_are_contiguous(self):
        batches = _batches(PublishType.CREATE, 250)
        assert [b.batch_index for b in batches] == list(range(len(batches)))
