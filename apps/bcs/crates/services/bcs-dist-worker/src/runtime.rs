use crate::{WorkerConfig, rank_candidates};
use bcs_dist_model::*;
use bcs_service_api::{
    port::{
        dist_runtime::DistWorkerPeerPort,
        repo::distributed::{common::*, inbox::*, ownership::*, session::*},
    },
    types::dist_runtime::*,
};
use std::{
    collections::{BTreeMap, VecDeque},
    num::NonZeroU32,
    sync::{Arc, Mutex},
};
use tokio::{
    sync::Mutex as AsyncMutex,
    time::{Instant, timeout},
};

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Phase {
    Recovering,
    Idle,
    Running,
    Effects,
    Unknown,
    Transferring,
    Releasing,
}
pub(crate) struct Session {
    pub acquired: AcquiredSession,
    pub phase: Phase,
    pub pending_effects: u64,
    pub pending_schedule: bool,
    pub drain_retry_at: Instant,
}
#[derive(Default)]
pub(crate) struct Scan {
    pub queue: VecDeque<Sid>,
    pub next: Option<PageToken>,
}
#[derive(Clone)]
pub(crate) enum AcquisitionStage {
    Acquire {
        initial: Box<Checkpoint>,
        uncertain: bool,
    },
    Observe(Box<AcquiredSession>),
    Diagnostic {
        code: String,
    },
    Release(SessionOwnership),
}
#[derive(Clone)]
pub(crate) struct Acquisition {
    pub claim: ShardClaim,
    pub stage: AcquisitionStage,
    pub retry_at: Instant,
}
#[derive(Clone)]
pub(crate) struct Incoming {
    pub request: HandoffRequest,
    pub uncertain: bool,
    pub gate: Arc<AsyncMutex<()>>,
    pub retry_at: Instant,
}
#[derive(Clone)]
pub(crate) struct Outgoing {
    pub target: MemberRecord,
    pub request: HandoffRequest,
    pub uncertain: bool,
}
pub(crate) struct State {
    pub draining: bool,
    pub stopped: bool,
    pub started: Instant,
    pub valid_until: Instant,
    pub last_heartbeat: Instant,
    pub confirmed_alive: bool,
    pub shards: BTreeMap<ShardId, ShardClaim>,
    pub sessions: BTreeMap<Sid, Session>,
    pub scans: BTreeMap<ShardId, Scan>,
    pub acquisitions: BTreeMap<Sid, Acquisition>,
    pub retries: BTreeMap<Sid, (ShardClaim, Instant)>,
    pub priority_shards: VecDeque<ShardId>,
    pub priority_turn: bool,
    pub recovery_cursor: Option<ShardId>,
    pub recovery_priority: VecDeque<ShardId>,
    pub recovery_turn: bool,
    pub member_cursor: Option<PageToken>,
    pub rejected_targets: BTreeMap<ShardId, Vec<(Instance, Instant)>>,
    pub acquisition_cursor: Option<Sid>,
    pub retry_turn: bool,
    pub incoming: BTreeMap<String, Incoming>,
    pub claims: BTreeMap<ShardId, (ClaimShard, bool, Instant)>,
    pub outgoing: BTreeMap<ShardId, Outgoing>,
    pub candidates_since: BTreeMap<ShardId, Instant>,
    pub shard_cursor: usize,
    pub drain_cursor: Option<ShardId>,
    pub transfer_cursors: BTreeMap<ShardId, Sid>,
    pub drain_progress: u64,
    pub drain_retries: BTreeMap<ShardId, Instant>,
}
impl State {
    pub fn made_progress(&mut self) {
        self.drain_progress = self.drain_progress.wrapping_add(1);
    }
}
pub type CheckpointInitializer =
    dyn Fn(&SessionSpec) -> Result<Checkpoint, RuntimeError> + Send + Sync;

type SharedFeed = Arc<AsyncMutex<Option<Box<dyn ShardFeedPort>>>>;

