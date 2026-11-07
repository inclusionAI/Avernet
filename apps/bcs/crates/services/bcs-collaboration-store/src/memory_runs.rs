use super::*;
#[async_trait]
impl StateMachineRunRepoPort for MemoryCollaborationStore {
    async fn get_node_statuses(&self, run: &str, nodes: &[String]) -> ServiceResult<BTreeMap<String, StateMachineNodeStatus>> {
        self.branch_node_statuses(run, nodes).await
    }
    async fn skip_nodes(&self, run: &str, nodes: &[String], at: u64) -> ServiceResult<bool> {
        self.skip_branch_nodes(run, nodes, at).await
    }
    async fn accept_history_message(&self, command: AcceptStateMachineHistory) -> ServiceResult<Option<StateMachineHistoryCheckpoint>> {
        let checkpoint = history::checkpoint("memory", command.payload.clone())?;
        Ok(self.commit_eventful_transition(StateMachineEventfulTransition::AcceptHistory(command)).await?.then_some(checkpoint))
    }
    async fn get_history_message(&self, id: &StateMachineHistoryIdentity) -> ServiceResult<Option<StateMachineHistoryCheckpoint>> { self.history_get(id).await }
    async fn list_history_messages_pending(&self, after: Option<&StateMachineHistoryCursor>, limit: usize) -> ServiceResult<StateMachineHistoryPage> { self.history_page(after, limit).await }
    async fn confirm_history_message(&self, checkpoint: &StateMachineHistoryCheckpoint, at: u64) -> ServiceResult<()> { self.history_confirm(checkpoint, at).await }

    async fn list_unrepaired_history_runs(&self, runs: &[String]) -> ServiceResult<Vec<String>> { self.unrepaired_history_runs(runs).await }
    async fn confirm_terminal_history_repair(&self, run: &str, at: u64) -> ServiceResult<()> { self.confirm_history_repair(run, at).await }
    async fn list_terminal_runs_for_cleanup(&self, after: Option<&str>, limit: usize) -> ServiceResult<Vec<String>> { self.terminal_cleanup_candidates(after, limit).await }
    async fn cleanup_terminal_run_checkpoints(&self, run: &str, limit: usize) -> ServiceResult<usize> { self.cleanup_terminal_work(run, limit).await }
    async fn fail_missing_startup(&self, command: bcs_service_api::FailStateMachineStartup) -> ServiceResult<bool> { self.fail_startup_gap(command).await }
    async fn get_startup_failure(&self, run: &str) -> ServiceResult<Option<bcs_service_api::StateMachineStartupFailure>> { self.startup_failure(run).await }
    async fn fail_missing_dispatch(&self, run: &str, node: &str, attempt: i32, now: u64) -> ServiceResult<bool> { self.fail_dispatch_gap(run, node, attempt, now).await }

    async fn save_terminal_im(&self, payload: bcs_service_api::StateMachineTerminalImPayload) -> ServiceResult<bool> { self.save_terminal_notification(payload).await }
    async fn get_terminal_im(&self, run: &str) -> ServiceResult<Option<bcs_service_api::StateMachineTerminalImCheckpoint>> { self.get_terminal_notification(run).await }
    async fn list_terminal_im_pending(&self, after: Option<&str>, limit: usize) -> ServiceResult<Vec<String>> { self.list_terminal_notifications(after, limit).await }
    async fn claim_terminal_im(&self, run: &str, owner: String, now: u64, until: u64) -> ServiceResult<Option<bcs_service_api::StateMachineTerminalImClaim>> { self.claim_terminal_notification(run, owner, now, until).await }
    async fn update_terminal_im_progress(&self, claim: &bcs_service_api::StateMachineTerminalImClaim, expected: bcs_service_api::StateMachineTerminalImProgress, next: bcs_service_api::StateMachineTerminalImProgress, now: u64) -> ServiceResult<bool> { self.update_terminal_notification(claim, expected, next, now).await }
    async fn release_terminal_im(&self, claim: &bcs_service_api::StateMachineTerminalImClaim) -> ServiceResult<bool> { self.release_terminal_notification(claim).await }
    async fn supersede_terminal_im(&self, run: &str) -> ServiceResult<bool> { self.supersede_terminal_notification(run).await }

