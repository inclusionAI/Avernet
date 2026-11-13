//! Edge and error-path contract tests for the Session HTTP handlers.
//!
//! The older session case files cover the happy path of each route. These
//! modules pin the remaining branches that existing clients already rely on:
//! wrong-role / wrong-owner rejections, missing Session or Group, store error
//! propagation, state-machine and collection edges.
//!
//! The fixture builds one in-memory Session store (`EdgeSessions`) whose reads
//! and writes can be told to fail, so the route-level error mapping is asserted
//! without touching production behavior.

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use axum::{
    body::{to_bytes, Body},
    http::{HeaderMap, Request, StatusCode},
    Router,
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
    CreateOrReactivateOutcome, CreateSessionLaunch, Group, GroupCoreService, GroupStrategy,
    Participant, ParticipantMode, ParticipantRole, ReactivateSessionLaunch, Session,
    SessionKind, SessionLaunchError, SessionLaunchOutcome, SessionLaunchService,
    SessionManagementService, SessionStatus, SessionUseCaseError,
};
use bcs_services_container::Services;
use serde_json::Value;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

#[path = "session_handler_edges/session_application.rs"]
mod session_application;
#[path = "session_handler_edges/participants.rs"]
mod participants;
#[path = "session_handler_edges/participants_edges.rs"]
mod participants_edges;
#[path = "session_handler_edges/lifecycle.rs"]
mod lifecycle;
#[path = "session_handler_edges/creation.rs"]
mod creation;
#[path = "session_handler_edges/history_chat.rs"]
mod history_chat;

const SESSION_ID: &str = "group-1:00000001";

// ---------------------------------------------------------------
// Failure injection
// ---------------------------------------------------------------

/// A reproducible Session store failure. `SessionUseCaseError` is not
/// `Clone`, so tests configure the shape and the store materializes it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Fail {
    NotFound,
    InvalidParams,
    Conflict,
    Forbidden,
    Internal,
}

impl Fail {
    fn error(self) -> SessionUseCaseError {
        match self {
            Fail::NotFound => SessionUseCaseError::NotFound("session store miss".to_string()),
            Fail::InvalidParams => SessionUseCaseError::InvalidParams("bad params".to_string()),
            Fail::Conflict => SessionUseCaseError::Conflict("state conflict".to_string()),
            Fail::Forbidden => SessionUseCaseError::Internal(
                bcs_service_api::ServiceError::Forbidden("denied by store".to_string()),
            ),
            Fail::Internal => SessionUseCaseError::Internal(
                bcs_service_api::ServiceError::InternalError("store unavailable".to_string()),
            ),
        }
    }
}

// ---------------------------------------------------------------
// In-memory Session store
// ---------------------------------------------------------------

#[derive(Default)]
pub(super) struct EdgeSessions {
    pub(super) sessions: Mutex<Vec<Session>>,
    pub(super) get_error: Mutex<Option<Fail>>,
    pub(super) add_error: Mutex<Option<Fail>>,
    pub(super) remove_error: Mutex<Option<Fail>>,
    pub(super) mode_error: Mutex<Option<Fail>>,
    pub(super) scope_error: Mutex<Option<Fail>>,
    pub(super) title_error: Mutex<Option<Fail>>,
    pub(super) complete_error: Mutex<Option<Fail>>,
    pub(super) delete_error: Mutex<Option<Fail>>,
    pub(super) delete_result: Mutex<bool>,
    pub(super) list_error: Mutex<Option<Fail>>,
    pub(super) collected_list_error: Mutex<Option<Fail>>,
    pub(super) collect_error: Mutex<Option<Fail>>,
    pub(super) uncollect_error: Mutex<Option<Fail>>,
    pub(super) collected: Mutex<HashMap<(String, String), u64>>,
}

impl EdgeSessions {
    pub(super) fn with_session(session: Session) -> Self {
        Self {
            sessions: Mutex::new(vec![session]),
            delete_result: Mutex::new(true),
            ..Self::default()
        }
    }

    pub(super) async fn store(&self, fail: &Mutex<Option<Fail>>) -> Result<(), SessionUseCaseError> {
        match *fail.lock().await {
            Some(failure) => Err(failure.error()),
            None => Ok(()),
        }
    }
}

#[async_trait::async_trait]
impl SessionManagementService for EdgeSessions {
    async fn create_or_reactivate(
        &self,
        _cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        Err(SessionUseCaseError::Conflict("not used by edge tests".to_string()))
    }

