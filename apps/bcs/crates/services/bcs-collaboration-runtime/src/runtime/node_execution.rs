use super::*;
pub(super) fn predecessor_node_ids(compiled: &CompiledStateMachine, node_id: &str) -> Vec<String> {
    let mut predecessor_node_ids = compiled.upstreams.get(node_id).cloned().unwrap_or_default();
    predecessor_node_ids.sort();
    predecessor_node_ids.dedup();
    predecessor_node_ids
}

pub(super) fn group_context_input(group: &Group, session_id: &str) -> GroupContextInput {
    GroupContextInput {
        session_id: session_id.to_string(),
        driver_bot: group.driver_bot.clone(),
        originator: group.originator().to_string(),
        participants: group
            .participants
            .iter()
            .map(|participant| GroupContextParticipant {
                id: participant.bot_uuid.clone(),
                name: participant.bot_name.clone(),
                role: Some(participant_role_slug(participant.role).to_string()),
                is_bot: participant.is_bot(),
            })
            .collect(),
        bcs_session_id: Some(session_id.to_string()),
    }
}

fn participant_role_slug(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
}

fn judge_service_error_message(error: &ServiceError) -> String {
    match error {
        ServiceError::InternalError(message) => message.clone(),
        _ => error.to_string(),
    }
}impl CollaborationRuntime {

    pub(super) async fn dispatch_node(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        node_id: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(
                    "runtime.kind must be state_machine".to_string(),
                ));
            }
        };
        let node_definition = state_machine.nodes.get(node_id).ok_or_else(|| {
            CollaborationRuntimeError::InvalidDefinition(format!("node not found: {node_id}"))
        })?;
        match node_definition.kind {
            StateMachineNodeKind::HumanInput => {
                return self
                    .activate_human_input(compiled, group, run, node_id)
                    .await;
            }
            StateMachineNodeKind::BotTask => {}
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(format!(
                    "node {node_id} kind is not supported"
                )));
            }
        }
        let node_run = self
            .runs
            .get_node_run(&run.run_id, node_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(run.run_id.clone()))?;
        let attempt = node_run.attempt;
        let prompt = self.build_node_prompt(compiled, run, node_id).await?;
        let assignee_bot_id = node_run
            .assignee_bot_id
            .clone()
            .filter(|bot_id| !bot_id.trim().is_empty())
            .ok_or_else(|| {
                CollaborationRuntimeError::InvalidDefinition(format!(
                    "bot task node {node_id} has no assignee_bot_id"
                ))
            })?;
        let delivery_request_id = format!("smnode-{}-{}-{}", run.run_id, node_id, attempt);
        let started_at = bcs_protocol::now_ms();
        let event = self.prepare_node_event(&compiled,
            "state_machine.node.started",
            run,
            "state_machine.node",
            node_id,
            &format!("{node_id}:{attempt}:started"),
            Some(&assignee_bot_id),
            Some(EventActorType::Bot),
            started_at,
            BTreeMap::from([
                ("run_id".to_string(), serde_json::json!(run.run_id.clone())),
                ("node_id".to_string(), serde_json::json!(node_id)),
                ("attempt".to_string(), serde_json::json!(attempt)),
                (
                    "predecessor_node_ids".to_string(),
                    serde_json::json!(predecessor_node_ids(compiled, node_id)),
                ),
                (
                    "assignee_id".to_string(),
                    serde_json::json!(assignee_bot_id.clone()),
                ),
                (
                    "started_at".to_string(),
                    serde_json::json!(event_timestamp(started_at)?),
                ),
            ]),
        )?;
        let marked = match event {
            Some(event) => {
                self.runs
                    .commit_eventful_transition(StateMachineEventfulTransition::StartBotNode {
                        run_id: run.run_id.clone(),
                        node_id: node_id.to_string(),
                        attempt,
                        delivery_request_id: delivery_request_id.clone(),
                        started_at_ms: started_at,
                        event,
                    })
                    .await?
            }
            None => {
                self.runs
                    .mark_node_running_if_run_active(
                        &run.run_id,
                        node_id,
                        attempt,
                        delivery_request_id.clone(),
                        started_at,
                    )
                    .await?
            }
        };
        if !marked {
            return Ok(());
        }
        self.observe_loop_node(compiled, run, node_id, attempt, "started", None);
        let payload = match self.prepare_node_dispatch(group, run, &node_run, prompt, started_at).await {
            Ok(payload) => payload,
            Err(error) => {
                self.fail_dispatched_node(compiled, group, run, node_id, attempt, error.to_string()).await?;
                return Err(error);
            }
        };
        if !self.runs.save_node_dispatch(payload).await? {
            if !self.progression_run_is_active(&run.run_id).await? { return Ok(()); }
            return Err(CollaborationRuntimeError::Conflict("Node no longer accepts a dispatch checkpoint".into()));
        }
        self.resume_node_dispatch(compiled, group, run, node_id, attempt).await
    }

    pub(super) async fn fail_dispatched_node(
        &self,
        _compiled: &CompiledStateMachine,
        _group: &Group,
        run: &StateMachineRun,
        node_id: &str,
        attempt: i32,
        error: String,
    ) -> Result<(), CollaborationRuntimeError> {
        let now = bcs_protocol::now_ms();
        if self.runs.fail_node_attempt_with_action(FailStateMachineNodeAttempt {
            run_id: run.run_id.clone(), node_id: node_id.to_string(), attempt,
            error: error.clone(), completed_at_ms: now, action: StateMachineFailureAction::FailRun,
        }).await? {
            self.fail_run(run, error).await?;
        }
        Ok(())
    }

    pub(super) async fn loop_context_for_node(
        &self,
        compiled: &CompiledStateMachine,
        run_id: &str,
        node_id: &str,
    ) -> Result<Option<bcs_domain::LoopContext>, CollaborationRuntimeError> {
        let Some(plan) = &compiled.execution_plan else { return Ok(None); };
        let meta = plan.node_metadata.get(node_id).ok_or_else(|| {
            CollaborationRuntimeError::Internal(ServiceError::InternalError("missing execution node mapping".into()))
        })?;
        let previous = match &meta.previous_result_node_id {
            Some(id) => self.runs.get_node_run(run_id, id).await?,
            None => None,
        };
        crate::loop_context::build_loop_context(plan, run_id, node_id, previous.as_ref())
    }

    pub(super) async fn build_node_prompt(
        &self,
        compiled: &CompiledStateMachine,
        run: &StateMachineRun,
        node_id: &str,
    ) -> Result<String, CollaborationRuntimeError> {
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(
                    "runtime.kind must be state_machine".to_string(),
                ));
            }
        };
        let node = state_machine.nodes.get(node_id).ok_or_else(|| {
            CollaborationRuntimeError::InvalidDefinition(format!("node not found: {node_id}"))
        })?;
        let upstream_ids = compiled
            .upstreams
            .get(node_id)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        let mut upstream_text = String::new();
        for upstream_id in upstream_ids {
            if let Some(upstream_run) = self.runs.get_node_run(&run.run_id, upstream_id).await? {
                if !crate::loop_context::projects_upstream_artifact(compiled.execution_plan.as_ref(), node_id, &upstream_run) {
                    continue;
                }
                if let Some(text) = upstream_run.artifact_text {
                    upstream_text.push_str(&format!("\n[{upstream_id}]\n{text}\n"));
                }
            }
        }
        let input =
            serde_json::to_string_pretty(&run.input).unwrap_or_else(|_| run.input.to_string());
        let loop_block = self.loop_context_for_node(compiled, &run.run_id, node_id).await?
            .map(|context| format!("{}\n\n", crate::loop_context::render_loop_context(&context)))
            .unwrap_or_default();
        Ok(format!(
            "[State Machine Task]\nnode_id: {node_id}\ndisplay_name: {}\n\n[Input]\n{input}\n\n{loop_block}[Upstream Outputs]{}\n\n[Instruction]\n{}",
            node.display_name,
            if upstream_text.is_empty() {
                "\n(none)\n".to_string()
            } else {
                upstream_text
            },
            node.instruction.as_deref().unwrap_or("")
        ))
    }

    pub(super) async fn evaluate_node_outcome(
        &self,
        compiled: &CompiledStateMachine,
        run: &StateMachineRun,
        node_id: &str,
        attempt: i32,
        artifact_text: &str,
    ) -> Result<JudgeEvaluationResult, CollaborationRuntimeError> {
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => return Ok(JudgeEvaluationResult::Outcome("complete".to_string(), None)),
        };
        let Some(node) = state_machine.nodes.get(node_id) else {
            return Ok(JudgeEvaluationResult::Outcome("complete".to_string(), None));
        };
        let Some(judge) = &node.judge else {
            return Ok(JudgeEvaluationResult::Outcome("complete".to_string(), None));
        };
        let upstream_outputs = self
            .judge_upstream_outputs(compiled, &run.run_id, node_id)
            .await?;
        let judge_type = judge
            .judge_type
            .clone()
            .unwrap_or_else(|| "llm".to_string());
        let allowed_outcomes = judge.outcomes.clone();
        let judge_timeout_ms = node
            .node_timeout_ms
            .or(state_machine.defaults.node_timeout_ms)
            .unwrap_or(DEFAULT_JUDGE_TIMEOUT_MS)
            .max(1);
        info!(target: RUNTIME_LOG_TARGET,
            run_id = %run.run_id,
            node_id = %node_id,
            attempt = attempt,
            judge_type = %judge_type,
            allowed_outcomes = ?allowed_outcomes,
            timeout_ms = judge_timeout_ms,
            "state_machine: judge evaluation started"
        );
        let started_at = Instant::now();
        let decision = match tokio::time::timeout(
            Duration::from_millis(judge_timeout_ms),
            self.judge.judge(JudgeRequest {
                run_id: run.run_id.clone(),
                node_id: node_id.to_string(),
                attempt,
                judge_type: judge_type.clone(),
                criteria: judge.criteria.clone(),
                allowed_outcomes: allowed_outcomes.clone(),
                input: run.input.clone(),
                upstream_outputs,
                artifact_text: artifact_text.to_string(),
            }),
        )
        .await
        {
            Ok(Ok(decision)) => decision,
            Ok(Err(error)) => {
                let elapsed_ms = elapsed_ms(started_at);
                let provider_error = judge_service_error_message(&error);
                let failure =
                    format!("judge failed for node {node_id} attempt {attempt}: {provider_error}");
                warn!(target: RUNTIME_LOG_TARGET,
                    run_id = %run.run_id,
                    node_id = %node_id,
                    attempt = attempt,
                    judge_type = %judge_type,
                    elapsed_ms = elapsed_ms,
                    error = %provider_error,
                    "state_machine: judge evaluation failed"
                );
                let details = self.judge_failure_details(
                    run,
                    node_id,
                    attempt,
                    &judge_type,
                    &allowed_outcomes,
                    "judge_failed",
                    &provider_error,
                    None,
                    elapsed_ms,
                );
                return Ok(JudgeEvaluationResult::Failed(failure, details));
            }
            Err(_) => {
                let elapsed_ms = elapsed_ms(started_at);
                let failure = format!(
                    "judge timed out for node {node_id} attempt {attempt} after {judge_timeout_ms}ms"
                );
                warn!(target: RUNTIME_LOG_TARGET,
                    run_id = %run.run_id,
                    node_id = %node_id,
                    attempt = attempt,
                    judge_type = %judge_type,
                    timeout_ms = judge_timeout_ms,
                    elapsed_ms = elapsed_ms,
                    "state_machine: judge evaluation timed out"
                );
                let details = self.judge_failure_details(
                    run,
                    node_id,
                    attempt,
                    &judge_type,
                    &allowed_outcomes,
                    "judge_timeout",
                    &failure,
                    Some(judge_timeout_ms),
                    elapsed_ms,
                );
                return Ok(JudgeEvaluationResult::Failed(failure, details));
            }
        };
        let elapsed_ms = elapsed_ms(started_at);
        if !allowed_outcomes
            .iter()
            .any(|outcome| outcome == &decision.outcome)
        {
            let failure = format!(
                "judge outcome is not declared by node {node_id}: {}",
                decision.outcome
            );
            warn!(target: RUNTIME_LOG_TARGET,
                run_id = %run.run_id,
                node_id = %node_id,
                attempt = attempt,
                judge_type = %judge_type,
                outcome = %decision.outcome,
                elapsed_ms = elapsed_ms,
                "state_machine: judge evaluation returned invalid outcome"
            );
            let details = self.judge_failure_details(
                run,
                node_id,
                attempt,
                &judge_type,
                &allowed_outcomes,
                "judge_invalid_outcome",
                &failure,
                None,
                elapsed_ms,
            );
            return Ok(JudgeEvaluationResult::Failed(failure, details));
        }
        info!(target: RUNTIME_LOG_TARGET,
            run_id = %run.run_id,
            node_id = %node_id,
            attempt = attempt,
            judge_type = %judge_type,
            outcome = %decision.outcome,
            elapsed_ms = elapsed_ms,
            "state_machine: judge evaluation completed"
        );
        Ok(JudgeEvaluationResult::Outcome(decision.outcome.clone(), Some(decision)))
    }

    pub(super) fn judge_failure_details(
        &self,
        run: &StateMachineRun,
        node_id: &str,
        attempt: i32,
        judge_type: &str,
        allowed_outcomes: &[String],
        reason: &str,
        error: &str,
        timeout_ms: Option<u64>,
        elapsed_ms: u64,
    ) -> Value {
        serde_json::json!({
                    "run_id": run.run_id.clone(),
                    "node_id": node_id,
                    "attempt": attempt,
                    "reason": reason,
                    "error": error,
                    "judge_type": judge_type,
                    "allowed_outcomes": allowed_outcomes,
                    "timeout_ms": timeout_ms,
                    "elapsed_ms": elapsed_ms,
                })
    }

    pub(super) async fn judge_upstream_outputs(
        &self,
        compiled: &CompiledStateMachine,
        run_id: &str,
        node_id: &str,
    ) -> Result<Vec<JudgeArtifact>, CollaborationRuntimeError> {
        let upstreams = compiled
            .upstreams
            .get(node_id)
            .map(Vec::as_slice)
            .unwrap_or(&[]);
        let mut artifacts = Vec::new();
        for upstream_id in upstreams {
            if let Some(node_run) = self.runs.get_node_run(run_id, upstream_id).await? {
                if !crate::loop_context::projects_upstream_artifact(compiled.execution_plan.as_ref(), node_id, &node_run) {
                    continue;
                }
                if let Some(text) = node_run.artifact_text {
                    artifacts.push(JudgeArtifact {
                        node_id: upstream_id.clone(),
                        text,
                    });
                }
            }
        }
        Ok(artifacts)
    }
}
