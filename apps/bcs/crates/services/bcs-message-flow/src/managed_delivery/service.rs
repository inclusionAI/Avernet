use super::*;

#[async_trait]
impl ManagedMessageDeliveryService for ManagedMessageDelivery {
    fn subscribe_changes(&self) -> Option<tokio::sync::broadcast::Receiver<Vec<PersistedMessageDelivery>>> {
        Some(self.subscribe())
    }

    /// Typed caller dispatch commit (§7.5.1): the only legitimate producer of
    /// the `caller_dispatch` projection keys. Six-field association must match
    /// the committed row exactly (env/target/session/run/attempt/dispatch);
    /// non-caller rows, wrong status, revision≠0 or misordered deadlines
    /// refuse. Returns true when a new commit was written, false for an
    /// identical same-version resubmission.
    async fn commit_caller_dispatch(
        &self,
        command: bcs_service_api::application::message_delivery::CommitCallerDispatchCommand,
    ) -> Result<bool, ManagedDeliveryError> {
        let commit = command.commit;
        if commit.request_digest.len() != 71 || commit.control_revision != 0 {
            return Err(ManagedDeliveryError::Conflict);
        }
        if commit.not_after_ms <= command.now_ms {
            return Err(ManagedDeliveryError::Conflict);
        }
        let row = self
            .repo
            .get_delivery(&command.delivery_id)
            .await?
            .ok_or(ManagedDeliveryError::NotFound)?;
        if row.env != command.dispatch.environment
            || row.target_bot_id != command.dispatch.target_bot_id
            || row.session_id != command.dispatch.session_id
            || row.run_id.as_deref() != Some(command.dispatch.run_id.as_str())
            || row.attempt_no != command.dispatch.attempt
            || row.request_id.as_deref() != Some(command.dispatch.dispatch_id.as_str())
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        // Caller rows only: the ordinary path must never carry the keys.
        if row.semantic_projection_json.get("caller_required").is_none() {
            return Err(ManagedDeliveryError::Conflict);
        }
        // Idempotent same-version resubmission: an identical existing
        // commit is a no-write success.
        if let Some(existing) = row.semantic_projection_json.get("caller_dispatch") {
            let identical = existing.get("version").and_then(|v| v.as_i64()) == Some(1)
                && existing.get("caller_request_digest").and_then(|v| v.as_str())
                    == Some(commit.request_digest.as_str())
                && existing.get("caller_not_after_ms").and_then(|v| v.as_i64())
                    == Some(commit.not_after_ms)
                && existing.get("caller_control_revision").and_then(|v| v.as_u64())
                    == Some(commit.control_revision);
            if identical && row.state.state_version == command.expected_state_version {
                return Ok(false);
            }
        }
        // A changed payload at any version conflicts; stale versions persist
        // through the ordinary CAS below.
        let transition = self
            .transition(DeliveryTransitionCommand {
                delivery_id: command.delivery_id,
                expected_state_version: command.expected_state_version,
                event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::CommitCallerDispatch,
                now_ms: command.now_ms,
                request_id: Some(command.dispatch.dispatch_id.clone()),
                actor_id: None,
                reply: None,
                transport_context_json: None,
                deadline_at_ms: None,
                caller_dispatch_commit: Some(commit),
                caller_cancel: None,
                caller_terminal: None,
            })
            .await
            .map_err(|_| ManagedDeliveryError::Conflict)?;
        let _ = transition;
        Ok(true)
    }

