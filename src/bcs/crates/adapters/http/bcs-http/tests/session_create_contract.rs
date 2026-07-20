//! Contract tests for `POST /groups/{id}/sessions` Human-creator group access.
//!
//! Bug fix #12: when the resolved creator is Human, BCS must verify the
//! Human can access the group (own at least one participant bot, or be a
//! participant themselves), legacy server.rs:12300-12320. Without this
//! check, a Human cookie can create sessions in any group they discover.

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{HeaderMap, Request, StatusCode},
};
use bcs_bot::BotCore;
use bcs_group::GroupStore;
use bcs_auth_api::{AuthError, UserIdentityInfo};
use bcs_http::{
    router::build_router,
    state::{HttpAppState, HttpUserIdentity, UserIdentityPort},
};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, CancelStateMachineRunCommand,
    CollaborationDefinition, CollaborationRuntimeError,
    CollaborationRuntimeService, ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome,
    CreateOrReactivateCommand, CreateOrReactivateOutcome, Group, GroupCoreService, GroupStrategy,
    HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome, Participant,
    ParticipantMode, ParticipantRole, Session, SessionKind, SessionManagementService,
    SessionHistoryResult, SessionStatus, SessionUseCaseError, StartStateMachineRunCommand,
    StartStateMachineRunOutcome, StateMachineDeliveryCorrelation, StateMachineRun,
    StateMachineRunStatus, StateMachineRunView,
};
use bcs_services_container::Services;
use serde_json::Value;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

