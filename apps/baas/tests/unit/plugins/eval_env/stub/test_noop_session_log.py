"""NoopEvalSessionLog 单元测试。"""

from secbaas.community.plugins.eval_env.stub._noop_session_log import (
    NoopEvalSessionLog,
)


class TestNoopEvalSessionLog:
    def test_log_eval_session_is_noop(self):
        log = NoopEvalSessionLog()
        # 应不抛异常，空操作
        log.log_eval_session(
            eval_id="eval-1",
            bot_id="bot-1",
            session_id="sess-1",
            method="deliver_message",
        )

    def test_enrich_chat_metadata_returns_original(self):
        log = NoopEvalSessionLog()
        metadata = {"eval_id": "eval-1"}
        result = log.enrich_chat_metadata(metadata=metadata, run_id="run-1")
        assert result is metadata
        assert result == {"eval_id": "eval-1"}

    def test_extract_eval_headers_returns_original(self):
        log = NoopEvalSessionLog()
        metadata = {"key": "value"}
        result = log.extract_eval_headers(
            metadata=metadata,
            x_eval_id="eval-1",
            x_default_tag="staging",
        )
        assert result is metadata
        assert result == {"key": "value"}
