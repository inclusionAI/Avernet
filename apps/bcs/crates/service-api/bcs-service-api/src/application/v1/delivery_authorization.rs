//! Final-writer delivery authorization contract
//! (bot-authority spec §8.2 实时撤权 / §8.3 权威资源读取).
//!
//! Every protected Workbench frame is re-authorized against
//! committed authorization facts immediately before the socket
//! writer sends it. The application layer returns an
//! application-level result only; the WS adapter NEVER observes a
//! SQL/Core error — a failing underlying read is desensitized to
//! `ApplicationError::internal`, which the adapter maps to
//! [`DeliveryDecision::InvalidateBinding`] (fail closed).

use async_trait::async_trait;

use bcs_domain::{MessageAudience, MessageVisibilityDomain};

use super::error::ApplicationError;

/// The view explicitly selected by the V1 Workbench connection
/// over the existing `view_actor_id` wire field (spec §8.3: 连接
/// 时显式选择视角并重新授权). The token never pre-selects a view;
/// the default (`None` wire value) is the authenticated Human self,
/// and another Human actor is always rejected.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConnectionView {
    /// The authenticated Human's own actor view.
    HumanSelf,
    /// A controlled Bot's view. A selection parameter, NOT a
    /// credential: the connection must still verify the current
    /// management edge and the Bot's resource qualification.
    Bot { bot_id: String },
}

/// The connection binding carried with every protected frame
/// (spec §8.2: 在连接绑定中保存真实 Human、env/tenant、资源和视角).
///
/// The binding pins identity (real authenticated User), compatibility
/// tenant metadata, the selected view actor, the resource scope and
/// the connection instance — it deliberately holds NO cached
/// authorization role or message view scope: every frame re-reads
/// the current resource snapshot.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProtectedDeliveryBinding {
    /// Optional compatibility metadata copied from the connection
    /// credential. Not an authorization fact.
    pub tenant: Option<String>,
    /// The real authenticated User id the connection is bound to.
    pub user_id: String,
    /// The token/reply-bound Group resource.
    pub group_id: String,
    /// The Session resource when the connection is session-scoped;
    /// `None` keeps the plain Group view.
    pub session_id: Option<String>,
    /// The view actor selected for the connection (a Human actor id
    /// or a Bot uuid).
    pub view_actor_id: String,
    /// WS connection instance the binding belongs to. A fresh
    /// connection gets a fresh id, so a replaced instance can never
    /// inherit another Human's sink.
    pub connection_instance_id: u64,
}

impl ProtectedDeliveryBinding {
    /// Whether the view actor is the binding's own Human self actor
    /// (`human_{user_id}`).
    pub fn is_human_self_view(&self) -> bool {
        self.view_actor_id == format!("human_{}", self.user_id)
    }

    /// The Bot uuid when the selected view is a Bot.
    pub fn bot_view_id(&self) -> Option<&str> {
        if self.is_human_self_view() {
            None
        } else {
            Some(self.view_actor_id.as_str())
        }
    }
}

/// Trusted producer metadata carried with a frame (spec §8.3: 帧
/// 保留 producer 的 domain/audience 等元数据). The domain comes from
/// the producer's own visibility declaration, never from the
/// containing Group strategy.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeliveryVisibility {
    pub domain: MessageVisibilityDomain,
    pub audience: Option<MessageAudience>,
}

/// Per-frame final-writer decision (spec §8.2 三态).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeliveryDecision {
    /// The frame is visible to the current view: send it.
    Deliver,
    /// The frame is invisible to the current view ONLY (scope
    /// tightened or audience-directed elsewhere): drop this frame,
    /// keep the binding and every other frame flowing.
    SkipMessage,
    /// The view lost its authority edge or its resource
    /// qualification: revoke the binding, drop the queue, remove the
    /// subscriptions and notify the client with the existing close
    /// protocol.
    InvalidateBinding,
}

/// Application-level final-writer authorization (spec §8.2: 所有
/// 受保护数据在最终 writer 发送前重新向应用服务请求授权).
#[async_trait]
pub trait DeliveryAuthorizationService: Send + Sync {
    /// Decide one frame against the CURRENT committed authorization
    /// facts. Every call is an independent check — no connection-time
    /// or queue-time context is reused, no enqueue-time authorization
    /// exists, and no positive-TTL cache replaces this read.
    ///
    /// `Err` is a desensitized application failure (the underlying
    /// read could not be completed); the adapter MUST treat it as
    /// [`DeliveryDecision::InvalidateBinding`] rather than guessing.
    async fn authorize_delivery(
        &self,
        binding: &ProtectedDeliveryBinding,
        message: &DeliveryVisibility,
    ) -> Result<DeliveryDecision, ApplicationError>;
}
