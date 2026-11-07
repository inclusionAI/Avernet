use super::*;
#[async_trait]
impl CollaborationRuntimeService for CollaborationRuntime {
    async fn validate_definition_yaml(
        &self,
        cmd: ValidateCollaborationDefinitionYamlCommand,
    ) -> Result<CollaborationDefinitionValidationOutcome, CollaborationRuntimeError> {
        Ok(validate_authoring_definition_yaml_with_instrumentation(
            cmd, &self.fixed_loop_limits, self.loop_instrumentation.as_deref(),
            self.loop_execution_enabled, self.nested_loop_execution_enabled,
        ))
    }

    async fn get_session_state_machine_permission(
        &self,
        cmd: SessionStateMachinePermissionCommand,
    ) -> Result<SessionStateMachinePermissionView, CollaborationRuntimeError> {
        self.get_session_state_machine_permission_impl(cmd).await
    }

    async fn start_session_state_machine_run(
        &self,
        cmd: StartSessionStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        self.start_session_state_machine_run_impl(cmd).await
    }

    async fn start_state_machine_run(
        &self,
        cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        self.start_state_machine_run_impl(cmd).await
    }

    async fn get_state_machine_run(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        self.run_view(run_id).await
    }

    async fn rerun_state_machine_run(
        &self,
        command: RerunStateMachineCommand,
    ) -> Result<RerunStateMachineOutcome, CollaborationRuntimeError> {
        self.rerun_state_machine_run_impl(command).await
    }

    async fn get_state_machine_run_by_session_id(
        &self,
        session_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run_by_session_id(session_id).await? else {
            return Ok(None);
        };
        self.run_view(&run.run_id).await
    }

    async fn handle_session_human_input(
        &self,
        cmd: HandleSessionHumanInputCommand,
    ) -> Result<HandleSessionHumanInputOutcome, CollaborationRuntimeError> {
        self.handle_session_human_input_impl(cmd).await
    }

    async fn respond_human_node(
        &self,
        cmd: RespondHumanNodeCommand,
    ) -> Result<RespondHumanNodeOutcome, CollaborationRuntimeError> {
        self.respond_human_node_impl(cmd).await
    }
    async fn human_input_notification_is_current(
        &self, run_id: &str, session_id: &str, node_id: &str, deadline_ms: u64,
    ) -> Result<bool, CollaborationRuntimeError> {
        if deadline_ms <= bcs_protocol::now_ms() { return Ok(false); }
        let Some(run) = self.runs.get_run(run_id).await? else { return Ok(false); };
        if run.status != StateMachineRunStatus::Running || run.session_id != session_id { return Ok(false); }
        let Some(node) = self.runs.get_node_run(run_id, node_id).await? else { return Ok(false); };
        if node.status != StateMachineNodeStatus::Running || node.artifact_text.is_some()
            || node.assignee_bot_id.is_some() || node.timeout_deadline_ms != Some(deadline_ms) { return Ok(false); }
        let session = self.sessions.get(session_id).await
            .map_err(|e| CollaborationRuntimeError::InvalidRequest(e.to_string()))?;
        Ok(session.is_some_and(|s| s.group_id == run.group_id && s.status == SessionStatus::Running
            && (s.session_kind != SessionKind::ServiceInvocation || run.session_activation_count == Some(s.activation_count))))
    }

    async fn list_pending_human_nodes(
        &self,
        cmd: ListPendingHumanNodesCommand,
    ) -> Result<Vec<PendingHumanNodeView>, CollaborationRuntimeError> {
        self.list_pending_human_nodes_impl(cmd).await
    }

    async fn get_state_machine_run_for_human(
        &self,
        cmd: HumanRunAccessCommand,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(&cmd.run_id).await? else {
            return Ok(None);
        };
        self.authorize_human_for_run(&run, &cmd.caller_actor_id)
            .await?;
        self.run_view(&cmd.run_id).await
    }

