//! Caller container gateway port (spec §10.8, P2-B first slice).
//!
//! Transport-agnostic outbound port for `caller.container.ensure`: BCS asks
//! the target Bot's Provider (internal Gateway duty) to make sure a routable
//! caller container exists for one caller × Bot × session scope. It is NOT
//! the Provider HTTP adapter itself; the composition root binds the HTTP
//! implementation over the Provider webhook infrastructure.
//!
//! Invariants frozen here (wire contract: apps/bcs/docs/caller-container-gateway.md):
//! - `caller_subject_ref` is runtime-only opaque material: no serde, redacted
//!   Debug, reachable only via explicit `expose`. It may never be persisted,
//!   logged, queued or shown to model context by any consumer of this port.
//! - ensure authenticates as the BCS service (Provider own credentials); it
//!   never depends on a not-yet-issued execution token and never extends the
//!   Human identity's validity window.
//! - The response only asserts linkage and container readback; it never
//!   grants execution rights, and `ready` never implies history_ready or
//!   MCP authorization.
//! - absence of `binding_ref` never means weakened isolation — it only means
//!   the Gateway chose not to expose a technical reference.
//! - The error set is closed and never carries URLs, response bodies or
//!   opaque refs; a failed or timed-out ensure never proves the container
//!   was not created, so retries must reuse the same idempotency key and
//!   never synthesize a fresh one.

use std::fmt;

use async_trait::async_trait;
use thiserror::Error;

use bcs_domain::RedactedToken;

pub use bcs_domain::caller_identity_material::CallerOpaqueRef;

/// V1 isolation requirements. The value set here is the Bot's minimum
/// requirement; the response reports the Gateway's actual guarantee
/// separately, so a lowered guarantee never passes silently.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallerContainerMinimum {
    CallerContainer,
}

/// The Gateway's actual isolation guarantee for a prepared container.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallerContainerIsolation {
    ContainerBound,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EnsureCreateMode {
    Lazy,
    Warmup,
    MustExist,
}

/// One ensure request. `run`/scope facts bind this container preparation to
/// a real dispatch lineage; they are association-only and never a parent
/// authorization.
#[derive(Debug, Clone)]
pub struct EnsureCallerContainer {
    pub caller_subject_ref: CallerOpaqueRef,
    pub target_bot_id: String,
    pub session_id: String,
    pub environment: String,
    pub minimum_isolation: CallerContainerMinimum,
    /// Stable retry key: same key with different input must fail; must not
    /// embed credentials or identity material.
    pub idempotency_key: String,
    pub run: ContainerRunAssociation,
    pub create_mode: EnsureCreateMode,
    /// Positive container TTL; container lifetime never extends identity.
    pub container_ttl_ms: i64,
    /// Trusted deadline inputs (identity/request/container policy). The
    /// Gateway-provided bounds later shrink the effective window further.
    pub deadline_ms: i64,
    pub now_ms: i64,
}

/// Association of an ensure with one canonical dispatch lineage.
#[derive(Debug, Clone)]
pub struct ContainerRunAssociation {
    pub run_id: String,
    pub root_run_id: String,
    pub parent_run_id: Option<String>,
    pub message_turn_id: String,
    /// Shared-history cutoff seq boundary INHERITED from the parent/root
    /// (spec §6.8): a child never re-derives the ceiling from the current
    /// group watermark at derivation time — lineage binds the boundary, so
    /// later Human messages can never leak into a run's history input.
    /// 0 means "no inherited ceiling" (plain H2A, no shared history).
    pub history_cutoff_seq: u64,
}

/// The Gateway's successful readback. `ready=false` describes a real
/// gateway response, not an error; the application decides whether it
/// satisfies the dispatched policy.
impl Clone for CallerContainerDescriptor {
    /// Clone preserves the opaque refs' redacted boundaries (they are Clone
    /// but non-serializable, expose-only).
    fn clone(&self) -> Self {
        Self {
            sandbox_container_id: self.sandbox_container_id.clone(),
            target_bot_id: self.target_bot_id.clone(),
            caller_subject_ref: self.caller_subject_ref.clone(),
            ready: self.ready,
            isolation: self.isolation,
            binding_ref: self.binding_ref.clone(),
            runtime_generation: self.runtime_generation,
            expires_at_ms: self.expires_at_ms,
            observe_target: self.observe_target.clone(),
        }
    }
}

