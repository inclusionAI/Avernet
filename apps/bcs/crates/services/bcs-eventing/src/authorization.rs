//! Authorization boundary for Event Subscription application policy.

use async_trait::async_trait;
use bcs_service_api::application::v1::{ApplicationError, AuthenticatedCaller};
use bcs_service_api::types::{EventActor, EventSubscriptionScope};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventSubscriptionAuthorizationAction {
    Create,
    Read,
    Manage,
    Test,
    Replay,
    Skip,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorizedEventSubscriptionScope {
    pub actor: EventActor,
    /// Full payload access is intentionally independent from scope management.
    pub full_payload_allowed: bool,
}

/// Resolves existing group/session/task/run ownership and higher-level grants.
///
/// Implementations return an application error so they can preserve the
/// caller-visible distinction between an absent scope, an invisible scope
/// (404), and a visible but forbidden scope (403). HTTP routes must not repeat
/// this policy.
#[async_trait]
pub trait EventSubscriptionAuthorizer: Send + Sync {
    async fn authorize(
        &self,
        caller: &AuthenticatedCaller,
        scope: &EventSubscriptionScope,
        action: EventSubscriptionAuthorizationAction,
    ) -> Result<AuthorizedEventSubscriptionScope, ApplicationError>;
}
