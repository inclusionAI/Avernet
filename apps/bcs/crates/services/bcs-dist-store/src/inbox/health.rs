//! A persisted progress counter is not a clock. Health requires a complete fixed
//! round whose start is locally known, or observing a new such proof after a
//! recent local observation. Cold producers fail closed until progress is seen.
use super::{StoreInboxProducer, state::JournalRoot, storage::*};
use bcs_dist_model::ShardId;
use bcs_service_api::port::repo::distributed::common::StoreError;
use std::{
    collections::BTreeMap,
    time::{Duration, Instant},
};

pub(super) struct ShardHealth {
    counters: [u64; 2],
    confirmed: [Option<Instant>; 2],
    rounds: [Option<(u64, Instant)>; 2],
    observed: Instant,
    known_exists: bool,
}
pub(super) type Health = BTreeMap<ShardId, ShardHealth>;
impl ShardHealth {
    fn new(root: &JournalRoot, fresh: bool) -> Self {
        let now = Instant::now();
        Self {
            counters: [root.repair_health, root.discovery_health],
            confirmed: if fresh { [Some(now); 2] } else { [None; 2] },
            rounds: [None; 2],
            observed: now,
            known_exists: !fresh,
        }
    }
}
impl StoreInboxProducer {
    pub(super) fn check_health(
        &self,
        shard: ShardId,
        root: &JournalRoot,
        fresh: bool,
    ) -> Result<(), StoreError> {
        let now = Instant::now();
        let limit = self.inner.config.maintenance_stale_after;
        let counters = [root.repair_health, root.discovery_health];
        let ages = [
            root.repair_health_age_millis,
            root.discovery_health_age_millis,
        ];
        let mut cache = self
            .inner
            .health
            .lock()
            .map_err(|_| invalid("health_lock"))?;
        if !cache.contains_key(&shard) {
            evict_if_full(&mut cache, self.inner.config.max_readers);
            cache.insert(shard, ShardHealth::new(root, fresh));
        }
        let state = cache
            .get_mut(&shard)
            .ok_or_else(|| invalid("health_missing"))?;
        if fresh && state.known_exists {
            return Err(invalid("journal_root_lost"));
        }
        if fresh {
            state.confirmed = [Some(now); 2];
            state.observed = now;
        } else {
            state.known_exists = true;
        }
        if counters
            .iter()
            .zip(state.counters)
            .any(|(next, old)| *next < old)
        {
            return Err(invalid("maintenance_history_rollback"));
        }
        if now.duration_since(state.observed) >= limit {
            state.confirmed = [None; 2];
            state.counters = counters;
        }
        for (index, counter) in counters.iter().copied().enumerate() {
            if counter > state.counters[index] {
                // Completion happened after our preceding read. Subtract the
                // publishing worker's measured whole-round duration, preserving
                // a conservative lower bound on the round's start time.
                state.confirmed[index] = state
                    .observed
                    .checked_sub(Duration::from_millis(ages[index]));
            }
        }
        state.counters = counters;
        state.observed = now;
        if state
            .confirmed
            .iter()
            .any(|seen| seen.is_none_or(|at| now.duration_since(at) >= limit))
        {
            return Err(pressure());
        }
        if state
            .confirmed
            .iter()
            .any(|seen| seen.is_some_and(|at| now.duration_since(at) >= limit / 2))
        {
            tracing::warn!(shard = shard.0, "admission maintenance progress is stale");
        }
        Ok(())
    }
    pub(super) fn root_confirmed(&self, shard: ShardId) -> Result<(), StoreError> {
        if let Some(state) = self
            .inner
            .health
            .lock()
            .map_err(|_| invalid("health_lock"))?
            .get_mut(&shard)
        {
            state.known_exists = true;
        }
        Ok(())
    }
    pub(super) fn begin_health_round(
        &self,
        shard: ShardId,
        root: &JournalRoot,
        discovery: bool,
        round: u64,
    ) -> Result<(), StoreError> {
        let mut cache = self
            .inner
            .health
            .lock()
            .map_err(|_| invalid("health_lock"))?;
        if !cache.contains_key(&shard) {
            evict_if_full(&mut cache, self.inner.config.max_readers);
            cache.insert(shard, ShardHealth::new(root, root.next == 0));
        }
        let previous = &mut cache
            .get_mut(&shard)
            .ok_or_else(|| invalid("health_missing"))?
            .rounds[usize::from(discovery)];
        // A losing concurrent scanner must not make an already-started fixed
        // round appear younger. Older resumptions cannot replace newer evidence.
        if previous.is_none_or(|(id, _)| id < round) {
            *previous = Some((round, Instant::now()));
        }
        Ok(())
    }
    pub(super) fn health_round_start(
        &self,
        shard: ShardId,
        discovery: bool,
        round: u64,
    ) -> Result<Option<Instant>, StoreError> {
        let cache = self
            .inner
            .health
            .lock()
            .map_err(|_| invalid("health_lock"))?;
        Ok(cache
            .get(&shard)
            .and_then(|h| h.rounds[usize::from(discovery)])
            .filter(|(id, at)| {
                *id == round && at.elapsed() < self.inner.config.maintenance_stale_after
            })
            .map(|(_, at)| at))
    }
    pub(super) fn confirm_health(
        &self,
        shard: ShardId,
        root: &JournalRoot,
        discovery: bool,
        started: Instant,
    ) -> Result<(), StoreError> {
        let mut cache = self
            .inner
            .health
            .lock()
            .map_err(|_| invalid("health_lock"))?;
        let state = cache
            .get_mut(&shard)
            .ok_or_else(|| invalid("health_missing"))?;
        let index = usize::from(discovery);
        state.counters[index] = if discovery {
            root.discovery_health
        } else {
            root.repair_health
        };
        state.confirmed[index] = Some(started);
        state.observed = Instant::now();
        state.known_exists = true;
        Ok(())
    }
}

/// Local evidence is reconstructible; eviction drops trust rather than retaining
/// authority. A reassigned shard can always establish a new complete scan round.
fn evict_if_full(cache: &mut Health, capacity: usize) {
    if cache.len() >= capacity
        && let Some(oldest) = cache
            .iter()
            .min_by_key(|(_, h)| h.observed)
            .map(|(sid, _)| *sid)
    {
        cache.remove(&oldest);
    }
}

#[cfg(test)]
#[allow(clippy::expect_used, reason = "test assertions")]
mod tests {
    use super::*;
    use crate::{
        inbox::{InboxProducerConfig, SystemClock},
        layout::KeyLayout,
    };
    use bcs_dist_model::BackendId;
    use bcs_test_support::store_model::Model;
    use std::sync::Arc;

    #[test]
    fn concurrent_same_round_preserves_earliest_start() {
        let store = Arc::new(Model::default());
        let p = StoreInboxProducer::new(
            store.clone(),
            store.clone(),
            store.clone(),
            store,
            KeyLayout::new("test", "health").expect("layout"),
            Arc::new(SystemClock),
            InboxProducerConfig::new(BackendId("model".into())),
        )
        .expect("provider");
        let root = JournalRoot::default();
        let shard = ShardId(42);
        p.begin_health_round(shard, &root, false, 1).expect("start");
        let first = p.health_round_start(shard, false, 1).expect("read");
        p.begin_health_round(shard, &root, false, 1)
            .expect("concurrent start");
        assert_eq!(p.health_round_start(shard, false, 1).expect("read"), first);
    }
}
