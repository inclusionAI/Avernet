//! Shared real-DB auth test harness for Task 13 — V1 + legacy routers over a
//! real SQLite-backed identity stack with an injectable `DbPlugin` decorator.
//!
//! The harness builds:
//! - `LocalSqliteDbPlugin` with the `bcs_user_identities` schema
//!   (migration 028 columns + indexes)
//! - `DbUserIdentityStore` (a single instance shared by every port)
//! - `RepoAuthSessionIdentityPort` (the production strict bridge)
//! - `OAuthSessionEngine` over the bridge
//! - `build_api_auth` (Task 12) — the production V1 verifier + V1 auth
//!   service + CSRF origins
//! - `build_oauth_entry_services` — the production LEGACY `/auth/*` service
//!   (same shared engine/identities)
//! - assembled V1 + legacy routers running in-process via `tower::oneshot`
//!
//! An injectable `DbPlugin` sits between the SQLite plugin and the store so a
//! test can fail ALL `query` or `execute` calls (`FaultyDbPlugin`) or hold
//! ONE matching `query`/`execute` call on a `tokio::sync::Barrier` until the
//! race driver releases it (`BarrierDbPlugin`).
//!
//! `StubUserIdentityPort` is the only test stub: the LEGACY `/auth/user` GET
//! handler is its only consumer, and no race test reaches that handler. The
//! shared `bcs_session` cookie path (the strict engine + bridge + store) is
//! the production path; nothing about it is faked.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test assertions intentionally fail fast"
)]

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::Router;
use tokio::sync::Barrier;

use bcs::api_auth_registry::default_api_auth_registrations;
use bcs::api_auth_wiring::{
    ApiAuthAssemblyInputs, BuiltApiAuth, build_api_auth, build_oauth_entry_services,
    provider_instance_map,
};
use bcs::auth_wiring::build_oauth_provider;
use bcs::identity_session_wiring::RepoAuthSessionIdentityPort;
use bcs_api_http::ApiState;
use bcs_auth_api::{
    AuthError, AuthSessionIdentityPort, InstallSession, OAuthConfig, OAuthProvider,
    PendingOAuthLoginStore, SessionScope, SessionVersion, SessionWrite, UserIdentityInfo,
    UserIdentityPort,
};
use bcs_auth_oauth::{MemoryPendingOAuthLoginStore, OAuthSessionEngine};
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement,
    DbTransactionStep, DbTransactionStepResult,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_http::oauth::{OAuthRouteState, routes as legacy_oauth_routes};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, ApplicationError, CreateBotFriendRequest, CreateGroup,
    CreateGroupInvitation, CreateSession, CreateSessionInvitation, CreateSessionOutcome,
    DeleteBotFriendship, DeleteGroup, DeleteGroupParticipant, DeleteResult, DeleteSession,
    DeleteSessionParticipant, Friendship, FriendshipService, FriendRequest, GetGroup, GetSession,
    GroupDetail, GroupService, GroupSummary, Invitation, InvitationAcceptResult,
    InvitationService, IssueRegisterToken, ListBotFriendRequests, ListBotFriendships, ListGroups,
    ListSessionMessages, ListSessions, Page, RegisterBot, RegisterService, RegisterTokenView,
    RejectFriendRequest, SessionCompletionResult, SessionDetail, SessionMessageService,
    SessionParticipant, SessionService, SessionSummary, UpdateGroup, UpdateGroupParticipant,
    UpdateSession, UpdateSessionParticipant,
};
use bcs_service_api::port::secret::{SecretAccessError, SecretAccessPort, SecretRecord};
use bcs_user_identity::DbUserIdentityStore;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

pub const JWT_SECRET: &str = "test-session-signing-secret-32b";
pub const ENV: &str = "local";
pub const ORIGIN: &str = "https://workbench.example";
pub const BASE_URL: &str = "https://bcs.example.com";
pub const PROVIDER: &str = "github";

