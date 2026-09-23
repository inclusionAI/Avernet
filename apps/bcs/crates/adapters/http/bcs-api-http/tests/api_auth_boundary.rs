//! Boundary tests for the V1 API auth plugin chain provenance layer.
//!
//! These tests deliberately avoid the business facade; they exercise the
//! pure delivery-layer pieces introduced in Task 7:
//!   - `TrustedBrowserOrigins` (CSRF allow-list keyed off `AuthenticationContext`)
//!   - `CompositePrincipalVerifier` (config-order chain with no fallback after success)
//!   - `VerificationAttempt` (OAuth session cache, request-local)
//!   - HTTP-layer error mapping for Missing/Invalid/Forbidden/Unavailable/Internal
//!
//! Production still wires ONLY the Gateway verifier after this task. The
//! boundary tests cover the provenance/CSRF vocabulary that Tasks 8/12 will
//! reuse when OAuth/Sso cookie verifiers arrive.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Method, Request, StatusCode};
use bcs_api_http::{
    ApiState, AuthenticationContext, CompositePrincipalVerifier, CredentialKind,
    ErrorResponse, PrincipalVerificationError, PrincipalVerifier, TrustedBrowserOrigins,
    VerifiedRequestIdentity, VerificationAttempt, router,
};
use axum::response::IntoResponse;
use bcs_service_api::application::v1::ApplicationError;
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, AddSessionParticipant, AuthenticatedCaller,
    AuthenticatedUserIdentity, BotRegistration, CreateBotFriendRequest, CreateGroup,
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
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Minimal pure-Origin test (from the brief) — no business facade needed.
// ---------------------------------------------------------------------------

#[test]
fn trusted_browser_origins_rejects_cookie_unsafe_without_origin_and_accepts_non_cookie() {
    let origins = TrustedBrowserOrigins::new(vec!["https://workbench.example.com".into()])
        .expect("valid origins");
    let cookie = AuthenticationContext {
        source: "github".into(),
        credential_kind: CredentialKind::OAuthSessionCookie,
    };
    let gateway = AuthenticationContext {
        source: "gateway".into(),
        credential_kind: CredentialKind::GatewayPrincipalHeader,
    };
    assert!(origins.validate(&Method::POST, None, &cookie).is_err());
    assert!(origins.validate(&Method::POST, None, &gateway).is_ok());
}

// ---------------------------------------------------------------------------
// Counting fake verifier — used to assert exact call counts in the matrix.
// ---------------------------------------------------------------------------

#[derive(Clone)]
struct CountingOutcome {
    outcome: Arc<Mutex<Result<VerifiedRequestIdentity, PrincipalVerificationError>>>,
    calls: Arc<AtomicUsize>,
    is_oauth: bool,
}

impl CountingOutcome {
    fn ok_gateway() -> Self {
        Self::ok_with("gateway", CredentialKind::GatewayPrincipalHeader)
    }
    fn ok_with(source: &str, kind: CredentialKind) -> Self {
        Self::new(Ok(VerifiedRequestIdentity {
            caller: AuthenticatedCaller {
                tenant: Some("tenant-a".to_string()),
                user: Some(AuthenticatedUserIdentity {
                    id: "staff-1".to_string(),
                    username: "alice".to_string(),
                    display_name: None,
                    full_name: None,
                }),
                bot: None,
                app: None,
                access_key: None,
            },
            authentication_context: AuthenticationContext {
                source: source.to_string(),
                credential_kind: kind,
            },
            display: Default::default(),
        }))
    }
    fn err(kind: PrincipalVerificationError) -> Self {
        Self::new(Err(kind))
    }
    fn new(outcome: Result<VerifiedRequestIdentity, PrincipalVerificationError>) -> Self {
        Self {
            outcome: Arc::new(Mutex::new(outcome)),
            calls: Arc::new(AtomicUsize::new(0)),
            is_oauth: false,
        }
    }
    fn with_oauth(mut self) -> Self {
        self.is_oauth = true;
        self
    }
    fn calls(&self) -> usize {
        self.calls.load(Ordering::SeqCst)
    }
}

