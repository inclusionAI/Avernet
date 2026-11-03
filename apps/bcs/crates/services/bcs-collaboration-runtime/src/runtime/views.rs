use super::*;
pub(super) fn run_graph_view(
    run: StateMachineRun,
    node_runs: Vec<StateMachineNodeRun>,
    compiled: &CompiledStateMachine,
    authoring: &CollaborationDefinition,
) -> Result<StateMachineRunGraphView, CollaborationRuntimeError> {
    let state_machine = match &authoring.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
        _ => {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "runtime.kind must be state_machine".to_string(),
            ));
        }
    };
    let node_runs_by_id = node_runs
        .iter()
        .map(|node| (node.node_id.as_str(), node))
        .collect::<BTreeMap<_, _>>();
    let projection = project_definition_graph(compiled)?;
    let plan = compiled.execution_plan.as_ref();
    let edge_routes: BTreeMap<_, _> = plan.into_iter().flat_map(|plan| &plan.edge_metadata)
        .map(|edge| ((edge.source_execution_node_id.as_str(), edge.outcome.as_str(), edge.target_execution_node_id.as_str()), edge.loop_route.clone())).collect();
    let nodes = projection
        .nodes
        .into_iter()
        .map(|node| {
            let run_node = node_runs_by_id.get(node.node_id.as_str()).copied();
            StateMachineGraphNodeView {
                execution: plan.and_then(|plan| plan.node_metadata.get(&node.node_id)).and_then(crate::definition::node_execution_metadata),
                node_id: node.node_id,
                display_name: node.display_name,
                kind: node.kind,
                assignee_display_name: match &node.assignee {
                    Some(StateMachineAssignee::BotBinding { binding }) => authoring.participants.get(binding)
                        .and_then(|participant| participant.display_name.as_deref())
                        .map(str::trim).filter(|name| !name.is_empty()).map(str::to_owned),
                    _ => None,
                },
                assignee: node.assignee,
                final_output: node.final_output,
                status: run_node.map(|node| node.status),
                attempt: run_node.map(|node| node.attempt),
                outcome: run_node.and_then(|node| node.outcome.clone()),
                assignee_bot_id: run_node.and_then(|node| node.assignee_bot_id.clone()),
                started_at: run_node.and_then(|node| node.started_at),
                completed_at: run_node.and_then(|node| node.completed_at),
                sub_status: run_node.and_then(node_sub_status),
            }
        })
        .collect();
    let edges = projection
        .edges
        .into_iter()
        .map(|edge| StateMachineGraphEdgeView {
            display_name: edge.display_name,
            loop_route: edge_routes.get(&(edge.source.as_str(), edge.outcome.as_str(), edge.target.as_str())).cloned().flatten(),
            source: edge.source,
            outcome: edge.outcome,
            target: edge.target,
            guard: edge.guard,
        })
        .collect();
    Ok(StateMachineRunGraphView {
        loops: crate::loop_graph::loop_graph_descriptors(authoring),
        run,
        definition: StateMachineGraphDefinitionView {
            id: compiled.definition.id.clone(),
            version: compiled.definition.version,
            name: compiled.definition.name.clone(),
            graph_mode: state_machine.graph_mode,
            execution_graph_mode: plan.map(|plan| plan.state_machine.graph_mode),
            execution_plan_compiler_version: plan.map(|plan| plan.compiler_version.clone()),
            initial_node: state_machine.initial_node.clone(),
            initial_nodes: compiled.initial_nodes.clone(),
        },
        nodes,
        edges,
    })
}

pub(super) fn node_uses_judge(compiled: &CompiledStateMachine, node_id: &str) -> bool {
    match &compiled.definition.runtime {
        CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine
            .nodes
            .get(node_id)
            .is_some_and(|node| node.judge.is_some()),
        _ => false,
    }
}

fn node_sub_status(node: &StateMachineNodeRun) -> Option<StateMachineNodeSubStatus> {
    if node.status != StateMachineNodeStatus::Running {
        return None;
    }
    Some(if node.artifact_text.is_some() {
        StateMachineNodeSubStatus::Judging
    } else {
        StateMachineNodeSubStatus::AwaitingResponse
    })
}

