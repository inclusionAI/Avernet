"""Helpers for consuming template-factory capability snapshots.

AC template factory resolved snapshots may declare ``template_config.capabilities``.
When this key is present it is the only truth source for capability gates: missing
nodes default to false and legacy template_type fallbacks must not be mixed in.
"""
from typing import Any, Dict, Optional


def has_declared_capabilities(template_config: Optional[Dict[str, Any]]) -> bool:
    return isinstance(template_config, dict) and "capabilities" in template_config


def _capabilities(template_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(template_config, dict):
        return {}
    capabilities = template_config.get("capabilities")
    return capabilities if isinstance(capabilities, dict) else {}


def can_join_bcn_as_provider(template_config: Optional[Dict[str, Any]]) -> bool:
    capabilities = _capabilities(template_config)
    bcn = capabilities.get("bcn")
    return isinstance(bcn, dict) and bool(bcn.get("join_as_provider"))
