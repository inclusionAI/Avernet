//! Committed TaskFeed with explicit append evidence and logical retention.
mod append;
mod follow;
mod publish;
mod reader;
mod retention;
mod storage;

use crate::{
    codec::{decode_json, encode_json},
    evidence::{EffectVerifier, completion_identity},
    identity::{digest_fields, digest_json, hex_of},
    layout::KeyLayout,
};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::{
    core::dist_projection::OutputProjectionCoreService,
    port::repo::distributed::{common::*, result::*, session::EffectKind, task_feed::*},
};
use bcs_store_api::{
    StoreCapability, Version, Versioned, WriteOutcome, document::DocumentStorePlugin, stream::*,
    zset::ZSetStorePlugin,
};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use std::{
    collections::BTreeMap,
    num::NonZeroU32,
    ops::Bound,
    sync::Arc,
    time::{Duration, Instant},
};

#[derive(Clone)]
pub struct TaskFeedStores {
    pub documents: Arc<dyn DocumentStorePlugin>,
    pub streams: Arc<dyn StreamStorePlugin>,
    pub index: Arc<dyn ZSetStorePlugin>,
    pub blobs: Arc<dyn BlobStorePort>,
    pub results: Arc<dyn ResultStorePort>,
}

#[derive(Debug, Clone)]
pub struct TaskFeedConfig {
    pub page_entries: u32,
    pub page_bytes: usize,
    pub scan_pages: u32,
    pub scan_duration: Duration,
    pub io_timeout: Duration,
    pub poll_interval: Duration,
    pub max_followers: usize,
}
impl Default for TaskFeedConfig {
    fn default() -> Self {
        Self {
            page_entries: 128,
            page_bytes: 1024 * 1024,
            scan_pages: 32,
            scan_duration: Duration::from_secs(2),
            io_timeout: Duration::from_secs(2),
            poll_interval: Duration::from_millis(250),
            max_followers: 128,
        }
    }
}

