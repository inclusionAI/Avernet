"""Public contracts for Bot Channel configuration.

Only DingTalk is a supported Channel provider today. Secrets are accepted on
writes but represented only by ``has_client_secret`` on reads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ChannelType = Literal["dingding"]
ChannelStatus = Literal["active", "inactive"]
# 三值口径与老前端一致（老页面"生效环境"选择器）：draft 是唯一可默认的
# 值；verify/online 行是发布流的输入（按 stage 现读合成各环境的渠道配置），
# 仅服务 Bot 允许写入。stage 在创建后不可变。
ChannelStage = Literal["draft", "verify", "online"]
ChannelBindingMode = Literal["plugin", "bcn_gateway"]
GroupChatScope = Literal["per_sender", "conversation_shared"]
OutboundVisibility = Literal["full_transcript", "lead_only"]

# 模式校验矩阵：字段只属于一种绑定模式（见设计 spec §3.2）。
_PLUGIN_ONLY_FIELDS: tuple[str, ...] = (
    "card_template_key",
    "dm_policy",
    "allowlist",
    "reply_to_message",
    "aix_enable",
    "include_sender_name",
)
_BCN_ONLY_FIELDS: tuple[str, ...] = (
    "group_chat_scope",
    "outbound_visibility",
    "forward_sender_identity",
)


def validate_mode_matrix(
    mode: str,
    *,
    robot_code: str | None,
    fields_set: set[str],
) -> None:
    """Reject fields that do not belong to the channel's binding mode.

    Shared by the create-side pydantic validator and the router's update-side
    check (the update side knows the stored mode, which the body alone cannot).
    """
    if mode == "bcn_gateway":
        if not (robot_code or "").strip():
            raise ValueError(
                "robot_code is required when binding_mode is 'bcn_gateway'"
            )
        rejected = sorted(set(_PLUGIN_ONLY_FIELDS) & fields_set)
        if rejected:
            raise ValueError(
                "fields only valid for plugin channels were provided: "
                + ", ".join(rejected)
            )
    else:
        rejected = sorted(set(_BCN_ONLY_FIELDS) & fields_set)
        if rejected:
            raise ValueError(
                "fields only valid for bcn_gateway channels were provided: "
                + ", ".join(rejected)
            )


def validate_streaming_card_template(
    *,
    enable_streaming_cards: bool | None,
    card_template_id: str | None,
) -> None:
    """Streaming cards need a template id — surface the gap at validation time.

    Without this check a ``card_template_id``-less streaming config rows
    through both surfaces: the plugin write path stores it and the BCS
    payload (``send_mode: streaming_card`` with an empty template) is
    structurally accepted. The card then fails only at message time, where
    the user cannot tell which configuration is at fault.
    """
    if enable_streaming_cards and not (card_template_id or "").strip():
        raise ValueError(
            "card_template_id is required when enable_streaming_cards is true"
        )


class DingTalkChannelConfigCreate(BaseModel):
    """DingTalk credentials and behavior for a new Channel."""

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1, description="DingTalk application client id.")
    client_secret: str = Field(
        min_length=1,
        description="DingTalk application secret. Write-only and never returned.",
        json_schema_extra={"writeOnly": True},
    )
    card_template_id: str | None = Field(
        default=None, description="DingTalk interactive-card template identifier."
    )
    card_template_key: str | None = Field(
        default=None, description="Template field used for the card message body."
    )
    enable_streaming_cards: bool = Field(
        default=False, description="Whether DingTalk cards update while output streams."
    )
    dm_policy: Literal["open", "disabled"] = Field(
        default="open", description="Whether direct messages may reach this Channel."
    )
    allowlist: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Sender identifiers allowed to use the Channel; '*' permits all.",
    )
    reply_to_message: bool = Field(
        default=True, description="Whether replies attach to the source message."
    )
    robot_code: str = Field(
        default="", description="Optional DingTalk robot code used by the application."
    )
    aix_enable: bool = Field(
        default=True, description="Whether the DingTalk AI-card extension is enabled."
    )
    include_sender_name: bool = Field(
        default=True, description="Whether the sender's name enters Bot context."
    )
    group_chat_scope: GroupChatScope | None = Field(
        default=None,
        description="Group-session scoping; only valid when binding_mode is 'bcn_gateway'.",
    )
    outbound_visibility: OutboundVisibility | None = Field(
        default=None,
        description="Outbound transcript visibility; only valid when binding_mode is 'bcn_gateway'.",
    )
    forward_sender_identity: bool | None = Field(
        default=None,
        description=(
            "Whether inbound messages forward the DingTalk sender identity to the "
            "Bot's context; only valid when binding_mode is 'bcn_gateway'."
        ),
    )


class DingTalkChannelConfigUpdate(BaseModel):
    """Partial DingTalk config update; omitted values remain unchanged."""

    model_config = ConfigDict(extra="forbid")

    client_id: str | None = Field(
        default=None, min_length=1, description="Replacement client id; omit to keep."
    )
    client_secret: str | None = Field(
        default=None,
        min_length=1,
        description="Replacement secret. Omit or send null to preserve the stored secret.",
        json_schema_extra={"writeOnly": True},
    )
    card_template_id: str | None = Field(
        default=None, description="Replacement card-template id; null clears it."
    )
    card_template_key: str | None = Field(
        default=None, description="Replacement card-template key; null clears it."
    )
    enable_streaming_cards: bool | None = Field(
        default=None, description="Enable or disable streaming cards; omit to keep."
    )
    dm_policy: Literal["open", "disabled"] | None = Field(
        default=None, description="New direct-message policy; omit to keep."
    )
    allowlist: list[str] | None = Field(
        default=None, description="Replacement sender allowlist; omit to keep."
    )
    reply_to_message: bool | None = Field(
        default=None, description="New reply-linking behavior; omit to keep."
    )
    robot_code: str | None = Field(
        default=None, description="Replacement DingTalk robot code; omit to keep."
    )
    aix_enable: bool | None = Field(
        default=None, description="Enable or disable the DingTalk AI-card extension."
    )
    include_sender_name: bool | None = Field(
        default=None,
        description="Whether sender names enter Bot context; omit to keep.",
    )
    group_chat_scope: GroupChatScope | None = Field(
        default=None,
        description="New group-session scoping; omit to keep. bcn_gateway channels only.",
    )
    outbound_visibility: OutboundVisibility | None = Field(
        default=None,
        description="New outbound visibility; omit to keep. bcn_gateway channels only.",
    )
    forward_sender_identity: bool | None = Field(
        default=None,
        description=(
            "Whether inbound messages forward the DingTalk sender identity; "
            "omit to keep. bcn_gateway channels only."
        ),
    )


class DingTalkChannelConfig(BaseModel):
    """Safe DingTalk config projection; never contains the client secret."""

    client_id: str = Field(description="DingTalk application client id.")
    has_client_secret: bool = Field(
        description="Whether a secret is stored; its value is never returned."
    )
    card_template_id: str | None = Field(
        default=None, description="DingTalk interactive-card template identifier."
    )
    card_template_key: str | None = Field(
        default=None, description="Template field used for the card message body."
    )
    enable_streaming_cards: bool = Field(
        default=False, description="Whether DingTalk cards update while output streams."
    )
    dm_policy: str = Field(
        default="open", description="Direct-message policy stored for the Channel."
    )
    allowlist: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Sender identifiers allowed to use the Channel; '*' permits all.",
    )
    reply_to_message: bool = Field(
        default=True, description="Whether replies attach to the source message."
    )
    robot_code: str = Field(
        default="", description="Optional DingTalk robot code used by the application."
    )
    aix_enable: bool = Field(
        default=True, description="Whether the DingTalk AI-card extension is enabled."
    )
    include_sender_name: bool = Field(
        default=True, description="Whether the sender's name enters Bot context."
    )
    group_chat_scope: GroupChatScope | None = Field(
        default=None,
        description="Group-session scoping when binding_mode is 'bcn_gateway'; null otherwise.",
    )
    outbound_visibility: OutboundVisibility | None = Field(
        default=None,
        description="Outbound visibility when binding_mode is 'bcn_gateway'; null otherwise.",
    )
    forward_sender_identity: bool | None = Field(
        default=None,
        description=(
            "Whether inbound messages forward the DingTalk sender identity when "
            "binding_mode is 'bcn_gateway'; null otherwise."
        ),
    )


class ChannelCreate(BaseModel):
    """Create one inactive Channel row on the Bot named in the path."""

    model_config = ConfigDict(extra="forbid")

    type: ChannelType = Field(
        description="Channel provider; only 'dingding' is supported."
    )
    binding_mode: ChannelBindingMode = Field(
        default="plugin",
        description=(
            "How the Channel connects: 'plugin' writes openclaw.json direct "
            "config; 'bcn_gateway' syncs a BCS binding (per-sender sessions)."
        ),
    )
    stage: ChannelStage = Field(
        default="draft",
        description=(
            "Which configuration stage the row belongs to; the legacy UI's "
            "'生效环境' selector. verify/online rows are publish-flow inputs "
            "for service Bots, exist only for 'plugin' channels (the legacy "
            "BCN page has no stage concept), and are rejected for other bot "
            "types. Immutable after creation."
        ),
    )

    @model_validator(mode="after")
    def _check_binding_mode_matrix(self) -> "ChannelCreate":
        validate_mode_matrix(
            self.binding_mode,
            robot_code=self.config.robot_code,
            fields_set=self.config.model_fields_set,
        )
        validate_streaming_card_template(
            enable_streaming_cards=self.config.enable_streaming_cards,
            card_template_id=self.config.card_template_id,
        )
        # 老实现里"生效环境"是插件页专属能力（BCN 页无 stage 概念）。当前
        # 消费方亦然：发布流只合成 plugin 行的 engine_overrides，BCS 同步
        # 又跳过 verify/online —— 放行只会产出一个无消费方的组合。
        if self.stage != "draft" and self.binding_mode == "bcn_gateway":
            raise ValueError(
                "stage 'verify'/'online' applies to plugin channels; "
                "bcn_gateway channels resolve their environment through BCS"
            )
        return self

    description: str | None = Field(
        default=None, description="Optional human-readable purpose of the Channel."
    )
    config: DingTalkChannelConfigCreate = Field(
        description="DingTalk credentials and message behavior."
    )


class ChannelUpdate(BaseModel):
    """Partial update of an existing draft Channel."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(
        default=None, description="New description; null clears it, omit to keep."
    )
    binding_mode: ChannelBindingMode | None = Field(
        default=None,
        description="Must equal the stored mode; the binding mode is immutable after creation.",
    )
    config: DingTalkChannelConfigUpdate | None = Field(
        default=None,
        description="Partial DingTalk config update; omit to keep all config.",
    )


