//! Pure display projection shared by Worker candidates and TaskFeed recovery.
use bcs_dist_model::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OutputBudget {
    Actions,
    Artifacts,
    IdentityBytes,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ProjectionError {
    #[error("unsupported display format: {format}")]
    UnsupportedFormat { format: u32 },
    #[error("invalid frozen output limits: {code}")]
    InvalidLimits { code: &'static str },
    #[error("invalid persisted display: {code}")]
    InvalidSnapshot { code: &'static str },
    #[error("invalid artifact operation: {code}")]
    InvalidArtifact { code: &'static str },
    #[error("unresolved output reference")]
    UnresolvedReference,
    #[error("output quota exceeded: {budget:?}")]
    OutputQuotaExceeded { budget: OutputBudget },
    #[error("frozen task output limits changed")]
    FrozenLimitsChanged,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OutputProjection {
    pub snapshot: DisplaySnapshot,
    pub usage: OutputUsage,
    pub display_events: u32,
    /// Encoded logical payload bytes observed by this candidate. Publication's
    /// byte threshold separately counts the complete encoded output events.
    pub charged_bytes: u64,
}

/// No IO, clock, random identity or hidden mutable state. Inputs must have their
/// immutable references resolved and verified. Errors preserve every input.
pub trait OutputProjectionCoreService: Send + Sync {
    fn validate_limits(&self, limits: &OutputLimits) -> Result<(), ProjectionError>;
    /// Validate bounded frozen metadata repeated in checkpoint/effect envelopes.
    /// Physical framing is shared across tasks; metadata/count budgets may be reduced.
    fn validate_session_limits(
        &self,
        spec: &SessionSpec,
        limits: &OutputLimits,
    ) -> Result<(), ProjectionError>;
    /// Used at admission/acquisition/publication to reject replacement of a
    /// task's frozen profile. This does not establish scope or write authority.
    fn validate_task_limits(
        &self,
        snapshot: &TaskSnapshot,
        frozen: &OutputLimits,
    ) -> Result<(), ProjectionError>;
    /// New uncommitted changes only: charge full Upsert values / AppendText deltas
    /// once in the candidate usage. Caller commits usage with state and consumed.
    /// Keep the candidate on an unknown commit; do not reapply to confirmed state.
    fn project_candidate(
        &self,
        current: &DisplaySnapshot,
        usage: OutputUsage,
        changes: &[ArtifactChange],
        limits: &OutputLimits,
    ) -> Result<OutputProjection, ProjectionError>;
    /// Read-side replay of already committed events. Reuse the same transform and
    /// format/identity/count checks, without counting cumulative usage again.
    /// The caller selects events strictly after snapshot coverage and deduplicates
    /// stable event identities; repeated AppendText is not intrinsically idempotent.
    fn fold_committed(
        &self,
        current: &DisplaySnapshot,
        changes: &[ArtifactChange],
        limits: &OutputLimits,
    ) -> Result<DisplaySnapshot, ProjectionError>;
}
