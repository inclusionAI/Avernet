//! HTTP contract tests for the session-file member gate (`ensure_session_member`)
//! and the `human_has_session_access` helper.
//!
//! The gate must judge membership against the session's *own* participants
//! (not the group seed), accept a Human caller through a bot they own, and
//! reject non-members as well as missing sessions / missing parent groups.
//! The share handler resolves `session_participants` from the already-loaded
//! session and delegates membership authz to the service, so only the gate
//! differs between share and the read routes here (the service is the noop
//! default, which returns 500 for share_mint — proving the gate passed).

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
use serde_json::json;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

const SID: &str = "group-1:00000001";

// ---- read routes: list / capabilities ------------------------------------

#[tokio::test]
async fn gate_allows_session_member_bot_on_list() {
    let (app, _t) = bot_app("group-1", true).await;
    let resp = get(&app, SID, "Bearer driver-token").await;
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn gate_allows_session_member_bot_on_capabilities() {
    let (app, _t) = bot_app("group-1", true).await;
    let resp = app
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(format!("/sessions/{SID}/files/capabilities"))
                .header("authorization", "Bearer worker-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn gate_rejects_non_member_bot() {
    let (app, _t) = bot_app("group-1", true).await;
    // outsider-bot is registered but is NOT a participant of the session.
    let resp = get(&app, SID, "Bearer outsider-token").await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn gate_allows_human_via_owned_participant_bot() {
    let (app, _t, _r) = human_app("alice").await;
    // Human `alice` is not a direct participant (`human_alice`), but owns
    // `driver-bot`, which IS a session participant → `human_has_session_access`
    // owned-bot branch must admit the call.
    let resp = get(&app, SID, "").await;
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn gate_rejects_human_without_owned_participant_bot() {
    let (app, _t, _r) = human_app("bob").await;
    // Human `bob` owns only `worker-bot`-equivalent? Here bob owns no bot in the
    // session, so both `human_has_session_access` branches miss → 403.
    let resp = get(&app, SID, "").await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn gate_rejects_when_session_missing() {
    let (app, _t) = bot_app("group-1", true).await;
    // Unknown session id → session_management.get returns None → 403.
    let resp = get(&app, "group-1:deadbeef", "Bearer driver-token").await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn gate_rejects_when_parent_group_missing() {
    // Session exists but references a group that is not in the store → 403.
    let (app, _t) = bot_app("group-absent", false).await;
    let resp = get(&app, SID, "Bearer driver-token").await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);
}

// ---- share: exercises the handler's session_participants resolution -------

#[tokio::test]
async fn share_member_passes_gate_and_builds_command() {
    let (app, _t) = bot_app("group-1", true).await;
    // Noop share_mint returns Internal (500); reaching it proves the gate
    // passed and the ShareMintCommand (incl. session_participants) was built.
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/sessions/{SID}/files/fake-file/share"))
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(json!({ "ttl_seconds": 300 }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn share_rejects_non_member() {
    let (app, _t) = bot_app("group-1", true).await;
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/sessions/{SID}/files/fake-file/share"))
                .header("authorization", "Bearer outsider-token")
                .header("content-type", "application/json")
                .body(Body::from(json!({ "ttl_seconds": 300 }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);
}

// ----- helpers ------------------------------------------------------------

async fn get(app: &axum::Router, sid: &str, auth: &str) -> axum::response::Response {
    let mut b = Request::builder()
        .method("GET")
        .uri(format!("/sessions/{sid}/files"));
    if !auth.is_empty() {
        b = b.header("authorization", auth);
    }
    app.clone().oneshot(b.body(Body::empty()).unwrap()).await.unwrap()
}

/// Build an app whose session `SID` lives in `session_group`. When
/// `upsert_group` is true the group `group-1` is present (driver/worker as
/// group participants); the session participants are always driver+worker.
/// An `outsider-bot` is registered (with token) but is NOT a session member.
async fn bot_app(session_group: &str, upsert_group: bool) -> (axum::Router, TempDir) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    register_bot(&registry, "worker-bot", "Worker").await;
    register_bot(&registry, "outsider-bot", "Outsider").await;
    registry.store_token_mapping("driver-token".to_string(), "driver-bot".to_string()).await;
    registry.store_token_mapping("worker-token".to_string(), "worker-bot".to_string()).await;
    registry.store_token_mapping("outsider-token".to_string(), "outsider-bot".to_string()).await;

    let group_store = Arc::new(GroupStore::new());
    if upsert_group {
        let mut group = Group::new(
            "group-1",
            "driver-bot",
            vec![
                bot_participant("driver-bot", ParticipantRole::Driver),
                bot_participant("worker-bot", ParticipantRole::Consultant),
            ],
        );
        group.group_strategy = bcs_service_api::GroupStrategy::Chat;
        group_store.upsert(group).await.unwrap();
    }

    let session = Session {
        id: SID.to_string(),
        group_id: session_group.to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants: vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::bot("worker-bot", ParticipantRole::Consultant),
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
        collected_at: None,
    };

    let sessions = Arc::new(RecordingSessions { session: Mutex::new(Some(session)) });

    let mut services = Services::noop();
    services.registry = registry;
    services.group = group_store;
    services.session_management = sessions;

    (build_router(HttpAppState::new(services)), temp_dir)
}

/// Human-caller app: the human `staff` is attached via a static identity port.
/// To exercise the owned-bot branch we register `driver-bot` (a session
/// participant) as owned by `staff` only when `staff` == "alice"; other humans
/// own no bots in the session and must be rejected.
async fn human_app(staff: &str) -> (axum::Router, TempDir, Arc<BotCore>) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    register_bot(&registry, "worker-bot", "Worker").await;
    if staff == "alice" {
        registry.save_created_by("driver-bot", staff, true).await.unwrap();
    }

    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-1",
        "driver-bot",
        vec![
            bot_participant("driver-bot", ParticipantRole::Driver),
            bot_participant("worker-bot", ParticipantRole::Consultant),
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
            Participant::bot("worker-bot", ParticipantRole::Consultant),
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
    (app, temp_dir, registry)
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
        unimplemented!("not used by member-gate tests")
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

    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> {
        Ok(false)
    }
}
