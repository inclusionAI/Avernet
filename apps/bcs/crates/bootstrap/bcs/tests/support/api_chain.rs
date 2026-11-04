//! The Task 13 acceptance fixture: the REAL V1 authentication chain —
//! DbPlugin decorator → REAL `DbUserIdentityStore` (via the REAL bootstrap
//! identity wiring) → Task 5 `RepoAuthSessionIdentityPort` bridge →
//! Task 12 `assemble_v1_api_auth` (real composite verifier + real
//! `AuthApplicationService` facade) → the same Stateful wrap the server
//! composition mounts → the REAL `bcs_api_http::router` — plus the REAL
//! legacy `/auth/*` entry over the same shared strict session port (the
//! `server_run` shape) and the native Human browser router over the REAL
//! legacy chain, for cross-entry and compatibility evidence.
//!
//! Fakes are exactly the seams bootstrap owns: the SecretAccessPort (a
//! counting in-memory store), the Bot registry the Stateful gate
//! revalidates against (scripted per outcome), and the business
//! application services behind the routes (counting stubs). Faults are
//! injected ONLY at the DbPlugin boundary (`support::auth_db`).

#![allow(dead_code)] // shared support: each suite target uses a subset
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode};
use axum::response::Response;
use serde_json::Value;
use tower::ServiceExt;

use bcs::api_auth_ports::{
    ConfiguredOAuthProviderBridge, PendingLoginStoreBridge, StrictSessionPortBridge,
};
use bcs::api_auth_wiring::{
    assemble_v1_api_auth, browser_verifier_for_tests, production_registrations,
    stateful_verifier_for_tests,
};
use bcs::auth_wiring::{build_auth_chain, resolve_auth_config};
use bcs::identity_wiring::IdentityPorts;
use bcs_auth_api::OAuthConfig;
use bcs_auth_api::session::{
    AuthSessionIdentityPort, InstallSession, SessionScope, SessionVersion,
};
use bcs_auth_oauth::{MemoryPendingOAuthLoginStore, OAuthSessionEngine};
use bcs_config_api::api_auth::ApiAuthConfig;
use bcs_config_api::auth::AuthChainConfig;
use bcs_http::oauth::OAuthRouteState;
use bcs_jwt::{OAuthSessionJwt, token_hash};
// The Noop services filling the `ApiState::new` slots (shared shape with
// the Task 11 suite). Only a subset is used per suite target.
#[allow(dead_code)]
#[path = "../../../../adapters/http/bcs-api-http/tests/oauth_browser_binding/noops.rs"]
mod api_state_noops;

use super::auth_db::DbHarness;

// The counting/scripted fakes live in their own module (`support/api_fakes.rs`);
// re-exported so the suite heads keep a single import.
#[allow(unused_imports)] // re-exported for the suite heads; per-target use varies
pub use super::api_fakes::{
    CallCounter, CountingBotSelfService, CountingDraftService, CountingGroupService,
    CountingSecretAccess, RegistryScript, ScriptedRegistry, SeededSession,
    gateway_bot_principal_token, gateway_user_principal_token, sign_session_token,
};

/// Fixture-wide trusted configuration (mirrors what production injects).
pub const ENV: &str = "task13";
pub const TRUSTED_ORIGIN: &str = "http://127.0.0.1:8000";
pub const SESSION_SECRET_REF: &str = "task13-session-signing-key";
pub const GATEWAY_SIGNING_KEY_REF: &str = "task13-gateway-signing-key";
/// The literal signing material. The `[api.auth]` chain resolves it through
/// the secret PORT by logical reference; the legacy entry receives the
/// same value through its `OAuthConfig` — ONE shared session space, the
/// precondition for the cross-entry race contract.
pub const SESSION_SIGNING_MATERIAL: &str = "task13-session-signing-material-32B!!";
pub const GATEWAY_SIGNING_MATERIAL: &[u8] = b"task13-gateway-signing-material-32B!";

