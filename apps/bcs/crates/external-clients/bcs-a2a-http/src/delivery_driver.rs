use super::*;

impl A2aHttpTransport {
    pub(super) async fn apply(&self, record: &mut A2aRunRecord, projection: &mut Projection, event: StreamResponse) -> ServiceResult<bool> {
        let (mut next, outputs) = projection.stage(event).map_err(|_|invalid("unsupported or inconsistent remote event"))?;
        let mut finished = false;
        let mut input_message=None;
        // Consume in order. A later output failure never replays prior accepted
        // deltas: this pending batch is not restarted from its original event.
        for output in outputs {
            match output {
                Output::Identity { task_id, context_id } => {
                    if record.context_id.as_ref().is_some_and(|id|id != &context_id) { return Err(invalid("remote context changed")); }
                    record.remote_task_id = Some(task_id); record.context_id = Some(context_id);
                    self.save(record, A2aRunState::Running).await?;
                }
                Output::Delta(text) => self.emit(record, ChatEventState::Delta, Some(text), None).await?,
                Output::MessageFinal(text) => {
                    if !text.is_empty() { self.emit(record, ChatEventState::Delta, Some(text), None).await?; }
                    self.save(record, A2aRunState::Completed).await?;
                    self.emit(record, ChatEventState::Final, None, None).await?;
                    finished = true;
                    self.runtime()?.contexts.mark_provider_transport_terminal(&record.run_id).await;
                }
                Output::Status(state) => match state {
                    TaskState::Completed | TaskState::Failed | TaskState::Canceled | TaskState::Rejected => {
                        let (state, remote) = match state {
                            TaskState::Completed => (ChatEventState::Final, A2aRunState::Completed),
                            TaskState::Canceled => (ChatEventState::Aborted, A2aRunState::Canceled),
                            _ => (ChatEventState::Error, A2aRunState::Failed),
                        };
                        self.save(record, remote).await?;
                        self.emit(record, state, None, (remote == A2aRunState::Failed).then_some("remote_task_failed")).await?;
                        self.runtime()?.contexts.mark_provider_transport_terminal(&record.run_id).await;
                        finished = true;
                    }
                    TaskState::InputRequired => self.request_input(record,input_message.take().ok_or_else(||invalid("input schema status message required"))?).await?,
                    TaskState::AuthRequired => return Err(invalid("dynamic authorization not configured")),
                    TaskState::Submitted | TaskState::Working => {}
                    _ => {
                        if !next.begin_state_recovery() {return Err(invalid("unknown Task state recovery budget exhausted"));}
                        let task=self.snapshot(record).await?;
                        if !matches!(task.status.state,TaskState::Submitted | TaskState::Working | TaskState::Completed
                            | TaskState::Failed | TaskState::Canceled | TaskState::InputRequired | TaskState::Rejected | TaskState::AuthRequired) {
                            return Err(invalid("unknown remote Task state"));
                        }
                        // Earlier outputs of this batch were already ingested.
                        // Commit before calibrating the queried snapshot so its
                        // Artifact cannot replay that accepted text prefix.
                        *projection=next;
                        return Box::pin(self.apply(record,projection,StreamResponse::Task(task))).await;
                    }
                },
                // Standard status text is diagnostic, never an Artifact answer.
                // Negotiated tool/Structured Input decoding is connected separately.
                Output::StatusMessage(message) => input_message=Some(message),
                Output::Tool(payload) => {
                    let state = serde_json::from_value(payload["state"].clone())?;
                    self.emit_payload(record, state, payload).await?;
                }
            }
        }
        *projection = next;
        Ok(finished)
    }

