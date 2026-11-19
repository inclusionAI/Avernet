use super::{effects::*, references::References};
use crate::{
    WorkerRuntime,
    runtime::{err, store_error},
};
use bcs_dist_model::*;
use bcs_service_api::{
    core::{dist_projection::OutputProjectionCoreService, dist_runner::DistRunnerCoreService},
    port::{
        dist_agent::DistGroupDirectoryPort,
        repo::distributed::{
            admission::*, budget::*, common::*, control::*, dispatch::*, inbox::*, result::*,
            session::*, task_feed::*,
        },
    },
    types::dist_runtime::RuntimeError,
};
use futures::{StreamExt, stream::FuturesUnordered};
use std::{collections::BTreeMap, num::NonZeroU32, sync::Arc, time::Duration};
use tokio::sync::Mutex;

/// Host supplies services, time and storage; no concrete provider or Agent client
/// belongs to the Worker processor. `run` also owns coordination/heartbeat loops.
pub struct ProcessorPorts {
    pub scan_budget: Arc<dyn ScanBudgetPort>,
    pub control: Arc<dyn SessionControlPort>,
    pub admission: Arc<dyn AdmissionClosurePort>,
    pub runner: Arc<dyn DistRunnerCoreService>,
    pub projection: Arc<dyn OutputProjectionCoreService>,
    pub directory: Arc<dyn DistGroupDirectoryPort>,
    pub tasks: Arc<dyn TaskReaderPort>,
    pub output: Arc<dyn TaskPublisherPort>,
    pub blobs: Arc<dyn BlobStorePort>,
    pub results: Arc<dyn ResultStorePort>,
    pub dispatch: Arc<dyn DispatchControlPort>,
    pub now: Arc<dyn Fn() -> TimestampMillis + Send + Sync>,
}
#[derive(Debug, Clone)]
pub struct ProcessorConfig {
    pub concurrency: usize,
    pub max_sessions: usize,
    pub request_timeout: Duration,
    pub poll_interval: Duration,
    pub retry_delay: Duration,
    /// Temporary remote Thinking is lossy above this per-Worker token rate.
    pub thinking_events_per_second: u32,
    pub max_commits_per_turn: u32,
    /// Reserved for the actual provider envelope/control metadata, above the
    /// Task's separate finalization reserve. Checked again after digest encoding.
    pub envelope_reserve_bytes: usize,
}
impl Default for ProcessorConfig {
    fn default() -> Self {
        Self {
            concurrency: 4,
            max_sessions: 128,
            request_timeout: Duration::from_secs(2),
            poll_interval: Duration::from_millis(50),
            retry_delay: Duration::from_millis(200),
            thinking_events_per_second: 32,
            max_commits_per_turn: 8,
            envelope_reserve_bytes: 64 * 1024,
        }
    }
}
pub(super) struct Prepared {
    pub candidate: Checkpoint,
    pub finalizing: bool,
    pub id: CommitId,
    pub purpose: CommitPurpose,
}
pub(super) struct Slot {
    pub acquired: AcquiredSession,
    pub commits_since_yield: u32,
    pub control: Option<ControlSnapshot>,
    pub risk_outcome: Option<(AttemptIdentity, AttemptOutcome)>,
    pub risk_begin: Option<(AttemptIdentity, ReplayIdentity, u64)>,
    pub spec: Option<SessionSpec>,
    pub dispatch_specs: BTreeMap<Did, DispatchSpec>,
    pub recovering: bool,
    pub effects_pending: bool,
    pub completed: Option<CompletedEffects>,
    pub replay: Option<ReplayProgress>,
    pub input: Option<SessionBatch>,
    pub delivery_valid: bool,
    pub reader_paused: bool,
    pub releasing: bool,
    pub parking_replay: bool,
    pub prepared: Option<Prepared>,
    pub evaluation: Option<super::prepare::FrozenDecision>,
    pub settlement: Option<SettleSession>,
    pub pending: Option<CommitRequest>,
    pub pending_since: Option<tokio::time::Instant>,
    pub round_started: bool,
    pub retry_at: tokio::time::Instant,
    pub ack_pending: bool,
}
impl Slot {
    fn new(acquired: AcquiredSession) -> Self {
        let ack_pending = acquired.last_commit.is_some();
        Self {
            effects_pending: acquired.envelope.checkpoint.outbox.is_some(),
            acquired,
            commits_since_yield: 0,
            control: None,
            risk_outcome: None,
            risk_begin: None,
            spec: None,
            dispatch_specs: BTreeMap::new(),
            recovering: true,
            completed: None,
            replay: None,
            input: None,
            delivery_valid: false,
            reader_paused: false,
            releasing: false,
            parking_replay: false,
            prepared: None,
            pending: None,
            pending_since: None,
            evaluation: None,
            settlement: None,
            round_started: false,
            retry_at: tokio::time::Instant::now(),
            ack_pending,
        }
    }
    pub fn idle(&self) -> bool {
        !self.recovering
            && !self.effects_pending
            && self.pending.is_none()
            && self.prepared.is_none()
            && self.evaluation.is_none()
            && self.settlement.is_none()
            && !self.ack_pending
            && !self.releasing
    }
}
#[derive(Default)]
pub(super) struct ProcessorState {
    pub(super) slots: BTreeMap<Sid, Slot>,
    cursor: Option<Sid>,
    shard_cursor: usize,
}
pub struct WorkerProcessor {
    pub(super) runtime: Arc<WorkerRuntime>,
    pub(super) ports: ProcessorPorts,
    pub(super) config: ProcessorConfig,
    pub(super) references: References,
    pub(super) effects: EffectPorts,
    pub(super) thinking_budget: std::sync::Mutex<super::thinking::ThinkingBudget>,
    pub(super) state: Mutex<ProcessorState>,
}
impl WorkerProcessor {
    pub fn new(
        runtime: Arc<WorkerRuntime>,
        ports: ProcessorPorts,
        config: ProcessorConfig,
    ) -> Result<Self, RuntimeError> {
        if config.concurrency == 0
            || config.concurrency > 32
            || config.max_sessions == 0
            || config.max_sessions > 128
            || runtime.config.max_active_sessions > config.max_sessions
            || runtime.config.page_size > 128
            || config.request_timeout.is_zero()
            || config.request_timeout > Duration::from_secs(2)
            || config.poll_interval.is_zero()
            || config.retry_delay < Duration::from_millis(5)
            || config.retry_delay > Duration::from_secs(5)
            || !(1..=128).contains(&config.thinking_events_per_second)
            || !(1..=128).contains(&config.max_commits_per_turn)
            || config.envelope_reserve_bytes < 64 * 1024
            || config.envelope_reserve_bytes > 128 * 1024
        {
            return Err(err("processor_config"));
        }
        super::capacity::validate(runtime.sessions.checkpoint_capacity(), config.envelope_reserve_bytes)
            .map_err(store_error)?;
        let profile = ports.scan_budget.profile();
        if ports.dispatch.scan_budget_profile().as_ref() != Some(&profile) {
            return Err(err("processor_scan_budget_mismatch"));
        }
        runtime.install_scan_budget(ports.scan_budget.clone())?;
        let references = References {
            blobs: ports.blobs.clone(),
            timeout: config.request_timeout,
            envelope_reserve: config.envelope_reserve_bytes,
        };
        let effects = EffectPorts {
            output: ports.output.clone(),
            results: ports.results.clone(),
            dispatch: ports.dispatch.clone(),
        };
        Ok(Self {
            thinking_budget: std::sync::Mutex::new(super::thinking::ThinkingBudget::new(
                config.thinking_events_per_second,
            )),
            runtime,
            ports,
            config,
            references,
            effects,
            state: Mutex::new(ProcessorState::default()),
        })
    }
    pub async fn run(&self, stop: tokio::sync::watch::Receiver<bool>) -> Result<(), RuntimeError> {
        let mut processor_stop = stop.clone();
        let processing = async {
            let mut interval = tokio::time::interval(self.config.poll_interval);
            loop {
                tokio::select! {
                    changed = processor_stop.changed() => if changed.is_err() || *processor_stop.borrow() { return Ok(()) },
                    _ = interval.tick() => {
                        if *processor_stop.borrow() { return Ok(()) }
                        if let Err(error) = self.tick().await {
                            tracing::warn!(code=%error.code, "Worker processor iteration deferred");
                        }
                    }
                }
            }
        };
        tokio::select! { result = self.runtime.run(stop) => result, result = processing => result }
    }
    /// At most one round per Session, bounded concurrent Sessions, rotating every
    /// visit (including failures). Unknown requests remain in their original slot.
    pub async fn tick(&self) -> Result<(), RuntimeError> {
        let mut state = self.state.lock().await;
        let active = {
            self.runtime
                .state()?
                .sessions
                .iter()
                .map(|(sid, s)| (sid.clone(), s.acquired.ownership.clone()))
                .collect::<BTreeMap<_, _>>()
        };
        state
            .slots
            .retain(|sid, slot| active.get(sid) == Some(&slot.acquired.ownership));
        for acquired in self.runtime.acquired_sessions()? {
            if let Some(slot) = state.slots.get_mut(&acquired.ownership.sid) {
                // Reacquisition can use the same shard owner/epoch after a local
                // idle release. Runtime Recovering is the new lifecycle signal.
                if !slot.recovering {
                    *slot = Slot::new(acquired);
                }
            } else if state.slots.len() < self.config.max_sessions {
                state
                    .slots
                    .insert(acquired.ownership.sid.clone(), Slot::new(acquired));
            }
        }
        let mut failure = self.diagnose_acquisition().await.err();
        if let Err(error) = self.read_one(&mut state).await {
            failure.get_or_insert(error);
        }
        let after = state.cursor.as_ref();
        let now = tokio::time::Instant::now();
        let selected = state
            .slots
            .iter()
            .filter(|(sid, _)| after.is_none_or(|a| *sid > a))
            .chain(
                state
                    .slots
                    .iter()
                    .filter(|(sid, _)| after.is_some_and(|a| *sid <= a)),
            )
            .filter(|(_, slot)| slot.retry_at <= now)
            .take(self.config.concurrency)
            .map(|(sid, _)| sid.clone())
            .collect::<Vec<_>>();
        state.cursor = selected.last().cloned().or(state.cursor.clone());
        let mut results = FuturesUnordered::new();
        for (sid, slot) in &mut state.slots {
            if selected.contains(sid) {
                results.push(self.visit(sid.clone(), slot));
            }
        }
        // Slots stay in the map throughout every await. Cancellation drops only
        // the future, never a frozen candidate or its unknown commit identity.
        while let Some(result) = results.next().await {
            if let Err(error) = result {
                failure.get_or_insert(error);
            }
        }
        failure.map_or(Ok(()), Err)
    }
    async fn visit(&self, sid: Sid, slot: &mut Slot) -> Result<(), RuntimeError> {
        let mut result = self.step(slot).await;
        if let Err(error) = &result
            && super::diagnostics::requires_block(&error.code)
        {
            let code = error.code.clone();
            result = self.block(slot, &code).await;
        }
        if let Err(error) = &result {
            slot.retry_at = tokio::time::Instant::now() + self.config.retry_delay;
            tracing::warn!(sid=%sid.0, code=%error.code, "Worker session round deferred");
        }
        result
    }
    async fn read_one(&self, state: &mut ProcessorState) -> Result<(), RuntimeError> {
        let shards = state
            .slots
            .values()
            .filter(|s| {
                s.idle()
                    && s.input.is_none()
                    && s.commits_since_yield < self.config.max_commits_per_turn
            })
            .map(|s| s.acquired.ownership.shard)
            .collect::<std::collections::BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        if shards.is_empty() {
            return Ok(());
        }
        let shard = shards[state.shard_cursor % shards.len()];
        state.shard_cursor = state.shard_cursor.wrapping_add(1);
        let limits = ReadLimits {
            max_sessions: NonZeroU32::MIN,
            max_entries: NonZeroU32::MIN,
            max_bytes: NonZeroU32::new(128 * 1024).ok_or_else(|| err("processor_input_budget"))?,
        };
        let batch = match self
            .runtime
            .read_inputs(shard, limits, Duration::ZERO)
            .await
        {
            Ok(batch) => batch,
            Err(error) => {
                for slot in state
                    .slots
                    .values_mut()
                    .filter(|s| s.acquired.ownership.shard == shard)
                {
                    slot.delivery_valid = false;
                }
                self.runtime.rebuild_feed(shard).await?;
                return Err(error);
            }
        };
        let mut repairs = Vec::new();
        // Store every delivered batch before awaiting failure recovery. A failed
        // sibling watch must not discard input whose reader cursor advanced.
        for batch in batch.sessions {
            if let Some(slot) = state.slots.get_mut(&batch.sid) {
                if slot.idle()
                    && slot.input.is_none()
                    && slot.commits_since_yield < self.config.max_commits_per_turn
                {
                    slot.input = Some(batch);
                    slot.delivery_valid = true;
                } else {
                    slot.delivery_valid = false;
                    slot.reader_paused = true;
                    repairs.push((slot.acquired.ownership.clone(), true));
                }
            }
        }
        for failure in batch.failures {
            if let Some(slot) = state.slots.get_mut(&failure.sid) {
                slot.delivery_valid = false;
                repairs.push((slot.acquired.ownership.clone(), false));
            }
        }
        let mut failure = None;
        for (owner, pause) in repairs {
            let result = if pause {
                self.runtime.pause_inputs(&owner).await
            } else {
                self.runtime.rewatch_inputs(&owner).await
            };
            if let Err(error) = result {
                failure.get_or_insert(error);
            }
        }
        failure.map_or(Ok(()), Err)
    }
    pub(super) async fn io<T>(
        &self,
        future: impl Future<Output = Result<T, StoreError>>,
    ) -> Result<T, RuntimeError> {
        tokio::time::timeout(self.config.request_timeout, future)
            .await
            .map_err(|_| err("store_outcome_unknown"))?
            .map_err(store_error)
    }
}