/// Redacted Debug: the opaque refs and observe target must never print.
impl fmt::Debug for CallerContainerDescriptor {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CallerContainerDescriptor")
            .field("target_bot_id", &self.target_bot_id)
            .field("caller_subject_ref", &"<redacted>")
            .field("ready", &self.ready)
            .field("isolation", &self.isolation)
            .field("binding_ref", &self.binding_ref.as_ref().map(|_| "<redacted>"))
            .field("runtime_generation", &self.runtime_generation)
            .field("expires_at_ms", &self.expires_at_ms)
            .field("observe_target", &self.observe_target.as_ref().map(|_| "<redacted>"))
            .finish()
    }
}

/// Runtime-only observe destination: trusted BCS subject and optional paired
/// Gateway reference/generation from ensure. Subject-routed providers omit
/// those references and resolve the Human×Bot target themselves. Never an
/// execution credential; lives only in
/// the in-memory observe index, never in DB/model/logs.
#[derive(Debug, Clone)]
pub struct CallerObserveTarget {
    /// Trusted routing subject, never an execution credential.
    pub caller_subject: CallerOpaqueRef,
    /// Both reference and epoch are present for ensure routing; both absent
    /// for an explicitly declared caller_subject routing provider.
    pub caller_ref: Option<CallerOpaqueRef>,
    pub target_bot_id: String,
    pub session_id: String,
    /// Opaque observer generation (the Gateway's internal target epoch).
    pub target_epoch: Option<CallerOpaqueRef>,
    pub expires_at_ms: i64,
}

pub struct CallerContainerDescriptor {
    /// Actual target sandbox ID from trusted Gateway readback. Never alias binding_ref.
    pub sandbox_container_id: Option<CallerOpaqueRef>,
    pub target_bot_id: String,
    /// Readback association; must match the request's subject and target —
    /// a mismatch is a corrupted readback, not a new grant.
    pub caller_subject_ref: CallerOpaqueRef,
    pub ready: bool,
    pub isolation: CallerContainerIsolation,
    /// Optional opaque technical reference if the Gateway supports strong
    /// binding; absent never weakens isolation.
    pub binding_ref: Option<CallerOpaqueRef>,
    pub runtime_generation: Option<u32>,
    /// Container/binding validity bound; never extends identity validity.
    pub expires_at_ms: i64,
    /// Group-sync observe target (spec §10.8): present when the Gateway
    /// supports caller group synchronization; absent for pure H2A. Missing
    /// on a capability-declaring Gateway is `OBSERVE_TARGET_UNAVAILABLE`
    /// — never fabricated.
    pub observe_target: Option<CallerObserveTarget>,
}

/// Closed failure set. No variant carries URLs, response bodies or refs.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum CallerContainerError {
    /// The request itself was invalid before any network attempt.
    #[error("invalid ensure request")]
    InvalidRequest,
    /// The Provider/Gateway reported a definite rejection. Whether a retry
    /// could work must be explicit, never guessed by the caller.
    #[error("gateway rejected the ensure request")]
    GatewayRejected { retryable: bool },
    /// Transport or Provider failure, including ambiguous outcomes: this
    /// never proves the container was not created, and the same
    /// idempotency key must be reused on retry.
    #[error("provider gateway unavailable")]
    ProviderUnavailable { retryable: bool },
}

/// Service-authenticated ensure seam. Implementations must:
/// - issue at most one network call per invocation;
/// - return the Bot/subject linkage from the already-bound request, never
///   from a response echo (the frozen wire subset has no echo fields);
/// - keep the container/binding deadline out of the returned descriptor
///   intact (the application takes `min` across identity and container).
#[async_trait]
pub trait CallerContainerGatewayPort: Send + Sync {
    async fn ensure_container(
        &self,
        target: &bcs_domain::BotDeliveryTarget,
        service_credential: &RedactedToken,
        request: EnsureCallerContainer,
    ) -> Result<CallerContainerDescriptor, CallerContainerError>;
}