pub fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock")
        .as_secs()
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Fixture
// ---------------------------------------------------------------------------

/// One assembled acceptance fixture.
pub struct ApiChainFixture {
    pub db: DbHarness,
    pub secrets: Arc<CountingSecretAccess>,
    pub registry: Arc<ScriptedRegistry>,
    pub group: Arc<CallCounter>,
    /// Counter for the special-purpose surfaces (drafts / bot self).
    pub surface: Arc<CallCounter>,
    pub identity: Arc<dyn AuthSessionIdentityPort>,
    pub ports: IdentityPorts,
    pub v1_router: axum::Router,
    pub legacy_router: axum::Router,
    pub human_router: axum::Router,
    pub authority: Option<bcs_test_support::bot_authority::AuthorityRoutesFixture>,
    pub invite: Option<Arc<bcs_test_support::bot_authority::InviteGateStub>>,
    pub jwt: OAuthSessionJwt,
}

/// What the fixture builds per suite.
pub struct FixtureSpec {
    /// The `[api.auth]` chain names, in chain order.
    pub chain: Vec<&'static str>,
    /// Extra provider tables: `(source name, raw TOML fragment)`.
    pub table_fragments: Vec<(String, String)>,
    pub secrets: Vec<(String, String)>,
    pub registry: RegistryScript,
    /// Mock human id for the native Human router's legacy chain; its
    /// presence mounts the human router.
    pub mock_human: Option<&'static str>,
    /// Mount the shared authority application fixture (with the invite
    /// stub wired into BOTH the middleware gate and the application).
    pub authority: bool,
    /// Whether the mounted invite stub admits Human callers
    /// (`invite_code_required` when false) — per-fixture, because the
    /// stub's decision is consulted by BOTH the middleware gate and the
    /// authority application.
    pub authority_invite_allow: bool,
    /// Mount the counting Draft service (the N2 public-GET surface).
    pub drafts: bool,
    /// Mount the counting Bot Self / Agent Self service (the N2
    /// dedicated-credential surface).
    pub bot_self: bool,
    /// Build the legacy entry over its OWN store instance (independent
    /// `DbUserIdentityStore` over the same database), for the
    /// cross-store race suite. Default: one shared store, the
    /// production `server_run` shape.
    pub independent_legacy_store: bool,
    /// File-backed shared SQLite (independent plugin instances need one
    /// shared database file); `None` = in-memory.
    pub sqlite_path: Option<std::path::PathBuf>,
}

impl FixtureSpec {
    /// The standard gateway+github chain over an in-memory migrated DB.
    pub fn standard() -> Self {
        Self {
            chain: vec!["gateway", "github"],
            table_fragments: Self::standard_tables(),
            secrets: CountingSecretAccess::standard_entries(),
            registry: RegistryScript::Ok,
            mock_human: None,
            authority: false,
            authority_invite_allow: true,
            drafts: false,
            bot_self: false,
            independent_legacy_store: false,
            sqlite_path: None,
        }
    }

    /// An OAuth-only chain (no Gateway source) — the N4 shape.
    pub fn oauth_only() -> Self {
        Self {
            chain: vec!["github"],
            table_fragments: vec![(
                "github".to_string(),
                "client_id = \"github-task13\"\nclient_secret_secret = \"task13-github-client-secret\"".to_string(),
            )],
            secrets: vec![
                (SESSION_SECRET_REF.to_string(), SESSION_SIGNING_MATERIAL.to_string()),
                (
                    "task13-github-client-secret".to_string(),
                    "gh-secret".to_string(),
                ),
            ],
            registry: RegistryScript::Ok,
            mock_human: None,
            authority: false,
            authority_invite_allow: true,
            drafts: false,
            bot_self: false,
            independent_legacy_store: false,
            sqlite_path: None,
        }
    }