    async fn cancel_caller_dispatch(
        &self,
        command: bcs_service_api::application::message_delivery::CancelCallerDispatchCommand,
    ) -> Result<bool, ManagedDeliveryError> {
        let cancel = command.cancel;
        if cancel.cancel_request_id.trim().is_empty() {
            return Err(ManagedDeliveryError::Conflict);
        }
        let row = self
            .repo
            .get_delivery(&command.delivery_id)
            .await?
            .ok_or(ManagedDeliveryError::NotFound)?;
        if row.env != command.dispatch.environment
            || row.target_bot_id != command.dispatch.target_bot_id
            || row.session_id != command.dispatch.session_id
            || row.run_id.as_deref() != Some(command.dispatch.run_id.as_str())
            || row.attempt_no != command.dispatch.attempt
            || row.request_id.as_deref() != Some(command.dispatch.dispatch_id.as_str())
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        // Caller rows only: the ordinary path must never carry the keys.
        if row.semantic_projection_json.get("caller_required").is_none() {
            return Err(ManagedDeliveryError::Conflict);
        }
        // The intent freezes the committed dispatch facts: a revision or
        // deadline that drifted from the persisted commit conflicts.
        let Some(dispatch) = row.semantic_projection_json.get("caller_dispatch") else {
            return Err(ManagedDeliveryError::Conflict);
        };
        if dispatch.get("caller_control_revision").and_then(|v| v.as_u64()) != Some(cancel.control_revision) {
            return Err(ManagedDeliveryError::Conflict);
        }
        if dispatch.get("caller_not_after_ms").and_then(|v| v.as_i64()) != Some(cancel.not_after_ms) {
            return Err(ManagedDeliveryError::Conflict);
        }
        // A durably committed intent is replay-idempotent only for the SAME
        // intent at the row's current version; a different intent never
        // overwrites a committed one (spec §"取消意图与回执" 1).
        if let Some(existing) = row.semantic_projection_json.get("caller_cancel") {
            let identical = existing.get("version").and_then(|v| v.as_i64()) == Some(1)
                && existing.get("cancel_request_id").and_then(|v| v.as_str())
                    == Some(cancel.cancel_request_id.as_str())
                && existing.get("caller_control_revision").and_then(|v| v.as_u64())
                    == Some(cancel.control_revision)
                && existing.get("reason").and_then(|v| v.as_str()) == Some(cancel.reason.as_str())
                && existing.get("not_after_ms").and_then(|v| v.as_i64()) == Some(cancel.not_after_ms)
                && existing.get("cancel_state").and_then(|v| v.as_str()) == Some("requested");
            if !identical {
                return Err(ManagedDeliveryError::Conflict);
            }
            if matches!(row.state.status, Status::Cancelling | Status::CancelUnknown)
                && row.state.state_version == command.expected_state_version
            {
                return Ok(false);
            }
            return Err(ManagedDeliveryError::Conflict);
        }
        let transition = self
            .transition(DeliveryTransitionCommand {
                delivery_id: command.delivery_id,
                expected_state_version: command.expected_state_version,
                event: bcs_service_api::core::message_delivery::DeliveryLifecycleEvent::CancelCallerDispatch,
                now_ms: command.now_ms,
                request_id: Some(command.dispatch.dispatch_id.clone()),
                actor_id: None,
                reply: None,
                transport_context_json: None,
                deadline_at_ms: None,
                caller_dispatch_commit: None,
                caller_cancel: Some(cancel),
                caller_terminal: None,
            })
            .await
            .map_err(|_| ManagedDeliveryError::Conflict)?;
        let _ = transition;
        Ok(true)
    }

