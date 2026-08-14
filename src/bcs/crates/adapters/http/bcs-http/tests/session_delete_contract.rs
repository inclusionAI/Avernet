//! HTTP contract tests for `DELETE /sessions/{sid}`.
//!
//! Coverage: session creator may delete (regression), the group's driver bot
//! may delete, a Human who owns the driver bot may delete, and an unrelated
//! bot is rejected.

use std::sync::Arc;

use axum::{
    body::Body,
    http::{HeaderMap, Request, StatusCode},
};
use bcs_auth_api::{AuthError, UserIdentityInfo};
use bcs_bot::BotCore;
use bcs_group::GroupStore;
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
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

const SID: &str = "group-1:00000001";

#[tokio::test]
async fn delete_session_allows_driver_bot() {
    let (app, _t) = bot_app().await;
    let resp = delete(&app, SID, "driver-bot").await;
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn delete_session_allows_session_creator_bot() {
    let (app, _t) = bot_app().await;
    // creator-bot is the session.created_by; it is NOT the driver bot.
    let resp = delete(&app, SID, "creator-bot").await;
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn delete_session_rejects_unrelated_bot() {
    let (app, _t) = bot_app().await;
    let resp = delete(&app, SID, "outsider-bot").await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn delete_session_rejects_missing_session() {
    let (app, _t) = bot_app().await;
    let resp = delete(&app, "group-1:deadbeef", "driver-bot").await;
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn delete_session_allows_human_owner_of_driver_bot() {
    // alice owns driver-bot (the group driver). She may delete the session.
    let (app, _t) = human_app("alice").await;
    // Human identity resolves caller_id = human_alice; bot_id query is the
    // resolved actor id used by delete_session's authz.
    let resp = delete(&app, SID, "human_alice").await;
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn delete_session_rejects_human_without_owned_driver_or_creator() {
    // bob owns no creator/driver bot → 403.
    let (app, _t) = human_app("bob").await;
    let resp = delete(&app, SID, "human_bob").await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);
}

// ----- helpers ------------------------------------------------------------

async fn delete(app: &axum::Router, sid: &str, bot_id: &str) -> axum::response::Response {
    app.clone()
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/sessions/{sid}?bot_id={bot_id}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap()
}

/// Bot-caller app: group driver = driver-bot, session created_by = creator-bot.
/// driver/creator/outsider bots are all registered; session participants include
/// driver-bot + creator-bot.
async fn bot_app() -> (axum::Router, TempDir) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    register_bot(&registry, "creator-bot", "Creator").await;
    register_bot(&registry, "outsider-bot", "Outsider").await;

    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-1",
        "driver-bot",
        vec![
            bot_participant("driver-bot", ParticipantRole::Driver),
            bot_participant("creator-bot", ParticipantRole::Consultant),
        ],
    );
    group.group_strategy = bcs_service_api::GroupStrategy::Chat;
    group_store.upsert(group).await.unwrap();

    let session = Session {
        id: SID.to_string(),
        group_id: "group-1".to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants: vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::bot("creator-bot", ParticipantRole::Consultant),
        ],
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: None,
        activation_count: 1,
        caller_principal: None,
        created_by: Some("creator-bot".to_string()),
        current_msg_seq: 0,
        participant_join_seq: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
        collected_at: None,
    };

    let sessions = Arc::new(RecordingSessions { session: Mutex::new(Some(session)) });

    let mut services = Services::noop();
    services.registry = registry;
    services.group = group_store;
    services.session_management = sessions;
    (build_router(HttpAppState::new(services)), temp_dir)
}

/// Human-caller app: alice owns driver-bot; session created_by = creator-bot.
async fn human_app(staff: &str) -> (axum::Router, TempDir) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    register_bot(&registry, "creator-bot", "Creator").await;
    if staff == "alice" {
        registry.save_created_by("driver-bot", "alice", true).await.unwrap();
    }

    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-1",
        "driver-bot",
        vec![
            bot_participant("driver-bot", ParticipantRole::Driver),
            bot_participant("creator-bot", ParticipantRole::Consultant),
        ],
    );
    group.group_strategy = bcs_service_api::GroupStrategy::Chat;
    group_store.upsert(group).await.unwrap();

    let session = Session {
        id: SID.to_string(),
        group_id: "group-1".to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants: vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::bot("creator-bot", ParticipantRole::Consultant),
        ],
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: None,
        activation_count: 1,
        caller_principal: None,
        created_by: Some("creator-bot".to_string()),
        current_msg_seq: 0,
        participant_join_seq: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
        collected_at: None,
    };

    let sessions = Arc::new(RecordingSessions { session: Mutex::new(Some(session)) });

    let mut services = Services::noop();
    services.registry = registry.clone();
    services.group = group_store;
    services.session_management = sessions;

    let identity: Arc<dyn UserIdentityPort + Send + Sync> = Arc::new(StaticHumanIdentity {
        staff_no: Some(staff.to_string()),
    });
    let app = build_router(HttpAppState::new(services).with_user_identity(identity));
    (app, temp_dir)
}

fn bot_participant(bot_uuid: &str, role: ParticipantRole) -> Participant {
    Participant {
        bot_uuid: bot_uuid.to_string(),
        bot_name: None,
        kind: None,
        role,
        actor_kind: ActorKind::Bot,
        mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
    }
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

struct RecordingSessions {
    session: Mutex<Option<Session>>,
}

#[async_trait::async_trait]
impl SessionManagementService for RecordingSessions {
    async fn create_or_reactivate(
        &self,
        _cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        unimplemented!("not used by delete-session tests")
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

    // delete returns true so the handler reports 200 (deleted) instead of 404.
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> {
        Ok(true)
    }
}
