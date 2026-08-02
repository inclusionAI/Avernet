"""Keep credentials carried in query strings out of the server's own logs.

A WebSocket client cannot set request headers — the browser API offers no way —
so socket credentials travel as query parameters. The engine socket's
``x-proxypass-token`` is one such credential, and uvicorn logs the request
target *including its query string* on every accepted or refused handshake::

    INFO: 127.0.0.1:43078 - "WebSocket /openapi/v1/engine/T/api/ws?x-proxypass-token=eyJ…" [accepted]

That line is emitted through ``uvicorn.error`` and is not governed by the
``access_log`` switch, so a deployment cannot turn it off without losing the
server's error log too. Left alone, every accepted socket writes a live
credential into the log, where it stays readable for as long as it is valid and
by anyone who can read logs.

This filter rewrites the value, not the parameter, so a log line still shows
*which* credential was presented and to what path — enough to trace a request —
without being a copy of the credential itself.

**Scope, stated honestly:** this covers the log this process writes. A
credential in a URL is also visible to every other hop that records request
lines — a proxy, a load balancer, the upstream itself — and none of those are
reachable from here. The durable fix is for the credential not to be in the URL,
which belongs to the contract that publishes the address, not to this relay.
"""

from __future__ import annotations

import logging
import re

#: Substrings that mark a query parameter as carrying a secret. Matched against
#: the parameter *name*, case-insensitively, so ``x-proxypass-token`` is caught
#: by ``token`` without this module having to know the engine proxy's spelling.
#: Deliberately excludes a bare ``key``, which would redact ``monkey=`` and
#: every other innocent parameter that happens to contain it.
_CREDENTIAL_HINTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "authorization",
    "signature",
    "api_key",
    "apikey",
    "access_key",
)

_REDACTED = "<redacted>"

#: A ``?``/``&``-introduced parameter whose name contains a hint, up to the next
#: separator. The value stops at ``&`` or whitespace so the rest of a log line —
#: uvicorn's trailing ``" [accepted]`` — survives intact.
_CREDENTIAL_PARAM = re.compile(
    r"([?&][^=&\s]*(?:" + "|".join(_CREDENTIAL_HINTS) + r")[^=&\s]*=)[^&\s\"']*",
    re.IGNORECASE,
)

#: The loggers uvicorn writes request lines through. ``uvicorn.error`` carries
#: the WebSocket lines despite its name; ``uvicorn.access`` carries the HTTP
#: ones, which would expose a credential just as readily on a forwarded request.
_TARGET_LOGGERS = ("uvicorn.error", "uvicorn.access")


def redact_credentials(text: str) -> str:
    """Replace credential-bearing query values in *text*."""
    return _CREDENTIAL_PARAM.sub(rf"\1{_REDACTED}", text)


class CredentialRedactingFilter(logging.Filter):
    """Redacts credential query values before a record is formatted.

    Rewrites the record rather than dropping it: an accepted socket should still
    appear in the log, just without its credential. ``args`` is where uvicorn
    puts the request target, and ``msg`` is checked too so a caller that
    pre-formatted its message is covered as well.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_credentials(arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_credentials(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        if isinstance(record.msg, str):
            record.msg = redact_credentials(record.msg)
        return True


def install_credential_redaction() -> None:
    """Attach the filter to the loggers that record request targets.

    Idempotent, because the composition root may build the app more than once
    in a test process and a second filter would only redact what the first
    already did.
    """
    for name in _TARGET_LOGGERS:
        logger = logging.getLogger(name)
        if not any(
            isinstance(existing, CredentialRedactingFilter)
            for existing in logger.filters
        ):
            logger.addFilter(CredentialRedactingFilter())
