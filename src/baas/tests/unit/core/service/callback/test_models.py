# Copyright (c) 2004-2026, Ant Group.
# All Rights Reserved.

"""Unit tests for callback _models.py."""

from secbaas.community.core.service.callback import CallbackPayload, CallbackResult


class TestCallbackPayload:
    def test_to_dict_with_all_fields(self):
        payload = CallbackPayload(
            run_id="r1",
            bot_id="b1",
            status="COMPLETED",
            result="done",
            error="err",
            metadata={"key": "val"},
            session_id="sess-1",
        )
        d = payload.to_dict()
        assert d == {
            "run_id": "r1",
            "bot_id": "b1",
            "status": "COMPLETED",
            "result": "done",
            "error": "err",
            "metadata": {"key": "val"},
            "session_id": "sess-1",
        }

    def test_to_dict_with_defaults(self):
        payload = CallbackPayload(run_id="r1", bot_id="b1", status="PENDING")
        d = payload.to_dict()
        assert d["run_id"] == "r1"
        assert d["bot_id"] == "b1"
        assert d["status"] == "PENDING"
        assert d["result"] is None
        assert d["error"] is None
        assert d["metadata"] is None
        assert d["session_id"] is None

    def test_frozen(self):
        payload = CallbackPayload(run_id="r1", bot_id="b1", status="PENDING")
        try:
            payload.run_id = "r2"
            raise AssertionError("Should have raised FrozenInstanceError")
        except AttributeError:
            pass

    def test_result_message_truncation_not_applied(self):
        """CallbackResult.message is stored as-is (no truncation in model)."""
        long_msg = "x" * 300
        result = CallbackResult(success=False, status_code=500, message=long_msg)
        assert result.message == long_msg

    def test_result_defaults(self):
        result = CallbackResult(success=True)
        assert result.success is True
        assert result.status_code is None
        assert result.message == ""