    async fn handle_caller_terminal(
        &self,
        command: bcs_service_api::application::message_delivery::CallerTerminalEventCommand,
    ) -> Result<bcs_service_api::application::message_delivery::CallerTerminalReceipt, ManagedDeliveryError> {
        let rows = self
            .repo
            .lookup(DeliveryLookup::Request(command.dispatch.dispatch_id.clone()))
            .await?;
        let row = rows
            .into_iter()
            .find(|row| {
                row.env == command.dispatch.environment
                    && row.target_bot_id == command.dispatch.target_bot_id
                    && row.session_id == command.dispatch.session_id
                    && row.run_id.as_deref() == Some(command.dispatch.run_id.as_str())
                    && row.attempt_no == command.dispatch.attempt
                    && row.request_id.as_deref() == Some(command.dispatch.dispatch_id.as_str())
            })
            .ok_or(ManagedDeliveryError::NotFound)?;
        // Caller rows only; a terminal observation for the ordinary path is
        // a contract violation, not a lifecycle event.
        if row.semantic_projection_json.get("caller_required").is_none() {
            return Err(ManagedDeliveryError::Conflict);
        }
        // The observation binds to committed dispatch facts — an
        // uncommitted row has nothing a Provider could attest against.
        if row.semantic_projection_json.get("caller_dispatch").is_none() {
            return Err(ManagedDeliveryError::Conflict);
        }
        let receipt_for = |row: &PersistedMessageDelivery| {
            let execution_state = serde_json::to_value(row.state.status)
                .ok()
                .and_then(|value| value.as_str().map(str::to_string))
                .unwrap_or_else(|| "unknown".to_string());
            bcs_service_api::application::message_delivery::CallerTerminalReceipt::Applied {
                delivery_id: row.delivery_id.clone(),
                execution_state,
                state_version: row.state.state_version,
            }
        };
        // 终态仲裁: an already-committed terminal is immutable — the exact
        // triple (outcome, event_seq, control_revision) replays as a
        // duplicate; anything else, including a higher sequence, conflicts.
        if let Some(existing) = row.semantic_projection_json.get("caller_terminal") {
            let identical = existing.get("version").and_then(|v| v.as_i64()) == Some(1)
                && existing.get("outcome").and_then(|v| v.as_str())
                    == Some(command.observation.outcome.as_str())
                && existing.get("event_seq").and_then(|v| v.as_u64()) == Some(command.observation.event_seq)
                && existing.get("caller_control_revision").and_then(|v| v.as_u64())
                    == Some(command.observation.control_revision);
            if !identical {
                return Err(ManagedDeliveryError::Conflict);
            }
            return Ok(match receipt_for(&row) {
                bcs_service_api::application::message_delivery::CallerTerminalReceipt::Applied {
                    delivery_id,
                    execution_state,
                    state_version,
                } => bcs_service_api::application::message_delivery::CallerTerminalReceipt::Duplicate {
                    delivery_id,
                    execution_state,
                    state_version,
                },
                other => other,
            });
        }
        // Outcome selects the ordinary terminal event; a cancellation that
        // already committed keeps its logical Cancelled state and records
        // the actual outcome independently.
        let event = if matches!(row.state.status, Status::Cancelling | Status::CancelUnknown) {
            Event::Aborted
        } else {
            match command.observation.outcome {
                bcs_service_api::application::message_delivery::CallerTerminalOutcome::Completed => Event::Completed,
                bcs_service_api::application::message_delivery::CallerTerminalOutcome::Failed => Event::Failed,
                bcs_service_api::application::message_delivery::CallerTerminalOutcome::Aborted => Event::Aborted,
            }
        };
        let updated = self
            .transition(DeliveryTransitionCommand {
                delivery_id: row.delivery_id.clone(),
                expected_state_version: row.state.state_version,
                event,
                now_ms: command.now_ms,
                request_id: Some(command.dispatch.dispatch_id.clone()),
                actor_id: None,
                reply: None,
                transport_context_json: None,
                deadline_at_ms: None,
                caller_dispatch_commit: None,
                caller_cancel: None,
                caller_terminal: Some(command.observation),
            })
            .await
            .map_err(|_| ManagedDeliveryError::Conflict)?;
        Ok(receipt_for(&updated))
    }

