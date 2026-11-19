//! Bounded cooperative scan execution. Cancellation drops a poll, not the job.
//! Jobs retain only bounded provider inputs/state; there are no spawned tasks.
use super::{StoreInboxProducer, storage::*};
use bcs_dist_model::{PageToken, ShardId, Sid};
use bcs_service_api::port::repo::distributed::{admission::AdmissionMaintenance, common::*};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    future::Future,
    pin::Pin,
    sync::{Arc, Mutex},
    time::Duration,
};

pub(super) enum ScanResult {
    Maintenance(AdmissionMaintenance),
    History(Page<Sid>),
}
type ScanFuture = Pin<Box<dyn Future<Output = Result<ScanResult, StoreError>> + Send>>;
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub(super) struct JobKey {
    pub class: u8,
    pub shard: ShardId,
    pub id: String,
}
pub(super) struct Job {
    future: Option<ScanFuture>,
    pub history: Option<Arc<Mutex<HistoryContinuation>>>,
    last_poll: tokio::time::Instant,
}
pub(super) struct ScanScheduler {
    jobs: Mutex<BTreeMap<JobKey, Arc<tokio::sync::Mutex<Job>>>>,
}
#[derive(Clone)]
pub(super) enum ScanHandle {
    Owned(Arc<ScanScheduler>),
    /// The retained future owns Inner, but never owns the scheduler holding it.
    Execution,
}
impl ScanHandle {
    pub fn new() -> Self {
        Self::Owned(Arc::new(ScanScheduler {
            jobs: Mutex::new(BTreeMap::new()),
        }))
    }
    pub fn scheduler(&self) -> Result<Arc<ScanScheduler>, StoreError> {
        match self {
            Self::Owned(s) => Ok(s.clone()),
            Self::Execution => Err(invalid("recursive_scan")),
        }
    }
}
impl StoreInboxProducer {
    pub(super) fn scan_execution(&self) -> Self {
        Self {
            inner: self.inner.clone(),
            scans: ScanHandle::Execution,
        }
    }
}
impl ScanScheduler {
    pub fn reserve(
        &self,
        key: JobKey,
        future: ScanFuture,
        history: Option<Arc<Mutex<HistoryContinuation>>>,
    ) -> Result<Option<Arc<tokio::sync::Mutex<Job>>>, StoreError> {
        let mut jobs = self
            .jobs
            .lock()
            .map_err(|_| invalid("scan_scheduler_poisoned"))?;
        if let Some(job) = jobs.get(&key) {
            return Ok(Some(job.clone()));
        }
        // Abandoned caller cursors must not occupy the bounded slots forever.
        // Dropping an idle scan cannot remove a durable intent or acknowledge an
        // effect. A history token reconstructs its fixed cut; maintenance restarts
        // from the persisted cursor and idempotent canonical phases.
        jobs.retain(|_, job| {
            job.try_lock()
                .map_or(true, |j| j.last_poll.elapsed() < Duration::from_secs(5))
        });
        // Repair and discovery have two slots each; history has four. A failed
        // class cannot allocate all eight slots. Each job retains at most one
        // bounded provider round (128 entries / 1MiB encoded page, bounded root).
        let class_limit = if key.class == 2 { 4 } else { 2 };
        if jobs.len() >= 8 || jobs.keys().filter(|k| k.class == key.class).count() >= class_limit {
            return Ok(None);
        }
        let job = Arc::new(tokio::sync::Mutex::new(Job {
            future: Some(future),
            history,
            last_poll: tokio::time::Instant::now(),
        }));
        jobs.insert(key, job.clone());
        Ok(Some(job))
    }
    pub async fn poll(
        &self,
        key: &JobKey,
        job: &Arc<tokio::sync::Mutex<Job>>,
    ) -> Result<Option<ScanResult>, StoreError> {
        let Ok(mut guard) = job.try_lock() else {
            return Ok(None);
        };
        // A caller can retain an Arc across idle reclamation. A detached job
        // must never resume outside the bounded reservation table.
        {
            let jobs = self
                .jobs
                .lock()
                .map_err(|_| invalid("scan_scheduler_poisoned"))?;
            if !jobs
                .get(key)
                .is_some_and(|current| Arc::ptr_eq(current, job))
            {
                return Ok(None);
            }
        }
        guard.last_poll = tokio::time::Instant::now();
        let Some(future) = guard.future.as_mut() else {
            return Err(pressure());
        };
        let result = tokio::time::timeout(Duration::from_millis(100), future.as_mut()).await;
        let Ok(result) = result else {
            return Ok(None);
        };
        guard.future = None;
        drop(guard);
        let mut jobs = self
            .jobs
            .lock()
            .map_err(|_| invalid("scan_scheduler_poisoned"))?;
        if jobs
            .get(key)
            .is_some_and(|current| Arc::ptr_eq(current, job))
        {
            jobs.remove(key);
        }
        result.map(Some)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct HistoryContinuation {
    pub id: String,
    pub shard: ShardId,
    pub cut: Option<u64>,
    pub cursor: u64,
    pub limit: u32,
}
impl HistoryContinuation {
    pub fn decode(
        shard: ShardId,
        page: &PageRequest,
        max_history: u64,
    ) -> Result<Self, StoreError> {
        if page.limit.get() > 128 {
            return Err(invalid("page_limit"));
        }
        let value = if let Some(encoded) = page
            .after
            .as_ref()
            .and_then(|p| p.0.strip_prefix("scan-poll-v1:"))
        {
            if encoded.len() > 1024 {
                return Err(invalid("history_cursor"));
            }
            serde_json::from_str::<Self>(encoded).map_err(|_| invalid("history_cursor"))?
        } else {
            let (cut, cursor) = match &page.after {
                Some(token) => {
                    let (cut, cursor) = serde_json::from_str::<(u64, u64)>(&token.0)
                        .map_err(|_| invalid("history_cursor"))?;
                    (Some(cut), cursor)
                }
                None => (None, 0),
            };
            let original = page.after.as_ref().map_or("", |p| p.0.as_str());
            let hash = crate::identity::digest_fields(&[
                &shard.0.to_be_bytes(),
                original.as_bytes(),
                &page.limit.get().to_be_bytes(),
            ]);
            Self {
                id: crate::identity::hex_of(&hash).into(),
                shard,
                cut,
                cursor,
                limit: page.limit.get(),
            }
        };
        if value.shard != shard
            || value.limit != page.limit.get()
            || value.id.len() != 64
            || !value.id.bytes().all(|b| b.is_ascii_hexdigit())
            || value
                .cut
                .is_some_and(|cut| cut > max_history || value.cursor > cut)
            || (value.cut.is_none() && value.cursor != 0)
        {
            return Err(invalid("history_cursor"));
        }
        Ok(value)
    }
    pub fn token(&self) -> Result<PageToken, StoreError> {
        Ok(PageToken(format!(
            "scan-poll-v1:{}",
            serde_json::to_string(self).map_err(|_| invalid("history_cursor"))?
        )))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[tokio::test(start_paused = true)]
    #[allow(clippy::unwrap_used, reason = "scan reservation assertions")]
    async fn reaped_reservation_cannot_execute_outside_the_job_limit() {
        let scheduler = ScanHandle::new().scheduler().unwrap();
        let calls = Arc::new(AtomicUsize::new(0));
        let key = JobKey {
            class: 0,
            shard: ShardId(1),
            id: "old".into(),
        };
        let observed = calls.clone();
        let old = scheduler
            .reserve(
                key.clone(),
                Box::pin(async move {
                    observed.fetch_add(1, Ordering::SeqCst);
                    Ok(ScanResult::History(Page {
                        items: vec![],
                        next: None,
                    }))
                }),
                None,
            )
            .unwrap()
            .unwrap();
        // A different caller reaps the idle reservation while its first caller
        // still holds an Arc and has not yet entered poll.
        tokio::time::advance(Duration::from_secs(6)).await;
        let replacement = scheduler
            .reserve(
                JobKey {
                    id: "new".into(),
                    ..key.clone()
                },
                Box::pin(std::future::pending()),
                None,
            )
            .unwrap()
            .unwrap();
        assert!(scheduler.poll(&key, &old).await.unwrap().is_none());
        assert_eq!(calls.load(Ordering::SeqCst), 0);
        drop(replacement);
    }
}
