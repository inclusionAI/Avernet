"""The server's own logs must not become a copy of the credentials they carry.

A WebSocket client cannot set request headers, so a socket credential travels in
the query string — and uvicorn logs the request target *with* its query on every
handshake, through ``uvicorn.error`` rather than the switchable access log.
"""

from __future__ import annotations

import logging

import pytest

from gateway.community.adapters.web._log_redaction import (
    CredentialRedactingFilter,
    install_credential_redaction,
    redact_credentials,
)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        # The one this change actually introduces.
        (
            "/openapi/v1/bots/messages/T/api/ws?x-proxypass-token=eyJhbGciOi.J9.sig",
            "/openapi/v1/bots/messages/T/api/ws?x-proxypass-token=<redacted>",
        ),
        ("/x?a=1&token=abc&b=2", "/x?a=1&token=<redacted>&b=2"),
        ("/x?password=p&secret=s", "/x?password=<redacted>&secret=<redacted>"),
        ("/x?api_key=k", "/x?api_key=<redacted>"),
        ("/x?Authorization=bearer", "/x?Authorization=<redacted>"),
    ],
)
def test_a_credential_query_value_is_replaced(target: str, expected: str) -> None:
    assert redact_credentials(target) == expected


@pytest.mark.parametrize(
    "target",
    [
        "/openapi/v1/bots/messages/T/api/ws",  # no query at all
        "/x?ordinary=value",
        "/x?monkey=fine",  # contains 'key', and is not a credential
        "/x?page=2&limit=10",
    ],
)
def test_an_ordinary_query_value_is_left_alone(target: str) -> None:
    """Over-redaction costs debuggability, so the hint list excludes bare 'key'."""
    assert redact_credentials(target) == target


def test_the_parameter_name_and_path_survive() -> None:
    """Redacting the value, not the parameter, keeps the line traceable.

    A log that says *which* credential was presented and to what path is still
    useful for tracing a request; one that drops the query entirely is not.
    """
    redacted = redact_credentials(
        "/openapi/v1/bots/messages/T/ws?x-proxypass-token=live"
    )
    assert "x-proxypass-token" in redacted
    assert "/openapi/v1/bots/messages/T/ws" in redacted
    assert "live" not in redacted


def test_the_filter_rewrites_the_record_uvicorn_actually_emits() -> None:
    """Uvicorn passes the target as a positional arg, not in the message."""
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "WebSocket %s" [accepted]',
        args=("127.0.0.1:1", "/openapi/v1/bots/messages/T/ws?x-proxypass-token=live"),
        exc_info=None,
    )
    assert CredentialRedactingFilter().filter(record) is True
    formatted = record.getMessage()
    assert "live" not in formatted
    assert "x-proxypass-token=<redacted>" in formatted
    assert formatted.endswith('" [accepted]')  # the rest of the line is intact


def test_the_filter_keeps_the_record() -> None:
    """Redacted, not dropped: an accepted socket must still appear in the log."""
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="/x?token=live",
        args=None,
        exc_info=None,
    )
    assert CredentialRedactingFilter().filter(record) is True
    assert record.getMessage() == "/x?token=<redacted>"


def test_installing_twice_adds_one_filter() -> None:
    """The composition root may build the app more than once in a test process."""
    logger = logging.getLogger("uvicorn.error")
    for existing in list(logger.filters):
        if isinstance(existing, CredentialRedactingFilter):
            logger.removeFilter(existing)
    install_credential_redaction()
    install_credential_redaction()
    installed = [f for f in logger.filters if isinstance(f, CredentialRedactingFilter)]
    assert len(installed) == 1