    async fn get(&self, sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
        self.store(&self.get_error).await?;
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
        self.store(&self.list_error).await?;
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
                participant_id.is_none_or(|id| s.participants.iter().any(|p| p.bot_uuid == id))
            })
            .skip(offset as usize)
            .take(limit as usize)
            .cloned()
            .collect())
    }

    async fn complete_if_running(
        &self,
        sid: &str,
        output: Option<Value>,
        error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        self.store(&self.complete_error).await?;
        let mut stored = self.sessions.lock().await;
        let Some(session) = stored.iter_mut().find(|s| s.id == sid) else {
            return Ok(None);
        };
        if session.status != SessionStatus::Running {
            return Ok(None);
        }
        session.status = SessionStatus::Completed;
        session.output = output;
        session.error_message = error;
        Ok(Some(session.clone()))
    }

    async fn add_participant(
        &self,
        sid: &str,
        participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        self.store(&self.add_error).await?;
        let mut stored = self.sessions.lock().await;
        let Some(session) = stored.iter_mut().find(|s| s.id == sid) else {
            return Err(SessionUseCaseError::NotFound(sid.to_string()));
        };
        session
            .participants
            .retain(|p| p.bot_uuid != participant.bot_uuid);
        session.participants.push(participant);
        Ok(session.clone())
    }

    async fn remove_participant(
        &self,
        sid: &str,
        bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        self.store(&self.remove_error).await?;
        let mut stored = self.sessions.lock().await;
        let Some(session) = stored.iter_mut().find(|s| s.id == sid) else {
            return Err(SessionUseCaseError::NotFound(sid.to_string()));
        };
        let before = session.participants.len();
        session.participants.retain(|p| p.bot_uuid != bot_uuid);
        if session.participants.len() == before {
            return Err(SessionUseCaseError::NotFound(bot_uuid.to_string()));
        }
        Ok(session.clone())
    }

    async fn update_participant_mode(
        &self,
        sid: &str,
        bot_uuid: &str,
        mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        self.store(&self.mode_error).await?;
        let mut stored = self.sessions.lock().await;
        let Some(session) = stored.iter_mut().find(|s| s.id == sid) else {
            return Err(SessionUseCaseError::NotFound(sid.to_string()));
        };
        let Some(participant) = session
            .participants
            .iter_mut()
            .find(|p| p.bot_uuid == bot_uuid)
        else {
            return Err(SessionUseCaseError::NotFound(bot_uuid.to_string()));
        };
        participant.mode = Some(mode);
        Ok(session.clone())
    }

    async fn update_participant_message_view_scope(
        &self,
        sid: &str,
        actor_id: &str,
        message_view_scope: bcs_domain::MessageViewScope,
    ) -> Result<Session, SessionUseCaseError> {
        self.store(&self.scope_error).await?;
        let mut stored = self.sessions.lock().await;
        let Some(session) = stored.iter_mut().find(|s| s.id == sid) else {
            return Err(SessionUseCaseError::NotFound(sid.to_string()));
        };
        let Some(participant) = session
            .participants
            .iter_mut()
            .find(|p| p.bot_uuid == actor_id)
        else {
            return Err(SessionUseCaseError::NotFound(actor_id.to_string()));
        };
        participant.message_view_scope = message_view_scope;
        Ok(session.clone())
    }

    async fn update_participant_mode_and_message_view_scope(
        &self,
        sid: &str,
        actor_id: &str,
        mode: Option<ParticipantMode>,
        message_view_scope: bcs_domain::MessageViewScope,
    ) -> Result<Session, SessionUseCaseError> {
        self.update_participant_message_view_scope(sid, actor_id, message_view_scope)
            .await?;
        if let Some(mode) = mode {
            self.update_participant_mode(sid, actor_id, mode).await
        } else {
            self.get(sid)
                .await?
                .ok_or_else(|| SessionUseCaseError::NotFound(sid.to_string()))
        }
    }

    async fn update_title(
        &self,
        sid: &str,
        title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        self.store(&self.title_error).await?;
        let mut stored = self.sessions.lock().await;
        let Some(session) = stored.iter_mut().find(|s| s.id == sid) else {
            return Err(SessionUseCaseError::NotFound(sid.to_string()));
        };
        session.session_title = title;
        Ok(session.clone())
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn delete(&self, sid: &str) -> Result<bool, SessionUseCaseError> {
        self.store(&self.delete_error).await?;
        let deleted = *self.delete_result.lock().await;
        if deleted {
            self.sessions.lock().await.retain(|s| s.id != sid);
        }
        Ok(deleted)
    }

    async fn collect(&self, sid: &str, bot_uuid: &str) -> Result<(), SessionUseCaseError> {
        self.store(&self.collect_error).await?;
        self.collected
            .lock()
            .await
            .insert((sid.to_string(), bot_uuid.to_string()), 42);
        Ok(())
    }

    async fn uncollect(&self, sid: &str, bot_uuid: &str) -> Result<(), SessionUseCaseError> {
        self.store(&self.uncollect_error).await?;
        self.collected
            .lock()
            .await
            .remove(&(sid.to_string(), bot_uuid.to_string()));
        Ok(())
    }

    async fn list_collected_by_group(
        &self,
        group_id: &str,
        bot_uuid: &str,
        _status: Option<SessionStatus>,
        _title_contains: Option<&str>,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        self.store(&self.collected_list_error).await?;
        let collected = self.collected.lock().await;
        Ok(self
            .sessions
            .lock()
            .await
            .iter()
            .filter(|s| s.group_id == group_id)
            .filter(|s| {
                collected.contains_key(&(s.id.clone(), bot_uuid.to_string()))
            })
            .cloned()
            .collect())
    }

    async fn collected_at_map(
        &self,
        session_ids: &[&str],
        bot_uuid: &str,
    ) -> Result<Vec<(String, u64)>, SessionUseCaseError> {
        let collected = self.collected.lock().await;
        Ok(session_ids
            .iter()
            .filter_map(|sid| {
                collected
                    .get(&(sid.to_string(), bot_uuid.to_string()))
                    .map(|at| (sid.to_string(), *at))
            })
            .collect())
    }
}

