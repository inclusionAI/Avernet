//! Independent Dispatch supervision; no Agent transport or reducer execution.
mod evidence;
mod visit;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    budget::*, common::*, dispatch::*, inbox::*, ownership::*, session::SessionStorePort,
};
pub use evidence::TerminalDispatchEvidence;
use futures::{StreamExt, stream};
use std::{
    collections::{BTreeSet, VecDeque},
    num::NonZeroU32,
    sync::Arc,
    time::Duration,
};
use tokio::time::Instant;

/// Shared ports only. The same scan_budget instance must also be injected into
/// the durable scanning providers; scan_due itself owns its native IO permits.
#[derive(Clone)]
pub struct SupervisorPorts {
    pub dispatch: Arc<dyn DispatchControlPort>,
    pub sessions: Arc<dyn SessionStorePort>,
    pub inbox: Arc<dyn InboxProducerPort>,
    pub membership: Arc<dyn MembershipPort>,
    pub scan_budget: Arc<dyn ScanBudgetPort>,
    pub now: Arc<dyn Fn() -> TimestampMillis + Send + Sync>,
}
#[derive(Debug, Clone)]
pub struct SupervisorConfig {
    pub shards: Vec<ShardId>,
    pub concurrency: usize,
    pub max_pending: usize,
    pub max_pending_bytes: usize,
    pub page_entries: u32,
    pub io_timeout: Duration,
    pub recheck_interval: Duration,
    pub unknown_interval: Duration,
    pub retry_backoff: Duration,
}
impl Default for SupervisorConfig {
    fn default() -> Self {
        Self {
            shards: vec![],
            concurrency: 8,
            max_pending: 64,
            max_pending_bytes: 64 * JOB_BYTES,
            page_entries: 128,
            io_timeout: Duration::from_secs(2),
            recheck_interval: Duration::from_secs(1),
            unknown_interval: Duration::from_secs(2),
            retry_backoff: Duration::from_millis(200),
        }
    }
}
/// Reservations cover cached identities, compact facts and the <=16KiB request.
const JOB_BYTES: usize = 24 * 1024;
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SupervisorReport {
    pub discovered: usize,
    pub visited: usize,
    pub notified: usize,
    /// Verification-mode dispatches routed to the verification queue
    /// index instead of a normal hint (plan T7).
    pub routed: usize,
    pub returned: usize,
    pub settled: usize,
    pub deferred: usize,
    pub failures: usize,
}
#[derive(Clone)]
struct Facts {
    sid: Sid,
    tid: Tid,
    pool: PoolId,
    revision: Revision,
    holder: Option<Instance>,
    deadline: TimestampMillis,
    terminal: Option<TerminalDispatchEvidence>,
    /// The verification handoff in force (plan T7): a due dispatch in
    /// verification mode is routed to the verification queue, never to a
    /// normal execution hint.
    verification: Option<VerificationHandoff>,
}
#[derive(Clone)]
enum Phase {
    Load,
    Holder(Facts),
    Notify(Facts),
    Spec(Facts),
    Admit {
        facts: Facts,
        request: Box<AdmitRequest>,
    },
    Settle {
        revision: Revision,
        at: TimestampMillis,
    },
    Done,
}
struct Job {
    shard: ShardId,
    did: Did,
    phase: Phase,
    ready: Instant,
    failures: u8,
}
struct ShardCursor {
    shard: ShardId,
    after: Option<PageToken>,
}
pub struct DispatchSupervisor {
    ports: SupervisorPorts,
    config: SupervisorConfig,
    shards: Vec<ShardCursor>,
    next_shard: usize,
    pending: VecDeque<Job>,
    identities: BTreeSet<String>,
    discover_next: bool,
}
impl DispatchSupervisor {
    pub fn new(ports: SupervisorPorts, config: SupervisorConfig) -> Result<Self, StoreError> {
        let profile = ports.scan_budget.profile();
        if ports.dispatch.scan_budget_profile().as_ref() != Some(&profile)
            || ports.inbox.scan_budget_profile().as_ref() != Some(&profile)
        {
            return Err(invalid("supervisor_shared_budget_identity"));
        }
        if config.shards.len() > 1024
            || config.concurrency == 0
            || config.concurrency > 8
            || config.concurrency > profile.io_slots
            || config.max_pending == 0
            || config.max_pending > 64
            || config.max_pending_bytes < JOB_BYTES
            || config.max_pending_bytes > 64 * JOB_BYTES
            || config.page_entries == 0
            || config.page_entries > 128
            || config.io_timeout.is_zero()
            || config.io_timeout > Duration::from_secs(2)
            || config.recheck_interval.is_zero()
            || config.recheck_interval > Duration::from_secs(60)
            || config.unknown_interval < config.recheck_interval
            || config.unknown_interval > Duration::from_secs(60)
            || config.retry_backoff.is_zero()
            || config.retry_backoff > Duration::from_secs(5)
            || profile.identity.is_empty()
            || profile.identity.len() > 256
            || profile.io_slots == 0
            || profile.io_slots > 8
            || profile.io_slots > profile.pool_size / 4
            || profile.logs_per_second == 0
            || profile.logs_per_second > 1000
            || profile.sessions_per_second == 0
            || profile.sessions_per_second > 200
            || profile.burst == 0
            || profile.burst > 128
        {
            return Err(invalid("supervisor_config"));
        }
        let mut unique = BTreeSet::new();
        if config.shards.iter().any(|s| !unique.insert(s.0)) {
            return Err(invalid("supervisor_duplicate_shard"));
        }
        let shards = config
            .shards
            .iter()
            .map(|s| ShardCursor {
                shard: *s,
                after: None,
            })
            .collect();
        Ok(Self {
            ports,
            config,
            shards,
            next_shard: 0,
            pending: VecDeque::new(),
            identities: BTreeSet::new(),
            discover_next: true,
        })
    }
    pub fn pending(&self) -> usize {
        self.pending.len()
    }
    pub fn reserved_bytes(&self) -> usize {
        self.pending.len() * JOB_BYTES
    }
    /// One discovery IO OR one concurrent batch of at most eight single-IO phases.
    /// Cancellation leaves original jobs and their identities in the queue.
    pub async fn tick(&mut self) -> SupervisorReport {
        if self.discover_next || self.pending.is_empty() {
            self.discover_next = false;
            self.discover().await
        } else {
            self.discover_next = true;
            self.process().await
        }
    }
    async fn discover(&mut self) -> SupervisorReport {
        let mut report = SupervisorReport::default();
        let available = self
            .config
            .max_pending
            .min(self.config.max_pending_bytes / JOB_BYTES)
            .saturating_sub(self.pending.len());
        if self.shards.is_empty() || available == 0 {
            return report;
        }
        let index = self.next_shard % self.shards.len();
        self.next_shard = (index + 1) % self.shards.len();
        let cursor = &mut self.shards[index];
        let limit = NonZeroU32::new(self.config.page_entries.min(available as u32))
            .unwrap_or(NonZeroU32::MIN);
        let result = tokio::time::timeout(
            self.config.io_timeout,
            self.ports.dispatch.scan_due(
                cursor.shard,
                (self.ports.now)(),
                PageRequest {
                    after: cursor.after.clone(),
                    limit,
                },
            ),
        )
        .await;
        let page = match result {
            Ok(Ok(page)) => page,
            _ => {
                report.failures = 1;
                return report;
            }
        };
        if page.items.len() > limit.get() as usize
            || page.next.as_ref().is_some_and(|t| t.0.len() > 1024)
            || page.items.iter().any(|d| !identifier(&d.0))
        {
            report.failures = 1;
            return report;
        }
        cursor.after = page.next;
        for did in page.items {
            if self.identities.insert(did.0.clone()) {
                self.pending.push_back(Job {
                    shard: cursor.shard,
                    did,
                    phase: Phase::Load,
                    ready: Instant::now(),
                    failures: 0,
                });
                report.discovered += 1;
            }
        }
        report
    }
    async fn process(&mut self) -> SupervisorReport {
        let now = Instant::now();
        let business_now = (self.ports.now)();
        let ports = &self.ports;
        let config = &self.config;
        let mut results = stream::FuturesUnordered::new();
        for job in self
            .pending
            .iter_mut()
            .filter(|job| job.ready <= now)
            .take(config.concurrency)
        {
            results.push(visit::visit(ports, config, job, business_now));
        }
        let mut total = SupervisorReport::default();
        while let Some(r) = results.next().await {
            total.visited += r.visited;
            total.notified += r.notified;
            total.routed += r.routed;
            total.returned += r.returned;
            total.settled += r.settled;
            total.deferred += r.deferred;
            total.failures += r.failures;
        }
        drop(results);
        self.pending.retain(|job| {
            if matches!(job.phase, Phase::Done) {
                self.identities.remove(&job.did.0);
                false
            } else {
                true
            }
        });
        let rotate = total.visited.min(self.pending.len());
        self.pending.rotate_left(rotate);
        total
    }
}
fn identifier(s: &str) -> bool {
    !s.is_empty() && s.len() <= 256 && !s.chars().any(|c| c.is_control() || c.is_whitespace())
}
fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}
fn next_at(now: TimestampMillis, interval: Duration) -> TimestampMillis {
    TimestampMillis(
        now.0
            .saturating_add(interval.as_millis().min(i64::MAX as u128) as i64),
    )
}
