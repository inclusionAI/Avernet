//! Process-local read-through cache for per-Bot caller configuration
//! snapshots (spec §10.7.2 reads ONLY — the sync write path bypasses it and
//! invalidates on success).
//!
//! Freshness contract (2026-10-20 §15.96):
//! - SAME process: a successful configuration sync invalidates the entry, so
//!   the very next read goes to storage; PATCH-to-read remains immediate.
//! - CROSS process/replica: there is no invalidation bus. A present snapshot
//!   (the conservative direction: keep treating the target as caller-enabled
//!   until the TTL expires) converges within the positive TTL; a negative
//!   answer — target missing or never-configured, the direction that would
//!   let a freshly ENABLED target take the ordinary Owner path — converges
//!   within the much shorter negative TTL.
//! - EXPIRY: an expired entry is reloaded; the load error propagates and is
//!   NEVER answered from the stale entry (fail closed, no stale-extend).
//! - Load errors are not cached; singleflight collapses concurrent misses of
//!   one key; capacity eviction never evicts an in-flight loader (model of
//!   [`crate::provider_cache`], with a per-value-class TTL instead of one
//!   fixed TTL).

use std::collections::BTreeMap;
use std::future::Future;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use bcs_service_api::port::repo::BotCallerConfiguration;
use bcs_service_api::ServiceResult;

static HITS: AtomicU64 = AtomicU64::new(0);
static NEGATIVE_HITS: AtomicU64 = AtomicU64::new(0);
static MISSES: AtomicU64 = AtomicU64::new(0);
static ERRORS: AtomicU64 = AtomicU64::new(0);
static INVALIDATIONS: AtomicU64 = AtomicU64::new(0);
static LOADS: AtomicU64 = AtomicU64::new(0);

pub const POSITIVE_TTL: Duration = Duration::from_secs(30);
pub const NEGATIVE_TTL: Duration = Duration::from_secs(5);
const CACHE_CAPACITY: usize = 4096;

/// [hits, negative_hits, misses, errors, invalidations, loads]
pub fn statistics() -> [u64; 6] {
    [&HITS, &NEGATIVE_HITS, &MISSES, &ERRORS, &INVALIDATIONS, &LOADS]
        .map(|n| n.load(Ordering::Relaxed))
}

/// The cached answer class decides the TTL: a present snapshot is the
/// slow-changing, conservative direction; every negative answer (target
/// missing, or present-but-never-configured) is the fast-convergence
/// direction because a freshly enabled target must not stay negative long.
fn is_positive(value: &Option<BotCallerConfiguration>) -> bool {
    value
        .as_ref()
        .is_some_and(|config| config.snapshot.is_some())
}

type Entry = Arc<tokio::sync::Mutex<Option<(Instant, Option<BotCallerConfiguration>)>>>;

pub(crate) struct CallerConfigCache {
    entries: Mutex<BTreeMap<String, Entry>>,
    capacity: usize,
    positive_ttl: Duration,
    negative_ttl: Duration,
}

impl CallerConfigCache {
    pub fn new(positive_ttl: Duration, negative_ttl: Duration) -> Self {
        Self {
            entries: Mutex::new(BTreeMap::new()),
            capacity: CACHE_CAPACITY,
            positive_ttl,
            negative_ttl,
        }
    }

    fn ttl_for(&self, value: &Option<BotCallerConfiguration>) -> Duration {
        if is_positive(value) { self.positive_ttl } else { self.negative_ttl }
    }

    pub fn invalidate(&self, key: &str) {
        self.entries.lock().unwrap().remove(key);
        INVALIDATIONS.fetch_add(1, Ordering::Relaxed);
    }