#[derive(Clone)]
pub struct StoreTaskFeed {
    stores: TaskFeedStores,
    projection: Arc<dyn OutputProjectionCoreService>,
    verifier: EffectVerifier,
    layout: KeyLayout,
    config: TaskFeedConfig,
    followers: Arc<tokio::sync::Semaphore>,
}
impl StoreTaskFeed {
    pub fn new(
        stores: TaskFeedStores,
        projection: Arc<dyn OutputProjectionCoreService>,
        layout: KeyLayout,
        config: TaskFeedConfig,
    ) -> Result<Self, StoreError> {
        if config.page_entries == 0
            || config.page_entries > 128
            || config.page_bytes < storage::MAX_ENTRY_BYTES
            || config.page_bytes > 1024 * 1024
            || config.scan_pages == 0
            || config.scan_pages > 32
            || config.scan_duration.is_zero()
            || config.scan_duration > Duration::from_secs(2)
            || config.io_timeout.is_zero()
            || config.io_timeout > Duration::from_secs(2)
            || config.poll_interval.is_zero()
            || config.max_followers == 0
            || config.max_followers > 128
        {
            return Err(invalid("task_feed_config"));
        }
        if !stores.documents.supports(StoreCapability::DocumentCas)
            || !stores.streams.supports(StoreCapability::Stream)
            || !stores.index.supports(StoreCapability::ZSet)
        {
            return Err(unavailable("task_feed_capabilities"));
        }
        Ok(Self {
            verifier: EffectVerifier::new(stores.documents.clone(), layout.clone()),
            stores,
            projection,
            layout,
            followers: Arc::new(tokio::sync::Semaphore::new(config.max_followers)),
            config,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct AppendReservation {
    identity: String,
    digest: Digest,
    effect: EffectId,
    previous: Option<EventId>,
    first: EventId,
    last: EventId,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct OutputHead {
    schema_version: u32,
    completed_head: Option<EventId>,
    keep_from: Option<EventId>,
    removed_through: Option<EventId>,
    pending: Option<AppendReservation>,
}
impl Default for OutputHead {
    fn default() -> Self {
        Self {
            schema_version: 1,
            completed_head: None,
            keep_from: None,
            removed_through: None,
            pending: None,
        }
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
enum CompletedOperation {
    Append {
        previous: Option<EventId>,
        first: EventId,
        last: EventId,
    },
    Publish {
        tid: Tid,
        source_commit: CommitSeq,
    },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct Completion {
    schema_version: u32,
    identity: String,
    digest: Digest,
    operation: CompletedOperation,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct TaskRetention {
    schema_version: u32,
    scope: TaskScope,
    sid: Sid,
    removed_through: EventId,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct TrimProgress {
    schema_version: u32,
    keep_from: EventId,
    fixed_head: EventId,
    after: Option<EventId>,
    verified_through: Option<EventId>,
    complete: bool,
}

#[async_trait]
impl TaskPublisherPort for StoreTaskFeed {
    async fn append(&self, request: AppendCommittedEvents) -> Result<AppendOutcome, StoreError> {
        self.append_batch(request).await
    }
    async fn publish(
        &self,
        request: PublishCommittedSnapshot,
    ) -> Result<PublishOutcome, StoreError> {
        self.publish_snapshot(request).await
    }
    async fn trim(&self, request: RetentionRequest) -> Result<TrimOutcome, StoreError> {
        self.trim_output(request).await
    }
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}
fn unavailable(code: &str) -> StoreError {
    StoreError::NotApplied { code: code.into() }
}
fn unknown(operation: &str) -> StoreError {
    StoreError::OutcomeUnknown {
        operation_id: operation.into(),
    }
}
// A definite failure of one later storage call does not undo an earlier write
// in the enclosing effect. Preserve the effect identity for reconciliation.
fn after_write(error: StoreError, operation: &str) -> StoreError {
    match error {
        StoreError::NotApplied { .. } => unknown(operation),
        other => other,
    }
}
fn stream_id(id: EventId) -> StreamId {
    StreamId {
        major: id.commit.0,
        minor: u64::from(id.index.get()),
    }
}
fn event_id(id: StreamId) -> Result<EventId, StoreError> {
    let index = u32::try_from(id.minor)
        .ok()
        .and_then(NonZeroU32::new)
        .ok_or_else(|| invalid("output_event_id"))?;
    if id.major == 0 {
        return Err(invalid("output_event_id"));
    }
    Ok(EventId {
        commit: CommitSeq(id.major),
        index,
    })
}

/// One bounded retry episode, distinct from later scheduling retries.
struct CasBudget {
    started: Instant,
    attempts: u8,
}
impl CasBudget {
    fn new() -> Self {
        Self {
            started: Instant::now(),
            attempts: 0,
        }
    }
    async fn attempt(&mut self, operation: &str) -> Result<(), StoreError> {
        if self.attempts >= 8 || self.started.elapsed() >= Duration::from_millis(500) {
            return Err(unknown(operation));
        }
        if self.attempts > 0 {
            let remaining = Duration::from_millis(500).saturating_sub(self.started.elapsed());
            tokio::time::sleep(
                Duration::from_millis((5u64 << (self.attempts - 1)).min(200)).min(remaining),
            )
            .await;
        }
        self.attempts += 1;
        if self.started.elapsed() >= Duration::from_millis(500) {
            return Err(unknown(operation));
        }
        Ok(())
    }
    async fn io<T>(
        &self,
        operation: &str,
        future: impl Future<Output = Result<T, StoreError>>,
    ) -> Result<T, StoreError> {
        let remaining = Duration::from_millis(500).saturating_sub(self.started.elapsed());
        if remaining.is_zero() {
            return Err(unknown(operation));
        }
        tokio::time::timeout(remaining, future)
            .await
            .map_err(|_| unknown(operation))?
            .map_err(|error| after_write(error, operation))
    }
}