#[tokio::test]
async fn create_session_rejects_human_who_does_not_own_any_participant() {
    // staff_no=mallory has no bots in group-1; group-1 has only alice's
    // driver-bot. mallory's POST should be 403, not 201.
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "driver-bot".to_string(),
            BotCapabilities {
                name: Some("Driver".to_string()),
                summary: Some("driver".to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .save_created_by("driver-bot", "alice", true)
        .await
        .unwrap();

    let group_store = Arc::new(GroupStore::new());
    group_store
        .upsert(Group::new(
            "group-1",
            "driver-bot",
            vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
        ))
        .await
        .unwrap();

    let sessions = Arc::new(MockSessions::default());

    let mut services = Services::noop();
    services.registry = registry;
    services.group = group_store;
    services.session_management = sessions.clone();

    let identity_port: Arc<dyn UserIdentityPort + Send + Sync> = Arc::new(StaticHumanIdentity {
        staff_no: Some("mallory".to_string()),
    });
    let app = build_router(HttpAppState::new(services).with_user_identity(identity_port));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/sessions")
                .header("content-type", "application/json")
                .body(Body::from(serde_json::json!({}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let creates = sessions.create_calls.lock().await;
    assert!(
        creates.is_empty(),
        "session must NOT be created when Human lacks group access"
    );
}

#[tokio::test]
async fn create_session_allows_human_who_owns_a_participant_bot() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "driver-bot".to_string(),
            BotCapabilities {
                name: Some("Driver".to_string()),
                summary: Some("driver".to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .save_created_by("driver-bot", "alice", true)
        .await
        .unwrap();

    let group_store = Arc::new(GroupStore::new());
    group_store
        .upsert(Group::new(
            "group-1",
            "driver-bot",
            vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
        ))
        .await
        .unwrap();

    let sessions = Arc::new(MockSessions::default());

    let mut services = Services::noop();
    services.registry = registry;
    services.group = group_store;
    services.session_management = sessions.clone();

    let identity_port: Arc<dyn UserIdentityPort + Send + Sync> = Arc::new(StaticHumanIdentity {
        staff_no: Some("alice".to_string()),
    });
    let app = build_router(HttpAppState::new(services).with_user_identity(identity_port));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/sessions")
                .header("content-type", "application/json")
                .body(Body::from(serde_json::json!({}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::CREATED);
}

#[tokio::test]
async fn state_machine_group_session_creation_starts_run_with_created_session() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "driver-bot".to_string(),
            BotCapabilities {
                name: Some("Driver".to_string()),
                summary: Some("driver".to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .save_created_by("driver-bot", "alice", true)
        .await
        .unwrap();

    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-1",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    group.version = 7;
    group_store.upsert(group).await.unwrap();

    let sessions = Arc::new(MockSessions::default());
    let collaboration = Arc::new(RecordingCollaborationRuntime::default());

    let mut services = Services::noop();
    services.registry = registry;
    services.group = group_store;
    services.session_management = sessions.clone();
    services.collaboration_runtime = collaboration.clone();

    let identity_port: Arc<dyn UserIdentityPort + Send + Sync> = Arc::new(StaticHumanIdentity {
        staff_no: Some("alice".to_string()),
    });
    let app = build_router(
        HttpAppState::new(services).with_user_identity(identity_port),
    );

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-1/sessions")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({"input": {"question": "hello"}}).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CREATED);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
            .unwrap();
    assert_eq!(body["session_id"], "group-1:abcdef12");
    assert_eq!(body["state_machine_run_id"], "sm-http-test");

    let session_commands = sessions.create_calls.lock().await;
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

// ----- helpers -----

struct StaticHumanIdentity {
    staff_no: Option<String>,
}

#[async_trait::async_trait]
impl UserIdentityPort for StaticHumanIdentity {
    async fn extract(
        &self,
        _headers: &HeaderMap,
        _uri: &axum::http::Uri,
    ) -> Option<HttpUserIdentity> {
        self.staff_no.as_ref().map(|sn| HttpUserIdentity {
            staff_no: Some(sn.clone()),
            nick_name: Some("Test".to_string()),
        })
    }

    async fn ensure_identity(
        &self,
        _auth_source: &str,
        _external_user_id: &str,
        _external_user_name: Option<&str>,
        _avatar: Option<&str>,
        _env: &str,
    ) -> Result<String, AuthError> {
        Ok("noop-identity".to_string())
    }

    async fn get_identity_by_token(
        &self,
        _token: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn get_identity_by_user_id(
        &self,
        _user_id: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn update_token(
        &self,
        _user_id: &str,
        _token: &str,
        _expire_at: u64,
    ) -> Result<(), AuthError> {
        Ok(())
    }
}

#[derive(Default)]
struct MockSessions {
    create_calls: Mutex<Vec<CreateOrReactivateCommand>>,
}

#[async_trait::async_trait]
impl SessionManagementService for MockSessions {
    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        let session = Session {
            id: format!("{}:abcdef12", cmd.group_id),
            group_id: cmd.group_id.clone(),
            session_title: cmd.params.session_title.clone(),
            env: None,
            status: SessionStatus::Running,
            session_kind: cmd.params.session_kind,
            participants: cmd.params.participants.clone(),
            group_version: cmd.params.group_version,
            caller_id: cmd.params.caller_id.clone(),
            input: cmd.params.input.clone(),
            output: None,
            error_message: None,
            callback_status: None,
            activation_count: 1,
            caller_principal: cmd.params.caller_principal.clone(),
            created_by: cmd.params.created_by.clone(),
            current_msg_seq: 0,
            participant_join_seq: None,
            created_at: 1,
            updated_at: 1,
            completed_at: None,
            meta: cmd.params.meta.clone(),
            collected_at: None,
        };
        self.create_calls.lock().await.push(cmd);
        Ok(CreateOrReactivateOutcome {
            session,
            created: true,
        })
    }

    async fn get(&self, _sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
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
        _output: Option<Value>,
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

#[tokio::test]
async fn public_group_session_includes_non_member_human_in_participants() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "driver-bot".to_string(),
            BotCapabilities {
                name: Some("Driver".to_string()),
                summary: Some("driver".to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();

    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "public-group",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    group.visibility = "public".to_string();
    group_store.upsert(group).await.unwrap();

    let sessions = Arc::new(MockSessions::default());

    let mut services = Services::noop();
    services.registry = registry;
    services.group = group_store;
    services.session_management = sessions.clone();

    // Human "bob" is NOT a member of the group and does NOT own driver-bot
    let identity_port: Arc<dyn UserIdentityPort + Send + Sync> = Arc::new(StaticHumanIdentity {
        staff_no: Some("bob".to_string()),
    });
    let app = build_router(HttpAppState::new(services).with_user_identity(identity_port));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/public-group/sessions")
                .header("content-type", "application/json")
                .body(Body::from(serde_json::json!({}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CREATED, "non-member human should be able to create session on public group");

    let body_bytes = to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
    let json: Value = serde_json::from_slice(&body_bytes).unwrap();
    let participants = json["participants"].as_array().expect("participants array");
    let human_in_session = participants.iter().any(|p| p["bot_uuid"] == "human_bob");
    assert!(
        human_in_session,
        "human_bob must be in session participants, got: {:?}",
        participants.iter().map(|p| p["bot_uuid"].as_str()).collect::<Vec<_>>()
    );
}
