//! Channel use-case service。
//!
//! HTTP 回调 adapter 调 `handle_inbound`;message-flow 出站 hook 调 `try_outbound`;
//! HTTP/CLI 管理 binding。实现在 `services/bcs-channel`。

use async_trait::async_trait;

use bcs_domain::{
    BindingTarget, GroupChatScope, ChannelBinding, ChannelConfig, ChannelType,
    ParticipantRole, Visibility,
};

use crate::core::ServiceError;
use crate::port::channel_delivery::{ChannelOutboundEventKind, ChannelRenderHint};

/// Channel use-case 错误。
#[derive(Debug, thiserror::Error)]
pub enum ChannelUseCaseError {
    #[error("channel binding not found: {0}")]
    NotFound(String),
    #[error("invalid channel params: {0}")]
    InvalidParams(String),
    #[error(transparent)]
    Internal(ServiceError),
}

impl From<ServiceError> for ChannelUseCaseError {
    fn from(e: ServiceError) -> Self {
        ChannelUseCaseError::Internal(e)
    }
}

/// Failure category for normalized channel inbound processing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChannelInboundFailureKind {
    InvalidInbound,
    BindingNotFound,
    BindingLookupFailed,
    ActorResolutionFailed,
    ContextResolutionFailed,
    SessionResolutionFailed,
    DispatchFailed,
    Internal,
}

/// Typed failure returned while processing normalized channel inbound messages.
#[derive(Debug, thiserror::Error)]
#[error("channel inbound {kind:?}: {diagnostic}")]
pub struct ChannelInboundError {
    pub kind: ChannelInboundFailureKind,
    pub retryable: bool,
    pub diagnostic: String,
}

impl ChannelInboundError {
    pub fn new(
        kind: ChannelInboundFailureKind,
        retryable: bool,
        diagnostic: impl Into<String>,
    ) -> Self {
        Self {
            kind,
            retryable,
            diagnostic: diagnostic.into(),
        }
    }
}

/// 一条入站 IM 消息(已由 adapter 从 IM 原生 body 解析为中性结构)。
#[derive(Debug, Clone)]
pub struct InboundMessage {
    pub channel_type: ChannelType,
    pub account_ref: String,
    pub im_conversation_id: String,
    /// 钉钉 conversationType:1=单聊,2=群。
    pub conversation_type: String,
    pub im_user_id: String,
    pub im_user_nick: Option<String>,
    pub text: String,
    /// 该消息是否 @ 了本机器人(isInAtList / atUsers 命中)。
    pub is_at_bot: bool,
    /// 去重用。
    pub msg_id: String,
}

/// 创建 binding 入参。
#[derive(Debug, Clone)]
pub struct CreateBindingCommand {
    pub channel_type: ChannelType,
    pub account_ref: String,
    pub target: BindingTarget,
    /// Provider-specific group chat scope; applies to bot and group targets.
    pub group_chat_scope: Option<GroupChatScope>,
    pub outbound_visibility: Visibility,
    pub env: String,
    pub created_by: Option<String>,
    pub config: ChannelConfig,
}

/// 出站 hook 的单条事件(由 message-flow 在 bot_event/human send 路径构造)。
#[derive(Debug, Clone)]
pub struct OutboundMessage {
    pub group_id: String,
    pub bcs_session_id: String,
    pub run_id: String,
    pub sender_actor_id: String,
    pub sender_role: ParticipantRole,
    /// 发送者显示名(用于 "[name]" 前缀)。
    pub sender_label: String,
    pub kind: ChannelOutboundEventKind,
    pub text: Option<String>,
    pub raw_payload: serde_json::Value,
    pub render_hint: ChannelRenderHint,
    /// 该消息是否来自 IM(防回环:来自 IM 的不再转发回去)。
    pub source_is_channel: bool,
}

#[async_trait]
pub trait ChannelService: Send + Sync {
    /// 入站:解析 binding → 确保 Human actor → 按 group strategy 分流:
    /// Chat 型 session 调 MessageFlowService,StateMachine 触发 runtime task。
    /// Errors are classified for the delivery adapter to provide actionable feedback.
    async fn handle_inbound(&self, msg: InboundMessage) -> Result<(), ChannelInboundError>;

    /// 出站 hook:若 session 绑定了 channel 会话且可见性允许,则投递到 IM。
    /// 无绑定 / 不可见 / 来自 IM(防回环)→ no-op。
    async fn try_outbound(&self, msg: OutboundMessage) -> Result<(), ChannelUseCaseError>;

    async fn create_binding(
        &self,
        cmd: CreateBindingCommand,
    ) -> Result<ChannelBinding, ChannelUseCaseError>;
    async fn list_bindings(&self) -> Result<Vec<ChannelBinding>, ChannelUseCaseError>;
    async fn set_binding_status(&self, id: &str, active: bool)
        -> Result<(), ChannelUseCaseError>;
    async fn update_binding_config(
        &self,
        id: &str,
        config: serde_json::Value,
    ) -> Result<(), ChannelUseCaseError>;
    async fn delete_binding(&self, id: &str) -> Result<(), ChannelUseCaseError>;
}

#[cfg(test)]
mod tests {
    use super::{ChannelInboundError, ChannelInboundFailureKind};

    #[test]
    fn inbound_error_preserves_failure_kind_retryability_and_diagnostic() {
        let error = ChannelInboundError::new(
            ChannelInboundFailureKind::ActorResolutionFailed,
            true,
            "actor write failed",
        );

        assert_eq!(error.kind, ChannelInboundFailureKind::ActorResolutionFailed);
        assert!(error.retryable);
        assert!(error.diagnostic.contains("actor write failed"));
    }
}
