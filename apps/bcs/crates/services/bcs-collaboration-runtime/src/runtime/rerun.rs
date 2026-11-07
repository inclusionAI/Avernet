use super::*;
fn build_rerun_node_runs(
    run: &StateMachineRun,
    source_nodes: Vec<StateMachineNodeRun>,
) -> Vec<StateMachineNodeRun> {
    source_nodes
        .into_iter()
        .map(|source| StateMachineNodeRun {
            run_id: run.run_id.clone(),
            node_id: source.node_id,
            status: StateMachineNodeStatus::Pending,
            attempt: 0,
            node_timeout_ms: source.node_timeout_ms,
            timeout_deadline_ms: None,
            max_attempts: source.max_attempts,
            assignee_bot_id: source.assignee_bot_id,
            outcome: None,
            responded_by: None,
            delivery_request_id: None,
            bot_delivery_run_id: None,
            artifact_text: None,
            error: None,
            started_at: None,
            completed_at: None,
        })
        .collect()
}impl CollaborationRuntime {

    pub(super) async fn resume_materialized_rerun(
        &self,
        compiled: &CompiledStateMachine,
        group: &Group,
        mut run: StateMachineRun,
        opening_message: &RenderedOpeningMessage,
        created: bool,
    ) -> Result<RerunStateMachineOutcome, CollaborationRuntimeError> {
        if !matches!(run.status, StateMachineRunStatus::Pending | StateMachineRunStatus::Running) {
            let view = self.run_view(&run.run_id).await?.ok_or_else(|| CollaborationRuntimeError::RunNotFound(run.run_id.clone()))?;
            return Ok(RerunStateMachineOutcome { view, created });
        }
        if let Err(error) = self.save_opening_payload(&run, opening_message).await {
            self.fail_run(&run, error.to_string()).await?;
            return Err(error);
        }
        self.ensure_materialized_run_started(&mut run).await?;

        if run.status != StateMachineRunStatus::Running {
            let view = self
                .run_view(&run.run_id)
                .await?
                .ok_or_else(|| CollaborationRuntimeError::RunNotFound(run.run_id.clone()))?;
            return Ok(RerunStateMachineOutcome { view, created });
        }

        if let Err(error) = self
            .persist_saved_opening(&run)
            .await
        {
            self.fail_run(&run, error.to_string()).await?;
            return Err(error);
        }
        self.publish_state_machine_panel_event(group, &run, opening_message)
            .await;
        for node_id in &compiled.initial_nodes {
            self.dispatch_node(compiled, group, &run, node_id).await?;
        }
        let view = self
            .run_view(&run.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(run.run_id.clone()))?;
        Ok(RerunStateMachineOutcome { view, created })
    }

    pub(super) async fn start_session_state_machine_run_impl(
        &self,
        cmd: StartSessionStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        let permission = self
            .get_session_state_machine_permission(SessionStateMachinePermissionCommand {
                session_id: cmd.session_id.clone(),
                caller_bot_id: cmd.caller_bot_id.clone(),
            })
            .await?;
        if !permission.allowed {
            return Err(CollaborationRuntimeError::Forbidden(format!(
                "{}: {}",
                permission.reason_code, permission.message
            )));
        }

        let validation =
            validate_authoring_definition_yaml_with_instrumentation(ValidateCollaborationDefinitionYamlCommand {
                definition_yaml: cmd.definition_yaml.clone(),
                judge_available: cmd.judge_available,
            }, &self.fixed_loop_limits, self.loop_instrumentation.as_deref(),
                self.loop_execution_enabled, self.nested_loop_execution_enabled);
        if !validation.valid {
            let details = validation
                .errors
                .iter()
                .map(|error| format!("{} {}: {}", error.code, error.path, error.message))
                .collect::<Vec<_>>()
                .join("; ");
            return Err(CollaborationRuntimeError::InvalidDefinition(details));
        }

        self.start_state_machine_run(StartStateMachineRunCommand {
            group_id: permission.group_id,
            session_id: Some(cmd.session_id),
            definition_yaml: Some(cmd.definition_yaml),
            definition: None,
            definition_ref: None,
            participant_bindings: Some(cmd.participant_bindings),
            opening_message_override: cmd.opening_message,
            input: cmd.input,
            caller_id: Some(cmd.caller_bot_id),
            authenticated_human: None,
            caller_association: cmd.caller_association,
        })
        .await
    }

    pub(super) async fn rerun_state_machine_run_impl(
        &self,
        command: RerunStateMachineCommand,
    ) -> Result<RerunStateMachineOutcome, CollaborationRuntimeError> {
        let source = self
            .runs
            .get_run(&command.source_run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(command.source_run_id.clone()))?;
        self.authorize_run_access(&source, command.authenticated_human.as_ref())
            .await?;
        if source.status != StateMachineRunStatus::Failed {
            return Err(CollaborationRuntimeError::Conflict(
                "source state-machine Run must be failed before rerun".to_string(),
            ));
        }
        let session = self
            .sessions
            .get(&source.session_id)
            .await
            .map_err(|error| CollaborationRuntimeError::InvalidRequest(error.to_string()))?
            .ok_or_else(|| {
                CollaborationRuntimeError::InvalidRequest(format!(
                    "session not found: {}",
                    source.session_id
                ))
            })?;
        if session.group_id != source.group_id {
            return Err(CollaborationRuntimeError::Forbidden(
                "source Run Session no longer belongs to its Group".to_string(),
            ));
        }
        let group = self.groups.get(&source.group_id).await.ok_or_else(|| {
            CollaborationRuntimeError::InvalidRequest(format!(
                "group not found: {}",
                source.group_id
            ))
        })?;
        let definition = self
            .definitions
            .get_run_snapshot(&source.run_id)
            .await?
            .ok_or_else(|| {
                CollaborationRuntimeError::Conflict(
                    "source state-machine Run has no immutable snapshot".to_string(),
                )
            })?;
        let loaded = crate::snapshot::load_state_machine_snapshot(definition)?;
        if loaded.plan.is_some() && !self.loop_execution_enabled {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "v2 rerun execution is disabled until runtime and recovery support is enabled".into(),
            ));
        }
        if loaded.plan.as_ref().is_some_and(|plan|
            plan.compiler_version == crate::fixed_loop::NESTED_FIXED_LOOP_COMPILER_VERSION)
            && !self.nested_loop_execution_enabled
        {
            return Err(CollaborationRuntimeError::InvalidDefinition(
                "state_machine.loop.nesting rerun admission is not enabled".into(),
            ));
        }
        let compiled = loaded.execution;
        if let Some(existing) = self.runs.get_direct_rerun(&source.run_id).await? {
            let opening_message = self.opening_message_for_existing_run(&existing).await?;
            return self
                .resume_materialized_rerun(&compiled, &group, existing, &opening_message, false)
                .await;
        }
        let source_nodes = self.runs.list_node_runs(&source.run_id).await?;
        if let Some(plan) = &compiled.execution_plan {
            let source_ids: BTreeSet<_> = source_nodes.iter().map(|node| &node.node_id).collect();
            let plan_ids: BTreeSet<_> = plan.state_machine.nodes.keys().collect();
            if source_ids != plan_ids || source_nodes.len() != plan_ids.len() {
                return Err(CollaborationRuntimeError::Internal(ServiceError::InternalError(
                    "v2 rerun source nodes do not match the immutable execution plan".into(),
                )));
            }
        }
        let run_id = format!("sm-{}", Uuid::new_v4());
        let now = bcs_protocol::now_ms();
        let service_session = session.session_kind == SessionKind::ServiceInvocation;
        let run = StateMachineRun {
            run_id: run_id.clone(),
            root_run_id: Some(
                source
                    .root_run_id
                    .clone()
                    .unwrap_or_else(|| source.run_id.clone()),
            ),
            rerun_of: Some(source.run_id.clone()),
            definition_id: source.definition_id.clone(),
            definition_version: source.definition_version,
            group_id: source.group_id.clone(),
            group_version: source.group_version,
            session_id: source.session_id.clone(),
            session_activation_count: service_session
                .then_some(session.activation_count.saturating_add(1)),
            created_by: command
                .authenticated_human
                .as_ref()
                .map(|human| human.actor_id.clone())
                .or_else(|| source.created_by.clone()),
            status: StateMachineRunStatus::Pending,
            input: source.input.clone(),
            opening_message_override: source.opening_message_override.clone(),
            output: None,
            error: None,
            created_at: now,
            updated_at: now,
            completed_at: None,
        };
        let nodes = build_rerun_node_runs(&run, source_nodes);
        let opening_message =
            render_state_machine_opening_message(&group, &run, session.session_title.as_deref())?;
        let (run, opening_message, created) = match self
            .runs
            .create_rerun_if_session_idle(CreateStateMachineRerun {
                source_run_id: source.run_id.clone(),
                run: run.clone(),
                nodes,
                reactivate_service_session: service_session,
            })
            .await?
        {
            CreateStateMachineRerunOutcome::Existing(existing) => {
                let opening_message = self.opening_message_for_existing_run(&existing).await?;
                (existing, opening_message, false)
            }
            CreateStateMachineRerunOutcome::Conflict => {
                return Err(CollaborationRuntimeError::Conflict(
                    "state-machine Session has an active Run or cannot be reactivated".to_string(),
                ));
            }
            CreateStateMachineRerunOutcome::Created => (run, opening_message, true),
        };
        self.resume_materialized_rerun(&compiled, &group, run, &opening_message, created)
            .await
    }
}