pub struct WorkerRuntime {
    pub(crate) instance: Instance,
    pub(crate) lease: MembershipLease,
    pub(crate) config: WorkerConfig,
    pub(crate) membership: Arc<dyn MembershipPort>,
    pub(crate) owners: Arc<dyn ShardLeasePort>,
    pub(crate) sessions: Arc<dyn SessionStorePort>,
    pub(crate) inbox: Arc<dyn InboxConsumerPort>,
    pub(crate) scheduling: Arc<dyn InboxSchedulingPort>,
    pub(crate) peers: Arc<dyn DistWorkerPeerPort>,
    pub(crate) initialize: Arc<CheckpointInitializer>,
    pub(crate) state: Mutex<State>,
    pub(crate) tick_guard: AsyncMutex<()>,
    pub(crate) feeds: Mutex<BTreeMap<ShardId, SharedFeed>>,
    pub(crate) reader_restarts: std::sync::atomic::AtomicU64,
    pub(crate) scan_budget:
        Mutex<Option<Arc<dyn bcs_service_api::port::repo::distributed::budget::ScanBudgetPort>>>,
}
impl WorkerRuntime {
    pub fn fresh_instance(node: String) -> Instance {
        Instance {
            node,
            incarnation: uuid::Uuid::new_v4().to_string(),
        }
    }
    #[allow(
        clippy::too_many_arguments,
        reason = "explicit injected coordination boundaries"
    )]
    pub async fn start(
        node: String,
        control_address: String,
        config: WorkerConfig,
        membership: Arc<dyn MembershipPort>,
        owners: Arc<dyn ShardLeasePort>,
        sessions: Arc<dyn SessionStorePort>,
        inbox: Arc<dyn InboxConsumerPort>,
        scheduling: Arc<dyn InboxSchedulingPort>,
        peers: Arc<dyn DistWorkerPeerPort>,
        initialize: Arc<CheckpointInitializer>,
    ) -> Result<Arc<Self>, RuntimeError> {
        Self::start_with_peer_factory(
            node,
            control_address,
            config,
            membership,
            owners,
            sessions,
            inbox,
            scheduling,
            |_| Ok(peers),
            initialize,
        )
        .await
    }

    /// Create one fresh instance and bind it to both the peer transport and
    /// membership registration.
    #[allow(
        clippy::too_many_arguments,
        reason = "explicit injected coordination boundaries"
    )]
    pub async fn start_with_peer_factory(
        node: String,
        control_address: String,
        config: WorkerConfig,
        membership: Arc<dyn MembershipPort>,
        owners: Arc<dyn ShardLeasePort>,
        sessions: Arc<dyn SessionStorePort>,
        inbox: Arc<dyn InboxConsumerPort>,
        scheduling: Arc<dyn InboxSchedulingPort>,
        make_peers: impl FnOnce(&Instance) -> Result<Arc<dyn DistWorkerPeerPort>, RuntimeError>,
        initialize: Arc<CheckpointInitializer>,
    ) -> Result<Arc<Self>, RuntimeError> {
        config.validate()?;
        if sessions
            .local_session_capacity()
            .is_some_and(|capacity| config.max_active_sessions > capacity)
        {
            return Err(err("session_store_capacity_mismatch"));
        }
        let instance = Self::fresh_instance(node);
        let peers = make_peers(&instance)?;
        let started = Instant::now();
        let lease = timeout(
            config.request_timeout,
            membership.register(
                instance.clone(),
                RuntimeRole::Worker,
                control_address,
                config.lease_ttl,
            ),
        )
        .await
        .map_err(|_| err("registration_unknown"))?
        .map_err(store_error)?;
        let valid_until = started
            .checked_add(config.lease_ttl)
            .ok_or_else(|| err("lease_budget"))?;
        if Instant::now() >= valid_until {
            return Err(err("registration_expired_in_flight"));
        }
        tracing::info!(node=%instance.node, incarnation=%instance.incarnation, "Worker registration confirmed");
        Ok(Arc::new(Self {
            instance,
            lease,
            config,
            membership,
            owners,
            sessions,
            inbox,
            scheduling,
            peers,
            initialize,
            state: Mutex::new(State {
                draining: false,
                stopped: false,
                started: Instant::now(),
                valid_until,
                last_heartbeat: started,
                confirmed_alive: true,
                shards: BTreeMap::new(),
                sessions: BTreeMap::new(),
                scans: BTreeMap::new(),
                acquisitions: BTreeMap::new(),
                retries: BTreeMap::new(),
                priority_shards: VecDeque::new(),
                priority_turn: true,
                recovery_cursor: None,
                recovery_priority: VecDeque::new(),
                recovery_turn: true,
                member_cursor: None,
                rejected_targets: BTreeMap::new(),
                acquisition_cursor: None,
                retry_turn: false,
                incoming: BTreeMap::new(),
                claims: BTreeMap::new(),
                outgoing: BTreeMap::new(),
                candidates_since: BTreeMap::new(),
                shard_cursor: 0,
                drain_cursor: None,
                transfer_cursors: BTreeMap::new(),
                drain_progress: 0,
                drain_retries: BTreeMap::new(),
            }),
            tick_guard: AsyncMutex::new(()),
            feeds: Mutex::new(BTreeMap::new()),
            reader_restarts: std::sync::atomic::AtomicU64::new(0),
            scan_budget: Mutex::new(None),
        }))
    }
    pub fn instance(&self) -> &Instance {
        &self.instance
    }
    pub(crate) fn state(&self) -> Result<std::sync::MutexGuard<'_, State>, RuntimeError> {
        self.state.lock().map_err(|_| err("worker_state_poisoned"))
    }
    pub(crate) fn ready_locked(&self, state: &State) -> bool {
        !state.draining
            && !state.stopped
            && state.confirmed_alive
            && Instant::now() < state.valid_until
            && state.started.elapsed() >= self.config.lease_ttl
    }
    pub(crate) async fn self_alive(&self) -> Result<bool, RuntimeError> {
        let alive = timeout(
            self.config.request_timeout,
            self.membership.liveness(&self.instance),
        )
        .await;
        let known = matches!(alive, Ok(Ok(Liveness::Alive)));
        let mut state = self.state()?;
        state.confirmed_alive = known;
        Ok(known && Instant::now() < state.valid_until)
    }
    /// Supervised heartbeat and scanning run independently, so a busy recovery
    /// scan cannot starve renewal. This future owns both loops; it spawns no
    /// detached tasks. The host calls drain from its shutdown hook, then signals
    /// stop. Stopping without drain deliberately falls back to lease expiration.
    pub async fn run(&self, stop: tokio::sync::watch::Receiver<bool>) -> Result<(), RuntimeError> {
        if *stop.borrow() {
            return Ok(());
        }
        let mut heartbeat_stop = stop.clone();
        let mut scan_stop = stop;
        let heartbeat = async {
            let mut interval = tokio::time::interval(self.config.heartbeat_interval);
            loop {
                tokio::select! {
                    changed=heartbeat_stop.changed()=>{if changed.is_err() || *heartbeat_stop.borrow() {return Ok(())}},
                    _=interval.tick()=>{
                        if self.state()?.stopped {return Ok(())}
                        if let Err(error)=self.heartbeat().await {
                            tracing::warn!(node=%self.instance.node, incarnation=%self.instance.incarnation, code=%error.code, "Worker heartbeat unavailable");
                            if error.code=="membership_lost" {return Err(error)}
                        }
                    }
                }
            }
        };
        let scanning = async {
            let mut interval = tokio::time::interval(self.config.check_interval);
            loop {
                tokio::select! {
                    changed=scan_stop.changed()=>{if changed.is_err() || *scan_stop.borrow() {return Ok(())}},
                    _=interval.tick()=>{
                        if self.state()?.stopped {return Ok(())}
                        if let Err(error)=self.tick().await {
                            tracing::warn!(node=%self.instance.node, incarnation=%self.instance.incarnation, code=%error.code, "Worker coordination scan deferred");
                        }
                    }
                }
            }
        };
        tokio::select! {result=heartbeat=>result,result=scanning=>result}
    }
    /// One bounded iteration. The caller may drive this from its existing task
    /// supervisor; this method never spawns an untracked detached loop.
    pub async fn tick(&self) -> Result<(), RuntimeError> {
        let _guard = self.tick_guard.lock().await;
        self.heartbeat().await?;
        if !self.self_alive().await? || !self.ready_locked(&*self.state()?) {
            return Ok(());
        }
        self.resolve_claims().await?;
        let mut acquire_budget = self.config.max_acquires_per_tick;
        // Reserve at least half the round for newly discovered work. Unknown
        // requests retain responsibility without monopolizing every round.
        let mut retry_budget = if acquire_budget == 1 {
            let mut state = self.state()?;
            state.retry_turn = !state.retry_turn;
            usize::from(state.retry_turn)
        } else {
            acquire_budget / 2
        };
        let retry_limit = retry_budget;
        if let Err(error) = self.resolve_acquisition_budget(&mut retry_budget).await {
            tracing::warn!(node=%self.instance.node, incarnation=%self.instance.incarnation, code=%error.code, "Worker acquisition confirmation deferred");
        }
        acquire_budget -= retry_limit - retry_budget;
        let mut failure = None;
        let candidates = self.candidates().await?;
        let count = self
            .config
            .shards
            .len()
            .min(self.config.max_shards_per_tick);
        let mut liveness = BTreeMap::new();
        let mut visited = std::collections::BTreeSet::new();
        for _ in 0..count {
            if !self.ready_locked(&*self.state()?) {
                break;
            }
            let shard = {
                let mut s = self.state()?;
                // Alternate priority hints with the persistent scan so neither
                // handoffs nor ordinary shards can starve the other.
                let priority = if s.priority_turn && !s.priority_shards.is_empty() {
                    s.priority_turn = false;
                    s.priority_shards.pop_front()
                } else {
                    s.priority_turn = true;
                    None
                };
                priority.unwrap_or_else(|| {
                    let shard = self.config.shards[s.shard_cursor % self.config.shards.len()];
                    s.shard_cursor = (s.shard_cursor + 1) % self.config.shards.len();
                    shard
                })
            };
            if !visited.insert(shard) {
                continue;
            }
            let current = match self.bounded(self.owners.current(shard)).await {
                Ok(current) => current,
                Err(error) => {
                    failure.get_or_insert(error);
                    continue;
                }
            };
            if let Some(record) = &current {
                if record.claim.owner.instance == self.instance {
                    self.state()?.shards.insert(shard, record.claim.clone());
                    if let Some(h) = &record.handoff {
                        let _ = self
                            .bounded(self.owners.clear_handoff(&record.claim, &h.request_id))
                            .await?;
                    }
                    continue;
                }
                {
                    let mut state = self.state()?;
                    state.shards.remove(&shard);
                    state.retries.retain(|_, (claim, _)| claim.shard != shard);
                }
                // Do not rebalance healthy owners; uncertain liveness is not death.
                let identity = (
                    &record.claim.owner.instance.node,
                    &record.claim.owner.instance.incarnation,
                );
                let key = (identity.0.clone(), identity.1.clone());
                let live = if let Some(live) = liveness.get(&key) {
                    *live
                } else {
                    let live = self
                        .bounded(self.membership.liveness(&record.claim.owner.instance))
                        .await?;
                    liveness.insert(key, live);
                    live
                };
                if live != Liveness::Dead {
                    self.state()?.candidates_since.remove(&shard);
                    continue;
                }
            }
            let instances = candidates
                .iter()
                .map(|m| m.instance.clone())
                .collect::<Vec<_>>();
            let ranked = rank_candidates(shard, &instances, self.config.identity);
            let Some(rank) = ranked.iter().position(|i| *i == self.instance) else {
                continue;
            };
            let since = *self
                .state()?
                .candidates_since
                .entry(shard)
                .or_insert_with(Instant::now);
            if since.elapsed()
                < self
                    .config
                    .candidate_backoff
                    .saturating_mul(u32::try_from(rank).unwrap_or(u32::MAX))
            {
                continue;
            }
            let request = ClaimShard {
                shard,
                me: self.instance.clone(),
                expected_epoch: current.map(|r| r.claim.owner.epoch),
                request_id: RequestId(format!("claim:{}:{}", self.instance.incarnation, shard.0)),
            };
            {
                let mut state = self.state()?;
                if !self.ready_locked(&state) {
                    break;
                }
                state
                    .claims
                    .entry(shard)
                    .or_insert((request.clone(), false, Instant::now()));
            }
            if let Err(error) = self.issue_claim(request).await {
                failure.get_or_insert(error);
            }
        }
        if let Err(error) = self.recover_owned(&mut acquire_budget).await {
            failure.get_or_insert(error);
        }
        // Use unused new-work budget to confirm outstanding requests.
        if acquire_budget > 0
            && let Err(error) = self.resolve_acquisition_budget(&mut acquire_budget).await
        {
            failure.get_or_insert(error);
        }
        self.close_unused_feeds()?;
        failure.map_or(Ok(()), Err)
    }
    pub(crate) async fn bounded<T>(
        &self,
        future: impl Future<Output = Result<T, StoreError>>,
    ) -> Result<T, RuntimeError> {
        timeout(self.config.request_timeout, future)
            .await
            .map_err(|_| err("store_outcome_unknown"))?
            .map_err(store_error)
    }
    pub(crate) async fn issue_claim(&self, request: ClaimShard) -> Result<(), RuntimeError> {
        let shard = request.shard;
        let prior_unknown = {
            let mut state = self.state()?;
            if state.draining
                && state
                    .claims
                    .get(&shard)
                    .is_some_and(|(_, _, at)| *at > Instant::now())
            {
                return Ok(());
            }
            let prior = state
                .claims
                .get(&shard)
                .is_some_and(|(_, unknown, _)| *unknown);
            if let Some((_, unknown, retry_at)) = state.claims.get_mut(&shard) {
                *unknown = true;
                // Record before I/O so cancellation also respects backoff.
                *retry_at = Instant::now() + self.config.check_interval;
            }
            prior
        };
        match self.bounded(self.owners.claim(request.clone())).await {
            Ok(LeaseClaim::Claimed(claim)) | Ok(LeaseClaim::AlreadyClaimed(claim)) => {
                tracing::info!(shard=shard.0, epoch=claim.owner.epoch.0, node=%self.instance.node, incarnation=%self.instance.incarnation, request_id=%request.request_id.0, "Worker shard claim confirmed");
                let mut state = self.state()?;
                state.shards.insert(shard, claim);
                state.claims.remove(&shard);
                state.made_progress();
            }
            Ok(LeaseClaim::Conflict) => {
                tracing::debug!(shard=shard.0, request_id=%request.request_id.0, "Worker shard claim conflicted");
                if !prior_unknown {
                    let mut state = self.state()?;
                    state.claims.remove(&shard);
                    state.made_progress();
                } else if let Some(record) = self.bounded(self.owners.current(shard)).await?
                    && record.claim.owner.epoch.0 > request.expected_epoch.map_or(0, |e| e.0)
                {
                    let mut state = self.state()?;
                    if record.claim.owner.instance == self.instance {
                        state.shards.insert(shard, record.claim);
                    }
                    state.claims.remove(&shard);
                    state.made_progress();
                }
            }
            Err(error) => {
                tracing::warn!(shard=shard.0, request_id=%request.request_id.0, code=%error.code, "Worker shard claim awaits confirmation");
                if let Some((_, unknown, _)) = self.state()?.claims.get_mut(&shard) {
                    *unknown = true;
                }
                return Err(error);
            }
        }
        Ok(())
    }
    pub(crate) async fn resolve_claims(&self) -> Result<(), RuntimeError> {
        let claims = {
            let state = self.state()?;
            state
                .claims
                .values()
                .filter(|(_, _, at)| !state.draining || *at <= Instant::now())
                .map(|(request, _, _)| request.clone())
                .collect::<Vec<_>>()
        };
        let mut failure = None;
        for request in claims.into_iter().take(self.config.max_shards_per_tick) {
            if let Err(error) = self.issue_claim(request).await {
                failure.get_or_insert(error);
            }
        }
        failure.map_or(Ok(()), Err)
    }
    pub async fn heartbeat(&self) -> Result<(), RuntimeError> {
        if self.state()?.stopped {
            return Err(err("worker_stopped"));
        }
        let start = Instant::now();
        if self.state()?.last_heartbeat.elapsed() < self.config.heartbeat_interval {
            return Ok(());
        }
        self.state()?.last_heartbeat = start;
        match timeout(
            self.config.request_timeout,
            self.membership.renew(&self.lease, self.config.lease_ttl),
        )
        .await
        {
            Ok(Ok(Renewal::Renewed)) => {
                let mut s = self.state()?;
                s.confirmed_alive = true;
                s.valid_until = start + self.config.lease_ttl;
                Ok(())
            }
            Ok(Ok(Renewal::Lost)) => {
                tracing::warn!(node=%self.instance.node, incarnation=%self.instance.incarnation, "Worker lease lost");
                self.state()?.confirmed_alive = false;
                Err(err("membership_lost"))
            }
            _ => {
                self.state()?.confirmed_alive = false;
                Err(err("membership_unknown"))
            }
        }
    }
    pub(crate) async fn candidates(&self) -> Result<Vec<MemberRecord>, RuntimeError> {
        let mut after = self.state()?.member_cursor.clone();
        let mut result = vec![];
        for _ in 0..self.config.max_member_pages {
            let page = self
                .bounded(
                    self.membership.members(
                        RuntimeRole::Worker,
                        PageRequest {
                            after,
                            limit: NonZeroU32::new(self.config.page_size)
                                .ok_or_else(|| err("page_size"))?,
                        },
                    ),
                )
                .await?;
            result.extend(page.items);
            after = page.next;
            self.state()?.member_cursor = after.clone();
            if after.is_none() {
                return Ok(result);
            }
        }
        // A partial candidate view can contend safely, but must remain bounded.
        Ok(result)
    }
    /// Newly acquired sessions are Recovering, even if their outbox is empty.
    /// The embedding processor must reconcile persisted effects and acknowledge
    /// completion before begin_round can accept new input.
    pub fn acquired_sessions(&self) -> Result<Vec<AcquiredSession>, RuntimeError> {
        Ok(self
            .state()?
            .sessions
            .values()
            .filter(|s| s.phase == Phase::Recovering)
            .map(|s| s.acquired.clone())
            .collect())
    }
    pub async fn recovery_completed(&self, owner: &SessionOwnership) -> Result<(), RuntimeError> {
        let after = {
            let state = self.state()?;
            let session = state
                .sessions
                .get(&owner.sid)
                .ok_or_else(|| err("session_not_active"))?;
            if session.acquired.ownership != *owner || session.phase != Phase::Recovering {
                return Err(err("session_recovery_state"));
            }
            session.acquired.envelope.checkpoint.consumed.clone()
        };
        let feed = self.feed(owner.shard).await?;
        self.bounded(async {
            feed.lock()
                .await
                .as_mut()
                .ok_or_else(reader_rebuilding)?
                .watch(owner.sid.clone(), after)
                .await
        })
        .await?;
        let mut state = self.state()?;
        let s = state
            .sessions
            .get_mut(&owner.sid)
            .ok_or_else(|| err("session_not_active"))?;
        if s.acquired.ownership != *owner || s.phase != Phase::Recovering {
            return Err(err("session_recovery_state"));
        }
        s.pending_effects = 0;
        s.phase = Phase::Idle;
        Ok(())
    }
    pub(crate) async fn feed(&self, shard: ShardId) -> Result<SharedFeed, RuntimeError> {
        let shared = self
            .feeds
            .lock()
            .map_err(|_| err("worker_feed_poisoned"))?
            .entry(shard)
            .or_insert_with(|| Arc::new(AsyncMutex::new(None)))
            .clone();
        {
            let mut guard = shared.lock().await;
            if guard.is_none() {
                let opened = self.bounded(self.inbox.open(shard)).await?;
                let watched = self
                    .state()?
                    .sessions
                    .values()
                    .filter(|s| s.acquired.ownership.shard == shard && s.phase != Phase::Releasing)
                    .map(|s| {
                        (
                            s.acquired.ownership.sid.clone(),
                            s.acquired.envelope.checkpoint.consumed.clone(),
                        )
                    })
                    .collect::<Vec<_>>();
                *guard = Some(Box::new(crate::rebuilding_feed::RebuildingFeed::new(
                    opened,
                    watched,
                    self.config.request_timeout,
                )));
            }
        }
        Ok(shared)
    }
    pub async fn read_inputs(
        &self,
        shard: ShardId,
        limits: ReadLimits,
        wait: std::time::Duration,
    ) -> Result<FeedBatch, RuntimeError> {
        if !self.self_alive().await?
            || !self.ready_locked(&*self.state()?)
            || !self.state()?.shards.contains_key(&shard)
        {
            return Err(err("worker_not_ready"));
        }
        let feed = self.feed(shard).await?;
        self.bounded(async {
            feed.lock()
                .await
                .as_mut()
                .ok_or_else(reader_rebuilding)?
                .next(limits, wait.min(self.config.request_timeout))
                .await
        })
        .await
    }
    /// The embedding processor checks this before issuing another commit/effect.
    /// Shutdown cannot revoke an external operation already issued; persisted
    /// fencing and effect idempotency remain mandatory.
    pub fn may_continue_round(&self, owner: &SessionOwnership) -> Result<bool, RuntimeError> {
        let state = self.state()?;
        Ok(!state.stopped
            && state.confirmed_alive
            && Instant::now() < state.valid_until
            && state
                .sessions
                .get(&owner.sid)
                .is_some_and(|s| s.acquired.ownership == *owner))
    }
    pub fn begin_round(&self, sid: &Sid) -> Result<AcquiredSession, RuntimeError> {
        let mut state = self.state()?;
        if !self.ready_locked(&state) {
            return Err(err("worker_not_ready"));
        }
        let ownership = state
            .sessions
            .get(sid)
            .ok_or_else(|| err("session_not_active"))?
            .acquired
            .ownership
            .clone();
        if !state
            .shards
            .get(&ownership.shard)
            .is_some_and(|claim| claim.owner == ownership.owner)
        {
            return Err(err("shard_no_longer_owned"));
        }
        let s = state
            .sessions
            .get_mut(sid)
            .ok_or_else(|| err("session_not_active"))?;
        if s.phase != Phase::Idle || s.pending_schedule {
            return Err(err("session_not_quiescent"));
        }
        s.phase = Phase::Running;
        Ok(s.acquired.clone())
    }
    /// A confirmed checkpoint may still have effects to replay. Uncertain commit
    /// callers use round_unknown instead, never this method with guessed state.
    pub fn checkpoint_confirmed(
        &self,
        owner: &SessionOwnership,
        envelope: CheckpointEnvelope,
        receipt: CommitReceipt,
        pending_effects: u64,
    ) -> Result<(), RuntimeError> {
        let mut state = self.state()?;
        let s = state
            .sessions
            .get_mut(&owner.sid)
            .ok_or_else(|| err("session_not_active"))?;
        if s.acquired.ownership != *owner
            || envelope.owner != owner.owner
            || !matches!(s.phase, Phase::Running | Phase::Unknown)
        {
            return Err(err("session_round_state"));
        }
        if receipt.sid != owner.sid
            || receipt.owner != owner.owner
            || receipt.sequence != envelope.checkpoint.commit_seq
            || receipt.consumed != envelope.checkpoint.consumed
            || !envelope.checkpoint.outbox.as_ref().is_some_and(|batch| {
                batch.commit_id == receipt.commit_id
                    && batch.content_digest == receipt.content_digest
                    && batch.source_commit == receipt.sequence
            })
            || s.acquired.envelope.checkpoint.commit_seq.0.checked_add(1)
                != Some(envelope.checkpoint.commit_seq.0)
        {
            return Err(err("checkpoint_receipt_mismatch"));
        }
        s.acquired.last_commit = Some(receipt);
        s.acquired.envelope = envelope;
        s.pending_effects = pending_effects;
        s.phase = if pending_effects == 0 {
            Phase::Idle
        } else {
            Phase::Effects
        };
        Ok(())
    }
    pub fn effects_completed(&self, owner: &SessionOwnership) -> Result<(), RuntimeError> {
        let mut state = self.state()?;
        let s = state
            .sessions
            .get_mut(&owner.sid)
            .ok_or_else(|| err("session_not_active"))?;
        if s.acquired.ownership != *owner || s.phase != Phase::Effects {
            return Err(err("session_effect_state"));
        }
        s.pending_effects = 0;
        s.phase = Phase::Idle;
        Ok(())
    }
    pub fn round_unknown(&self, owner: &SessionOwnership) -> Result<(), RuntimeError> {
        let mut state = self.state()?;
        let s = state
            .sessions
            .get_mut(&owner.sid)
            .ok_or_else(|| err("session_not_active"))?;
        if s.acquired.ownership != *owner {
            return Err(err("session_owner_changed"));
        }
        s.phase = Phase::Unknown;
        Ok(())
    }
    /// Release only quiescent local state. This never removes persistent pending
    /// work or changes ownership; a later scan can recover it again if required.
    pub async fn release_session(&self, owner: &SessionOwnership) -> Result<(), RuntimeError> {
        {
            let mut state = self.state()?;
            let s = state
                .sessions
                .get_mut(&owner.sid)
                .ok_or_else(|| err("session_not_active"))?;
            if s.acquired.ownership != *owner
                || !matches!(s.phase, Phase::Idle | Phase::Releasing)
                || s.pending_schedule
                || s.pending_effects != 0
            {
                return Err(err("session_not_quiescent"));
            }
            s.phase = Phase::Releasing;
            s.drain_retry_at = Instant::now() + self.config.check_interval;
        }
        let feed = self
            .feeds
            .lock()
            .map_err(|_| err("worker_feed_poisoned"))?
            .get(&owner.shard)
            .cloned();
        if let Some(feed) = feed {
            self.bounded(async {
                feed.lock()
                    .await
                    .as_mut()
                    .ok_or_else(reader_rebuilding)?
                    .unwatch(&owner.sid)
                    .await
            })
            .await?;
        }
        // Cleanup follows transfer/unwatch I/O; its own retry interval starts
        // here, not at the earlier batch-selection or transfer timestamp.
        if let Some(session) = self.state()?.sessions.get_mut(&owner.sid) {
            session.drain_retry_at = Instant::now() + self.config.check_interval;
        }
        self.bounded(self.sessions.release_local(owner)).await?;
        let mut state = self.state()?;
        if state
            .sessions
            .get(&owner.sid)
            .is_some_and(|s| s.acquired.ownership == *owner && s.phase == Phase::Releasing)
        {
            state.sessions.remove(&owner.sid);
            state.made_progress();
        }
        drop(state);
        self.close_unused_feeds()?;
        Ok(())
    }
    pub(crate) fn close_unused_feeds(&self) -> Result<(), RuntimeError> {
        let state = self.state()?;
        self.feeds
            .lock()
            .map_err(|_| err("worker_feed_poisoned"))?
            .retain(|shard, _| {
                state
                    .sessions
                    .values()
                    .any(|s| s.acquired.ownership.shard == *shard)
                    || state.acquisitions.values().any(|a| a.claim.shard == *shard)
            });
        Ok(())
    }
    pub(crate) fn prioritize(&self, shard: ShardId) -> Result<(), RuntimeError> {
        let mut state = self.state()?;
        if !state.priority_shards.contains(&shard) {
            state.priority_shards.push_back(shard);
        }
        if !state.recovery_priority.contains(&shard) {
            state.recovery_priority.push_back(shard);
        }
        if let Some(scan) = state.scans.get_mut(&shard) {
            scan.next = None;
        }
        for (claim, due) in state.retries.values_mut() {
            if claim.shard == shard {
                *due = Instant::now();
            }
        }
        Ok(())
    }
    /// Pair every issued settle with completion or leave it pending on unknown.
    /// Draining forbids issuing another settle that could postpone pending work.
    pub fn scheduling_started(&self, owner: &SessionOwnership) -> Result<(), RuntimeError> {
        let mut state = self.state()?;
        if !self.ready_locked(&state) {
            return Err(err("worker_not_ready"));
        }
        let s = state
            .sessions
            .get_mut(&owner.sid)
            .ok_or_else(|| err("session_not_active"))?;
        if s.acquired.ownership != *owner || s.pending_schedule {
            return Err(err("session_schedule_state"));
        }
        s.pending_schedule = true;
        Ok(())
    }
    pub fn scheduling_completed(&self, owner: &SessionOwnership) -> Result<(), RuntimeError> {
        let mut state = self.state()?;
        let s = state
            .sessions
            .get_mut(&owner.sid)
            .ok_or_else(|| err("session_not_active"))?;
        if s.acquired.ownership != *owner {
            return Err(err("session_owner_changed"));
        }
        s.pending_schedule = false;
        Ok(())
    }
}
pub(crate) fn err(code: &str) -> RuntimeError {
    RuntimeError { code: code.into() }
}
pub(crate) fn store_error(error: StoreError) -> RuntimeError {
    err(match error {
        StoreError::OutcomeUnknown { .. } => "store_outcome_unknown",
        StoreError::NotApplied { ref code } if code == "session_control_paused" => {
            "session_control_paused"
        }
        StoreError::NotApplied { .. } => "store_not_applied",
        StoreError::Invalid { .. } => "store_invalid",
        StoreError::UnsupportedFormat { .. } => "store_format",
    })
}

pub(crate) fn reader_rebuilding() -> StoreError {
    StoreError::NotApplied {
        code: "reader_rebuilding".into(),
    }
}
