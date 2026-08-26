"""Application DI container — composition root for the sandbox-proxy."""

from __future__ import annotations

import logging
from typing import Any

from dependency_injector import containers, providers

from sandboxproxy.community.bootstrap.plugins import PluginContainer
from sandboxproxy.community.core.authn import JwtVerifier
from sandboxproxy.community.core.forwarding import ForwardingProxy
from sandboxproxy.community.core.relay import RelayServer
from sandboxproxy.community.spi import RelayApiClient


class ApplicationContainer(containers.DeclarativeContainer):
    config = providers.Configuration()

    plugins = providers.Container(PluginContainer, config=config)

    relay_client: providers.Dependency[Any] = providers.Dependency()
    target_resolver: providers.Dependency[Any] = providers.Dependency()
    jwt_verifier: providers.Dependency[Any] = providers.Dependency()
    forwarding: providers.Dependency[Any] = providers.Dependency()
    relay_server: providers.Dependency[Any] = providers.Dependency()


def build_relay_server(
    relay_client: RelayApiClient, wait_timeout: float
) -> RelayServer:
    return RelayServer(relay_client, wait_timeout=wait_timeout)


def initialize_services(container: ApplicationContainer) -> None:
    logger = logging.getLogger("bootstrap")
    config = container.config()

    user_config = config["user_config"]

    instance = config.get("instance", "") or ""
    if not instance:
        from sandboxproxy.community.api.identity import resolve_instance_id

        instance = resolve_instance_id()

    logger.info("Wiring plugin container")
    plugin_container = container.plugins()

    logger.info("Resolving target resolver")
    resolver = plugin_container.resolver()
    container.target_resolver.override(providers.Object(resolver))

    logger.info("Resolving relay client")
    relay_client = plugin_container.relay_client()
    container.relay_client.override(providers.Object(relay_client))

    logger.info("Building JWT verifier")
    jwt_secret = user_config.get("jwt", {}).get("secret", "")
    container.jwt_verifier.override(
        providers.Singleton(JwtVerifier.from_secret, secret=jwt_secret)
    )

    logger.info("Building forwarding proxy")
    container.forwarding.override(providers.Singleton(ForwardingProxy))

    logger.info("Building relay server")
    wait_timeout = 30.0
    container.relay_server.override(
        providers.Singleton(
            build_relay_server,
            relay_client=relay_client,
            wait_timeout=wait_timeout,
        )
    )

    container.wire(packages=["sandboxproxy.community.adapters.web"])

    logger.info("All components initialised successfully")
    logger.info("Instance identity: %s", instance)


def shutdown_services(container: ApplicationContainer) -> None:
    logging.getLogger("bootstrap").info("All components shut down")
