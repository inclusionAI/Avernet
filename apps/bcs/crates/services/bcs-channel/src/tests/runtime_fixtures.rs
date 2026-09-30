use super::*;

#[derive(Default)]
pub(super) struct RecordingCollaborationRuntime {
    pub(super) stale_human_runs: Mutex<std::collections::HashSet<String>>,
    pub(super) starts: Mutex<Vec<StartStateMachineRunCommand>>,
    pub(super) start_error: Mutex<Option<String>>,
    pub(super) runs_by_session: Mutex<HashMap<String, StateMachineRunView>>,
    pub(super) cancelled_sessions: Mutex<Vec<(String, String)>>,
    pub(super) cancel_error: Mutex<Option<String>>,
    pub(super) pending_human_nodes: Mutex<Vec<PendingHumanNodeView>>,
    pub(super) human_responses: Mutex<Vec<RespondHumanNodeCommand>>,
}

#[async_trait]
impl CollaborationRuntimeService for RecordingCollaborationRuntime {
    async fn human_input_notification_is_current(&self, run_id: &str, _: &str, _: &str, _: u64) -> Result<bool, CollaborationRuntimeError> {
        Ok(!self.stale_human_runs.lock().await.contains(run_id))
    }

    async fn start_state_machine_run(
        &self,
        cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        let session_id = match cmd.session_id.clone() {
            Some(session_id) => session_id,
            None => "state_session".to_string(),
        };
        self.starts.lock().await.push(cmd.clone());
        if let Some(error) = self.start_error.lock().await.clone() {
            return Err(CollaborationRuntimeError::InvalidRequest(error));
        }
        let view = StateMachineRunView {
            run: StateMachineRun {
                run_id: "state_run_1".to_string(),
                root_run_id: Some("state_run_1".to_string()),
                rerun_of: None,
                definition_id: "definition_1".to_string(),
                definition_version: 1,
                group_id: cmd.group_id,
                group_version: 1,
                session_id,
                session_activation_count: None,
                created_by: cmd.caller_id,
                status: StateMachineRunStatus::Running,
                input: cmd.input,
                opening_message_override: None,
                output: None,
                error: None,
                created_at: 1,
                updated_at: 1,
                completed_at: None,
            },
            nodes: Vec::new(),
            node_execution_metadata: None,
            judge_outputs: Vec::new(),
        };
        self.runs_by_session
            .lock()
            .await
            .insert(view.run.session_id.clone(), view.clone());
        Ok(StartStateMachineRunOutcome { view })
    }

    async fn get_state_machine_run_by_session_id(
        &self,
        session_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(self.runs_by_session.lock().await.get(session_id).cloned())
    }

    async fn cancel_session_runs(
        &self,
        session_id: &str,
        reason: &str,
    ) -> Result<(), CollaborationRuntimeError> {
        if let Some(error) = self.cancel_error.lock().await.clone() {
            return Err(CollaborationRuntimeError::InvalidRequest(error));
        }
        self.cancelled_sessions
            .lock()
            .await
            .push((session_id.to_string(), reason.to_string()));
        Ok(())
    }

    async fn list_pending_human_nodes(
        &self,
        _cmd: ListPendingHumanNodesCommand,
    ) -> Result<Vec<PendingHumanNodeView>, CollaborationRuntimeError> {
        Ok(self.pending_human_nodes.lock().await.clone())
    }

