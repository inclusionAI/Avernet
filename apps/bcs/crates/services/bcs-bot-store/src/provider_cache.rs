//! Bounded process-local read-through cache. Removing an entry invalidates its
//! generation (Arc identity), including any older in-flight loader.
use std::{collections::BTreeMap, future::Future, sync::{Arc, Mutex, atomic::{AtomicU64, Ordering}}, time::{Duration, Instant}};
use bcs_service_api::ServiceResult;

static HITS: AtomicU64 = AtomicU64::new(0);
static NEGATIVE_HITS: AtomicU64 = AtomicU64::new(0);
static MISSES: AtomicU64 = AtomicU64::new(0);
static ERRORS: AtomicU64 = AtomicU64::new(0);
static INVALIDATIONS: AtomicU64 = AtomicU64::new(0);
static LOADS: AtomicU64 = AtomicU64::new(0);

pub fn statistics() -> [u64; 6] {
    [&HITS, &NEGATIVE_HITS, &MISSES, &ERRORS, &INVALIDATIONS, &LOADS].map(|n| n.load(Ordering::Relaxed))
}

type Entry<T> = Arc<tokio::sync::Mutex<Option<(Instant, Option<T>)>>>;
pub(crate) struct ProviderCache<T> {
    entries: Mutex<BTreeMap<String, Entry<T>>>,
    capacity: usize,
    ttl: Duration,
}
impl<T: Clone> ProviderCache<T> {
    pub fn new(capacity: usize, ttl: Duration) -> Self { Self { entries: Mutex::new(BTreeMap::new()), capacity, ttl } }
    pub fn invalidate(&self, key: &str) {
        self.entries.lock().unwrap().remove(key);
        INVALIDATIONS.fetch_add(1, Ordering::Relaxed);
    }
    pub async fn get<F, Fut>(&self, key: &str, mut load: F) -> ServiceResult<Option<T>>
    where F: FnMut() -> Fut, Fut: Future<Output = ServiceResult<Option<T>>> {
        loop {
            let entry = {
                let mut entries = self.entries.lock().unwrap();
                if !entries.contains_key(key) && entries.len() >= self.capacity {
                    // Do not evict loaders: that would defeat same-key singleflight.
                    let victim = entries.iter().find(|(_, value)| Arc::strong_count(value) == 1).map(|(k,_)| k.clone());
                    if let Some(victim) = victim { entries.remove(&victim); }
                    else { return Err(bcs_service_api::ServiceError::InternalError("provider cache loading capacity exceeded".into())); }
                }
                entries.entry(key.into()).or_insert_with(|| Arc::new(tokio::sync::Mutex::new(None))).clone()
            };
            let mut value = entry.lock().await;
            if let Some((created, cached)) = &*value {
                if created.elapsed() < self.ttl {
                    if !self.current(key, &entry) { continue; }
                    HITS.fetch_add(1, Ordering::Relaxed);
                    if cached.is_none() { NEGATIVE_HITS.fetch_add(1, Ordering::Relaxed); }
                    return Ok(cached.clone());
                }
            }
            MISSES.fetch_add(1, Ordering::Relaxed);
            LOADS.fetch_add(1, Ordering::Relaxed);
            let loaded = match load().await {
                Ok(loaded) => loaded,
                Err(error) => { ERRORS.fetch_add(1, Ordering::Relaxed); return Err(error); }
            };
            if !self.current(key, &entry) { continue; }
            *value = Some((Instant::now(), loaded.clone()));
            return Ok(loaded);
        }
    }
    fn current(&self, key: &str, entry: &Entry<T>) -> bool {
        self.entries.lock().unwrap().get(key).is_some_and(|current| Arc::ptr_eq(current, entry))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn negative_cache_and_invalidation() {
        let cache = ProviderCache::<u64>::new(2, Duration::from_secs(30));
        assert_eq!(cache.get("a", || async { Ok(None) }).await.unwrap(), None);
        assert_eq!(cache.get("a", || async { panic!("negative hit queried DB") }).await.unwrap(), None);
        cache.invalidate("a");
        assert_eq!(cache.get("a", || async { Ok(Some(2)) }).await.unwrap(), Some(2));
    }
    #[tokio::test]
    async fn invalidation_during_load_cannot_refill_old_value() {
        let cache = Arc::new(ProviderCache::<u64>::new(2, Duration::from_secs(30)));
        let started = Arc::new(tokio::sync::Notify::new());
        let resume = Arc::new(tokio::sync::Notify::new());
        let other = cache.clone(); let started2 = started.clone(); let resume2 = resume.clone();
        let job = tokio::spawn(async move {
            let mut n = 0;
            other.get("a", || { n += 1; let n = n; let s = started2.clone(); let r = resume2.clone(); async move {
                if n == 1 { s.notify_one(); r.notified().await; } Ok(Some(n))
            }}).await.unwrap()
        });
        started.notified().await; cache.invalidate("a"); resume.notify_one();
        assert_eq!(job.await.unwrap(), Some(2));
    }
    #[tokio::test]
    async fn errors_are_not_cached_and_capacity_is_bounded() {
        let cache = ProviderCache::<u64>::new(2, Duration::ZERO);
        assert!(cache.get("a", || async { Err(bcs_service_api::ServiceError::InternalError("db".into())) }).await.is_err());
        for key in ["a", "b", "c"] { assert_eq!(cache.get(key, || async { Ok(Some(1)) }).await.unwrap(), Some(1)); }
        assert_eq!(cache.entries.lock().unwrap().len(), 2);
    }
    #[tokio::test]
    async fn concurrent_misses_share_one_loader() {
        let cache = Arc::new(ProviderCache::<u64>::new(2, Duration::from_secs(30)));
        let loads = Arc::new(AtomicU64::new(0));
        let mut jobs = tokio::task::JoinSet::new();
        for _ in 0..20 {
            let cache = cache.clone(); let loads = loads.clone();
            jobs.spawn(async move { cache.get("a", || async {
                loads.fetch_add(1, Ordering::SeqCst); tokio::task::yield_now().await; Ok(Some(7))
            }).await.unwrap() });
        }
        while let Some(result) = jobs.join_next().await { assert_eq!(result.unwrap(), Some(7)); }
        assert_eq!(loads.load(Ordering::SeqCst), 1);
    }
}
