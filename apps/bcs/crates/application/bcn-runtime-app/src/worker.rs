//! Bounded single-owner reducer turns; every effect follows a durable checkpoint.
use crate::{recovery::Lifecycle, *};
use async_trait::async_trait;
use bcn_runtime_api::{application::*, core::SessionRunner};
use futures::FutureExt;
use std::{
    collections::{BTreeMap, BTreeSet},
    future::Future,
    pin::Pin,
    sync::Arc,
    time::Duration,
};
use tokio::sync::Mutex;
type WorkFuture = Pin<Box<dyn Future<Output = RuntimeResult<()>> + Send>>;
struct PendingWork {
    future: WorkFuture,
    started_at: u64,
}
struct State {
    pending: BTreeMap<CommandId, PendingWork>,
    life: Lifecycle,
    owned: BTreeMap<u16, OwnershipFence>,
    cursors: BTreeMap<u16, Option<String>>,
    shard: u16,
    owned_offset: u16,
    repair: Option<String>,
    commands: Option<String>,
    active: BTreeSet<SessionId>,
    completed: u32,
}
pub struct DefaultWorker {
    node: NodeIdentity,
    ports: RuntimePorts,
    runners: Vec<Arc<dyn SessionRunner>>,
    judge: Arc<dyn JudgeExecutor>,
    clock: Arc<dyn Clock>,
    state: Mutex<State>,
}
impl DefaultWorker {
    pub fn new(
        node: NodeIdentity,
        ports: RuntimePorts,
        runners: Vec<Arc<dyn SessionRunner>>,
        judge: Arc<dyn JudgeExecutor>,
        clock: Arc<dyn Clock>,
    ) -> RuntimeResult<Self> {
        validate_node(&node)?;
        Ok(Self {
            node,
            ports,
            runners,
            judge,
            clock,
            state: Mutex::new(State {
                pending: BTreeMap::new(),
                life: Lifecycle::default(),
                owned: BTreeMap::new(),
                cursors: BTreeMap::new(),
                shard: 0,
                owned_offset: 0,
                repair: None,
                commands: None,
                active: BTreeSet::new(),
                completed: 0,
            }),
        })
    }
    async fn ownership(
        &self,
        s: &mut State,
        shard: ShardId,
    ) -> RuntimeResult<Option<OwnershipFence>> {
        let actual = self.ports.ownership.read_owner(&shard).await?;
        let fence = match actual {
            Some(f) if f.owner == self.node => Some(f),
            None if s.life.phase == RuntimePhase::Running => Some(
                self.ports
                    .ownership
                    .claim_shard(shard.clone(), self.node.clone(), None)
                    .await?,
            ),
            Some(f)
                if s.life.phase == RuntimePhase::Running
                    && s.life.may_takeover(self.clock.now_ms()) =>
            {
                let observed = self
                    .ports
                    .liveness
                    .inspect_batch(&[f.owner.clone()])
                    .await?;
                if observed
                    .first()
                    .is_some_and(|o| o.node == f.owner && o.liveness == Liveness::Dead)
                {
                    Some(
                        self.ports
                            .ownership
                            .claim_shard(shard.clone(), self.node.clone(), Some(f))
                            .await?,
                    )
                } else {
                    None
                }
            }
            _ => None,
        };
        if let Some(f) = &fence {
            s.owned.insert(shard.number, f.clone());
        } else {
            s.owned.remove(&shard.number);
        }
        Ok(fence)
    }
    /// One deterministic session turn, useful to drive an isolated scheduler without sleeps.
    pub async fn step(&self, session: &SessionId) -> RuntimeResult<u32> {
        let mut state = self.state.lock().await;
        if state.life.phase == RuntimePhase::Stopped {
            return Ok(0);
        }
        if !state.active.contains(session)
            && state.active.len() >= RuntimeDefaults::default().recovery_concurrency as usize
        {
            return Err(RuntimeError::CapacityExceeded);
        }
        state
            .life
            .heartbeat(
                &self.node,
                self.ports.liveness.as_ref(),
                self.clock.now_ms(),
            )
            .await?;
        let Some(fence) = self.ownership(&mut state, session_shard(session)).await? else {
            return Ok(0);
        };
        state.active.insert(session.clone());
        let result = self.session(session, fence).await;
        if self
            .ports
            .sessions
            .load(session)
            .await?
            .is_some_and(|cp| cp.state.current_task.is_none() && cp.state.pending_tasks.is_empty())
        {
            if state.active.remove(session) {
                state.completed += 1;
            }
        }
        result
    }
    async fn publish(&self, cp: &SessionCheckpoint) -> RuntimeResult<()> {
        // Session-wide event order is checkpoint order, never event-ID lexical order.
        for event in &cp.pending_events {
            self.ports
                .events
                .publish_hard_event(
                    &cp.fence,
                    CommittedEventRef {
                        session_id: cp.spec.session_id.clone(),
                        event_id: event.event_id.clone(),
                    },
                )
                .await?;
        }
        for effect in &cp.pending_effects {
            self.ports
                .commands
                .publish(
                    &cp.fence,
                    CommittedEffectRef {
                        session_id: cp.spec.session_id.clone(),
                        command_id: effect_command_id(&cp.spec.session_id, effect)?,
                    },
                )
                .await?;
        }
        Ok(())
    }
    async fn session(&self, session: &SessionId, fence: OwnershipFence) -> RuntimeResult<u32> {
        let guard = self
            .ports
            .sessions
            .begin_recovery(session, &fence, RuntimeDefaults::default().poison_takeovers)
            .await?;
        if guard.quarantined {
            return Err(RuntimeError::Unavailable(
                "session quarantined after unsuccessful takeovers".into(),
            ));
        }
        let spec = self
            .ports
            .sessions
            .load_spec(session)
            .await?
            .ok_or(RuntimeError::NotFound)?;
        let reducer = runner(&self.runners, spec.kind)?;
        let old = self.ports.sessions.load(session).await?;
        let mut cp = match old {
            Some(cp) if cp.fence == fence => cp,
            _ => {
                self.ports
                    .sessions
                    .activate(spec.clone(), reducer.initialize(&spec)?, fence)
                    .await?
            }
        };
        self.publish(&cp).await?;
        cp = self
            .ports
            .sessions
            .load(session)
            .await?
            .ok_or(RuntimeError::NotFound)?;
        timers::restore(&self.ports, reducer, &cp, self.clock.now_ms()).await?;
        let inputs = self
            .ports
            .inbox
            .read_batch(
                session,
                cp.consumed_input,
                RuntimeDefaults::default().inbox_batch,
            )
            .await?;
        let mut progressed = 0;
        for entry in inputs {
            let transition = match reducer.reduce(&cp.state, &entry.input) {
                Ok(t) if transition_fits(&cp, &t)? => t,
                Ok(_) => fallback(reducer, &cp, &entry.input, RuntimeError::CapacityExceeded)?,
                Err(e) if matches!(&entry.input.kind, SessionInputKind::Agent { event: AgentEvent::ObservationFailed { .. }, .. }) => {
                    self.observation_failure(reducer, &cp, &entry.input, e).await?
                }
                Err(e @ (RuntimeError::ContextBlocked | RuntimeError::CapacityExceeded)) => {
                    fallback(reducer, &cp, &entry.input, e)?
                }
                Err(e) => return Err(e),
            };
            let commit = SessionCommit {
                session_id: session.clone(),
                fence: cp.fence.clone(),
                expected_revision: cp.revision,
                consumed_input: entry.position,
                transition,
            };
            cp = match self.ports.sessions.commit(commit).await {
                Ok(cp) => cp,
                Err(RuntimeError::CapacityExceeded) => {
                    let transition =
                        fallback(reducer, &cp, &entry.input, RuntimeError::CapacityExceeded)?;
                    self.ports
                        .sessions
                        .commit(SessionCommit {
                            session_id: session.clone(),
                            fence: cp.fence.clone(),
                            expected_revision: cp.revision,
                            consumed_input: entry.position,
                            transition,
                        })
                        .await?
                }
                Err(e) => return Err(e),
            };
            progressed += 1;
            self.publish(&cp).await?;
            // Both publications update checkpoint revision: never reuse the pre-publication CAS.
            cp = self
                .ports
                .sessions
                .load(session)
                .await?
                .ok_or(RuntimeError::NotFound)?;
        }
        self.ports
            .inbox
            .advance(session, &cp.fence, cp.consumed_input)
            .await?;
        if cp
            .state
            .current_task
            .as_ref()
            .is_some_and(|t| t.status.is_terminal())
            && cp.state.pending_tasks.is_empty()
            && cp.pending_events.is_empty()
            && cp.pending_effects.is_empty()
            && cp.deadlines.is_empty()
        {
            let mut next = cp.state.clone();
            next.current_task = None;
            cp = self
                .ports
                .sessions
                .commit(SessionCommit {
                    session_id: session.clone(),
                    fence: cp.fence.clone(),
                    expected_revision: cp.revision,
                    consumed_input: cp.consumed_input,
                    transition: Transition {
                        next_state: next,
                        effects: vec![],
                        events: vec![],
                        timer_changes: vec![],
                    },
                })
                .await?;
        }
        if cp.state.current_task.is_none()
            && cp.state.pending_tasks.is_empty()
            && cp.pending_events.is_empty()
            && cp.pending_effects.is_empty()
            && cp.deadlines.is_empty()
        {
            match self
                .ports
                .sessions
                .deactivate(session, &cp.fence, cp.revision)
                .await
            {
                Ok(()) => {}
                Err(RuntimeError::Conflict(_)) => {}
                Err(e) => return Err(e),
            }
        }
        Ok(progressed)
    }
    async fn observation_failure(
        &self, reducer: &dyn SessionRunner, cp: &SessionCheckpoint,
        input: &SessionInput, error: RuntimeError,
    ) -> RuntimeResult<Transition> {
        let SessionInputKind::Agent { task_id, node_id, dispatch_id, .. } = &input.kind else {
            return Err(RuntimeError::InvalidInput("observation input required".into()));
        };
        let record = self.ports.dispatches.read(dispatch_id).await?.ok_or(RuntimeError::NotFound)?;
        if record.context.session_id != cp.spec.session_id || &record.context.task_id != task_id || &record.context.node_id != node_id {
            return Err(RuntimeError::AccessDenied);
        }
        // Snapshot may have saved its handle immediately before observation failed,
        // without an Accepted input. Restore that canonical handle through the pure
        // reducer, then commit its state and TaskFailure together at the original input.
        if let Some(handle) = record.handle {
            let accepted = reducer.reduce(&cp.state, &SessionInput {
                event_id: input.event_id.clone(), at_ms: input.at_ms,
                kind: SessionInputKind::Agent { task_id: task_id.clone(), node_id: node_id.clone(), dispatch_id: dispatch_id.clone(), event: AgentEvent::Accepted { handle } },
            })?;
            let mut recovered = cp.clone(); recovered.state = accepted.next_state;
            let mut failed = fallback(reducer, &recovered, input, error)?;
            for effect in accepted.effects { if !failed.effects.contains(&effect) { failed.effects.push(effect); } }
            failed.events.extend(accepted.events);
            failed.timer_changes.extend(accepted.timer_changes);
            Ok(failed)
        } else { fallback(reducer, cp, input, error) }
    }
    /// At most 64 sessions and 64 command executions, plus 64 shard inspections per tick.
    /// Transient failures are reported and back off; accepted data stays in durable indexes.
    pub async fn tick(&self) -> RuntimeResult<TickReport> {
        let mut s = self.state.lock().await;
        let now = self.clock.now_ms();
        let mut report = TickReport::default();
        if s.life.phase == RuntimePhase::Stopped {
            return Ok(report);
        }
        s.life
            .heartbeat(&self.node, self.ports.liveness.as_ref(), now)
            .await?;
        if s.life.expired(now) {
            s.pending.clear();
            s.life.phase = RuntimePhase::Stopped;
            return Ok(report);
        }
        if now < s.life.retry_at {
            return Ok(report);
        }
        let d = RuntimeDefaults::default();
        // Retained sessions are a hint, never a lifetime reservation of the turn budget.
        let mut sessions = if s.life.phase == RuntimePhase::Running { BTreeSet::new() } else { s.active.clone() };
        if s.life.phase == RuntimePhase::Running {
            // Reserve at least half the turn for existing durable wake work even
            // when new admissions continuously fill the repair page.
            let page = self
                .ports
                .admissions
                .repair_pending(&self.node.scope, s.repair.clone(), d.recovery_concurrency / 2)
                .await?;
            s.repair = page.next_cursor;
            for receipt in page.items {
                if sessions.len() < d.recovery_concurrency as usize {
                    sessions.insert(receipt.task.session_id);
                }
            }
            for _ in 0..d.recovery_concurrency {
                let shard = ShardId {
                    scope: self.node.scope.clone(),
                    number: s.shard,
                };
                s.shard = (s.shard + 1) % d.shards;
                self.ownership(&mut s, shard).await?;
            }
        }
        let owned: Vec<_> = s.owned.keys().copied().collect();
        let mut inspected = 0;
        for offset in 0..owned.len().min(d.recovery_concurrency as usize) {
            if sessions.len() >= d.recovery_concurrency as usize { break; }
            inspected += 1;
            let n = owned[(s.owned_offset as usize + offset) % owned.len()];
            let shard = ShardId {
                scope: self.node.scope.clone(),
                number: n,
            };
            let limit = (d.recovery_concurrency as usize - sessions.len()).max(1) as u32;
            let page = self
                .ports
                .sessions
                .scan_active(&shard, s.cursors.get(&n).cloned().flatten(), limit)
                .await?;
            s.cursors.insert(n, page.next_cursor);
            for session in page.items {
                if sessions.len() < d.recovery_concurrency as usize {
                    sessions.insert(session);
                }
            }
        }
        s.owned_offset = s.owned_offset.wrapping_add(inspected);
        for session in &s.active {
            if sessions.len() < d.recovery_concurrency as usize { sessions.insert(session.clone()); }
        }
        s.active = sessions.clone();
        for session in sessions {
            let result = async {
                let Some(fence) = self.ownership(&mut s, session_shard(&session)).await? else {
                    s.active.remove(&session);
                    return Ok(0);
                };
                self.session(&session, fence).await
            }
            .await;
            match result {
                Ok(n) => {
                    report.progressed += n;
                    if self.ports.sessions.load(&session).await?.is_some_and(|cp| {
                        cp.state.current_task.is_none() && cp.state.pending_tasks.is_empty()
                    }) {
                        if s.active.remove(&session) {
                            s.completed += 1;
                        }
                    }
                }
                Err(e) => {
                    s.active.remove(&session);
                    report.errors.push(e)
                }
            }
        }
        let remaining = d
            .recovery_concurrency
            .saturating_sub(s.pending.len() as u32);
        if remaining > 0 {
            let page = self
                .ports
                .commands
                .receive(
                    &self.node.scope,
                    &self.node,
                    CommandConsumerKind::Worker,
                    s.commands.clone(),
                    remaining,
                )
                .await?;
            s.commands = page.next_cursor;
            for command in page.items {
                if s.pending.contains_key(&command.command_id) {
                    continue;
                }
                let ports = self.ports.clone();
                let judge = self.judge.clone();
                let node = self.node.clone();
                s.pending.insert(
                    command.command_id.clone(),
                    PendingWork {
                        started_at: now,
                        future: Box::pin(async move {
                            results::execute(&ports, judge.as_ref(), command.clone(), now).await?;
                            ports.commands.ack(&command.command_id, &node).await
                        }),
                    },
                );
            }
        }
        let ids: Vec<_> = s.pending.keys().cloned().collect();
        for id in ids {
            let Some(mut work) = s.pending.remove(&id) else {
                continue;
            };
            if now.saturating_sub(work.started_at) >= d.stall_ms {
                report.errors.push(RuntimeError::Unavailable(
                    "effect I/O stalled; durable command retained".into(),
                ));
                continue;
            }
            match work.future.as_mut().now_or_never() {
                Some(Ok(())) => report.progressed += 1,
                Some(Err(e)) => report.errors.push(e),
                None => {
                    s.pending.insert(id, work);
                }
            }
        }
        if report.errors.is_empty() {
            s.life.progressed();
        } else {
            s.life.backoff(now);
        }
        Ok(report)
    }
}
fn transition_fits(cp: &SessionCheckpoint, t: &Transition) -> RuntimeResult<bool> {
    // Reserve 128 KiB for repository envelope, durable pending items, indexes and diagnostics.
    // Conservative command/intent/projection/event mutation estimate, bounded before any write.
    let weight = t
        .effects
        .iter()
        .map(|e| match e {
            EffectIntent::SendAgent { .. } | EffectIntent::SaveResult { .. } => 2,
            _ => 1,
        })
        .sum::<usize>()
        + t.events.len();
    // Keep the prompt projection within half the existing 128 KiB snapshot reserve.
    // Artifacts may independently use 896 KiB; questions include scoped IDs/JSON overhead.
    let interactions: Vec<_> = t.next_state.current_task.iter().flat_map(|task|task.nodes.values())
        .filter_map(|node|node.interaction_id.as_ref().zip(node.interaction_prompt.as_ref()))
        .map(|(interaction_id,prompt)|ActiveInteraction{interaction_id:interaction_id.clone(),prompt:prompt.clone()}).collect();
    if size(&interactions)? > 65536 { return Ok(false); }
    let mut saved_reserve = 0;
    if let Some(task) = &t.next_state.current_task {
        for node in task.nodes.values().filter(|n| n.saved_result.is_none()) {
            if let Some(intent) = &node.result_intent {
                saved_reserve += size(intent)? + 128;
            }
        }
    }
    Ok(size(&cp.spec)?
        + size(&t.next_state)?
        + size(&t.effects)?
        + size(&t.events)?
        + saved_reserve
        < MAX_VALUE_BYTES - 131072
        && weight <= 88
        && t.events.len() <= 32
        && t.effects.len() <= 64)
}
fn fallback(
    reducer: &dyn SessionRunner,
    cp: &SessionCheckpoint,
    input: &SessionInput,
    error: RuntimeError,
) -> RuntimeResult<Transition> {
    let task_id = match &input.kind {
        SessionInputKind::Submit { task } => task.task_id.clone(),
        SessionInputKind::Agent { task_id, .. }
        | SessionInputKind::HumanReply { task_id, .. }
        | SessionInputKind::JudgeDecided { task_id, .. } => task_id.clone(),
        SessionInputKind::Cancel { command } => command.task_id.clone(),
        _ => cp
            .state
            .current_task
            .as_ref()
            .map(|t| t.task_id.clone())
            .ok_or_else(|| error.clone())?,
    };
    let output = match &input.kind {
        SessionInputKind::Agent {
            event: AgentEvent::Snapshot { snapshot },
            ..
        } => snapshot.output.clone(),
        SessionInputKind::Agent {
            event: AgentEvent::Completed { output } | AgentEvent::FailedWithOutput { output, .. } | AgentEvent::ObservationFailed { output, .. },
            ..
        } => output.clone(),
        _ => vec![],
    };
    reducer.reduce(
        &cp.state,
        &SessionInput {
            event_id: input.event_id.clone(),
            at_ms: input.at_ms,
            kind: SessionInputKind::TaskFailure {
                task_id,
                error,
                output: projection::bounded_output(output, 8192),
            },
        },
    )
}
#[async_trait]
impl WorkerRuntime for DefaultWorker {
    async fn run(&self, mut shutdown: CancellationReceiver) -> RuntimeResult<()> {
        loop {
            if *shutdown.borrow() || shutdown.has_changed().is_err() {
                break;
            }
            tokio::select! {result=self.tick()=>{result?;},_=shutdown.changed()=>{break;}}
            if self.status().await?.phase == RuntimePhase::Stopped {
                return Ok(());
            }
            tokio::select! {_=tokio::time::sleep(Duration::from_millis(500))=>{},_=shutdown.changed()=>{break;}}
        }
        let deadline = self
            .clock
            .now_ms()
            .saturating_add(RuntimeDefaults::default().drain_ms);
        self.drain(deadline).await?;
        loop {
            let report = self.drain(deadline).await?;
            if report.remaining == 0 || report.deadline_reached {
                break;
            }
            self.tick().await?;
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
        Ok(())
    }
    /// Nonblocking drain poll. Existing sessions keep progressing; no new shard claims.
    async fn drain(&self, deadline_ms: u64) -> RuntimeResult<DrainReport> {
        let mut s = self.state.lock().await;
        if !s.life.registered {
            s.life
                .heartbeat(
                    &self.node,
                    self.ports.liveness.as_ref(),
                    self.clock.now_ms(),
                )
                .await?;
        }
        self.ports.liveness.mark_draining(&self.node).await?;
        s.life.drain_deadline = Some(
            s.life
                .drain_deadline
                .map_or(deadline_ms, |d| d.min(deadline_ms)),
        );
        s.life.phase = RuntimePhase::Draining;
        let remaining = (s.active.len() + s.pending.len()) as u32;
        let deadline_reached = s.life.expired(self.clock.now_ms());
        if remaining == 0 || deadline_reached {
            s.pending.clear();
            s.life.phase = RuntimePhase::Stopped;
        }
        Ok(DrainReport {
            completed: s.completed,
            remaining,
            deadline_reached,
        })
    }
    async fn status(&self) -> RuntimeResult<WorkerStatus> {
        let s = self.state.lock().await;
        Ok(WorkerStatus {
            node: self.node.clone(),
            phase: s.life.phase,
            active_sessions: s.active.len() as u32,
            owned_shards: s.owned.len() as u32,
        })
    }
}
