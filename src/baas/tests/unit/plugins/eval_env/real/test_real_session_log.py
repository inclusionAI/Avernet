"""RealEvalSessionLog 单元测试。"""

from secbaas.community.plugins.eval_env.real._real_session_log import (
    RealEvalSessionLog,
)


class TestRealEvalSessionLog:
    def test_log_eval_session(self):
        log = RealEvalSessionLog()
        # 应不抛异常
        log.log_eval_session(
            eval_id="eval-1",
            bot_id="bot-1",
            session_id="sess-1",
            method="deliver_message",
        )

    def test_enrich_chat_metadata_with_eval_id(self):
        log = RealEvalSessionLog()
        metadata = {"eval_id": "eval-1"}
        result = log.enrich_chat_metadata(metadata=metadata, run_id="run-1")
        assert result["eval_observed"] is True
        assert result["eval_run_id"] == "run-1"
        assert result["eval_id"] == "eval-1"

    def test_enrich_chat_metadata_with_default_tag(self):
        log = RealEvalSessionLog()
        metadata = {"default_tag": "staging"}
        result = log.enrich_chat_metadata(metadata=metadata, run_id="run-1")
        assert result["eval_observed"] is True
        assert result["eval_run_id"] == "run-1"

    def test_enrich_chat_metadata_without_eval_fields(self):
        log = RealEvalSessionLog()
        metadata = {"other_key": "value"}
        result = log.enrich_chat_metadata(metadata=metadata, run_id="run-1")
        assert "eval_observed" not in result
        assert "eval_run_id" not in result
        assert result["other_key"] == "value"

    def test_extract_eval_headers_with_x_eval_id(self):
        log = RealEvalSessionLog()
        metadata = {}
        result = log.extract_eval_headers(
            metadata=metadata,
            x_eval_id="eval-123",
            x_default_tag=None,
        )
        assert result["eval_id"] == "eval-123"
        assert "session_id" not in result

    def test_extract_eval_headers_with_x_eval_id_format_warning(self):
        """x_eval_id 不以 'eval' 开头时应记录 warning 但依然注入。"""
        log = RealEvalSessionLog()
        metadata = {}
        result = log.extract_eval_headers(
            metadata=metadata,
            x_eval_id="bad-format-id",
            x_default_tag=None,
        )
        assert result["eval_id"] == "bad-format-id"
        assert "session_id" not in result

    def test_extract_eval_headers_with_x_default_tag(self):
        log = RealEvalSessionLog()
        metadata = {}
        result = log.extract_eval_headers(
            metadata=metadata,
            x_eval_id=None,
            x_default_tag="staging",
        )
        assert result["default_tag"] == "staging"
        assert result["bot_options"]["lifecycle_stage"] == "staging"

    def test_extract_eval_headers_both_headers(self):
        log = RealEvalSessionLog()
        metadata = {}
        result = log.extract_eval_headers(
            metadata=metadata,
            x_eval_id="eval-456",
            x_default_tag="production",
        )
        assert result["eval_id"] == "eval-456"
        assert "session_id" not in result
        assert result["default_tag"] == "production"
        assert result["bot_options"]["lifecycle_stage"] == "production"

    def test_extract_eval_headers_no_headers(self):
        log = RealEvalSessionLog()
        metadata = {"existing": "data"}
        result = log.extract_eval_headers(
            metadata=metadata,
            x_eval_id=None,
            x_default_tag=None,
        )
        assert result == {"existing": "data"}

    def test_extract_eval_headers_preserves_existing_session_id(self):
        """metadata 中已有 session_id 时不应覆盖。"""
        log = RealEvalSessionLog()
        metadata = {"session_id": "original-sess"}
        result = log.extract_eval_headers(
            metadata=metadata,
            x_eval_id="eval-789",
            x_default_tag=None,
        )
        assert result["session_id"] == "original-sess"
        assert result["eval_id"] == "eval-789"

    def test_extract_eval_headers_preserves_existing_bot_options(self):
        """metadata 中已有 bot_options 时应更新而非覆盖。"""
        log = RealEvalSessionLog()
        metadata = {"bot_options": {"existing_key": "value"}}
        result = log.extract_eval_headers(
            metadata=metadata,
            x_eval_id=None,
            x_default_tag="staging",
        )
        assert result["bot_options"]["existing_key"] == "value"
        assert result["bot_options"]["lifecycle_stage"] == "staging"