#[async_trait]
impl PrincipalVerifier for CountingOutcome {
    async fn verify(
        &self,
        _headers: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        self.outcome
            .lock()
            .expect("outcome lock")
            .clone()
            .map_err(|e| PrincipalVerificationError::clone(&e))
    }

    async fn verify_in_attempt(
        &self,
        headers: &HeaderMap,
        attempt: &mut VerificationAttempt,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        if !self.is_oauth {
            return self.verify(headers).await;
        }
        if attempt.oauth_session.is_none() {
            self.calls.fetch_add(1, Ordering::SeqCst);
            let inner = self
                .outcome
                .lock()
                .expect("outcome lock")
                .clone()
                .map_err(|e| PrincipalVerificationError::clone(&e));
            attempt.oauth_session = Some(inner);
        }
        match &attempt.oauth_session {
            Some(Ok(id)) => Ok(id.clone()),
            Some(Err(e)) => Err(PrincipalVerificationError::clone(e)),
            None => Err(PrincipalVerificationError::Internal),
        }
    }
}

// ---------------------------------------------------------------------------
// ProbeFriendshipService — records the bot-friendship list calls so the
// matrix can assert ZERO business-service usage for every rejection case.
// The router's downstream use-cases are all Noop; only the friendships POST
// path uses the probe (and the probe is held by the same Arc the ApiState
// stores, so tests observe the same mutex state).
// ---------------------------------------------------------------------------

#[derive(Default)]
struct ProbeFriendshipService {
    list_bot_friendship_calls: Mutex<Vec<ListBotFriendships>>,
    delete_bot_friendship_calls: Mutex<Vec<DeleteBotFriendship>>,
}

impl ProbeFriendshipService {
    fn total_calls(&self) -> usize {
        self.list_bot_friendship_calls
            .lock()
            .expect("list lock")
            .len()
            + self
                .delete_bot_friendship_calls
                .lock()
                .expect("delete lock")
                .len()
    }
}

#[async_trait]
impl FriendshipService for ProbeFriendshipService {
    async fn list_bot_friendships(
        &self,
        command: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        self.list_bot_friendship_calls
            .lock()
            .expect("calls lock")
            .push(command);
        Ok(Page {
            items: Vec::new(),
            total: 0,
            offset: 0,
            limit: 20,
        })
    }
    async fn delete_bot_friendship(
        &self,
        command: DeleteBotFriendship,
    ) -> Result<DeleteResult, ApplicationError> {
        self.delete_bot_friendship_calls
            .lock()
            .expect("calls lock")
            .push(command);
        Ok(DeleteResult { deleted: true })
    }
    async fn create_bot_friend_request(
        &self,
        _command: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        unimplemented!("probe")
    }
    async fn list_bot_friend_requests(
        &self,
        _command: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        unimplemented!("probe")
    }
    async fn accept_friend_request(
        &self,
        _command: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        unimplemented!("probe")
    }
    async fn reject_friend_request(
        &self,
        _command: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        unimplemented!("probe")
    }
}

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
        _command: AddSessionParticipant,
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

struct NoopGroupService;

