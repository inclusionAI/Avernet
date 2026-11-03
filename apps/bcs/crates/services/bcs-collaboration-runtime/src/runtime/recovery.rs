use super::*;
impl CollaborationRuntime {

    pub(super) async fn recover_run_progression(&self, run_id: &str) -> Result<(), CollaborationRuntimeError> {
        let Some(mut run) = self.runs.get_run(run_id).await? else { return Ok(()); };
        if !matches!(run.status, StateMachineRunStatus::Pending | StateMachineRunStatus::Running) { return Ok(()); }
        // Recovery requires the original snapshot even for v1. The legacy
        // foreground fallback to today's Definition is not recovery evidence.
        let Some(snapshot) = self.definitions.get_run_snapshot(run_id).await? else {
            if self.converge_missing_startup(&run, bcs_service_api::StateMachineMissingStartupFact::Snapshot, None).await? { return Ok(()); }
            return Err(CollaborationRuntimeError::InvalidRequest("progression recovery requires an immutable snapshot; preparation grace has not expired or state changed".into()));
        };
        let loaded = crate::snapshot::load_state_machine_snapshot(snapshot)?;
        if loaded.plan.is_some() && !self.loop_execution_enabled {
            return Err(CollaborationRuntimeError::InvalidRequest("v2 progression recovery is disabled".into()));
        }
        let compiled = loaded.execution;
        let group = self.groups.get(&run.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest("progression recovery group is unavailable".into())
        })?;
        let session = self.sessions.get(&run.session_id).await
            .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?
            .ok_or_else(|| CollaborationRuntimeError::InvalidRequest("progression recovery Session is unavailable".into()))?;
        if session.group_id != run.group_id || session.status != SessionStatus::Running
            || (session.session_kind == SessionKind::ServiceInvocation
                && run.session_activation_count != Some(session.activation_count))
        {
            return Err(CollaborationRuntimeError::InvalidRequest("progression recovery Session activation is not active".into()));
        }
        let mut nodes = self.runs.list_node_runs(run_id).await?;
        // Validate the entire saved graph before repairing any failure.
        crate::progression::derive(&compiled, run_id, &nodes)?;
        if run.status == StateMachineRunStatus::Pending || nodes.iter().any(|node| compiled.initial_nodes.contains(&node.node_id)
            && crate::progression::is_unstarted(node.status)
            && !(node.status == StateMachineNodeStatus::RetryScheduled && node.attempt > 0))
        {
            if self.runs.get_run_opening(run_id).await?.is_none() {
                if self.converge_missing_startup(&run, bcs_service_api::StateMachineMissingStartupFact::Opening, None).await? { return Ok(()); }
                return Err(CollaborationRuntimeError::InvalidRequest("startup recovery requires a persisted opening checkpoint; preparation grace has not expired or state changed".into()));
            }
            self.ensure_materialized_run_started(&mut run).await?;
            if !self.progression_run_is_active(run_id).await? { return Ok(()); }
            self.persist_saved_opening(&run).await?;
            for node_id in &compiled.initial_nodes {
                if !self.progression_run_is_active(run_id).await? { return Ok(()); }
                if self.runs.get_node_run(run_id, node_id).await?.is_some_and(|node| crate::progression::is_unstarted(node.status)) {
                    self.dispatch_node(&compiled, &group, &run, node_id).await?;
                }
            }
            nodes = self.runs.list_node_runs(run_id).await?;
        }
        if self.history_persistence_enabled { self.repair_existing_history(&run).await?; }
        self.runs.supersede_inactive_node_dispatches(run_id).await?;
        let covered_human_nodes = if let Some(outbound) = self.session_channel_outbound.as_ref() {
            if !self.progression_run_is_active(run_id).await? { return Ok(()); }
            outbound.recover_human_input_requests(run_id, &run.session_id).await?
        } else { Vec::new() };
        for node in &nodes {
            if node.status == StateMachineNodeStatus::Running && node.assignee_bot_id.is_some() && node.artifact_text.is_none() {
                if !self.progression_run_is_active(run_id).await? { return Ok(()); }
                self.resume_node_dispatch(&compiled, &group, &run, &node.node_id, node.attempt).await?;
            }
        }
        // Running human nodes do not pass through dispatch again after a crash.
        // Channel reuses the saved text/target/deadline and its send barrier.
        for node in &nodes {
            if node.status == StateMachineNodeStatus::Running && node.assignee_bot_id.is_none() && node.artifact_text.is_none()
                && !covered_human_nodes.contains(&node.node_id) {
                self.preserve_node_history(&compiled, &group, &run, node).await?;
                self.resume_human_ready(&compiled, &run, &node.node_id).await?;
            }
        }
        if !self.progression_run_is_active(run_id).await? { return Ok(()); }
        nodes = self.runs.list_node_runs(run_id).await?;
        for node in nodes.iter().filter(|node| node.status == StateMachineNodeStatus::Running
            && node.artifact_text.is_some() && !node_uses_judge(&compiled, &node.node_id)) {
            self.resume_accepted_history_output(&compiled, &group, &run, node).await?;
        }
        nodes = self.runs.list_node_runs(run_id).await?;
        let judging = nodes.iter().filter(|node| node.status == StateMachineNodeStatus::Running
            && node.artifact_text.is_some() && node_uses_judge(&compiled, &node.node_id))
            .map(|node| (node.node_id.clone(), node.attempt)).collect::<Vec<_>>();
        if !judging.is_empty() {
            for (node_id, attempt) in judging {
                if !self.progression_run_is_active(run_id).await? { return Ok(()); }
                self.resume_judging(&compiled, &group, &run, &node_id, attempt).await?;
            }
            if !self.progression_run_is_active(run_id).await? { return Ok(()); }
            nodes = self.runs.list_node_runs(run_id).await?;
        }
        let failed = nodes.iter().filter(|node| node.status == StateMachineNodeStatus::Failed)
            .map(|node| (node.node_id.clone(), node.attempt)).collect::<Vec<_>>();
        if !failed.is_empty() {
            for (node_id, attempt) in failed {
                if !self.progression_run_is_active(run_id).await? { return Ok(()); }
                self.resume_failed_node(&compiled, &group, &run, &node_id, attempt).await?;
            }
            if !self.progression_run_is_active(run_id).await? { return Ok(()); }
            nodes = self.runs.list_node_runs(run_id).await?;
        }
        for node in nodes.iter().filter(|node| node.status == StateMachineNodeStatus::Completed) {
            self.persist_node_output(&compiled, &group, &run, node).await?;
        }
        let progression = crate::progression::derive(&compiled, run_id, &nodes)?;
        for node_ids in progression.skip.chunks(bcs_service_api::port::repo::collaboration::STATE_MACHINE_NODE_BATCH_LIMIT) {
            if !self.progression_run_is_active(run_id).await? { return Ok(()); }
            if !self.runs.skip_nodes(run_id, node_ids, bcs_protocol::now_ms()).await? {
                return Err(ServiceError::Conflict("recovery skip raced with node progression; retry next scan".into()).into());
            }
        }
        // Reload after bounded atomic skip commits so joins can observe the
        // completed barrier in this sweep, including after a partial skip.
        let progression = if progression.skip.is_empty() {
            progression
        } else {
            crate::progression::derive(&compiled, run_id, &self.runs.list_node_runs(run_id).await?)?
        };
        for node_id in &progression.ready {
            if !self.progression_run_is_active(run_id).await? { return Ok(()); }
            if self.all_upstreams_completed(&compiled, run_id, node_id).await? {
                let node = self.runs.get_node_run(run_id, node_id).await?;
                if node.is_some_and(|node| crate::progression::is_unstarted(node.status)) {
                    self.dispatch_node(&compiled, &group, &run, node_id).await?;
                }
            }
        }
        if progression.done && self.progression_run_is_active(run_id).await? {
            self.complete_run_if_done(&compiled, &run).await?;
        }
        Ok(())
    }

    pub(super) async fn process_expired_node_timeout_candidate(
        &self,
        node: &StateMachineNodeRun,
        now: u64,
        timeout_grace_ms: u64,
    ) -> Result<bool, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(&node.run_id).await? else {
            return Ok(false);
        };
        if run.status != StateMachineRunStatus::Running {
            return Ok(false);
        }
        let Some(group) = self.groups.get(&run.group_id).await else {
            self.cancel_state_machine_run(CancelStateMachineRunCommand {
                run_id: run.run_id.clone(),
                reason: Some("group_not_found".to_string()),
            })
            .await?;
            warn!(
                target: "state_machine_timeout_scanner",
                event = "scanner.run_aborted_missing_group",
                run_id = %run.run_id,
                group_id = %run.group_id,
                node_id = %node.node_id,
                "state-machine run aborted because its group no longer exists"
            );
            return Ok(true);
        };
        let session_exists = self
            .sessions
            .get(&run.session_id)
            .await
            .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?
            .is_some();
        if !session_exists {
            self.cancel_state_machine_run(CancelStateMachineRunCommand {
                run_id: run.run_id.clone(),
                reason: Some("session_not_found".to_string()),
            })
            .await?;
            warn!(
                target: "state_machine_timeout_scanner",
                event = "scanner.run_aborted_missing_session",
                run_id = %run.run_id,
                group_id = %run.group_id,
                session_id = %run.session_id,
                node_id = %node.node_id,
                "state-machine run aborted because its session no longer exists"
            );
            return Ok(true);
        }
        let compiled = self.load_run_compiled(&run).await?;
        let timeout_ms = node.node_timeout_ms.unwrap_or_default();
        let deadline_ms = node.timeout_deadline_ms;
        let error = format!(
            "state-machine node '{}' timed out after {}ms",
            node.node_id, timeout_ms
        );
        self.events
            .append_event(
                &run.run_id,
                Some(&node.node_id),
                Some(node.attempt),
                "state_machine.node.timeout",
                serde_json::json!({
                    "run_id": run.run_id.clone(),
                    "node_id": node.node_id.clone(),
                    "attempt": node.attempt,
                    "node_timeout_ms": timeout_ms,
                    "timeout_deadline_ms": deadline_ms,
                    "observed_at_ms": now,
                    "timeout_grace_ms": timeout_grace_ms,
                    "delivery_request_id": node.delivery_request_id.clone(),
                    "bot_delivery_run_id": node.bot_delivery_run_id.clone(),
                }),
                now,
            )
            .await?;
        let failed = self
            .fail_retryable_node_attempt(&run.run_id, &node.node_id, node.attempt, error.clone(), now)
            .await?;
        if !failed {
            return Ok(false);
        }
        log_state_machine_timeout(&run, node, timeout_ms, deadline_ms, timeout_grace_ms);
        self.resume_failed_node(
            &compiled,
            &group,
            &run,
            &node.node_id,
            node.attempt,
        )
        .await?;
        Ok(true)
    }

    pub(super) async fn process_expired_node_timeouts_impl(
        &self,
        limit: usize,
        timeout_grace_ms: u64,
    ) -> Result<usize, CollaborationRuntimeError> {
        if limit == 0 {
            return Ok(0);
        }
        let now = bcs_protocol::now_ms();
        let expired_nodes = self
            .runs
            .list_expired_running_node_runs(now, timeout_grace_ms, limit)
            .await?;
        let mut processed = 0usize;
        let mut skipped = 0usize;
        let mut first_skipped = None;
        for node in expired_nodes {
            match self
                .process_expired_node_timeout_candidate(&node, now, timeout_grace_ms)
                .await
            {
                Ok(true) => processed += 1,
                Ok(false) => {}
                Err(error) => {
                    skipped += 1;
                    if first_skipped.is_none() {
                        first_skipped = Some((node.run_id, node.node_id, error.to_string()));
                    }
                }
            }
        }
        if let Some((run_id, node_id, error)) = first_skipped {
            warn!(
                target: "state_machine_timeout_scanner",
                event = "scanner.candidates_skipped",
                skipped,
                first_run_id = %run_id,
                first_node_id = %node_id,
                first_error = %error,
                "state-machine timeout scanner skipped unprocessable candidates"
            );
            if processed == 0 {
                // An entirely failed batch is not an idle scan. Let the
                // scheduler back off, while partial progress stays available.
                return Err(CollaborationRuntimeError::Internal(ServiceError::InternalError(
                    format!("timeout recovery made no progress; {skipped} candidates failed: {error}"),
                )));
            }
        }
        Ok(processed)
    }

    pub(super) async fn recover_state_machine_progression_impl(
        &self,
        after_run_id: Option<String>,
        limit: usize,
    ) -> Result<bcs_service_api::StateMachineProgressionRecoveryPage, CollaborationRuntimeError> {
        if limit == 0 { return Ok(Default::default()); }
        let (running, pending) = tokio::try_join!(
            self.runs.list_running_runs(after_run_id.as_deref(), limit),
            self.runs.list_pending_runs(after_run_id.as_deref(), limit),
        )?;
        let mut candidates = running;
        candidates.extend(pending);
        candidates.sort_by(|left, right| left.run_id.cmp(&right.run_id));
        candidates.dedup_by(|left, right| left.run_id == right.run_id);
        candidates.truncate(limit);
        let next_run_id = if candidates.len() == limit { candidates.last().map(|run| run.run_id.clone()) } else { None };
        let mut page = bcs_service_api::StateMachineProgressionRecoveryPage {
            scanned: candidates.len(), next_run_id, ..Default::default()
        };
        for run in candidates {
            match self.recover_run_progression(&run.run_id).await {
                Ok(()) => page.reconciled += 1,
                Err(error) => page.failures.push(bcs_service_api::StateMachineProgressionRecoveryFailure {
                    run_id: run.run_id, error: error.to_string(),
                }),
            }
        }
        Ok(page)
    }

    pub(super) async fn cleanup_state_machine_terminal_work_impl(&self, after_run_id: Option<String>, limit: usize)
        -> Result<bcs_service_api::StateMachineProgressionRecoveryPage, CollaborationRuntimeError> {
        // Bound both candidate history and per-Run writes, including direct callers.
        let limit = limit.min(32);
        if limit == 0 { return Ok(Default::default()); }
        let ids = self.runs.list_terminal_runs_for_cleanup(after_run_id.as_deref(), limit).await?;
        let history_repairs = if self.history_persistence_enabled {
            self.runs.list_unrepaired_history_runs(&ids).await?
        } else { Vec::new() };
        let mut page = bcs_service_api::StateMachineProgressionRecoveryPage { scanned: ids.len(),
            next_run_id: if ids.len() == limit { ids.last().cloned() } else { None }, ..Default::default() };
        for run_id in ids {
            let result = async {
                self.runs.cleanup_terminal_run_checkpoints(&run_id, 32).await?;
                if history_repairs.contains(&run_id) {
                    if let Some(run) = self.runs.get_run(&run_id).await? {
                        self.repair_existing_history(&run).await?;
                        self.runs.confirm_terminal_history_repair(&run_id, bcs_protocol::now_ms()).await?;
                    }
                }
                Ok::<_, CollaborationRuntimeError>(())
            }.await;
            match result {
                Ok(()) => page.reconciled += 1,
                Err(error) => page.failures.push(bcs_service_api::StateMachineProgressionRecoveryFailure { run_id, error: error.to_string() }),
            }
        }
        Ok(page)
    }

    pub(super) async fn recover_state_machine_sessions_impl(
        &self,
        after_session_id: Option<String>,
        limit: usize,
    ) -> Result<bcs_service_api::StateMachineSessionRecoveryPage, CollaborationRuntimeError> {
        if limit == 0 { return Ok(Default::default()); }
        let candidates = self.sessions.list_running_service_after(after_session_id.as_deref(), limit as u64).await
            .map_err(|error| CollaborationRuntimeError::Internal(ServiceError::InternalError(error.to_string())))?;
        let next_session_id = if candidates.len() == limit { candidates.last().map(|session| session.id.clone()) } else { None };
        let mut page = bcs_service_api::StateMachineSessionRecoveryPage {
            scanned: candidates.len(), next_session_id, ..Default::default()
        };
        for session in candidates {
            match self.recover_terminal_run_session(&session).await {
                Ok(true) => page.completed += 1,
                Ok(false) => {},
                Err(error) => page.failures.push(bcs_service_api::StateMachineSessionRecoveryFailure {
                    session_id: session.id, error: error.to_string(),
                }),
            }
        }
        Ok(page)
    }
}
