//! The held index: the dispatches one Connector instance holds.
//!
//! A dispatch is added before its claim and removed only after it ended (or
//! when the claim found nothing to hold), so the index may list too much but
//! never misses a call. It only speeds recovery up; the holder recorded in the
//! dispatch document is the truth.
//!
//! Spec rule B6: listed from before the claim until after the call ended.
//! Removing too early could hide a call from recovery; removing too late (or
//! never, after a failed removal) only costs a wasted look at an ended call.
//!
//! One sorted set per Connector process run:
//!
//! ```text
//! key:    bcn:v1:held:<cluster>:<ns>:<node>:<incarnation>
//!         bcn:v1:held:c01:main:conn-03:1794370950
//! member: dispatch id, e.g. s0042.7f3a9c.b21e.n2.a1
//! score:  claim time in ms (wall clock), e.g. 1794370950456
//! ```
//!
//! Who reads it: nothing in production today; `StoreDispatchQueue::held_by`
//! pages it. A dead Connector's calls are recovered without it: the Worker's
//! supervisor re-posts their hints and a takeover follows once membership
//! answers `Dead`.

use crate::shared::Shared;
use bcs_dist_model::{Did, Instance};
use bcs_dist_store::error::{from_read, from_write};
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::zset::{ZAddMode, ZSetEntry, ZSetRange};
use std::num::NonZeroU32;
use std::ops::Bound;

/// Lists `did` under `me`, with the current time as score. Called right
/// before a claim write; re-adding an existing member only moves its score.
pub(crate) async fn add(shared: &Shared, me: &Instance, did: &Did) -> Result<(), StoreError> {
    let key = shared.layout.held(me)?;
    let entry = ZSetEntry {
        member: did.0.clone(),
        score: shared.clock.now().0 as f64,
    };
    shared
        .zset
        .add(&key, &[entry], ZAddMode::Upsert)
        .await
        .map(|_| ())
        .map_err(|e| from_write(&e, &did.0))
}

/// Unlists `did` from `me`. Called after the call ended, or when a claim
/// found nothing to hold. The count of removed members is ignored.
pub(crate) async fn remove(shared: &Shared, me: &Instance, did: &Did) -> Result<(), StoreError> {
    let key = shared.layout.held(me)?;
    shared
        .zset
        .remove(&key, std::slice::from_ref(&did.0))
        .await
        .map(|_| ())
        .map_err(|e| from_write(&e, &did.0))
}

/// One page of an instance's held dispatches, oldest claim first; pass the
/// last entry of a page back as `after` to continue.
pub(crate) async fn page(
    shared: &Shared,
    instance: &Instance,
    after: Option<ZSetEntry>,
    limit: NonZeroU32,
) -> Result<Vec<ZSetEntry>, StoreError> {
    let key = shared.layout.held(instance)?;
    let range = ZSetRange {
        min: Bound::Unbounded,
        max: Bound::Unbounded,
        after,
        limit,
    };
    shared.zset.range(&key, &range).await.map_err(|e| from_read(&e))
}