    async fn mark_run_transport_unknown(
        &self,scope:&bcs_service_api::BotRunScope,run_id:&str,now_ms:u64,
    )->Result<(),ManagedDeliveryError> {
        let now=i64::try_from(now_ms).map_err(|_|ManagedDeliveryError::Conflict)?;
        if run_id.is_empty() {return Err(ManagedDeliveryError::Conflict);}
        for _ in 0..3 {
            let rows=self.repo.lookup(DeliveryLookup::CanonicalRun {bot:scope.bot_id.clone(),session:scope.session_id.clone(),run:run_id.into()}).await?;
            let [row]=rows.as_slice() else {return Err(ManagedDeliveryError::NotFound);};
            if row.run_id.as_deref()!=Some(run_id) || row.target_bot_id!=scope.bot_id || row.group_id!=scope.group_id
                || row.session_id!=scope.session_id || row.state.kind!=DeliveryType::Send {return Err(ManagedDeliveryError::Conflict);}
            if row.state.status.is_terminal() || matches!(row.state.status,Status::Unknown | Status::CancelUnknown) {return Ok(());}
            if !row.state.may_have_been_sent || !matches!(row.state.status,Status::Dispatching | Status::Running | Status::Cancelling) {
                return Err(ManagedDeliveryError::Conflict);
            }
            let command=DeliveryTransitionCommand {delivery_id:row.delivery_id.clone(),expected_state_version:row.state.state_version,
                event:Event::TransportUnknown,now_ms:now,request_id:row.request_id.clone(),actor_id:None,reply:None,
                transport_context_json:None,deadline_at_ms:None,caller_dispatch_commit:None,caller_cancel:None,caller_terminal:None};
            match self.apply(command).await {
                Ok(_)=>return Ok(()),
                Err(ManagedDeliveryError::Repository(MessageDeliveryRepoError::Conflict))
                | Err(ManagedDeliveryError::Lifecycle(bcs_service_api::core::message_delivery::DeliveryLifecycleError::StaleVersion {..}))=>continue,
                Err(error)=>return Err(error),
            }
        }
        Err(ManagedDeliveryError::Conflict)
    }

    async fn reschedule_run_deadline(
        &self, scope: &bcs_service_api::BotRunScope, run_id: &str, deadline_ms: u64, now_ms: u64,
    ) -> Result<(), ManagedDeliveryError> {
        let deadline = i64::try_from(deadline_ms).map_err(|_|ManagedDeliveryError::Conflict)?;
        let now = i64::try_from(now_ms).map_err(|_|ManagedDeliveryError::Conflict)?;
        if run_id.is_empty() || deadline <= now { return Err(ManagedDeliveryError::Conflict); }
        for _ in 0..3 {
            let rows = self.repo.lookup(DeliveryLookup::CanonicalRun {bot:scope.bot_id.clone(),session:scope.session_id.clone(),run:run_id.to_string()}).await?;
            let mut exact = rows.iter().filter(|row|row.run_id.as_deref() == Some(run_id));
            let row = exact.next().ok_or(ManagedDeliveryError::NotFound)?;
            if exact.next().is_some() || row.target_bot_id != scope.bot_id || row.session_id != scope.session_id
                || row.group_id != scope.group_id || row.state.kind != DeliveryType::Send
                || !matches!(row.state.status, Status::Dispatching | Status::Running) {
                return Err(ManagedDeliveryError::Conflict);
            }
            let command = DeliveryTransitionCommand {delivery_id:row.delivery_id.clone(),expected_state_version:row.state.state_version,
                event:Event::RunDeadlineAdjusted,now_ms:now,request_id:row.request_id.clone(),actor_id:None,
                reply:None,transport_context_json:None,deadline_at_ms:Some(deadline),caller_dispatch_commit:None,caller_cancel:None,caller_terminal:None};
            match self.apply(command).await {
                Ok(updated) if updated.run_deadline_at_ms == Some(deadline)
                    && matches!(updated.state.status,Status::Dispatching | Status::Running) => return Ok(()),
                Err(ManagedDeliveryError::Repository(MessageDeliveryRepoError::Conflict))
                | Err(ManagedDeliveryError::Lifecycle(bcs_service_api::core::message_delivery::DeliveryLifecycleError::StaleVersion {..})) => continue,
                Err(error) => return Err(error),
                _ => return Err(ManagedDeliveryError::Conflict),
            }
        }
        Err(ManagedDeliveryError::Conflict)
    }