    async fn save_chat_result(&self, payload: bcs_service_api::StateMachineChatResultPayload) -> ServiceResult<bool> { self.save_publication(payload).await }
    async fn get_chat_result(&self, run: &str) -> ServiceResult<Option<bcs_service_api::StateMachineChatResultCheckpoint>> { self.get_publication(run).await }
    async fn claim_chat_result(&self, run: &str, owner: String, now: u64, until: u64) -> ServiceResult<Option<bcs_service_api::StateMachineChatResultClaim>> { self.claim_publication(run, owner, now, until).await }
    async fn begin_chat_result_send(&self, claim: &bcs_service_api::StateMachineChatResultClaim, now: u64) -> ServiceResult<bool> { self.begin_publication(claim, now).await }
    async fn finish_chat_result(&self, claim: &bcs_service_api::StateMachineChatResultClaim, outcome: bcs_service_api::StateMachineChatResultOutcome, now: u64) -> ServiceResult<bool> { self.finish_publication(claim, outcome, now).await }
    async fn expire_chat_result(&self, run: &str, now: u64) -> ServiceResult<bool> { self.expire_publication(run, now).await }
    async fn release_chat_result(&self, claim: &bcs_service_api::StateMachineChatResultClaim) -> ServiceResult<bool> { self.release_publication(claim).await }

    async fn save_node_dispatch(&self, payload: StateMachineDispatchPayload) -> ServiceResult<bool> { self.save_dispatch(payload).await }
    async fn get_node_dispatch(&self, run: &str, node: &str, attempt: i32) -> ServiceResult<Option<StateMachineDispatchCheckpoint>> { self.get_dispatch(run, node, attempt).await }
    async fn claim_node_dispatch(&self, run: &str, node: &str, attempt: i32, owner: String, now: u64, until: u64) -> ServiceResult<Option<StateMachineDispatchClaim>> { self.claim_dispatch(run, node, attempt, owner, now, until).await }
    async fn begin_node_dispatch_send(&self, claim: &StateMachineDispatchClaim, now: u64) -> ServiceResult<bool> { self.begin_dispatch_send(claim, now).await }
    async fn finish_node_dispatch(&self, claim: &StateMachineDispatchClaim, result: StateMachineDispatchResult, now: u64) -> ServiceResult<bool> { self.finish_dispatch(claim, result, now).await }
    async fn expire_node_dispatch(&self, command: FailStateMachineNodeAttempt) -> ServiceResult<bool> { self.expire_dispatch(command).await }
    async fn release_node_dispatch(&self, claim: &StateMachineDispatchClaim) -> ServiceResult<bool> { self.release_dispatch(claim).await }
    async fn supersede_inactive_node_dispatches(&self, run: &str) -> ServiceResult<()> { self.supersede_dispatches(run).await }

    async fn save_run_opening(&self, payload: bcs_service_api::StateMachineOpeningPayload) -> ServiceResult<bool> {
        self.save_opening(payload).await
    }
    async fn get_run_opening(&self, run: &str) -> ServiceResult<Option<bcs_service_api::StateMachineOpeningCheckpoint>> {
        self.get_opening(run).await
    }
    async fn mark_run_opening_delivered(&self, run: &str, at: u64) -> ServiceResult<bool> {
        self.deliver_opening(run, at).await
    }

    async fn begin_node_judging(&self, run: &str, node: &str, attempt: i32, artifact: String, responder: Option<String>) -> ServiceResult<bool> {
        self.begin_judging(run, node, attempt, artifact, responder).await
    }
    async fn claim_node_judging(&self, run: &str, node: &str, attempt: i32, owner: String, now: u64, until: u64) -> ServiceResult<Option<bcs_service_api::StateMachineJudgeClaim>> {
        self.claim_judging(run, node, attempt, owner, now, until).await
    }
    async fn release_node_judging(&self, claim: &bcs_service_api::StateMachineJudgeClaim) -> ServiceResult<bool> {
        self.release_judging(claim).await
    }

    async fn commit_eventful_transition(
        &self,
        transition: StateMachineEventfulTransition,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let mut candidate = inner.clone();
        if !apply_memory_state_machine_transition(&mut candidate, &transition)? {
            return Ok(false);
        }
        let events = state_machine_transition_events(&transition);
        if events.is_empty() {
            *inner = candidate;
            return Ok(true);
        }
        let event_store = self.event_store.as_ref().ok_or_else(|| ServiceError::InvalidOperation {
            message: "Eventful Memory state-machine transition requires the shared Memory Event Store".into(), request_id: None,
        })?;
        event_store
            .commit_business_mutations(&events, || {
                *inner = candidate;
                Ok(())
            })
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        Ok(true)
    }