fn apply_message_window(
    messages: Vec<GroupMessage>, limit: u64, before: Option<u64>,
) -> Vec<GroupMessage> {
    let messages = messages.into_iter().filter(|message| before.is_none_or(|timestamp| message.timestamp < timestamp)).collect();
    merge_state_machine_history(Vec::new(), messages, limit)
}impl CollaborationRuntime {

    pub(super) async fn run_view(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(run_id).await? else {
            return Ok(None);
        };
        let nodes = self.runs.list_node_runs(run_id).await?;
        let judge_outputs = self.judge_outputs(run_id).await?;
        let node_execution_metadata = self.run_plan_for_view(run_id).await?.map(|plan|
            plan.node_metadata.iter().filter_map(|(id, meta)|
                crate::definition::node_execution_metadata(meta).map(|execution| (id.clone(), execution))).collect());
        Ok(Some(StateMachineRunView {
            run,
            nodes,
            node_execution_metadata,
            judge_outputs,
        }))
    }

    pub(super) async fn judge_outputs(
        &self,
        run_id: &str,
    ) -> Result<Vec<StateMachineJudgeOutputView>, CollaborationRuntimeError> {
        let events = self
            .events
            .list_events_by_run_and_type(run_id, "state_machine.judge.completed")
            .await?;
        events
            .into_iter()
            .map(|event| {
                let decision = serde_json::from_value(event.payload).map_err(|error| {
                    CollaborationRuntimeError::InvalidRequest(format!(
                        "invalid state_machine.judge.completed payload: {error}"
                    ))
                })?;
                Ok(StateMachineJudgeOutputView {
                    node_id: event.node_id.unwrap_or_default(),
                    attempt: event.attempt.unwrap_or_default(),
                    created_at: event.created_at,
                    decision,
                })
            })
            .collect()
    }

    pub(super) async fn judge_outputs_for_node(
        &self,
        run_id: &str,
        node_id: &str,
    ) -> Result<Vec<StateMachineJudgeOutputView>, CollaborationRuntimeError> {
        let events = self
            .events
            .list_events_by_run_node_and_type(run_id, node_id, "state_machine.judge.completed")
            .await?;
        events
            .into_iter()
            .map(|event| {
                let decision = serde_json::from_value(event.payload).map_err(|error| {
                    CollaborationRuntimeError::InvalidRequest(format!(
                        "invalid state_machine.judge.completed payload: {error}"
                    ))
                })?;
                Ok(StateMachineJudgeOutputView {
                    node_id: event.node_id.unwrap_or_else(|| node_id.to_string()),
                    attempt: event.attempt.unwrap_or_default(),
                    created_at: event.created_at,
                    decision,
                })
            })
            .collect()
    }

    pub(super) async fn pending_human_node_view(
        &self,
        compiled: &CompiledStateMachine,
        run: &StateMachineRun,
        node_run: &StateMachineNodeRun,
    ) -> Result<PendingHumanNodeView, CollaborationRuntimeError> {
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(
                    "runtime.kind must be state_machine".to_string(),
                ));
            }
        };
        let node = state_machine.nodes.get(&node_run.node_id).ok_or_else(|| {
            CollaborationRuntimeError::NodeNotFound {
                run_id: run.run_id.clone(),
                node_id: node_run.node_id.clone(),
            }
        })?;
        let mut upstream_artifacts = self
            .judge_upstream_outputs(compiled, &run.run_id, &node_run.node_id)
            .await?;
        upstream_artifacts.sort_by(|left, right| left.node_id.cmp(&right.node_id));
        Ok(PendingHumanNodeView {
            node_id: node_run.node_id.clone(),
            execution: crate::definition::execution_metadata_for_node(compiled, &node_run.node_id)?,
            display_name: node.display_name.clone(),
            instruction: node.instruction.clone().unwrap_or_default(),
            response_ref: format!("{}/{}", run.run_id, node_run.node_id),
            judge_outcomes: node
                .judge
                .as_ref()
                .map(|judge| judge.outcomes.clone())
                .unwrap_or_default(),
            timeout_deadline_ms: node_run.timeout_deadline_ms,
            upstream_artifacts,
            loop_context: self.loop_context_for_node(compiled, &run.run_id, &node_run.node_id).await?,
        })
    }

    pub(super) async fn run_plan_for_view(&self, run_id: &str) -> Result<Option<bcs_domain::StateMachineExecutionPlan>, CollaborationRuntimeError> {
        let Some(snapshot) = self.definitions.get_run_snapshot(run_id).await? else {
            // Legacy Run/Node queries were available before snapshots existed.
            return Ok(None);
        };
        if snapshot.execution_plan.is_none() && matches!(&snapshot.definition.runtime,
            CollaborationRuntimeDefinition::StateMachine(machine) if machine.version == 1)
        {
            return Ok(None);
        }
        Ok(crate::snapshot::load_state_machine_snapshot(snapshot)?.plan)
    }

    pub(super) async fn state_machine_panel_message(
        &self,
        run: &StateMachineRun,
    ) -> Result<GroupMessage, CollaborationRuntimeError> {
        let opening_message = self.opening_message_for_existing_run(run).await?;
        let RenderedOpeningMessage { content, component } = opening_message;
        Ok(GroupMessage {
            id: format!("{}:000-panel", run.run_id),
            timestamp: run.created_at,
            sender: BCS_STATE_MACHINE_MESSAGE_SENDER.to_string(),
            content,
            message_type: GroupMessageType::Bot,
            bot_name: Some(BCS_STATE_MACHINE_MESSAGE_SENDER_NAME.to_string()),
            role: MessageRole::Assistant,
            history_meta: None,
            metadata: Some(state_machine_panel_metadata(run, component.as_deref())),
            run_id: String::new(),
            attachments: None,
        })
    }

    pub(super) async fn state_machine_messages_from_snapshot(
        &self,
        group: &Group,
        run: &StateMachineRun,
        human_view: Option<&HumanMessageView>,
    ) -> Result<Vec<GroupMessage>, CollaborationRuntimeError> {
        let nodes = self.runs.list_node_runs(&run.run_id).await?;
        let compiled = self.load_run_definition_for_view(run).await?.execution;
        let state_machine = match &compiled.definition.runtime {
            CollaborationRuntimeDefinition::StateMachine(state_machine) => state_machine,
            _ => {
                return Err(CollaborationRuntimeError::InvalidDefinition(
                    "runtime.kind must be state_machine".to_string(),
                ));
            }
        };
        let session = match self.sessions.get(&run.session_id).await {
            Ok(session) => session,
            Err(error) => {
                warn!(target: RUNTIME_LOG_TARGET,
                    session_id = %run.session_id,
                    error = %error,
                    "state_machine: failed to load session participant names"
                );
                None
            }
        };
        let panel_key = format!("{}:000-panel", run.run_id);
        let saved_messages = if let Some(repo) = self.message_repo.as_ref() {
            let mut keys = BTreeSet::from([panel_key.clone(), physical_message_id(&panel_key)]);
            for node in &nodes {
                if node.artifact_text.is_some() {
                    keys.extend([output_message_id(node), output_message_key(node)]);
                }
                if node.started_at.is_some() && state_machine.nodes.get(&node.node_id)
                    .is_some_and(|definition| definition.kind == StateMachineNodeKind::HumanInput) {
                    let prompt = format!("{}:{}:{}:human-input-prompt", run.run_id, node.node_id, node.attempt);
                    keys.extend([physical_message_id(&prompt), prompt]);
                }
            }
            let saved = repo.get_state_machine_messages_by_keys(&run.session_id, &keys.into_iter().collect::<Vec<_>>())
                .await.map_err(message_history_error)?;
            let mut by_key = BTreeMap::new();
            for message in saved {
                if message.group_id != run.group_id || message.run_id != run.run_id {
                    return Err(CollaborationRuntimeError::Conflict("history identity mismatch".into()));
                }
                if let Some(key) = &message.client_msg_id {
                    by_key.entry(key.clone()).or_insert_with(|| message.clone());
                }
                by_key.entry(message.message_id.clone()).or_insert(message);
            }
            by_key
        } else { BTreeMap::new() };
        let mut messages = Vec::new();
        if let Some(saved) = saved_messages.get(&panel_key).or_else(|| saved_messages.get(&physical_message_id(&panel_key))) {
            if human_view.is_none_or(|view| view.allows(saved)) {
                messages.extend(project_state_machine_message(saved));
            }
        } else {
            messages.push(self.state_machine_panel_message(run).await?);
        }
        for node in nodes {
            let node_definition = state_machine.nodes.get(&node.node_id).ok_or_else(|| {
                CollaborationRuntimeError::NodeNotFound {
                    run_id: run.run_id.clone(),
                    node_id: node.node_id.clone(),
                }
            })?;
            let prompt_key = format!("{}:{}:{}:human-input-prompt", run.run_id, node.node_id, node.attempt);
            if let Some(saved) = saved_messages.get(&prompt_key).or_else(|| saved_messages.get(&physical_message_id(&prompt_key))) {
                if human_view.is_some_and(|view| view.scope == bcs_domain::MessageViewScope::Participant && view.allows(saved)) {
                    messages.extend(project_state_machine_message(saved));
                }
            } else if node_definition.kind == StateMachineNodeKind::HumanInput
                && node.started_at.is_some()
                && human_view
                    .is_some_and(|view| view.scope == bcs_domain::MessageViewScope::Participant)
            {
                let session = session.as_ref().ok_or_else(|| {
                    CollaborationRuntimeError::InvalidRequest(format!(
                        "session not found: {}",
                        run.session_id
                    ))
                })?;
                let prompt_actor_ids = human_input_prompt_actor_ids(node_definition, session)?;
                let audience = MessageAudience::directed(prompt_actor_ids.iter().cloned())
                    .map_err(|error| {
                        CollaborationRuntimeError::InvalidDefinition(format!(
                            "HumanInput prompt audience is invalid: {error}"
                        ))
                    })?;
                let prompt_is_visible = human_view.is_none_or(|view| {
                    view.allows_artifact(MessageVisibilityDomain::StateMachine, Some(&audience))
                });
                if prompt_is_visible {
                    let pending = self.pending_human_node_view(&compiled, run, &node).await?;
                    messages.push(GroupMessage {
                        id: format!(
                            "{}:{}:{}:human-input-prompt",
                            run.run_id, node.node_id, node.attempt
                        ),
                        timestamp: node.started_at.unwrap_or(run.updated_at),
                        sender: BCS_STATE_MACHINE_MESSAGE_SENDER.to_string(),
                        content: pending.instruction.clone(),
                        message_type: GroupMessageType::Bot,
                        bot_name: Some(BCS_STATE_MACHINE_MESSAGE_SENDER_NAME.to_string()),
                        role: MessageRole::Assistant,
                        history_meta: None,
                        metadata: Some(state_machine_human_input_prompt_metadata(
                            run,
                            &node,
                            &pending,
                            &prompt_actor_ids,
                        )),
                        run_id: String::new(),
                        attachments: None,
                    });
                }
            }
            if let Some(saved) = saved_messages.get(&output_message_id(&node))
                .or_else(|| saved_messages.get(&output_message_key(&node))) {
                if human_view.is_none_or(|view| view.allows(saved)) {
                    messages.push(stored_output_message(saved)?);
                }
                continue;
            }
            if let Some(view) = human_view
                && view.scope == bcs_domain::MessageViewScope::Participant
                && node.responded_by.as_deref() != Some(view.actor_id.as_str())
            {
                continue;
            }
            if let Some(artifact_text) = node.artifact_text.as_ref() {
                let is_human = node.responded_by.is_some();
                let Some(sender) = node
                    .responded_by
                    .clone()
                    .or_else(|| node.assignee_bot_id.clone())
                else {
                    continue;
                };
                messages.push(GroupMessage {
                    id: if compiled.execution_plan.is_some() { output_message_id(&node) } else { output_message_key(&node) },
                    timestamp: node.completed_at.unwrap_or(run.updated_at),
                    sender: sender.clone(),
                    content: artifact_text.clone(),
                    // Keep the existing group-history wire shape: user messages
                    // are identified by role, while message_type remains `bot`.
                    // This lets the current Workbench render HumanInput responses
                    // without requiring a frontend protocol change.
                    message_type: GroupMessageType::Bot,
                    bot_name: if is_human {
                        session
                            .as_ref()
                            .and_then(|session| {
                                session
                                    .participants
                                    .iter()
                                    .find(|participant| participant.bot_uuid == sender)
                            })
                            .and_then(|participant| participant.bot_name.clone())
                    } else {
                        bot_display_name(group, &sender)
                    },
                    role: if is_human {
                        MessageRole::User
                    } else {
                        MessageRole::Assistant
                    },
                    history_meta: None,
                    metadata: Some(state_machine_message_metadata(run, &node, "output", crate::definition::execution_metadata_for_node(&compiled, &node.node_id)?.as_ref())),
                    run_id: String::new(),
                    attachments: None,
                });
            }
        }
        Ok(messages)
    }

    pub(super) async fn get_state_machine_node_run_impl(
        &self,
        run_id: &str,
        node_id: &str,
    ) -> Result<Option<StateMachineNodeRunView>, CollaborationRuntimeError> {
        let Some(node) = self.runs.get_node_run(run_id, node_id).await? else {
            return Ok(None);
        };
        let judge_outputs = self.judge_outputs_for_node(run_id, node_id).await?;
        let sub_status = node_sub_status(&node);
        let execution = match self.run_plan_for_view(run_id).await? {
            Some(plan) => {
                let meta = plan.node_metadata.get(node_id).ok_or_else(|| CollaborationRuntimeError::InvalidDefinition(
                    "Node is absent from its saved execution plan".into()))?;
                crate::definition::node_execution_metadata(meta)
            }
            None => None,
        };
        Ok(Some(StateMachineNodeRunView {
            node,
            execution,
            sub_status,
            judge_outputs,
        }))
    }

    pub(super) async fn get_state_machine_session_history_impl(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        if limit == 0 {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "history limit must be greater than 0".to_string(),
            ));
        }
        if self.history_persistence_enabled {
            let Some(session) = self.sessions.get(session_id).await.map_err(|error| {
                CollaborationRuntimeError::Internal(ServiceError::InternalError(error.to_string()))
            })? else { return Ok(None); };
            if session.created_at >= self.history_cutoff_timestamp {
                return self.message_store_history(&session, limit, before, None).await;
            }
        }
        let runs = self.runs.list_runs_by_session_id(session_id).await?;
        let Some(latest_run) = runs.first() else {
            return Ok(None);
        };
        let group = self.groups.get(&latest_run.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!(
                "group not found: {}",
                latest_run.group_id
            ))
        })?;
        let mut messages = Vec::new();
        for run in runs {
            messages.extend(
                self.state_machine_messages_from_snapshot(&group, &run, None)
                    .await?,
            );
        }
        let persisted = self.persisted_history_attempts(&group.id, session_id, limit, before, None).await?;
        messages = merge_state_machine_history(persisted, messages, u64::MAX);
        let messages = apply_message_window(messages, limit, before);
        let next_before = messages.iter().map(|message| message.timestamp).min();
        Ok(Some(SessionHistoryResult {
            session_id: session_id.to_string(),
            messages,
            limit,
            before,
            next_before,
        }))
    }

    pub(super) async fn get_state_machine_session_history_for_view_impl(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
        human_view: HumanMessageView,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        if human_view.scope == bcs_domain::MessageViewScope::Full {
            return self
                .get_state_machine_session_history(session_id, limit, before)
                .await;
        }
        if limit == 0 {
            return Err(CollaborationRuntimeError::InvalidRequest(
                "history limit must be greater than 0".to_string(),
            ));
        }
        if self.history_persistence_enabled {
            let Some(session) = self.sessions.get(session_id).await.map_err(|error| {
                CollaborationRuntimeError::Internal(ServiceError::InternalError(error.to_string()))
            })? else { return Ok(None); };
            if session.created_at >= self.history_cutoff_timestamp {
                return self.message_store_history(&session, limit, before, Some(human_view)).await;
            }
        }
        let runs = self.runs.list_runs_by_session_id(session_id).await?;
        let Some(latest_run) = runs.first() else {
            return Ok(None);
        };
        let group = self.groups.get(&latest_run.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!(
                "group not found: {}",
                latest_run.group_id
            ))
        })?;
        let mut messages = Vec::new();
        for run in runs {
            messages.extend(
                self.state_machine_messages_from_snapshot(&group, &run, Some(&human_view))
                    .await?,
            );
        }
        let persisted = self.persisted_history_attempts(&group.id, session_id, limit, before, Some(human_view)).await?;
        messages = merge_state_machine_history(persisted, messages, u64::MAX);
        let messages = apply_message_window(messages, limit, before);
        let next_before = messages.iter().map(|message| message.timestamp).min();
        Ok(Some(SessionHistoryResult {
            session_id: session_id.to_string(),
            messages,
            limit,
            before,
            next_before,
        }))
    }
}
