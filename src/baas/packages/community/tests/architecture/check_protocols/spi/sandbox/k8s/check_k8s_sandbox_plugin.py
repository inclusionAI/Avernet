from unittest.mock import MagicMock

from secbaas.api.device_manage import K8sCredentials
from secbaas.plugins.sandbox.k8s import RealK8sSandboxPlugin
from secbaas.spi.sandbox.k8s import (
    K8sClientManager,
)
from secbaas.spi.sandbox.k8s import (
    K8sSandboxPlugin as K8sSandboxPluginProtocol,
)

# Assign value, will trigger mypy type check
_k8s_sandbox_plugin: K8sSandboxPluginProtocol = RealK8sSandboxPlugin(
    credentials=MagicMock(spec=K8sCredentials),
    client_manager=MagicMock(spec=K8sClientManager),
)
