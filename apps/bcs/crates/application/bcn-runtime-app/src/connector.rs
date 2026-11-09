//! Independently scheduled Connector actors. In-flight sockets are disposable; dispatch records are authority.
use crate::{recovery::Lifecycle, *};
use async_trait::async_trait;
use bcn_runtime_api::application::*;
use futures::{FutureExt, StreamExt};
use std::{collections::BTreeMap, future::Future, pin::Pin, sync::Arc, time::Duration};
use tokio::sync::Mutex;
enum Io {
    Stream(AgentEventStream),
    Response(AgentSendResponse),
    Snapshot(ExecutionSnapshot),
    Cancel(CancelOutcome),
}
type IoFuture = Pin<Box<dyn Future<Output = ConnectorResult<Io>> + Send>>;
struct Actor {
    record: DispatchRecord,
    client: Arc<dyn AgentConnector>,
    io: Option<IoFuture>,
    stream: Option<AgentEventStream>,
    output: Vec<MessagePart>,
    pending_text: String,
    reset: bool,
    sequence: u64,
    last_progress: u64,
    last_emit: u64,
    next_query: u64,
    pending: Option<(DispatchReceipt, SessionInput)>,
    observed: Option<AgentEvent>,
}
struct State {
    life: Lifecycle,
    actors: BTreeMap<DispatchId, Actor>,
    commands: Option<String>,
    dispatches: Option<String>,
    supervised_at: u64,
    completed: u32,
}
pub struct DefaultConnector {
    node: NodeIdentity,
    ports: RuntimePorts,
    bindings: Vec<(FrozenTargetBinding, Arc<dyn AgentConnector>)>,
    clock: Arc<dyn Clock>,
    state: Mutex<State>,
}
impl DefaultConnector {
    pub fn new(
        node: NodeIdentity,
        ports: RuntimePorts,
        bindings: Vec<(FrozenTargetBinding, Arc<dyn AgentConnector>)>,
        clock: Arc<dyn Clock>,
    ) -> RuntimeResult<Self> {
        validate_node(&node)?;
        for (i, (binding, client)) in bindings.iter().enumerate() {
            if binding.target.scope != node.scope
                || binding.capabilities != client.capabilities()
                || bindings[..i].iter().any(|(b, _)| b == binding)
            {
                return Err(RuntimeError::InvalidInput(
                    "connector registry binding/capabilities".into(),
                ));
            }
        }
        Ok(Self {
            node,
            ports,
            bindings,
            clock,
            state: Mutex::new(State {
                life: Lifecycle::default(),
                actors: BTreeMap::new(),
                commands: None,
                dispatches: None,
                supervised_at: 0,
                completed: 0,
            }),
        })
    }
    fn client(&self, operation: &DispatchOperation) -> RuntimeResult<Arc<dyn AgentConnector>> {
        let binding = match operation {
            DispatchOperation::Send { request } => &request.binding,
            DispatchOperation::Cancel { binding, .. } => binding,
        };
        self.bindings
            .iter()
            .find(|(b, _)| b == binding)
            .map(|(_, c)| c.clone())
            .ok_or_else(|| {
                RuntimeError::Unsupported("frozen connector binding is not registered".into())
            })
    }
    async fn add(&self, s: &mut State, record: DispatchRecord) -> RuntimeResult<()> {
        if matches!(
            record.phase,
            DispatchPhase::ResultRecorded | DispatchPhase::Unknown
        ) || s.actors.contains_key(&record.fence.dispatch_id)
        {
            return Ok(());
        }
        if s.actors.len() >= RuntimeDefaults::default().recovery_concurrency as usize {
            return Ok(());
        }
        let client = self.client(&record.operation)?;
        let output = if matches!(record.operation, DispatchOperation::Send { .. }) {
            match self
                .ports
                .events
                .read_projection(&record.fence.dispatch_id)
                .await
            {
                Ok(output) => output.unwrap_or_default(),
                Err(RuntimeError::Conflict(_)) => vec![],
                Err(e) => return Err(e),
            }
        } else {
            vec![]
        };
        let now = self.clock.now_ms();
        s.actors.insert(
            record.fence.dispatch_id.clone(),
            Actor {
                record,
                client,
                io: None,
                stream: None,
                output,
                pending_text: String::new(),
                reset: false,
                sequence: 0,
                last_progress: now,
                last_emit: now,
                next_query: now,
                pending: None,
                observed: None,
            },
        );
        Ok(())
    }
    async fn discover(&self, s: &mut State, now: u64) -> RuntimeResult<()> {
        let d = RuntimeDefaults::default();
        if s.life.phase != RuntimePhase::Running {
            return Ok(());
        }
        let remaining = d.recovery_concurrency.saturating_sub(s.actors.len() as u32);
        if remaining > 0 {
            let page = self
                .ports
                .commands
                .receive(
                    &self.node.scope,
                    &self.node,
                    CommandConsumerKind::Connector,
                    s.commands.clone(),
                    remaining,
                )
                .await?;
            s.commands = page.next_cursor;
            for command in page.items {
                let record = match self
                    .ports
                    .dispatches
                    .claim(&command.command_id, self.node.clone())
                    .await
                {
                    Ok(r) => r,
                    Err(RuntimeError::OwnershipLost) => continue,
                    Err(e) => return Err(e),
                };
                self.ports
                    .commands
                    .ack(&command.command_id, &self.node)
                    .await?;
                self.add(s, record).await?;
            }
        }
        if now.saturating_sub(s.supervised_at) < d.dispatch_supervision_ms {
            return Ok(());
        }
        s.supervised_at = now;
        let page = self
            .ports
            .dispatches
            .scan_active(
                &self.node.scope,
                s.dispatches.clone(),
                d.recovery_concurrency,
            )
            .await?;
        s.dispatches = page.next_cursor;
        for mut record in page.items {
            if s.actors.len() >= d.recovery_concurrency as usize {
                break;
            }
            if record.fence.owner != self.node {
                if !s.life.may_takeover(now) {
                    continue;
                }
                let observations = self
                    .ports
                    .liveness
                    .inspect_batch(&[record.fence.owner.clone()])
                    .await?;
                if !observations
                    .first()
                    .is_some_and(|o| o.node == record.fence.owner && o.liveness == Liveness::Dead)
                {
                    continue;
                }
                record = self
                    .ports
                    .dispatches
                    .takeover(&record.fence, self.node.clone())
                    .await?;
            }
            self.add(s, record).await?;
        }
        Ok(())
    }
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
            s.actors.clear();
            s.life.phase = RuntimePhase::Stopped;
            return Ok(report);
        }
        if now < s.life.retry_at {
            return Ok(report);
        }
        if let Err(e) = self.discover(&mut s, now).await {
            report.errors.push(e);
        }
        let ids: Vec<_> = s.actors.keys().cloned().collect();
        for id in ids {
            let mut actor = s.actors.remove(&id).ok_or(RuntimeError::NotFound)?;
            match self.poll_actor(&mut actor, now).await {
                Ok(done) => {
                    report.progressed += 1;
                    if done {
                        s.completed += 1;
                    } else {
                        s.actors.insert(id, actor);
                    }
                }
                Err(RuntimeError::CapacityExceeded) => {
                    actor.observed = None;
                    self.terminal_failure(&mut actor, RuntimeError::CapacityExceeded, now)?;
                    match self.persist(&mut actor).await {
                        Ok(true) => s.completed += 1,
                        Ok(false) => {
                            s.actors.insert(id, actor);
                        }
                        Err(e) => {
                            report.errors.push(e);
                            s.actors.insert(id, actor);
                        }
                    }
                }
                Err(RuntimeError::OwnershipLost) => {
                    report.errors.push(RuntimeError::OwnershipLost);
                }
                Err(e) => {
                    report.errors.push(e);
                    s.actors.insert(id, actor);
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
    async fn poll_actor(&self, a: &mut Actor, now: u64) -> RuntimeResult<bool> {
        let current = self
            .ports
            .dispatches
            .read(&a.record.fence.dispatch_id)
            .await?
            .ok_or(RuntimeError::NotFound)?;
        if current.fence != a.record.fence {
            return Err(RuntimeError::OwnershipLost);
        }
        a.record = current;
        if matches!(
            a.record.phase,
            DispatchPhase::ResultRecorded | DispatchPhase::Unknown
        ) {
            return Ok(true);
        }
        if a.pending.is_some() {
            return self.persist(a).await;
        }
        if matches!(a.record.operation, DispatchOperation::Send { .. }) {
            let terminal = self
                .ports
                .events
                .read_snapshot(&a.record.context.task_id)
                .await?
                .is_some_and(|s| s.task.status.is_terminal());
            let superseded = matches!(
                self.ports
                    .events
                    .read_projection(&a.record.fence.dispatch_id)
                    .await,
                Err(RuntimeError::Conflict(_))
            );
            if terminal || superseded {
                if a.record.phase == DispatchPhase::Prepared {
                    a.record = self
                        .ports
                        .dispatches
                        .mark_may_have_sent(&a.record.fence, a.record.revision)
                        .await?;
                }
                self.terminal_failure(a, RuntimeError::ExecutionUnknown, now)?;
                return self.persist(a).await;
            }
        }
        if let Some(event) = a.observed.clone() {
            self.event(a, event, now).await?;
            a.observed = None;
            if a.pending.is_some() {
                return self.persist(a).await;
            }
            return Ok(false);
        }
        if !a.pending_text.is_empty()
            && now.saturating_sub(a.last_emit) >= RuntimeDefaults::default().token_batch_ms
        {
            self.flush(a, now).await?;
        }
        if a.io.is_none() && a.stream.is_none() && now >= a.next_query {
            self.start(a, now).await?;
            if a.pending.is_some() {
                return self.persist(a).await;
            }
        }
        if let Some(io) = a.io.as_mut() {
            if let Some(result) = io.as_mut().now_or_never() {
                a.io = None;
                a.last_progress = now;
                match result {
                    Ok(Io::Stream(stream)) => a.stream = Some(stream),
                    Ok(Io::Response(AgentSendResponse::Message { parts, handle })) => {
                        let event = match handle {
                            Some(handle) => AgentEvent::Snapshot {
                                snapshot: ExecutionSnapshot {
                                    handle,
                                    status: TaskStatus::Completed,
                                    output: parts,
                                    error: None,
                                },
                            },
                            None => AgentEvent::Completed { output: parts },
                        };
                        self.observe(a, event, now).await?;
                    }
                    Ok(Io::Response(AgentSendResponse::Task { snapshot }))
                    | Ok(Io::Snapshot(snapshot)) => {
                        self.observe(a, AgentEvent::Snapshot { snapshot }, now)
                            .await?
                    }
                    Ok(Io::Cancel(outcome)) => self.cancellation(a, outcome, now)?,
                    Err(e) => self.failure(a, e, now)?,
                }
            }
        } else if let Some(stream) = a.stream.as_mut() {
            if let Some(next) = stream.next().now_or_never() {
                a.last_progress = now;
                match next {
                    Some(Ok(event)) => self.observe(a, event, now).await?,
                    Some(Err(e)) => {
                        a.stream = None;
                        self.failure(a, e, now)?;
                    }
                    None => {
                        a.stream = None;
                        self.failure(
                            a,
                            ConnectorError::MayHaveBeenAccepted(
                                "stream ended without terminal evidence".into(),
                            ),
                            now,
                        )?;
                    }
                }
            }
        }
        if a.pending.is_some() {
            return self.persist(a).await;
        }
        if now.saturating_sub(a.last_progress) >= RuntimeDefaults::default().stall_ms {
            a.io = None;
            a.stream = None;
            self.failure(
                a,
                ConnectorError::MayHaveBeenAccepted("progress stalled".into()),
                now,
            )?;
            a.last_progress = now;
        }
        Ok(false)
    }
    async fn start(&self, a: &mut Actor, now: u64) -> RuntimeResult<()> {
        let prepared = a.record.phase == DispatchPhase::Prepared;
        if prepared {
            a.record = self
                .ports
                .dispatches
                .mark_may_have_sent(&a.record.fence, a.record.revision)
                .await?;
        }
        if matches!(a.record.operation, DispatchOperation::Send { .. }) && !a.reset {
            projection::reset(&self.ports, &a.record, a.output.clone(), a.sequence).await?;
            a.reset = true;
            a.sequence += 1;
        }
        let client = a.client.clone();
        match a.record.operation.clone() {
            DispatchOperation::Cancel { request, .. } if prepared => {
                a.io = Some(Box::pin(async move {
                    client.cancel_task(request).await.map(Io::Cancel)
                }));
            }
            DispatchOperation::Cancel { .. } => {
                self.cancellation(a, CancelOutcome::Unknown, now)?
            }
            DispatchOperation::Send { mut request } if prepared => {
                if request.expires_at_ms.is_some_and(|d| now >= d) {
                    return self.terminal_failure(
                        a,
                        RuntimeError::Unavailable("dispatch expired before send".into()),
                        now,
                    );
                }
                if a.record.context.node_id.is_some() {
                    request.message.context_id = Some(SessionId::new(
                        request.dispatch_id.scope.clone(),
                        hash("group-attempt-context", &request.dispatch_id)?,
                    ));
                }
                if request.binding.capabilities.streaming {
                    a.io = Some(Box::pin(async move {
                        client.send_streaming_message(request).await.map(Io::Stream)
                    }));
                } else {
                    a.io = Some(Box::pin(async move {
                        client.send_message(request).await.map(Io::Response)
                    }));
                }
            }
            DispatchOperation::Send { request } => {
                let Some(handle) = a.record.handle.clone() else {
                    return self.terminal_failure(a, RuntimeError::ExecutionUnknown, now);
                };
                if request.binding.capabilities.query {
                    a.io = Some(Box::pin(async move {
                        client.get_task(handle).await.map(Io::Snapshot)
                    }));
                } else if request.binding.capabilities.subscribe {
                    a.io = Some(Box::pin(async move {
                        client.subscribe_to_task(handle).await.map(Io::Stream)
                    }));
                } else {
                    self.terminal_failure(a, RuntimeError::ExecutionUnknown, now)?;
                }
                a.next_query = now.saturating_add(RuntimeDefaults::default().progress_ms);
            }
        }
        Ok(())
    }
    async fn flush(&self, a: &mut Actor, now: u64) -> RuntimeResult<()> {
        if !a.pending_text.is_empty() {
            projection::delta(&self.ports, &a.record, a.pending_text.clone(), a.sequence).await?;
            a.pending_text.clear();
            a.sequence += 1;
            a.last_emit = now;
        }
        Ok(())
    }
    async fn observe(&self, a: &mut Actor, event: AgentEvent, now: u64) -> RuntimeResult<()> {
        // A stream item is retained until every required persistence step succeeds.
        a.observed = Some(event.clone());
        self.event(a, event, now).await?;
        a.observed = None;
        Ok(())
    }
    async fn event(&self, a: &mut Actor, event: AgentEvent, now: u64) -> RuntimeResult<()> {
        let handle = match &event {
            AgentEvent::Accepted { handle }
            | AgentEvent::InputRequired { handle, .. }
            | AgentEvent::AuthRequired { handle } => Some(handle.clone()),
            AgentEvent::Snapshot { snapshot } => Some(snapshot.handle.clone()),
            _ => None,
        };
        if let Some(handle) = handle {
            a.record = self
                .ports
                .dispatches
                .save_handle(&a.record.fence, a.record.revision, handle)
                .await?;
        }
        let event = match event {
            AgentEvent::Delta { text } => {
                if let Some(MessagePart::Text { text: tail }) = a.output.last_mut() {
                    tail.push_str(&text);
                } else {
                    a.output.push(MessagePart::Text { text: text.clone() });
                }
                if size(&a.output)? > MAX_CONTENT_BYTES {
                    a.output = projection::bounded_output(a.output.clone(), MAX_CONTENT_BYTES);
                    return self.terminal_failure(a, RuntimeError::CapacityExceeded, now);
                }
                a.pending_text.push_str(&text);
                return Ok(());
            }
            AgentEvent::Completed { output } => {
                if size(&output)? > MAX_CONTENT_BYTES {
                    a.output = projection::bounded_output(output, MAX_CONTENT_BYTES);
                    return self.terminal_failure(a, RuntimeError::CapacityExceeded, now);
                }
                a.output = output.clone();
                AgentEvent::Completed { output }
            }
            AgentEvent::Snapshot { mut snapshot } => {
                snapshot.error = snapshot.error.map(bounded_error);
                if size(&snapshot.output)? > MAX_CONTENT_BYTES {
                    a.output = projection::bounded_output(snapshot.output, MAX_CONTENT_BYTES);
                    return self.terminal_failure(a, RuntimeError::CapacityExceeded, now);
                }
                a.output = snapshot.output.clone();
                a.pending_text.clear();
                projection::reset(&self.ports, &a.record, a.output.clone(), a.sequence).await?;
                a.sequence += 1;
                if snapshot.status.is_terminal() {
                    a.stream = None;
                } else {
                    a.next_query = now.saturating_add(RuntimeDefaults::default().progress_ms);
                }
                if snapshot.status == TaskStatus::Failed && snapshot.error.is_none() {
                    snapshot.error = Some(RuntimeError::Unavailable("remote failed".into()));
                }
                AgentEvent::Snapshot { snapshot }
            }
            AgentEvent::Failed { error } => return self.terminal_failure(a, error, now),
            AgentEvent::FailedWithOutput { error, output } => {
                a.output = projection::bounded_output(output, MAX_CONTENT_BYTES);
                return self.terminal_failure(a, error, now);
            }
            other => other,
        };
        self.flush(a, now).await?;
        self.agent_receipt(a, event, now)
    }
    fn failure(&self, a: &mut Actor, error: ConnectorError, now: u64) -> RuntimeResult<()> {
        if matches!(a.record.operation, DispatchOperation::Cancel { .. })
            && error != ConnectorError::OwnershipLost
        {
            return self.cancellation(a, CancelOutcome::Unknown, now);
        }
        match error {
            ConnectorError::OwnershipLost => Err(RuntimeError::OwnershipLost),
            ConnectorError::MayHaveBeenAccepted(_) if a.record.handle.is_some() => {
                a.next_query = now.saturating_add(RuntimeDefaults::default().progress_ms);
                Ok(())
            }
            ConnectorError::MayHaveBeenAccepted(_) => {
                self.terminal_failure(a, RuntimeError::ExecutionUnknown, now)
            }
            ConnectorError::NotSent(_) => self.terminal_failure(
                a,
                RuntimeError::Unavailable("transport proved request not sent".into()),
                now,
            ),
            ConnectorError::RemoteFailed { .. } => self.terminal_failure(
                a,
                RuntimeError::Unavailable("remote execution failed".into()),
                now,
            ),
            ConnectorError::Unsupported(_) => self.terminal_failure(
                a,
                RuntimeError::Unsupported("connector operation".into()),
                now,
            ),
        }
    }
    fn terminal_failure(&self, a: &mut Actor, error: RuntimeError, now: u64) -> RuntimeResult<()> {
        let error = bounded_error(error);
        a.io = None;
        a.stream = None;
        a.pending_text.clear();
        self.agent_receipt(
            a,
            AgentEvent::FailedWithOutput {
                error,
                output: projection::bounded_output(a.output.clone(), MAX_CONTENT_BYTES),
            },
            now,
        )
    }
    fn agent_receipt(&self, a: &mut Actor, event: AgentEvent, now: u64) -> RuntimeResult<()> {
        let id = EventId::new(
            a.record.context.task_id.scope.clone(),
            hash("agent-receipt", &(&a.record.fence, &event))?,
        );
        let input = SessionInput {
            event_id: id,
            at_ms: now,
            kind: SessionInputKind::Agent {
                task_id: a.record.context.task_id.clone(),
                node_id: a.record.context.node_id.clone(),
                dispatch_id: a.record.fence.dispatch_id.clone(),
                event: event.clone(),
            },
        };
        a.pending = Some((DispatchReceipt::Agent { event }, input));
        Ok(())
    }
    fn cancellation(&self, a: &mut Actor, outcome: CancelOutcome, now: u64) -> RuntimeResult<()> {
        let outcome = if size(&outcome)? > MAX_CONTENT_BYTES {
            CancelOutcome::Unknown
        } else {
            outcome
        };
        let id = EventId::new(
            a.record.context.task_id.scope.clone(),
            hash("cancel-receipt", &(&a.record.fence, &outcome))?,
        );
        let input = SessionInput {
            event_id: id,
            at_ms: now,
            kind: SessionInputKind::Cancellation {
                task_id: a.record.context.task_id.clone(),
                node_id: a.record.context.node_id.clone(),
                dispatch_id: a.record.fence.dispatch_id.clone(),
                operation_id: a.record.operation.operation_id().clone(),
                outcome: outcome.clone(),
            },
        };
        a.pending = Some((DispatchReceipt::Cancellation { outcome }, input));
        Ok(())
    }
    async fn persist(&self, a: &mut Actor) -> RuntimeResult<bool> {
        let (receipt, mut input) = a.pending.clone().ok_or(RuntimeError::NotFound)?;
        if let Some(prior) = self
            .ports
            .inbox
            .find_input(&a.record.context.session_id, &input.event_id)
            .await?
        {
            if prior.kind != input.kind {
                return Err(RuntimeError::Conflict("receipt identity reused".into()));
            }
            input = prior;
        }
        a.record = self
            .ports
            .dispatches
            .save_receipt(
                &a.record.fence,
                a.record.revision,
                receipt,
                &a.record.context.session_id,
                input,
            )
            .await?;
        a.pending = None;
        Ok(matches!(
            a.record.phase,
            DispatchPhase::ResultRecorded | DispatchPhase::Unknown
        ))
    }
}
#[async_trait]
impl ConnectorRuntime for DefaultConnector {
    async fn run(&self, mut shutdown: CancellationReceiver) -> RuntimeResult<()> {
        loop {
            if *shutdown.borrow() || shutdown.has_changed().is_err() {
                break;
            }
            tokio::select! {result=self.tick()=>{result?;},_=shutdown.changed()=>{break;}}
            if self.status().await?.phase == RuntimePhase::Stopped {
                return Ok(());
            }
            tokio::select! {_=tokio::time::sleep(Duration::from_millis(200))=>{},_=shutdown.changed()=>{break;}}
        }
        let deadline = self
            .clock
            .now_ms()
            .saturating_add(RuntimeDefaults::default().drain_ms);
        loop {
            let report = self.drain(deadline).await?;
            if report.remaining == 0 || report.deadline_reached {
                break;
            }
            self.tick().await?;
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
        Ok(())
    }
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
        let remaining = s.actors.len() as u32;
        let deadline_reached = s.life.expired(self.clock.now_ms());
        if remaining == 0 || deadline_reached {
            s.actors.clear();
            s.life.phase = RuntimePhase::Stopped;
        }
        Ok(DrainReport {
            completed: s.completed,
            remaining,
            deadline_reached,
        })
    }
    async fn status(&self) -> RuntimeResult<ConnectorStatus> {
        let s = self.state.lock().await;
        Ok(ConnectorStatus {
            node: self.node.clone(),
            phase: s.life.phase,
            active_dispatches: s.actors.len() as u32,
        })
    }
}

fn bounded_error(error: RuntimeError) -> RuntimeError {
    fn clip(s: String) -> String {
        s.chars().take(512).collect()
    }
    match error {
        RuntimeError::InvalidInput(s) => RuntimeError::InvalidInput(clip(s)),
        RuntimeError::Unsupported(s) => RuntimeError::Unsupported(clip(s)),
        RuntimeError::Conflict(s) => RuntimeError::Conflict(clip(s)),
        RuntimeError::Storage(s) => RuntimeError::Storage(clip(s)),
        RuntimeError::Unavailable(s) => RuntimeError::Unavailable(clip(s)),
        other => other,
    }
}
