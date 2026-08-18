"""AICoding default-capability extensions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Optional

_AICODING_EXT_KEY = "aicoding"


@dataclass(frozen=True)
class AicodingDefaultCapabilitiesExtInfo:
    template_config: Mapping[str, Any] | None = None

    @classmethod
    def from_ext_info(
        cls,
        ext_info: Optional[Mapping[str, Any]],
    ) -> "AicodingDefaultCapabilitiesExtInfo":
        """Parse the AICoding payload from default-capabilities ext_info.

        Expected shape::

            {
                "aicoding": {
                    "template_config": template_config,
                }
            }
        """
        if not isinstance(ext_info, Mapping):
            return cls()

        aicoding_info = ext_info.get(_AICODING_EXT_KEY)
        if not isinstance(aicoding_info, Mapping):
            return cls()

        template_config = aicoding_info.get("template_config")
        return cls(
            template_config=template_config
            if isinstance(template_config, Mapping)
            else None,
        )


def merge_template_preset_capabilities(
    default_items: List[dict],
    ext_info: Optional[Mapping[str, Any]],
    *,
    capability_key: str,
    identity_key: str,
) -> List[dict]:
    """Merge AICoding template preset capabilities onto engine defaults.

    Reads presets from
    ``ext_info.aicoding.template_config.bot_template_config.preset_capabilities``
    and merges the selected capability list by its identity field. Existing
    default entries keep their original position and are field-overridden by the
    template entry; new template entries are appended in template order.
    """
    items = [dict(cfg) for cfg in default_items]
    positions = {
        cfg.get(identity_key): idx
        for idx, cfg in enumerate(items)
        if cfg.get(identity_key)
    }

    for entry in _template_preset_entries(
        ext_info,
        capability_key=capability_key,
        identity_key=identity_key,
    ):
        key = entry[identity_key]
        existing_idx = positions.get(key)
        if existing_idx is None:
            positions[key] = len(items)
            items.append(dict(entry))
        else:
            items[existing_idx] = {**items[existing_idx], **entry}

    return items


def _template_preset_entries(
    ext_info: Optional[Mapping[str, Any]],
    *,
    capability_key: str,
    identity_key: str,
) -> List[dict]:
    """Return normalized AICoding template preset entries."""
    template_config = AicodingDefaultCapabilitiesExtInfo.from_ext_info(
        ext_info
    ).template_config
    if template_config is None:
        return []
    bot_template_config = template_config.get("bot_template_config")
    if not isinstance(bot_template_config, Mapping):
        return []
    preset_capabilities = bot_template_config.get("preset_capabilities")
    if not isinstance(preset_capabilities, Mapping):
        return []
    raw_items = preset_capabilities.get(capability_key)
    if not isinstance(raw_items, list):
        return []

    entries: List[dict] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        raw_key = item.get(identity_key)
        if not isinstance(raw_key, str) or not raw_key.strip():
            continue
        entry = dict(item)
        entry[identity_key] = raw_key.strip()
        entries.append(entry)
    return entries
