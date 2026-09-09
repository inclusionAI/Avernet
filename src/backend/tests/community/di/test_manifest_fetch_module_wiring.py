"""Wiring regression tests for the manifest fetch composition root."""

from __future__ import annotations


def test_credential_service_receives_the_deployment_transport_allowlist(
    test_injector,
) -> None:
    """The endpoint guard's escape hatch has to be reachable from config.

    ``SourceCredentialService`` refuses an ``oss_aksk`` endpoint whose host
    resolves to a private address — which is exactly what an internal object
    store endpoint does. Its ``endpoint_allow_hosts`` parameter is how a
    deployment declares such a host, and it takes the SAME value the guarded
    fetcher reads, from the same ``user_config.bot_config_manifest`` block, so
    the fetch road and the credential surface cannot disagree about which
    internal hosts exist.

    That parameter defaults to ``()``, so a composition root that simply omits
    it type-checks, resolves, and passes every unit test of the service — while
    leaving a legitimately internal endpoint unregistrable by ANY
    configuration. Asserted against the real injector rather than the source
    text: what matters is the value that arrives, not that a keyword appears.
    """
    from agentclaw.community.api.source_credential_service import (
        SourceCredentialServiceProtocol,
    )
    from agentclaw.community.di import config as cfg

    declared = ("store.corp.internal", "objects.corp.internal")
    test_injector.binder.bind(
        cfg.BotConfigManifestConfig,
        to=cfg.BotConfigManifestConfig(fetch_transport_allowlist=declared),
    )

    service = test_injector.get(SourceCredentialServiceProtocol)

    assert tuple(service._endpoint_allow_hosts) == declared
