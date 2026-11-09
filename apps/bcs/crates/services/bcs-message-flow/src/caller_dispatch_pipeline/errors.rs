//! Structured errors of the protected caller dispatch pipeline.
//!
//! Lives in its own submodule (not inline in `caller_dispatch_pipeline.rs`)
//! so the stage-liveness wraps of spec 2026-11-03 can land in the pipeline
//! while the file stays below the 1000-line CI limit. The `pub use` in the
//! parent module keeps the historical
//! `caller_dispatch_pipeline::CallerDispatchError` path stable.

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum CallerDispatchError {
    /// No identity parked for this scope (admission never published, or
    /// already claimed/expired). Fail closed; never owner-dispatch.
    #[error("caller identity unavailable")]
    IdentityUnavailable,
    /// Container preparation refused (not ready, linkage corrupt, isolation
    /// below requirement). Identity already consumed by the claim.
    #[error("caller container preparation failed")]
    PreparationFailed,
    /// The identity plugin refused to issue (gate: bad input, expired
    /// subject, misconfigured keys).
    #[error("caller identity plugin refused to issue")]
    IssueFailed,
    /// Structural input invalid (attempt 0, empty facts, inverted clocks).
    #[error("invalid caller dispatch input")]
    InvalidInput,
    /// The identity plugin refused the subject establishment (the
    /// ingress→trusted-subject exchange failed — trusted ingress refused,
    /// a misconfigured profile, or a trust window that already ended).
    /// Identity already consumed by the claim — the human re-submits.
    #[error("caller subject establishment failed")]
    EstablishFailed,
    /// The typed `commit_caller_dispatch` CAS refused the canonical digest
    /// (association drift, stale version, status divergence). Network has
    /// already been touched at preparation, so callers must converge this
    /// conservatively; the one-shot identity is consumed.
    #[error("caller dispatch commit refused")]
    CommitFailed,
    /// A stage exceeded its per-stage liveness budget (spec
    /// 2026-11-03). The collaborator may hold partial state — the claim
    /// may be consumed, the ensure may be mid-flight — so callers must
    /// converge conservatively (Failed), never as a provable not-sent.
    /// Distinct from `IdentityUnavailable`, which asserts a provable
    /// claim refusal.
    #[error("caller dispatch stage timed out at {stage}")]
    StageTimeout { stage: &'static str },
}