    pub(super) async fn tracking_failed(&self, record: &mut A2aRunRecord) {
        match self.inner.runs.get(&record.run_id).await {
            Ok(Some(current)) if current.state.is_terminal() => return,
            Ok(Some(mut current)) => {
                // A failed first identity write may leave a durable Submitting
                // row without Task ID. Retain the authenticated response's ID
                // solely for exact cleanup of this same immutable submission.
                if current.remote_task_id.is_none() && current.run_id==record.run_id && current.scope==record.scope
                    && current.group_id==record.group_id && current.interface==record.interface
                    && current.message_id==record.message_id && current.request_digest==record.request_digest
                    && current.credential_kind==record.credential_kind
                    && (current.context_id.is_none() || current.context_id==record.context_id) {
                    current.remote_task_id=record.remote_task_id.clone();
                    current.context_id=record.context_id.clone();
                }
                *record=current;
            }
            _ => {
                tracing::error!(run_id = %record.run_id, "A2A tracking failure could not read durable correlation");
            }
        }
        if self.tracking_lost(record).await.is_err() {
            tracing::error!(run_id = %record.run_id, "A2A tracking failure could not preserve uncertain delivery state");
        }
        if record.remote_task_id.is_some() {
            let stopped = match self.cancel_remote(record, 15_000).await {
                Ok(task) if task.status.state.is_terminal() => Some(task),
                _ => self.snapshot(record).await.ok().filter(|task| task.status.state.is_terminal()),
            };
            match stopped {
                Some(task) => {
                    let state = match task.status.state {
                        TaskState::Completed => A2aRunState::Completed,
                        TaskState::Canceled => A2aRunState::Canceled,
                        _ => A2aRunState::Failed,
                    };
                    if self.save(record, state).await.is_err() {
                        tracing::error!(run_id = %record.run_id, "A2A cleanup result could not be persisted");
                        return;
                    }
                    // Only a confirmed remote terminal permits releasing the
                    // local execution slot after observation/conversion failed.
                    if self.emit(record, ChatEventState::Error, None, Some("tracking_failed")).await.is_err() {
                        tracing::error!(run_id = %record.run_id, "A2A tracking failure could not publish its error terminal");
                        return;
                    }
                    match self.runtime() {
                        Ok(runtime) => runtime.contexts.mark_provider_transport_terminal(&record.run_id).await,
                        Err(_) => tracing::error!(run_id = %record.run_id, "A2A tracking failure could not mark its transport terminal"),
                    }
                    return;
                }
                _ => tracing::warn!(run_id = %record.run_id, "A2A cleanup unconfirmed; context remains blocked"),
            }
        }
        if !record.state.is_terminal() && self.save(record, A2aRunState::Unknown).await.is_err() {
            tracing::error!(run_id = %record.run_id, "A2A tracking failure could not persist unknown outcome");
        }
    }

    async fn cancel_remote(&self, record: &A2aRunRecord, timeout_ms: u64) -> ServiceResult<a2a::Task> {
        if self.inner.runs.has_other_active_task_run(record).await? {
            return Err(invalid("shared Task has another active Run; run-scoped cancellation would widen scope"));
        }
        let deadline=tokio::time::Instant::now()+Duration::from_millis(timeout_ms);
        let task = record.remote_task_id.as_ref().ok_or_else(||invalid("cancel cannot infer remote Task ID"))?;
        let request = serde_json::from_value(json!({"id":task}))?;
        let result = tokio::time::timeout(Duration::from_millis(timeout_ms), self.client(record).await?.cancel_task(&request)).await
            .map_err(|_|invalid("cancel outcome unknown"))?;
        let result=match result {
            Ok(task)=>task,
            Err(error) if error.code==a2a::error_code::TASK_NOT_CANCELABLE=>{
                match tokio::time::timeout_at(deadline,self.snapshot(record)).await {
                    Ok(Ok(task)) if task.status.state.is_terminal()=>task,
                    _=>return Err(delivery_error::protocol_failure(&error,"cancel rejected or unconfirmed")),
                }
            }
            Err(error)=>return Err(delivery_error::protocol_failure(&error,"cancel rejected or unconfirmed")),
        };
        if result.id != *task || Some(&result.context_id) != record.context_id.as_ref() { return Err(invalid("cancel response identity mismatch")); }
        Ok(result)
    }

    pub(super) async fn snapshot(&self, record: &A2aRunRecord) -> ServiceResult<a2a::Task> {
        let task_id = record.remote_task_id.as_ref().ok_or_else(||invalid("remote Task not yet known"))?;
        let request = serde_json::from_value(json!({"id":task_id}))?;
        let task = tokio::time::timeout(Duration::from_secs(15), self.client(record).await?.get_task(&request)).await
            .map_err(|_|invalid("Task query timed out"))?.map_err(|error|delivery_error::protocol_failure(&error,"Task query failed"))?;
        if &task.id != task_id || Some(&task.context_id) != record.context_id.as_ref() { return Err(invalid("Task query identity mismatch")); }
        Ok(task)
    }

