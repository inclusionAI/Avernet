//! HTTP E2E cutover for the adapter-level Human→Bot
//! control probes (bot-authority plan Task 6.5,
//! carries A + T6d-2): the mounted session route
//! probes, the friend act-as-Bot probe, and the
//! group member-caller fallback all consult the
//! container-injected authority hook NOW, and a
//! revoked owner is fallback-free denied through
//! the REAL router even when a HISTORICAL
//! `created_by` row still exists.
//!
//! Entries: `DELETE /sessions/{sid}`,
//! `GET /groups/{id}/sessions`,
//! `POST /sessions/{sid}/collect`,
//! `POST /collaboration/friend-connections/requests`
//! (act-as-Bot), `POST /groups/{id}/members`
//! (Human coordinator fallback).

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    dead_code,
    reason = "shared fixture fakes"
)]

#[path = "authority_cutover_parts/authority_friend_checks.rs"]
mod friend_checks;
#[path = "authority_cutover_parts/authority_session_checks.rs"]
mod session_checks;

use std::sync::Arc;

use async_trait::async_trait;
use axum::{
    body::{to_bytes, Body},
    http::{HeaderMap, Request, StatusCode},
};
use bcs_auth_api::{AuthError, UserIdentityInfo};
use bcs_bot::BotCore;
use bcs_group::GroupStore;
use bcs_http::router::build_router;
use bcs_http::state::{
    HttpAppState, HttpUserIdentity, UserIdentityPort,
};
use bcs_service_api::{
    ActorKind, BotCapabilities, BotRegistryCoreService,
    CreateOrReactivateCommand, Group, GroupCoreService, Participant,
    ParticipantMode, ParticipantRole, Session, SessionKind,
    SessionManagementService, SessionStatus, SessionUseCaseError,
};
use bcs_services_container::Services;
use bcs_test_support::LazySeedAuthorityHook;
use serde_json::Value;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// REAL authority chain helper: `pairs` seed live OWNER
// edges; handover fixtures rely on the owner-edge
// replacement rule (spec §3.2.1).
// ---------------------------------------------------------------------------

fn trusted_chain(pairs: &[(&str, &str)]) -> Arc<LazySeedAuthorityHook> {
    let chain = bcs_test_support::lazy_authority_chain();
    for (bot_id, owner) in pairs {
        chain.hook.seed_owner(bot_id, owner);
    }
    chain.hook
}

// ---------------------------------------------------------------------------
// Session routes (sessions.rs probes)
// ---------------------------------------------------------------------------

const SID: &str = "group-1:00000001";

struct RecordingSessions {
    session: Mutex<Option<Session>>,
}

#[async_trait]
impl SessionManagementService for RecordingSessions {
    async fn create_or_reactivate(
        &self,
        _cmd: CreateOrReactivateCommand,
    ) -> Result<bcs_service_api::CreateOrReactivateOutcome, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("unused".to_string()))
    }

    async fn get(&self, session_id: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self
            .session
            .lock()
            .await
            .as_ref()
            .filter(|s| s.id == session_id)
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
        group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(self
            .session
            .lock()
            .await
            .as_ref()
            .filter(|s| s.group_id == group_id)
            .cloned()
            .into_iter()
            .collect())
    }




    async fn complete_if_running(
        &self,
        _sid: &str,
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
        let s = self
            .session
            .lock()
            .await
            .clone()
            .unwrap();
        Ok(s)
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        let s = self
            .session
            .lock()
            .await
            .clone()
            .unwrap();
        Ok(s)
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        let s = self
            .session
            .lock()
            .await
            .clone()
            .unwrap();
        Ok(s)
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("unused".to_string()))
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn delete(&self, session_id: &str) -> Result<bool, SessionUseCaseError> {
        let mut session = self.session.lock().await;
        let was = session.as_ref().is_some_and(|s| s.id == session_id);
        *session = None;
        Ok(was)
    }

    async fn collect(&self, _sid: &str, _bot_uuid: &str) -> Result<(), SessionUseCaseError> {
        Ok(())
    }

    async fn uncollect(&self, _sid: &str, _bot_uuid: &str) -> Result<(), SessionUseCaseError> {
        Ok(())
    }
}

