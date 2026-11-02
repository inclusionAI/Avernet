//! Delivery authentication provenance and CSRF boundary (V1 API auth plan,
//! Task 7; spec §7.4, acceptance S1/S2).
//!
//! Three slices:
//! - `TrustedBrowserOrigins` construction and validate() semantics (the
//!   verbatim plan step-1 spec test included).
//! - `CompositePrincipalVerifier` ordering: only Missing continues; any
//!   other outcome is final and later sources are never consulted; the
//!   request-local `VerificationAttempt` prevents a second engine consult.
//! - The `verify_principal` middleware matrix, with counting fakes, for both
//!   a probe router (asserting the pinned provenance that reaches handlers)
//!   and the real V1 router: gateway-first success with a stray cookie must
//!   not trip cookie rules; cookie-first success with a valid Gateway header
//!   still requires a trusted Origin on unsafe methods; Origin rejection is
//!   a final 403 that never re-enters the chain; error taxonomy mapping.
//!
//! Source names and credential kinds are pinned by the registering wrapper
//! (constants in these fakes and in the real Gateway verifier), never from
//! request headers, cookies, or claims.

#![allow(clippy::expect_used, clippy::unwrap_used)]

// Shared Noop service impls; the remaining fixtures (HeaderVerifier,
// register-state builder) belong to other test targets and stay unused here.
#[allow(dead_code)]
mod register_support;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::extract::State;
use axum::http::{HeaderMap, HeaderValue, Request, StatusCode};
use axum::middleware::from_fn_with_state;
use axum::response::Response;
use axum::Router;
use bcs_api_http::{
    ApiState, AuthenticationContext, CompositePrincipalVerifier, CredentialKind,
    PrincipalVerificationError, PrincipalVerifier, TrustedBrowserOrigins, VerificationAttempt,
    VerifiedRequestIdentity, router,
};
use bcs_api_http::v1::common::{PrincipalVerificationState, verify_principal};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity,
    BotRegistration, CreateBotFriendRequest, DeleteBotFriendship, DeleteResult, FriendRequest,
    FriendRequestStatus, Friendship, FriendshipService, IssueRegisterToken, ListBotFriendRequests,
    ListBotFriendships, Page, RegisterBot, RegisterService, RegisterTokenView, RejectFriendRequest,
};
use register_support::{NoopGroupService, NoopInvitationService, NoopSessionMessageService, NoopSessionService};
use serde_json::Value;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Step-1 spec test (plan Task 7, verbatim)
// ---------------------------------------------------------------------------

// NOTE: kept verbatim, including the `axum::http::Method` qualification;
// `Method` is therefore not imported unqualified anywhere in this file.

#[test]
fn cookie_provenance_requires_trusted_origin_and_header_provenance_does_not() {
    let origins = TrustedBrowserOrigins::new(vec!["https://workbench.example.com".into()]).unwrap();
    let cookie = AuthenticationContext { source: "github".into(), credential_kind: CredentialKind::OAuthSessionCookie };
    let gateway = AuthenticationContext { source: "gateway".into(), credential_kind: CredentialKind::GatewayPrincipalHeader };
    assert!(origins.validate(&axum::http::Method::POST, None, &cookie).is_err());
    assert!(origins.validate(&axum::http::Method::POST, None, &gateway).is_ok());
}

// ---------------------------------------------------------------------------
// TrustedBrowserOrigins: constructor structure and validate() semantics
// ---------------------------------------------------------------------------

#[test]
fn trusted_browser_origins_constructor_enforces_exact_scheme_host_port() {
    // Accept the legal shapes (exact origins only).
    assert!(TrustedBrowserOrigins::new(vec![
        "https://workbench.example.com".into(),
        "http://127.0.0.1:8000".into(),
        "https://[2001:db8::1]:8443".into(),
    ])
    .is_ok());
    // Reject everything that would weaken exact matching.
    for entry in [
        "",
        "  ",
        "https://workbench.example.com/panel",
        "https://workbench.example.com?x=1",
        "https://workbench.example.com#frag",
        "https://*.example.com",
        "https://user:pass@example.com",
        "ftp://workbench.example.com",
        "example.com",
        "https://",
        "https://:8443",
        "https://example:not-a-port",
        "https://[::1",
        // Task 7 review tightening (Task 12): an unbracketed multi-colon
        // authority (a bare IPv6 literal or a malformed host:port:extra)
        // rejects instead of mis-splitting on the last colon.
        "https://2001:db8::1",
        "https://a:b:80",
    ] {
        assert!(TrustedBrowserOrigins::new(vec![entry.to_string()]).is_err(), "entry {entry:?}");
    }
    let duplicated = vec!["https://a.example.com".to_string(), "https://a.example.com".to_string()];
    assert!(TrustedBrowserOrigins::new(duplicated).is_err(), "duplicates");
    assert!(TrustedBrowserOrigins::new(vec![]).is_err(), "empty list");
}

