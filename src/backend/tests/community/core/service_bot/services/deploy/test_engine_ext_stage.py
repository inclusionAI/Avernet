"""Unit tests for the engine_ext stage helper (mapping + ``restamp_stage``)."""
from __future__ import annotations

import pytest

from agentclaw.community.core.service_bot.services.deploy.engine_ext_stage import (
    STAGE_VALUE,
    DeliveryArtifact,
    apply_engine_overrides,
    enrich_engine_ext,
    restamp_stage,
)
from agentclaw.community.core.service_bot.types import PublishStage


@pytest.mark.unit
def test_enrich_adds_keys_with_stage_value() -> None:
    out = enrich_engine_ext(
        {"memory_ref": "nas://m"}, bot_id="b7", owner_id="u1", stage=PublishStage.DRAFT
    )
    assert out == {
        "memory_ref": "nas://m",
        "bot_id": "b7",
        "owner_id": "u1",
        "stage": "draft",
    }


@pytest.mark.unit
def test_enrich_backend_keys_win_collision_and_default_none() -> None:
    out = enrich_engine_ext(
        {"bot_id": "ENGINE", "stage": "x", "keep": "me"},
        bot_id=None,
        owner_id=None,
        stage=PublishStage.VERIFY,
    )
    assert out == {"keep": "me", "bot_id": "", "owner_id": "", "stage": "canary"}


@pytest.mark.unit
def test_enrich_stringifies_bot_id_and_does_not_mutate_input() -> None:
    src = {"a": 1}
    out = enrich_engine_ext(src, bot_id=7, owner_id="u1", stage=PublishStage.ONLINE)
    assert out["bot_id"] == "7"
    assert out["stage"] == "release"
    assert src == {"a": 1}  # input untouched


@pytest.mark.unit
def test_stage_value_mapping() -> None:
    # The deliberate translation: enum verify/online → engine canary/release.
    assert STAGE_VALUE == {
        PublishStage.DRAFT: "draft",
        PublishStage.VERIFY: "canary",
        PublishStage.ONLINE: "release",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "stage,expected",
    [
        (PublishStage.DRAFT, "draft"),
        (PublishStage.VERIFY, "canary"),
        (PublishStage.ONLINE, "release"),
    ],
)
def test_restamp_sets_stage(stage: PublishStage, expected: str) -> None:
    artifact = {"engine_type": "teclaw", "engine_ext": {"bot_id": "b", "stage": "draft"}}
    out = restamp_stage(artifact, stage)
    assert out["engine_ext"]["stage"] == expected


@pytest.mark.unit
def test_restamp_preserves_other_engine_ext_keys() -> None:
    artifact = {
        "engine_ext": {
            "bot_id": "b",
            "owner_id": "u1",
            "stage": "draft",
            "memory_ref": "nas://ws/MEMORY.md",
        }
    }
    out = restamp_stage(artifact, PublishStage.VERIFY)
    assert out["engine_ext"] == {
        "bot_id": "b",
        "owner_id": "u1",
        "stage": "canary",
        "memory_ref": "nas://ws/MEMORY.md",
    }


@pytest.mark.unit
def test_restamp_does_not_mutate_input() -> None:
    engine_ext = {"bot_id": "b", "stage": "draft"}
    artifact = {"engine_ext": engine_ext}
    out = restamp_stage(artifact, PublishStage.ONLINE)
    # input untouched
    assert engine_ext == {"bot_id": "b", "stage": "draft"}
    assert artifact["engine_ext"] is engine_ext
    # output is a distinct object
    assert out is not artifact
    assert out["engine_ext"] is not engine_ext


@pytest.mark.unit
def test_restamp_none_is_noop() -> None:
    # ARCA mount path pins migration_path, not config_artifact.
    assert restamp_stage(None, PublishStage.VERIFY) is None


@pytest.mark.unit
def test_restamp_no_engine_ext_is_noop() -> None:
    artifact = {"engine_type": "teclaw"}
    assert restamp_stage(artifact, PublishStage.VERIFY) is artifact


@pytest.mark.unit
def test_restamp_engine_ext_without_stage_is_noop() -> None:
    # A non-enriched engine_ext (no backend ``stage`` seeded at build) is left alone.
    artifact = {"engine_ext": {"memory_ref": "nas://ws/MEMORY.md"}}
    assert restamp_stage(artifact, PublishStage.VERIFY) is artifact


# ── apply_engine_overrides ──────────────────────────────────────────────

_VERIFY_CHANNELS = {"channels": {"dingding": {"enabled": True, "accounts": [{"client_id": "v"}]}}}


@pytest.mark.unit
def test_apply_overlays_channels_replacing_base() -> None:
    artifact = {
        "engine_overrides": {
            "channels": {"dingding": {"enabled": True, "accounts": [{"client_id": "draft"}]}}
        }
    }
    out = apply_engine_overrides(artifact, _VERIFY_CHANNELS)
    assert out["engine_overrides"] == _VERIFY_CHANNELS


