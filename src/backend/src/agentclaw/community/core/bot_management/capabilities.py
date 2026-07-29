"""Helpers for consuming template-factory capability snapshots.

AC template factory resolved snapshots may declare ``template_config.capabilities``.
When this key is present it is the only truth source for capability gates: missing
nodes default to false and legacy template_type fallbacks must not be mixed in.
"""
from typing import Any, Dict, Optional


TEMPLATE_FACTORY_MARKER_KEYS = frozenset({
    "template_key",
    "template_uid",
    "template_version_id",
    "template_version",
})


def is_template_factory_config(template_config: Optional[Dict[str, Any]]) -> bool:
    return isinstance(template_config, dict) and any(
        key in template_config for key in TEMPLATE_FACTORY_MARKER_KEYS
    )


def has_declared_capabilities(template_config: Optional[Dict[str, Any]]) -> bool:
    return isinstance(template_config, dict) and "capabilities" in template_config


def _capabilities(template_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(template_config, dict):
        return {}
    capabilities = template_config.get("capabilities")
    return capabilities if isinstance(capabilities, dict) else {}


def _bool_capability(
    capabilities: Dict[str, Any],
    flat_key: str,
    nested_key: str,
    nested_value_key: str,
) -> bool:
    flat_value = capabilities.get(flat_key)
    if isinstance(flat_value, bool):
        return flat_value

    nested_value = capabilities.get(nested_key)
    if isinstance(nested_value, bool):
        return nested_value
    if isinstance(nested_value, dict):
        return bool(nested_value.get(nested_value_key))

    return False


def can_join_bcn_as_provider(template_config: Optional[Dict[str, Any]]) -> bool:
    capabilities = _capabilities(template_config)
    return _bool_capability(
        capabilities,
        "enable_bcn_network",
        "bcn",
        "join_as_provider",
    )
