"""Engine-specific bot provisioning extension point."""

from .provisioning import (
    EngineExtraProperties,
    ExtraPropertiesContributor,
    BotProvisioningContext,
    EngineProvisioningStrategy,
)
from .registry import (
    EngineProvisioningRegistry,
    build_extra_properties_fail_open,
    build_engine_extra_properties_fail_open,
    get_engine_provisioning_registry,
    resolve_provisioning,
)

__all__ = [
    "EngineExtraProperties",
    "ExtraPropertiesContributor",
    "BotProvisioningContext",
    "EngineProvisioningRegistry",
    "EngineProvisioningStrategy",
    "build_extra_properties_fail_open",
    "build_engine_extra_properties_fail_open",
    "get_engine_provisioning_registry",
    "resolve_provisioning",
]
