use super::*;

#[tokio::test]
async fn state_machine_service_invocation_starts_run_with_created_session() {
    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-1",
        "bot-manager",
        vec![Participant::bot("bot-manager", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    group.version = 7;
    group_store.upsert(group).await.unwrap();

    let sessions = Arc::new(RecordingSessionManagement::default());
    let collaboration = Arc::new(RecordingCollaborationRuntime::default());
    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = sessions.clone();
    services.collaboration_runtime = collaboration.clone();

    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/services/group-1/sessions")
                .header("content-type", "application/json")
                .header("X-BCS-Service-Key", "test-key")
                .body(Body::from(
                    json!({
                        "caller_id": "trace-1",
                        "input": {"task": "workflow"}
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::ACCEPTED);
    let body: serde_json::Value = serde_json::from_slice(
        &to_bytes(response.into_body(), usize::MAX).await.unwrap(),
    )
    .unwrap();
    assert_eq!(body["session_id"], "group-1:abcdef12");
    assert_eq!(body["state_machine_run_id"], "sm-http-test");

    let session_commands = sessions.commands.lock().await;
    assert_eq!(session_commands[0].params.session_kind, SessionKind::ServiceInvocation);
    assert_eq!(session_commands[0].params.group_version, Some(7));

    let run_commands = collaboration.start_commands.lock().await;
    assert_eq!(run_commands.len(), 1);
    assert_eq!(run_commands[0].group_id, "group-1");
    assert_eq!(
        run_commands[0].session_id.as_deref(),
        Some("group-1:abcdef12")
    );
    assert!(run_commands[0].definition_yaml.is_none());
    assert!(run_commands[0].definition_ref.is_none());
}
#[derive(Default)]
struct RecordingCollaborationRuntime {
    start_commands: Mutex<Vec<StartStateMachineRunCommand>>,
}

#[async_trait::async_trait]
impl CollaborationRuntimeService for RecordingCollaborationRuntime {
    async fn start_state_machine_run(
        &self,
        cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        self.start_commands.lock().await.push(cmd.clone());
        Ok(StartStateMachineRunOutcome {
            view: StateMachineRunView {
                run: StateMachineRun {
                    run_id: "sm-http-test".to_string(),
                    root_run_id: Some("sm-http-test".to_string()),
                    rerun_of: None,
                    definition_id: "sm_e2e_single".to_string(),
                    definition_version: 1,
                    group_id: cmd.group_id,
                    group_version: 1,
                    session_id: cmd.session_id.unwrap_or_else(|| "group-1:abcdef12".to_string()),
                    session_activation_count: None,
                    created_by: cmd.caller_id.clone(),
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
    ) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> {
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
        _definition: CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn configure_group_runtime(
        &self,
        _cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest(
            "not used by this test".to_string(),
        ))
    }
}
