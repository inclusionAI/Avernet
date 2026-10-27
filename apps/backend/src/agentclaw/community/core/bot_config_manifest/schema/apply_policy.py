"""Closed v1 category-level Apply policy, independent of caller identity."""

from typing import Any

from agentclaw.community.core.bot_config_manifest.capabilities import ManifestCategory


def validate_apply_policy(ctx, parsed: dict[str, Any]) -> None:
    if "apply_policy" not in parsed:
        return
    policy = parsed["apply_policy"]
    if not isinstance(policy, dict):
        ctx.add(
            "apply_policy", "invalid_apply_policy", "apply_policy must be an object"
        )
        return
    support = ctx.capabilities.apply_policy_support()
    for category, options in policy.items():
        at = f"apply_policy.{category}"
        if category not in ("skills", "mcp"):
            ctx.add(
                at, "unknown_apply_policy", "only skills and mcp support Apply policies"
            )
            continue
        if not isinstance(options, dict):
            ctx.add(at, "invalid_apply_policy", "category policy must be an object")
            continue
        for key, value in options.items():
            if key != "preserve_defaults":
                ctx.add(f"{at}.{key}", "unknown_apply_policy", "unknown Apply policy")
            elif type(value) is not bool:
                ctx.add(
                    f"{at}.{key}",
                    "invalid_apply_policy",
                    "preserve_defaults must be boolean",
                )
            elif key not in support.get(category, ()):
                ctx.add(
                    at,
                    "unsupported_apply_policy",
                    "this category policy is unsupported",
                )


def preserved_categories(parsed) -> frozenset[ManifestCategory]:
    policy = parsed.get("apply_policy", {})
    return frozenset(
        category
        for category in (ManifestCategory.SKILLS, ManifestCategory.MCP)
        if policy.get(category.value, {}).get("preserve_defaults", False)
    )