    /// The N5 chain: all four OAuth providers in sequence.
    pub fn four_oauth_providers(alipay_keys: (String, String)) -> Self {
        Self {
            chain: vec!["alipay", "github", "google", "wechat"],
            table_fragments: vec![
                (
                    "alipay".to_string(),
                    "client_id = \"alipay-task13\"\nprivate_key_secret = \"task13-alipay-private\"\nalipay_public_key_secret = \"task13-alipay-public\"".to_string(),
                ),
                (
                    "github".to_string(),
                    "client_id = \"github-task13\"\nclient_secret_secret = \"task13-github-client-secret\"".to_string(),
                ),
                (
                    "google".to_string(),
                    "client_id = \"google-task13\"\nclient_secret_secret = \"task13-google-client-secret\"".to_string(),
                ),
                (
                    "wechat".to_string(),
                    "client_id = \"wechat-task13\"\nclient_secret_secret = \"task13-wechat-secret\"".to_string(),
                ),
            ],
            secrets: vec![
                (SESSION_SECRET_REF.to_string(), SESSION_SIGNING_MATERIAL.to_string()),
                ("task13-alipay-private".to_string(), alipay_keys.0),
                ("task13-alipay-public".to_string(), alipay_keys.1),
                ("task13-github-client-secret".to_string(), "gh-secret".to_string()),
                ("task13-google-client-secret".to_string(), "go-secret".to_string()),
                ("task13-wechat-secret".to_string(), "wx-secret".to_string()),
            ],
            registry: RegistryScript::Ok,
            mock_human: None,
            authority: false,
            authority_invite_allow: true,
            drafts: false,
            bot_self: false,
            independent_legacy_store: false,
            sqlite_path: None,
        }
    }

    fn standard_tables() -> Vec<(String, String)> {
        vec![
            (
                "gateway".to_string(),
                format!("signing_key_secret = \"{GATEWAY_SIGNING_KEY_REF}\""),
            ),
            (
                "github".to_string(),
                "client_id = \"github-task13\"\nclient_secret_secret = \"task13-github-client-secret\"".to_string(),
            ),
        ]
    }

    fn api_auth_config(&self) -> ApiAuthConfig {
        let mut body = format!(
            "chain = [{}]\npublic_base_url = \"{}/openapi/v1/auth\"\n\
             session_signing_key_secret = \"{SESSION_SECRET_REF}\"\n\
             success_redirect_path = \"/\"\n\
             trusted_browser_origins = [\"{TRUSTED_ORIGIN}\"]\n",
            self.chain
                .iter()
                .map(|name| format!("\"{name}\""))
                .collect::<Vec<_>>()
                .join(", "),
            TRUSTED_ORIGIN,
        );
        // Plugin parameter tables are RELATIVE to the [api.auth] section.
        for (name, fragment) in &self.table_fragments {
            body.push_str(&format!("\n[{name}]\n{fragment}\n"));
        }
        toml::from_str(&body).expect("fixture [api.auth] section parses")
    }
}

