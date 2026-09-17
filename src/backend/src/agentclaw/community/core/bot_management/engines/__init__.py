"""Engine-specific bot provisioning extension point."""

from .provisioning import (
    BotProvisioningContext,
    EngineProvisioningStrategy,
    HostedWorkspaceNotEligibleError,
    HostedWorkspaceProvisioningError,
)
from .registry import (
    EngineProvisioningRegistry,
    get_engine_provisioning_registry,
    resolve_outbound_rule_envelope,
    resolve_provisioning,
)

__all__ = [
    "BotProvisioningContext",
    "EngineProvisioningRegistry",
    "EngineProvisioningStrategy",
    "HostedWorkspaceNotEligibleError",
    "HostedWorkspaceProvisioningError",
    "get_engine_provisioning_registry",
    "resolve_provisioning",
    "resolve_outbound_rule_envelope",
]
