"""Unit tests for DefaultStreamConverter."""

import json

import pytest

from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.sse._default_converter import DefaultStreamConverter


@pytest.fixture
def converter():
    return DefaultStreamConverter()


def test_name():
    assert DefaultStreamConverter.name() == "default"


def test_convert_delta(converter):
    chunk = StreamChunk(type="delta", content="hello")
    event = converter.convert(chunk, run_id="run-1")
    assert event.event == "delta"
    data = json.loads(event.data)
    assert data["run_id"] == "run-1"
    assert data["content"] == "hello"
    assert data["seq"] == 1


def test_convert_final(converter):
    chunk = StreamChunk(type="final", content="result")
    event = converter.convert(chunk, run_id="run-1")
    assert event.event == "final"
    data = json.loads(event.data)
    assert data["content"] == "result"
    assert data["seq"] == 1


def test_convert_final_with_usage(converter):
    usage = {"input_tokens": 10, "output_tokens": 5}
    chunk = StreamChunk(type="final", content="result", usage=usage)
    event = converter.convert(chunk, run_id="run-1")
    data = json.loads(event.data)
    assert data["usage"] == usage


def test_convert_final_with_pending_usage(converter):
    usage_chunk = StreamChunk(type="usage", usage={"input_tokens": 10})
    converter.convert(usage_chunk, run_id="run-1")
    final_chunk = StreamChunk(type="final", content="result")
    event = converter.convert(final_chunk, run_id="run-1")
    data = json.loads(event.data)
    assert data["usage"] == {"input_tokens": 10}


def test_convert_error(converter):
    chunk = StreamChunk(type="error", content="something went wrong")
    event = converter.convert(chunk, run_id="run-1")
    assert event.event == "error"
    data = json.loads(event.data)
    assert data["error"]["code"] == "BOT_EXECUTION_ERROR"
    assert data["error"]["message"] == "something went wrong"


def test_convert_error_with_custom_code(converter):
    chunk = StreamChunk(
        type="error",
        content="bad request",
        metadata={"error_code": "INVALID_INPUT"},
    )
    event = converter.convert(chunk, run_id="run-1")
    data = json.loads(event.data)
    assert data["error"]["code"] == "INVALID_INPUT"


def test_convert_error_empty_content(converter):
    chunk = StreamChunk(type="error", content="")
    event = converter.convert(chunk, run_id="run-1")
    data = json.loads(event.data)
    assert data["error"]["message"] == "Unknown error"


def test_convert_usage(converter):
    chunk = StreamChunk(type="usage", usage={"input_tokens": 10})
    event = converter.convert(chunk, run_id="run-1")
    assert event.event == "delta"
    data = json.loads(event.data)
    assert data["content"] == ""
    assert data["seq"] == 1


def test_convert_usage_no_usage_dict(converter):
    chunk = StreamChunk(type="usage", usage=None)
    event = converter.convert(chunk, run_id="run-1")
    assert event.event == "delta"


def test_convert_unknown_type(converter):
    chunk = StreamChunk(type="unknown", content="passthrough")
    event = converter.convert(chunk, run_id="run-1")
    assert event.event == "delta"
    data = json.loads(event.data)
    assert data["content"] == "passthrough"


def test_convert_seq_increments(converter):
    c1 = converter.convert(StreamChunk(type="delta", content="a"), run_id="r")
    c2 = converter.convert(StreamChunk(type="delta", content="b"), run_id="r")
    d1 = json.loads(c1.data)
    d2 = json.loads(c2.data)
    assert d2["seq"] == d1["seq"] + 1
