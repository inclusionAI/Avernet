"""K8s sandbox plugin — stub and real implementations."""

from secbaas.community.plugins.sandbox.k8s.real import (
    RealK8sSandbox,
    RealK8sSandboxPlugin,
)
from secbaas.community.plugins.sandbox.k8s.stub import (
    StubCommandResult,
    StubK8sSandbox,
    StubK8sSandboxPlugin,
)

__all__ = [
    "RealK8sSandbox",
    "RealK8sSandboxPlugin",
    "StubCommandResult",
    "StubK8sSandbox",
    "StubK8sSandboxPlugin",
]
