use super::*;
pub(super) fn compiled_has_human_input(compiled: &CompiledStateMachine) -> bool {
    match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine
            .nodes
            .values()
            .any(|node| node.kind == StateMachineNodeKind::HumanInput),
        _ => false,
    }
}

pub(super) fn human_input_assignees(compiled: &CompiledStateMachine) -> HashSet<String> {
    match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine
            .nodes
            .values()
            .filter_map(|node| {
                if node.kind != StateMachineNodeKind::HumanInput {
                    return None;
                }
                match &node.assignee {
                    Some(StateMachineAssignee::RuntimeActor { actor }) => Some(actor.clone()),
                    _ => None,
                }
            })
            .collect(),
        _ => HashSet::new(),
    }
}

pub(super) fn human_input_prompt_actor_ids(
    node: &bcs_domain::StateMachineNodeDefinition,
    session: &Session,
) -> Result<Vec<String>, CollaborationRuntimeError> {
    let actor_ids = match &node.assignee {
        Some(StateMachineAssignee::RuntimeActor { actor }) => vec![actor.clone()],
        None => session
            .participants
            .iter()
            .filter(|participant| {
                participant.is_human() && participant.effective_mode() == ParticipantMode::Present
            })
            .map(|participant| participant.bot_uuid.clone())
            .collect(),
        Some(StateMachineAssignee::BotBinding { .. }) => {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "human_input prompt cannot target a bot_binding assignee".to_string(),
            ));
        }
    };
    let audience = MessageAudience::directed(actor_ids).map_err(|error| {
        CollaborationRuntimeError::InvalidDefinition(format!(
            "HumanInput prompt audience is invalid: {error}"
        ))
    })?;
    let MessageAudience::Directed { actor_ids } = audience else {
        unreachable!("MessageAudience::directed must produce a directed audience")
    };
    Ok(actor_ids)
}impl CollaborationRuntime {

    pub(super) async fn activate_human_input(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        run: &StateMachineRun,
        node_id: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        self.loop_context_for_node(compiled, &run.run_id, node_id).await?;
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
        let node_run = self
            .runs
            .get_node_run(&run.run_id, node_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::NodeNotFound {
                run_id: run.run_id.clone(),
                node_id: node_id.to_string(),
            })?;
        let now = bcs_protocol::now_ms();
        let timeout_ms = node.node_timeout_ms.ok_or_else(|| {
            CollaborationRuntimeError::InvalidDefinition(format!(
                "human_input node {node_id} requires node_timeout_ms"
            ))
        })?;
        let deadline = now.saturating_add(timeout_ms);
        let assignee_id = match &node.assignee {
            Some(StateMachineAssignee::RuntimeActor { actor }) => Some(actor.as_str()),
            _ => None,
        };
        let mut data = BTreeMap::from([
            ("run_id".to_string(), serde_json::json!(run.run_id.clone())),
            ("node_id".to_string(), serde_json::json!(node_id)),
            ("attempt".to_string(), serde_json::json!(node_run.attempt)),
            (
                "predecessor_node_ids".to_string(),
                serde_json::json!(predecessor_node_ids(compiled, node_id)),
            ),
            (
                "started_at".to_string(),
                serde_json::json!(event_timestamp(now)?),
            ),
        ]);
        if let Some(assignee_id) = assignee_id {
            data.insert("assignee_id".to_string(), serde_json::json!(assignee_id));
        }
        let event = self.prepare_node_event(&compiled,
            "state_machine.node.started",
            run,
            "state_machine.node",
            node_id,
            &format!("{node_id}:{}:started", node_run.attempt),
            None,
            None,
            now,
            data,
        )?;
        let command = MarkHumanNodeRunningCommand {
            run_id: run.run_id.clone(),
            node_id: node_id.to_string(),
            attempt: node_run.attempt,
            started_at_ms: now,
            timeout_deadline_ms: deadline,
        };
        let marked = if self.history_persistence_enabled {
            self.activate_human_with_history(compiled, run, &node_run, command, event).await?
        } else { match event {
            Some(event) => {
                self.runs
                    .commit_eventful_transition(StateMachineEventfulTransition::StartHumanNode {
                        command,
                        event,
                    })
                    .await?
            }
            None => {
                self.runs
                    .mark_human_node_running_if_run_active(command)
                    .await?
            }
        }
        };
        if !marked {
            return Ok(());
        }
        self.observe_loop_node(compiled, run, node_id, node_run.attempt, "started", None);
        match self.opening_message_for_existing_run(run).await {
            Ok(opening_message) => {
                self.publish_state_machine_panel_event(group, run, &opening_message)
                    .await;
            }
            Err(error) => {
                warn!(target: RUNTIME_LOG_TARGET,
                    run_id = %run.run_id,
                    session_id = %run.session_id,
                    error = %error,
                    "state_machine: failed to reload opening message for frontend publication"
                );
            }
        }
        if let Err(error) = self.resume_human_ready(compiled, run, node_id).await {
            warn!(target: RUNTIME_LOG_TARGET,run_id = %run.run_id, node_id = %node_id, error = %error, "state_machine: human-ready channel delivery failed");
        }
        Ok(())
    }

    pub(super) async fn resume_human_ready(&self, compiled: &CompiledStateMachine, run: &StateMachineRun, node_id: &str) -> Result<(), CollaborationRuntimeError> {
        let CollaborationRuntimeDefinition::StateMachine(state_machine) = &compiled.definition.runtime else { return Ok(()); };
        let node = state_machine.nodes.get(node_id).ok_or_else(|| CollaborationRuntimeError::InvalidDefinition("missing human node".into()))?;
        if node.notification.is_none() { return Ok(()); }
        let running_node = self
            .runs
            .get_node_run(&run.run_id, node_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::NodeNotFound {
                run_id: run.run_id.clone(),
                node_id: node_id.to_string(),
            })?;
        let pending = self
            .pending_human_node_view(compiled, run, &running_node)
            .await?;

        let Some(notification) = node.notification.as_ref() else {
            return Ok(());
        };
        let assignee_actor_id = match &node.assignee {
            Some(StateMachineAssignee::RuntimeActor { actor }) => actor.clone(),
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(format!(
                    "human_input node {node_id} with notification requires runtime_actor assignee"
                )));
            }
        };
        let human_input_channel = state_machine.human_input_channel.as_ref().ok_or_else(|| {
            CollaborationRuntimeError::InvalidDefinition(format!(
                "human_input node {node_id} with notification requires state_machine.human_input_channel"
            ))
        })?;
        let Some(outbound) = self.session_channel_outbound.as_ref() else {
            return Ok(());
        };
        let event = HumanInputReadyEvent {
            event_id: format!("human-ready:{}:{}", run.run_id, node_id),
            group_id: run.group_id.clone(),
            session_id: run.session_id.clone(),
            run_id: run.run_id.clone(),
            node_id: node_id.to_string(),
            execution: pending.execution.clone(),
            display_name: pending.display_name,
            instruction: pending.instruction,
            assignee_actor_id,
            channel_type: human_input_channel.channel_type.clone(),
            notification_mode: notification.mode,
            fixed_group_conversation_id: human_input_channel
                .fixed_group
                .as_ref()
                .map(|group| group.conversation_id.clone()),
            response_ref: pending.response_ref,
            upstream_artifacts: pending.upstream_artifacts,
            judge_outcomes: pending.judge_outcomes,
            timeout_deadline_ms: pending.timeout_deadline_ms,
            loop_context: pending.loop_context,
        };
        if !self.human_input_notification_is_current(&run.run_id, &run.session_id, node_id,
            running_node.timeout_deadline_ms.ok_or_else(|| CollaborationRuntimeError::InvalidRequest("human node has no original deadline".into()))?).await? { return Ok(()); }
        outbound.publish_human_input_ready(event).await?;
        Ok(())
    }

    pub(super) async fn validate_human_input_channel_for_group(
        &self,
        group_id: &str,
        compiled: &CompiledStateMachine,
    ) -> Result<(), CollaborationRuntimeError> {
        let CollaborationRuntimeDefinition::StateMachine(state_machine) =
            &compiled.definition.runtime
        else {
            return Ok(());
        };
        let Some(channel) = state_machine.human_input_channel.as_ref() else {
            return Ok(());
        };
        if let Some(outbound) = self.session_channel_outbound.as_ref() {
            outbound
                .validate_human_input_channel(group_id, &channel.channel_type)
                .await?;
        }
        Ok(())
    }

    pub(super) async fn handle_session_human_input_impl(
        &self,
        cmd: HandleSessionHumanInputCommand,
    ) -> Result<HandleSessionHumanInputOutcome, CollaborationRuntimeError> {
        let group = self.groups.get(&cmd.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", cmd.group_id))
        })?;
        let state_machine_group = group.group_strategy == GroupStrategy::StateMachine;
        let Some(session_id) = cmd.session_id.as_deref() else {
            if state_machine_group {
                return Err(CollaborationRuntimeError::Conflict(
                    "state-machine messages require a session id".to_string(),
                ));
            }
            return Ok(HandleSessionHumanInputOutcome::NotStateMachine);
        };
        let Some(run) = self.runs.get_run_by_session_id(session_id).await? else {
            if state_machine_group {
                return Err(CollaborationRuntimeError::Conflict(
                    "state-machine session has no active run".to_string(),
                ));
            }
            return Ok(HandleSessionHumanInputOutcome::NotStateMachine);
        };
        if run.group_id != cmd.group_id {
            return Err(CollaborationRuntimeError::Conflict(
                "state-machine session does not belong to the target group".to_string(),
            ));
        }

        let pending = self
            .list_pending_human_nodes(ListPendingHumanNodesCommand {
                run_id: run.run_id.clone(),
                caller_actor_id: cmd.caller_actor_id.clone(),
            })
            .await?;
        let node = match pending.as_slice() {
            [node] => node,
            [] => {
                if !state_machine_group {
                    return Ok(HandleSessionHumanInputOutcome::NotStateMachine);
                }
                return Err(CollaborationRuntimeError::Conflict(
                    "state machine is not waiting for Human input".to_string(),
                ));
            }
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(
                    "multiple pending human_input nodes are not supported".to_string(),
                ));
            }
        };
        let caller_ingress = cmd.caller_ingress.clone();
        let response = self
            .respond_human_node(RespondHumanNodeCommand {
                run_id: run.run_id,
                node_id: node.node_id.clone(),
                caller_actor_id: cmd.caller_actor_id,
                content: cmd.content,
                source: cmd.source,
            })
            .await?;
        // §15.83 S2 human-input rearm (spec 2026-10-22): refreshes the
        // run's delegation-only association window. The response's primary
        // purpose is node data — a missing registrar or refused refresh
        // never fails the response; nodes keep the existing lineage facts.
        //
        // INTENTIONAL ORDERING BOUNDARY — do not "fix" the apparent
        // mismatch: `respond_human_node` drives node progression
        // internally, meaning sibling/next nodes dispatched inside
        // `apply_completed_node_progression` read the PRE-rearm lineage
        // window; a refreshed window only benefits subsequent rounds
        // (retry / progression scan / later dispatch). This is the
        // accepted consequence of the spec's "refresh AFTER consumption"
        // ordering (spec 2026-10-22: 续注真正生效于前端重连后的新输入).
        // Whoever touches `apply_completed_node_progression` must NOT
        // rearrange this ordering based on that apparent mismatch.
        if let Some(ingress) = caller_ingress {
            self.rearm_instance_association(&response.run, ingress).await;
        }
        Ok(HandleSessionHumanInputOutcome::Consumed { response })
    }

    pub(super) async fn respond_human_node_impl(
        &self,
        cmd: RespondHumanNodeCommand,
    ) -> Result<RespondHumanNodeOutcome, CollaborationRuntimeError> {
        let content = cmd.content.trim().to_string();
        if content.is_empty() {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "human response must not be empty".to_string(),
            ));
        }
        if content.len() > MAX_HUMAN_RESPONSE_BYTES {
            return Err(CollaborationRuntimeError::InvalidRequest(format!(
                "human response exceeds {MAX_HUMAN_RESPONSE_BYTES} UTF-8 bytes"
            )));
        }

        let run = self
            .runs
            .get_run(&cmd.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id.clone()))?;
        self.authorize_human_for_run(&run, &cmd.caller_actor_id)
            .await?;
        if run.status != StateMachineRunStatus::Running {
            return Err(CollaborationRuntimeError::Conflict(
                "human node is no longer accepting responses".to_string(),
            ));
        }

        let compiled = self.load_run_compiled(&run).await?;
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(
                    "runtime.kind must be state_machine".to_string(),
                ));
            }
        };
        let node_definition = state_machine.nodes.get(&cmd.node_id).ok_or_else(|| {
            CollaborationRuntimeError::NodeNotFound {
                run_id: cmd.run_id.clone(),
                node_id: cmd.node_id.clone(),
            }
        })?;
        if node_definition.kind != StateMachineNodeKind::HumanInput {
            return Err(CollaborationRuntimeError::InvalidRequest(format!(
                "node {} is not a human_input node",
                cmd.node_id
            )));
        }
        // COSEC: IM-targeted input is restricted to its frozen assignee. A
        // frontend-only node has no assignee and retains the existing rule
        // that any Present Human participant in the run session may respond.
        let caller_matches_node = match &node_definition.assignee {
            None => true,
            Some(StateMachineAssignee::RuntimeActor { actor }) => actor == &cmd.caller_actor_id,
            Some(StateMachineAssignee::BotBinding { .. }) => false,
        };
        if !caller_matches_node {
            return Err(CollaborationRuntimeError::Forbidden(
                "caller is not the HumanInput node assignee".to_string(),
            ));
        }

        let node = self
            .runs
            .get_node_run(&cmd.run_id, &cmd.node_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::NodeNotFound {
                run_id: cmd.run_id.clone(),
                node_id: cmd.node_id.clone(),
            })?;
        let now = bcs_protocol::now_ms();
        if node.status != StateMachineNodeStatus::Running
            || node
                .timeout_deadline_ms
                .is_none_or(|deadline| deadline <= now)
        {
            return Err(CollaborationRuntimeError::Conflict(
                "human node is no longer accepting responses".to_string(),
            ));
        }

        let group = self.groups.get(&run.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!("group not found: {}", run.group_id))
        })?;
        self.preserve_node_history(&compiled, &group, &run, &node).await?;
        if node_definition.judge.is_some() {
            let accepted = if self.history_persistence_enabled || self.has_history_output(&run, &node).await? {
                self.accept_output_history(&compiled, &group, &run, &node, &content, Some(&cmd.caller_actor_id), true).await?
            } else {
                self.runs.begin_node_judging(&run.run_id, &cmd.node_id, node.attempt,
                    content, Some(cmd.caller_actor_id)).await?
            };
            if !accepted {
                return Err(CollaborationRuntimeError::Conflict("human Judge input is already fixed".into()));
            }
            match self.resume_judging(&compiled, &group, &run, &cmd.node_id, node.attempt).await? {
                JudgingProgress::Unclaimed => return Err(CollaborationRuntimeError::Conflict("human Judge is already claimed or no longer active".into())),
                JudgingProgress::Failed(error) => return Err(CollaborationRuntimeError::JudgeUnavailable(error)),
                JudgingProgress::Completed => {}
            }
            let node = self.runs.get_node_run(&run.run_id, &cmd.node_id).await?
                .ok_or_else(|| CollaborationRuntimeError::RunNotFound(run.run_id.clone()))?;
            let run = self.runs.get_run(&run.run_id).await?
                .ok_or_else(|| CollaborationRuntimeError::RunNotFound(run.run_id.clone()))?;
            return Ok(RespondHumanNodeOutcome { node, run });
        }
        let accepted = if self.history_persistence_enabled || self.has_history_output(&run, &node).await? {
            self.accept_output_history(&compiled, &group, &run, &node, &content, Some(&cmd.caller_actor_id), false).await?
        } else { self.runs.record_human_response_if_running(
                &run.run_id,
                &cmd.node_id,
                node.attempt,
                content.clone(),
                cmd.caller_actor_id.clone(),
            )
            .await? };
        if !accepted {
            return Err(CollaborationRuntimeError::Conflict(
                "human node is no longer accepting responses".to_string(),
            ));
        }
        match self
            .evaluate_node_outcome(&compiled, &run, &cmd.node_id, node.attempt, &content)
            .await?
        {
            JudgeEvaluationResult::Outcome(outcome, _) => {
                let completed_at = bcs_protocol::now_ms();
                let completed_content = content.clone();
                let responded_by = cmd.caller_actor_id.clone();
                let event = self.prepare_node_event(&compiled,
                    "state_machine.node.completed",
                    &run,
                    "state_machine.node",
                    &cmd.node_id,
                    &format!("{}:{}:completed", cmd.node_id, node.attempt),
                    Some(&responded_by),
                    Some(EventActorType::Human),
                    completed_at,
                    BTreeMap::from([
                        ("run_id".to_string(), serde_json::json!(run.run_id.clone())),
                        (
                            "node_id".to_string(),
                            serde_json::json!(cmd.node_id.clone()),
                        ),
                        ("attempt".to_string(), serde_json::json!(node.attempt)),
                        ("outcome".to_string(), serde_json::json!(outcome.clone())),
                        (
                            "output".to_string(),
                            content_value(Value::String(completed_content.clone()))?,
                        ),
                        (
                            "completed_at".to_string(),
                            serde_json::json!(event_timestamp(completed_at)?),
                        ),
                        (
                            "duration_ms".to_string(),
                            serde_json::json!(
                                completed_at
                                    .saturating_sub(node.started_at.unwrap_or(completed_at),)
                            ),
                        ),
                    ]),
                )?;
                let completed = match event {
                    Some(event) => {
                        self.runs
                            .commit_eventful_transition(
                                StateMachineEventfulTransition::CompleteNode {
                                    run_id: run.run_id.clone(),
                                    node_id: cmd.node_id.clone(),
                                    attempt: node.attempt,
                                    outcome: outcome.clone(),
                                    artifact_text: completed_content.clone(),
                                    responded_by: Some(responded_by.clone()),
                                    completed_at_ms: completed_at,
                                    event,
                                },
                            )
                            .await?
                    }
                    None => {
                        self.runs
                            .complete_node_attempt(
                                &run.run_id,
                                &cmd.node_id,
                                node.attempt,
                                outcome.clone(),
                                content,
                                Some(cmd.caller_actor_id),
                                completed_at,
                            )
                            .await?
                    }
                };
                if !completed {
                    return Err(CollaborationRuntimeError::Conflict(
                        "human response lost the completion race".to_string(),
                    ));
                }
                self.observe_loop_node(&compiled, &run, &cmd.node_id, node.attempt, "completed", Some(&outcome));
                let view = self
                    .apply_completed_node_progression(
                        &compiled,
                        &group,
                        &run,
                        &cmd.node_id,
                        &outcome,
                        completed_at,
                    )
                    .await?;
                let node = self
                    .runs
                    .get_node_run(&run.run_id, &cmd.node_id)
                    .await?
                    .ok_or_else(|| CollaborationRuntimeError::NodeNotFound {
                        run_id: run.run_id.clone(),
                        node_id: cmd.node_id.clone(),
                    })?;
                Ok(RespondHumanNodeOutcome {
                    node,
                    run: view.run,
                })
            }
            JudgeEvaluationResult::Failed(error, _) => {
                let failed_at = bcs_protocol::now_ms();
                let failed = self
                    .fail_retryable_node_attempt(
                        &run.run_id,
                        &cmd.node_id,
                        node.attempt,
                        error.clone(),
                        failed_at,
                    )
                    .await?;
                if !failed {
                    return Err(CollaborationRuntimeError::Conflict(
                        "human response lost the completion race".to_string(),
                    ));
                }
                self.resume_failed_node(
                    &compiled,
                    &group,
                    &run,
                    &cmd.node_id,
                    node.attempt,
                )
                .await?;
                Err(CollaborationRuntimeError::JudgeUnavailable(error))
            }
        }
    }
}