impl ApiChainFixture {
    pub async fn assemble(spec: FixtureSpec) -> Self {
        let db = match &spec.sqlite_path {
            Some(path) => DbHarness::migrated_shared_file(path).await,
            None => DbHarness::migrated().await,
        };
        let ports = db.identity_ports();
        let identity = ports.auth_session_identity.clone();
        let secrets = Arc::new(CountingSecretAccess::with_entries(spec.secrets.clone()));
        let registry = Arc::new(ScriptedRegistry::new(spec.registry));
        let group = Arc::new(CallCounter::default());
        let surface_calls = Arc::new(CallCounter::default());
        let jwt = OAuthSessionJwt::new(SESSION_SIGNING_MATERIAL);

        // The REAL explicit-chain assembly (Tasks 6–12): merged-schema
        // validation, ONLY enabled sources' secrets resolved through the
        // counting port, real provider clients, real facade.
        let built = assemble_v1_api_auth(
            &spec.api_auth_config(),
            &production_registrations(),
            identity.clone(),
            ENV.to_string(),
            secrets.clone(),
        )
        .await
        .expect("fixture [api.auth] chain assembles");
        let built_verifier = built.verifier.clone();
        let built_origins = built.origins.clone();
        let facade = built
            .auth_service
            .clone()
            .expect("fixture chains enable an OAuth source, so the facade exists");

        // The Stateful outer gate — the SAME wrap the server composition
        // mounts, revalidating signed Bot principals against the registry.
        let verifier = stateful_verifier_for_tests(built_verifier, registry.clone());

        // The V1 ApiState: counting/Noop services, REAL verifier chain,
        // real facade, real composed CSRF origins.
        let mut state = bcs_api_http::ApiState::new(
            Arc::new(CountingGroupService { calls: group.clone() }),
            Arc::new(api_state_noops::NoopSessionService),
            Arc::new(api_state_noops::NoopMessageService),
            Arc::new(api_state_noops::NoopInvitationService),
            Arc::new(api_state_noops::NoopRegisterService),
            Arc::new(api_state_noops::NoopFriendshipService),
            verifier,
        )
        .with_auth_service(facade.clone(), format!("{TRUSTED_ORIGIN}/openapi/v1/auth"))
        .with_trusted_browser_origins(
            built_origins
                .clone()
                .expect("cookie-kind sources are in the chain, origins are composed"),
        );

        // The authority + middleware invite-gate mounts (compatibility N3).
        let mut authority_fixture = None;
        let mut invite = None;
        if spec.authority {
            let mounted = bcs_test_support::bot_authority::authority_service_fixture(
                spec.authority_invite_allow,
            )
            .await;
            state = state
                .with_bot_authority_service(mounted.service.clone())
                .with_invite_code_service(mounted.invite.clone())
                .with_invite_code_gate_enabled(true);
            invite = Some(mounted.invite.clone());
            authority_fixture = Some(mounted);
        }

        // The N2 special-purpose surfaces keep their own protocols: the
        // counting Draft service (public fixed reads) and the counting
        // Bot Self service (bearer/actor credentials).
        if spec.drafts {
            state.collaboration_drafts = Some(Arc::new(CountingDraftService {
                calls: surface_calls.clone(),
            }));
        }
        if spec.bot_self {
            state = state.with_bot_self_service(Arc::new(CountingBotSelfService {
                calls: surface_calls.clone(),
            }));
        }

        let v1_router = bcs_api_http::router(state.clone());

        // The legacy `/auth/*` entry — the `server_run` shape: its own
        // engine + bridge over the (shared or independent) strict port and
        // the same pending-store contract; only the flow namespace and the
        // identity display reader differ.
        let legacy_identity = if spec.independent_legacy_store {
            db.identity_ports().auth_session_identity
        } else {
            identity.clone()
        };
        let legacy_engine = Arc::new(OAuthSessionEngine::new(
            OAuthSessionJwt::new(SESSION_SIGNING_MATERIAL),
            legacy_identity.clone(),
            ENV.to_string(),
            1800,
        ));
        let legacy_service: Arc<dyn bcs_service_api::application::v1::AuthService> = Arc::new(
            bcs_app_auth::AuthApplicationService::new(
                // Refresh/logout never consult providers; the login family
                // is not part of the race suites.
                Arc::new(ConfiguredOAuthProviderBridge::new(vec![])),
                Arc::new(StrictSessionPortBridge::new(
                    legacy_engine,
                    legacy_identity.clone(),
                    ENV.to_string(),
                )),
                Arc::new(PendingLoginStoreBridge::new(Arc::new(
                    MemoryPendingOAuthLoginStore::new(),
                ))),
                "/".to_string(),
                "legacy".to_string(),
            ),
        );
        let legacy_state = Arc::new(OAuthRouteState::new(
            legacy_service,
            ports.user_identity.clone(),
            legacy_oauth_config(),
            None,
            vec![TRUSTED_ORIGIN.to_string()],
        ));
        let legacy_router = bcs_http::oauth::routes(legacy_state);

        // The native Human browser router over the REAL legacy auth chain
        // (`local` mock plugin through the real resolver + builder), with
        // the production BrowserPrincipalVerifier wrapper.
        let human_router = match spec.mock_human {
            Some(mock_human) => {
                let chain_config = AuthChainConfig {
                    chain: vec!["local".to_string()],
                    require_authentication: false,
                    mock_user_id: Some(mock_human.to_string()),
                    mock_user_name: Some("Task 13 Mock Human".to_string()),
                    allow_mock_headers: false,
                    oauth: None,
                };
                let resolved = resolve_auth_config(&chain_config, ENV);
                let chain = build_auth_chain(&resolved, registry.clone(), None, None);
                let browser_state =
                    ApiChainFixture::browser_state(&state, browser_verifier_for_tests(Arc::new(chain)));
                bcs_api_http::v1::human::router(browser_state)
            }
            None => bcs_api_http::v1::human::router(state),
        };

        ApiChainFixture {
            db,
            secrets,
            registry,
            group,
            surface: surface_calls,
            identity,
            ports,
            v1_router,
            legacy_router,
            human_router,
            authority: authority_fixture,
            invite,
            jwt,
        }
    }

