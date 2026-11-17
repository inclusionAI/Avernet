use crate::*;
use async_trait::async_trait;
use std::{num::NonZeroU32, ops::Bound};

#[derive(Debug, Clone, PartialEq)]
pub struct ZSetEntry {
    pub member: String,
    /// Real score, independent of logical version. +/- infinity allowed; NaN invalid.
    pub score: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ZAddMode {
    Upsert,
    InsertOnly,
    UpdateOnly,
    Lower,
    Higher,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ZSetRange {
    pub min: Bound<f64>,
    pub max: Bound<f64>,
    /// Exclusive (score, UTF-8 member bytes) continuation, never a mutable offset.
    pub after: Option<ZSetEntry>,
    pub limit: NonZeroU32,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ZSetMutation {
    Put { entry: ZSetEntry, mode: ZAddMode },
    Remove { member: String },
}

#[derive(Debug, Clone, PartialEq)]
pub struct ZSetSnapshot {
    /// None only if this key has never had a version. Empty sets retain Some.
    pub version: Option<Version>,
    pub entries: Vec<ZSetEntry>,
}

/// Scores sort ascending, ties by member bytes. Pages are individually consistent
/// but concurrent score changes can move entries between pages; a full enumeration
/// needs a higher-level reconciliation protocol. No scan may silently exceed limit.
#[async_trait]
pub trait ZSetStorePlugin: StorePlugin {
    /// One nonempty batch; duplicate members/NaN invalidate the entire request.
    /// Lower/Higher also insert missing members. Count changed members only.
    async fn add(&self, key: &str, entries: &[ZSetEntry], mode: ZAddMode) -> StoreResult<u64>;
    async fn remove(&self, key: &str, members: &[String]) -> StoreResult<u64>;
    async fn range(&self, key: &str, range: &ZSetRange) -> StoreResult<Vec<ZSetEntry>>;
    /// VersionedZSet. Whole-collection version and page from the SAME read.
    async fn snapshot(&self, key: &str, range: &ZSetRange) -> StoreResult<ZSetSnapshot>;
    /// VersionedZSet. None expects no version history, not merely an empty set.
    /// Atomic exact collection-version check + all ordered mutations + version
    /// advance. Each accepted nonempty batch advances even if scores do not change
    /// or removals find nothing. Conflicts do nothing; re-read/re-decide before retry.
    /// Empty batches/duplicate members/invalid scores fail before any mutation.
    /// Ordinary add/remove MUST also invalidate tokens when VersionedZSet is
    /// advertised, including no-op writes. Removing the last member keeps history.
    async fn update_if_version(
        &self,
        key: &str,
        expected: Option<Version>,
        mutations: &[ZSetMutation],
    ) -> StoreResult<WriteOutcome>;
}
