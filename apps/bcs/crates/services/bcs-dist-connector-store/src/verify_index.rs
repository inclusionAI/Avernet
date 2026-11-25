//! The verification queue index (plan T7): dispatches handed off to the
//! verification lane, scored by their next due time.
//!
//! The index is a repairable over-approximation of the authoritative state,
//! exactly like the held index: it may list too much (stale or ended entries
//! are dropped at the next claim), never too little — the handoff CAS arms
//! the dispatch's `due` obligation, so a Supervisor scan of the
//! authoritative state repairs a missing entry after any crash.

use crate::shared::Shared;
use bcs_dist_model::{Did, TimestampMillis};
use bcs_dist_store::error::{from_read, from_write};
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::zset::{ZAddMode, ZSetEntry, ZSetRange};
use std::num::NonZeroU32;
use std::ops::Bound;

const PAGE_LIMIT: u32 = 64;

/// Registers `did` as due at `due` (upsert: a repair rewrites the entry,
/// never duplicates it).
pub(crate) async fn add(shared: &Shared, did: &Did, due: TimestampMillis) -> Result<(), StoreError> {
    let key = shared.layout.verify_queue();
    let entry = ZSetEntry {
        member: did.0.clone(),
        score: due.0 as f64,
    };
    shared
        .zset
        .add(&key, &[entry], ZAddMode::Upsert)
        .await
        .map(|_| ())
        .map_err(|e| from_write(&e, &did.0))
}

pub(crate) async fn remove(shared: &Shared, did: &Did) -> Result<(), StoreError> {
    let key = shared.layout.verify_queue();
    shared
        .zset
        .remove(&key, std::slice::from_ref(&did.0))
        .await
        .map(|_| ())
        .map_err(|e| from_write(&e, &did.0))
}

/// One page of dispatches due at or before `now`, oldest first. Pages are
/// bounded and rank-ordered; the caller claims against the authoritative
/// document before anything else.
pub(crate) async fn page(
    shared: &Shared,
    now: TimestampMillis,
    after: Option<ZSetEntry>,
    limit: NonZeroU32,
) -> Result<Vec<(Did, TimestampMillis)>, StoreError> {
    let key = shared.layout.verify_queue();
    let range = ZSetRange {
        min: Bound::Unbounded,
        max: Bound::Included(now.0 as f64),
        after,
        limit,
    };
    let entries = shared.zset.range(&key, &range).await.map_err(|e| from_read(&e))?;
    Ok(entries
        .into_iter()
        .filter_map(|entry| {
            let due = entry.score as i64;
            Some((Did(entry.member), TimestampMillis(due)))
        })
        .collect())
}

/// The page limit of one due scan.
pub(crate) fn limit(want: NonZeroU32) -> NonZeroU32 {
    NonZeroU32::new(want.get().min(PAGE_LIMIT)).unwrap_or(NonZeroU32::MIN)
}