    async fn create_run(
        &self,
        run: StateMachineRun,
        nodes: Vec<StateMachineNodeRun>,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        let run_id = run.run_id.clone();
        inner.runs.insert(run_id.clone(), run);
        for node in nodes {
            inner
                .nodes
                .insert((run_id.clone(), node.node_id.clone()), node);
        }
        Ok(())
    }

    async fn create_run_if_session_idle(
        &self,
        run: StateMachineRun,
        nodes: Vec<StateMachineNodeRun>,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if inner.runs.values().any(|existing| {
            existing.session_id == run.session_id
                && matches!(
                    existing.status,
                    StateMachineRunStatus::Pending | StateMachineRunStatus::Running
                )
        }) {
            return Ok(false);
        }
        let run_id = run.run_id.clone();
        inner.runs.insert(run_id.clone(), run);
        for node in nodes {
            inner
                .nodes
                .insert((run_id.clone(), node.node_id.clone()), node);
        }
        Ok(true)
    }

    async fn create_rerun_if_session_idle(
        &self,
        command: CreateStateMachineRerun,
    ) -> ServiceResult<CreateStateMachineRerunOutcome> {
        let mut inner = self.inner.write().await;
        if let Some(existing) = inner
            .runs
            .values()
            .find(|run| run.rerun_of.as_deref() == Some(command.source_run_id.as_str()))
            .cloned()
        {
            return Ok(CreateStateMachineRerunOutcome::Existing(existing));
        }
        let Some(source) = inner.runs.get(&command.source_run_id) else {
            return Ok(CreateStateMachineRerunOutcome::Conflict);
        };
        let opening_message_override = source.opening_message_override.clone();
        if source.status != StateMachineRunStatus::Failed
            || source.session_id != command.run.session_id
            || inner.runs.values().any(|run| {
                run.session_id == command.run.session_id
                    && matches!(
                        run.status,
                        StateMachineRunStatus::Pending | StateMachineRunStatus::Running
                    )
            })
        {
            return Ok(CreateStateMachineRerunOutcome::Conflict);
        }
        let Some(snapshot) = inner
            .run_snapshots
            .get(&command.source_run_id)
            .cloned()
        else {
            return Err(ServiceError::InvalidOperation {
                message: "source state-machine Run has no immutable snapshot".to_string(),
                request_id: None,
            });
        };
        let resolved_bindings = inner
            .run_resolved_participant_bindings
            .get(&command.source_run_id)
            .cloned();
        if command.reactivate_service_session {
            let session_repo = self.session_repo.as_ref().ok_or_else(|| {
                ServiceError::InvalidOperation {
                    message: "Memory state-machine rerun requires a Session Repo".to_string(),
                    request_id: None,
                }
            })?;
            let Some(expected_activation_count) = command
                .run
                .session_activation_count
                .and_then(|activation_count| activation_count.checked_sub(1))
            else {
                return Ok(CreateStateMachineRerunOutcome::Conflict);
            };
            let Some(session) = session_repo
                .reactivate_if_completed_activation(
                    &command.run.session_id,
                    expected_activation_count,
                    Some(command.run.input.clone()),
                )
                .await?
            else {
                return Ok(CreateStateMachineRerunOutcome::Conflict);
            };
            debug_assert_eq!(
                Some(session.activation_count),
                command.run.session_activation_count
            );
        }
        let run_id = command.run.run_id.clone();
        let mut run = command.run;
        run.opening_message_override = opening_message_override;
        inner.run_snapshots.insert(run_id.clone(), snapshot);
        if let Some(resolved_bindings) = resolved_bindings {
            inner
                .run_resolved_participant_bindings
                .insert(run_id.clone(), resolved_bindings);
        }
        inner.runs.insert(run_id.clone(), run);
        for node in command.nodes {
            inner
                .nodes
                .insert((run_id.clone(), node.node_id.clone()), node);
        }
        Ok(CreateStateMachineRerunOutcome::Created)
    }

    async fn get_direct_rerun(
        &self,
        source_run_id: &str,
    ) -> ServiceResult<Option<StateMachineRun>> {
        let inner = self.inner.read().await;
        Ok(inner
            .runs
            .values()
            .find(|run| run.rerun_of.as_deref() == Some(source_run_id))
            .cloned())
    }