    async fn respond_human_node(
        &self,
        cmd: RespondHumanNodeCommand,
    ) -> Result<RespondHumanNodeOutcome, CollaborationRuntimeError> {
        self.human_responses.lock().await.push(cmd.clone());
        Ok(RespondHumanNodeOutcome {
            node: StateMachineNodeRun {
                run_id: cmd.run_id.clone(),
                node_id: cmd.node_id,
                status: StateMachineNodeStatus::Completed,
                attempt: 0,
                node_timeout_ms: Some(60_000),
                timeout_deadline_ms: None,
                max_attempts: 1,
                assignee_bot_id: None,
                outcome: Some("complete".to_string()),
                responded_by: Some(cmd.caller_actor_id),
                delivery_request_id: None,
                bot_delivery_run_id: None,
                artifact_text: Some(cmd.content),
                error: None,
                started_at: Some(1),
                completed_at: Some(2),
            },
            run: StateMachineRun {
                run_id: cmd.run_id,
                root_run_id: Some("state_run_1".to_string()),
                rerun_of: None,
                definition_id: "definition_1".to_string(),
                definition_version: 1,
                group_id: "group_sm".to_string(),
                group_version: 1,
                session_id: "state_session".to_string(),
                session_activation_count: None,
                created_by: None,
                status: StateMachineRunStatus::Running,
                input: serde_json::Value::Null,
                opening_message_override: None,
                output: None,
                error: None,
                created_at: 1,
                updated_at: 2,
                completed_at: None,
            },
        })
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn get_state_machine_session_history(
        &self,
        _session_id: &str,
        _limit: u64,
        _before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn cancel_state_machine_run(
        &self,
        cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::RunNotFound(cmd.run_id))
    }

    async fn lookup_delivery_correlation(
        &self,
        _run_id: &str,
    ) -> Result<Option<bcs_domain::StateMachineDeliveryCorrelation>, CollaborationRuntimeError>
    {
        Ok(None)
    }

    async fn register_delivery_alias(
        &self,
        _delivery_request_id: &str,
        _bot_delivery_run_id: String,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn handle_bot_terminal_event(
        &self,
        _cmd: HandleBotTerminalEventCommand,
    ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
        Ok(HandleBotTerminalEventOutcome {
            consumed: false,
            view: None,
        })
    }

    async fn upsert_definition(
        &self,
        _definition: bcs_domain::CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn configure_group_runtime(
        &self,
        _cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest(
            "Recording runtime does not configure groups".to_string(),
        ))
    }
}

#[derive(Default)]
pub(super) struct RecordingRegistry {
    pub(super) ensured: Mutex<Vec<(String, String)>>,
    pub(super) fail_ensure_human: Mutex<Option<String>>,
}

#[async_trait]
impl BotRegistryCoreService for RecordingRegistry {
    async fn register(
        &self,
        _bot_id: String,
        _capabilities: BotCapabilities,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str) -> bool {
        false
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        Some(registered_bot(bot_id))
    }

    async fn get_agent_credentials(&self, _bot_id: &str) -> Option<AgentCredentials> {
        None
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn discover(&self, _query: &str) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_skills(&self, _skills: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_domains(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn find_by_scopes(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
        Vec::new()
    }

    async fn unregister(&self, _bot_id: &str) -> bool {
        false
    }

    async fn cleanup_expired(&self) {}

    async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
        None
    }

    async fn save_to_storage(
        &self,
        _bot_id: &str,
        _caps: &BotCapabilities,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_visibility(&self, _bot_id: &str, _visibility: &str) -> ServiceResult<()> {
        Ok(())
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn ensure_human_actor(
        &self,
        staff_no: &str,
        nick_name: &str,
    ) -> ServiceResult<EnsureHumanResult> {
        if let Some(error) = self.fail_ensure_human.lock().await.clone() {
            return Err(ServiceError::InternalError(error));
        }
        self.ensured
            .lock()
            .await
            .push((staff_no.to_string(), nick_name.to_string()));
        Ok(EnsureHumanResult { created: true })
    }

    async fn has_been_onboarded(&self, _bot_id: &str) -> bool {
        true
    }

    async fn save_created_by(
        &self,
        _bot_id: &str,
        _created_by: &str,
        _overwrite: bool,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn save_token(&self, _bot_id: &str, _token: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn load_token(&self, _bot_id: &str) -> Option<String> {
        None
    }

    async fn find_bot_by_token(&self, _token: &str) -> Option<String> {
        None
    }

    async fn register_streaming_connection(&self, _bot_id: String) -> Result<String, ()> {
        Err(())
    }

    async fn reconnect_streaming(
        &self,
        _existing_token: String,
    ) -> Result<(String, String), ()> {
        Err(())
    }

    async fn disconnect_streaming(&self, _bot_id: &str) {}

    async fn is_connected(&self, _bot_id: &str) -> bool {
        false
    }

    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Err(())
    }

    async fn list_connected(&self) -> Vec<String> {
        Vec::new()
    }

    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

    async fn register_http_connection(&self, _bot_id: String, token: String) -> String {
        token
    }

    async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
        Ok(BotDeliveryTarget::WebSocket {
            bot_id: bot_id.to_string(),
        })
    }
}

#[tokio::test]
async fn recording_registry_update_status_rejects_heartbeat_renewal() {
    let registry = Arc::new(RecordingRegistry::default());
    assert!(!registry.update_status("heartbeat-renewal-unsupported").await);
}