pub const V1_GROUPS: &str = "/openapi/v1/collaboration/groups";
pub const V1_REFRESH: &str = "/openapi/v1/auth/refresh";
pub const V1_LOGOUT: &str = "/openapi/v1/auth/logout";
pub const V1_USER: &str = "/openapi/v1/auth/user";
pub const V1_CALLBACK_GITHUB: &str = "/openapi/v1/auth/callback/github";

pub const LEGACY_REFRESH: &str = "/auth/refresh";
pub const LEGACY_LOGOUT: &str = "/auth/logout";

/// Cookie header builder.
pub fn cookie(jwt: &str) -> String {
    format!("bcs_session={jwt}")
}

// ---------------------------------------------------------------------------
// Decorator #1: FaultyDbPlugin — fail every `query` AND/OR every `execute`.
// ---------------------------------------------------------------------------

pub struct FaultyDbPlugin {
    inner: Arc<dyn DbPlugin>,
    pub fail_query: AtomicBool,
    pub fail_execute: AtomicBool,
}

impl FaultyDbPlugin {
    pub fn new(inner: Arc<dyn DbPlugin>) -> Self {
        Self {
            inner,
            fail_query: AtomicBool::new(false),
            fail_execute: AtomicBool::new(false),
        }
    }
}

#[async_trait]
impl DbPlugin for FaultyDbPlugin {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        if self.fail_query.load(Ordering::SeqCst) {
            return Err(DbError::Backend("injected-faulty-query".into()));
        }
        self.inner.query(statement).await
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        if self.fail_execute.load(Ordering::SeqCst) {
            return Err(DbError::Backend("injected-faulty-execute".into()));
        }
        self.inner.execute(statement).await
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

// ---------------------------------------------------------------------------
// Decorator #2: BarrierDbPlugin — single-use hold on a SQL-op matching a
// substring. The first matching call blocks on `barrier.wait()` until a
// race driver participates in the same barrier (which has n=2). Subsequent
// calls pass through to the inner plugin untouched (single-use).
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BarrierOp {
    Query,
    Execute,
}

/// The barrier can fire either BEFORE the inner call (so the held operation
/// has NOT yet committed) or AFTER the inner call returns Ok (so the inner
/// commit is done and only the caller's continuation is paused). After is
/// used by the "rotate commits but the refresh HTTP response is paused"
/// race (§8.5 必须保证的并发结果 #2: refresh(A→B) 先提交，随后 logout(A)
/// 撤销同实例 B 即便 refresh HTTP 响应迟到) — inner returns Ok(Applied),
/// the barrier holds refresh's continuation, logout lands on the other
/// entry meanwhile, and B ends up revoked before refresh issues Set-Cookie.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BarrierTiming {
    BeforeInner,
    AfterInner,
}

pub struct BarrierDbPlugin {
    inner: Arc<dyn DbPlugin>,
    barrier: Arc<Barrier>,
    sql_substring: String,
    op: BarrierOp,
    timing: BarrierTiming,
    triggered: AtomicBool,
}

impl BarrierDbPlugin {
    pub fn new(
        inner: Arc<dyn DbPlugin>,
        barrier: Arc<Barrier>,
        op: BarrierOp,
        sql_substring: impl Into<String>,
    ) -> Self {
        Self::with_timing(inner, barrier, op, sql_substring, BarrierTiming::BeforeInner)
    }

    pub fn with_timing(
        inner: Arc<dyn DbPlugin>,
        barrier: Arc<Barrier>,
        op: BarrierOp,
        sql_substring: impl Into<String>,
        timing: BarrierTiming,
    ) -> Self {
        Self {
            inner,
            barrier,
            sql_substring: sql_substring.into(),
            op,
            timing,
            triggered: AtomicBool::new(false),
        }
    }
}