// ---------------------------------------------------------------
// Scripted Session launch
// ---------------------------------------------------------------

/// Returns one configured launch error, or a freshly created Session, and
/// records every command the route forwarded.
pub(super) struct ScriptedLaunch {
    pub(super) error: Mutex<Option<LaunchFailure>>,
    pub(super) created: Mutex<bool>,
    pub(super) create_calls: Mutex<Vec<CreateSessionLaunch>>,
}

#[derive(Clone, Copy)]
pub(super) enum LaunchFailure {
    GroupNotFound,
    SessionNotFound,
    Forbidden,
    InvalidRole,
    InvalidRequest,
    Conflict,
    Runtime,
    Internal,
}

impl ScriptedLaunch {
    pub(super) fn failing(error: LaunchFailure) -> Arc<Self> {
        Arc::new(Self {
            error: Mutex::new(Some(error)),
            created: Mutex::new(true),
            create_calls: Mutex::new(Vec::new()),
        })
    }

    fn outcome(&self, group_id: &str, created: bool) -> SessionLaunchOutcome {
        SessionLaunchOutcome {
            session: session_for(group_id, SessionKind::Chat),
            created,
            state_machine_run: None,
            initial_run: None,
        }
    }

    /// Keep the configured failure armed: it is a peek, not a one-shot take.
    async fn configured_failure(&self) -> Option<LaunchFailure> {
        *self.error.lock().await
    }
}

#[async_trait::async_trait]
impl SessionLaunchService for ScriptedLaunch {
    async fn create(
        &self,
        command: CreateSessionLaunch,
    ) -> Result<SessionLaunchOutcome, SessionLaunchError> {
        self.create_calls.lock().await.push(command.clone());
        match self.configured_failure().await {
            Some(LaunchFailure::GroupNotFound) => Err(SessionLaunchError::GroupNotFound(
                command.request.group_id.clone(),
            )),
            Some(LaunchFailure::SessionNotFound) => Err(SessionLaunchError::SessionNotFound(
                "group-1:deadbeef".to_string(),
            )),
            Some(LaunchFailure::Forbidden) => {
                Err(SessionLaunchError::Forbidden("not your group".to_string()))
            }
            Some(LaunchFailure::InvalidRole) => {
                Err(SessionLaunchError::InvalidRole("bad role".to_string()))
            }
            Some(LaunchFailure::InvalidRequest) => {
                Err(SessionLaunchError::InvalidRequest("bad request".to_string()))
            }
            Some(LaunchFailure::Conflict) => {
                Err(SessionLaunchError::Conflict("already running".to_string()))
            }
            Some(LaunchFailure::Runtime) => Err(SessionLaunchError::Runtime(
                bcs_service_api::CollaborationRuntimeError::InvalidRequest("runtime".to_string()),
            )),
            Some(LaunchFailure::Internal) => Err(SessionLaunchError::Internal(
                bcs_service_api::ServiceError::InternalError("launch store down".to_string()),
            )),
            None => {
                let created = *self.created.lock().await;
                Ok(self.outcome(&command.request.group_id, created))
            }
        }
    }

