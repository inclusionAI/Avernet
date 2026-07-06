//! Contract tests for session member-management endpoints.
//!
//! Mirrors legacy server.rs:13170-13260:
//!   POST   /sessions/{sid}/members                 → 401 unauth, role validated
//!   DELETE /sessions/{sid}/members/{bot_uuid}      → 401 unauth
//!   PATCH  /sessions/{sid}/members/{bot_uuid}      → 401 unauth

use std::sync::Arc;

use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use bcs_bot::BotCore;
use bcs_group::GroupStore;
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{
    ActorKind, BotCapabilities, BotRegistryCoreService, CreateOrReactivateCommand,
    CreateOrReactivateOutcome, Group, GroupCoreService, GroupStrategy, Participant,
    ParticipantMode, ParticipantRole, Session, SessionKind, SessionManagementService,
    SessionStatus, SessionUseCaseError,
};
use bcs_services_container::Services;
use serde_json::json;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

#[tokio::test]
async fn add_session_participant_rejects_unauthenticated_caller() {
    let (app, _sessions, _temp_dir) = test_app(GroupStrategy::Chat).await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/group-1:00000001/members")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({"bot_uuid": "extra-bot", "role": "consultant"}).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn add_session_participant_rejects_role_incompatible_with_strategy() {
    // ManagerWorker strategy must reject `consultant` role.
    let (app, _sessions, _temp_dir) = test_app(GroupStrategy::ManagerWorker).await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/group-1:00000001/members")
                .header("content-type", "application/json")
                .header("authorization", "Bearer driver-token")
                .body(Body::from(
                    json!({"bot_uuid": "extra-bot", "role": "consultant"}).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn remove_session_participant_rejects_unauthenticated_caller() {
    let (app, _sessions, _temp_dir) = test_app(GroupStrategy::Chat).await;

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/sessions/group-1:00000001/members/worker-bot")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn update_session_participant_mode_rejects_unauthenticated_caller() {
    let (app, _sessions, _temp_dir) = test_app(GroupStrategy::Chat).await;

    let response = app
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/sessions/group-1:00000001/members/worker-bot")
                .header("content-type", "application/json")
                .body(Body::from(json!({"mode": "muted"}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

// ----- helpers -----

async fn test_app(
    strategy: GroupStrategy,
) -> (axum::Router, Arc<RecordingSessions>, TempDir) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    register_bot(&registry, "worker-bot", "Worker").await;
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    registry
        .store_token_mapping("worker-token".to_string(), "worker-bot".to_string())
        .await;

    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-1",
        "driver-bot",
        vec![
            Participant {
                bot_uuid: "driver-bot".to_string(),
                bot_name: Some("Driver".to_string()),
                kind: None,
                role: match strategy {
                    GroupStrategy::Chat | GroupStrategy::StateMachine => ParticipantRole::Driver,
                    GroupStrategy::ManagerWorker => ParticipantRole::Manager,
                },
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
            },
            Participant {
                bot_uuid: "worker-bot".to_string(),
                bot_name: Some("Worker".to_string()),
                kind: None,
                role: match strategy {
                    GroupStrategy::Chat | GroupStrategy::StateMachine => ParticipantRole::Consultant,
                    GroupStrategy::ManagerWorker => ParticipantRole::Worker,
                },
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
            },
        ],
    );
    group.group_strategy = strategy;
    group_store.upsert(group).await.unwrap();

    let session = Session {
        id: "group-1:00000001".to_string(),
        group_id: "group-1".to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants: vec![
            Participant::bot(
                "driver-bot",
                match strategy {
                    GroupStrategy::Chat | GroupStrategy::StateMachine => ParticipantRole::Driver,
                    GroupStrategy::ManagerWorker => ParticipantRole::Manager,
                },
            ),
            Participant::bot(
                "worker-bot",
                match strategy {
                    GroupStrategy::Chat | GroupStrategy::StateMachine => ParticipantRole::Consultant,
                    GroupStrategy::ManagerWorker => ParticipantRole::Worker,
                },
            ),
        ],
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: None,
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

    let sessions = Arc::new(RecordingSessions {
        session: Mutex::new(Some(session)),
    });

    let mut services = Services::noop();
    services.registry = registry;
    services.group = group_store;
    services.session_management = sessions.clone();

    let app = build_router(HttpAppState::new(services));
    (app, sessions, temp_dir)
}

async fn register_bot(registry: &BotCore, bot_id: &str, name: &str) {
    registry
        .register(
            bot_id.to_string(),
            BotCapabilities {
                name: Some(name.to_string()),
                summary: Some(format!("{name} summary")),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
}

struct RecordingSessions {
    session: Mutex<Option<Session>>,
}

#[async_trait::async_trait]
impl SessionManagementService for RecordingSessions {
    async fn create_or_reactivate(
        &self,
        _cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        unimplemented!("not used by member tests")
    }

    async fn get(&self, sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self
            .session
            .lock()
            .await
            .as_ref()
            .filter(|x| x.id == sid)
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
            .map(|x| x.id == session_id && x.group_id == group_id)
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
        _sid: &str,
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
        let s = self.session.lock().await.clone().unwrap();
        Ok(s)
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        let s = self.session.lock().await.clone().unwrap();
        Ok(s)
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        let s = self.session.lock().await.clone().unwrap();
        Ok(s)
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
