"""Device plugin SPI — pluggable device management backends."""

from __future__ import annotations

from typing import TYPE_CHECKING

from secbaas.community.spi.sandbox.arca import ArcaSandbox, ArcaSandboxPlugin
from secbaas.community.spi.sandbox.desktop import DesktopSandbox, DesktopSandboxPlugin
from secbaas.community.spi.sandbox.docker import DockerSandbox, DockerSandboxPlugin
from secbaas.community.spi.sandbox.k8s import K8sSandbox, K8sSandboxPlugin
from secbaas.community.spi.sandbox.poolab import PoolabSandboxPlugin

if TYPE_CHECKING:
    from collections.abc import Callable

    from secbaas.community.api.device_manage import (
        ArcaCredentials,
        K8sCredentials,
        PoolabCredentials,
    )
    from secbaas.community.spi.bot.teclaw import TeClawBotPlugin
    from secbaas.community.spi.sandbox.k8s import K8sSandboxPlugin


class PaasSandboxPlugins:
    """Device plugin registry for PaasServiceFactory.

    Consolidates all device-type plugin dependencies so the factory
    constructor doesn't grow with each new device implementation.
    """

    def __init__(
        self,
        arca_sandbox_plugin_factory: Callable[[ArcaCredentials], ArcaSandboxPlugin],
        desktop_sandbox_plugin: DesktopSandboxPlugin,
        teclaw_bot_plugin_factory: Callable[[str, float], TeClawBotPlugin],
        k8s_sandbox_plugin_factory: (
            Callable[[K8sCredentials], K8sSandboxPlugin] | None
        ) = None,
        docker_sandbox_plugin: DockerSandboxPlugin | None = None,
        poolab_sandbox_plugin_factory: (
            Callable[[PoolabCredentials], PoolabSandboxPlugin] | None
        ) = None,
    ) -> None:
        self.arca_sandbox_plugin_factory = arca_sandbox_plugin_factory
        self.desktop_sandbox_plugin = desktop_sandbox_plugin
        self.poolab_sandbox_plugin_factory = poolab_sandbox_plugin_factory
        self.teclaw_bot_plugin_factory = teclaw_bot_plugin_factory
        self.k8s_sandbox_plugin_factory = k8s_sandbox_plugin_factory
        self.docker_sandbox_plugin = docker_sandbox_plugin


__all__ = [
    "ArcaSandboxPlugin",
    "ArcaSandbox",
    "DesktopSandboxPlugin",
    "DesktopSandbox",
    "DockerSandbox",
    "DockerSandboxPlugin",
    "K8sSandbox",
    "K8sSandboxPlugin",
    "PaasSandboxPlugins",
    "PoolabSandboxPlugin",
]
