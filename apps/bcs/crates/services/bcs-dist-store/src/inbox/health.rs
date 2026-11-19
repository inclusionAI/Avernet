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
    capacity_warned_at: Option<Instant>,
}
pub(super) type Health = BTreeMap<ShardId, ShardHealth>;
/// Diagnostic observations are not admission authorization or a cross-key cut.
/// None means unavailable local evidence; it must not be graphed as zero age.
#[derive(Debug, Clone)]
pub struct MaintenanceObservation {
    pub retained_entries: Option<u64>,
    pub retained_bytes: Option<u64>,
    pub retained_sessions: Option<u64>,
    pub repair_backlog: Option<u64>,
    pub repair_round_age: Option<Duration>,
    pub discovery_round_age: Option<Duration>,
    pub repair_confirmed_age: Option<Duration>,
    pub discovery_confirmed_age: Option<Duration>,
    pub capacity: Option<super::RetentionCapacityObservation>,
}
impl ShardHealth {
    fn new(root: &JournalRoot, fresh: bool) -> Self {
        let now = Instant::now();
        Self {
            counters: [root.repair_health, root.discovery_health],
            confirmed: if fresh { [Some(now); 2] } else { [None; 2] },
            rounds: [None; 2],
            observed: now,
            known_exists: !fresh,
            capacity_warned_at: None,
        }
    }
    fn warn_capacity(
        &mut self,
        shard: ShardId,
        root: &JournalRoot,
        config: &super::InboxProducerConfig,
        now: Instant,
    ) {
        if super::capacity::shard_near_capacity(root, config)
            && self
                .capacity_warned_at
                .is_none_or(|at| now.duration_since(at) >= Duration::from_secs(60))
        {
            self.capacity_warned_at = Some(now);
            tracing::warn!(
                shard = shard.0,
                charged_entries = root.entries,
                entry_limit = config.max_history_entries,
                charged_bytes = root.bytes,
                byte_limit = config.max_history_bytes,
                normal_entries = root.normal_entries,
                normal_bytes = root.normal_bytes,
                session_reservations = root.sessions,
                distinct_sessions = root.inbox.len(),
                session_limit = config.max_sessions,
                "Inbox lifetime capacity is nearly exhausted; coordinate profile expansion before reserves are consumed"
            );
        }
    }
}
impl StoreInboxProducer {
    /// Two bounded authority reads, on demand. This does not refresh health or
    /// derive monotonic age from the persisted business clock.
    pub async fn maintenance_observation(
        &self,
        shard: ShardId,
    ) -> Result<MaintenanceObservation, StoreError> {
        let root = self
            .load::<JournalRoot>(&self.inner.layout.admission_root(shard))
            .await?
            .map(|(_, r)| r);
        let scan = self
            .load::<super::state::Scan>(&self.inner.layout.admission_scan(shard, "repair")?)
            .await?
            .map(|(_, s)| s);
        let now = Instant::now();
        let health = self
            .inner
            .health
            .lock()
            .map_err(|_| invalid("health_lock"))?;
        let health = health.get(&shard);
        let round_age = |index: usize| {
            health
                .and_then(|h| h.rounds[index])
                .map(|(_, at)| now.duration_since(at))
        };
        let confirmed_age = |index: usize| {
            health
                .and_then(|h| h.confirmed[index])
                .map(|at| now.duration_since(at))
        };
        Ok(MaintenanceObservation {
            retained_entries: root.as_ref().map(|r| r.next),
            retained_bytes: root.as_ref().map(|r| r.bytes),
            retained_sessions: root.as_ref().map(|r| r.sessions),
            capacity: root
                .as_ref()
                .map(|r| super::RetentionCapacityObservation::from_root(r, &self.inner.config)),
            repair_backlog: root
                .as_ref()
                .zip(scan.as_ref())
                .map(|(r, s)| r.next.saturating_sub(s.completed)),
            repair_round_age: round_age(0),
            discovery_round_age: round_age(1),
            repair_confirmed_age: confirmed_age(0),
            discovery_confirmed_age: confirmed_age(1),
        })
    }
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
        state.warn_capacity(shard, root, &self.inner.config, now);
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
    fn lifetime_capacity_warning_has_an_early_threshold_and_bounded_rate() {
        let cfg = InboxProducerConfig::new(BackendId("model".into()));
        let mut root = JournalRoot::default();
        let mut health = ShardHealth::new(&root, true);
        let shard = ShardId(42);
        let now = Instant::now();
        health.warn_capacity(shard, &root, &cfg, now);
        assert!(health.capacity_warned_at.is_none());
        root.entries = 3000;
        root.normal_entries = 3000;
        health.warn_capacity(shard, &root, &cfg, now);
        assert_eq!(health.capacity_warned_at, Some(now));
        health.warn_capacity(shard, &root, &cfg, now + Duration::from_secs(59));
        assert_eq!(health.capacity_warned_at, Some(now));
        health.warn_capacity(shard, &root, &cfg, now + Duration::from_secs(60));
        assert_eq!(
            health.capacity_warned_at,
            Some(now + Duration::from_secs(60))
        );
        root.entries = 0;
        root.normal_entries = 0;
        health.warn_capacity(shard, &root, &cfg, now + Duration::from_secs(120));
        assert_eq!(
            health.capacity_warned_at,
            Some(now + Duration::from_secs(60))
        );
    }

    #[test]
    fn concurrent_same_round_preserves_earliest_start() {
        let store = Arc::new(Model::default());
        let p = StoreInboxProducer::new(
            Arc::new(
                crate::budget::SharedScanBudget::new(crate::budget::ScanBudgetConfig::default())
                    .expect("valid shared scan budget"),
            ),
            Arc::new(bcs_dist_projection::OutputProjectionService),
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
