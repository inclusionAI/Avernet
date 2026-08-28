"""Contract tests for the neutral renewal digest module (``_renewal_digest.py``).

The ``arca-renew-digest`` CSV line is a monitoring contract: the logger name,
field order and separators must stay byte-identical to the legacy
``_sandbox_device_router`` emission, otherwise the monitor pipeline can no
longer parse the renewal success/failure stream. These tests pin that
contract together with the re-export compatibility layer that keeps legacy
imports working until Phase 3 removes the router class.
"""

import logging

import pytest

from secbaas.community.core.utils import _renewal_digest
from secbaas.community.core.utils._renewal_digest import (
    RENEWAL_DIGEST_LOGGER,
    log_renew_digest,
    ttl_for_digest,
)

_MODULE_LOGGER_NAME = _renewal_digest.__name__

_EXPECTED_FULL_LINE = (
    "ttl_renew_digest,u-001,renew,42,baas,ARCA-SANDBOX-xxx@10,success,"
    "2026-05-27-21:15:05,2026-05-27-23:15:05"
)


def test_logger_name_is_the_monitor_collection_contract():
    assert RENEWAL_DIGEST_LOGGER.name == "arca-renew-digest"


@pytest.mark.parametrize(
    ("ttl", "expected"),
    [
        (None, "-"),
        ("", "-"),
        ("2026-05-27 21:15:05", "2026-05-27-21:15:05"),
    ],
)
def test_ttl_for_digest_normalization(ttl, expected):
    assert ttl_for_digest(ttl) == expected


def test_digest_line_format_contract(caplog):
    caplog.set_level(logging.INFO, logger="arca-renew-digest")
    log_renew_digest(
        run_uuid="u-001",
        table_id=42,
        table_type="baas",
        arca_device_id="ARCA-SANDBOX-xxx@10",
        result="success",
        ttl_before="2026-05-27 21:15:05",
        ttl_after="2026-05-27 23:15:05",
    )
    # Exactly one record on the digest logger and it is byte-identical to the
    # legacy CSV contract (spaces normalized to hyphens inside TTL fields).
    assert len(caplog.messages) == 1
    assert caplog.messages[0] == _EXPECTED_FULL_LINE
    record = caplog.records[0]
    assert record.name == "arca-renew-digest"
    assert record.levelno == logging.INFO
    # Exactly 9 comma-separated fields, no stray spaces anywhere in the line.
    assert len(caplog.messages[0].split(",")) == 9
    assert " " not in caplog.messages[0]


def test_digest_line_uses_dash_placeholder_for_missing_ttl(caplog):
    caplog.set_level(logging.INFO, logger="arca-renew-digest")
    log_renew_digest(
        run_uuid="u-002",
        table_id=42,
        table_type="baas",
        arca_device_id="ARCA-SANDBOX-xxx@10",
        result="success",
        ttl_before=None,
        ttl_after="",
    )
    assert len(caplog.messages) == 1
    assert caplog.messages[0] == (
        "ttl_renew_digest,u-002,renew,42,baas,ARCA-SANDBOX-xxx@10,success,-,-"
    )


def test_digest_log_is_best_effort_on_logger_failure(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="arca-renew-digest")
    caplog.set_level(logging.WARNING, logger=_MODULE_LOGGER_NAME)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated logging backend failure")

    monkeypatch.setattr(_renewal_digest.RENEWAL_DIGEST_LOGGER, "info", boom)

    # Must not propagate the logging failure to the renewal flow.
    log_renew_digest(
        run_uuid="u-003",
        table_id=1,
        table_type="baas",
        arca_device_id="ARCA-SANDBOX-xxx@10",
        result="success",
        ttl_before=None,
        ttl_after=None,
    )

    warnings = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and "ttl_renew_digest" in r.getMessage()
    ]
    assert warnings, "expected a defensive warning for the failed digest log"
    assert all(r.name == _MODULE_LOGGER_NAME for r in warnings)


def test_router_reexport_keeps_legacy_import_compatible():
    from secbaas.community.core.service.health_check.sandbox._sandbox_device_router import (
        _log_renew_digest,
        _ttl_for_digest,
        arca_renew_digest_logger,
    )

    # Legacy names resolve to the neutral-module objects — same logger, same
    # functions, so pre-existing importers keep working byte-identically.
    assert _log_renew_digest is log_renew_digest
    assert _ttl_for_digest is ttl_for_digest
    assert arca_renew_digest_logger is RENEWAL_DIGEST_LOGGER
    assert arca_renew_digest_logger.name == "arca-renew-digest"
    assert _ttl_for_digest("2026-05-27 21:15:05") == "2026-05-27-21:15:05"
