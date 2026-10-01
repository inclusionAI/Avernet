"""Unit tests for the PublishExtState delivery-composition seam.

``compose_live`` (release/eval, LIVE channel re-fetch) and ``compose_stored``
(restart/rollback, STORED per-stage slot) are the single boundary through which
every delivery path composes its artifact. These tests pin the two producers'
behavior directly, independent of the flow facade.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from agentclaw.community.core.service_bot.services.deploy.engine_ext_stage import (
    DeliveryArtifact,
)
from agentclaw.community.core.service_bot.services.publish_flow.ext_state import (
    PublishExtState,
)
from agentclaw.community.core.service_bot.types import PublishStage

_CH = {"channels": {"dingding": {"enabled": True, "accounts": [{"client_id": "c1"}]}}}


def _artifact(stage="draft", channels=None):
    art = {"engine_type": "teclaw", "engine_ext": {"bot_id": "b", "owner_id": "u1", "stage": stage}}
    if channels is not None:
        art["engine_overrides"] = channels
    return art


def _record(ext):
    rec = Mock()
    rec.ext = ext
    rec.owner_id = "u1"
    rec.source_bot_id = "bot-1"
    return rec


def _ext_state(reader_result=_CH):
    reader = Mock()
    reader.overrides_for_stage.return_value = reader_result
    return PublishExtState(Mock(), reader), reader


# ── compose_live (LIVE re-fetch) ─────────────────────────────────────────────


@pytest.mark.unit
def test_compose_live_overlays_live_channels_and_returns_overrides():
    seam, reader = _ext_state(reader_result=_CH)
    record = _record({"config_artifact": _artifact("draft")})

    delivery, overrides = seam.compose_live(record, PublishStage.VERIFY)

    assert isinstance(delivery, DeliveryArtifact)
    assert delivery.config_artifact["engine_ext"]["stage"] == "canary"  # restamped
    assert delivery.config_artifact["engine_overrides"] == _CH  # live channels overlaid
    assert overrides == _CH  # handed back for the release path to persist
    reader.overrides_for_stage.assert_called_once()


@pytest.mark.unit
def test_compose_live_arca_no_config_artifact_is_noop_and_skips_reader():
    # ARCA mount path: ext has no config_artifact → no fetch, nothing composed.
    seam, reader = _ext_state()
    record = _record({"migration_path": "/tmp/m"})

    delivery, overrides = seam.compose_live(record, PublishStage.ONLINE)

    assert delivery.config_artifact is None
    assert overrides is None
    reader.overrides_for_stage.assert_not_called()


# ── compose_stored (STORED slot) ─────────────────────────────────────────────


@pytest.mark.unit
def test_compose_stored_delivers_the_slot_not_live_channels():
    # Regression shape (#168 / restart retry-hole): the base carries stale channels
    # and BOTH stages are stored; composing VERIFY must deliver the VERIFY slot.
    verify_ch = {"channels": {"dingding": {"accounts": [{"client_id": "verify"}]}}}
    online_ch = {"channels": {"dingding": {"accounts": [{"client_id": "online"}]}}}
    seam, reader = _ext_state()
    ext = {
        "config_artifact": _artifact("release", {"channels": {"dingding": {"accounts": [{"client_id": "stale"}]}}}),
        "engine_overrides_by_stage": {"verify": verify_ch, "online": online_ch},
    }

    delivery = seam.compose_stored(ext, PublishStage.VERIFY)

    assert delivery.config_artifact["engine_overrides"] == verify_ch
    assert delivery.config_artifact["engine_ext"]["stage"] == "canary"
    reader.overrides_for_stage.assert_not_called()  # stored, never live


@pytest.mark.unit
def test_compose_stored_pre_feature_record_restamps_base_only():
    base_ch = {"channels": {"dingding": {"accounts": [{"client_id": "base"}]}}}
    seam, _ = _ext_state()
    ext = {"config_artifact": _artifact("release", base_ch)}  # no engine_overrides_by_stage

    delivery = seam.compose_stored(ext, PublishStage.VERIFY)

    assert delivery.config_artifact["engine_overrides"] == base_ch  # unchanged (no overlay)
    assert delivery.config_artifact["engine_ext"]["stage"] == "canary"  # restamped


@pytest.mark.unit
def test_compose_stored_tolerates_json_null_slot():
    seam, _ = _ext_state()
    ext = {"config_artifact": _artifact("release"), "engine_overrides_by_stage": None}

    delivery = seam.compose_stored(ext, PublishStage.VERIFY)

    assert delivery.config_artifact["engine_ext"]["stage"] == "canary"


@pytest.mark.unit
def test_compose_stored_arca_no_config_artifact_is_none():
    seam, _ = _ext_state()
    delivery = seam.compose_stored({"migration_path": "/tmp/m"}, PublishStage.ONLINE)
    assert delivery.config_artifact is None