    async fn bounded_contexts(&self, carrier: &str, limit: usize) -> Result<BoundDeliveryContexts, ManagedDeliveryError> {
        let start = std::time::Instant::now();
        let result = self.repo.bounded_contexts(carrier, limit).await;
        self.observe_operation("bounded_contexts", start.elapsed().as_secs_f64(), result.as_ref().map_or(0, |v| v.rows.len()), result.is_ok());
        Ok(result?)
    }
    async fn lane_blocked(&self, row: &PersistedMessageDelivery) -> Result<bool, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.lane_blocked(row).await;
        self.observe_operation("lane_blocked", start.elapsed().as_secs_f64(), usize::from(result.is_ok()), result.is_ok()); Ok(result?)
    }
    fn observe_operation(&self, operation: &'static str, seconds: f64, rows: usize, success: bool) { if let Some(hook) = &self.instrumentation { hook.operation(operation, seconds, rows, success); } }
    async fn lookup(&self, scope: DeliveryLookup) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.lookup(scope).await;
        self.observe_operation("lookup", start.elapsed().as_secs_f64(), result.as_ref().map_or(0, Vec::len), result.is_ok()); Ok(result?)
    }
    async fn queued_bots(&self, after: &str, limit: usize) -> Result<Vec<String>, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.queued_bots(after, limit).await;
        self.observe_operation("queued_bots", start.elapsed().as_secs_f64(), result.as_ref().map_or(0, Vec::len), result.is_ok()); Ok(result?)
    }
    async fn active_count(&self, bot: &str) -> Result<u64, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.active_count(bot).await;
        self.observe_operation("active_count", start.elapsed().as_secs_f64(), usize::from(result.is_ok()), result.is_ok()); Ok(result?)
    }
    async fn queued_heads(&self, bot: &str, after: &str, limit: usize) -> Result<Vec<bcs_service_api::core::message_delivery::DeliveryScheduleEntry>, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.queued_heads(bot, after, limit).await;
        self.observe_operation("queued_heads", start.elapsed().as_secs_f64(), result.as_ref().map_or(0, Vec::len), result.is_ok()); Ok(result?)
    }
    async fn work_batch(&self, kind: DeliveryWorkBatch, now_ms: i64, after: &str, limit: usize) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.work_batch(kind, now_ms, after, limit).await;
        let operation = match kind { DeliveryWorkBatch::Expired => "expiry_batch", DeliveryWorkBatch::Control => "control_batch", DeliveryWorkBatch::Recovery => "recovery_batch" };
        self.observe_operation(operation, start.elapsed().as_secs_f64(), result.as_ref().map_or(0, Vec::len), result.is_ok()); Ok(result?)
    }
    async fn queue_statistics(&self) -> Result<Vec<DeliveryQueueStatistic>, ManagedDeliveryError> {
        let start = std::time::Instant::now(); let result = self.repo.queue_statistics().await;
        self.observe_operation("queue_statistics", start.elapsed().as_secs_f64(), result.as_ref().map_or(0, Vec::len), result.is_ok()); Ok(result?)
    }
    async fn unfinished(&self) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        Ok(self.repo.list_unfinished_deliveries().await?)
    }
    async fn update_wait_reasons(
        &self,
        waiting: Vec<(String, bcs_domain::message_delivery::DeliveryWaitReason)>,
        now_ms: i64,
    ) -> Result<(), ManagedDeliveryError> {
        if waiting.is_empty() {
            return Ok(());
        }
        let reasons: BTreeMap<_, _> = waiting.into_iter().collect();
        let mut owners = BTreeMap::new();
        for id in reasons.keys() {
            if let Some(row) = self.repo.get_delivery(id).await? { owners.insert(id.clone(), row.target_bot_id); }
        }
        let _guards = self.mutations.acquire(owners.values().cloned()).await;
        let mut updates = Vec::new();
        for id in reasons.keys() {
            let Some(mut row) = self.repo.get_delivery(id).await? else { continue; };
            if owners.get(id) != Some(&row.target_bot_id) { return Err(ManagedDeliveryError::Conflict); }
            let Some(reason) = reasons.get(&row.delivery_id) else {
                continue;
            };
            // Review-V P1-3: the barrier marker is the one wait reason that
            // rides a Dispatching (attempt-committed) row — every other
            // reason stays Queued-only.
            let barrier_park = matches!(
                reason,
                bcs_domain::message_delivery::DeliveryWaitReason::HistoryBarrier
            ) && matches!(row.state.status, Status::Dispatching);
            if (!barrier_park && row.state.status != Status::Queued) || row.wait_reason == Some(*reason) {
                continue;
            }
            let expected_state_version = row.state.state_version;
            row.state.state_version = expected_state_version
                .checked_add(1)
                .ok_or(ManagedDeliveryError::Conflict)?;
            row.wait_reason = Some(*reason);
            row.updated_at_ms = now_ms;
            updates.push(DeliveryCompareAndSet {
                expected_state_version,
                delivery: row,
            });
        }
        if !updates.is_empty() {
            let notifications = updates.iter().map(|u| u.delivery.clone()).collect();
            let repo = self.repo.clone();
            let (_, _guards) = persist_with_guards(_guards, async move { repo.commit_transition(updates, None).await }).await?;
            let _ = self.changes.send(notifications);
        }
        Ok(())
    }
    async fn accept_run(
        &self,
        request_id: &str,
        bot_id: &str,
        downstream_run_id: Option<&str>,
        now_ms: i64,
    ) -> Result<Option<PersistedMessageDelivery>, ManagedDeliveryError> {
        let _guards = self.mutations.acquire([bot_id.to_string()]).await;
        let rows = self.repo.lookup(DeliveryLookup::Request(request_id.into())).await?;
        let Some(original) = rows
            .iter()
            .find(|d| d.request_id.as_deref() == Some(request_id))
        else {
            return Ok(None);
        };
        if original.target_bot_id != bot_id || downstream_run_id.is_some_and(str::is_empty) {
            return Err(ManagedDeliveryError::Conflict);
        }
        let outcome = MessageDeliveryCore.transition(
            original.state,
            original.state.state_version,
            Event::Accepted,
        )?;
        if !matches!(
            original.state.status,
            Status::Dispatching
                | Status::Running
                | Status::Unknown
                | Status::Cancelling
                | Status::CancelUnknown
        ) {
            return Ok(Some(original.clone()));
        }
        let mut next = original.clone();
        if let Some(alias) = downstream_run_id {
            if self.repo.lookup(DeliveryLookup::Run { bot: bot_id.into(), alias: alias.into() }).await?.iter().any(|d| {
                d.delivery_id != original.delivery_id
                    && d.target_bot_id == bot_id
                    && (d.run_id.as_deref() == Some(alias)
                        || d.request_id.as_deref() == Some(alias)
                        || d.transport_context_json
                            .as_ref()
                            .and_then(|v| v.get("downstream_run_id"))
                            .and_then(|v| v.as_str())
                            == Some(alias))
            }) {
                return Err(ManagedDeliveryError::Conflict);
            }
            let metadata = next
                .transport_context_json
                .as_mut()
                .and_then(serde_json::Value::as_object_mut)
                .ok_or(ManagedDeliveryError::Conflict)?;
            if metadata
                .get("downstream_run_id")
                .and_then(|v| v.as_str())
                .is_some_and(|old| old != alias)
            {
                return Err(ManagedDeliveryError::Conflict);
            }
            metadata.insert(
                "downstream_run_id".into(),
                serde_json::Value::String(alias.into()),
            );
        }
        if !outcome.changed
            && original.accepted_at_ms.is_some()
            && next.transport_context_json == original.transport_context_json
        {
            return Ok(Some(original.clone()));
        }
        next.state = outcome.state;
        next.state.state_version = original
            .state
            .state_version
            .checked_add(1)
            .ok_or(ManagedDeliveryError::Conflict)?;
        next.accepted_at_ms.get_or_insert(now_ms);
        next.updated_at_ms = now_ms;
        let updates = vec![DeliveryCompareAndSet {
                    expected_state_version: original.state.state_version,
                    delivery: next.clone(),
                }];
        let repo = self.repo.clone();
        let (_, _guards) = persist_with_guards(_guards, async move { repo.commit_transition(updates, None).await }).await?;
        let _ = self.changes.send(vec![next.clone()]);
        Ok(Some(next))
    }

    async fn admit(
        &self,
        command: AdmitMessageDeliveries,
    ) -> Result<DeliveryAdmissionResult, ManagedDeliveryError> {
        self.admit_batch(vec![command]).await?.pop().ok_or(ManagedDeliveryError::Conflict)
    }
    async fn admit_batch(&self, mut commands: Vec<AdmitMessageDeliveries>) -> Result<Vec<DeliveryAdmissionResult>, ManagedDeliveryError> {
        let _slot = self
            .admission_slots
            .try_acquire()
            .map_err(|_| ManagedDeliveryError::Conflict)?;
        let bots: Vec<String> = commands.iter().flat_map(|c| c.targets.iter().map(|t| t.target_bot_id.clone())).collect();
        let _guards = self.mutations.acquire(bots).await;
        let policy = match &self.policy { Some(live) => Some(live.snapshot.read().await), None => None };
        if let Some(policy) = &policy {
          for command in &mut commands {
            if !matches!(command.flow_kind, bcs_domain::message_delivery::DeliveryFlowKind::Group | bcs_domain::message_delivery::DeliveryFlowKind::System | bcs_domain::message_delivery::DeliveryFlowKind::Task | bcs_domain::message_delivery::DeliveryFlowKind::DirectA2a) { return Err(ManagedDeliveryError::Conflict); }
            for target in &mut command.targets {
                let drain = target.kind == DeliveryType::Send && target.semantic_projection_json.get("drain_context").and_then(|v| v.as_bool()) == Some(true)
                    && !self.repo.lookup(DeliveryLookup::LanePendingContextCarrier { bot: target.target_bot_id.clone(), session: command.message.session_id.clone(), now_ms: command.now_ms }).await?.is_empty();
                let enabled = match command.flow_kind {
                    bcs_domain::message_delivery::DeliveryFlowKind::DirectA2a => policy.policy.manages_direct_a2a(&target.target_bot_id),
                    bcs_domain::message_delivery::DeliveryFlowKind::System => policy.policy.manages_system(&target.target_bot_id),
                    bcs_domain::message_delivery::DeliveryFlowKind::Task => policy.policy.manages_task(&target.target_bot_id),
                    _ => policy.policy.manages_group(&target.target_bot_id),
                };
                // Task-control notices are server-produced outcomes, not ordinary
                // chat/system admission. They must work with task-only policy and
                // a Manager whose own task queue is off; the scheduler still owns
                // their durable Send and enforces lane ordering/capacity.
                let task_notice = command.flow_kind == bcs_domain::message_delivery::DeliveryFlowKind::System
                    && command.message.sender_id == "system" && command.message.sender_type == bcs_domain::SenderType::System
                    && command.message.client_msg_id.as_deref().is_some_and(|key| key.starts_with("task-rejected:") || key.starts_with("task-dispatch-unknown:") || key.starts_with("task-unknown:") || key.starts_with("task-timeout-unknown:"))
                    && target.semantic_projection_json.get("task_control_notice").and_then(|v| v.as_bool()) == Some(true);
                if (!enabled && !drain && !task_notice)
                    || (drain && target.semantic_projection_json.get("policy_version").and_then(|v| v.as_u64()) != Some(policy.version))
                    || target.semantic_projection_json.get("policy_version").and_then(|v| v.as_u64()).is_some_and(|version| version != policy.version) {
                    return Err(ManagedDeliveryError::Conflict);
                }
                target.max_queued = policy.policy.bot(&target.target_bot_id).max_queued;
            }
            // §15.98: the policy expiry CAPS the admission's own TTL instead of
            // replacing it (the old overwrite silently deleted a carried
            // expiry whenever queue_ttl_ms was unset); with no source at
            // all the fail-safe floor keeps rows mortal. The floor is
            // anchored to the SERVER clock, never command.now_ms — the
            // caller-provided clock may be arbitrary and must not decide
            // the effective protection window.
            let policy_expiry = policy.policy.queue_ttl_ms.map(|ttl| command.now_ms.saturating_add(ttl as i64));
            let admission_expiry = command.expire_at_ms;
            let fallback_needed = policy_expiry.is_none()
                && admission_expiry.is_none()
                && command.targets.iter().any(|t| t.semantic_projection_json.get("caller_required").is_some());
            if fallback_needed
                && !QUEUE_TTL_FALLBACK_WARNED.swap(true, std::sync::atomic::Ordering::Relaxed)
            {
                tracing::warn!(
                    "delivery policy sets no queue_ttl_ms and this caller admission carries no expiry: \
                     applying the {}h fail-safe floor — configure queue_ttl_ms to take control",
                    QUEUE_TTL_FALLBACK_MS / 3_600_000,
                );
            }
            let cap = |expiry: i64| {
                let mut capped = expiry;
                if let Some(policy_expiry) = policy_expiry {
                    capped = capped.min(policy_expiry);
                }
                if let Some(admission_expiry) = admission_expiry {
                    capped = capped.min(admission_expiry);
                }
                capped
            };
            command.expire_at_ms = if command.flow_kind == bcs_domain::message_delivery::DeliveryFlowKind::DirectA2a {
                let deadline = command.targets.first().and_then(|t| t.semantic_projection_json.get("expires_at_ms")).and_then(|v| v.as_i64()).ok_or(ManagedDeliveryError::Conflict)?;
                Some(cap(deadline))
            } else {
                Some(match (admission_expiry, policy_expiry) {
                    (Some(admission), Some(policy)) => admission.min(policy),
                    (Some(admission), None) => admission,
                    (None, Some(policy)) => policy,
                    (None, None) => (bcs_protocol::now_ms() as i64).saturating_add(QUEUE_TTL_FALLBACK_MS),
                })
            };
          }
        }
        if !self
            .admission_available
            .load(std::sync::atomic::Ordering::SeqCst)
        {
            return Err(ManagedDeliveryError::Conflict);
        }
        let repo = self.repo.clone();
        let (results, _guards) = persist_with_guards(_guards, async move { repo.admit_batch(commands).await }).await?;
        for result in &results {
        tracing::debug!(message_id = %result.message.message_id, session_id = %result.message.session_id,
            duplicate = result.duplicate, target_count = result.deliveries.len(),
            rejected_count = result.deliveries.iter().filter(|row| row.state.status == Status::RejectedCapacity).count(),
            "managed message admission committed");
        if !result.duplicate {
            if let Some(hook) = &self.instrumentation { for row in &result.deliveries { hook.event("admitted", row); } }
            let _ = self.changes.send(result.deliveries.clone());
        }
        }
        Ok(results)
    }
    async fn snapshot(
        &self,
        session_id: Option<&str>,
    ) -> Result<Vec<PersistedMessageDelivery>, ManagedDeliveryError> {
        Ok(self.repo.list_deliveries(session_id).await?)
    }
    async fn transition(
        &self,
        command: DeliveryTransitionCommand,
    ) -> Result<PersistedMessageDelivery, ManagedDeliveryError> {
        self.apply(command).await
    }
    async fn recover(&self, now_ms: i64) -> Result<(), ManagedDeliveryError> {
        let mut cursor = String::new();
        loop {
        let batch = self.repo.work_batch(DeliveryWorkBatch::Recovery, now_ms, &cursor, 200).await?;
        if batch.is_empty() { break; }
        for row in batch {
            cursor = row.delivery_id.clone();
            self.apply(DeliveryTransitionCommand {
                caller_dispatch_commit: None,
                delivery_id: row.delivery_id,
                expected_state_version: row.state.state_version,
                event: Event::Recover,
                now_ms,
                request_id: None,
                actor_id: None,
                reply: None,
                transport_context_json: None,
                deadline_at_ms: None,
                caller_cancel: None,
                caller_terminal: None,
            })
            .await?;
        }
        }
        Ok(())
    }
}