    pub(super) async fn drive(&self, record: &mut A2aRunRecord, projection: &mut Projection,
        stream: BoxStream<'static, Result<StreamResponse, a2a::A2AError>>, mut stop: tokio::sync::watch::Receiver<bool>,
        mut resolutions:tokio::sync::mpsc::Receiver<interaction::Resolution>) -> ServiceResult<()> {
        let mut stream=Some(stream);
        let mut recoveries = 0;
        'tracking: loop {
            if record.state==A2aRunState::InputRequired {
                drop(stream.take());
                let Some(task)=self.wait_input(record,&mut stop,&mut resolutions).await? else {return Ok(());};
                if self.apply(record,projection,StreamResponse::Task(task)).await? {return Ok(());}
                if record.state==A2aRunState::InputRequired {continue;}
                recoveries=0;
            }
            let remaining = record.deadline_ms.saturating_sub(now_ms());
            if remaining == 0 { return Err(invalid("execution budget expired")); }
            let next = if let Some(stream)=stream.as_mut() {tokio::select! {
                _ = stop.changed() => return Ok(()),
                event = tokio::time::timeout(Duration::from_millis(remaining.min(900_000)), stream.next()) => event,
            }} else {Ok(None)};
            if let Ok(Some(Ok(event))) = next {
                    let event = if matches!(&event, StreamResponse::StatusUpdate(update) if update.status.state.is_terminal()) {
                        // Calibrate the authoritative final Artifact snapshot before
                        // emitting a BCS terminal. A clean SSE close is insufficient.
                        self.snapshot(record).await.ok().map(StreamResponse::Task)
                    } else { Some(event) };
                    if let Some(event) = event {
                        if self.apply(record, projection, event).await? { return Ok(()); }
                        // Only an accepted event from the stream proves recovery.
                        // A successful HTTP subscribe followed by EOF does not.
                        recoveries = 0;
                        continue;
                    }
            }
            // Query and subscribe are one bounded recovery operation. A failed
            // query or a terminal race at subscribe does not consume the entire
            // retry policy on its first transient network error.
            loop {
                    recoveries += 1;
                    if recoveries > 3 { return Err(invalid("stream recovery budget exhausted")); }
                    if now_ms() >= record.deadline_ms { return Err(invalid("execution budget expired")); }
                    if let Ok(task) = self.snapshot(record).await {
                        if self.apply(record, projection, StreamResponse::Task(task)).await? { return Ok(()); }
                        if record.state==A2aRunState::InputRequired {continue 'tracking;}
                        let request = serde_json::from_value(json!({"id":record.remote_task_id}))?;
                        if let Ok(client) = self.client(record).await {
                            if let Ok(Ok(recovered)) = tokio::time::timeout(Duration::from_secs(15), client.subscribe_to_task(&request)).await {
                                stream = Some(recovered);
                                break;
                            }
                        }
                    }
                    tokio::select! {
                        _ = stop.changed() => return Ok(()),
                        _ = tokio::time::sleep(Duration::from_millis(250)) => {}
                    }
            }
        }
    }

    pub(super) async fn cancel(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        let BotDeliveryTarget::A2a { bot_id, provider_id, provider_bot_ref, interface, .. } = &cmd.target else {
            return Err(invalid("incorrect cancel target"));
        };
        let scope = A2aContextScope { bot_id: bot_id.clone(), provider_id: provider_id.clone(), provider_bot_ref: provider_bot_ref.clone(),
            session_id: cmd.session_id.clone(), interface_url: interface.url.clone(), tenant: interface.tenant.clone(),
            route_fingerprint:self.route_fingerprint(interface)? };
        let mut record = if let Some(run) = &cmd.run_id {
            self.inner.runs.get(run).await?.ok_or_else(||invalid("cancel Run not found"))?
        } else {
            let mut runs = self.inner.runs.active(&scope).await?;
            if runs.len() != 1 { return Err(invalid("cancel scope is absent or ambiguous")); }
            runs.remove(0)
        };
        if record.scope.bot_id != *bot_id || record.scope.provider_id != *provider_id
            || record.scope.provider_bot_ref != *provider_bot_ref || record.scope.session_id != cmd.session_id
            || record.group_id != cmd.group_id { return Err(invalid("cancel scope mismatch")); }
        if record.state.is_terminal() {
            return Ok(BotAbortDeliveryResult {target_bot_id:bot_id.clone(),
                aborted_run_ids:if record.state==A2aRunState::Canceled {vec![record.run_id]} else {vec![]}});
        }
        let result = self.cancel_remote(&record, cmd.timeout_ms).await?;
        let mut aborted = Vec::new();
        if result.status.state == TaskState::Canceled {
            // Confirm the actual Task, then fence against concurrent terminal
            // persistence. Never return a successful broad scope cancellation.
            for _ in 0..3 {
                if record.state == A2aRunState::Canceled { aborted.push(record.run_id.clone()); break; }
                if record.state.is_terminal() { break; }
                match self.save(&mut record, A2aRunState::Canceled).await {
                    Ok(()) => { aborted.push(record.run_id.clone()); break; }
                    Err(ServiceError::Conflict(_)) => {}
                    Err(error) => return Err(error),
                }
                record = self.inner.runs.get(&record.run_id).await?.ok_or_else(||invalid("cancel correlation missing"))?;
            }
            if aborted.is_empty() && !record.state.is_terminal() { return Err(invalid("cancel persistence unconfirmed")); }
        }
        if !aborted.is_empty() {
            if let Some(stop) = self.inner.active.lock().map_err(|_|invalid("active map lock failed"))?.get(&record.run_id) {
                // No receiver means the stream already ended; remote cancellation
                // is still confirmed by the response and durable state above.
                stop.stop.send_replace(true);
            }
        }
        Ok(BotAbortDeliveryResult { target_bot_id: bot_id.clone(), aborted_run_ids: aborted })
    }
}
