"""AICoding default-capability extensions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

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