    /// Read-through with singleflight; an expired-or-errored load never
    /// answers from the stale entry.
    pub async fn get<F, Fut>(&self, key: &str, mut load: F) -> ServiceResult<Option<BotCallerConfiguration>>
    where
        F: FnMut() -> Fut,
        Fut: Future<Output = ServiceResult<Option<BotCallerConfiguration>>>,
    {
        loop {
            let entry = {
                let mut entries = self.entries.lock().unwrap();
                if !entries.contains_key(key) && entries.len() >= self.capacity {
                    let victim = entries
                        .iter()
                        .find(|(_, value)| Arc::strong_count(value) == 1)
                        .map(|(k, _)| k.clone());
                    match victim {
                        Some(victim) => {
                            entries.remove(&victim);
                        }
                        None => {
                            return Err(bcs_service_api::ServiceError::InternalError(
                                "caller config cache loading capacity exceeded".into(),
                            ))
                        }
                    }
                }
                entries
                    .entry(key.into())
                    .or_insert_with(|| Arc::new(tokio::sync::Mutex::new(None)))
                    .clone()
            };
            let mut value = entry.lock().await;
            if let Some((loaded_at, cached)) = &*value {
                if loaded_at.elapsed() < self.ttl_for(cached) {
                    if !self.current(key, &entry) {
                        continue;
                    }
                    HITS.fetch_add(1, Ordering::Relaxed);
                    if !is_positive(cached) {
                        NEGATIVE_HITS.fetch_add(1, Ordering::Relaxed);
                    }
                    return Ok(cached.clone());
                }
            }
            MISSES.fetch_add(1, Ordering::Relaxed);
            LOADS.fetch_add(1, Ordering::Relaxed);
            let loaded = match load().await {
                Ok(loaded) => loaded,
                Err(error) => {
                    ERRORS.fetch_add(1, Ordering::Relaxed);
                    return Err(error);
                }
            };
            if !self.current(key, &entry) {
                continue;
            }
            *value = Some((Instant::now(), loaded.clone()));
            return Ok(loaded);
        }
    }

    /// Batch cooperation path: admit a freshly batch-loaded answer without
    /// the per-key singleflight (the only batch caller is the admission
    /// scan, whose cold refills run at TTL granularity, not per message).
    /// An invalidation that happens while the batch reader was in flight
    /// wins over the refill for one simple reason: the entry is only
    /// created here, never refreshed over a later generation.
    pub fn store(&self, key: &str, value: Option<BotCallerConfiguration>) {
        let mut entries = self.entries.lock().unwrap();
        if entries.len() >= self.capacity && !entries.contains_key(key) {
            let victim = entries
                .iter()
                .find(|(_, value)| Arc::strong_count(value) == 1)
                .map(|(k, _)| k.clone());
            if let Some(victim) = victim {
                entries.remove(&victim);
            }
        }
        entries.insert(key.into(), Arc::new(tokio::sync::Mutex::new(Some((Instant::now(), value)))));
        LOADS.fetch_add(1, Ordering::Relaxed);
    }

    fn current(&self, key: &str, entry: &Entry) -> bool {
        self.entries
            .lock()
            .unwrap()
            .get(key)
            .is_some_and(|current| Arc::ptr_eq(current, entry))
    }