#[async_trait]
impl DbPlugin for BarrierDbPlugin {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let predicate_matches = self.op == BarrierOp::Query
            && !self.triggered.load(Ordering::SeqCst)
            && statement.sql().contains(&self.sql_substring);
        if predicate_matches && self.timing == BarrierTiming::BeforeInner {
            self.triggered.store(true, Ordering::SeqCst);
            let _ = self.barrier.wait().await;
        }
        let result = self.inner.query(statement).await?;
        if predicate_matches && self.timing == BarrierTiming::AfterInner {
            self.triggered.store(true, Ordering::SeqCst);
            let _ = self.barrier.wait().await;
        }
        Ok(result)
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        let predicate_matches = self.op == BarrierOp::Execute
            && !self.triggered.load(Ordering::SeqCst)
            && statement.sql().contains(&self.sql_substring);
        if predicate_matches && self.timing == BarrierTiming::BeforeInner {
            self.triggered.store(true, Ordering::SeqCst);
            let _ = self.barrier.wait().await;
        }
        let result = self.inner.execute(statement).await?;
        if predicate_matches && self.timing == BarrierTiming::AfterInner {
            self.triggered.store(true, Ordering::SeqCst);
            let _ = self.barrier.wait().await;
        }
        Ok(result)
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

// ---------------------------------------------------------------------------
// Counting secret access (no-op overhead resolved by name).
// ---------------------------------------------------------------------------

pub struct CountingSecretAccess {
    entries: HashMap<String, String>,
}

impl CountingSecretAccess {
    pub fn with_entries(entries: &[(&str, &str)]) -> Self {
        Self {
            entries: entries
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
        }
    }
}

#[async_trait]
impl SecretAccessPort for CountingSecretAccess {
    async fn get_secret(&self, name: &str) -> Result<SecretRecord, SecretAccessError> {
        match self.entries.get(name) {
            Some(value) => Ok(SecretRecord {
                name: name.to_string(),
                user: String::new(),
                value: value.clone(),
            }),
            None => Err(SecretAccessError::NotFound(name.to_string())),
        }
    }
}

// ---------------------------------------------------------------------------
// CountingGroupService — records `list_groups` calls; returns Ok(empty page).
// ---------------------------------------------------------------------------

#[derive(Default)]
pub struct CountingGroupService {
    pub list_groups_calls: Mutex<Vec<ListGroups>>,
}

impl CountingGroupService {
    pub fn list_groups_count(&self) -> usize {
        self.list_groups_calls.lock().expect("lock").len()
    }
}

#[async_trait]
impl GroupService for CountingGroupService {
    async fn list_groups(&self, command: ListGroups) -> Result<Page<GroupSummary>, ApplicationError> {
        let offset = command.offset;
        let limit = command.limit;
        self.list_groups_calls.lock().expect("lock").push(command);
        Ok(Page::empty(offset, limit))
    }
    async fn create(&self, _command: CreateGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn get(&self, _command: GetGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn update(&self, _command: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn delete(&self, _command: DeleteGroup) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn add_participant(
        &self,
        _command: bcs_service_api::application::v1::AddGroupParticipant,
    ) -> Result<bcs_service_api::application::v1::Participant, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn update_participant(
        &self,
        _command: UpdateGroupParticipant,
    ) -> Result<bcs_service_api::application::v1::Participant, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn delete_participant(
        &self,
        _command: DeleteGroupParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
}

// ---------------------------------------------------------------------------
// Noop stubs for the other ApiState services (mirrors bcs-api-http's
// `api_auth_boundary.rs` noop pattern). Only `list_groups` is observed; the
// rest never reach the protected boundary in this test.
// ---------------------------------------------------------------------------

struct NoopSessionService;

#[async_trait]
impl SessionService for NoopSessionService {
    async fn create(&self, _command: CreateSession) -> Result<CreateSessionOutcome, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn list(&self, _command: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn get(&self, _command: GetSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn update(&self, _command: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn delete(&self, _command: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn complete(
        &self,
        _command: bcs_service_api::application::v1::CompleteSession,
    ) -> Result<SessionCompletionResult, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn collect(
        &self,
        _command: bcs_service_api::application::v1::CollectSession,
    ) -> Result<bcs_service_api::application::v1::SessionCollectionResult, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn uncollect(
        &self,
        _command: bcs_service_api::application::v1::UncollectSession,
    ) -> Result<bcs_service_api::application::v1::SessionCollectionResult, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn add_participant(
        &self,
        _command: bcs_service_api::application::v1::AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn update_participant(
        &self,
        _command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn delete_participant(
        &self,
        _command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
}

struct NoopMessageService;

#[async_trait]
impl SessionMessageService for NoopMessageService {
    async fn list(&self, _command: ListSessionMessages) -> Result<Vec<bcs_service_api::GroupMessage>, ApplicationError> {
        Ok(Vec::new())
    }
}

struct NoopInvitationService;

#[async_trait]
impl InvitationService for NoopInvitationService {
    async fn create_group_invitation(
        &self,
        _command: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn create_session_invitation(
        &self,
        _command: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn accept_invitation(
        &self,
        _command: bcs_service_api::application::v1::AcceptInvitation,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
}

struct NoopRegisterService;

#[async_trait]
impl RegisterService for NoopRegisterService {
    async fn issue_register_token(
        &self,
        _command: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn register_bot(&self, _command: RegisterBot) -> Result<bcs_service_api::application::v1::BotRegistration, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
}

struct NoopFriendshipService;

#[async_trait]
impl FriendshipService for NoopFriendshipService {
    async fn list_bot_friendships(
        &self,
        _command: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        Ok(Page::empty(0, 20))
    }
    async fn delete_bot_friendship(
        &self,
        _command: DeleteBotFriendship,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn create_bot_friend_request(
        &self,
        _command: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn list_bot_friend_requests(
        &self,
        _command: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        Ok(Page::empty(0, 20))
    }
    async fn accept_friend_request(
        &self,
        _command: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
    async fn reject_friend_request(
        &self,
        _command: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
}

// ---------------------------------------------------------------------------
// Stub `UserIdentityPort` (legacy OAuth route state). Only `/auth/user`
// consumes it; race tests never do.
// ---------------------------------------------------------------------------

pub struct StubUserIdentityPort;

#[async_trait]
impl UserIdentityPort for StubUserIdentityPort {
    async fn ensure_identity(
        &self,
        _auth_source: &str,
        _external_user_id: &str,
        _external_user_name: Option<&str>,
        _avatar: Option<&str>,
        _env: &str,
    ) -> Result<String, AuthError> {
        Err(AuthError::LookupFailed("stub".into()))
    }
    async fn lookup_by_user_id(
        &self,
        _user_id: &str,
        _auth_source: &str,
    ) -> Result<Option<String>, AuthError> {
        Ok(None)
    }
    async fn get_identity_by_token(&self, _token: &str) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }
    async fn get_identity_by_user_id(&self, _user_id: &str) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }
}

pub fn empty_auth_chain() -> Arc<bcs_auth_api::AuthPluginChain> {
    Arc::new(bcs_auth_api::AuthPluginChain::new(Vec::new()))
}

// ---------------------------------------------------------------------------
// MySQL harness helpers (Part D) — env-driven; not configured ⇒ explicit
// failure message (never green-by-default).
// ---------------------------------------------------------------------------

pub fn mysql_test_url() -> Result<String, String> {
    match std::env::var("BCS_TEST_MYSQL_URL") {
        Ok(s) if !s.trim().is_empty() => Ok(s),
        Ok(_) => Err("BCS_TEST_MYSQL_URL is set but empty".to_string()),
        Err(_) => Err(
            "BCS_TEST_MYSQL_URL not set; live MySQL conformance is not run".to_string(),
        ),
    }
}

// ---------------------------------------------------------------------------
// Schema setup.
// ---------------------------------------------------------------------------

pub async fn create_user_identities_schema(db: &Arc<dyn DbPlugin>) {
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_user_identities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            auth_source TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            user_name TEXT DEFAULT NULL,
            external_user_name TEXT DEFAULT NULL,
            avatar TEXT DEFAULT NULL,
            token TEXT DEFAULT NULL,
            token_expire_at TEXT DEFAULT NULL,
            env TEXT NOT NULL,
            session_id TEXT,
            session_revision INTEGER NOT NULL DEFAULT 0,
            session_expires_at INTEGER NOT NULL DEFAULT 0,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )",
    ))
    .await
    .expect("create bcs_user_identities");
    db.execute(DbStatement::new(
        "CREATE UNIQUE INDEX uk_user_id ON bcs_user_identities(user_id)",
    ))
    .await
    .expect("create uk_user_id");
    db.execute(DbStatement::new(
        "CREATE UNIQUE INDEX uk_external ON bcs_user_identities(auth_source, external_user_id, env)",
    ))
    .await
    .expect("create uk_external");
}

// ---------------------------------------------------------------------------
// Top-level assembled stack.
// ---------------------------------------------------------------------------

pub struct AuthStack {
    /// The real backing plugin (no decorator). Use to plant raw SQL fixtures
    /// (corrupt rows, deletes) directly — the decorated store would mishandle
    /// the fixture.
    pub raw_db: Arc<dyn DbPlugin>,
    /// The decorated plugin exposed to the store.
    pub decorated_db: Arc<dyn DbPlugin>,
    pub store: Arc<DbUserIdentityStore>,
    pub identities: Arc<dyn AuthSessionIdentityPort>,
    pub engine: Arc<OAuthSessionEngine>,
    pub built: BuiltApiAuth,
    pub legacy_auth_service: Arc<dyn bcs_service_api::application::v1::AuthService>,
    pub v1_router: Router,
    pub legacy_router: Router,
    pub group_service: Arc<CountingGroupService>,
}

pub struct StackOptions {
    pub decorated_db: Option<Arc<dyn DbPlugin>>,
}

impl StackOptions {
    pub fn with_decorated(decorated_db: Arc<dyn DbPlugin>) -> Self {
        Self { decorated_db: Some(decorated_db) }
    }
    pub fn plain() -> Self {
        Self { decorated_db: None }
    }
}

fn api_auth_config() -> bcs_config_api::ApiAuthConfig {
    toml::from_str(&format!(
        r#"chain = ["github"]
public_base_url = "{BASE_URL}"
session_signing_key_secret = "session-key"
trusted_browser_origins = ["{ORIGIN}"]

[github]
client_id = "gh-client-id"
client_secret_secret = "gh-client-secret-ref"
"#,
    ))
    .expect("parse ApiAuthConfig")
}

fn build_secret_access() -> Arc<dyn SecretAccessPort> {
    Arc::new(CountingSecretAccess::with_entries(&[
        ("session-key", JWT_SECRET),
        ("gh-client-secret-ref", "gh-secret-value"),
    ]))
}

pub async fn build_stack(opts: StackOptions) -> AuthStack {
    let raw_db: Arc<dyn DbPlugin> =
        Arc::new(LocalSqliteDbPlugin::new().expect("open in-memory sqlite"));
    create_user_identities_schema(&raw_db).await;

    let decorated_db: Arc<dyn DbPlugin> = opts
        .decorated_db
        .unwrap_or_else(|| raw_db.clone());

    let store = Arc::new(DbUserIdentityStore::sqlite(decorated_db.clone()));
    let identities: Arc<dyn AuthSessionIdentityPort> =
        Arc::new(RepoAuthSessionIdentityPort::new(store.clone(), store.clone()));
    let engine = Arc::new(OAuthSessionEngine::new(
        bcs_jwt::OAuthSessionJwt::new(JWT_SECRET),
        identities.clone(),
        ENV.to_string(),
        1800,
    ));

    // Phase A: production V1 assembly (Task 12 — verifier + V1 auth_service +
    // origins). Pre-supply the session signing material so the build never
    // reaches the SecretAccessPort for it.
    let config = api_auth_config();
    let secret_access = build_secret_access();
    let inputs = ApiAuthAssemblyInputs {
        registrations: default_api_auth_registrations(),
        config,
        secret_access,
        sessions: identities.clone(),
        env: ENV.to_string(),
        oauth: Some(engine.clone()),
        session_signing_material: Some(JWT_SECRET.to_string()),
    };
    let built = build_api_auth(inputs).await.expect("build_api_auth");

    let mut oauth_providers: HashMap<String, Arc<dyn OAuthProvider>> = HashMap::new();
    {
        // Build the github OAuth provider via the SAME composition path used
        // by the legacy auth_wiring. A verified secret is needed to build the
        // provider struct; the provider CLIENT will never be invoked.
        let mut provider_settings = bcs_config_api::ProviderSettings::default();
        provider_settings.client_id = "gh-client-id".to_string();
        provider_settings.client_secret = Some(secrecy::Secret::new("gh-secret-value".to_string()));
        let provider = build_oauth_provider("github", &provider_settings)
            .expect("build github provider");
        oauth_providers.insert("github".to_string(), provider);
    }
    let chain_providers = provider_instance_map(oauth_providers);
    let pending: Arc<dyn PendingOAuthLoginStore> = Arc::new(MemoryPendingOAuthLoginStore::new());
    let oauth_config = OAuthConfig {
        jwt_secret: JWT_SECRET.to_string(),
        idle_timeout_minutes: 30,
        base_url: BASE_URL.to_string(),
        cookie_secure: false,
        env: ENV.to_string(),
        success_redirect_path: "/".to_string(),
    };
    let entry = build_oauth_entry_services(
        chain_providers,
        engine.clone(),
        identities.clone(),
        pending,
        &oauth_config,
    );

    // V1 router.
    let group_service = Arc::new(CountingGroupService::default());
    let state = ApiState::new(
        group_service.clone() as Arc<dyn GroupService>,
        Arc::new(NoopSessionService) as Arc<dyn SessionService>,
        Arc::new(NoopMessageService) as Arc<dyn SessionMessageService>,
        Arc::new(NoopInvitationService) as Arc<dyn InvitationService>,
        Arc::new(NoopRegisterService) as Arc<dyn RegisterService>,
        Arc::new(NoopFriendshipService) as Arc<dyn FriendshipService>,
        built.verifier.clone(),
    )
    .with_trusted_browser_origins(built.origins.clone())
    .with_auth_service(
        built.auth_service.clone().expect("OAuth chain → auth_service"),
        built.public_base_url.clone().unwrap_or_default(),
    );
    let v1_router = bcs_api_http::router(state);

    // Legacy `/auth/*` router.
    let legacy_state = Arc::new(OAuthRouteState::new(
        entry.legacy.clone(),
        JWT_SECRET,
        Arc::new(StubUserIdentityPort) as Arc<dyn UserIdentityPort>,
        oauth_config,
        Some(empty_auth_chain()),
        Some(Arc::new(vec![ORIGIN.to_string()])),
    ));
    let legacy_router = legacy_oauth_routes(legacy_state);

    AuthStack {
        raw_db,
        decorated_db,
        store,
        identities,
        engine,
        built,
        legacy_auth_service: entry.legacy,
        v1_router,
        legacy_router,
        group_service,
    }
}

// ---------------------------------------------------------------------------
// Session installation helpers.
// ---------------------------------------------------------------------------

/// Install a real live session through the production `engine.install` path
/// and return the `bcs_session` JWT cookie value.
pub async fn install_session(
    stack: &AuthStack,
    provider: &str,
    external_user_id: &str,
    display_name: &str,
) -> String {
    install_session_with_avatar(stack, provider, external_user_id, display_name, None).await
}

/// Same as [`install_session`] but seeds the identity row's avatar (the
/// trusted display data the strict snapshot carries back on verify).
pub async fn install_session_with_avatar(
    stack: &AuthStack,
    provider: &str,
    external_user_id: &str,
    display_name: &str,
    avatar: Option<&str>,
) -> String {
    let user_id = stack
        .identities
        .ensure_identity(provider, external_user_id, Some(display_name), avatar, ENV)
        .await
        .expect("ensure_identity");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let issued = stack
        .engine
        .install(
            SessionScope {
                user_id,
                provider: provider.to_string(),
                env: ENV.to_string(),
            },
            Some(display_name.to_string()),
            now,
        )
        .await
        .expect("install login session");
    issued.token
}

/// Low-level install — bypassing `engine.install` — to plant a live session
/// with a KNOWN `session_id`/`hash`. Used by tests that mint the JWT manually
/// (race scenario 5 — two stores sharing the same SQLite DB).
pub async fn install_session_for_identity(
    stack: &AuthStack,
    provider: &str,
    external_user_id: &str,
    sid: &str,
    hash: &str,
) -> SessionScope {
    let user_id = stack
        .identities
        .ensure_identity(provider, external_user_id, Some("Alice"), None, ENV)
        .await
        .expect("ensure_identity");
    let scope = SessionScope {
        user_id,
        provider: provider.to_string(),
        env: ENV.to_string(),
    };
    let outcome = stack
        .identities
        .install_login_session(InstallSession {
            scope: scope.clone(),
            expected_revision: 0,
            next: SessionVersion {
                session_id: sid.to_string(),
                revision: 1,
                token_hash: hash.to_string(),
            },
            expires_at: 5_000_000_000,
        })
        .await
        .expect("install_login_session");
    assert_eq!(outcome, SessionWrite::Applied, "fixture install must apply");
    scope
}

// ---------------------------------------------------------------------------
// HTTP helpers — deterministic in-process via `tower::oneshot`.
// ---------------------------------------------------------------------------

/// Run a request against a router; returns (status, set_cookie, body).
pub async fn send(
    router: &Router,
    method: axum::http::Method,
    uri: &str,
    cookie: Option<&str>,
    origin: Option<&str>,
) -> (axum::http::StatusCode, Option<String>, Option<String>) {
    use axum::body::Body;
    use axum::http::Request;
    use tower::ServiceExt;

    let mut builder = Request::builder().method(method).uri(uri);
    if let Some(c) = cookie {
        builder = builder.header("cookie", c);
    }
    if let Some(o) = origin {
        builder = builder.header("origin", o);
    }
    let request = builder.body(Body::empty()).expect("request");
    let response = router.clone().oneshot(request).await.expect("response");
    let status = response.status();
    let set_cookie = response
        .headers()
        .get(axum::http::header::SET_COOKIE)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());
    let body_bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("body bytes");
    let body = String::from_utf8_lossy(&body_bytes).into_owned();
    let body_opt = if body.is_empty() { None } else { Some(body) };
    (status, set_cookie, body_opt)
}

/// If `set_cookie` is Some, confirms it contains a non-empty, non-Max-Age=0
/// `bcs_session=...` value. Returns `None` when no Set-Cookie at all. Panics on
/// multiple non-empty bcs_session entries (callers want a definitive answer).
pub fn assert_no_set_cookie(set_cookie: &Option<String>) {
    if let Some(s) = set_cookie.as_ref() {
        assert!(
            !s.contains("bcs_session=") || s.contains("Max-Age=0"),
            "expected NO Set-Cookie bcs_session, got: {s}"
        );
    }
}

/// Confirm `set_cookie` is a non-empty bcs_session that is not Max-Age=0.
pub fn extract_session_token(set_cookie: &Option<String>) -> Option<String> {
    let s = set_cookie.as_ref()?;
    if !s.starts_with("bcs_session=") {
        return None;
    }
    let rest = &s["bcs_session=".len()..];
    let token = rest.split(';').next().unwrap_or("");
    if token.is_empty() || s.contains("Max-Age=0") {
        None
    } else {
        Some(token.to_string())
    }
}

// ---------------------------------------------------------------------------
// Race timeout wrapper — fail faster than a hung barrier.
// ---------------------------------------------------------------------------

pub async fn with_timeout<F, T>(future: F) -> T
where
    F: std::future::Future<Output = T>,
{
    match tokio::time::timeout(std::time::Duration::from_secs(15), future).await {
        Ok(value) => value,
        Err(_) => panic!("test timed out waiting for a barrier release or HTTP reply"),
    }
}