#[derive(Default, Clone)]
struct StaticHumanIdentity {
    staff_no: Option<String>,
}

#[async_trait]
impl UserIdentityPort for StaticHumanIdentity {
    async fn extract(&self, _headers: &HeaderMap, _uri: &axum::http::Uri) -> Option<HttpUserIdentity> {
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

    async fn get_identity_by_token(&self, _token: &str) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn get_identity_by_user_id(&self, _user_id: &str) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn update_token(&self, _user_id: &str, _token: &str, _expire_at: u64) -> Result<(), AuthError> {
        Ok(())
    }
}

struct SessionFixture {
    app: axum::Router,
    authority: Arc<LazySeedAuthorityHook>,
    _temp_dir: TempDir,
}

/// Human-caller session app: group driver = driver-bot,
/// session created_by = creator-bot; both carry a
/// HISTORICAL `created_by` row for `alice` (legacy data
/// intentionally left — it must never authorize after
/// revocation).
async fn session_fixture(staff: &str) -> SessionFixture {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, name, owner) in [
        ("driver-bot", "Driver", Some("alice")),
        ("creator-bot", "Creator", Some("alice")),
        ("managed-bot", "Managed", Some("alice")),
    ] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(name.to_string()),
                    visibility: "public".to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
        if let Some(owner) = owner {
            registry.save_created_by(bot_id, owner, true).await.unwrap();
        }
    }

    let group_store = Arc::new(GroupStore::new());
    group_store
        .upsert(Group::new(
            "group-1",
            "driver-bot",
            vec![
                bot_participant("driver-bot", ParticipantRole::Driver),
                bot_participant("managed-bot", ParticipantRole::Consultant),
            ],
        ))
        .await
        .unwrap();

    let session = Session {
        id: SID.to_string(),
        group_id: "group-1".to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants: vec![
            bot_participant("driver-bot", ParticipantRole::Driver),
            bot_participant("managed-bot", ParticipantRole::Consultant),
        ],
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        activation_count: 1,
        message_visibility_version: 1,
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
    let sessions = Arc::new(RecordingSessions {
        session: Mutex::new(Some(session)),
    });

    let authority = trusted_chain(&[]);
    let mut services = Services::noop();
    services.registry = registry.clone();
    services.group = group_store;
    services.session_management = sessions;
    services.bot_authority_hook = authority.clone();

    let identity: Arc<dyn UserIdentityPort + Send + Sync> = Arc::new(StaticHumanIdentity {
        staff_no: Some(staff.to_string()),
    });
    let app = build_router(HttpAppState::new(services).with_user_identity(identity));
    SessionFixture {
        app,
        authority,
        #[allow(dead_code)]
        _temp_dir: temp_dir,
    }
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

async fn delete_session(app: &axum::Router, sid: &str, bot_id: &str) -> axum::response::Response {
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

async fn list_sessions(app: &axum::Router, group_id: &str) -> axum::response::Response {
    app.clone()
        .oneshot(
            Request::builder()
                .uri(format!("/groups/{group_id}/sessions"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap()
}

// ---------------------------------------------------------------------------
// Friend act-as-Bot (T6d-2a): routed friend-connections entry.
// ---------------------------------------------------------------------------

mod connect_fake {
    use async_trait::async_trait;
    use std::sync::Mutex;

    use bcs_domain::edge_permission::{
        FriendListEntry, PermissionRequest, RequestStatus,
    };
    use bcs_service_api::application::connect::{
        ConnectResult, ConnectService, ConnectStatus, RequestDirection,
        RequestsPage,
    };
    use bcs_service_api::{RequestAuthHeaders, ServiceResult};

    /// Records the routed create_connect calls.
    pub struct RecordingConnectService {
        pub create_commands: Mutex<Vec<(String, String, Option<String>)>>,
    }

    impl RecordingConnectService {
        pub fn new() -> Self {
            Self {
                create_commands: Mutex::new(Vec::new()),
            }
        }

        pub async fn requests(&self) -> Vec<(String, String, Option<String>)> {
            self.create_commands
                .lock()
                .unwrap_or_else(|p| p.into_inner())
                .clone()
        }
    }

    #[async_trait]
    impl ConnectService for RecordingConnectService {
        async fn create_connect(
            &self,
            caller: &str,
            to_bot: &str,
            message: Option<String>,
            _request_auth: Option<RequestAuthHeaders>,
        ) -> ServiceResult<ConnectResult> {
            self.create_commands
                .lock()
                .unwrap_or_else(|p| p.into_inner())
                .push((caller.to_string(), to_bot.to_string(), message));
            Ok(ConnectResult {
                request_ids: vec!["1".to_string()],
                edge_ids: vec![11],
                status: ConnectStatus::Pending,
                auto_accepted: false,
            })
        }

        async fn approve(
            &self,
            _request_id: &str,
            _decider: &str,
            _request_auth: Option<RequestAuthHeaders>,
        ) -> ServiceResult<Vec<u64>> {
            Err(super::service_err("not used by this test"))
        }

        async fn reject(
            &self,
            _request_id: &str,
            _decider: &str,
            _reason: Option<String>,
        ) -> ServiceResult<()> {
            Err(super::service_err("not used by this test"))
        }

        async fn cancel(&self, _request_id: &str) -> ServiceResult<()> {
            Err(super::service_err("not used by this test"))
        }

        async fn get_request(&self, _request_id: &str) -> ServiceResult<PermissionRequest> {
            Err(super::service_err("not used by this test"))
        }

        async fn revoke_friend(
            &self,
            _caller: &str,
            _target: &str,
            _request_auth: Option<RequestAuthHeaders>,
        ) -> ServiceResult<Vec<u64>> {
            Err(super::service_err("not used by this test"))
        }

        async fn list_friends(&self, _actor: &str) -> ServiceResult<Vec<FriendListEntry>> {
            Ok(Vec::new())
        }

        async fn list_friends_paginated(
            &self,
            _actor: &str,
            _query: bcs_service_api::port::repo::edge_grant::FriendListQuery,
        ) -> ServiceResult<bcs_service_api::application::connect::FriendEntriesPage> {
            Ok(bcs_service_api::application::connect::FriendEntriesPage {
                items: Vec::new(),
                total: 0,
            })
        }

        async fn list_requests(
            &self,
            _actor: &str,
            _direction: RequestDirection,
            _status: Option<RequestStatus>,
            _page: u32,
            _page_size: u32,
        ) -> ServiceResult<RequestsPage> {
            Ok(RequestsPage {
                items: Vec::new(),
                total: 0,
                page: 0,
                page_size: 20,
            })
        }
    }
}

#[derive(Default, Clone)]
struct StaticFriendIdentity {
    staff_no: String,
}

#[async_trait]
impl UserIdentityPort for StaticFriendIdentity {
    async fn extract(&self, _headers: &HeaderMap, _uri: &axum::http::Uri) -> Option<HttpUserIdentity> {
        Some(HttpUserIdentity {
            staff_no: Some(self.staff_no.clone()),
            nick_name: None,
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

    async fn get_identity_by_token(&self, _token: &str) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn get_identity_by_user_id(&self, _user_id: &str) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn update_token(&self, _user_id: &str, _token: &str, _expire_at: u64) -> Result<(), AuthError> {
        Ok(())
    }
}

fn friend_app(
    connect: Arc<connect_fake::RecordingConnectService>,
    authority: Arc<LazySeedAuthorityHook>,
    staff_no: &str,
) -> axum::Router {
    let registry = Arc::new(BotCore::new());
    let mut services = Services::noop();
    services.registry = registry;
    services.bot_authority_hook = authority;
    let identity: Arc<dyn UserIdentityPort + Send + Sync> =
        Arc::new(StaticFriendIdentity {
            staff_no: staff_no.to_string(),
        });
    build_router(
        HttpAppState::new(services)
            .with_connect(connect)
            .with_user_identity(identity),
    )
}

async fn request_act_as(
    app: &axum::Router,
    _staff: &str,
    from_actor: &str,
) -> axum::response::Response {
    app.clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/collaboration/friend-connections/requests")
                .header("authorization", "Bearer human-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "from_actor": from_actor,
                        "actor_kind": "bot",
                        "to_bot": "peer-bot",
                        "message": "matrix"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap()
}

fn service_err(message: &str) -> bcs_service_api::ServiceError {
    bcs_service_api::ServiceError::Conflict(message.to_string())
}

#[tokio::test]
async fn friend_act_as_follows_the_current_control_matrix() {
    // Routed act-as entry (T6d-2a): the judgment is the
    // CURRENT authority edge; no `created_by` mirror.
    let connect = Arc::new(connect_fake::RecordingConnectService::new());
    let authority = trusted_chain(&[("bot-owned", "10001")]);

    // A(owner): the CURRENT owner acts as the Bot.
    let app = friend_app(connect.clone(), authority.clone(), "10001");
    let resp = request_act_as(&app, "10001", "bot-owned").await;
    assert_eq!(resp.status(), StatusCode::OK);

    // M(manager): the same judgment through the manager
    // edge.
    authority.seed_manager("bot-owned", "20002");
    let app = friend_app(connect.clone(), authority.clone(), "20002");
    let resp = request_act_as(&app, "20002", "bot-owned").await;
    assert_eq!(resp.status(), StatusCode::OK);

    // X(manager withdrawal): fallback-free deny.
    authority.revoke_manager("bot-owned", "20002");
    let resp = request_act_as(&app, "20002", "bot-owned").await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);

    // A→B handover: the new owner acts; the old owner is
    // denied on the SAME route.
    authority.seed_owner("bot-owned", "30003");
    let app_b = friend_app(connect.clone(), authority.clone(), "30003");
    let resp = request_act_as(&app_b, "30003", "bot-owned").await;
    assert_eq!(resp.status(), StatusCode::OK);
    let app_a = friend_app(connect.clone(), authority.clone(), "10001");
    let resp = request_act_as(&app_a, "10001", "bot-owned").await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);

    // The routed entry really ran the connect service.
    assert_eq!(connect.requests().await.len(), 3);
}

// ---------------------------------------------------------------------------
// Group member-caller (T6d-2b): routed /members entry.
// ---------------------------------------------------------------------------

#[derive(Default, Clone)]
struct StaticMembersIdentity {
    staff_no: String,
}

#[async_trait]
impl UserIdentityPort for StaticMembersIdentity {
    async fn extract(&self, _headers: &HeaderMap, _uri: &axum::http::Uri) -> Option<HttpUserIdentity> {
        Some(HttpUserIdentity {
            staff_no: Some(self.staff_no.clone()),
            nick_name: None,
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

    async fn get_identity_by_token(&self, _token: &str) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn get_identity_by_user_id(&self, _user_id: &str) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn update_token(&self, _user_id: &str, _token: &str, _expire_at: u64) -> Result<(), AuthError> {
        Ok(())
    }
}

async fn members_app(
    authority: Arc<LazySeedAuthorityHook>,
    staff_no: &str,
    originator: Option<&str>,
) -> axum::Router {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    // The group's coordinator Bot (driver) and the
    // candidate member Bot (public; no friendship
    // required).
    registry
        .register(
            "driver-bot".to_string(),
            BotCapabilities {
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .register(
            "candidate-bot".to_string(),
            BotCapabilities {
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    // A live Human actor row for the calling staff: the
    // app layer resolves a Human caller through the
    // registry (their own actor kind admits as self).
    registry
        .ensure_human_actor(staff_no, staff_no)
        .await
        .expect("human actor row for the caller");
    drop(temp_dir);
    let group_store = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-matrix",
        "driver-bot",
        vec![bot_participant("driver-bot", ParticipantRole::Driver)],
    );
    group.originator = originator.map(str::to_string);
    group_store.upsert(group).await.expect("group fixture");
    let group_query = Arc::new(
        bcs_group::GroupManagement::with_defaults(
            group_store.clone(),
            registry,
            Arc::new(bcs_test_support::NoopFriendCoreService),
        )
        // The app layer re-checks the SAME live
        // edge through its own hook.
        .with_bot_authority(authority.clone()),
    );
    let mut services = Services::noop();
    services.group = group_store;
    services.group_query = group_query.clone();
    services.group_management = group_query;
    services.bot_authority_hook = authority;
    let identity: Arc<dyn UserIdentityPort + Send + Sync> = Arc::new(StaticMembersIdentity {
        staff_no: staff_no.to_string(),
    });
    build_router(HttpAppState::new(services).with_user_identity(identity))
}

async fn add_member(app: &axum::Router) -> axum::response::Response {
    app.clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-matrix/members")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({ "bot_uuid": "candidate-bot" })
                        .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap()
}

#[tokio::test]
async fn group_member_caller_follows_the_current_control_matrix() {
    // Routed member entry (T6d-2b): the Human
    // coordinator fallback now lives on the CURRENT
    // authority edge (no `list_bots_by_creator`).
    let authority = trusted_chain(&[("driver-bot", "100")]);

    // A(owner): the CURRENT owner of the driver acts as
    // the coordinator Bot.
    let app = members_app(authority.clone(), "100", None).await;
    let resp = add_member(&app).await;
    let status = resp.status();
    let body = to_bytes(resp.into_body(), usize::MAX).await.unwrap();
    assert_eq!(
        status,
        StatusCode::OK,
        "A-owner member add: {}",
        String::from_utf8_lossy(&body)
    );

    // M(manager): the same judgment through the manager
    // edge.
    authority.seed_manager("driver-bot", "200");
    let app = members_app(authority.clone(), "200", None).await;
    let resp = add_member(&app).await;
    assert_eq!(resp.status(), StatusCode::OK);

    // X(manager withdrawal): fallback-free deny.
    authority.revoke_manager("driver-bot", "200");
    let app = members_app(authority.clone(), "200", None).await;
    let resp = add_member(&app).await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);

    // A→B handover: the new owner steers; the old owner,
    // whose legacy `created_by` row remains, is
    // fallback-free denied.
    authority.seed_owner("driver-bot", "400");
    let app_new = members_app(authority.clone(), "400", None).await;
    let resp = add_member(&app_new).await;
    assert_eq!(resp.status(), StatusCode::OK);
    let app_old = members_app(authority.clone(), "100", None).await;
    let resp = add_member(&app_old).await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn group_member_caller_admits_the_humans_own_originator_role() {
    // Review finding (PR #2625): the group's originator can be
    // the calling Human themself; the authority hook carries no
    // Human→Human manage edge by design, so the fallback must
    // recognize the Human's own actor id instead of 403-ing.
    // The driver Bot is controlled by ANOTHER user; the Human
    // holds no edge on any coordinator Bot at all.
    let authority = trusted_chain(&[("driver-bot", "400")]);
    let staff_originator = members_app(authority.clone(), "100", Some("human_100")).await;
    let resp = add_member(&staff_originator).await;
    let status = resp.status();
    let body = to_bytes(resp.into_body(), usize::MAX).await.unwrap();
    assert_eq!(
        status,
        StatusCode::OK,
        "self-originator member add must be admitted: {}",
        String::from_utf8_lossy(&body)
    );

    // An unrelated Human (neither originator nor holder of any
    // manage edge on the driver) is still denied.
    let staff_other = members_app(authority.clone(), "200", Some("human_100")).await;
    let resp = add_member(&staff_other).await;
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);

    // After the driver changes hands again, the Human's own
    // originator admission is unchanged (spec §3.3: the
    // independent resource fact survives driver handover and
    // manager-edge revocation).
    authority.seed_owner("driver-bot", "300");
    let staff_after_handover = members_app(authority.clone(), "100", Some("human_100")).await;
    let resp = add_member(&staff_after_handover).await;
    assert_eq!(resp.status(), StatusCode::OK);
}

// ---------------------------------------------------------------------------
// Legacy /friends routes (T6d-2c, GPT-review batch C
// finding #3): the Human act-as-Bot ownership probe
// follows the CURRENT authority edge — the historical
// `created_by` (including its `None` auto-claim)
// authorizes nothing here, and a failing read is an
// internal error, never a deny-as-403.
// ---------------------------------------------------------------------------

mod friends_fake {
    use std::sync::Mutex;

    use async_trait::async_trait;
    use bcs_service_api::application::friends::{
        CreateFriendRequestCommand, FriendListEntry,
        FriendRequestDecisionCommand, FriendService,
        FriendUseCaseError, ListFriendRequestsCommand,
        ListFriendsCommand,
    };
    use bcs_service_api::{FriendRequest, FriendRequestStatus};

    /// Records the calling actor of succeeded
    /// route-level ownership gates.
    pub struct RecordingFriendService {
        pub create_callers: Mutex<Vec<String>>,
        pub accept_callers: Mutex<Vec<(String, String)>>,
        pub friend_list_targets: Mutex<Vec<String>>,
    }

    impl RecordingFriendService {
        pub fn new() -> Self {
            Self {
                create_callers: Mutex::new(Vec::new()),
                accept_callers: Mutex::new(Vec::new()),
                friend_list_targets: Mutex::new(Vec::new()),
            }
        }
    }

    #[async_trait]
    impl FriendService for RecordingFriendService {
        async fn create_friend_request(
            &self,
            command: CreateFriendRequestCommand,
        ) -> Result<FriendRequest, FriendUseCaseError> {
            self.create_callers
                .lock()
                .unwrap()
                .push(command.caller_actor_id.clone());
            Ok(FriendRequest {
                id: "req-1".to_string(),
                from_bot: command.caller_actor_id.clone(),
                to_bot: command.to_bot.clone(),
                status: FriendRequestStatus::Pending,
                created_at: 1,
                updated_at: 1,
            })
        }

        async fn list_friend_requests(
            &self,
            _command: ListFriendRequestsCommand,
        ) -> Result<Vec<FriendRequest>, FriendUseCaseError> {
            Ok(Vec::new())
        }

        async fn accept_friend_request(
            &self,
            command: FriendRequestDecisionCommand,
        ) -> Result<(), FriendUseCaseError> {
            self.accept_callers
                .lock()
                .unwrap()
                .push((
                    command.caller_actor_id.clone(),
                    command.request_id.clone(),
                ));
            Ok(())
        }

        async fn reject_friend_request(
            &self,
            _command: FriendRequestDecisionCommand,
        ) -> Result<(), FriendUseCaseError> {
            Ok(())
        }

        async fn friend_request_receiver(
            &self,
            _request_id: &str,
        ) -> Result<String, FriendUseCaseError> {
            Ok("receiver-bot".to_string())
        }

        async fn list_friends(
            &self,
            command: ListFriendsCommand,
        ) -> Result<Vec<FriendListEntry>, FriendUseCaseError> {
            self.friend_list_targets
                .lock()
                .unwrap()
                .push(command.caller_actor_id.clone());
            Ok(Vec::new())
        }
    }
}
