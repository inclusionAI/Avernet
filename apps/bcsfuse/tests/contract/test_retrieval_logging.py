"""Request logging must correlate the real pipeline without exposing content."""

import io
import logging

import pytest
from app.infra.public.observability.trace_middleware import TraceIdMiddleware
from app.infra.trace_context import (
    bind_trace_id,
    get_trace_id,
    install_trace_record_factory,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.contract.test_tc_catalog import runtime as tc_runtime  # noqa: F401
from tests.contract.test_tc_catalog import sync


@pytest.fixture(autouse=True)
def restore_logging_factory():
    previous = logging.getLogRecordFactory()
    yield
    logging.setLogRecordFactory(previous)


def test_composed_trace_reaches_formatted_child_logs_without_configure_logging():
    # Model the internal runner: logging is configured externally, not by main.py.
    logging.setLogRecordFactory(logging.LogRecord)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("[%(traceid)s] %(message)s"))

    class ExternalTraceFallback(logging.Filter):
        def filter(self, record):
            if not hasattr(record, "traceid"):
                record.traceid = "-"
            return True

    handler.addFilter(ExternalTraceFallback())
    child = logging.getLogger("app.application.services.trace_probe")
    previous_level = child.level
    child.setLevel(logging.INFO)
    child.addHandler(handler)
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)

    @app.get("/probe")
    async def probe():
        child.info("matching candidates")
        return {"trace": get_trace_id()}

    try:
        with TestClient(app) as client:
            response = client.get("/probe", headers={"X-Trace-ID": "request-probe"})
        assert response.json() == {"trace": "request-probe"}
        assert "[request-probe] matching candidates" in stream.getvalue()
        child.info("outside request")
        assert "[-] outside request" in stream.getvalue()
    finally:
        child.removeHandler(handler)
        child.setLevel(previous_level)


def test_trace_install_preserves_existing_factory_and_is_idempotent():
    def external_factory(*args, **kwargs):
        record = logging.LogRecord(*args, **kwargs)
        record.external_marker = "preserved"
        return record

    logging.setLogRecordFactory(external_factory)
    install_trace_record_factory()
    first = logging.getLogRecordFactory()
    install_trace_record_factory()
    assert logging.getLogRecordFactory() is first
    with bind_trace_id("factory-probe"):
        record = first("probe", logging.INFO, __file__, 1, "message", (), None)
    assert record.external_marker == "preserved"
    assert record.traceid == "factory-probe"


def test_trace_factory_preserves_host_trace_outside_requests():
    def host_factory(*args, **kwargs):
        record = logging.LogRecord(*args, **kwargs)
        record.traceid = "host-background-trace"
        return record

    logging.setLogRecordFactory(host_factory)
    install_trace_record_factory()
    with bind_trace_id(""):
        record = logging.getLogRecordFactory()("probe", logging.INFO, __file__, 1, "message", (), None)
    assert record.traceid == "host-background-trace"














def test_tc_recommend_logs_do_not_expose_query_or_description(tc_runtime, caplog):  # noqa: F811
    client, _registry = tc_runtime
    sync(client, description="private-summary-sentinel")
    caplog.set_level(logging.DEBUG)
    response = client.post("/api/v1/recommend", json={"question": "private-query-sentinel"})
    assert response.status_code == 200
    assert response.json()["recommendations"][0]["worker_id"] == "bot:owner"
    assert "private-query-sentinel" not in caplog.text
    assert "private-summary-sentinel" not in caplog.text
