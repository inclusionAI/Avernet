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
use bcs_service_api::application::session_files::{
    CapabilitiesView, DeleteFileCommand, DownloadRoute,
    PrepareUploadCommand, PrepareUploadResult,
    SessionFileService as SessionFilesService, SessionFileUseCaseError,
    ShareConsumeResult, ShareMintCommand, ShareMintResult,
};
use bcs_service_api::port::repo::{
    SessionFileListParams, SessionFileListPage,
};
use bcs_domain::session_file::SessionFile;
use bcs_storage_api::ByteStream;
use serde_json::json;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

const SID: &str = "group-1:00000001";

// ---- channel conversation lookup ----------------------------------------

#[tokio::test]
async fn channel_conversation_lookup_allows_session_member_bot() {
    let (app, _t) = bot_app("group-1", true).await;
    let resp = get_channel_conversations(&app, SID, "Bearer driver-token").await;
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn channel_conversation_lookup_allows_human_via_owned_participant_bot() {
    let (app, _t, _r, _chain) = human_app("alice").await;
    let resp = get_channel_conversations(&app, SID, "").await;
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn channel_conversation_lookup_rejects_non_member_bot() {
    let (app, _t) = bot_app("group-1", true).await;
    let resp = get_channel_conversations(&app, SID, "Bearer outsider-token").await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn channel_conversation_lookup_requires_bot_or_human_identity() {
    let (app, _t) = bot_app("group-1", true).await;
    let resp = get_channel_conversations(&app, SID, "").await;
    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
}

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
    let (app, _t, _r, _chain) = human_app("alice").await;
    // Human `alice` is not a direct participant (`human_alice`), but owns
    // `driver-bot`, which IS a session participant → `human_has_session_access`
    // owned-bot branch must admit the call.
    let resp = get(&app, SID, "").await;
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn gate_rejects_human_without_owned_participant_bot() {
    let (app, _t, _r, _chain) = human_app("bob").await;
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

async fn get_channel_conversations(
    app: &axum::Router,
    sid: &str,
    auth: &str,
) -> axum::response::Response {
    let mut request = Request::builder()
        .method("GET")
        .uri(format!(
            "/channels/conversations/by-session?bcs_session_id={sid}&channel_type=dingtalk"
        ));
    if !auth.is_empty() {
        request = request.header("authorization", auth);
    }
    app.clone()
        .oneshot(request.body(Body::empty()).unwrap())
        .await
        .unwrap()
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
        message_visibility_version: 1,
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
async fn human_app(
    staff: &str,
) -> (
    axum::Router,
    TempDir,
    Arc<BotCore>,
    Arc<bcs_test_support::LazySeedAuthorityHook>,
) {
    human_app_with_chain(staff).await
}

async fn human_app_with_chain(
    staff: &str,
) -> (
    axum::Router,
    TempDir,
    Arc<BotCore>,
    Arc<bcs_test_support::LazySeedAuthorityHook>,
) {
    human_app_files(staff, None).await
}

/// The harness with an optional wire-in for the
/// SessionFileService (delete-matrix tests).
async fn human_app_files(
    staff: &str,
    files: Option<Arc<dyn SessionFilesService>>,
) -> (
    axum::Router,
    TempDir,
    Arc<BotCore>,
    Arc<bcs_test_support::LazySeedAuthorityHook>,
) {
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
        message_visibility_version: 1,
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
    if let Some(files) = files {
        services.session_files = files;
    }
    // LIVE authority chain mirroring the fixture
    // `created_by` rows (bot-authority plan Task 6.5).
    let authority = bcs_test_support::lazy_authority_chain().hook;
    if staff == "alice" {
        authority.seed_owner("driver-bot", "alice");
    }
    services.bot_authority_hook = authority.clone();

    let identity: Arc<dyn UserIdentityPort + Send + Sync> = Arc::new(StaticHumanIdentity {
        staff_no: Some(staff.to_string()),
    });
    let app = build_router(HttpAppState::new(services).with_user_identity(identity));
    (app, temp_dir, registry, authority)
}

fn bot_participant(bot_uuid: &str, role: ParticipantRole) -> Participant {
    Participant {
        bot_uuid: bot_uuid.to_string(),
        bot_name: None,
        kind: None,
        role,
        actor_kind: ActorKind::Bot,
        mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
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

// ---------------------------------------------------------------------------
// GPT-review batch C finding #13: the legacy delete
// expands the FILE OWNER as an authorized-delete
// candidate (V1-facade parity), judged by the LIVE
// control edge: the file-owner controller deletes a
// file a participant Bot uploaded; after the A→B
// handover the revoked controller is fallback-free
// denied and the CURRENT controller deletes.
// ---------------------------------------------------------------------------

/// A files service whose `get` returns ONE file
/// (owned by the participant `worker-bot`) and whose
/// `delete_file` records the resolved
/// `caller_identities`.
struct RecordingFileDeletes {
    deletes: Mutex<Vec<DeleteFileCommand>>,
}

impl RecordingFileDeletes {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            deletes: Mutex::new(Vec::new()),
        })
    }

    async fn deleted_identities(&self) -> Vec<(String, Vec<String>)> {
        self.deletes
            .lock()
            .await
            .iter()
            .map(|cmd| {
                (
                    cmd.caller.actor_id.clone(),
                    cmd.caller_identities.clone(),
                )
            })
            .collect()
    }
}

#[async_trait::async_trait]
impl SessionFilesService for RecordingFileDeletes {
    async fn capabilities(&self) -> CapabilitiesView {
        CapabilitiesView {
            storage: "test".into(),
            presign_upload: false,
            presign_download: false,
            inline_view: false,
            max_size: 1024,
        }
    }

    async fn prepare_upload(
        &self,
        _cmd: PrepareUploadCommand,
    ) -> Result<PrepareUploadResult, SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::Internal(
            bcs_service_api::ServiceError::InternalError("unused".into()),
        ))
    }

    async fn stream_upload(
        &self,
        _session_id: &str,
        _file_id: &str,
        _part_number: Option<u16>,
        _body: ByteStream,
        _content_length: u64,
    ) -> Result<(), SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::Internal(
            bcs_service_api::ServiceError::InternalError("unused".into()),
        ))
    }

    async fn complete_upload(
        &self,
        _session_id: &str,
        _file_id: &str,
    ) -> Result<SessionFile, SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::NotFound("unused".into()))
    }

    async fn delete_file(
        &self,
        cmd: DeleteFileCommand,
    ) -> Result<(), SessionFileUseCaseError> {
        // Mirror the session-file service's
        // `can_delete`: caller identities against
        // the file owner / session creator /
        // driver (the service facts; the
        // IDENTITY expansion is the route's).
        let owner = "worker-bot";
        let authorized = cmd
            .caller_identities
            .iter()
            .any(|identity| {
                identity == owner
                    || cmd.session_creator.as_deref() == Some(identity)
                    || cmd.driver_bot.as_deref() == Some(identity.as_str())
            });
        if !authorized {
            return Err(SessionFileUseCaseError::Forbidden(
                "not authorized to delete".into(),
            ));
        }
        self.deletes.lock().await.push(cmd);
        Ok(())
    }

    async fn get(
        &self,
        session_id: &str,
        file_id: &str,
    ) -> Result<SessionFile, SessionFileUseCaseError> {
        Ok(SessionFile {
            file_id: file_id.to_string(),
            session_id: session_id.to_string(),
            file_name: "report.txt".into(),
            mime_type: "text/plain".into(),
            size: 3,
            sha256: None,
            // The FILE owner is a participant Bot that
            // is neither the session creator nor the
            // group driver.
            owner: bcs_domain::ActorRef {
                actor_kind: ActorKind::Bot,
                actor_id: "worker-bot".to_string(),
            },
            storage_backend: "test".into(),
            object_handle: String::new(),
            status: bcs_domain::FileStatus::Ready,
            created_at: 1,
            updated_at: 1,
        })
    }

    async fn list(
        &self,
        _session_id: &str,
        _params: SessionFileListParams,
    ) -> Result<SessionFileListPage, SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::NotFound("unused".into()))
    }

    async fn download_route(
        &self,
        _session_id: &str,
        _file_id: &str,
        _ttl_secs: Option<u64>,
        _show: bool,
    ) -> Result<(SessionFile, DownloadRoute), SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::NotFound("unused".into()))
    }

    async fn share_mint(
        &self,
        _cmd: ShareMintCommand,
    ) -> Result<ShareMintResult, SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::Internal(
            bcs_service_api::ServiceError::InternalError("unused".into()),
        ))
    }

    async fn share_mint_for_history(
        &self,
        _session_id: &str,
        _file_id: &str,
        _ttl_seconds: u64,
    ) -> Result<ShareMintResult, SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::NotFound("unused".into()))
    }

    async fn share_consume(
        &self,
        _token: &str,
    ) -> Result<ShareConsumeResult, SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::NotFound("unused".into()))
    }

    async fn get_stream(
        &self,
        _session_id: &str,
        _file_id: &str,
    ) -> Result<(SessionFile, ByteStream), SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::NotFound("unused".into()))
    }

    async fn sweep_expired_pending(&self) -> Result<u64, SessionFileUseCaseError> {
        Ok(0)
    }

    async fn delete_all_for_session(
        &self,
        _session_id: &str,
    ) -> Result<u64, SessionFileUseCaseError> {
        Ok(0)
    }
}

