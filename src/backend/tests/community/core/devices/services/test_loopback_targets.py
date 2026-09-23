"""Loopback-target predicate shared by singlebox layers."""

import pytest

from agentclaw.community.core.devices.services.loopback_targets import (
    is_loopback_target,
)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        # host:port and bare-host loopback forms
        ("localhost:20010", True),
        ("127.0.0.1:20010", True),
        ("localhost", True),
        # IPv6 loopback: bracketed-with-port and unbracketed-with-port
        ("[::1]:20010", True),
        ("::1:20010", True),
        # Non-loopback: remote engines, other loopback /8, pod IPs,
        # ARCA routing targets.
        ("engine.example.test:20010", False),
        ("127.0.0.2:20010", False),
        ("10.42.0.1:20003", False),
        ("ARCA_xxx@alt:20003", False),
        # Scheme-ful and malformed inputs never classify as loopback.
        ("http://127.0.0.1:1234", False),
        ("[::1", False),
        ("", False),
    ],
)
def test_is_loopback_target(target: str, expected: bool) -> None:
    assert is_loopback_target(target) is expected