    async fn get_run(&self, run_id: &str) -> ServiceResult<Option<StateMachineRun>> {
        let inner = self.inner.read().await;
        Ok(inner.runs.get(run_id).cloned())
    }

    async fn list_running_runs(&self, after_run_id: Option<&str>, limit: usize) -> ServiceResult<Vec<StateMachineRun>> {
        let inner = self.inner.read().await;
        Ok(inner.runs.range::<str, _>((
            after_run_id.map_or(std::ops::Bound::Unbounded, std::ops::Bound::Excluded),
            std::ops::Bound::Unbounded,
        )).filter(|(_, run)| run.status == StateMachineRunStatus::Running)
            .take(limit).map(|(_, run)| run.clone()).collect())
    }

    async fn list_pending_runs(&self, after_run_id: Option<&str>, limit: usize) -> ServiceResult<Vec<StateMachineRun>> {
        let inner = self.inner.read().await;
        Ok(inner.runs.range::<str, _>((
            after_run_id.map_or(std::ops::Bound::Unbounded, std::ops::Bound::Excluded),
            std::ops::Bound::Unbounded,
        )).filter(|(_, run)| run.status == StateMachineRunStatus::Pending)
            .take(limit).map(|(_, run)| run.clone()).collect())
    }

    async fn get_run_by_session_id(
        &self,
        session_id: &str,
    ) -> ServiceResult<Option<StateMachineRun>> {
        let inner = self.inner.read().await;
        Ok(inner
            .runs
            .values()
            .filter(|run| run.session_id == session_id)
            .max_by_key(|run| run.created_at)
            .cloned())
    }

    async fn list_runs_by_session_id(
        &self,
        session_id: &str,
    ) -> ServiceResult<Vec<StateMachineRun>> {
        let inner = self.inner.read().await;
        let mut runs = inner
            .runs
            .values()
            .filter(|run| run.session_id == session_id)
            .cloned()
            .collect::<Vec<_>>();
        runs.sort_by(|left, right| {
            right
                .created_at
                .cmp(&left.created_at)
                .then_with(|| right.run_id.cmp(&left.run_id))
        });
        Ok(runs)
    }

    async fn list_node_runs(&self, run_id: &str) -> ServiceResult<Vec<StateMachineNodeRun>> {
        let inner = self.inner.read().await;
        let mut nodes = inner
            .nodes
            .iter()
            .filter_map(|((node_run_id, _), node)| {
                if node_run_id == run_id {
                    Some(node.clone())
                } else {
                    None
                }
            })
            .collect::<Vec<_>>();
        nodes.sort_by(|left, right| left.node_id.cmp(&right.node_id));
        Ok(nodes)
    }

    async fn get_node_run(
        &self,
        run_id: &str,
        node_id: &str,
    ) -> ServiceResult<Option<StateMachineNodeRun>> {
        let inner = self.inner.read().await;
        Ok(inner
            .nodes
            .get(&(run_id.to_string(), node_id.to_string()))
            .cloned())
    }

