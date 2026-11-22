use super::*;
fn collect_reachable_targets(
    state_machine: &bcs_domain::StateMachineDefinition,
    initial_targets: &[String],
    reachable: &mut HashSet<String>,
) {
    let mut stack = initial_targets.to_vec();
    while let Some(node_id) = stack.pop() {
        if !reachable.insert(node_id.clone()) {
            continue;
        }
        let Some(node) = state_machine.nodes.get(&node_id) else {
            continue;
        };
        for transition in node.transitions.values() {
            for target in &transition.targets {
                stack.push(target.clone());
            }
        }
    }
}impl CollaborationRuntime {

    pub(super) async fn dispatch_ready_targets(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        completed_node_id: &str,
        outcome: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => return Ok(()),
        };
        let targets = state_machine
            .nodes
            .get(completed_node_id)
            .and_then(|node| node.transitions.get(outcome))
            .map(|transition| transition.targets.clone())
            .unwrap_or_default();
        log_state_machine_transition(
            run,
            completed_node_id,
            outcome,
            targets.as_slice(),
            MessageLogStatus::Routed,
        );
        for target in targets {
            if self
                .all_upstreams_completed(compiled, &run.run_id, &target)
                .await?
            {
                let Some(target_run) = self.runs.get_node_run(&run.run_id, &target).await? else {
                    continue;
                };
                if matches!(
                    target_run.status,
                    StateMachineNodeStatus::Pending
                        | StateMachineNodeStatus::Ready
                        | StateMachineNodeStatus::RetryScheduled
                ) {
                    self.dispatch_node(compiled, group, run, &target).await?;
                }
            }
        }
        Ok(())
    }

    pub(super) async fn skip_unselected_targets(
        &self,
        compiled: &CompiledStateMachine,
        run: &StateMachineRun,
        completed_node_id: &str,
        selected_outcome: &str,
        skipped_at: u64,
    ) -> Result<(), CollaborationRuntimeError> {
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => return Ok(()),
        };
        let Some(node) = state_machine.nodes.get(completed_node_id) else {
            return Ok(());
        };
        let selected_targets = node
            .transitions
            .get(selected_outcome)
            .map(|transition| transition.targets.as_slice())
            .unwrap_or(&[]);
        let mut protected_targets = HashSet::new();
        collect_reachable_targets(state_machine, selected_targets, &mut protected_targets);
        let unselected_targets = node.transitions.iter()
            .filter(|(outcome, _)| outcome.as_str() != selected_outcome)
            .flat_map(|(_, transition)| transition.targets.iter().cloned()).collect::<Vec<_>>();
        self.skip_branch_targets(state_machine, run, &unselected_targets, skipped_at, &protected_targets).await
    }

    pub(super) async fn skip_branch_targets(
        &self,
        state_machine: &bcs_domain::StateMachineDefinition,
        run: &StateMachineRun,
        initial_targets: &[String],
        skipped_at: u64,
        protected_targets: &HashSet<String>,
    ) -> Result<(), CollaborationRuntimeError> {
        use bcs_service_api::port::repo::collaboration::STATE_MACHINE_NODE_BATCH_LIMIT;
        let mut reachable = HashSet::new();
        collect_reachable_targets(state_machine, initial_targets, &mut reachable);
        let mut candidates: Vec<_> = reachable.difference(protected_targets).cloned().collect();
        candidates.sort();
        // A raced ancestor invalidates its entire write batch. Refresh scalar
        // statuses and recalculate traversal, with bounded contention retries.
        for _ in 0..3 {
            let mut statuses = BTreeMap::new();
            for ids in candidates.chunks(STATE_MACHINE_NODE_BATCH_LIMIT) {
                statuses.extend(self.runs.get_node_statuses(&run.run_id, ids).await?);
            }
            let mut visited = HashSet::new();
            let mut stack = initial_targets.to_vec();
            let mut pending = Vec::new();
            while let Some(node_id) = stack.pop() {
                if !visited.insert(node_id.clone()) || protected_targets.contains(&node_id) { continue; }
                match statuses.get(&node_id) {
                    Some(StateMachineNodeStatus::Skipped) => {},
                    Some(status) if crate::progression::is_unstarted(*status) => pending.push(node_id.clone()),
                    _ => continue, // missing, Running or terminal nodes fence their descendants
                }
                if let Some(node) = state_machine.nodes.get(&node_id) {
                    stack.extend(node.transitions.values().flat_map(|transition| transition.targets.iter().cloned()));
                }
            }
            let mut raced = false;
            // DFS pre-order places each traversed ancestor before descendants.
            // Earlier batches are permanently Skipped; within a batch, CAS is
            // all-or-nothing so a dispatch race cannot skip past a Running node.
            for ids in pending.chunks(STATE_MACHINE_NODE_BATCH_LIMIT) {
                if !self.runs.skip_nodes(&run.run_id, ids, skipped_at).await? {
                    raced = true;
                    break;
                }
                for node_id in ids {
                    log_state_machine_transition(run, node_id, "skipped", &[], MessageLogStatus::Skipped);
                }
            }
            if !raced { return Ok(()); }
            if !self.runs.get_run(&run.run_id).await?.is_some_and(|run| run.status == StateMachineRunStatus::Running) {
                return Ok(());
            }
        }
        Err(ServiceError::Conflict("branch skip raced with node progression; retry recovery".into()).into())
    }

    pub(super) async fn all_upstreams_completed(
        &self,
        compiled: &CompiledStateMachine,
        run_id: &str,
        node_id: &str,
    ) -> Result<bool, CollaborationRuntimeError> {
        let upstreams = compiled
            .upstreams
            .get(node_id)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        for upstream_id in upstreams {
            let Some(node_run) = self.runs.get_node_run(run_id, upstream_id).await? else {
                return Ok(false);
            };
            if !matches!(
                node_run.status,
                StateMachineNodeStatus::Completed | StateMachineNodeStatus::Skipped
            ) {
                return Ok(false);
            }
        }
        Ok(true)
    }

    pub(super) async fn fail_retryable_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        error: String,
        completed_at_ms: u64,
    ) -> Result<bool, CollaborationRuntimeError> {
        let node = self.runs.get_node_run(run_id, node_id).await?.ok_or_else(|| {
            CollaborationRuntimeError::NodeNotFound { run_id: run_id.into(), node_id: node_id.into() }
        })?;
        if node.status != StateMachineNodeStatus::Running || node.attempt != attempt { return Ok(false); }
        let retry = attempt >= 0 && attempt.checked_add(1).is_some_and(|next| next < node.max_attempts.max(1));
        self.runs.fail_node_attempt_with_action(FailStateMachineNodeAttempt {
            run_id: run_id.into(), node_id: node_id.into(), attempt, error, completed_at_ms,
            action: if retry { StateMachineFailureAction::Retry } else { StateMachineFailureAction::FailRun },
        }).await.map_err(Into::into)
    }

    pub(super) async fn resume_failed_node(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        node_id: &str,
        attempt: i32,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let Some(failure) = self.runs.get_node_attempt_failure(&run.run_id, node_id, attempt).await? else {
            if self.runs.get_node_run(&run.run_id, node_id).await?.is_none() {
                return Err(CollaborationRuntimeError::NodeNotFound { run_id: run.run_id.clone(), node_id: node_id.into() });
            }
            // Another worker already retried/completed this attempt.
            return self.run_view(&run.run_id).await;
        };
        let action = failure.action.ok_or_else(|| CollaborationRuntimeError::InvalidRequest(
            "failed attempt has no saved failure action; legacy failures cannot be recovered".into(),
        ))?;
        let node = failure.node;
        self.preserve_node_history(compiled, group, run, &node).await?;
        let error = node.error.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest("failed attempt has no saved error".into())
        })?;
        if node.completed_at.is_none() {
            return Err(CollaborationRuntimeError::InvalidRequest("failed attempt has no completion timestamp".into()));
        }
        let max_attempts = node.max_attempts.max(1);
        if action == StateMachineFailureAction::Retry {
            let next_attempt = attempt.checked_add(1).filter(|next| attempt >= 0 && *next < max_attempts)
                .ok_or_else(|| CollaborationRuntimeError::InvalidRequest("saved retry exceeds attempt limit".into()))?;
            let retry_at = bcs_protocol::now_ms();
            let event = self.prepare_node_event(&compiled,
                "state_machine.node.retry_scheduled",
                run,
                "state_machine.node",
                node_id,
                &format!("{node_id}:{attempt}:retry:{next_attempt}"),
                None,
                None,
                retry_at,
                BTreeMap::from([
                    ("run_id".to_string(), serde_json::json!(run.run_id.clone())),
                    ("node_id".to_string(), serde_json::json!(node_id)),
                    ("attempt".to_string(), serde_json::json!(attempt)),
                    ("next_attempt".to_string(), serde_json::json!(next_attempt)),
                    ("max_attempts".to_string(), serde_json::json!(max_attempts)),
                    (
                        "retry_at".to_string(),
                        serde_json::json!(event_timestamp(retry_at)?),
                    ),
                    ("reason".to_string(), serde_json::json!(error.clone())),
                ]),
            )?;
            let scheduled = match event {
                Some(event) => {
                    self.runs
                        .commit_eventful_transition(
                            StateMachineEventfulTransition::ScheduleNodeRetry {
                                run_id: run.run_id.clone(),
                                node_id: node_id.to_string(),
                                failed_attempt: attempt,
                                next_attempt,
                                event,
                            },
                        )
                        .await?
                }
                None => {
                    self.runs
                        .schedule_node_retry(&run.run_id, node_id, attempt, next_attempt)
                        .await?
                }
            };
            if scheduled {
                self.observe_loop_node(compiled, run, node_id, next_attempt, "retry_scheduled", None);
                self.events
                    .append_event(
                        &run.run_id,
                        Some(node_id),
                        Some(next_attempt),
                        "state_machine.node.retry_scheduled",
                        serde_json::json!({
                            "run_id": run.run_id.clone(),
                            "node_id": node_id,
                            "failed_attempt": attempt,
                            "next_attempt": next_attempt,
                            "max_attempts": max_attempts,
                            "reason": error,
                        }),
                        retry_at,
                    )
                    .await?;
                info!(target: RUNTIME_LOG_TARGET,
                    run_id = %run.run_id,
                    group_id = %run.group_id,
                    session_id = %run.session_id,
                    node_id = %node_id,
                    attempt = attempt,
                    next_attempt = next_attempt,
                    "state_machine: node retry scheduled"
                );
                self.dispatch_node(compiled, group, run, node_id).await?;
            }
            return self.run_view(&run.run_id).await;
        }
        self.fail_run(run, error).await
    }

    pub(super) async fn apply_completed_node_progression(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        node_id: &str,
        outcome: &str,
        completed_at_ms: u64,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        if compiled.execution_plan.is_some() {
            let node = self.runs.get_node_run(&run.run_id, node_id).await?.ok_or_else(||
                CollaborationRuntimeError::NodeNotFound { run_id: run.run_id.clone(), node_id: node_id.into() })?;
            self.persist_node_output(compiled, group, run, &node).await?;
        }
        self.skip_unselected_targets(compiled, run, node_id, outcome, completed_at_ms)
            .await?;
        self.dispatch_ready_targets(compiled, group, run, node_id, outcome)
            .await?;
        self.complete_run_if_done(compiled, run).await?;
        self.run_view(&run.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(run.run_id.clone()))
    }

    pub(super) async fn progression_run_is_active(&self, run_id: &str) -> Result<bool, CollaborationRuntimeError> {
        Ok(self.runs.get_run(run_id).await?.is_some_and(|run| run.status == StateMachineRunStatus::Running))
    }
}
