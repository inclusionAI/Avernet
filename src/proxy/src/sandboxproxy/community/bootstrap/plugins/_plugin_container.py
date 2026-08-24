"""Plugin DI container — selects resolver/relay_client implementations."""

from dependency_injector import containers, providers

from sandboxproxy.community.plugins.relay_client.baas import BaasRelayClient
from sandboxproxy.community.plugins.relay_client.stub import StubRelayClient
from sandboxproxy.community.plugins.resolver.prefix import PrefixTargetResolver
from sandboxproxy.community.plugins.resolver.stub import StubTargetResolver


class PluginContainer(containers.DeclarativeContainer):
    config = providers.Configuration()

    resolver = providers.Selector(
        config.plugins.resolver,
        prefix=providers.Singleton(PrefixTargetResolver, config=config.user_config),
        stub=providers.Singleton(StubTargetResolver),
    )

    relay_client = providers.Selector(
        config.plugins.relay_client,
        baas=providers.Singleton(
            BaasRelayClient,
            baas_host=config.user_config.baas.host,
            instance=config.instance,
            worker_pid=config.worker_pid,
            socket_path=config.socket_path,
        ),
        stub=providers.Singleton(StubRelayClient),
    )
