//! Direct Chat async run repository contract.
//!
//! `ChatRunRecord` and its supporting enums are the auditable lifecycle
//! record for a Direct Chat async run (`POST /bots/{id}/chat-async` →
//! `GET /chat/runs/{run_id}`). The trait below is a thin persistence +
//! compare-and-set + scan port: it deliberately does not own the run state
//! machine. Terminal-guard and transition rules ("only Pending → Running",
//! "terminal is immutable") live in the `ChatRunStore` engine as a single
//! source of truth, so the in-memory and SQL implementations never duplicate
//! that behavior.
//!
//! See `docs/superpowers/specs/2026-08-27-bcs-run-governance-design.md` for
//! the field-level authority split (MySQL authoritative + Redis hot cache)
//! and the combined boundary with `BotRunContextPort`.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::{ChatResponseMode, ChatRunMetricCount, DirectChatClientKind};

/// Maximum accumulated content kept for a single run (1 MiB). Slicing past
/// this uses char-boundary-safe truncation; see `ChatRunStore::append_delta`.
pub const MAX_CONTENT_BYTES: usize = 1_024 * 1_024;

/// Run lifecycle state. Serialized lowercase for the HTTP wire contract and
/// for SQL storage.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ChatRunState {
    Pending,
    Submitted,
    Running,
    Completed,
    Failed,
    Cancelled,
}

impl ChatRunState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Submitted => "submitted",
            Self::Running => "running",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }

    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Failed | Self::Cancelled)
    }
}

/// How a run concludes relative to provider delivery acknowledgement.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ChatRunCompletionPolicy {
    WaitForFinal,
    DetachDeliveryAck,
}

/// Authoritative record for one Direct Chat async run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChatRunRecord {
    pub run_id: String,
    pub bot_uuid: String,
    pub from_bot_id: String,
    pub session_key: String,
    pub state: ChatRunState,
    pub accumulated_content: String,
    pub error_message: Option<String>,
    pub created_at_ms: u64,
    pub updated_at_ms: u64,
    pub completed_at_ms: Option<u64>,
    pub expires_at_ms: u64,
    pub version: u64,
    pub content_truncated: bool,
    pub client: Option<String>,
    pub response_mode: ChatResponseMode,
    #[serde(skip_serializing, default = "default_completion_policy")]
    pub completion_policy: ChatRunCompletionPolicy,
    #[serde(skip_serializing, default)]
    pub delivery_ack_at_ms: Option<u64>,
}

fn default_completion_policy() -> ChatRunCompletionPolicy {
    ChatRunCompletionPolicy::WaitForFinal
}

impl ChatRunRecord {
    pub fn new(
        run_id: String,
        bot_uuid: String,
        from_bot_id: String,
        session_key: String,
        now_ms: u64,
        expires_at_ms: u64,
        client: Option<String>,
        response_mode: ChatResponseMode,
        completion_policy: ChatRunCompletionPolicy,
    ) -> Self {
        Self {
            run_id,
            bot_uuid,
            from_bot_id,
            session_key,
            state: ChatRunState::Pending,
            accumulated_content: String::new(),
            error_message: None,
            created_at_ms: now_ms,
            updated_at_ms: now_ms,
            completed_at_ms: None,
            expires_at_ms,
            version: 1,
            content_truncated: false,
            client,
            response_mode,
            completion_policy,
            delivery_ack_at_ms: None,
        }
    }
}

/// Outcome of a compare-and-set write against the run record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CasOutcome {
    /// The new record was applied; carries the updated record (version bumped).
    Applied(ChatRunRecord),
    /// The stored version did not match `expected_version`. Carries the
    /// current stored record (if present) so the caller can retry.
    Conflict(Option<ChatRunRecord>),
    /// The run is already terminal; the transition was rejected. Carries the
    /// current stored record (if present).
    Terminal(Option<ChatRunRecord>),
}

/// Repository error. `Backend` MUST be propagated by callers; issue #1546
/// forbids converting a persistence failure into a successful API response.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ChatRunRepoError {
    #[error("chat run {0} already exists")]
    DuplicateRunId(String),
    #[error("chat run not found")]
    NotFound,
    #[error("chat run store at capacity ({max_entries})")]
    Capacity { max_entries: usize },
    #[error("chat run store backend error: {0}")]
    Backend(String),
}

/// Thin persistence port for Direct Chat async runs.
///
/// Field-level authority (see spec): state/version/timestamps/terminal content
/// are authoritative in the implementation's primary store (MySQL for
/// `SqlChatRunRepo`); streaming-in-progress `accumulated_content` may live in
/// a hot cache. The engine (`ChatRunStore`) decides WHICH method to call:
/// `compare_and_set_state` for state transitions, `append_streaming_content`
/// for streaming deltas, `compare_and_set_terminal` for the final transition.
#[async_trait]
pub trait ChatRunRepoPort: Send + Sync + 'static {
    async fn create(&self, record: ChatRunRecord) -> Result<(), ChatRunRepoError>;

    async fn get(&self, run_id: &str) -> Result<Option<ChatRunRecord>, ChatRunRepoError>;

    /// Atomically apply a state transition: only succeeds when the stored
    /// version equals `expected_version` and the stored state is non-terminal.
    /// The implementation bumps `version` and returns the applied record.
    async fn compare_and_set_state(
        &self,
        run_id: &str,
        expected_version: u64,
        new: ChatRunRecord,
    ) -> Result<CasOutcome, ChatRunRepoError>;

    /// Atomically apply a terminal transition (state + final content + error
    /// + completion timestamp) under the same guard as `compare_and_set_state`.
    async fn compare_and_set_terminal(
        &self,
        run_id: &str,
        expected_version: u64,
        new: ChatRunRecord,
    ) -> Result<CasOutcome, ChatRunRepoError>;

    /// Hot-update streaming accumulated content. Only needs to be atomic
    /// against the caller's `expected_version` for the version bump; a
    /// conflict returns `Ok(false)` rather than an error. Implementations may
    /// keep this in a hot cache without touching the primary store per delta.
    async fn append_streaming_content(
        &self,
        run_id: &str,
        expected_version: u64,
        accumulated: String,
        truncated: bool,
    ) -> Result<bool, ChatRunRepoError>;

    /// Scan active (non-terminal) runs whose `expires_at_ms < now_ms`, for the
    /// timeout sweep that marks overdue runs failed.
    async fn list_active(&self, now_ms: u64) -> Result<Vec<ChatRunRecord>, ChatRunRepoError>;

    /// Delete terminal runs past their retention window. Returns the run IDs that
    /// were actually removed, so the engine can emit per-run lifecycle events.
    async fn delete_expired_terminal(
        &self,
        now_ms: u64,
        retention_ms: u64,
    ) -> Result<Vec<String>, ChatRunRepoError>;

    /// Aggregate counts by state × client kind for metrics.
    async fn metric_counts(&self) -> Result<Vec<ChatRunMetricCount>, ChatRunRepoError>;

    /// Scan every run's `run_id → client kind` mapping. Used by the cleanup
    /// loop to attribute lifecycle events to the right client after a run may
    /// already have been deleted. Sub-identity (not aggregated) on purpose.
    async fn list_client_kinds(
        &self,
    ) -> Result<std::collections::HashMap<String, DirectChatClientKind>, ChatRunRepoError>;
}