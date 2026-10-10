"""Credential-safe fields for Caller external boundary events."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from urllib.parse import quote, quote_plus

_SENSITIVE = (
    "token",
    "authorization",
    "cookie",
    "password",
    "secret",
    "key",
    "credential",
    "session",
)


def redact_boundary(
    value: object, *, session_response: bool = False, secrets: tuple[str, ...] = ()
) -> object:
    """Recursively preserve business fields while hiding reusable credentials."""
    if session_response:
        # COSEC: session titles/messages may repeat another returned session ID.
        def collect_session_ids(item: object) -> list[str]:
            if isinstance(item, Mapping):
                own = [
                    entry
                    for name, entry in item.items()
                    if name in {"id", "session_key"} and isinstance(entry, str)
                ]
                return own + [
                    secret
                    for entry in item.values()
                    for secret in collect_session_ids(entry)
                ]
            if isinstance(item, (list, tuple)):
                return [
                    secret for entry in item for secret in collect_session_ids(entry)
                ]
            return []

        secrets = tuple(dict.fromkeys((*secrets, *collect_session_ids(value))))
    # COSEC: rule values inherit sensitivity from header_name, not from "value".
    if isinstance(value, Mapping):
        sensitive_header = any(
            part in str(value.get("header_name", "")).lower() for part in _SENSITIVE
        )
        return {
            str(name): "[REDACTED]"
            if any(part in str(name).lower() for part in _SENSITIVE)
            or (name == "value" and sensitive_header)
            or (session_response and name == "id")
            else redact_boundary(
                item, session_response=session_response, secrets=secrets
            )
            for name, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            redact_boundary(item, session_response=session_response, secrets=secrets)
            for item in value
        ]
    if isinstance(value, bytes) or (isinstance(value, str) and len(value) > 8192):
        raw = value if isinstance(value, bytes) else value.encode()
        return {
            "type": type(value).__name__,
            "length": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    if isinstance(value, str):
        # COSEC: redact known credentials even inside free-form error messages.
        for secret in secrets:
            if secret:
                for representation in {
                    secret,
                    quote(secret, safe=""),
                    quote_plus(secret),
                }:
                    value = value.replace(representation, "[REDACTED]")
    return value


def install_httpx_credential_filter() -> None:
    """Sanitize HTTPX URL query credentials without changing logging levels."""
    import logging
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    class CredentialQueryFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            # COSEC: HTTPX logs URLs independently of application boundary events.
            if isinstance(record.args, tuple):
                arguments = []
                for argument in record.args:
                    raw = str(argument)
                    if raw.startswith(("http://", "https://")):
                        parsed = urlsplit(raw)
                        query = parse_qsl(parsed.query, keep_blank_values=True)
                        if any(
                            any(part in name.lower() for part in _SENSITIVE)
                            for name, _ in query
                        ):
                            safe_query = [
                                (
                                    name,
                                    "[REDACTED]"
                                    if any(part in name.lower() for part in _SENSITIVE)
                                    else value,
                                )
                                for name, value in query
                            ]
                            argument = urlunsplit(
                                (
                                    parsed.scheme,
                                    parsed.netloc,
                                    parsed.path,
                                    urlencode(safe_query),
                                    parsed.fragment,
                                )
                            )
                    arguments.append(argument)
                record.args = tuple(arguments)
            return True

    logger = logging.getLogger("httpx")
    if not any(
        getattr(item, "caller_credential_filter", False) for item in logger.filters
    ):
        filter_instance = CredentialQueryFilter()
        filter_instance.caller_credential_filter = True
        logger.addFilter(filter_instance)
