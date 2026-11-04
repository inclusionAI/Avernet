"""The wire request must honor the selected model and use only profile text."""

import logging

import pytest
from app.infra.reranker.http_reranker import HttpReranker, get_reranker

from tests.contract.test_tc_catalog import runtime as tc_runtime  # noqa: F401
from tests.contract.test_tc_catalog import sync

QUERY = "UCT2.0 事件策略日志为空"
PREFIX = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"




@pytest.fixture
def wire(monkeypatch):
    import requests

    monkeypatch.setenv("RERANKER_BASE_URL", "https://reranker.example.test/v1")
    monkeypatch.setenv("RERANKER_API_KEY", "test-placeholder")
    sent = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [
                {"index": i, "relevance_score": 0.8}
                for i, _ in enumerate(self.payload["documents"])
            ]}

    def post(url, *, headers, json, timeout):
        sent.append(json)
        return Response(json)

    monkeypatch.setattr(requests, "post", post)
    return sent


@pytest.mark.parametrize("configured,requested", [
    ("bge-reranker-v2-m3", "Qwen3-Reranker-4B"),
    ("Qwen3-Reranker-4B", "bge-reranker-v2-m3"),
])
def test_explicit_model_reaches_http_without_changing_process_default(monkeypatch, wire, configured, requested):
    import os

    monkeypatch.setenv("RERANKER_MODEL", configured)
    result = get_reranker(requested).rerank(QUERY, [{"id": "bot:1", "text": "名称: UCT20值班助手"}], 1)
    assert result[0].score == 0.8
    assert wire[0]["model"] == requested
    assert os.environ["RERANKER_MODEL"] == configured


def test_qwen_wire_uses_exact_platform_template(monkeypatch, wire):
    monkeypatch.setenv("RERANKER_MODEL", "Qwen3-Reranker-4B")
    result = get_reranker("Qwen3-Reranker-4B").rerank(QUERY, [{"id": "bot:1", "text": "名称: UCT20值班助手"}], 1)
    assert result[0].score == 0.8
    assert wire[0]["query"] == PREFIX + "<Instruct>: Given a web search query, retrieve relevant passages that answer the query\n<Query>: UCT2.0 事件策略日志为空\n<Document>: "
    assert wire[0]["documents"] == ["名称: UCT20值班助手" + SUFFIX]


@pytest.mark.parametrize("query,documents", [
    (QUERY, ["名称: UCT20值班助手"]),
    ("  查询\n下一行  ", ["  多行\n正文\t保留  ", "Passage: 原文自带前缀"]),
])
def test_bge_wire_preserves_original_query_and_documents(monkeypatch, wire, query, documents):
    monkeypatch.setenv("RERANKER_MODEL", "bge-reranker-v2-m3")
    result = HttpReranker().rerank(
        query, [{"id": str(i), "text": text} for i, text in enumerate(documents)], 1,
    )
    assert result[0].score == 0.8
    assert wire[0] == {
        "model": "bge-reranker-v2-m3", "query": query, "documents": documents,
    }




def test_http_debug_logging_does_not_dump_candidate_content(monkeypatch, wire, caplog):
    monkeypatch.setenv("RERANKER_MODEL", "Qwen3-Reranker-4B")
    caplog.set_level(logging.DEBUG)
    result = HttpReranker().rerank(
        QUERY, [{"id": str(i), "text": "sensitive-profile-input"} for i in range(100)], 10,
    )
    assert len(result) == 10
    assert "sensitive-profile-input" not in caplog.text


def test_prepub_defaults_to_info_but_explicit_debug_is_preserved(monkeypatch):
    from app.bootstrap.logging_setup import resolve_business_log_level

    monkeypatch.setenv("SERVER_ENV", "prepub")
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert resolve_business_log_level() == logging.INFO
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    assert resolve_business_log_level() == logging.DEBUG


def test_candidate_decision_rows_require_debug(caplog):
    from app.domain.services.retrieval_logging import log_rows

    logger = logging.getLogger("app.test.reranker_rows")
    caplog.set_level(logging.INFO, logger=logger.name)
    log_rows(logger, "candidate_decisions", ["id", "score"], [["bot:1", 0.8]])
    assert not caplog.records
    caplog.set_level(logging.DEBUG, logger=logger.name)
    log_rows(logger, "candidate_decisions", ["id", "score"], [["bot:1", 0.8]])
    assert len(caplog.records) == 1
    assert "bot:1" in caplog.records[0].getMessage()








def test_http_default_uses_qwen_without_model_configuration(monkeypatch, wire):
    monkeypatch.delenv("RERANKER_MODEL", raising=False)
    result = HttpReranker().rerank(QUERY, [{"id": "bot:1", "text": "名称: UCT20值班助手"}], 1)
    assert result[0].score == 0.8
    assert wire[0]["model"] == "Qwen3-Reranker-4B"
    assert wire[0]["query"].startswith(PREFIX)
    assert wire[0]["documents"] == ["名称: UCT20值班助手" + SUFFIX]




@pytest.mark.parametrize("configured,requested,enabled,expected", [
    (None, None, True, "Qwen3-Reranker-4B"),
    ("bge-reranker-v2-m3", None, True, "bge-reranker-v2-m3"),
    ("Qwen3-Reranker-4B", "bge-reranker-v2-m3", True, "bge-reranker-v2-m3"),
    ("bge-reranker-v2-m3", "Qwen3-Reranker-4B", True, "Qwen3-Reranker-4B"),
    (None, None, False, None),
])
def test_tc_model_default_preserves_overrides_and_disable(
    tc_runtime, monkeypatch, wire, configured, requested, enabled, expected,  # noqa: F811
):
    client, registry = tc_runtime
    if configured is None:
        monkeypatch.delenv("RERANKER_MODEL", raising=False)
    else:
        monkeypatch.setenv("RERANKER_MODEL", configured)
    registry.require("tc_search").reranker = HttpReranker()
    sync(client)
    response = client.post("/api/v1/recommend", json={
        "question": "MySQL", "enable_rerank": enabled, "reranker_model": requested,
    })
    assert response.status_code == 200
    assert response.json()["recommendations"]
    if enabled:
        assert wire[0]["model"] == expected
    else:
        assert wire == []


@pytest.mark.parametrize("failure", ["missing_key", "http_error", "malformed", "zero"])
def test_http_failure_propagates_but_zero_is_valid(monkeypatch, failure):
    import requests

    monkeypatch.setenv("RERANKER_BASE_URL", "https://reranker.example.test")
    monkeypatch.setenv("RERANKER_API_KEY", "" if failure == "missing_key" else "test-placeholder")

    class Response:
        def raise_for_status(self):
            if failure == "http_error":
                raise requests.HTTPError("controlled model outage")

        def json(self):
            return {"scores": [0.0]} if failure == "zero" else {}

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response())
    if failure == "zero":
        assert HttpReranker().rerank("query", [{"id": "worker", "text": "description"}], 1)[0].score == 0.0
    else:
        with pytest.raises(RuntimeError):
            HttpReranker().rerank("query", [{"id": "worker", "text": "description"}], 1)
