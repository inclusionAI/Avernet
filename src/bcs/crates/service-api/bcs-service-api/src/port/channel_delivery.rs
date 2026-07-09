use async_trait::async_trait;

use bcs_domain::{ChannelType, ParticipantRole};

use crate::{ServiceError, ServiceResult};

/// 定位一个 IM 账号(用于取凭证 / 判断可达)。
#[derive(Debug, Clone)]
pub struct ChannelBindingRef {
    pub channel_type: ChannelType,
    /// 钉钉 = robotCode。
    pub account_ref: String,
}

/// 出站 channel 事件类型。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChannelOutboundEventKind {
    ChatDelta,
    ChatFinal,
    Agent,
    System,
}

/// adapter 如何处理该事件。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChannelRenderHint {
    Render,
    PassThrough,
    IgnoreByDefault,
}

/// 一条出站到 IM 的 channel 事件。
#[derive(Debug, Clone)]
pub struct ChannelOutboundEvent {
    pub binding_ref: ChannelBindingRef,
    /// 投递到 IM 的哪个会话(钉钉 conversationId)。
    pub im_conversation_id: String,
    /// 钉钉 conversationType:"1"=单聊,"2"=群聊。
    pub im_conversation_type: String,
    /// PerSender scope 下用于标识回复对象。
    pub im_user_id: Option<String>,
    pub im_user_display_name: Option<String>,
    pub bcs_session_id: String,
    pub run_id: String,
    pub sender_actor_id: String,
    pub sender_label: String,
    /// 是否在 IM 文本中渲染发送者前缀(`[BotName] ...`)。
    /// Group target + FullTranscript 为 true;LeadOnly 和 Bot target/direct-bot 为 false。
    pub render_sender_label: bool,
    pub sender_role: ParticipantRole,
    pub kind: ChannelOutboundEventKind,
    pub text: Option<String>,
    pub raw_payload: serde_json::Value,
    pub render_hint: ChannelRenderHint,
}

/// 归一化投递结果(刻意极简,仿 `BotDeliveryResult`)。
#[derive(Debug)]
pub struct ChannelDeliveryResult {
    pub delivered: bool,
    pub error: Option<ServiceError>,
}

/// 出站投递 port。与 `BotDeliveryPort`/`FrontendDeliveryPort` 平级,投 IM。
#[async_trait]
pub trait ChannelDeliveryPort: Send + Sync {
    async fn is_available(&self, binding: &ChannelBindingRef) -> bool;
    async fn deliver_event(&self, event: ChannelOutboundEvent) -> ServiceResult<ChannelDeliveryResult>;
}
