//! Security gateway plugin API for BCS.
//!
//! Defines the outbound contract for Bot-to-Bot message security checks. The
//! routing interceptor asks a [`SecurityGatewayPort`] whether an outbound
//! message may be delivered without caring whether the verdict comes from an
//! external AI security gateway over HTTP, a local fake, or a noop that always
//! allows.
//!
//! The port deliberately uses neutral request/verdict types — no transport,
//! wire protocol (camelCase fields, vendor error codes), or gateway address
//! leaks across this boundary. Concrete backends live in `plugins/*` crates and
//! are selected at the composition root.

use async_trait::async_trait;

/// Neutral request describing one outbound message to security-check.
///
/// Carries raw, unvalidated context. Whether an `agent_code` is required, what
/// token format is acceptable, etc., is decided by the concrete backend — the
/// interceptor does not pre-validate, so backend-specific preconditions never
/// leak into the generic routing layer.
#[derive(Debug, Clone)]
pub struct SecurityCheckRequest {
    /// Sender bot id (for diagnostics / backend logging).
    pub sender_bot_id: String,
    /// Receiver bot id (for diagnostics / backend logging).
    pub receiver_bot_id: String,
    /// Sender bot's agent_code, if present (capability identity).
    pub sender_agent_code: Option<String>,
    /// Receiver bot's agent_code, if present (capability identity).
    pub receiver_agent_code: Option<String>,
    /// Sender's agent_token, used by backends that require authorization.
    pub agent_token: Option<String>,
    /// Message content to inspect.
    pub message_content: String,
    /// Original message id; a backend may return a replacement task id.
    pub message_id: String,
}

/// Neutral verdict returned by a [`SecurityGatewayPort`] implementation.
///
/// The interceptor maps this onto its delivery decision and applies the
/// `dry_run` policy (so the port stays free of policy concerns).
#[derive(Debug, Clone)]
pub enum SecurityVerdict {
    /// Allowed. `task_id` is an optional backend-assigned id to stamp onto the
    /// message in place of the original id.
    Allow { task_id: Option<String> },
    /// Denied. Carries a machine-readable code and human-readable message; the
    /// interceptor decides whether to actually block (non dry-run) or observe.
    Deny { code: String, message: String },
    /// Backend unavailable / errored / input unverifiable. The caller treats
    /// this as fail-open (continue delivery) and surfaces the reason.
    Unavailable { reason: String },
}

/// Outbound message security gateway.
///
/// Implemented by:
/// - a public noop (always [`SecurityVerdict::Allow`]) for open-source builds,
/// - a configurable fake for tests,
/// - the real external gateway client (HTTP) injected in internal builds.
#[async_trait]
pub trait SecurityGatewayPort: Send + Sync + 'static {
    async fn check(&self, request: SecurityCheckRequest) -> SecurityVerdict;
}