    async fn get_state_machine_run_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(&cmd.run_id).await? else {
            return Ok(None);
        };
        self.authorize_run_access(&run, cmd.authenticated_human.as_ref())
            .await?;
        self.run_view(&cmd.run_id).await
    }

    async fn get_state_machine_node_run_for_human(
        &self,
        cmd: HumanRunAccessCommand,
        node_id: &str,
    ) -> Result<Option<StateMachineNodeRunView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(&cmd.run_id).await? else {
            return Ok(None);
        };
        self.authorize_human_for_run(&run, &cmd.caller_actor_id)
            .await?;
        self.get_state_machine_node_run(&cmd.run_id, node_id).await
    }

    async fn get_state_machine_node_run_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
        node_id: &str,
    ) -> Result<Option<StateMachineNodeRunView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(&cmd.run_id).await? else {
            return Ok(None);
        };
        self.authorize_run_access(&run, cmd.authenticated_human.as_ref())
            .await?;
        self.get_state_machine_node_run(&cmd.run_id, node_id).await
    }

    async fn get_state_machine_run_graph_for_human(
        &self,
        cmd: HumanRunAccessCommand,
    ) -> Result<Option<StateMachineRunGraphView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(&cmd.run_id).await? else {
            return Ok(None);
        };
        self.authorize_human_for_run(&run, &cmd.caller_actor_id)
            .await?;
        self.get_state_machine_run_graph(&cmd.run_id).await
    }

    async fn get_state_machine_run_graph_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
    ) -> Result<Option<StateMachineRunGraphView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(&cmd.run_id).await? else {
            return Ok(None);
        };
        self.authorize_run_access(&run, cmd.authenticated_human.as_ref())
            .await?;
        self.get_state_machine_run_graph(&cmd.run_id).await
    }

    async fn cancel_state_machine_run_for_human(
        &self,
        cmd: HumanRunAccessCommand,
        reason: Option<String>,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        self.cancel_state_machine_run_for_human_impl(cmd, reason).await
    }

    async fn cancel_state_machine_run_with_access(
        &self,
        cmd: StateMachineRunAccessCommand,
        reason: Option<String>,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        self.cancel_state_machine_run_with_access_impl(cmd, reason).await
    }

    async fn get_state_machine_node_run(
        &self,
        run_id: &str,
        node_id: &str,
    ) -> Result<Option<StateMachineNodeRunView>, CollaborationRuntimeError> {
        self.get_state_machine_node_run_impl(run_id, node_id).await
    }

    async fn get_state_machine_run_graph(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineRunGraphView>, CollaborationRuntimeError> {
        let Some(run) = self.runs.get_run(run_id).await? else {
            return Ok(None);
        };
        let loaded = self.load_run_definition_for_view(&run).await?;
        let nodes = self.runs.list_node_runs(run_id).await?;
        Ok(Some(run_graph_view(run, nodes, &loaded.execution, &loaded.definition)?))
    }

    async fn get_state_machine_session_history(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        self.get_state_machine_session_history_impl(session_id, limit, before).await
    }

    async fn get_state_machine_session_history_for_view(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
        human_view: HumanMessageView,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        self.get_state_machine_session_history_for_view_impl(session_id, limit, before, human_view).await
    }

    async fn cancel_state_machine_run(
        &self,
        cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        self.cancel_state_machine_run_impl(cmd).await
    }
    async fn lookup_delivery_correlation(
        &self,
        run_id: &str,
    ) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> {
        Ok(self.runs.lookup_delivery_correlation(run_id).await?)
    }

    async fn register_delivery_alias(
        &self,
        delivery_request_id: &str,
        bot_delivery_run_id: String,
    ) -> Result<(), CollaborationRuntimeError> {
        self.runs
            .register_delivery_alias(delivery_request_id, bot_delivery_run_id)
            .await?;
        Ok(())
    }

    async fn handle_bot_terminal_event(
        &self,
        cmd: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
        self.handle_bot_terminal_event_impl(cmd).await
    }

    async fn process_expired_node_timeouts(
        &self,
        limit: usize,
        timeout_grace_ms: u64,
    ) -> Result<usize, CollaborationRuntimeError> {
        self.process_expired_node_timeouts_impl(limit, timeout_grace_ms).await
    }

    async fn recover_state_machine_history(&self, cursor: Option<String>, limit: usize)
        -> Result<bcs_service_api::StateMachineProgressionRecoveryPage, CollaborationRuntimeError> {
        self.recover_history_page(cursor, limit).await
    }

    async fn recover_state_machine_progression(
        &self,
        after_run_id: Option<String>,
        limit: usize,
    ) -> Result<bcs_service_api::StateMachineProgressionRecoveryPage, CollaborationRuntimeError> {
        self.recover_state_machine_progression_impl(after_run_id, limit).await
    }

    async fn cleanup_state_machine_terminal_work(&self, after_run_id: Option<String>, limit: usize)
        -> Result<bcs_service_api::StateMachineProgressionRecoveryPage, CollaborationRuntimeError> {
        self.cleanup_state_machine_terminal_work_impl(after_run_id, limit).await
    }

    async fn recover_state_machine_terminal_im(&self, after_run_id: Option<String>, limit: usize)
        -> Result<bcs_service_api::StateMachineProgressionRecoveryPage, CollaborationRuntimeError> {
        if limit == 0 { return Ok(Default::default()); }
        let ids = self.runs.list_terminal_im_pending(after_run_id.as_deref(), limit).await?;
        let mut page = bcs_service_api::StateMachineProgressionRecoveryPage { scanned: ids.len(),
            next_run_id: if ids.len() == limit { ids.last().cloned() } else { None }, ..Default::default() };
        for run_id in ids {
            match self.resume_terminal_im(&run_id).await {
                Ok(()) => page.reconciled += 1,
                Err(error) => page.failures.push(bcs_service_api::StateMachineProgressionRecoveryFailure { run_id, error: error.to_string() }),
            }
        }
        Ok(page)
    }

    async fn recover_state_machine_sessions(
        &self,
        after_session_id: Option<String>,
        limit: usize,
    ) -> Result<bcs_service_api::StateMachineSessionRecoveryPage, CollaborationRuntimeError> {
        self.recover_state_machine_sessions_impl(after_session_id, limit).await
    }

    async fn upsert_definition(
        &self,
        definition: CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError> {
        reject_explicit_participant_roles(&definition)?;
        let compiled = self.compile_for_execution(definition)?;
        self.definitions.upsert(compiled.definition).await?;
        Ok(())
    }

    async fn upsert_definition_with_source_yaml(
        &self,
        definition: CollaborationDefinition,
        source_yaml: String,
    ) -> Result<(), CollaborationRuntimeError> {
        reject_authoring_yaml_identity(&source_yaml)?;
        validate_v2_definition_input(serde_yaml::from_str(&source_yaml)
            .map_err(|error| CollaborationRuntimeError::InvalidDefinition(error.to_string()))?)?;
        reject_explicit_participant_roles(&definition)?;
        let compiled = self.compile_for_execution(definition)?;
        self.definitions
            .upsert_with_source_yaml(compiled.definition, source_yaml)
            .await?;
        Ok(())
    }

    async fn get_group_collaboration_definition(
        &self,
        group_id: &str,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        self.group_collaboration_definition_view(group_id).await
    }

    async fn cancel_session_runs(
        &self,
        session_id: &str,
        reason: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        self.cancel_session_runs_impl(session_id, reason).await
    }

    async fn cancel_group_runs(
        &self,
        group_id: &str,
        reason: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        self.cancel_group_runs_impl(group_id, reason).await
    }

    async fn delete_group_runtime_state(
        &self,
        group_id: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        self.delete_group_runtime_state_impl(group_id).await
    }

    async fn patch_group_collaboration_definition(
        &self,
        cmd: PatchGroupCollaborationDefinitionCommand,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        self.patch_group_collaboration_definition_impl(cmd).await
    }

    async fn upgrade_group_collaboration_definition(
        &self,
        cmd: UpgradeGroupCollaborationDefinitionCommand,
    ) -> Result<GroupCollaborationDefinitionView, CollaborationRuntimeError> {
        self.upgrade_group_collaboration_definition_impl(cmd).await
    }

    async fn configure_group_runtime(
        &self,
        cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        self.configure_group_runtime_impl(cmd).await
    }

    fn caller_orchestration_wired(&self) -> bool {
        self.caller_instance_registrar.is_some() || self.caller_node_dispatch.is_some()
    }
}