    /// Non-loading freshness probe for the batch read path: `Some(value)`
    /// answers from a fresh entry, `None` means "load me" (absent, expired,
    /// or invalidated). Unlike [`Self::get`] it does not singleflight, so
    /// the batch caller owns its own single round trip.
    pub async fn peek(&self, key: &str) -> Option<Option<BotCallerConfiguration>> {
        let entry = self.entries.lock().unwrap().get(key).cloned()?;
        let value = entry.lock().await;
        match &*value {
            Some((loaded_at, cached)) if loaded_at.elapsed() < self.ttl_for(cached) => {
                Some(cached.clone())
            }
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::port::repo::CallerIdentitySnapshot;
    use bcs_service_api::CallerIdentityConfig;

    fn config_with(enabled: bool, revision: u64) -> BotCallerConfiguration {
        BotCallerConfiguration {
            snapshot: Some(CallerIdentitySnapshot {
                source_revision: revision,
                caller_identity: CallerIdentityConfig { enabled, ..Default::default() },
            }),
        }
    }

    fn revision_of(answer: Option<BotCallerConfiguration>) -> u64 {
        answer.unwrap().snapshot.unwrap().source_revision
    }

    #[tokio::test]
    async fn fresh_entries_hit_and_do_not_reload() {
        let cache = CallerConfigCache::new(Duration::from_secs(30), Duration::from_secs(5));
        let loaded = cache
            .get("pos", || async { Ok(Some(config_with(true, 1))) })
            .await
            .unwrap();
        assert_eq!(revision_of(loaded), 1);
        let hit = cache
            .get("pos", || async { panic!("fresh entry must not reload") })
            .await
            .unwrap();
        assert_eq!(revision_of(hit), 1);
    }

    #[tokio::test]
    async fn expired_entry_reloads_and_error_never_serves_stale() {
        let cache = CallerConfigCache::new(Duration::from_secs(30), Duration::ZERO);
        let cached = cache.get("neg", || async { Ok(None) }).await.unwrap();
        assert!(cached.is_none(), "negative answers are cached too");
        // TTL zero: the cached None is immediately expired; a failing reload
        // must surface the error instead of answering the stale None.
        assert!(
            cache
                .get("neg", || async {
                    Err(bcs_service_api::ServiceError::InvalidOperation {
                        message: "db".into(),
                        request_id: None,
                    })
                })
                .await
                .is_err(),
            "an expired entry must never be answered from stale storage"
        );
        // Errors are not cached: a good reload still populates.
        let reloaded = cache
            .get("neg", || async { Ok(Some(config_with(true, 2))) })
            .await
            .unwrap();
        assert!(reloaded.is_some());
    }

    #[tokio::test]
    async fn concurrency_collapses_into_one_loader() {
        let cache = Arc::new(CallerConfigCache::new(Duration::from_secs(30), Duration::from_secs(5)));
        let loads = Arc::new(AtomicU64::new(0));
        let mut jobs = tokio::task::JoinSet::new();
        for _ in 0..16 {
            let cache = cache.clone();
            let loads = loads.clone();
            jobs.spawn(async move {
                let loads = loads.clone();
                cache
                    .get("k", move || {
                        let loads = loads.clone();
                        async move {
                            loads.fetch_add(1, Ordering::SeqCst);
                            tokio::task::yield_now().await;
                            Ok(Some(config_with(true, 1)))
                        }
                    })
                    .await
                    .unwrap()
            });
        }
        while let Some(result) = jobs.join_next().await {
            assert_eq!(revision_of(result.unwrap()), 1);
        }
        assert_eq!(loads.load(Ordering::SeqCst), 1, "singleflight must collapse cold misses");
    }

    #[tokio::test]
    async fn invalidation_forces_reload_same_key() {
        let cache = CallerConfigCache::new(Duration::from_secs(30), Duration::from_secs(30));
        let first = cache.get("k", || async { Ok(Some(config_with(true, 1))) }).await.unwrap();
        assert_eq!(revision_of(first), 1);
        cache.invalidate("k");
        let second = cache.get("k", || async { Ok(Some(config_with(false, 2))) }).await.unwrap();
        assert_eq!(second.unwrap().snapshot.unwrap().source_revision, 2);
    }

    #[tokio::test]
    async fn store_and_peek_answer_batch_refills() {
        let cache = CallerConfigCache::new(Duration::from_secs(30), Duration::from_secs(5));
        cache.store("k", Some(config_with(true, 3)));
        let hit = cache
            .get("k", || async { panic!("stored entry must hit without a reload") })
            .await
            .unwrap();
        assert_eq!(revision_of(hit), 3);
        let peeked = cache.peek("k").await.flatten();
        assert_eq!(peeked.unwrap().snapshot.unwrap().source_revision, 3);
        cache.invalidate("k");
        assert!(cache.peek("k").await.is_none(), "invalidated entries must not peek fresh");

        // Negative refills (target missing / never configured) peek None-yet-fresh.
        cache.store("missing", None);
        assert!(cache.peek("missing").await.is_some_and(|answer| answer.is_none()));
        let negative = cache
            .get("missing", || async { panic!("fresh negative must not reload") })
            .await
            .unwrap();
        assert!(negative.is_none());
    }
}
