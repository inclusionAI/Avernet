//! The held index: the unfinished calls of one command partition.
//!
//! A dispatch is listed in its partition (the partition its hints go to)
//! before its first claim, and removed only once it ended or its document is
//! gone. It is shared by every holder the call ever has, so a claim that
//! loses a race, or a former holder, never removes it: the index may list too
//! much, never too little (spec rule B6). The holder recorded in the dispatch
//! document stays the truth; the index only tells the partition's owner which
//! calls to look at when it acquires the partition.
//!
//! One sorted set per partition of a pool:
//!
//! ```text
//! key:    bcn:v1:<cluster>:<ns>:<pool>:p<nnnn>:held
//!         bcn:v1:c01:main:a2a:p0003:held
//! member: dispatch id, e.g. s0042.7f3a9c.b21e.n2.a1
//! score:  time first listed in ms (wall clock), e.g. 1794370950456
//! ```

use crate::shared::Shared;
use bcs_dist_model::{Did, PoolId};
use bcs_dist_store::error::{from_read, from_write};
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::zset::{ZAddMode, ZSetEntry, ZSetRange};
use std::num::NonZeroU32;
use std::ops::Bound;

/// The index key of `did` in `pool`.
fn key(shared: &Shared, pool: &PoolId, did: &Did) -> Result<String, StoreError> {
    let partition = shared.config.streams.partition_of(did);
    shared.layout.partition_held(pool, partition)
}

/// Lists `did`, keeping its first score. Called right before a claim write.
pub(crate) async fn add(shared: &Shared, pool: &PoolId, did: &Did) -> Result<(), StoreError> {
    let key = key(shared, pool, did)?;
    let entry = ZSetEntry {
        member: did.0.clone(),
        score: shared.clock.now().0 as f64,
    };
    shared
        .zset
        .add(&key, &[entry], ZAddMode::InsertOnly)
        .await
        .map(|_| ())
        .map_err(|e| from_write(&e, &did.0))
}

/// Unlists `did`. Called only when the call ended or its document is gone.
pub(crate) async fn remove(shared: &Shared, pool: &PoolId, did: &Did) -> Result<(), StoreError> {
    let key = key(shared, pool, did)?;
    shared
        .zset
        .remove(&key, std::slice::from_ref(&did.0))
        .await
        .map(|_| ())
        .map_err(|e| from_write(&e, &did.0))
}

/// The first `limit` calls listed in one partition, oldest first.
pub(crate) async fn page(
    shared: &Shared,
    pool: &PoolId,
    partition: u32,
    limit: NonZeroU32,
) -> Result<Vec<Did>, StoreError> {
    let key = shared.layout.partition_held(pool, partition)?;
    let range = ZSetRange {
        min: Bound::Unbounded,
        max: Bound::Unbounded,
        after: None,
        limit,
    };
    let entries = shared.zset.range(&key, &range).await.map_err(|e| from_read(&e))?;
    Ok(entries.into_iter().map(|entry| Did(entry.member)).collect())
}