async fn delete_report(
    app: &axum::Router,
) -> axum::response::Response {
    app.clone()
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri(format!("/sessions/{SID}/files/report.txt"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap()
}

#[tokio::test]
async fn legacy_delete_expands_the_file_owner_through_the_live_edge() {
    let files = RecordingFileDeletes::new();
    let (app_a, _t, _r, chain_a) =
        human_app_files("alice", Some(files.clone())).await;
    chain_a.seed_owner("worker-bot", "alice");
    let resp = delete_report(&app_a).await;
    let status = resp.status();
    let body = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .expect("body");
    assert_eq!(
        status,
        StatusCode::NO_CONTENT,
        "the file-owner controller deletes: {}",
        String::from_utf8_lossy(&body)
    );
    // The FILE owner reaches the authorized-delete
    // candidates through the same live-control check.
    let deletes = files.deleted_identities().await;
    assert_eq!(deletes.len(), 1);
    let (caller, identities) = &deletes[0];
    assert_eq!(caller, "human_alice");
    // The file owner WAS an expansion candidate and
    // PASSED the live-control check.
    assert!(
        identities.iter().any(|id| id == "worker-bot"),
        "the file owner must be an identity of a member caller: {identities:?}"
    );

    // A→B handover of `worker-bot`: the revoked
    // controller is denied (no candidate identity
    // passes: it is neither the driver nor the file
    // owner's CURRENT controller), and the CURRENT
    // controller deletes.
    let files_b = RecordingFileDeletes::new();
    let (app_b, _t, _r, chain_b) =
        human_app_files("bob", Some(files_b.clone())).await;
    chain_b.seed_owner("worker-bot", "bob");
    let resp_b = delete_report(&app_b).await;
    assert_eq!(resp_b.status(), StatusCode::NO_CONTENT);
    let deletes_b = files_b.deleted_identities().await;
    assert!(deletes_b
        .iter()
        .flat_map(|(_, ids)| ids.iter())
        .any(|id| id == "worker-bot"));

    // M polarity, then withdrawal: the CURRENT
    // manager of the file-owner Bot deletes
    // through the SAME owner-candidate
    // expansion; after the manager withdrawal
    // the route is fallback-free denied (the
    // manager-edge revocation is the expressible
    // revocation here: a zero-owner Bot is an
    // invalid authority state).
    let files_m = RecordingFileDeletes::new();
    let (app_m, _t, _r, chain_m) =
        human_app_files("carol", Some(files_m.clone())).await;
    // A Bot always has a live owner (B); carol
    // holds a manager edge on it.
    chain_m.seed_owner("worker-bot", "bob");
    chain_m.seed_manager("worker-bot", "carol");
    let resp_m = delete_report(&app_m).await;
    assert_eq!(
        resp_m.status(),
        StatusCode::NO_CONTENT,
        "the current manager deletes through the owner candidate"
    );
    assert!(
        files_m
            .deleted_identities()
            .await
            .iter()
            .flat_map(|(_, ids)| ids.iter())
            .any(|id| id == "worker-bot"),
    );

    chain_m.revoke_manager("worker-bot", "carol");
    let (app_revoked, _t, _r, _chain) =
        human_app_files("carol", Some(RecordingFileDeletes::new())).await;
    let resp_revoked = delete_report(&app_revoked).await;
    let status_revoked = resp_revoked.status();
    let body = axum::body::to_bytes(resp_revoked.into_body(), usize::MAX)
        .await
        .expect("body");
    assert_eq!(
        status_revoked,
        StatusCode::FORBIDDEN,
        "the revoked controller is fallback-free denied: {}",
        String::from_utf8_lossy(&body)
    );
}