    fn browser_state(
        state: &bcs_api_http::ApiState,
        verifier: Arc<dyn bcs_api_http::PrincipalVerifier>,
    ) -> bcs_api_http::ApiState {
        let mut browser = state.clone();
        browser.principal_verifier = verifier;
        browser
    }

    /// Seed a live strict session the way a completed callback would have
    /// (ensure_identity + CAS install of a fixture-signed version).
    pub async fn seed_session(&self, external: &str) -> SeededSession {
        let user_id = self
            .identity
            .ensure_identity("github", external, Some(external), None, ENV)
            .await
            .expect("seed identity");
        let sid = format!("{external}-session-id");
        let revision: u64 = 1;
        let token = sign_session_token(&self.jwt, &user_id, &sid, revision);
        let outcome = self
            .identity
            .install_login_session(InstallSession {
                scope: SessionScope {
                    user_id: user_id.clone(),
                    provider: "github".to_string(),
                    env: ENV.to_string(),
                },
                expected_revision: 0,
                next: SessionVersion {
                    session_id: sid.clone(),
                    revision,
                    token_hash: token_hash(&token),
                },
                expires_at: unix_now() + 1800,
            })
            .await
            .expect("seed install");
        assert!(
            matches!(outcome, bcs_auth_api::session::SessionWrite::Applied),
            "seeding must CAS-apply, got {outcome:?}"
        );
        SeededSession { user_id, token, sid, revision }
    }

    /// Sign a candidate version without installing it (the candidate B of
    /// the race contract: same sid, revision+1).
    pub fn candidate_token(&self, seed: &SeededSession, revision: u64) -> String {
        let token = sign_session_token(&self.jwt, &seed.user_id, &seed.sid, revision);
        assert_ne!(token, seed.token, "same-second candidates differ by revision claim");
        token
    }

    /// A COMPLETED LOGIN for an identity that already has a session row:
    /// the REAL engine's install path (what the login callbacks run after
    /// a successful exchange) — read the CURRENT revision, sign a NEW
    /// random session id, CAS-install — verified through the real strict
    /// verify to recover the trusted version this fixture needs.
    pub async fn login_new_session(&self, prior: &SeededSession) -> SeededSession {
        let engine = OAuthSessionEngine::new(
            OAuthSessionJwt::new(SESSION_SIGNING_MATERIAL),
            self.identity.clone(),
            ENV.to_string(),
            1800,
        );
        let issued = engine
            .install(
                SessionScope {
                    user_id: prior.user_id.clone(),
                    provider: "github".to_string(),
                    env: ENV.to_string(),
                },
                Some("Task 13 Race 用户".to_string()),
                unix_now(),
            )
            .await
            .expect("the real install path completes the new login");
        let snapshot = engine
            .verify(&issued.token, unix_now())
            .await
            .expect("the freshly installed session verifies");
        assert_eq!(
            snapshot.scope.user_id, prior.user_id,
            "the new login installs for the same identity"
        );
        SeededSession {
            user_id: prior.user_id.clone(),
            token: issued.token,
            sid: snapshot.version.session_id,
            revision: snapshot.version.revision,
        }
    }
}

