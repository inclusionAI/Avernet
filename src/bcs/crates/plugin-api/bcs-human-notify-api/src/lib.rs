//! Human mention notification plugin API for BCS.
//!
//! This crate is the provider-neutral contract for notifying human
//! participants when a message @-mentions them. Public binaries include the
//! built-in `dummy` backend; product-specific binaries can link crates that
//! submit [`HumanMentionNotifierFactory`] entries through `inventory`
//! (e.g. the internal DingTalk notifier).
//!
//! Provider crates receive only their own config table from
//! `human_notify.providers.<provider>`.
//!
//! The Plugin API owns its notification schema ([`MentionNotification`] /
//! [`MentionedHuman`]) so the plugin contract evolves independently from the
//! Service API port that produces it; the bootstrap adapter translates
//! between the two.

use std::sync::Arc;

use bcs_config_api::HumanNotifyProviderConfig;
use futures::future::BoxFuture;
use thiserror::Error;

/// A single @-mentioned human participant (plugin contract schema).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MentionedHuman {
    /// Human participant actor id, shaped as `human_{staff_no}`.
    pub actor_id: String,
    /// Display name used at mention time (participant `bot_name`).
    pub display_name: String,
}

/// One @-human mention event (plugin contract schema). One message maps to
/// one event carrying every human mentioned by that message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MentionNotification {
    /// Session id; empty string for group-level messages without a session.
    pub session_id: String,
    pub group_id: String,
    /// Sender actor id (`bot_x` or `human_y`).
    pub sender_actor_id: String,
    /// Sender display name.
    pub sender_label: String,
    /// Every human @-mentioned by this message (sender excluded).
    pub mentioned: Vec<MentionedHuman>,
    /// Message text shown to the mentioned human.
    pub message_text: String,
    pub timestamp_ms: u64,
}

/// Error returned by human-mention notification backends.
#[derive(Debug, Error)]
pub enum HumanNotifyError {
    /// The provider config table is syntactically valid but invalid for this
    /// provider. Build-time failures surface as startup errors.
    #[error("invalid human notify provider config: {0}")]
    Config(String),

    /// Runtime delivery failed for every recipient.
    #[error("human mention notification delivery failed: {0}")]
    Delivery(String),
}

/// Result alias for human-mention notification operations.
pub type HumanNotifyResult<T> = Result<T, HumanNotifyError>;

/// Notification backend for @-mentioned human participants.
#[async_trait::async_trait]
pub trait HumanMentionNotifier: Send + Sync {
    /// Backend name, matching the registered factory name.
    fn backend_name(&self) -> &'static str;

    /// Deliver one notification. Implementations decide per-recipient
    /// delivery; the aggregation contract is:
    /// a recipient whose `actor_id` the backend cannot parse is skipped and
    /// does not count as a failure; at least one recipient delivered (or no
    /// recipients at all, or all skipped) -> `Ok`; every recipient failed ->
    /// `Err(HumanNotifyError::Delivery)`.
    async fn notify(&self, notification: &MentionNotification) -> HumanNotifyResult<()>;
}

/// Factory function implemented by linked notifier provider crates.
pub type HumanMentionNotifierBuild = fn(
    HumanNotifyProviderConfig,
) -> BoxFuture<'static, HumanNotifyResult<Arc<dyn HumanMentionNotifier>>>;

/// Inventory entry for a human-mention notification backend.
pub struct HumanMentionNotifierFactory {
    /// Backend name selected by `human_notify.provider`.
    pub name: &'static str,

    /// Build the backend from `human_notify.providers.<name>`.
    pub build: HumanMentionNotifierBuild,
}

inventory::collect!(HumanMentionNotifierFactory);
