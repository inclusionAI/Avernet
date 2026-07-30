"""The environment contract for gateway principal verification.

The env var names are shared with the gateway's signer — one vocabulary for one
shared secret — so these tests are the record of what a deployment must set, and
of the deliberate choice to ship no fallback key.
"""

from __future__ import annotations

import pytest

from agentclaw.community.utils.gateway_principal_config import (
    get_principal_verifier_config,
    reset_principal_verifier_config_cache,
)


@pytest.fixture(autouse=True)
def clear_cache():
    reset_principal_verifier_config_cache()
    yield
    reset_principal_verifier_config_cache()


def test_reads_the_shared_key_and_endpoint_identity(monkeypatch):
    monkeypatch.setenv("AVERNET_PRINCIPAL_SIGNING_KEY", "the-shared-secret")
    monkeypatch.setenv("AVERNET_PRINCIPAL_AUDIENCE", "backend")
    monkeypatch.setenv("AVERNET_PRINCIPAL_ISSUER", "gateway")

    config = get_principal_verifier_config()

    assert config.signing_key == "the-shared-secret"
    assert config.audience == "backend"
    assert config.issuer == "gateway"


def test_audience_and_issuer_default_to_the_gateway_contract(monkeypatch):
    """``backend`` is our name in the gateway's ``upstreams.yaml``; ``gateway`` its ``iss``."""
    monkeypatch.setenv("AVERNET_PRINCIPAL_SIGNING_KEY", "k")
    monkeypatch.delenv("AVERNET_PRINCIPAL_AUDIENCE", raising=False)
    monkeypatch.delenv("AVERNET_PRINCIPAL_ISSUER", raising=False)

    config = get_principal_verifier_config()

    assert config.audience == "backend"
    assert config.issuer == "gateway"


def test_no_fallback_signing_key_is_invented(monkeypatch):
    """An unconfigured deployment must deny, not fall back to a shipped secret.

    The gateway's ``bare`` signer does keep a dev fallback; we deliberately do
    not mirror it. A committed shared secret is a committed credential, and here
    "no key" fails safe.
    """
    monkeypatch.delenv("AVERNET_PRINCIPAL_SIGNING_KEY", raising=False)

    assert get_principal_verifier_config().signing_key == ""


def test_whitespace_only_key_counts_as_unset(monkeypatch):
    """A key that is accidentally whitespace must not look configured."""
    monkeypatch.setenv("AVERNET_PRINCIPAL_SIGNING_KEY", "   ")

    assert get_principal_verifier_config().signing_key == ""


def test_config_is_read_once_per_process(monkeypatch):
    """Deployment config, not per-request state — the hot path re-reads nothing."""
    monkeypatch.setenv("AVERNET_PRINCIPAL_SIGNING_KEY", "first")
    first = get_principal_verifier_config()

    monkeypatch.setenv("AVERNET_PRINCIPAL_SIGNING_KEY", "second")

    assert get_principal_verifier_config() is first
