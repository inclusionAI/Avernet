"""Classify service binding history without borrowing another runtime's Provider."""
from __future__ import annotations

from typing import Literal

from agentclaw.community.core.devices.repository.record import DeviceBindingRecord


ServiceRestartBindingScope = Literal[
    "draft", "caller", "published", "evaluation", "other_identity", "unknown"
]


def service_restart_binding_scope(
    binding: DeviceBindingRecord,
    *,
    bot_id: str,
    entity_id: str,
    entity_type: str,
    env: str,
) -> ServiceRestartBindingScope:
    """Recognize DeviceService lifecycle metadata; never expose its credential.

    A Provider publish ID is not a stage marker: managed BaaS drafts also have
    one. Their callback/resource identity distinguishes them from release rows.
    """
    props = binding.device_props or {}
    # 以下为安全注释COSEC：历史记录必须属于同一资源，不能借其他运行实例恢复草稿。
    if (
        binding.entity_id != entity_id
        or binding.entity_type != entity_type
        or binding.env != env
        or props.get("bolt_id") != bot_id
    ):
        return "other_identity"
    if binding.apply_reason == f"caller_instance:{bot_id}":
        return "caller"
    if props.get("AGENTCLAW_DEFAULT_TAG") or props.get("default_tag"):
        return "evaluation"
    if props.get("stage") and props["stage"] != "draft":
        return "published"

    callback_token = props.get("callback_token")
    if (
        isinstance(callback_token, str)
        and bool(callback_token.strip())
        and props.get("entity_id") == entity_id
        and props.get("entity_type") == entity_type
    ):
        return "draft"
    if any(props.get(key) is not None for key in (
        "publish_id", "baas_publish_id", "destroy_publish_id"
    )):
        return "published"
    return "unknown"
