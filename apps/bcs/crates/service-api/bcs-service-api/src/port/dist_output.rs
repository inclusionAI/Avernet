//! Pure, replaceable output formatting over the Worker's neutral display model.
//!
//! The Worker and TaskFeed persist `DisplaySnapshot` and `ArtifactChange` data,
//! never a delivery protocol's DTOs. A read boundary resolves immutable payloads
//! before invoking this port. Implementations return opaque protocol JSON; the
//! delivery adapter supplies its task envelope and transport framing.

use bcs_dist_model::*;
use serde_json::Value;
use async_trait::async_trait;
use futures::stream::BoxStream;
use crate::port::repo::distributed::common::{Page, PageRequest, StoreError};
use crate::port::repo::distributed::task_feed::{RestoredTask, ResumeCheck, TaskFilter};

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("output formatting failed: {code}")]
pub struct OutputAdapterError {
    pub code: &'static str,
}

pub trait DistOutputAdapterPort: Send + Sync {
    /// A resolved DisplaySnapshot JSON value. Explicit message sources, if
    /// supplied alongside that display, must be preserved without fabrication.
    fn snapshot(&self, snapshot: &Value) -> Result<Value, OutputAdapterError>;
    /// All payloads must be resolved Inline values. Stable neutral identity and
    /// Upsert/AppendText semantics must survive formatting.
    fn artifact(&self, change: &ArtifactChange) -> Result<Value, OutputAdapterError>;
}

/// Read results after protocol formatting. These types are never persisted and
/// cannot be folded as neutral Worker events or charged against output usage.
#[derive(Debug, Clone, PartialEq)]
pub struct FormattedOutputEvent {
    pub id: EventId,
    pub tid: Tid,
    pub body: FormattedOutputBody,
}

#[derive(Debug, Clone, PartialEq)]
pub enum FormattedOutputBody {
    Status { status: TaskStatus, interactions: Vec<Interaction> },
    Artifact { content: Value },
    Terminal { status: TerminalStatus, result_ref: BlobRef },
}

#[derive(Debug, Clone, PartialEq)]
pub enum FormattedFollowItem {
    Events { events: Vec<FormattedOutputEvent>, next: TaskCursor },
    Gap,
}

/// Worker-side read facade over neutral committed output. It resolves immutable
/// references and applies one injected formatter to both full views and deltas.
/// The Gateway forwards these already formatted results without interpreting
/// display versions, artifacts or append operations. It never persists them.
#[async_trait]
pub trait DistTaskOutputReaderPort: Send + Sync {
    async fn restore(&self, scope: &TaskScope, tid: &Tid) -> Result<Option<RestoredTask>, StoreError>;
    async fn validate_resume(&self, scope: &TaskScope, cursor: &TaskCursor) -> Result<ResumeCheck, StoreError>;
    fn follow(&self, scope: TaskScope, after: TaskCursor) -> BoxStream<'static, Result<FormattedFollowItem, StoreError>>;
    /// Inline snapshots are formatted; unresolved list references remain opaque
    /// and are not fetched. This adds no per-task blob reads to a list request.
    async fn list(&self, scope: &TaskScope, filter: TaskFilter, page: PageRequest) -> Result<Page<TaskView>, StoreError>;
}