class ChannelStatusUpdate(BaseModel):
    """Activate or deactivate a Channel."""

    model_config = ConfigDict(extra="forbid")

    status: ChannelStatus = Field(
        description="Target runtime state: 'active' or 'inactive'."
    )


class Channel(BaseModel):
    """One Bot-bound external Channel."""

    id: int = Field(description="Numeric Channel identifier assigned by the Backend.")
    type: ChannelType = Field(
        description="Channel provider; currently always 'dingding'."
    )
    binding_mode: ChannelBindingMode = Field(
        default="plugin",
        description="How the Channel connects: 'plugin' or 'bcn_gateway'.",
    )
    description: str | None = Field(description="Human-readable purpose, if set.")
    bot_id: str = Field(description="Bot this Channel is bound to.")
    owner_id: str = Field(description="Owner of the Bot this Channel is bound to.")
    config: DingTalkChannelConfig = Field(
        description="Safe DingTalk config projection with no credential value."
    )
    status: ChannelStatus = Field(
        description="Current public state: active or inactive."
    )
    stage: ChannelStage = Field(
        default="draft",
        description=(
            "Configuration stage the row belongs to. verify/online rows exist "
            "only as publish inputs for service Bots; other bots expose draft."
        ),
    )
    created_at: datetime | None = Field(
        default=None, description="Channel creation time when available."
    )
    updated_at: datetime | None = Field(
        default=None, description="Most recent persisted update time when available."
    )
