//! Contract tests for `GET /groups/{id}/sessions`.
//!
//! Bug fix #10: when the list is empty and the caller is a formal member,
//! BCS must create a legacy session `{group_id}:00000000` and return it,
//! matching legacy server.rs:12838-12871. The current implementation only
//! `get`s it, so old groups (pre-session-split) get an empty array.
//!
//! Bug fix #11: Human caller's "formal member" check must consider both the
//! Human actor_id AND every Bot the Human owns; legacy server.rs:12767-12782.

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
    ActorKind, BotCapabilities, BotRegistryCoreService, CreateOrReactivateCommand,
    CreateOrReactivateOutcome, Group, GroupCoreService, Participant, ParticipantMode,
    ParticipantRole, Session, SessionKind, SessionManagementService, SessionStatus,
    SessionUseCaseError,
};
use bcs_services_container::Services;
use serde_json::Value;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

#[tokio::test]
async fn list_sessions_for_empty_group_creates_legacy_session_for_formal_member() {
    let (app, sessions, _temp_dir, _registry) = test_app(/* registry_humans = */ &[]).await;

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/groups/group-1/sessions")
                .header("authorization", "Bearer driver-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
            .unwrap();
    let items = body["items"].as_array().expect("items array");
    assert_eq!(items.len(), 1, "legacy session must be auto-created");
    assert_eq!(items[0]["session_id"], "group-1:00000000");

    // Verify session was actually persisted via create_or_reactivate.
    let creates = sessions.create_calls.lock().await;
    assert!(
        !creates.is_empty(),
        "create_or_reactivate must be called to persist the legacy session"
    );
    let cmd = &creates[0];
    assert_eq!(cmd.params.id.as_deref(), Some("group-1:00000000"));
}

#[tokio::test]
async fn participant_scoped_empty_result_does_not_create_legacy_session_when_group_has_sessions() {
    let (app, sessions, _temp_dir, _registry) = test_app_with_observer().await;

    sessions.sessions.lock().await.push(Session {
        id: "group-1:11111111".to_string(),
        group_id: "group-1".to_string(),
        session_title: Some("Driver session".to_string()),
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants: vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: None,
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
    });

    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/groups/group-1/sessions?participant=observer-bot")
                .header("authorization", "Bearer observer-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
            .unwrap();
    let items = body["items"].as_array().expect("items array");
    assert!(
        items.is_empty(),
        "participant-scoped list can be empty without creating a legacy session"
    );
    assert!(
        sessions.create_calls.lock().await.is_empty(),
        "legacy session should only be created when the group has no sessions at all"
    );
}

/// Bug fix #11: Human caller must be expanded to {actor_id, ...owned_bot_uuids}
/// when checking formal-member status. Legacy server.rs:12767-12782.
#[tokio::test]
async fn list_sessions_treats_human_owner_of_participant_bot_as_formal_member() {
    let (app, sessions, _temp_dir, registry) = human_owner_app().await;

    // Pre-seed a non-legacy session that only contains driver-bot as
    // participant — Human user (staff_no=alice) is NOT in participants.
    // Without bug-fix #11 the Human would be classified as non-formal and
    // see an empty array (because the only session does not contain them).
    let session = Session {
        id: "group-1:11111111".to_string(),
        group_id: "group-1".to_string(),
        session_title: Some("Real session".to_string()),
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants: vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: None,
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
    };
    sessions.sessions.lock().await.push(session);
    let _ = registry; // keep registry alive

    // Human caller: identity is set via the StaticHumanIdentity port
    // attached to the test app.
    let response = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/groups/group-1/sessions")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap())
            .unwrap();
    let items = body["items"].as_array().expect("items array");
    assert_eq!(
        items.len(),
        1,
        "Human owner of driver-bot must see the existing session as formal member"
    );
    assert_eq!(items[0]["session_id"], "group-1:11111111");
}

async fn human_owner_app() -> (axum::Router, Arc<RecordingSessions>, TempDir, Arc<BotCore>) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));

    // Register driver-bot owned by alice (staff_no).
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
            vec![Participant {
                bot_uuid: "driver-bot".to_string(),
                bot_name: Some("Driver".to_string()),
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
            }],
        ))
        .await
        .unwrap();

    let sessions = Arc::new(RecordingSessions::default());

    let mut services = Services::noop();
    services.registry = registry.clone();
    services.group = group_store;
    services.session_management = sessions.clone();

    let identity_port: Arc<dyn UserIdentityPort + Send + Sync> = Arc::new(StaticHumanIdentity {
        staff_no: Some("alice".to_string()),
    });
    let app = build_router(HttpAppState::new(services).with_user_identity(identity_port));
    (app, sessions, temp_dir, registry)
}

