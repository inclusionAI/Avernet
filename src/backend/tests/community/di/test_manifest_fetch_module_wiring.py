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


def test_the_entry_fetcher_reads_the_oss_road_through_the_one_object_store(
    test_injector,
) -> None:
    """The ``oss`` road has one implementation and one instance.

    ``DeclaredSourceResolver`` is the funnel every fetch-consuming category
    shares, and it takes the object store by constructor — a plain core class
    since the plugin seam went — then hands it to the one road that reads
    through it. Asserted on the wired
    instance rather than the module source: what matters is that the singleton
    the injector binds is the one the funnel reads through, so a test that
    substitutes the store on the injector substitutes it for every apply.
    """
    from agentclaw.community.core.bot_config_manifest.apply.source_resolver import (
        DeclaredSourceResolver,
    )
    from agentclaw.community.core.bot_config_manifest.fetch.object_store import (
        AliyunObjectStore,
    )
    from agentclaw.community.core.bot_config_manifest.support_matrix import (
        SourceKind,
    )

    store = test_injector.get(AliyunObjectStore)

    assert isinstance(store, AliyunObjectStore)
    resolver = test_injector.get(DeclaredSourceResolver)
    assert resolver._fetchers[SourceKind.OSS]._objects is store