    async fn mark_node_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        let node = node_mut(&mut inner, run_id, node_id)?;
        node.status = StateMachineNodeStatus::Running;
        node.attempt = attempt;
        node.delivery_request_id = Some(delivery_request_id);
        node.outcome = None;
        node.responded_by = None;
        node.started_at = Some(started_at);
        node.artifact_text = None;
        node.error = None;
        node.completed_at = None;
        node.timeout_deadline_ms = node
            .node_timeout_ms
            .map(|timeout_ms| started_at.saturating_add(timeout_ms));
        inner.node_failure_actions.remove(&(run_id.to_string(), node_id.to_string()));
        Ok(())
    }

    async fn mark_node_running_if_run_active(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if !matches!(
            node.status,
            StateMachineNodeStatus::Pending
                | StateMachineNodeStatus::Ready
                | StateMachineNodeStatus::RetryScheduled
        ) || node.attempt != attempt
        {
            return Ok(false);
        }
        node.status = StateMachineNodeStatus::Running;
        node.delivery_request_id = Some(delivery_request_id);
        node.outcome = None;
        node.responded_by = None;
        node.started_at = Some(started_at);
        node.artifact_text = None;
        node.error = None;
        node.completed_at = None;
        node.timeout_deadline_ms = node
            .node_timeout_ms
            .map(|timeout_ms| started_at.saturating_add(timeout_ms));
        Ok(true)
    }

    async fn complete_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        outcome: String,
        artifact_text: String,
        responded_by: Option<String>,
        completed_at: u64,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        if judge::is_judging(&inner, run_id, node_id, attempt) { return Ok(false); }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if node.status != StateMachineNodeStatus::Running || node.attempt != attempt {
            return Ok(false);
        }
        node.status = StateMachineNodeStatus::Completed;
        node.outcome = Some(outcome);
        node.responded_by = responded_by;
        node.artifact_text = Some(artifact_text);
        node.error = None;
        node.completed_at = Some(completed_at);
        node.timeout_deadline_ms = None;
        Ok(true)
    }

    async fn mark_human_node_running_if_run_active(
        &self,
        command: MarkHumanNodeRunningCommand,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, &command.run_id)? {
            return Ok(false);
        }
        let node = node_mut(&mut inner, &command.run_id, &command.node_id)?;
        if !matches!(
            node.status,
            StateMachineNodeStatus::Pending | StateMachineNodeStatus::Ready
        ) || node.attempt != command.attempt
        {
            return Ok(false);
        }
        node.status = StateMachineNodeStatus::Running;
        node.assignee_bot_id = None;
        node.delivery_request_id = None;
        node.bot_delivery_run_id = None;
        node.outcome = None;
        node.responded_by = None;
        node.artifact_text = None;
        node.error = None;
        node.started_at = Some(command.started_at_ms);
        node.completed_at = None;
        node.timeout_deadline_ms = Some(command.timeout_deadline_ms);
        Ok(true)
    }

    async fn record_node_artifact_if_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        if judge::is_judging(&inner, run_id, node_id, attempt) { return Ok(false); }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if node.status != StateMachineNodeStatus::Running || node.attempt != attempt {
            return Ok(false);
        }
        node.artifact_text = Some(artifact_text);
        node.error = None;
        Ok(true)
    }

    async fn record_human_response_if_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
        responded_by: String,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        if judge::is_judging(&inner, run_id, node_id, attempt) { return Ok(false); }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if node.status != StateMachineNodeStatus::Running || node.attempt != attempt {
            return Ok(false);
        }
        if node.artifact_text.is_some() {
            return Ok(
                node.artifact_text.as_deref() == Some(artifact_text.as_str())
                    && node.responded_by.as_deref() == Some(responded_by.as_str()),
            );
        }
        node.artifact_text = Some(artifact_text);
        node.responded_by = Some(responded_by);
        node.error = None;
        Ok(true)
    }

    async fn fail_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        error: String,
        completed_at: u64,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if node.status != StateMachineNodeStatus::Running || node.attempt != attempt {
            return Ok(false);
        }
        node.status = StateMachineNodeStatus::Failed;
        node.error = Some(error);
        node.completed_at = Some(completed_at);
        node.timeout_deadline_ms = None;
        inner.node_failure_actions.remove(&(run_id.to_string(), node_id.to_string()));
        Ok(true)
    }

    async fn fail_node_attempt_with_action(&self, command: FailStateMachineNodeAttempt) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, &command.run_id)? { return Ok(false); }
        let node = node_mut(&mut inner, &command.run_id, &command.node_id)?;
        if node.status != StateMachineNodeStatus::Running || node.attempt != command.attempt { return Ok(false); }
        if command.action == StateMachineFailureAction::Retry
            && !retry_is_within_limit(node.attempt, node.max_attempts)
        {
            return Ok(false);
        }
        node.status = StateMachineNodeStatus::Failed;
        node.error = Some(command.error);
        node.completed_at = Some(command.completed_at_ms);
        node.timeout_deadline_ms = None;
        inner.node_failure_actions.insert((command.run_id, command.node_id), command.action);
        Ok(true)
    }

    async fn get_node_attempt_failure(&self, run_id: &str, node_id: &str, attempt: i32) -> ServiceResult<Option<StateMachineNodeAttemptFailure>> {
        let inner = self.inner.read().await;
        let key = (run_id.to_string(), node_id.to_string());
        Ok(inner.nodes.get(&key).filter(|node| node.status == StateMachineNodeStatus::Failed && node.attempt == attempt)
            .map(|node| StateMachineNodeAttemptFailure { node: node.clone(), action: inner.node_failure_actions.get(&key).copied() }))
    }

    async fn schedule_node_retry(
        &self,
        run_id: &str,
        node_id: &str,
        failed_attempt: i32,
        next_attempt: i32,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        let key = (run_id.to_string(), node_id.to_string());
        if inner.node_failure_actions.get(&key) == Some(&StateMachineFailureAction::FailRun) { return Ok(false); }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if node.status != StateMachineNodeStatus::Failed || node.attempt != failed_attempt {
            return Ok(false);
        }
        node.status = StateMachineNodeStatus::RetryScheduled;
        node.attempt = next_attempt;
        node.delivery_request_id = None;
        node.bot_delivery_run_id = None;
        node.outcome = None;
        node.responded_by = None;
        node.artifact_text = None;
        node.error = None;
        node.started_at = None;
        node.completed_at = None;
        node.timeout_deadline_ms = None;
        inner.node_failure_actions.remove(&key);
        Ok(true)
    }

    async fn skip_node(&self, run_id: &str, node_id: &str, skipped_at: u64) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        if !run_is_running(&inner, run_id)? {
            return Ok(false);
        }
        let node = node_mut(&mut inner, run_id, node_id)?;
        if !matches!(node.status, StateMachineNodeStatus::Pending | StateMachineNodeStatus::Ready | StateMachineNodeStatus::RetryScheduled) {
            return Ok(false);
        }
        node.status = StateMachineNodeStatus::Skipped;
        node.completed_at = Some(skipped_at);
        node.timeout_deadline_ms = None;
        Ok(true)
    }

    async fn update_run_status(
        &self,
        run_id: &str,
        status: StateMachineRunStatus,
        output: Option<String>,
        error: Option<String>,
        updated_at: u64,
        completed_at: Option<u64>,
    ) -> ServiceResult<bool> {
        let mut inner = self.inner.write().await;
        let run = inner.runs.get_mut(run_id).ok_or_else(|| {
            ServiceError::InternalError(format!("state machine run not found: {run_id}"))
        })?;
        if is_terminal(run.status) {
            return Ok(false);
        }
        run.status = status;
        run.output = output;
        run.error = error;
        run.updated_at = updated_at;
        run.completed_at = completed_at;
        Ok(true)
    }

    async fn upsert_delivery_correlation(
        &self,
        correlation: StateMachineDeliveryCorrelation,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        inner
            .correlations
            .insert(correlation.delivery_request_id.clone(), correlation);
        Ok(())
    }

    async fn register_delivery_alias(
        &self,
        delivery_request_id: &str,
        bot_delivery_run_id: String,
    ) -> ServiceResult<()> {
        let mut inner = self.inner.write().await;
        let mut correlation = inner
            .correlations
            .get(delivery_request_id)
            .cloned()
            .ok_or_else(|| {
                ServiceError::InternalError(format!(
                    "delivery correlation not found: {delivery_request_id}"
                ))
            })?;
        correlation.bot_delivery_run_id = Some(bot_delivery_run_id.clone());
        inner
            .correlations
            .insert(delivery_request_id.to_string(), correlation.clone());
        inner
            .correlation_aliases
            .insert(bot_delivery_run_id, delivery_request_id.to_string());
        Ok(())
    }

    async fn lookup_delivery_correlation(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<StateMachineDeliveryCorrelation>> {
        let inner = self.inner.read().await;
        if let Some(correlation) = inner.correlations.get(run_id).cloned() {
            return Ok(Some(correlation));
        }
        let Some(primary_key) = inner.correlation_aliases.get(run_id) else {
            return Ok(None);
        };
        Ok(inner.correlations.get(primary_key).cloned())
    }

    async fn list_expired_running_node_runs(
        &self,
        now_ms: u64,
        timeout_grace_ms: u64,
        limit: usize,
    ) -> ServiceResult<Vec<StateMachineNodeRun>> {
        let inner = self.inner.read().await;
        let mut nodes = inner
            .nodes
            .values()
            .filter(|node| {
                node.status == StateMachineNodeStatus::Running
                    && node
                        .timeout_deadline_ms
                        .is_some_and(|deadline| deadline.saturating_add(timeout_grace_ms) <= now_ms)
                    && inner
                        .runs
                        .get(&node.run_id)
                        .is_some_and(|run| run.status == StateMachineRunStatus::Running)
            })
            .cloned()
            .collect::<Vec<_>>();
        nodes.sort_by(|left, right| {
            left.timeout_deadline_ms
                .cmp(&right.timeout_deadline_ms)
                .then_with(|| left.run_id.cmp(&right.run_id))
                .then_with(|| left.node_id.cmp(&right.node_id))
        });
        nodes.truncate(limit);
        Ok(nodes)
    }
}
