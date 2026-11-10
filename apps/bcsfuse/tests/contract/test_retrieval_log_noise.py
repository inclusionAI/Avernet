"""INFO describes retrieval decisions, not dependency construction internals."""

import logging

import pytest

from app.bootstrap.logging_setup import configure_business_logging


@pytest.fixture(autouse=True)
def restore_logger_state():
    previous_factory = logging.getLogRecordFactory()
    previous_levels = {
        name: logger.level for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    yield
    for name, logger in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(logger, logging.Logger):
            logger.setLevel(previous_levels.get(name, logging.NOTSET))
    logging.setLogRecordFactory(previous_factory)


@pytest.mark.parametrize("name", [
    "app.adapters.http.dependencies.fusion_dependencies",
    "app.adapters.http.dependencies.worker_dependencies",
    "app.infra.public.vectorstores.qdrant_local_vector_store",
    "app.infra.embedding.providers.real_provider",
    "search_core.providers.qdrant",
    "search_core.providers.embedding",
])
def test_infrastructure_detail_requires_debug_but_warnings_always_visible(caplog, name):
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger(name)
    configure_business_logging(logging.INFO)
    logger.info("initialization-detail")
    logger.warning("provider-degraded")
    assert "initialization-detail" not in caplog.text
    assert "provider-degraded" in caplog.text
    caplog.clear()
    configure_business_logging(logging.DEBUG)
    logger.info("initialization-detail")
    logger.debug("diagnostic-detail")
    assert "initialization-detail" in caplog.text
    assert "diagnostic-detail" in caplog.text
    caplog.clear()
    configure_business_logging(logging.INFO)
    logger.info("initialization-detail")
    assert not caplog.records


def test_http_request_chatter_is_hidden_without_losing_warnings(caplog):
    caplog.set_level(logging.DEBUG)
    configure_business_logging(logging.DEBUG)
    logging.getLogger("httpx").info("request-url-detail")
    logging.getLogger("httpx").warning("request-failed")
    assert "request-url-detail" not in caplog.text
    assert "request-failed" in caplog.text
