"""Contract tests for the logging configuration boundary."""

import logging
import time
from unittest.mock import patch


from app.infra.logging.logging_config import configure_logging


def test_json_log_format_is_a_supported_structured_formatter():
    with patch.dict("os.environ", {"LOG_FORMAT": "json"}, clear=False):
        configure_logging()

    root = logging.getLogger()
    assert root.handlers, "configure_logging must install at least one handler"
    formatter = root.handlers[0].formatter
    assert formatter is not None
    record = logging.LogRecord(
        name="bcsfuse.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="structured ready",
        args=(),
        exc_info=None,
    )
    import json

    rendered = formatter.format(record)
    structured = json.loads(rendered)
    assert structured["message"] == "structured ready"
    assert structured["level"] == "INFO"


def test_percent_log_format_still_uses_standard_formatter():
    legacy_format = "%(asctime)s %(levelname)s %(message)s"
    with patch.dict("os.environ", {"LOG_FORMAT": legacy_format}, clear=False):
        configure_logging()

    formatter = logging.getLogger().handlers[0].formatter
    assert isinstance(formatter, logging.Formatter)
    record = logging.LogRecord(
        name="bcsfuse.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="legacy ready",
        args=(),
        exc_info=None,
    )
    assert time.strftime("%Y-%m-%d") in formatter.format(record)
    assert "INFO" in formatter.format(record)
