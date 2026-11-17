use super::common::*;
use async_trait::async_trait;
use bcs_dist_model::*;
use futures::stream::BoxStream;

#[derive(Debug, Clone, PartialEq)]
pub struct AppendCommittedEvents {
    pub source: EffectSource,
    pub previous: Option<EventId>,
    pub events: Vec<OutputEvent>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AppendOutcome {
    Appended,
    AlreadyApplied,
    Compacted(CompletionCertificate),
    ContentConflict,
    OutOfOrder,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PublishCommittedSnapshot {
    pub source: EffectSource,
    pub snapshot: TaskSnapshot,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PublishOutcome {
    Published,
    AlreadyApplied,
    Superseded { source_commit: CommitSeq },
    ContentConflict,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RetentionRequest {
    pub sid: Sid,
    pub keep_from: EventId,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TrimOutcome {
    Trimmed,
    AlreadyTrimmed,
    NotCovered,
}

#[async_trait]
pub trait TaskPublisherPort: Send + Sync {
    /// Older ID alone is NOT AlreadyApplied. Partial batches resume in order;
    /// compacted success needs evidence of a previously completed batch.
    async fn append(&self, request: AppendCommittedEvents) -> Result<AppendOutcome, StoreError>;
    /// Atomically publish state/content/coverage, ordered by ORIGINAL source commit.
    async fn publish(
        &self,
        request: PublishCommittedSnapshot,
    ) -> Result<PublishOutcome, StoreError>;
    /// Verify durable snapshot coverage and earlier task archives before deleting.
    /// Provider owns this race-safe check; caller's requested boundary is not proof.
    /// Preserve a valid boundary/tombstone and required replay evidence.
    async fn trim(&self, request: RetentionRequest) -> Result<TrimOutcome, StoreError>;
}

#[derive(Debug, Clone, PartialEq)]
pub enum RestoredTask {
    Final(TaskView),
    Live { view: TaskView, cursor: TaskCursor },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ResumeCheck {
    Valid,
    Gap,
    InvalidScope,
}

#[derive(Debug, Clone, PartialEq)]
pub enum FollowItem {
    Events {
        items: Vec<OutputEvent>,
        next: TaskCursor,
    },
    Gap,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskFilter {
    pub sid: Option<Sid>,
    pub statuses: Vec<TaskStatus>,
}

#[async_trait]
pub trait TaskReaderPort: Send + Sync {
    /// Fold snapshot S and events (S,H] into one consistent view. Retry concurrent
    /// trimming; handle null initial coverage without silently skipping first events.
    async fn restore(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<RestoredTask>, StoreError>;
    async fn validate_resume(
        &self,
        scope: &TaskScope,
        cursor: &TaskCursor,
    ) -> Result<ResumeCheck, StoreError>;
    /// Validate gaps at delivery time, not only before the read. No per-event task read.
    fn follow(
        &self,
        scope: TaskScope,
        after: TaskCursor,
    ) -> BoxStream<'static, Result<FollowItem, StoreError>>;
    /// Includes live tasks and archived terminals, with bounded authorized paging.
    async fn list(
        &self,
        scope: &TaskScope,
        filter: TaskFilter,
        page: PageRequest,
    ) -> Result<Page<TaskView>, StoreError>;
}
