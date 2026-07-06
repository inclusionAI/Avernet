use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_bot::BotCore;
use bcs_group::GroupStore;
use bcs_http::{
    router::build_router,
    state::HttpAppState,
};
use bcs_http::service_key::{ApiKeyEntry, ApiKeyRegistry, caller_principal_for, sha256_hex};
use bcs_service_api::{
    BotRegistryCoreService, CancelStateMachineRunCommand, CollaborationDefinition,
    CollaborationRuntimeError, CollaborationRuntimeService, ConfigureGroupRuntimeCommand,
    ConfigureGroupRuntimeOutcome, CreateOrReactivateCommand, CreateOrReactivateOutcome, Group,
    GroupCoreService, GroupStrategy, HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome,
    NewSessionParams, Participant, ParticipantMode, ParticipantRole, ServiceSpec, Session,
    SessionKind, SessionManagementService, SessionHistoryResult, SessionStatus,
    SessionUseCaseError, StartStateMachineRunCommand, StartStateMachineRunOutcome,
    StateMachineDeliveryCorrelation, StateMachineRun,
    StateMachineRunStatus, StateMachineRunView,
};
use bcs_services_container::Services;
use serde_json::json;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

#[tokio::test]
async fn service_invocation_uses_api_key_and_seeds_group_session_fields() {
    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-1",
        "bot-manager",
        vec![
            Participant::bot("bot-manager", ParticipantRole::Manager),
            Participant::bot("bot-worker", ParticipantRole::Worker),
        ],
    );
    group.service_spec = Some(ServiceSpec {
        callback_config: None,
        timeout_seconds: None,
        max_concurrency: Some(2),
    });
    group_store.upsert(group).await.unwrap();

    let sessions = Arc::new(RecordingSessionManagement::default());
    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = sessions.clone();

    let raw_key = "secret";
    let hash = sha256_hex(raw_key);
    let app = build_router(
        HttpAppState::new(services).with_service_api_keys(Arc::new(ApiKeyRegistry::new(vec![
            ApiKeyEntry {
                name: "svc-a".to_string(),
                sha256: hash.clone(),
                bound_groups: vec!["group-1".to_string()],
            },
        ]))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/services/group-1/sessions")
                .header("content-type", "application/json")
                .header("X-BCS-Service-Key", raw_key)
                .body(Body::from(
                    json!({
                        "caller_id": "trace-1",
                        "session_title": "性能审计",
                        "input": {"task": "audit"},
                        "meta": {"k": "v"}
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

    let commands = sessions.commands.lock().await;
    assert_eq!(commands.len(), 1);
    let command = &commands[0];
    assert_eq!(command.group_id, "group-1");
    assert_eq!(command.session_id, None);
    assert_eq!(command.params.session_kind, SessionKind::ServiceInvocation);
    assert_eq!(command.params.participants.len(), 2);
    assert_eq!(command.params.group_version, Some(1));
    assert_eq!(command.params.caller_id.as_deref(), Some("trace-1"));
    assert_eq!(
        command.params.caller_principal.as_deref(),
        Some(caller_principal_for(&hash).as_str())
    );
    assert_eq!(command.params.created_by, command.params.caller_principal);
    assert_eq!(command.params.session_title.as_deref(), Some("性能审计"));
}

#[tokio::test]
async fn service_invocation_without_api_key_uses_bot_identity() {
    let tmp = TempDir::new().unwrap();
    let bot_registry = Arc::new(BotCore::with_base_dir(tmp.path().to_path_buf()));
    bot_registry
        .store_token_mapping("bot-token".to_string(), "bot-manager".to_string())
        .await;

    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-1",
        "bot-manager",
        vec![Participant::bot("bot-manager", ParticipantRole::Manager)],
    );
    group.service_spec = Some(ServiceSpec {
        callback_config: None,
        timeout_seconds: None,
        max_concurrency: Some(2),
    });
    group_store.upsert(group).await.unwrap();

    let sessions = Arc::new(RecordingSessionManagement::default());
    let mut services = Services::noop();
    services.registry = bot_registry;
    services.group = group_store;
    services.session_management = sessions.clone();

    let app = build_router(
        HttpAppState::new(services).with_service_api_keys(Arc::new(ApiKeyRegistry::new(vec![
            ApiKeyEntry {
                name: "svc-a".to_string(),
                sha256: sha256_hex("secret"),
                bound_groups: vec!["group-1".to_string()],
            },
        ]))),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/services/group-1/sessions")
                .header("content-type", "application/json")
                .header("Authorization", "Bearer bot-token")
                .body(Body::from(json!({"input": {"task": "audit"}}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::ACCEPTED);
    let commands = sessions.commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(
        commands[0].params.caller_principal.as_deref(),
        Some("bot:bot-manager")
    );
    assert_eq!(commands[0].params.created_by.as_deref(), Some("bot:bot-manager"));
}

#[tokio::test]
async fn service_invocation_without_api_key_or_bot_identity_is_rejected() {
    let app = build_router(HttpAppState::new(Services::noop()));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/services/group-1/sessions")
                .header("content-type", "application/json")
                .body(Body::from(json!({"input": {"task": "audit"}}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let body: serde_json::Value = serde_json::from_slice(
        &to_bytes(response.into_body(), usize::MAX).await.unwrap(),
    )
    .unwrap();
    assert_eq!(body["error"], "missing_bot_identity");
}

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
    group.service_spec = Some(ServiceSpec {
        callback_config: None,
        timeout_seconds: None,
        max_concurrency: Some(2),
    });
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
struct RecordingSessionManagement {
    commands: Mutex<Vec<CreateOrReactivateCommand>>,
}

#[async_trait::async_trait]
impl SessionManagementService for RecordingSessionManagement {
    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        let session = session_from_params("group-1:abcdef12", &cmd.group_id, &cmd.params);
        self.commands.lock().await.push(cmd);
        Ok(CreateOrReactivateOutcome {
            session,
            created: true,
        })
    }

    async fn get(&self, _session_id: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn belongs_to_group(
        &self,
        _session_id: &str,
        _group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(false)
    }

    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
        Ok(0)
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
        Ok(())
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> { Ok(false) }
}

fn session_from_params(id: &str, group_id: &str, params: &NewSessionParams) -> Session {
    Session {
        id: id.to_string(),
        group_id: group_id.to_string(),
        session_title: params.session_title.clone(),
        env: None,
        status: SessionStatus::Running,
        session_kind: params.session_kind,
        participants: params.participants.clone(),
        group_version: params.group_version,
        caller_id: params.caller_id.clone(),
        input: params.input.clone(),
        output: None,
        error_message: None,
        callback_status: Some("pending".to_string()),
        activation_count: 1,
        caller_principal: params.caller_principal.clone(),
        created_by: params.created_by.clone(),
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: params.meta.clone(),
        current_msg_seq: 0,
        participant_join_seq: None,
    }
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
                    definition_id: "sm_e2e_single".to_string(),
                    definition_version: 1,
                    group_id: cmd.group_id,
                    group_version: 1,
                    session_id: cmd.session_id.unwrap_or_else(|| "group-1:abcdef12".to_string()),
                    created_by: cmd.caller_id.clone(),
                    status: StateMachineRunStatus::Running,
                    input: cmd.input,
                    output: None,
                    error: None,
                    created_at: 1,
                    updated_at: 1,
                    completed_at: None,
                },
                nodes: Vec::new(),
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

// ----- get_service_session caller_principal isolation -----
//
// Legacy server.rs:13648-13658 enforces that only the original caller can
// read invocation details: `sess.caller_principal` must match
// `caller.caller_principal`, otherwise 403. Two distinct svc-keys both
// bound to the same group must NOT be able to read each other's sessions.

#[derive(Default)]
struct StaticSessionStore {
    session: Mutex<Option<Session>>,
}

#[async_trait::async_trait]
impl SessionManagementService for StaticSessionStore {
    async fn create_or_reactivate(
        &self,
        _cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        unimplemented!("not used by these tests")
    }

    async fn get(&self, sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self.session.lock().await.as_ref().filter(|s| s.id == sid).cloned())
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(self
            .session
            .lock()
            .await
            .as_ref()
            .map(|s| s.id == session_id && s.group_id == group_id)
            .unwrap_or(false))
    }

    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
        Ok(0)
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
        Ok(())
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> { Ok(false) }
}

#[tokio::test]
async fn get_service_session_rejects_cross_caller_principal_access() {
    use bcs_service_api::ParticipantRole;
    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-1",
        "bot-manager",
        vec![Participant::bot("bot-manager", ParticipantRole::Manager)],
    );
    group.service_spec = Some(ServiceSpec {
        callback_config: None,
        timeout_seconds: None,
        max_concurrency: None,
    });
    group_store.upsert(group).await.unwrap();

    // Two svc-keys, both bound to "group-1".
    let raw_a = "key-a";
    let raw_b = "key-b";
    let hash_a = sha256_hex(raw_a);
    let hash_b = sha256_hex(raw_b);

    // A session created by caller A.
    let session = Session {
        id: "group-1:abcdef12".to_string(),
        group_id: "group-1".to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::ServiceInvocation,
        participants: vec![Participant::bot("bot-manager", ParticipantRole::Manager)],
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: Some("pending".to_string()),
        activation_count: 1,
        caller_principal: Some(caller_principal_for(&hash_a)),
        created_by: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
    };

    let sessions = Arc::new(StaticSessionStore {
        session: Mutex::new(Some(session)),
    });

    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = sessions.clone();

    let app = build_router(
        HttpAppState::new(services).with_service_api_keys(Arc::new(ApiKeyRegistry::new(vec![
            ApiKeyEntry {
                name: "svc-a".to_string(),
                sha256: hash_a.clone(),
                bound_groups: vec!["group-1".to_string()],
            },
            ApiKeyEntry {
                name: "svc-b".to_string(),
                sha256: hash_b.clone(),
                bound_groups: vec!["group-1".to_string()],
            },
        ]))),
    );

    // Caller B reads A's session → must be 403, not 200.
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/services/group-1/sessions/group-1:abcdef12")
                .header("X-BCS-Service-Key", raw_b)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // Caller A reads its own session → 200.
    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/services/group-1/sessions/group-1:abcdef12")
                .header("X-BCS-Service-Key", raw_a)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
}

// ----- post_invocation reactivate semantics (fix #9) -----
//
// Legacy server.rs:12529-12535: reactivating a service-invocation session
// that is still Running returns 409 `session_is_running_cannot_invoke`,
// not 400. The use-case layer must surface this as Conflict, not
// InvalidParams, so the HTTP layer can map it to 409.

#[derive(Default)]
struct ReactivateConflictSessions {
    session: Mutex<Option<Session>>,
}

#[async_trait::async_trait]
impl SessionManagementService for ReactivateConflictSessions {
    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        // Simulate the real session core: reactivating a Running session
        // returns SessionUseCaseError::Conflict.
        if cmd.session_id.is_some() {
            return Err(SessionUseCaseError::Conflict(
                "session_is_running_cannot_invoke".to_string(),
            ));
        }
        unimplemented!("only reactivate path used by this test")
    }

    async fn get(&self, sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self
            .session
            .lock()
            .await
            .as_ref()
            .filter(|s| s.id == sid)
            .cloned())
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(self
            .session
            .lock()
            .await
            .as_ref()
            .map(|s| s.id == session_id && s.group_id == group_id)
            .unwrap_or(false))
    }

    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
        Ok(0)
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
        Ok(())
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> { Ok(false) }
}

#[tokio::test]
async fn post_invocation_running_session_returns_409() {
    use bcs_service_api::ParticipantRole;

    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-1",
        "bot-manager",
        vec![Participant::bot("bot-manager", ParticipantRole::Manager)],
    );
    group.service_spec = Some(ServiceSpec {
        callback_config: None,
        timeout_seconds: None,
        max_concurrency: None,
    });
    group_store.upsert(group).await.unwrap();

    // Pre-existing running service-invocation session.
    let session = Session {
        id: "group-1:abcdef12".to_string(),
        group_id: "group-1".to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::ServiceInvocation,
        participants: vec![Participant::bot("bot-manager", ParticipantRole::Manager)],
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: Some("pending".to_string()),
        activation_count: 1,
        caller_principal: None,
        created_by: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
    };
    let sessions = Arc::new(ReactivateConflictSessions {
        session: Mutex::new(Some(session)),
    });

    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = sessions.clone();

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
                        "session_id": "group-1:abcdef12",
                        "input": {"task": "audit"},
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CONFLICT);
}

// ----- Bug fix #13: post_invocation must use belongs_to_group contract -----
//
// services.rs handlers must consult `SessionManagementService::belongs_to_group`,
// not `session.group_id == group_id`. Future stores might add env scoping
// or soft-delete filtering that the field comparison would silently bypass.
//
// We simulate this by returning a session whose `group_id` field matches the
// URL but whose `belongs_to_group` returns false. The handler must honor
// the contract method (404), not the field (200/202).

#[derive(Default)]
struct ContractMismatchSessions {
    session: Mutex<Option<Session>>,
}

#[async_trait::async_trait]
impl SessionManagementService for ContractMismatchSessions {
    async fn create_or_reactivate(
        &self,
        _cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        unimplemented!()
    }

    async fn get(&self, sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self
            .session
            .lock()
            .await
            .as_ref()
            .filter(|s| s.id == sid)
            .cloned())
    }

    async fn belongs_to_group(
        &self,
        _session_id: &str,
        _group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        // Force the contract to disagree with `session.group_id`.
        Ok(false)
    }

    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
        Ok(0)
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
        Ok(())
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!()
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> { Ok(false) }
}

#[tokio::test]
async fn post_invocation_honors_belongs_to_group_contract_over_field_match() {
    use bcs_service_api::ParticipantRole;
    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-1",
        "bot-manager",
        vec![Participant::bot("bot-manager", ParticipantRole::Manager)],
    );
    group.service_spec = Some(ServiceSpec {
        callback_config: None,
        timeout_seconds: None,
        max_concurrency: None,
    });
    group_store.upsert(group).await.unwrap();

    let session = Session {
        id: "group-1:abcdef12".to_string(),
        // group_id field intentionally matches URL — but belongs_to_group
        // contract method returns false. The handler must trust the method.
        group_id: "group-1".to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Completed,
        session_kind: SessionKind::ServiceInvocation,
        participants: vec![Participant::bot("bot-manager", ParticipantRole::Manager)],
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: Some("succeeded".to_string()),
        activation_count: 1,
        caller_principal: None,
        created_by: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        created_at: 1,
        updated_at: 1,
        completed_at: Some(2),
        meta: None,
    };
    let sessions = Arc::new(ContractMismatchSessions {
        session: Mutex::new(Some(session)),
    });

    let mut services = Services::noop();
    services.group = group_store;
    services.session_management = sessions.clone();

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
                        "session_id": "group-1:abcdef12",
                        "input": {},
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(
        response.status(),
        StatusCode::NOT_FOUND,
        "post_invocation must use belongs_to_group contract, not session.group_id field match"
    );
}
