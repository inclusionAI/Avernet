use crate::*;
use async_trait::async_trait;
use std::{collections::BTreeMap, num::NonZeroU32, ops::Bound, time::Duration};

/// Ordered components, not a real-time timestamp. Explicit IDs can encode a
/// business sequence; conversion to backend cursor syntax belongs to providers.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct StreamId {
    pub major: u64,
    pub minor: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AppendId {
    Auto,
    Explicit(StreamId),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamEntry {
    pub id: StreamId,
    pub fields: BTreeMap<String, Vec<u8>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamRead {
    pub key: String,
    pub after: Option<StreamId>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ReadOptions {
    /// Global maximum across all requested keys, NOT per-key COUNT.
    pub max_entries: NonZeroU32,
    /// None polls; Some must be positive and within provider limits. Cancellation
    /// releases the blocking connection. Plain reads never advance a cursor;
    /// cancelled group reads may already have registered pending delivery.
    pub block: Option<Duration>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamPage {
    pub entries: Vec<StreamEntry>,
    /// Retained last generated ID even if all entries have been trimmed.
    pub last_id: Option<StreamId>,
    /// Highest actually removed prefix ID. Persistent and monotonic. If a saved
    /// cursor is less than this, a required prefix may be lost. Sparse IDs alone
    /// are not evidence of loss. Metadata and entries form ONE per-key read cut.
    pub removed_through: Option<StreamId>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamBatch {
    pub key: String,
    pub page: StreamPage,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrimPolicy {
    /// Exact prefix deletion: remove IDs strictly less than boundary.
    Before(StreamId),
    /// Exact length bound, intended for disposable hints; zero trims all entries.
    MaxLen(u64),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrimResult {
    pub removed: u64,
    pub removed_through: Option<StreamId>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GroupStart {
    Beginning,
    After(StreamId),
    Tail,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GroupCreateOutcome {
    Created,
    AlreadyExists,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClaimPage {
    pub entries: Vec<StreamEntry>,
    /// Removed pending entries cleaned from the group's PEL, not successful work.
    pub deleted: Vec<StreamId>,
    pub next: Option<StreamId>,
}

/// Durable ordered logs. Successful appends survive the provider's documented
/// failover model; no silent eviction/reset/TTL deletion. Trimming preserves head
/// and removed-prefix evidence. Implementations must reject unsupported metadata
/// guarantees instead of returning an empty result that silently hides a gap.
#[async_trait]
pub trait StreamStorePlugin: StorePlugin {
    /// Nonempty fields. IDs strictly advance, including after trim-to-empty.
    /// (0,0) is invalid as an explicit entry ID. Equal/older ID => IdNotIncreasing,
    /// NOT AlreadyApplied. No automatic retry with a new auto-generated ID after
    /// OutcomeUnknown. Caller owns message identity and duplicate classification.
    async fn append(
        &self,
        key: &str,
        id: AppendId,
        fields: &BTreeMap<String, Vec<u8>>,
    ) -> StoreResult<StreamId>;
    /// None cursor reads from earliest retained entry, never tail. Unique, nonempty
    /// keys; return one page per requested key in request order, even if empty.
    /// No cross-key snapshot. Stable ready streams must not be starved by a hot
    /// stream; rotate the bounded read fairly. Cross-partition requests unsupported
    /// by a provider fail before waiting; do not silently drop requested streams.
    async fn read(
        &self,
        streams: &[StreamRead],
        options: ReadOptions,
    ) -> StoreResult<Vec<StreamBatch>>;
    /// Ordered ascending by default; reverse=true reverses the SAME bounded range.
    async fn range(
        &self,
        key: &str,
        start: Bound<StreamId>,
        end: Bound<StreamId>,
        limit: NonZeroU32,
        reverse: bool,
    ) -> StoreResult<StreamPage>;
    /// Deletion and removed-prefix metadata publication are atomic. Coverage checks
    /// for checkpoints/snapshots are the service store's responsibility, not here.
    async fn trim(&self, key: &str, policy: TrimPolicy) -> StoreResult<TrimResult>;
    /// StreamGroups capability. Creates an empty stream if needed. An existing
    /// group is unchanged; retries MUST NOT reset its cursor or pending entries.
    async fn create_group(
        &self,
        key: &str,
        group: &str,
        start: GroupStart,
    ) -> StoreResult<GroupCreateOutcome>;
    /// Delivers new entries and registers pending ownership atomically. Consumer
    /// is an opaque unique process incarnation, not a lease or execution fence.
    /// Timeout/disconnect after possible delivery is OutcomeUnknown (PEL may change).
    async fn read_group(
        &self,
        key: &str,
        group: &str,
        consumer: &str,
        options: ReadOptions,
    ) -> StoreResult<Vec<StreamEntry>>;
    /// Remove pending records only, not stream data. Return newly acknowledged count.
    async fn ack(&self, key: &str, group: &str, ids: &[StreamId]) -> StoreResult<u64>;
    /// Bounded pending scan; claim only entries idle >= min_idle. Zero min_idle is
    /// valid for explicit recovery/tests. start is inclusive, next resumes the scan.
    /// Claims reset idle age. Pending entries whose payload was trimmed are reported
    /// in deleted, never converted to successfully executed effects.
    async fn claim_idle(
        &self,
        key: &str,
        group: &str,
        consumer: &str,
        min_idle: Duration,
        start: Option<StreamId>,
        limit: NonZeroU32,
    ) -> StoreResult<ClaimPage>;
}