#[async_trait]
impl GroupService for NoopGroupService {
    async fn list_groups(&self, _command: ListGroups) -> Result<Page<GroupSummary>, ApplicationError> {
        Err(ApplicationError::internal("noop"))
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

struct NoopMessageService;

#[async_trait]
impl SessionMessageService for NoopMessageService {
    async fn list(
        &self,
        _command: ListSessionMessages,
    ) -> Result<Vec<bcs_service_api::GroupMessage>, ApplicationError> {
        Err(ApplicationError::internal("noop"))
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
        _command: AcceptInvitation,
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
    async fn register_bot(&self, _command: RegisterBot) -> Result<BotRegistration, ApplicationError> {
        Err(ApplicationError::internal("noop"))
    }
}

fn build_state(
    verifier: Arc<dyn PrincipalVerifier>,
    origins: Option<TrustedBrowserOrigins>,
    probe: Arc<ProbeFriendshipService>,
) -> ApiState {
    let mut state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        probe,
        verifier,
    );
    if let Some(origins) = origins {
        state = state.with_trusted_browser_origins(origins);
    }
    state
}

async fn run_request(
    router: axum::Router,
    method: Method,
    uri: &str,
    auth_header: Option<&str>,
    origin: Option<&str>,
) -> StatusCode {
    let mut builder = Request::builder().method(method.clone()).uri(uri);
    if let Some(auth) = auth_header {
        builder = builder.header("x-test-auth", auth);
    }
    if let Some(origin) = origin {
        builder = builder.header("origin", origin);
    }
    let request = builder.body(Body::empty()).expect("request");
    let response = router.oneshot(request).await.expect("response");
    response.status()
}

/// Send a request that carries BOTH a Gateway signed-Principal header AND an
/// OAuth session cookie at once (the "mixed credential" form, §7.4). The
/// CountingOutcome fakes ignore headers, so the `gateway_header`/`cookie`
/// strings are descriptive rather than load-bearing, but they document the
/// intent of the matrix cell.
async fn run_request_mixed_credentials(
    router: axum::Router,
    method: Method,
    uri: &str,
    origin: Option<&str>,
    gateway_header: Option<&str>,
    cookie: Option<&str>,
) -> StatusCode {
    let mut builder = Request::builder().method(method.clone()).uri(uri);
    builder = builder.header("x-test-auth", "yes");
    if let Some(origin) = origin {
        builder = builder.header("origin", origin);
    }
    if let Some(gateway) = gateway_header {
        builder = builder.header("x-avernet-principal", gateway);
    }
    if let Some(cookie) = cookie {
        builder = builder.header("cookie", cookie);
    }
    let request = builder.body(Body::empty()).expect("request");
    let response = router.oneshot(request).await.expect("response");
    response.status()
}

/// GET on the friendships list route — CSRF check is skipped (GET is safe)
/// and the probe records each list call.
const FRIENDSHIPS_LIST_URI: &str = "/openapi/v1/collaboration/bots/bot-1/friendships";
/// DELETE on a single friendship — unsafe method (triggers CSRF when
/// `trusted_browser_origins` is configured with a cookie credential).
const FRIENDSHIPS_DELETE_URI: &str = "/openapi/v1/collaboration/bots/bot-1/friendships/bot-2";

// ---------------------------------------------------------------------------
// Full chain matrix tests.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn gateway_success_stops_chain_no_later_cookie_consultation() {
    let gateway = Arc::new(CountingOutcome::ok_gateway());
    let cookie = Arc::new(CountingOutcome::ok_with("github", CredentialKind::OAuthSessionCookie));
    let composite = Arc::new(CompositePrincipalVerifier::new(vec![
        gateway.clone() as Arc<dyn PrincipalVerifier>,
        cookie.clone() as Arc<dyn PrincipalVerifier>,
    ]));
    let probe = Arc::new(ProbeFriendshipService::default());
    let state = build_state(composite, None, probe.clone());
    // Origins=None -> CSRF skipped; GET on friendships list is a no-body safe
    // route wired through the v1 router. The probe records the list call.
    let status = run_request(
        router(state),
        Method::GET,
        FRIENDSHIPS_LIST_URI,
        Some("yes"),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK, "Gateway source authorizes the request");
    assert_eq!(gateway.calls(), 1, "gateway verifier ran once");
    assert_eq!(
        cookie.calls(),
        0,
        "cookie verifier MUST NOT be consulted after gateway success"
    );
    assert_eq!(probe.total_calls(), 1, "business service called once on success");
}

#[tokio::test]
async fn gateway_invalid_stops_chain_no_fallback_to_cookie() {
    let gateway = Arc::new(CountingOutcome::err(PrincipalVerificationError::Invalid));
    let cookie = Arc::new(CountingOutcome::ok_with("github", CredentialKind::OAuthSessionCookie));
    let composite = Arc::new(CompositePrincipalVerifier::new(vec![
        gateway.clone() as Arc<dyn PrincipalVerifier>,
        cookie.clone() as Arc<dyn PrincipalVerifier>,
    ]));
    let probe = Arc::new(ProbeFriendshipService::default());
    let state = build_state(composite, None, probe.clone());
    let status = run_request(
        router(state),
        Method::GET,
        FRIENDSHIPS_LIST_URI,
        Some("yes"),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "Invalid maps to 401");
    assert_eq!(gateway.calls(), 1);
    assert_eq!(cookie.calls(), 0, "Invalid stops the chain — no fallback");
    assert_eq!(probe.total_calls(), 0, "business service NOT called on rejection");
}

#[tokio::test]
async fn oauth_success_then_missing_origin_rejects_with_forbidden_no_business_call() {
    let oauth_only = Arc::new(
        CountingOutcome::ok_with("github", CredentialKind::OAuthSessionCookie).with_oauth(),
    );
    let composite = Arc::new(CompositePrincipalVerifier::new(vec![
        oauth_only.clone() as Arc<dyn PrincipalVerifier>,
    ]));
    let origins = TrustedBrowserOrigins::new(vec!["https://workbench.example.com".into()])
        .expect("valid origins");
    let probe = Arc::new(ProbeFriendshipService::default());
    let state = build_state(composite, Some(origins), probe.clone());
    // DELETE is unsafe -> CSRF runs against the OAuth cookie context with no
    // Origin header -> Forbidden (403).
    let status = run_request(
        router(state),
        Method::DELETE,
        FRIENDSHIPS_DELETE_URI,
        Some("yes"),
        None,
    )
    .await;
    assert_eq!(
        status,
        StatusCode::FORBIDDEN,
        "missing Origin on cookie unsafe method -> 403"
    );
    assert_eq!(oauth_only.calls(), 1, "OAuth verifier ran once (cached path)");
    assert_eq!(
        probe.total_calls(),
        0,
        "Origin rejection must NOT reach downstream use cases"
    );
}

#[tokio::test]
async fn oauth_success_with_matching_origin_authorizes_and_calls_business() {
    let oauth_only = Arc::new(
        CountingOutcome::ok_with("github", CredentialKind::OAuthSessionCookie).with_oauth(),
    );
    let composite = Arc::new(CompositePrincipalVerifier::new(vec![
        oauth_only.clone() as Arc<dyn PrincipalVerifier>,
    ]));
    let origins = TrustedBrowserOrigins::new(vec!["https://workbench.example.com".into()])
        .expect("valid origins");
    let probe = Arc::new(ProbeFriendshipService::default());
    let state = build_state(composite, Some(origins), probe.clone());
    let status = run_request(
        router(state),
        Method::DELETE,
        FRIENDSHIPS_DELETE_URI,
        Some("yes"),
        Some("https://workbench.example.com"),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(oauth_only.calls(), 1);
    assert_eq!(probe.total_calls(), 1, "business service called once on success");
}

// ---------------------------------------------------------------------------
// Mixed-credential matrix (§7.4). The request simultaneously carries BOTH a
// Gateway signed-Principal header AND an OAuth session cookie. The decision
// keys off the ACTUAL authenticated source (which verifier in the chain won),
// never off "request happens to carry a cookie/header".
// ---------------------------------------------------------------------------

/// §7.4 case 1: Gateway succeeds first. The actual authenticated source is
/// the Gateway Principal header. The cookie is present in the request but
/// must NOT trigger the cookie-credential Origin requirement. With
/// `trusted_browser_origins` configured AND no Origin on an unsafe method,
/// the chain-level CSRF check passes deterministically because the verified
/// `AuthenticationContext.credential_kind` is `GatewayPrincipalHeader`
/// (the non-cookie branch). The OAuth verifier in the chain is never
/// consulted.
#[tokio::test]
async fn mixed_credentials_gateway_success_overrides_cookie_no_origin_required() {
    let gateway = Arc::new(CountingOutcome::ok_gateway());
    let cookie =
        Arc::new(CountingOutcome::ok_with("github", CredentialKind::OAuthSessionCookie).with_oauth());
    let composite = Arc::new(CompositePrincipalVerifier::new(vec![
        gateway.clone() as Arc<dyn PrincipalVerifier>,
        cookie.clone() as Arc<dyn PrincipalVerifier>,
    ]));
    let origins = TrustedBrowserOrigins::new(vec!["https://workbench.example.com".into()])
        .expect("valid origins");
    let probe = Arc::new(ProbeFriendshipService::default());
    let state = build_state(composite, Some(origins), probe.clone());

    // DELETE (unsafe method) carries both credentials and NO Origin header.
    let status = run_request_mixed_credentials(
        router(state),
        Method::DELETE,
        FRIENDSHIPS_DELETE_URI,
        None,
        Some("dummy-gateway-jwt"),
        Some("bcs_session=opaque-session-token"),
    )
    .await;

    assert_eq!(
        status,
        StatusCode::OK,
        "Gateway success is the actual source; cookie presence must not trigger Origin"
    );
    assert_eq!(gateway.calls(), 1, "gateway verifier ran once and won");
    assert_eq!(
        cookie.calls(),
        0,
        "chain stopped at the first success — cookie verifier MUST NOT be consulted"
    );
    assert_eq!(
        probe.total_calls(),
        1,
        "business service called once after CSRF passes the non-cookie check"
    );
}

/// §7.4 case 2: OAuth verifier is the actual authenticated source. The
/// chain-level Gateway verifier returns MISSING (chain continues) and the
/// OAuth verifier succeeds with an `OAuthSessionCookie` credential. The CSRF
/// check now keys off THAT credential: an unsafe method without a matching
/// Origin returns Forbidden (403). The chain must NOT re-try the Gateway
/// (no fallback to Gateway), the Forbidden outcome must NOT re-run the
/// chain, and the business service must NOT be called. The fact that a
/// `x-avernet-principal` header is present in the request does not change
/// the actual authenticated source — the Gateway verifier already returned
/// Missing, so the OAuth verifier's result is what binds the
/// `AuthenticationContext`. (The brief's chain semantics: only `Missing`
/// continues to the next verifier; terminating the chain with `Invalid`
/// would short-circuit to 401 with no opportunity for the OAuth cookie to
/// bind, so this matrix uses `Missing` to prove the cookie-CSRF path
/// end-to-end.)
#[tokio::test]
async fn mixed_credentials_gateway_missing_cookie_succeeds_unsafe_no_origin_forbidden() {
    let gateway = Arc::new(CountingOutcome::err(PrincipalVerificationError::Missing));
    let cookie =
        Arc::new(CountingOutcome::ok_with("github", CredentialKind::OAuthSessionCookie).with_oauth());
    let composite = Arc::new(CompositePrincipalVerifier::new(vec![
        gateway.clone() as Arc<dyn PrincipalVerifier>,
        cookie.clone() as Arc<dyn PrincipalVerifier>,
    ]));
    let origins = TrustedBrowserOrigins::new(vec!["https://workbench.example.com".into()])
        .expect("valid origins");
    let probe = Arc::new(ProbeFriendshipService::default());
    let state = build_state(composite, Some(origins), probe.clone());

    let status = run_request_mixed_credentials(
        router(state),
        Method::DELETE,
        FRIENDSHIPS_DELETE_URI,
        None,
        Some("present-but-unrecognized-gateway-jwt"),
        Some("bcs_session=opaque-session-token"),
    )
    .await;

    assert_eq!(
        status,
        StatusCode::FORBIDDEN,
        "the actual authenticated source is the cookie; missing Origin on an unsafe \
         method -> 403; the still-attached Gateway header MUST NOT change the actual \
         authenticated source, and the chain MUST NOT fall back to retry Gateway"
    );
    assert_eq!(gateway.calls(), 1, "gateway verifier ran first and returned Missing");
    assert_eq!(
        cookie.calls(),
        1,
        "chain continued to the OAuth cookie verifier after Gateway returned Missing; \
         the OAuth verifier's result is the actual authentication source"
    );
    assert_eq!(
        probe.total_calls(),
        0,
        "Origin rejection must NOT reach downstream use cases; chain MUST NOT fall \
         back to Gateway after the Forbidden error"
    );
}

#[tokio::test]
async fn all_missing_returns_missing_maps_to_401_no_business_call() {
    let a = Arc::new(CountingOutcome::err(PrincipalVerificationError::Missing));
    let b = Arc::new(CountingOutcome::err(PrincipalVerificationError::Missing));
    let composite = Arc::new(CompositePrincipalVerifier::new(vec![
        a.clone() as Arc<dyn PrincipalVerifier>,
        b.clone() as Arc<dyn PrincipalVerifier>,
    ]));
    let probe = Arc::new(ProbeFriendshipService::default());
    let state = build_state(composite, None, probe.clone());
    let status = run_request(
        router(state),
        Method::GET,
        FRIENDSHIPS_LIST_URI,
        Some("yes"),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "All-Missing -> 401");
    assert_eq!(a.calls(), 1);
    assert_eq!(b.calls(), 1);
    assert_eq!(probe.total_calls(), 0);
}

#[tokio::test]
async fn forbidden_unavailable_internal_stop_chain_and_map_to_their_http_codes() {
    for (kind, expected) in [
        (PrincipalVerificationError::Forbidden, StatusCode::FORBIDDEN),
        (
            PrincipalVerificationError::Unavailable,
            StatusCode::SERVICE_UNAVAILABLE,
        ),
        (
            PrincipalVerificationError::Internal,
            StatusCode::INTERNAL_SERVER_ERROR,
        ),
    ] {
        let first = Arc::new(CountingOutcome::err(PrincipalVerificationError::clone(&kind)));
        let second = Arc::new(CountingOutcome::ok_gateway());
        let composite = Arc::new(CompositePrincipalVerifier::new(vec![
            first.clone() as Arc<dyn PrincipalVerifier>,
            second.clone() as Arc<dyn PrincipalVerifier>,
        ]));
        let probe = Arc::new(ProbeFriendshipService::default());
        let state = build_state(composite, None, probe.clone());
        let status = run_request(
            router(state),
            Method::GET,
            FRIENDSHIPS_LIST_URI,
            Some("yes"),
            None,
        )
        .await;
        assert_eq!(status, expected, "status for {:?}", kind);
        assert_eq!(first.calls(), 1);
        assert_eq!(second.calls(), 0, "later verifier NOT consulted after terminal err");
        assert_eq!(probe.total_calls(), 0, "business service NOT called");
    }
}

// ---------------------------------------------------------------------------
// VerificationAttempt caching behavior.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn verify_in_attempt_caches_underlying_oauth_result_within_a_request() {
    let oauth = Arc::new(
        CountingOutcome::ok_with("github", CredentialKind::OAuthSessionCookie).with_oauth(),
    );
    // Call the OAuth verifier's verify_in_attempt directly (NOT via the
    // composite, whose CompositePrincipalVerifier::verify_in_attempt
    // default-delegates to its own verify() which creates a fresh
    // internal attempt on every call).
    let verifier: Arc<dyn PrincipalVerifier> = oauth.clone() as Arc<dyn PrincipalVerifier>;
    let mut attempt = VerificationAttempt::default();
    let _ = verifier
        .verify_in_attempt(&HeaderMap::new(), &mut attempt)
        .await;
    let _ = verifier
        .verify_in_attempt(&HeaderMap::new(), &mut attempt)
        .await;
    assert_eq!(
        oauth.calls(),
        1,
        "cached Result reused — verify_in_attempt only performs the underlying verification once"
    );
}

#[tokio::test]
async fn verification_attempt_is_request_local_no_reuse_across_calls() {
    let oauth = Arc::new(
        CountingOutcome::ok_with("github", CredentialKind::OAuthSessionCookie).with_oauth(),
    );
    let verifier: Arc<dyn PrincipalVerifier> = oauth.clone() as Arc<dyn PrincipalVerifier>;
    let mut attempt_a = VerificationAttempt::default();
    let _ = verifier
        .verify_in_attempt(&HeaderMap::new(), &mut attempt_a)
        .await;
    let mut attempt_b = VerificationAttempt::default();
    let _ = verifier
        .verify_in_attempt(&HeaderMap::new(), &mut attempt_b)
        .await;
    assert_eq!(
        oauth.calls(),
        2,
        "a fresh attempt starts empty so cross-request caching is impossible"
    );
}

// ---------------------------------------------------------------------------
// TrustedBrowserOrigins shape rejection (mirrors Task 6's api_auth.rs).
// ---------------------------------------------------------------------------

#[test]
fn trusted_browser_origins_rejects_wildcard_userinfo_path_query_fragment_and_non_http() {
    for invalid in [
        "https://*.example.com",
        "https://user:pass@workbench.example.com",
        "https://workbench.example.com/path",
        "https://workbench.example.com/?q=1",
        "https://workbench.example.com#frag",
        "ftp://workbench.example.com",
        "not-a-url",
    ] {
        assert!(
            TrustedBrowserOrigins::new(vec![invalid.to_string()]).is_err(),
            "{invalid} should be rejected"
        );
    }
    assert!(TrustedBrowserOrigins::new(vec![
        "https://workbench.example.com".to_string(),
        "http://localhost:3000".to_string()
    ])
    .is_ok());
}

#[test]
fn trusted_browser_origins_rejects_duplicate_entries() {
    assert!(TrustedBrowserOrigins::new(vec![
        "https://workbench.example.com".to_string(),
        "https://workbench.example.com".to_string()
    ])
    .is_err());
}

// ---------------------------------------------------------------------------
// Default verify_in_attempt behavior — non-OAuth verifier should not
// populate the OAuth slot.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn default_verify_in_attempt_delegates_to_verify_when_not_overridden() {
    let gateway = CountingOutcome::ok_gateway();
    let verifier: Arc<dyn PrincipalVerifier> = Arc::new(gateway);
    let mut attempt = VerificationAttempt::default();
    let _ = verifier
        .verify_in_attempt(&HeaderMap::new(), &mut attempt)
        .await;
    assert!(attempt.oauth_session.is_none());
}

// ---------------------------------------------------------------------------
// Direct error response mapping — the 401/403/500/503 codes are produced at
// the HTTP envelope layer via the new ErrorResponse constructors.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn error_response_constructs_forbidden_unavailable_internal_codes() {
    let forbidden = ErrorResponse::forbidden("rid");
    let unavailable = ErrorResponse::unavailable("rid");
    let internal = ErrorResponse::internal("rid");

    let response = forbidden.into_response();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let _ = to_bytes(response.into_body(), usize::MAX).await.expect("forbidden body");

    let response = unavailable.into_response();
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    let _ = to_bytes(response.into_body(), usize::MAX).await.expect("unavailable body");

    let response = internal.into_response();
    assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    let _ = to_bytes(response.into_body(), usize::MAX).await.expect("internal body");
}
