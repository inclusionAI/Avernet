//! The held index: the dispatches one Connector instance holds.
//!
//! A dispatch is added before its claim and removed only after it ended (or
//! when the claim found nothing to hold), so the index may list too much but
//! never misses a call. It only speeds recovery up; the holder recorded in the
//! dispatch document is the truth.

use crate::shared::Shared;
use bcs_dist_model::{Did, Instance};
use bcs_dist_store::error::{from_read, from_write};
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::zset::{ZAddMode, ZSetEntry, ZSetRange};
use std::num::NonZeroU32;
use std::ops::Bound;

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
