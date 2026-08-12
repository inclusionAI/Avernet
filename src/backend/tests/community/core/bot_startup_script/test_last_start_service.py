"""Unit tests for the last-start reader (issue #926)."""
import json
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_startup_script.services.last_start_service import (
    BotStartupScriptRunReader,
)


def _reader(devices=None, raises=None):
    baas = MagicMock()
    if raises is not None:
        baas.get_publish_progress.side_effect = raises
    else:
        # device_details is a list of BATCHES; the device records nest under
        # each batch's "devices". Fixtures must mirror the producer, not the
        # shape the consumer wishes for — getting this wrong is what made two
        # earlier rounds of tests pass against broken code.
        baas.get_publish_progress.return_value = {
            "device_details": [
                {
                    "batch_id": 1,
                    "batch_index": 0,
                    "stage": "online",
                    "status": "COMPLETED",
                    "devices": devices or [],
                }
            ]
        }
    return BotStartupScriptRunReader(baas), baas


def _device(uuid="d1", status="SUCCESS", **result):
    return {
        "device_uuid": uuid,
        "result_status": status,
        "result_message": json.dumps(result) if result else None,
    }


def test_no_publish_id_returns_empty_without_calling_baas():
    reader, baas = _reader()
    assert reader.last_start(publish_id=None) == []
    baas.get_publish_progress.assert_not_called()


def test_parses_exit_code_and_output_per_instance():
    reader, _ = _reader([_device(exit_code=0, stdout="hello", stderr="")])
    (r,) = reader.last_start(publish_id=42)
    assert (r.instance_id, r.status, r.exit_code, r.stdout) == ("d1", "success", 0, "hello")


def test_instances_can_disagree():
    reader, _ = _reader([
        _device("d1", "SUCCESS", exit_code=0, stdout="ok"),
        _device("d2", "FAILED", exit_code=127, stderr="not found"),
    ])
    results = reader.last_start(publish_id=42)
    assert [r.status for r in results] == ["success", "failed"]


def test_truncation_is_surfaced():
    reader, _ = _reader([_device(exit_code=0, stdout="big[truncated]")])
    assert reader.last_start(publish_id=42)[0].truncated is True


def test_pending_device_has_no_exit_code():
    reader, _ = _reader([{"device_uuid": "d1", "result_status": "PROCESSING"}])
    r = reader.last_start(publish_id=42)[0]
    assert r.status == "pending" and r.exit_code is None


def test_plain_text_result_message_is_surfaced_not_dropped():
    """Older records are plain text — usually the error someone is looking for."""
    reader, _ = _reader([
        {"device_uuid": "d1", "result_status": "FAILED", "result_message": "boom"}
    ])
    assert reader.last_start(publish_id=42)[0].stderr == "boom"


def test_malformed_json_does_not_raise():
    reader, _ = _reader([
        {"device_uuid": "d1", "result_status": "FAILED", "result_message": "{bad"}
    ])
    assert reader.last_start(publish_id=42)[0].stderr == "{bad"


def test_baas_failure_returns_empty_rather_than_500():
    """Reading 'what happened last time' must not fail the request."""
    reader, _ = _reader(raises=RuntimeError("baas down"))
    assert reader.last_start(publish_id=42) == []


def test_flattens_batches_into_device_records():
    """device_details holds BatchDeviceProgress, devices nest one level down."""
    baas = MagicMock()
    baas.get_publish_progress.return_value = {
        "device_details": [
            {"batch_id": 1, "devices": [_device("d1", exit_code=0, stdout="a")]},
            {"batch_id": 2, "devices": [_device("d2", exit_code=0, stdout="b")]},
        ]
    }
    results = BotStartupScriptRunReader(baas).last_start(publish_id=42)
    assert [r.instance_id for r in results] == ["d1", "d2"]


def test_batches_never_leak_through_as_phantom_devices():
    """Iterating device_details directly yielded one empty pending entry per
    batch — a batch has no device_uuid and no result_message."""
    baas = MagicMock()
    baas.get_publish_progress.return_value = {
        "device_details": [
            {"batch_id": 1, "batch_index": 0, "status": "COMPLETED", "devices": []}
        ]
    }
    assert BotStartupScriptRunReader(baas).last_start(publish_id=42) == []


def test_failed_status_wins_even_when_the_exit_code_is_zero():
    """BaaS writes result_status=FAILED with exit_code=0 on destroy/stop."""
    reader, _ = _reader([_device("d1", "FAILED", exit_code=0)])
    assert reader.last_start(publish_id=42)[0].status == "failed"


def test_tolerates_a_flat_device_list():
    baas = MagicMock()
    baas.get_publish_progress.return_value = {"devices": [_device(exit_code=0)]}
    assert len(BotStartupScriptRunReader(baas).last_start(publish_id=42)) == 1


def test_exit_code_wins_over_a_contradicting_publish_status():
    """The hook is nohup'd, so the workflow can read SUCCESS while it failed."""
    reader, _ = _reader([_device("d1", "SUCCESS", exit_code=127, stderr="boom")])
    r = reader.last_start(publish_id=42)[0]
    assert r.status == "failed" and r.exit_code == 127


def test_truncation_marker_is_anchored_to_the_end():
    reader, _ = _reader([
        _device("d1", "SUCCESS", exit_code=0, stdout="mentions [truncated] mid-line")
    ])
    assert reader.last_start(publish_id=42)[0].truncated is False