#[test]
fn validate_applies_to_cookie_kinds_on_unsafe_methods_only() {
    let origins = TrustedBrowserOrigins::new(vec!["https://workbench.example.com".into()]).unwrap();
    for (source, kind) in [
        ("github", CredentialKind::OAuthSessionCookie),
        ("sso", CredentialKind::SsoCookie),
    ] {
        let context = AuthenticationContext { source: source.into(), credential_kind: kind.clone() };
        // Safe methods never need an Origin.
        assert!(origins.validate(&axum::http::Method::GET, None, &context).is_ok());
        assert!(origins.validate(&axum::http::Method::GET, Some("https://evil.example"), &context).is_ok());
        // Unsafe cookie writes need an exact trusted origin.
        assert!(origins.validate(&axum::http::Method::POST, Some("https://workbench.example.com"), &context).is_ok());
        assert!(origins.validate(&axum::http::Method::POST, None, &context).is_err());
        assert!(
            origins.validate(&axum::http::Method::POST, Some("https://workbench.example.org"), &context).is_err(),
            "host must match exactly",
        );
        assert!(
            origins.validate(&axum::http::Method::PUT, Some("http://workbench.example.com"), &context).is_err(),
            "scheme is part of the trust decision",
        );
        assert!(
            origins.validate(&axum::http::Method::DELETE, Some("HTTPS://WORKBENCH.EXAMPLE.COM"), &context).is_err(),
            "Origins match byte-exactly, not case-insensitively",
        );
    }
    // Explicit, non-cookie transfers carry no ambient authority.
    let explicit = AuthenticationContext { source: "connection-token".into(), credential_kind: CredentialKind::ExplicitToken };
    assert!(origins.validate(&axum::http::Method::POST, None, &explicit).is_ok());
    // The legacy marker never reaches the new chain; validate refuses it.
    let legacy = AuthenticationContext { source: "legacy-browser".into(), credential_kind: CredentialKind::LegacyBrowserBoundary };
    assert!(matches!(
        origins.validate(&axum::http::Method::POST, Some("https://workbench.example.com"), &legacy),
        Err(PrincipalVerificationError::Internal(_))
    ));
}

// ---------------------------------------------------------------------------
// Counting fakes
// ---------------------------------------------------------------------------

fn caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: "staff-1".into(),
            username: "alice".into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn pinned(caller: AuthenticatedCaller, source: &str, kind: CredentialKind) -> VerifiedRequestIdentity {
    VerifiedRequestIdentity::pin(caller, source, kind)
}

enum Prescript {
    Missing,
    Ok(&'static str, CredentialKind),
    Invalid,
    Forbidden,
    Unavailable,
    DependencyUnavailable,
    Internal,
}

/// A verifier whose outcome is fixed by the wrapper (never by request
/// content) and that counts how often it was consulted.
struct CountingVerifier {
    calls: AtomicUsize,
    prescript: Prescript,
}

impl CountingVerifier {
    fn new(prescript: Prescript) -> Arc<Self> {
        Arc::new(Self { calls: AtomicUsize::new(0), prescript })
    }