    async fn reactivate(
        &self,
        command: ReactivateSessionLaunch,
    ) -> Result<SessionLaunchOutcome, SessionLaunchError> {
        self.create(CreateSessionLaunch {
            request: command.request,
        })
        .await
    }
}

// ---------------------------------------------------------------
// Failing system-message notifier
// ---------------------------------------------------------------

/// Notification is best-effort: the routes log a warning and still answer.
/// The counter proves the route actually attempted the notification.
pub(super) struct FailingSystemMessages {
    calls: AtomicUsize,
}

impl FailingSystemMessages {
    pub(super) fn new() -> Self {
        Self {
            calls: AtomicUsize::new(0),
        }
    }

    pub(super) fn calls(&self) -> usize {
        self.calls.load(Ordering::Relaxed)
    }
}

#[async_trait::async_trait]
impl bcs_service_api::SystemMessageService for FailingSystemMessages {
    async fn notify(
        &self,
        _group_id: &str,
        _event: bcs_domain::SystemMessageEvent,
        _session_id: &str,
        _session_participants: &[Participant],
    ) -> bcs_service_api::ServiceResult<usize> {
        self.calls.fetch_add(1, Ordering::Relaxed);
        Err(bcs_service_api::ServiceError::InternalError(
            "notify down".to_string(),
        ))
    }
}

// ---------------------------------------------------------------
// Fixture
// ---------------------------------------------------------------

pub(super) struct Env {
    pub(super) app: Router,
    pub(super) sessions: Arc<EdgeSessions>,
    pub(super) registry: Arc<BotCore>,
    pub(super) groups: Arc<GroupStore>,
    pub(super) launch: Arc<ScriptedLaunch>,
    _temp: TempDir,
}

pub(super) struct EnvBuilder {
    pub(super) registry: Arc<BotCore>,
    pub(super) groups: Arc<GroupStore>,
    pub(super) sessions: Arc<EdgeSessions>,
    pub(super) launch: Arc<ScriptedLaunch>,
    services: Services,
    authority: Arc<dyn bcs_service_api::port::bot_authority::BotAuthorityPort>,
    session_application: Option<Arc<dyn bcs_service_api::application::v1::session::SessionService>>,
    staff: Option<String>,
    _temp: TempDir,
}

impl EnvBuilder {
    pub(super) fn authority(
        mut self,
        hook: Arc<dyn bcs_service_api::port::bot_authority::BotAuthorityPort>,
    ) -> Self {
        self.authority = hook;
        self
    }

    pub(super) fn session_application(
        mut self,
        service: Arc<dyn bcs_service_api::application::v1::session::SessionService>,
    ) -> Self {
        self.session_application = Some(service);
        self
    }

    pub(super) fn launch(mut self, launch: Arc<ScriptedLaunch>) -> Self {
        self.services.session_launch = launch.clone();
        self.launch = launch;
        self
    }

    pub(super) fn history(
        mut self,
        service: Arc<dyn bcs_service_api::GroupMessageHistoryService>,
    ) -> Self {
        self.services.group_message_history = service;
        self
    }

    pub(super) fn message_flow(
        mut self,
        service: Arc<dyn bcs_service_api::MessageFlowService>,
    ) -> Self {
        self.services.message_flow = service;
        self
    }

    pub(super) fn system_message(
        mut self,
        service: Arc<dyn bcs_service_api::SystemMessageService>,
    ) -> Self {
        self.services.system_message = service;
        self
    }

    pub(super) fn build(self) -> Env {
        let mut services = self.services;
        services.bot_authority_hook = self.authority;
        let identity_port: Arc<dyn UserIdentityPort + Send + Sync> = Arc::new(StaticHumanIdentity {
            staff_no: self.staff.clone(),
        });
        let mut state = HttpAppState::new(services).with_user_identity(identity_port);
        if let Some(application) = self.session_application {
            state = state.with_session_application(application);
        }
        Env {
            app: build_router(state),
            sessions: self.sessions,
            registry: self.registry,
            groups: self.groups,
            launch: self.launch,
            _temp: self._temp,
        }
    }
}

