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

use std::sync::Arc;

use bcs_config_api::HumanNotifyProviderConfig;
use futures::future::BoxFuture;
use thiserror::Error;

pub use bcs_service_api::port::human_notify::{MentionNotification, MentionedHuman};

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
    /// delivery; aggregation semantics are defined by the design spec:
    /// at least one recipient delivered (or no recipients) -> `Ok`,
    /// all recipients failed -> `Err(HumanNotifyError::Delivery)`.
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
