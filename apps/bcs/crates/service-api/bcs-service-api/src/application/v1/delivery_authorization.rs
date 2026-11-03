//! Operation-boundary delivery admission contract
//! (bot-authority spec §8.2/§8.3 — 已准入输出不追溯).
//!
//! Delivery authorization is decided ONCE per business operation per
//! recipient connection, at the moment the operation is accepted —
//! never per frame. The decision is frozen into a read-only
//! [`AdmittedRecipient`] permit; every later frame of that operation
//! is checked against the permit locally (pure predicate, no
//! database read, no authority re-query):
//!
//! - revocation (transfer, manager-edge removal, membership or
//!   scope change) DURING an admitted operation does not interrupt
//!   the operation's already-admitted output on the same live
//!   connection;
//! - a NEW operation, a new subscription, a reconnect or a view
//!   switch is admitted against CURRENT facts — a revoked viewer
//!   receives nothing of operations admitted after the revocation;
//! - an authorization read failure at admission time yields no
//!   permits for that operation (fail closed, logged); it must
//!   never cancel other permits or existing streams.
//!
//! The trait returns application-level results only; adapters never
//! observe a SQL/Core error.

use async_trait::async_trait;

use bcs_domain::{ActorKind, HumanMessageView, MessageAudience, MessageVisibilityDomain};

use super::error::ApplicationError;
use crate::types::MessageViewScope;

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
    /// management edge and the Bot's resource qualification at
    /// every admission boundary.
    Bot { bot_id: String },
}

/// The authenticated identity snapshot of ONE Workbench connection
/// (spec §8.2: 连接绑定保存真实 Human、env/tenant、资源和视角).
///
/// The binding pins identity (real authenticated User), compatibility
/// tenant metadata, the selected view actor, the resource scope and
/// the connection instance. It deliberately holds NO cached
/// authorization role or message view scope: adapters present it as
/// the admission CANDIDATE at every operation/event boundary, and
/// the application re-reads the committed facts there.
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
    /// inherit another connection's permits.
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

    /// The view actor kind derived from the actor id shape:
    /// `human_*` actors are Humans, everything else is a Bot.
    pub fn view_kind(&self) -> ActorKind {
        if self.view_actor_id.starts_with("human_") {
            ActorKind::Human
        } else {
            ActorKind::Bot
        }
    }
}

/// The canonical resource one delivery decision is scoped to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeliveryScope {
    /// The canonical Group the operation/event belongs to.
    pub group_id: String,
    /// The canonical Session when the operation is session-anchored;
    /// `None` is a true Group-only operation (never repaired from a
    /// Group seed for session-scoped candidates — F4 strict target).
    pub session_id: Option<String>,
}

/// Trusted producer metadata carried with a frame (spec §8.3: 帧
/// 保留 producer 的 domain/audience 等元数据). The domain comes from
/// the producer's own visibility declaration, never from the
/// containing Group strategy and never parsed back out of payload
/// JSON.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeliveryVisibility {
    pub domain: MessageVisibilityDomain,
    pub audience: Option<MessageAudience>,
}

/// The read-only, per-connection output permit decided at ONE
/// admission boundary (spec §8.2: 已准入接收者的不可变可见范围).
///
/// This is a visibility permit only: it carries NO Owner/Manager
/// boolean and no role for other business logic to reuse, and a
/// client-supplied actor/run id can never construct one. Frozen at
/// admission; mid-operation authority changes do not mutate it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AdmittedRecipient {
    /// The connection instance this permit belongs to. A replaced
    /// connection never matches another instance's permit.
    pub connection_instance_id: u64,
    /// The admitted view actor (Human actor id or Bot uuid).
    pub view_actor_id: String,
    /// The message view scope frozen at admission.
    pub scope: MessageViewScope,
    /// Whether legacy unclassified Chat frames are admitted (the
    /// associated Group's own strategy rule, decided at admission).
    pub allow_legacy_unclassified_chat: bool,
}

impl AdmittedRecipient {
    /// The pure per-frame predicate run against the permit: no I/O,
    /// no database, no clock. A `false` answer drops THIS frame
    /// only; the permit and every other frame are untouched.
    pub fn allows(&self, visibility: &DeliveryVisibility) -> bool {
        let view = HumanMessageView {
            actor_id: self.view_actor_id.clone(),
            scope: self.scope,
            allow_legacy_unclassified_chat: self.allow_legacy_unclassified_chat,
        };
        view.allows_artifact(visibility.domain, visibility.audience.as_ref())
    }
}

/// Application-level delivery admission (spec §8.2: 操作准入在业务
/// 接受时点完成，输出路径不再逐帧查询当前权限).
#[async_trait]
pub trait DeliveryAdmissionService: Send + Sync {
    /// Resolve the current delivery view of EVERY candidate
    /// connection for ONE scope, reading the authoritative
    /// Group/Session snapshots ONCE per call (never once per
    /// candidate) and the current management edge for Bot views.
    ///
    /// The SAME resolution backs both boundary kinds; only the call
    /// timing differs:
    ///
    /// - business-operation admission (at accept, before any output):
    ///   the adapter installs the returned views as frozen permits;
    /// - an independent resource event (member/metadata/system
    ///   notification): the adapter applies the views to THIS event
    ///   only and discards them — the next event re-resolves.
    ///
    /// A candidate that fails its checks is simply absent from the
    /// result (no permit). An underlying read failure is a
    /// desensitized `ApplicationError`: the caller must treat the
    /// WHOLE operation/event as permit-less (fail closed) without
    /// touching permits established by earlier admissions.
    async fn resolve_recipient_views(
        &self,
        scope: &DeliveryScope,
        candidates: &[ProtectedDeliveryBinding],
    ) -> Result<Vec<AdmittedRecipient>, ApplicationError>;
}
