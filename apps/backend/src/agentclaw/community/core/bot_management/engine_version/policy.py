"""Read effective restart restrictions; never resolve/write publication Pins."""

from __future__ import annotations
from typing import Any

from agentclaw.community.core.devices.services.sandbox_overrides import SandboxOverrides
from agentclaw.community.core.service_bot.services.arca_image_pin import (
    read_image_pin_from_ext,
)


def restart_restriction(
    bot: dict[str, Any], template: dict[str, Any], *, provider: str
) -> str:
    ext = bot.get("ext")
    if ext is not None and not isinstance(ext, dict):
        raise ValueError("invalid image policy")
    # The personal Pin overlays template image; service-draft restart clears it.
    if (bot.get("bot_type") == "personal" or provider == "arca") and isinstance(
        ext, dict
    ):
        image = read_image_pin_from_ext(ext)
        if image:
            return "image_pinned"
        if ext.get("sbot_pin_image") is True:
            raise ValueError("invalid image Pin")
    overrides = SandboxOverrides.from_template_config(template)
    overrides.validate()
    if overrides.image:
        return "explicit_image_unverified"
    if bot.get("active_engine") == "teclaw":
        return "restart_not_supported"
    if provider not in {"arca"}:
        return "restart_path_unverified"
    return ""


def has_version_override(template: dict[str, Any]) -> bool:
    envs = template.get("envs")
    return isinstance(envs, dict) and "AGENTCLAW_IMAGE_VERSION" in envs