// ----- helpers -----

#[derive(Default)]
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

async fn test_app(
    _humans: &[&str],
) -> (axum::Router, Arc<RecordingSessions>, TempDir, Arc<BotCore>) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;

    let group_store = Arc::new(GroupStore::new());
    group_store
        .upsert(Group::new(
            "group-1",
            "driver-bot",
            vec![Participant {
                bot_uuid: "driver-bot".to_string(),
                bot_name: Some("Driver".to_string()),
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
            }],
        ))
        .await
        .unwrap();

    let sessions = Arc::new(RecordingSessions::default());

    let mut services = Services::noop();
    services.registry = registry.clone();
    services.group = group_store;
    services.session_management = sessions.clone();

    let app = build_router(HttpAppState::new(services));
    (app, sessions, temp_dir, registry)
}

async fn test_app_with_observer() -> (
    axum::Router,
    Arc<RecordingSessions>,
    TempDir,
    Arc<BotCore>,
) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    register_bot(&registry, "observer-bot", "Observer").await;
    registry
        .store_token_mapping("observer-token".to_string(), "observer-bot".to_string())
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
                    bot_uuid: "observer-bot".to_string(),
                    bot_name: Some("Observer".to_string()),
                    kind: None,
                    role: ParticipantRole::Consultant,
                    actor_kind: ActorKind::Bot,
                    mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
                },
            ],
        ))
        .await
        .unwrap();

    let sessions = Arc::new(RecordingSessions::default());

    let mut services = Services::noop();
    services.registry = registry.clone();
    services.group = group_store;
    services.session_management = sessions.clone();

    let app = build_router(HttpAppState::new(services));
    (app, sessions, temp_dir, registry)
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

#[derive(Default)]
struct RecordingSessions {
    sessions: Mutex<Vec<Session>>,
    create_calls: Mutex<Vec<CreateOrReactivateCommand>>,
}

#[async_trait::async_trait]
impl SessionManagementService for RecordingSessions {
    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        let id = cmd
            .params
            .id
            .clone()
            .unwrap_or_else(|| format!("{}:abcdef12", cmd.group_id));
        let session = Session {
            id: id.clone(),
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
        self.sessions.lock().await.push(session.clone());
        self.create_calls.lock().await.push(cmd);
        Ok(CreateOrReactivateOutcome {
            session,
            created: true,
        })
    }

    async fn get(&self, sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self
            .sessions
            .lock()
            .await
            .iter()
            .find(|s| s.id == sid)
            .cloned())
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(self
            .sessions
            .lock()
            .await
            .iter()
            .any(|s| s.id == session_id && s.group_id == group_id))
    }

    async fn list_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        offset: u64,
        limit: u64,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        let title_filter = title_contains.map(|q| q.to_ascii_lowercase());
        Ok(self
            .sessions
            .lock()
            .await
            .iter()
            .filter(|s| s.group_id == group_id)
            .filter(|s| status.is_none_or(|status| s.status == status))
            .filter(|s| {
                title_filter.as_ref().is_none_or(|q| {
                    s.session_title
                        .as_deref()
                        .unwrap_or_default()
                        .to_ascii_lowercase()
                        .contains(q)
                })
            })
            .filter(|s| {
                participant_id.is_none_or(|participant_id| {
                    s.participants
                        .iter()
                        .any(|p| p.bot_uuid == participant_id)
                })
            })
            .skip(offset as usize)
            .take(limit as usize)
            .cloned()
            .collect())
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