@pytest.mark.unit
def test_apply_empty_overrides_clears_channels() -> None:
    artifact = {"engine_overrides": {"channels": {"dingding": {"enabled": True}}}}
    out = apply_engine_overrides(artifact, {})
    assert out["engine_overrides"] == {}  # channels dropped


@pytest.mark.unit
def test_apply_preserves_base_non_channel_keys() -> None:
    artifact = {
        "engine_overrides": {
            "temperature": 0.2,
            "channels": {"dingding": {"enabled": True, "accounts": [{"client_id": "draft"}]}},
        }
    }
    out = apply_engine_overrides(artifact, _VERIFY_CHANNELS)
    assert out["engine_overrides"] == {"temperature": 0.2, **_VERIFY_CHANNELS}


@pytest.mark.unit
def test_apply_sets_channels_when_base_has_none() -> None:
    artifact = {"engine_overrides": {}, "engine_ext": {"stage": "draft"}}
    out = apply_engine_overrides(artifact, _VERIFY_CHANNELS)
    assert out["engine_overrides"] == _VERIFY_CHANNELS


@pytest.mark.unit
def test_apply_ignores_non_channel_keys_in_overrides() -> None:
    # Contract is channels-only: a non-channel key in `overrides` must NOT leak into
    # the delivered artifact (and must not clobber a base knob).
    artifact = {"engine_overrides": {"temperature": 0.2}}
    out = apply_engine_overrides(artifact, {"channels": {"dingding": {}}, "temperature": 0.9})
    assert out["engine_overrides"] == {"temperature": 0.2, "channels": {"dingding": {}}}


@pytest.mark.unit
def test_apply_none_config_artifact_is_noop() -> None:
    # ARCA mount path: no composed artifact to overlay.
    assert apply_engine_overrides(None, _VERIFY_CHANNELS) is None


@pytest.mark.unit
def test_apply_none_overrides_is_noop() -> None:
    # No stored stage overrides (pre-feature record) → deliver base unchanged.
    artifact = {"engine_overrides": {"channels": {"dingding": {"enabled": True}}}}
    assert apply_engine_overrides(artifact, None) is artifact


@pytest.mark.unit
def test_apply_does_not_mutate_input() -> None:
    base_eo = {"temperature": 0.2, "channels": {"dingding": {"enabled": True}}}
    artifact = {"engine_overrides": base_eo}
    out = apply_engine_overrides(artifact, _VERIFY_CHANNELS)
    # input untouched
    assert base_eo == {"temperature": 0.2, "channels": {"dingding": {"enabled": True}}}
    assert artifact["engine_overrides"] is base_eo
    # output is a distinct object
    assert out is not artifact
    assert out["engine_overrides"] is not base_eo


# ── DeliveryArtifact.compose (restamp + overlay, the single combiner) ────────


@pytest.mark.unit
def test_compose_restamps_stage_and_overlays_channels() -> None:
    base = {
        "engine_ext": {"bot_id": "b", "stage": "draft"},
        "engine_overrides": {
            "channels": {"dingding": {"enabled": True, "accounts": [{"client_id": "draft"}]}}
        },
    }
    out = DeliveryArtifact.compose(base, PublishStage.VERIFY, _VERIFY_CHANNELS)
    assert out.config_artifact["engine_ext"]["stage"] == "canary"  # restamped
    assert out.config_artifact["engine_overrides"] == _VERIFY_CHANNELS  # overlaid


@pytest.mark.unit
def test_compose_none_overrides_restamps_only() -> None:
    # Pre-feature record: no stored overrides → base channels kept, stage restamped.
    base_channels = {"channels": {"dingding": {"accounts": [{"client_id": "base"}]}}}
    base = {"engine_ext": {"stage": "draft"}, "engine_overrides": base_channels}
    out = DeliveryArtifact.compose(base, PublishStage.ONLINE, None)
    assert out.config_artifact["engine_ext"]["stage"] == "release"
    assert out.config_artifact["engine_overrides"] == base_channels  # unchanged


@pytest.mark.unit
def test_compose_none_config_artifact_is_none() -> None:
    # ARCA mount path: no composed artifact in, config_artifact None out.
    assert DeliveryArtifact.compose(None, PublishStage.VERIFY, _VERIFY_CHANNELS).config_artifact is None


@pytest.mark.unit
def test_compose_does_not_mutate_input() -> None:
    engine_ext = {"bot_id": "b", "stage": "draft"}
    base = {"engine_ext": engine_ext, "engine_overrides": {}}
    DeliveryArtifact.compose(base, PublishStage.VERIFY, _VERIFY_CHANNELS)
    assert engine_ext == {"bot_id": "b", "stage": "draft"}  # input untouched
