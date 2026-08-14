//! Contract tests for `POST /sessions/{sid}/complete`.
//!
//! Mirrors the legacy `complete_session` handler in `bcs/src/server.rs:13115-13157`:
//! - Unauthenticated callers must be rejected (401).
//! - Service-invocation sessions cannot be completed via this endpoint (403).
//! - Only the session driver/lead may complete a chat session (403 otherwise).

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_bot::BotCore;
use bcs_group::GroupStore;
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{
    ActorKind, BotCapabilities, BotRegistryCoreService, CreateOrReactivateCommand,
    CreateOrReactivateOutcome, Group, GroupCoreService, Participant, ParticipantMode,
    ParticipantRole, Session, SessionKind, SessionManagementService, SessionStatus,
    SessionUseCaseError,
};
use bcs_services_container::Services;
use serde_json::json;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

#[tokio::test]
async fn complete_session_rejects_unauthenticated_caller() {
    let (app, _sessions, _temp_dir) = test_app(stub_session(SessionKind::Chat, vec![
        Participant::bot("driver-bot", ParticipantRole::Driver),
    ]))
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/group-1:00000001/complete")
                .header("content-type", "application/json")
                .body(Body::from(json!({}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn complete_session_rejects_service_invocation_kind() {
    let (app, _sessions, _temp_dir) =
        test_app(stub_session(SessionKind::ServiceInvocation, vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
        ]))
        .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/group-1:00000001/complete")
                .header("content-type", "application/json")
                .header("authorization", "Bearer driver-token")
                .body(Body::from(json!({}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body: serde_json::Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap()).unwrap();
    let msg = body["message"].as_str().unwrap_or("");
    assert!(
        msg.contains("service") && msg.contains("cannot"),
        "unexpected message: {msg}"
    );
}

#[tokio::test]
async fn complete_session_rejects_non_driver_bot() {
    let (app, _sessions, _temp_dir) = test_app(stub_session(SessionKind::Chat, vec![
        Participant::bot("driver-bot", ParticipantRole::Driver),
        Participant::bot("worker-bot", ParticipantRole::Consultant),
    ]))
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/group-1:00000001/complete")
                .header("content-type", "application/json")
                .header("authorization", "Bearer worker-token")
                .body(Body::from(json!({}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn complete_session_succeeds_for_driver_bot() {
    let (app, sessions, _temp_dir) = test_app(stub_session(SessionKind::Chat, vec![
        Participant::bot("driver-bot", ParticipantRole::Driver),
        Participant::bot("worker-bot", ParticipantRole::Consultant),
    ]))
    .await;

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/sessions/group-1:00000001/complete")
                .header("content-type", "application/json")
                .header("authorization", "Bearer driver-token")
                .body(Body::from(json!({}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let calls = sessions.complete_calls.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0], "group-1:00000001");
}

// ----- helpers -----

fn stub_session(kind: SessionKind, participants: Vec<Participant>) -> Session {
    Session {
        id: "group-1:00000001".to_string(),
        group_id: "group-1".to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: kind,
        participants,
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: Some("pending".to_string()),
        activation_count: 1,
        caller_principal: None,
        created_by: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        collected_at: None,
    }
}

async fn test_app(
    session: Session,
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
    group_store
        .upsert(Group::new(
            "group-1",
            "driver-bot",
            vec![
                Participant {
                    bot_uuid: "driver-bot".to_string(),
                    bot_name: Some("Driver".to_string()),
                    kind: None,
                    role: ParticipantRole::Driver,
                    actor_kind: ActorKind::Bot,
                    mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
                },
                Participant {
                    bot_uuid: "worker-bot".to_string(),
                    bot_name: Some("Worker".to_string()),
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: ActorKind::Bot,
                    mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
                },
            ],
        ))
        .await
        .unwrap();

    let sessions = Arc::new(RecordingSessions {
        session: Mutex::new(Some(session)),
        complete_calls: Mutex::new(Vec::new()),
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
    complete_calls: Mutex<Vec<String>>,
}

#[async_trait::async_trait]
impl SessionManagementService for RecordingSessions {
    async fn create_or_reactivate(
        &self,
        _cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        unimplemented!("not used by complete tests")
    }

    async fn get(&self, sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
        let s = self.session.lock().await;
        Ok(s.as_ref().filter(|x| x.id == sid).cloned())
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        let s = self.session.lock().await;
        Ok(s.as_ref()
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
        sid: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        self.complete_calls.lock().await.push(sid.to_string());
        let s = self.session.lock().await.clone();
        Ok(s)
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not used by complete tests")
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not used by complete tests")
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not used by complete tests")
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not used by complete tests")
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> { Ok(false) }
}
