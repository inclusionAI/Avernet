//! Cooperative dispatch scans retain one bounded phase across canceled polls.
use super::{scan::Step, *};
use bcs_service_api::port::repo::distributed::budget::ScanClass;
use std::{future::Future, pin::Pin, sync::Mutex};
use tokio::time::Instant;
const MAX_JOBS: usize = 2;
const SLICE: Duration = Duration::from_millis(100);
type ScanFuture = Pin<Box<dyn Future<Output = Result<Step, StoreError>> + Send>>;
pub(super) struct Job {
    future: Option<ScanFuture>,
    items: Vec<Did>,
    round: u64,
    finished: bool,
    last_poll: Instant,
}
pub(super) struct Scheduler {
    jobs: Mutex<BTreeMap<ShardId, Arc<tokio::sync::Mutex<Job>>>>,
}
#[derive(Clone)]
pub(super) enum ScanHandle {
    Owned(Arc<Scheduler>),
    // A retained future must not own the scheduler which retains that future.
    Execution,
}
impl ScanHandle {
    pub fn new() -> Self {
        Self::Owned(Arc::new(Scheduler {
            jobs: Mutex::new(BTreeMap::new()),
        }))
    }
    pub fn scheduler(&self) -> Result<Arc<Scheduler>, StoreError> {
        match self {
            Self::Owned(scheduler) => Ok(scheduler.clone()),
            Self::Execution => Err(invalid("recursive_dispatch_scan")),
        }
    }
}
impl StoreDispatch {
    fn scan_execution(&self) -> Self {
        Self {
            stores: self.stores.clone(),
            layout: self.layout.clone(),
            verifier: self.verifier.clone(),
            config: self.config.clone(),
            io_slots: self.io_slots.clone(),
            scans: ScanHandle::Execution,
        }
    }
}
impl Scheduler {
    pub fn reserve(
        &self,
        shard: ShardId,
    ) -> Result<Option<Arc<tokio::sync::Mutex<Job>>>, StoreError> {
        let mut jobs = self
            .jobs
            .lock()
            .map_err(|_| invalid("dispatch_scan_poisoned"))?;
        if let Some(job) = jobs.get(&shard) {
            return Ok(Some(job.clone()));
        }
        // Idle jobs may be dropped: every authoritative obligation and completed
        // phase is durable. Restart may rediscover an item, never acknowledge it.
        jobs.retain(|_, job| {
            job.try_lock()
                .map_or(true, |j| j.last_poll.elapsed() < Duration::from_secs(5))
        });
        if jobs.len() >= MAX_JOBS {
            return Ok(None);
        }
        let job = Arc::new(tokio::sync::Mutex::new(Job {
            future: None,
            items: vec![],
            round: 0,
            finished: false,
            last_poll: Instant::now(),
        }));
        jobs.insert(shard, job.clone());
        Ok(Some(job))
    }
    pub async fn poll(
        &self,
        board: &StoreDispatch,
        shard: ShardId,
        now: TimestampMillis,
        limit: u32,
        job: &Arc<tokio::sync::Mutex<Job>>,
    ) -> Result<Page<Did>, StoreError> {
        let Ok(mut guard) = job.try_lock() else {
            return board.scan_page(shard, vec![], false, 0);
        };
        // Another poll may have completed and removed this reservation between
        // reserve() and locking it. Never execute a detached job outside the cap.
        {
            let jobs = self
                .jobs
                .lock()
                .map_err(|_| invalid("dispatch_scan_poisoned"))?;
            if !jobs.get(&shard).is_some_and(|stored| Arc::ptr_eq(stored, job)) {
                return board.scan_page(shard, vec![], false, 0);
            }
        }
        guard.last_poll = Instant::now();
        let start = Instant::now();
        let resuming = guard.future.is_some();
        let mut failure = None;
        for _ in 0..limit {
            let remaining = SLICE.saturating_sub(start.elapsed());
            if remaining.is_zero() || guard.finished || guard.items.len() >= limit as usize {
                break;
            }
            if guard.future.is_none() {
                if !board.stores.scan_budget.claim(ScanClass::Dispatch, 1, 0)? {
                    break;
                }
                let runner = board.scan_execution();
                guard.future = Some(Box::pin(async move { runner.scan_step(shard, now).await }));
            }
            let Some(future) = guard.future.as_mut() else {
                break;
            };
            let Ok(result) = tokio::time::timeout(remaining, future.as_mut()).await else {
                break;
            };
            guard.future = None;
            match result {
                Ok(step) => {
                    guard.round = step.round;
                    guard.finished = step.finished;
                    if let Some(item) = step.item
                        && !guard.items.contains(&item)
                    {
                        guard.items.push(item);
                    }
                }
                Err(error) => {
                    failure = Some(error);
                    break;
                }
            }
            // Give another shard the bounded job slot once a delayed phase
            // completes, even if this shard has an arbitrarily long directory.
            if resuming {
                break;
            }
        }
        // No await follows publication. Caller cancellation preserves both the
        // current future and earlier staged results until a later poll returns.
        let count = guard.items.len().min(limit as usize);
        let items = guard.items.drain(..count).collect::<Vec<_>>();
        let finished = guard.finished && guard.items.is_empty();
        let round = guard.round;
        let release = guard.future.is_none() && guard.items.is_empty();
        if release {
            let mut jobs = self
                .jobs
                .lock()
                .map_err(|_| invalid("dispatch_scan_poisoned"))?;
            if jobs
                .get(&shard)
                .is_some_and(|stored| Arc::ptr_eq(stored, job))
            {
                jobs.remove(&shard);
            }
        }
        if items.is_empty()
            && let Some(error) = failure
        {
            return Err(error);
        }
        board.scan_page(shard, items, finished, round)
    }
}
