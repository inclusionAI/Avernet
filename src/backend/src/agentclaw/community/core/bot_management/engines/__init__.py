"""Engine-specific bot provisioning extension point."""

from .provisioning import (
    EngineExtraProperties,
    ExtraPropertiesContributor,
    BotProvisioningContext,
    EngineProvisioningStrategy,
)
from .registry import (
    EngineProvisioningRegistry,
    build_extra_envs_fail_open,
    build_extra_envs_from_bot,
    build_extra_properties_fail_open,
    build_extra_properties_from_bot,
    build_engine_extra_properties_fail_open,
    extract_runtime_token_fail_open,
    get_engine_provisioning_registry,
    resolve_provisioning,
    should_encrypt_template_token_fail_open,
)

__all__ = [
    "EngineExtraProperties",
    "ExtraPropertiesContributor",
    "BotProvisioningContext",
    "EngineProvisioningRegistry",
    "EngineProvisioningStrategy",
    "build_extra_envs_fail_open",
    "build_extra_envs_from_bot",
    "build_extra_properties_fail_open",
    "build_extra_properties_from_bot",
    "build_engine_extra_properties_fail_open",
    "extract_runtime_token_fail_open",
    "get_engine_provisioning_registry",
    "resolve_provisioning",
    "should_encrypt_template_token_fail_open",
]