    fn calls(&self) -> usize {
        self.calls.load(Ordering::SeqCst)
    }
}

#[async_trait]
impl PrincipalVerifier for CountingVerifier {
    async fn verify(
        &self,
        _headers: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        match &self.prescript {
            Prescript::Missing => Err(PrincipalVerificationError::Missing),
            Prescript::Ok(source, kind) => Ok(pinned(caller(), source, kind.clone())),
            Prescript::Invalid => Err(PrincipalVerificationError::Invalid("invalid".into())),
            Prescript::Forbidden => Err(PrincipalVerificationError::Forbidden("forbidden".into())),
            Prescript::Unavailable => Err(PrincipalVerificationError::Unavailable),
            Prescript::DependencyUnavailable => Err(PrincipalVerificationError::DependencyUnavailable),
            Prescript::Internal => Err(PrincipalVerificationError::Internal("wiring".into())),
        }
    }
}

/// A verifier that emulates the strict-OAuth wrapper: it consults a shared
/// backing engine only when the request-local attempt has no cached result,
/// then stores the outcome for any later consult in the same pass.
struct OAuthEngineFake {
    engine_calls: AtomicUsize,
    outcome: Result<VerifiedRequestIdentity, PrincipalVerificationError>,
}

impl OAuthEngineFake {
    fn new(outcome: Result<VerifiedRequestIdentity, PrincipalVerificationError>) -> Arc<Self> {
        Arc::new(Self { engine_calls: AtomicUsize::new(0), outcome })
    }
}

#[async_trait]
impl PrincipalVerifier for OAuthEngineFake {
    async fn verify(
        &self,
        _headers: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        unreachable!("the chain must consult this source through verify_in_attempt")
    }

    async fn verify_in_attempt(
        &self,
        _headers: &HeaderMap,
        attempt: &mut VerificationAttempt,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        if let Some(cached) = attempt.cached_oauth_session() {
            return cached;
        }
        self.engine_calls.fetch_add(1, Ordering::SeqCst);
        let outcome = self.outcome.clone();
        attempt.store_oauth_session(outcome.clone());
        outcome
    }
}

fn composite(verifiers: Vec<Arc<dyn PrincipalVerifier>>) -> Arc<CompositePrincipalVerifier> {
    Arc::new(CompositePrincipalVerifier::new(verifiers))
}

// ---------------------------------------------------------------------------
// Composite ordering semantics: only Missing continues
// ---------------------------------------------------------------------------

#[tokio::test]
async fn composite_runs_verifiers_in_registration_order_and_first_success_wins() {
    let ok_gateway =
        CountingVerifier::new(Prescript::Ok("gateway", CredentialKind::GatewayPrincipalHeader));
    let ok_oauth =
        CountingVerifier::new(Prescript::Ok("oauth", CredentialKind::OAuthSessionCookie));
    let missing = CountingVerifier::new(Prescript::Missing);
    let headers = HeaderMap::new();

    // First success pins provenance and ends the chain.
    let identity = composite(vec![ok_gateway.clone(), ok_oauth.clone()])
        .verify(&headers)
        .await
        .expect("first source authenticates");
    assert_eq!(identity.authentication_context.source, "gateway");
    assert_eq!(identity.authentication_context.credential_kind, CredentialKind::GatewayPrincipalHeader);
    assert_eq!(ok_gateway.calls(), 1);
    assert_eq!(ok_oauth.calls(), 0, "later sources are never consulted after success");

    // Missing continues; the next source's provenance is what survives.
    let identity = composite(vec![missing.clone(), ok_oauth.clone()])
        .verify(&headers)
        .await
        .expect("second source authenticates");
    assert_eq!(identity.authentication_context.source, "oauth");
    assert_eq!(identity.authentication_context.credential_kind, CredentialKind::OAuthSessionCookie);
    assert_eq!(missing.calls(), 1);
    assert_eq!(ok_oauth.calls(), 1);
}

#[tokio::test]
async fn composite_stops_at_the_first_non_missing_outcome() {
    let ok_oauth =
        CountingVerifier::new(Prescript::Ok("oauth", CredentialKind::OAuthSessionCookie));
    let headers = HeaderMap::new();
    let cases: [(&str, Prescript, PrincipalVerificationError); 5] = [
        ("invalid", Prescript::Invalid, PrincipalVerificationError::Invalid("invalid".into())),
        ("forbidden", Prescript::Forbidden, PrincipalVerificationError::Forbidden("forbidden".into())),
        ("dependency", Prescript::DependencyUnavailable, PrincipalVerificationError::DependencyUnavailable),
        ("internal", Prescript::Internal, PrincipalVerificationError::Internal("wiring".into())),
        (
            "unavailable",
            Prescript::Unavailable,
            PrincipalVerificationError::Unavailable,
        ),
    ];
    for (name, prescript, expected) in cases {
        let failing = CountingVerifier::new(prescript);
        let outcome = composite(vec![failing.clone(), ok_oauth.clone()])
            .verify(&headers)
            .await;
        assert_eq!(
            outcome.expect_err(name).to_string(),
            expected.to_string(),
            "{name}: the failing source's outcome must be final"
        );
        assert_eq!(failing.calls(), 1);
        assert_eq!(ok_oauth.calls(), 0, "{name}: later sources must not run after failure");
    }
}

#[tokio::test]
async fn composite_all_missing_is_missing_and_legacy_registration_is_internal() {
    let missing_a = CountingVerifier::new(Prescript::Missing);
    let missing_b = CountingVerifier::new(Prescript::Missing);
    let headers = HeaderMap::new();

    assert!(matches!(
        composite(vec![missing_a.clone(), missing_b.clone()]).verify(&headers).await,
        Err(PrincipalVerificationError::Missing)
    ));
    assert_eq!(missing_a.calls(), 1);
    assert_eq!(missing_b.calls(), 1);

    // The new registry never accepts the legacy native Human boundary.
    let legacy = CountingVerifier::new(Prescript::Ok("legacy", CredentialKind::LegacyBrowserBoundary));
    assert!(matches!(
        composite(vec![legacy.clone()]).verify(&headers).await,
        Err(PrincipalVerificationError::Internal(_))
    ));
    assert_eq!(legacy.calls(), 1);
}

#[tokio::test]
async fn verification_attempt_caches_one_engine_consult_per_request() {
    let engine_backed = OAuthEngineFake::new(Err(PrincipalVerificationError::Invalid(
        "expired session cookie".into(),
    )));
    let same_source_twice = composite(vec![engine_backed.clone(), engine_backed.clone()]);
    let headers = HeaderMap::new();

    // The request-local attempt must prevent the second consult from
    // calling the strict engine again for the same request.
    assert!(matches!(
        same_source_twice.verify(&headers).await,
        Err(PrincipalVerificationError::Invalid(_))
    ));
    assert_eq!(
        engine_backed.engine_calls.load(Ordering::SeqCst),
        1,
        "one composite pass = at most one engine consult"
    );

    // A fresh verify is a fresh request: the next pass consults the engine
    // exactly once more — the cached result never leaks across requests.
    assert!(matches!(
        same_source_twice.verify(&headers).await,
        Err(PrincipalVerificationError::Invalid(_))
    ));
    assert_eq!(engine_backed.engine_calls.load(Ordering::SeqCst), 2);

    // Attempt slot semantics: empty by default, stores then reads.
    let mut attempt = VerificationAttempt::new();
    assert!(attempt.cached_oauth_session().is_none());
    attempt.store_oauth_session(Ok(pinned(caller(), "oauth", CredentialKind::OAuthSessionCookie)));
    assert!(attempt.cached_oauth_session().expect("cached").is_ok());
}

// ---------------------------------------------------------------------------
// Middleware matrix (probe router): provenance pinning + per-source CSRF
// ---------------------------------------------------------------------------

/// What the handler observed: the caller extension stays the plain business
/// identity; the provenance extension is the context pinned by the ACTUAL
/// successful source.
type ProbeRecord = (String, String, CredentialKind);

#[derive(Clone)]
struct ProbeState {
    verifier: Arc<dyn PrincipalVerifier>,
    origins: Option<TrustedBrowserOrigins>,
    probes: Arc<Mutex<Vec<ProbeRecord>>>,
}

impl PrincipalVerificationState for ProbeState {
    fn principal_verifier(&self) -> &Arc<dyn PrincipalVerifier> {
        &self.verifier
    }