fn legacy_oauth_config() -> OAuthConfig {
    OAuthConfig {
        jwt_secret: SESSION_SIGNING_MATERIAL.to_string(),
        idle_timeout_minutes: 30,
        base_url: format!("{TRUSTED_ORIGIN}"),
        cookie_secure: false,
        env: ENV.to_string(),
        success_redirect_path: "/".to_string(),
    }
}

// ---------------------------------------------------------------------------
// Request driver
// ---------------------------------------------------------------------------

pub struct Driven {
    pub status: StatusCode,
    pub cookies: Vec<String>,
    pub body: Value,
    pub cache_control: Option<String>,
}

pub async fn drive(
    router: &axum::Router,
    method: &str,
    uri: &str,
    headers: &[(&str, String)],
) -> Driven {
    let mut builder = Request::builder().method(method).uri(uri);
    for (name, value) in headers {
        builder = builder.header(*name, value.as_str());
    }
    let request = builder.body(Body::empty()).expect("test request");
    let response = router
        .clone()
        .oneshot(request)
        .await
        .expect("router responds");
    parse_response(response).await
}

pub async fn parse_response(response: Response) -> Driven {
    let status = response.status();
    let cookies = response
        .headers()
        .get_all(axum::http::header::SET_COOKIE)
        .iter()
        .filter_map(|value| value.to_str().ok().map(str::to_string))
        .collect();
    let cache_control = response
        .headers()
        .get(axum::http::header::CACHE_CONTROL)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("response body");
    let body = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    Driven { status, cookies, body, cache_control }
}

/// Cookie + trusted Origin — the header pair a cookie-authenticated
/// unsafe write needs on both entries.
pub fn session_headers(token: &str) -> Vec<(&'static str, String)> {
    vec![
        ("cookie", format!("bcs_session={token}")),
        ("origin", TRUSTED_ORIGIN.to_string()),
    ]
}

pub fn session_cookie_only(token: &str) -> Vec<(&'static str, String)> {
    vec![("cookie", format!("bcs_session={token}"))]
}

/// The verbatim safe-session tail of the race contract, driven through
/// the protected V1 chain: BOTH the stale credential and the candidate
/// next version must be credentials-invalid (401), business never entered.
pub async fn assert_credentials_invalid_via_groups(
    router: &axum::Router,
    token: &str,
) -> Driven {
    let driven = drive(
        router,
        "GET",
        "/openapi/v1/collaboration/groups",
        &[("cookie", format!("bcs_session={token}"))],
    )
    .await;
    assert_eq!(
        driven.status,
        StatusCode::UNAUTHORIZED,
        "status {:?} body {:?}",
        driven.status,
        driven.body
    );
    assert_eq!(
        driven.body["data"]["error_code"], "unauthenticated",
        "401 carries the boundary's stable error code"
    );
    driven
}

pub fn assert_no_session_cookie(driven: &Driven) {
    assert!(
        driven.cookies.is_empty(),
        "a losing refresh must hand out NO session credential, got {:?}",
        driven.cookies
    );
}

pub fn rotated_token_of(driven: &Driven) -> String {
    driven
        .cookies
        .iter()
        .find(|cookie| cookie.starts_with("bcs_session="))
        .expect("rotation Set-Cookie")
        .split(';')
        .next()
        .and_then(|pair| pair.split_once('=').map(|(_, value)| value.to_string()))
        .expect("rotated credential")
}