/// Build the standard fixture for the requested Group strategy. `staff`
/// controls the ambient Human identity (None means unauthenticated unless a
/// Bot token is sent).
pub(super) async fn env(strategy: GroupStrategy, staff: Option<&str>) -> Env {
    builder(strategy, staff).await.build()
}

pub(super) async fn builder(strategy: GroupStrategy, staff: Option<&str>) -> EnvBuilder {
    let temp = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    register_bot(&registry, "worker-bot", "Worker").await;
    register_bot(&registry, "extra-bot", "Extra").await;
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    registry
        .store_token_mapping("bot-token".to_string(), "worker-bot".to_string())
        .await;
    registry
        .ensure_human_actor("alice", "Alice")
        .await
        .unwrap();

    let groups = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-1",
        "driver-bot",
        vec![
            participant("driver-bot", "Driver", ParticipantRole::Driver, ActorKind::Bot),
            participant(
                "worker-bot",
                "Worker",
                ParticipantRole::Consultant,
                ActorKind::Bot,
            ),
            participant("extra-bot", "Extra", ParticipantRole::Consultant, ActorKind::Bot),
        ],
    );
    group.group_strategy = strategy;
    groups.upsert(group).await.unwrap();

    let sessions = Arc::new(EdgeSessions::with_session(session_for(
        "group-1",
        SessionKind::Chat,
    )));

    let mut services = Services::noop();
    services.registry = registry.clone();
    services.group = groups.clone();
    services.session_management = sessions.clone();
    let launch = Arc::new(ScriptedLaunch {
        error: Mutex::new(None),
        created: Mutex::new(true),
        create_calls: Mutex::new(Vec::new()),
    });
    services.session_launch = launch.clone();

    EnvBuilder {
        registry,
        groups,
        sessions,
        launch,
        services,
        authority: bcs_test_support::lazy_authority_chain().hook,
        session_application: None,
        staff: staff.map(str::to_string),
        _temp: temp,
    }
}

fn participant(
    bot_uuid: &str,
    name: &str,
    role: ParticipantRole,
    actor_kind: ActorKind,
) -> Participant {
    Participant {
        bot_uuid: bot_uuid.to_string(),
        bot_name: Some(name.to_string()),
        kind: None,
        role,
        actor_kind,
        mode: Some(ParticipantMode::default_for(actor_kind)),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    }
}

pub(super) async fn register_bot(registry: &BotCore, bot_id: &str, name: &str) {
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

pub(super) fn session_for(group_id: &str, kind: SessionKind) -> Session {
    Session {
        id: SESSION_ID.to_string(),
        group_id: group_id.to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: kind,
        participants: vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::bot("worker-bot", ParticipantRole::Consultant),
        ],
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
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
    }
}

// ---------------------------------------------------------------
// Request helpers
// ---------------------------------------------------------------

pub(super) async fn call(app: &Router, request: Request<Body>) -> (StatusCode, Value) {
    let response = app.clone().oneshot(request).await.unwrap();
    let status = response.status();
    let bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let body: Value = if bytes.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(&bytes).unwrap_or(Value::Null)
    };
    (status, body)
}

pub(super) fn get(uri: &str) -> Request<Body> {
    Request::builder()
        .method("GET")
        .uri(uri)
        .body(Body::empty())
        .unwrap()
}

pub(super) fn get_as(uri: &str, token: &str) -> Request<Body> {
    Request::builder()
        .method("GET")
        .uri(uri)
        .header("authorization", format!("Bearer {token}"))
        .body(Body::empty())
        .unwrap()
}

pub(super) fn json_as(method: &str, uri: &str, token: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .header("authorization", format!("Bearer {token}"))
        .body(Body::from(body.to_string()))
        .unwrap()
}

pub(super) fn json_body(method: &str, uri: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap()
}

pub(super) fn empty(method: &str, uri: &str) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .body(Body::empty())
        .unwrap()
}

pub(super) fn empty_as(method: &str, uri: &str, token: &str) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("authorization", format!("Bearer {token}"))
        .body(Body::empty())
        .unwrap()
}

// ---------------------------------------------------------------
// Identity
// ---------------------------------------------------------------

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
}