    fn trusted_browser_origins(&self) -> Option<&TrustedBrowserOrigins> {
        self.origins.as_ref()
    }
}

async fn probe_handler(
    State(state): State<ProbeState>,
    axum::Extension(caller): axum::Extension<AuthenticatedCaller>,
    axum::Extension(context): axum::Extension<AuthenticationContext>,
) -> (StatusCode, &'static str) {
    state.probes.lock().expect("probe lock").push((
        caller.user.expect("user identity").id,
        context.source,
        context.credential_kind,
    ));
    (StatusCode::OK, "ok")
}

fn probe_router(state: ProbeState) -> Router {
    Router::new()
        .route(
            "/probe",
            // Safe-method legs need a reachable route too: CSRF coverage uses
            // GET probes against the same boundary.
            axum::routing::get(probe_handler).post(probe_handler),
        )
        .layer(from_fn_with_state(state.clone(), verify_principal::<ProbeState>))
        .with_state(state)
}

fn probe_request(method: &str, headers: &[(&str, &str)]) -> Request<Body> {
    let mut builder = Request::builder().method(method).uri("/probe").header("x-request-id", "probe-1");
    for (name, value) in headers {
        builder = builder.header(*name, *value);
    }
    builder.body(Body::empty()).expect("probe request")
}

fn probed(state: &ProbeState) -> usize {
    state.probes.lock().expect("probe lock").len()
}

fn last_probe(state: &ProbeState) -> ProbeRecord {
    state
        .probes
        .lock()
        .expect("probe lock")
        .last()
        .expect("probe record")
        .clone()
}

fn trusted_origins() -> TrustedBrowserOrigins {
    TrustedBrowserOrigins::new(vec!["https://workbench.example.com".into()]).expect("valid origins")
}

#[tokio::test]
async fn s2_gateway_first_success_with_stray_cookie_never_trips_cookie_rules() {
    let gateway = CountingVerifier::new(Prescript::Ok("gateway", CredentialKind::GatewayPrincipalHeader));
    let oauth = CountingVerifier::new(Prescript::Missing);
    let state = ProbeState {
        verifier: composite(vec![gateway.clone(), oauth.clone()]),
        origins: None,
        probes: Arc::new(Mutex::new(Vec::new())),
    };
    let app = probe_router(state.clone());

    // The stray (unverifiable) session cookie — and a forged source-name
    // header — are attached to a Gateway header-authenticated write; cookie
    // rules must not trigger, and the source name must remain the one this
    // registering wrapper pinned.
    let response = app
        .clone()
        .oneshot(probe_request(
            "POST",
            &[
                ("cookie", "bcs_session=stray-value"),
                ("x-avernet-principal", "signed-look-alike"),
                ("x-avernet-source", "forged-source-name"),
            ],
        ))
        .await
        .expect("probe response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(gateway.calls(), 1);
    assert_eq!(oauth.calls(), 0, "the first success ends the chain; the OAuth source is never consulted");
    assert_eq!(probed(&state), 1, "the business handler must run");
    assert_eq!(
        last_probe(&state),
        ("staff-1".to_string(), "gateway".to_string(), CredentialKind::GatewayPrincipalHeader),
        "provenance is pinned by the registering wrapper, never by request headers"
    );
}

#[tokio::test]
async fn s2_cookie_first_success_with_valid_gateway_header_still_requires_origin() {
    let oauth = CountingVerifier::new(Prescript::Ok("github", CredentialKind::OAuthSessionCookie));
    let gateway = CountingVerifier::new(Prescript::Ok("gateway", CredentialKind::GatewayPrincipalHeader));
    let state = ProbeState {
        verifier: composite(vec![oauth.clone(), gateway.clone()]),
        origins: Some(trusted_origins()),
        probes: Arc::new(Mutex::new(Vec::new())),
    };
    let app = probe_router(state.clone());

    // A valid-shaped Gateway header is attached, but the OAuth cookie source
    // wins first: Origin is mandatory and Gateway must not be consulted.
    let response = app
        .clone()
        .oneshot(probe_request("POST", &[("x-avernet-principal", "signed-look-alike")]))
        .await
        .expect("probe response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "forbidden");
    assert_eq!(probed(&state), 0, "the business handler must not run");
    assert_eq!(oauth.calls(), 1);
    assert_eq!(
        gateway.calls(),
        0,
        "the Origin rejection is final: the chain must not be re-entered to reach Gateway"
    );

    // With the trusted Origin present the same mixed credential passes and
    // the pinned provenance stays the OAuth source.
    let response = probe_router(state.clone())
        .oneshot(probe_request(
            "POST",
            &[("x-avernet-principal", "signed-look-alike"), ("origin", "https://workbench.example.com")],
        ))
        .await
        .expect("probe response with origin");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(probed(&state), 1);
    assert_eq!(
        last_probe(&state),
        ("staff-1".to_string(), "github".to_string(), CredentialKind::OAuthSessionCookie),
        "the cookie source that actually succeeded stays pinned even with a Gateway header attached"
    );
    assert_eq!(oauth.calls(), 2);
    assert_eq!(
        gateway.calls(),
        0,
        "a success earlier in the chain keeps later sources unconsulted"
    );
}

#[tokio::test]
async fn s1_malformed_or_untrusted_origins_on_cookie_writes_are_final_403s() {
    // Duplicate and non-UTF-8 Origin values must be rejected by the adapter
    // BEFORE validate — they never silently become "the first value".
    let cases: [(&str, Vec<(&str, &str)>); 2] = [
        ("untrusted origin", vec![("origin", "https://evil.example"), ("cookie", "bcs_session=x")]),
        ("missing origin", vec![("cookie", "bcs_session=x")]),
    ];
    let oauth = CountingVerifier::new(Prescript::Ok("github", CredentialKind::OAuthSessionCookie));
    let state = ProbeState {
        verifier: composite(vec![oauth.clone()]),
        origins: Some(trusted_origins()),
        probes: Arc::new(Mutex::new(Vec::new())),
    };
    for (name, headers) in cases {
        let app = probe_router(state.clone());
        let response = app
            .oneshot(probe_request("POST", &headers))
            .await
            .expect("probe response");
        assert_eq!(response.status(), StatusCode::FORBIDDEN, "{name}");
        assert_eq!(probed(&state), 0, "{name}: business must not run");
    }

    // Duplicate Origin values.
    let oauth_calls = oauth.calls();
    let app = probe_router(state.clone());
    let mut duplicate = probe_request("POST", &[("cookie", "bcs_session=x")]);
    duplicate.headers_mut().append(
        "origin",
        HeaderValue::from_static("https://workbench.example.com"),
    );
    duplicate.headers_mut().append(
        "origin",
        HeaderValue::from_static("https://workbench.example.com"),
    );
    let response = app.oneshot(duplicate).await.expect("duplicate origin response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN, "duplicate origin");
    assert_eq!(probed(&state), 0);
    assert_eq!(oauth.calls(), oauth_calls + 1);

    // Non-UTF-8 Origin bytes.
    let app = probe_router(state.clone());
    let mut opaque = probe_request("POST", &[("cookie", "bcs_session=x")]);
    opaque.headers_mut().insert(
        "origin",
        HeaderValue::from_bytes(&[0xff, 0xfe]).expect("opaque origin header"),
    );
    let response = app.oneshot(opaque).await.expect("opaque origin response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN, "non-UTF-8 origin");
    assert_eq!(probed(&state), 0);

    // Safe methods never carry a CSRF requirement even without an Origin.
    let app = probe_router(state.clone());
    let response = app
        .oneshot(probe_request("GET", &[("cookie", "bcs_session=x")]))
        .await
        .expect("safe method response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(probed(&state), 1);
}

#[tokio::test]
async fn missing_trusted_origins_fails_closed_for_cookie_writes() {
    let oauth = CountingVerifier::new(Prescript::Ok("github", CredentialKind::OAuthSessionCookie));
    let state = ProbeState {
        verifier: composite(vec![oauth]),
        origins: None,
        probes: Arc::new(Mutex::new(Vec::new())),
    };
    let response = probe_router(state.clone())
        .oneshot(probe_request(
            "POST",
            &[("cookie", "bcs_session=x"), ("origin", "https://workbench.example.com")],
        ))
        .await
        .expect("probe response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(probed(&state), 0);
}

#[tokio::test]
async fn non_cookie_provenance_and_legacy_boundary_keep_their_existing_semantics() {
    // Explicit tokens are header-independent per-request credentials.
    let explicit = CountingVerifier::new(Prescript::Ok("connection-token", CredentialKind::ExplicitToken));
    let state = ProbeState {
        verifier: composite(vec![explicit]),
        origins: Some(trusted_origins()),
        probes: Arc::new(Mutex::new(Vec::new())),
    };
    let response = probe_router(state.clone())
        .oneshot(probe_request("POST", &[]))
        .await
        .expect("probe response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(probed(&state), 1);

    // The legacy native Human router keeps its own browser_boundary write
    // protection; the new chain's trusted_browser_origins is not applied.
    // The native router wires its verifier DIRECTLY (never inside the new
    // composite, which rejects the legacy marker with Internal): the probe
    // state must mirror that wiring to keep the legacy stack's semantics.
    let legacy = CountingVerifier::new(Prescript::Ok("legacy-browser", CredentialKind::LegacyBrowserBoundary));
    let state = ProbeState {
        verifier: legacy,
        origins: Some(trusted_origins()),
        probes: Arc::new(Mutex::new(Vec::new())),
    };
    let response = probe_router(state.clone())
        .oneshot(probe_request("POST", &[]))
        .await
        .expect("probe response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(probed(&state), 1);
}

#[tokio::test]
async fn missing_fallthrough_and_error_taxonomy_at_the_middleware() {
    // A Missing source falls through to the next one.
    let missing = CountingVerifier::new(Prescript::Missing);
    let gateway = CountingVerifier::new(Prescript::Ok("gateway", CredentialKind::GatewayPrincipalHeader));
    let state = ProbeState {
        verifier: composite(vec![missing.clone(), gateway.clone()]),
        origins: None,
        probes: Arc::new(Mutex::new(Vec::new())),
    };
    let response = probe_router(state.clone())
        .oneshot(probe_request("POST", &[]))
        .await
        .expect("probe response");
    assert_eq!(response.status(), StatusCode::OK, "Missing continues the chain");
    assert_eq!(missing.calls(), 1);
    assert_eq!(gateway.calls(), 1);
    assert_eq!(probed(&state), 1);
    assert_eq!(
        last_probe(&state),
        ("staff-1".to_string(), "gateway".to_string(), CredentialKind::GatewayPrincipalHeader),
        "the fallthrough result is the source that actually succeeded"
    );

    // Every verifier failure variant keeps its response contract.
    let cases: [(&str, Prescript, u16, &str); 6] = [
        ("missing", Prescript::Missing, 401, "unauthenticated"),
        ("invalid", Prescript::Invalid, 401, "unauthenticated"),
        ("forbidden", Prescript::Forbidden, 403, "forbidden"),
        ("dependency", Prescript::DependencyUnavailable, 503, "auth_dependency_unavailable"),
        ("unavailable", Prescript::Unavailable, 502, "bot_identity_unavailable"),
        ("internal", Prescript::Internal, 500, "internal_error"),
    ];
    for (name, prescript, status, error_code) in cases {
        let state = ProbeState {
            verifier: composite(vec![CountingVerifier::new(prescript)]),
            origins: None,
            probes: Arc::new(Mutex::new(Vec::new())),
        };
        let response = probe_router(state.clone())
            .oneshot(probe_request("POST", &[]))
            .await
            .expect("probe response");
        assert_eq!(response.status().as_u16(), status, "{name}");
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], error_code, "{name}");
        assert_eq!(probed(&state), 0, "{name}: handlers must not run");
    }
}

#[tokio::test]
async fn expected_user_comparison_still_precedes_downstream_handling() {
    let gateway = CountingVerifier::new(Prescript::Ok("gateway", CredentialKind::GatewayPrincipalHeader));
    let state = ProbeState {
        verifier: composite(vec![gateway]),
        origins: None,
        probes: Arc::new(Mutex::new(Vec::new())),
    };
    // A switched Human context still yields the existing user_context_changed
    // 403 before any downstream handling.
    let response = probe_router(state.clone())
        .oneshot(probe_request("POST", &[("x-bcs-expected-user", "someone-else")]))
        .await
        .expect("probe response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "user_context_changed");
    assert_eq!(probed(&state), 0);

    // The matching comparison passes through.
    let response = probe_router(state.clone())
        .oneshot(probe_request("POST", &[("x-bcs-expected-user", "staff-1")]))
        .await
        .expect("probe response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(probed(&state), 1);
}

#[derive(Default)]
struct CountingFriendshipService {
    accepts: AtomicUsize,
}

struct NoopRegisterService;

#[async_trait]
impl RegisterService for NoopRegisterService {
    async fn issue_register_token(
        &self,
        _command: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        Err(ApplicationError::internal("register not configured"))
    }

    async fn register_bot(
        &self,
        _command: RegisterBot,
    ) -> Result<BotRegistration, ApplicationError> {
        Err(ApplicationError::internal("register not configured"))
    }
}

#[async_trait]
impl FriendshipService for CountingFriendshipService {
    async fn list_bot_friendships(
        &self,
        _command: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn delete_bot_friendship(
        &self,
        _command: DeleteBotFriendship,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn create_bot_friend_request(
        &self,
        _command: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn list_bot_friend_requests(
        &self,
        _command: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn accept_friend_request(
        &self,
        command: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        self.accepts.fetch_add(1, Ordering::SeqCst);
        let request_id = command.request_id;
        Ok(FriendRequest {
            request_id,
            from_bot_uuid: "bot-1".into(),
            to_bot_uuid: "bot-2".into(),
            status: FriendRequestStatus::Accepted,
            message: None,
            created_at: 1,
            updated_at: 1,
        })
    }

    async fn reject_friend_request(
        &self,
        _command: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }
}

fn real_router(
    verifier: Arc<dyn PrincipalVerifier>,
    origins: Option<TrustedBrowserOrigins>,
    friendship: Arc<CountingFriendshipService>,
) -> Router {
    let mut state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopSessionMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        friendship,
        verifier,
    );
    if let Some(origins) = origins {
        state = state.with_trusted_browser_origins(origins);
    }
    router(state)
}

fn friendship_accept_request(headers: &[(&str, &str)]) -> Request<Body> {
    let mut builder = Request::builder()
        .method("POST")
        .uri("/openapi/v1/collaboration/friend-requests/req-1/accept")
        .header("content-type", "application/json")
        .header("x-request-id", "request-123");
    for (name, value) in headers {
        builder = builder.header(*name, *value);
    }
    builder.body(Body::from("null")).expect("friendship accept request")
}

async fn response_json(response: Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

#[tokio::test]
async fn real_router_gateway_first_success_ignores_cookie_csrf() {
    let gateway = CountingVerifier::new(Prescript::Ok("gateway", CredentialKind::GatewayPrincipalHeader));
    let oauth = CountingVerifier::new(Prescript::Missing);
    let friendship = Arc::new(CountingFriendshipService::default());

    let app = real_router(composite(vec![gateway.clone(), oauth.clone()]), None, friendship.clone());
    let response = app
        .oneshot(friendship_accept_request(&[("cookie", "bcs_session=stray")]))
        .await
        .expect("accept response");
    assert_eq!(response.status(), StatusCode::OK, "stray cookie must not trip cookie rules");
    let body = response_json(response).await;
    assert_eq!(body["data"]["status"], "accepted");
    assert_eq!(friendship.accepts.load(Ordering::SeqCst), 1);
    assert_eq!(gateway.calls(), 1);
    assert_eq!(oauth.calls(), 0, "the first success ends the chain; later sources are never consulted");
}

#[tokio::test]
async fn real_router_cookie_first_success_requires_trusted_origin() {
    let oauth = CountingVerifier::new(Prescript::Ok("github", CredentialKind::OAuthSessionCookie));
    let gateway = CountingVerifier::new(Prescript::Ok("gateway", CredentialKind::GatewayPrincipalHeader));
    let friendship = Arc::new(CountingFriendshipService::default());

    let app = real_router(
        composite(vec![oauth.clone(), gateway.clone()]),
        Some(trusted_origins()),
        friendship.clone(),
    );
    let response = app
        .clone()
        .oneshot(friendship_accept_request(&[("x-avernet-principal", "signed-look-alike")]))
        .await
        .expect("accept response without origin");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "forbidden");
    assert_eq!(friendship.accepts.load(Ordering::SeqCst), 0, "business must not run");
    assert_eq!(oauth.calls(), 1);
    assert_eq!(gateway.calls(), 0, "no fallback to Gateway after an Origin rejection");

    // The very same chain passes with the trusted Origin.
    let response = app
        .oneshot(friendship_accept_request(&[
            ("x-avernet-principal", "signed-look-alike"),
            ("origin", "https://workbench.example.com"),
        ]))
        .await
        .expect("accept response with origin");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(friendship.accepts.load(Ordering::SeqCst), 1);
    assert_eq!(gateway.calls(), 0);
}

#[tokio::test]
async fn real_router_maps_every_verifier_error_variant() {
    let cases: [(&str, Prescript, u16, &str); 6] = [
        ("missing", Prescript::Missing, 401, "unauthenticated"),
        ("invalid", Prescript::Invalid, 401, "unauthenticated"),
        ("forbidden", Prescript::Forbidden, 403, "forbidden"),
        ("dependency", Prescript::DependencyUnavailable, 503, "auth_dependency_unavailable"),
        ("unavailable", Prescript::Unavailable, 502, "bot_identity_unavailable"),
        ("internal", Prescript::Internal, 500, "internal_error"),
    ];
    for (name, prescript, status, error_code) in cases {
        let friendship = Arc::new(CountingFriendshipService::default());
        let app = real_router(composite(vec![CountingVerifier::new(prescript)]), None, friendship.clone());
        let response = app
            .oneshot(friendship_accept_request(&[]))
            .await
            .expect("accept response");
        assert_eq!(response.status().as_u16(), status, "{name}");
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], error_code, "{name}");
        assert_eq!(friendship.accepts.load(Ordering::SeqCst), 0, "{name}: handlers must not run");
    }
}
