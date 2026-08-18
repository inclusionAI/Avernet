"""AICoding-specific CLI default resolution."""
from __future__ import annotations

from typing import Any, List, Mapping, Optional

from agentclaw.community.core.bot_management.engines.aicoding.default_capabilities import (
    merge_template_preset_capabilities,
)


class AicodingCliDefaultsResolver:
    """Resolve effective default CLIs for AICoding bots.

    AICoding templates may preset CLI capabilities at
    ``ext_info.aicoding.template_config.bot_template_config.preset_capabilities.cli``.
    These are merged by ``cli_code`` onto the AICoding engine default CLI list.
    """

    def resolve(
        self,
        default_items: List[dict],
        ext_info: Optional[Mapping[str, Any]] = None,
    ) -> List[dict]:
        return merge_template_preset_capabilities(
            default_items,
            ext_info,
            capability_key="cli",
            identity_key="cli_code",
        )
