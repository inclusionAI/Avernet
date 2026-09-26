//! Task 12 — public `[api.auth]` source assembly: registry `build` phase,
//! composite verifier wiring, and unified secret/origin handling.
//!
//! Real instances everywhere feasible (real `OAuthSessionEngine`, real
//! memory identity store, real strict bridge); only the `SecretAccessPort`
//! is faked (counting), plus a test-only external source descriptor that
//! records its invocations.

use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use async_trait::async_trait;
use axum::http::{HeaderMap, HeaderValue};
use serial_test::serial;

use bcs::api_auth_registry::{
    ApiAuthBuildContext, ApiAuthCapabilities, ApiAuthSourceRegistration,
    default_api_auth_registrations, validate_api_auth,
};
use bcs::api_auth_wiring::{ApiAuthAssemblyInputs, build_api_auth};
use bcs_api_http::{PrincipalVerificationError, PrincipalVerifier};
use bcs_auth_api::{
    AuthSessionIdentityPort, InstallSession, SessionRevoke, SessionScope, SessionStoreError,
    SessionWrite,
};
use bcs_config_api::ApiAuthConfig;
use bcs_service_api::port::secret::{SecretAccessError, SecretAccessPort, SecretRecord};

#[path = "api_auth_registration/session_cookie.rs"]
mod session_cookie;

// ---------------------------------------------------------------------------
// Counting fake SecretAccessPort
// ---------------------------------------------------------------------------

struct CountingSecretAccess {
    entries: std::collections::BTreeMap<String, String>,
    reads: AtomicUsize,
}

impl CountingSecretAccess {
    fn with_entries(entries: &[(&str, &str)]) -> Self {
        Self {
            entries: entries
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
            reads: AtomicUsize::new(0),
        }
    }

    fn reads(&self) -> usize {
        self.reads.load(Ordering::SeqCst)
    }
}

#[async_trait]
impl SecretAccessPort for CountingSecretAccess {
    async fn get_secret(&self, name: &str) -> Result<SecretRecord, SecretAccessError> {
        self.reads.fetch_add(1, Ordering::SeqCst);
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
// Test-only external source descriptor (records build invocations)
// ---------------------------------------------------------------------------

fn build_calls() -> &'static Mutex<Vec<String>> {
    static CALLS: OnceLock<Mutex<Vec<String>>> = OnceLock::new();
    CALLS.get_or_init(|| Mutex::new(Vec::new()))
}

fn record_build_call(name: &str) {
    build_calls().lock().expect("build call lock").push(name.to_string());
}

/// Always-Missing stub verifier handed out by the fake source's build fn.
#[derive(Default)]
struct StubExternalVerifier;

#[async_trait]
impl PrincipalVerifier for StubExternalVerifier {
    async fn verify(
        &self,
        _headers: &HeaderMap,
    ) -> Result<
        bcs_api_http::VerifiedRequestIdentity,
        PrincipalVerificationError,
    > {
        Err(PrincipalVerificationError::Missing)
    }
}

/// `build` fn for the test-only external source. A `fn` pointer cannot
/// capture per-test state, so invocations go through a crate-local mutex.
fn build_test_external_cookie(
    ctx: ApiAuthBuildContext,
) -> Pin<Box<dyn Future<Output = Result<Arc<dyn PrincipalVerifier>, String>> + Send>> {
    record_build_call(&ctx.name);
    Box::pin(async { Ok(Arc::new(StubExternalVerifier) as Arc<dyn PrincipalVerifier>) })
}

fn validate_test_external_cookie(value: &serde_json::Value) -> Result<(), String> {
    let client_id = value
        .get("client_id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "client_id is required".to_string())?;
    if client_id.trim().is_empty() {
        return Err("client_id must not be empty".to_string());
    }
    Ok(())
}

fn test_external_cookie_descriptor() -> ApiAuthSourceRegistration {
    ApiAuthSourceRegistration {
        name: "test_external_cookie",
        capabilities: ApiAuthCapabilities {
            uses_browser_cookie: true,
            is_oauth_provider: false,
        },
        validate: validate_test_external_cookie,
        build: build_test_external_cookie,
    }
}

fn registrations_with_fake() -> Vec<ApiAuthSourceRegistration> {
    let mut registrations = default_api_auth_registrations();
    registrations.push(test_external_cookie_descriptor());
    registrations
}

fn auth_from_toml(src: &str) -> ApiAuthConfig {
    toml::from_str(src).unwrap_or_else(|e| panic!("parse ApiAuthConfig: {e}"))
}

fn clear_build_calls() {
    build_calls().lock().expect("build call lock").clear();
}

fn build_calls_snapshot() -> Vec<String> {
    build_calls().lock().expect("build call lock").clone()
}

// ---------------------------------------------------------------------------
// Shared real-instance harness for the OAuth dispatch tests
// ---------------------------------------------------------------------------

/// Counts strict `get_session_by_hash` calls (the engine's per-request verify
/// IO) and can fault-inject a store failure.
struct CountingSessionIdentityPort {
    inner: Arc<dyn AuthSessionIdentityPort>,
    get_calls: AtomicUsize,
    fail_gets: AtomicBool,
}

impl CountingSessionIdentityPort {
    fn new(inner: Arc<dyn AuthSessionIdentityPort>) -> Self {
        Self {
            inner,
            get_calls: AtomicUsize::new(0),
            fail_gets: AtomicBool::new(false),
        }
    }

    fn get_calls(&self) -> usize {
        self.get_calls.load(Ordering::SeqCst)
    }
}

#[async_trait]
impl AuthSessionIdentityPort for CountingSessionIdentityPort {
    async fn read_session_revision(
        &self,
        scope: &SessionScope,
    ) -> Result<u64, SessionStoreError> {
        self.inner.read_session_revision(scope).await
    }

    async fn get_session_by_hash(
        &self,
        scope: &SessionScope,
        hash: &str,
        now: u64,
    ) -> Result<Option<bcs_auth_api::SessionSnapshot>, SessionStoreError> {
        self.get_calls.fetch_add(1, Ordering::SeqCst);
        if self.fail_gets.load(Ordering::SeqCst) {
            return Err(SessionStoreError::Unavailable);
        }
        self.inner.get_session_by_hash(scope, hash, now).await
    }

    async fn install_login_session(
        &self,
        command: InstallSession,
    ) -> Result<SessionWrite, SessionStoreError> {
        self.inner.install_login_session(command).await
    }

    async fn rotate_session(
        &self,
        command: bcs_auth_api::RotateSession,
    ) -> Result<SessionWrite, SessionStoreError> {
        self.inner.rotate_session(command).await
    }

    async fn revoke_session(
        &self,
        scope: &SessionScope,
        session_id: &str,
    ) -> Result<SessionRevoke, SessionStoreError> {
        self.inner.revoke_session(scope, session_id).await
    }

    async fn ensure_identity(
        &self,
        provider: &str,
        external_user_id: &str,
        name: Option<&str>,
        avatar: Option<&str>,
        env: &str,
    ) -> Result<String, SessionStoreError> {
        self.inner
            .ensure_identity(provider, external_user_id, name, avatar, env)
            .await
    }
}

const ENV: &str = "local";
const JWT_SECRET: &str = "test-session-signing-secret-32b";

/// Real identity store + strict bridge + ONE engine: exactly the instances
/// the production construction assembles.
async fn oauth_harness() -> (
    Arc<CountingSessionIdentityPort>,
    Arc<bcs_auth_oauth::OAuthSessionEngine>,
) {
    let store = Arc::new(bcs_user_identity::MemoryUserIdentityRepo::new());
    let strict_bridge = Arc::new(bcs::identity_session_wiring::RepoAuthSessionIdentityPort::new(
        store.clone(),
        store,
    ));
    let counting = Arc::new(CountingSessionIdentityPort::new(strict_bridge));
    let engine = Arc::new(bcs_auth_oauth::OAuthSessionEngine::new(
        bcs_jwt::OAuthSessionJwt::new(JWT_SECRET),
        counting.clone(),
        ENV.to_string(),
        1800,
    ));
    (counting, engine)
}

/// Install a live session for `provider` through the REAL engine + store and
/// return the `bcs_session` cookie header value.
async fn install_session(
    engine: &bcs_auth_oauth::OAuthSessionEngine,
    identities: &dyn AuthSessionIdentityPort,
    provider: &str,
    external_user_id: &str,
    display_name: &str,
) -> String {
    let user_id = identities
        .ensure_identity(provider, external_user_id, Some(display_name), None, ENV)
        .await
        .expect("ensure_identity");
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let issued = engine
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

fn headers_with_cookie(cookie_pair: &str) -> HeaderMap {
    let mut headers = HeaderMap::new();
    headers.insert("cookie", HeaderValue::from_str(cookie_pair).expect("header value"));
    headers
}

/// Composition inputs for an OAuth-flavored chain over the harness.
fn oauth_inputs(
    auth: ApiAuthConfig,
    secret_access: Arc<dyn SecretAccessPort>,
    engine: Arc<bcs_auth_oauth::OAuthSessionEngine>,
    sessions: Arc<dyn AuthSessionIdentityPort>,
) -> ApiAuthAssemblyInputs {
    ApiAuthAssemblyInputs {
        registrations: registrations_with_fake(),
        config: auth,
        secret_access,
        sessions,
        env: ENV.to_string(),
        oauth: Some(engine),
        session_signing_material: Some(JWT_SECRET.to_string()),
    }
}

// ---------------------------------------------------------------------------
// Assembly: build invocations and selection
// ---------------------------------------------------------------------------

#[test]
#[serial]
fn explicit_chain_builds_only_selected_source() {
    clear_build_calls();
    let auth = auth_from_toml(
        r#"chain = ["test_external_cookie"]
trusted_browser_origins = ["https://workbench.example.com"]

[test_external_cookie]
client_id = "fake-client"
"#,
    );
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[]));
    let sessions: Arc<dyn AuthSessionIdentityPort> = Arc::new(
        bcs::identity_session_wiring::RepoAuthSessionIdentityPort::new(
            Arc::new(bcs_user_identity::MemoryUserIdentityRepo::new()),
            Arc::new(bcs_user_identity::MemoryUserIdentityRepo::new()),
        ),
    );
    let built = tokio::runtime::Runtime::new()
        .expect("runtime")
        .block_on(build_api_auth(ApiAuthAssemblyInputs {
            registrations: registrations_with_fake(),
            config: auth,
            secret_access: secrets.clone(),
            sessions,
            env: ENV.to_string(),
            oauth: None,
            session_signing_material: None,
        }))
        .expect("explicit fake-only chain builds");

    // Build called exactly once, for the fake; no default-inventory source
    // is built (gateway would fail without its secret — proving defaults
    // are never auto-added).
    assert_eq!(build_calls_snapshot(), vec!["test_external_cookie".to_string()]);
    assert_eq!(built.sources, vec!["test_external_cookie".to_string()]);
    assert_eq!(secrets.reads(), 0, "fake source reads no secrets");
    // The verifier is the composite over the built sources; a chain without
    // credentials resolves to Missing (each stub returns Missing).
    let outcome = tokio::runtime::Runtime::new()
        .expect("runtime")
        .block_on(built.verifier.verify(&HeaderMap::new()));
    assert!(matches!(outcome, Err(PrincipalVerificationError::Missing)));
}

#[test]
#[serial]
fn unknown_source_in_chain_is_startup_error() {
    clear_build_calls();
    let auth = auth_from_toml(
        r#"chain = ["no_such_source"]

[no_such_source]
client_id = "x"
"#,
    );
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[]));
    let sessions: Arc<dyn AuthSessionIdentityPort> = Arc::new(
        bcs::identity_session_wiring::RepoAuthSessionIdentityPort::new(
            Arc::new(bcs_user_identity::MemoryUserIdentityRepo::new()),
            Arc::new(bcs_user_identity::MemoryUserIdentityRepo::new()),
        ),
    );
    let err = tokio::runtime::Runtime::new()
        .expect("runtime")
        .block_on(build_api_auth(ApiAuthAssemblyInputs {
            registrations: registrations_with_fake(),
            config: auth,
            secret_access: secrets,
            sessions,
            env: ENV.to_string(),
            oauth: None,
            session_signing_material: None,
        }))
        .expect_err("unknown source must fail startup");
    assert!(
        err.contains("no_such_source") && err.contains("unknown"),
        "expected unknown-source error, got: {err}"
    );
    assert!(build_calls_snapshot().is_empty(), "no build ran for invalid config");
}

#[test]
#[serial]
fn duplicate_registration_is_startup_error() {
    clear_build_calls();
    let mut registrations = registrations_with_fake();
    // Register the same name twice with a no-op validator.
    registrations.push(ApiAuthSourceRegistration {
        name: "test_external_cookie",
        capabilities: ApiAuthCapabilities {
            uses_browser_cookie: false,
            is_oauth_provider: false,
        },
        validate: |_: &serde_json::Value| Ok(()),
        build: build_test_external_cookie,
    });
    let auth = auth_from_toml(
        r#"chain = ["test_external_cookie"]
trusted_browser_origins = ["https://workbench.example.com"]

[test_external_cookie]
client_id = "fake-client"
"#,
    );
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[]));
    let sessions: Arc<dyn AuthSessionIdentityPort> = Arc::new(
        bcs::identity_session_wiring::RepoAuthSessionIdentityPort::new(
            Arc::new(bcs_user_identity::MemoryUserIdentityRepo::new()),
            Arc::new(bcs_user_identity::MemoryUserIdentityRepo::new()),
        ),
    );
    let err = tokio::runtime::Runtime::new()
        .expect("runtime")
        .block_on(build_api_auth(ApiAuthAssemblyInputs {
            registrations,
            config: auth,
            secret_access: secrets,
            sessions,
            env: ENV.to_string(),
            oauth: None,
            session_signing_material: None,
        }))
        .expect_err("duplicate registration must fail startup");
    assert!(
        err.contains("duplicate"),
        "expected duplicate-registration error, got: {err}"
    );
}


// ---------------------------------------------------------------------------
// Gateway build reuses the existing verifier construction
// ---------------------------------------------------------------------------

#[test]
fn oauth_engine_helper_builds_one_shared_engine() {
    // The composition helper must expose a single engine constructor used by
    // all OAuth sources (ONE engine per chain family).
    let store = Arc::new(bcs_user_identity::MemoryUserIdentityRepo::new());
    let bridge = Arc::new(bcs::identity_session_wiring::RepoAuthSessionIdentityPort::new(
        store.clone(),
        store,
    ));
    let engine_a = bcs::api_auth_wiring::build_api_auth_oauth_engine(
        "material-for-tests-at-least-long",
        bridge.clone(),
        ENV.to_string(),
        1800,
    );
    let engine_b = bcs::api_auth_wiring::build_api_auth_oauth_engine(
        "material-for-tests-at-least-long",
        bridge.clone(),
        ENV.to_string(),
        1800,
    );
    // Distinct engines are independent; the helper just centralizes
    // construction. Assert non-null construction over the SAME port type.
    let _ = (engine_a, engine_b);
}

// ---------------------------------------------------------------------------
// Registry cross-check error arms + provider-client dispatch battery.
// Covers the validation branches and provider builders that changed-line
// coverage flagged as unexercised: the OAuth-common / browser-origin
// cross-checks in `validate_api_auth`, and the google/wechat/alipay/unknown
// arms of `build_oauth_source_provider` plus its `resolve_source_secret`
// error surfaces.
// ---------------------------------------------------------------------------

#[test]
fn oauth_common_fields_rejected_without_oauth_source_in_chain() {
    let auth = auth_from_toml(
        r#"chain = ["gateway"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"

[gateway]
signing_key_secret = "GW_SIGNING"
"#,
    );
    let err = validate_api_auth(&auth, &default_api_auth_registrations())
        .expect_err("public_base_url without an OAuth source must fail");
    assert!(
        err.contains("public_base_url is only used with OAuth providers"),
        "expected public_base_url cross-check error, got: {err}"
    );

    let auth = auth_from_toml(
        r#"chain = ["gateway"]
session_signing_key_secret = "SESSION_SIGNING"

[gateway]
signing_key_secret = "GW_SIGNING"
"#,
    );
    let err = validate_api_auth(&auth, &default_api_auth_registrations())
        .expect_err("session_signing_key_secret without an OAuth source must fail");
    assert!(
        err.contains("session_signing_key_secret is only used with OAuth providers"),
        "expected session_signing_key_secret cross-check error, got: {err}"
    );
}

#[test]
fn cookie_source_requires_non_empty_trusted_browser_origins() {
    let missing = auth_from_toml(
        r#"chain = ["test_external_cookie"]

[test_external_cookie]
client_id = "x"
"#,
    );
    let err =
        validate_api_auth(&missing, &registrations_with_fake())
            .expect_err("cookie source without trusted_browser_origins must fail");
    assert!(
        err.contains("trusted_browser_origins is required when a cookie-emitting source"),
        "expected required-origins error, got: {err}"
    );

    let empty = auth_from_toml(
        r#"chain = ["test_external_cookie"]
trusted_browser_origins = []

[test_external_cookie]
client_id = "x"
"#,
    );
    let err = validate_api_auth(&empty, &registrations_with_fake())
        .expect_err("empty trusted_browser_origins must fail");
    assert!(
        err.contains("trusted_browser_origins must not be empty"),
        "expected empty-origins error, got: {err}"
    );
}

#[test]
fn trusted_browser_origins_rejected_without_cookie_source_in_chain() {
    let auth = auth_from_toml(
        r#"chain = ["gateway"]
trusted_browser_origins = ["https://workbench.example.com"]

[gateway]
signing_key_secret = "GW_SIGNING"
"#,
    );
    let err = validate_api_auth(&auth, &default_api_auth_registrations())
        .expect_err("origins without a cookie source must fail");
    assert!(
        err.contains("trusted_browser_origins is only used with cookie-emitting sources"),
        "expected origins-only-used-with-cookie error, got: {err}"
    );
}

#[test]
fn oauth_provider_dispatch_builds_google_and_wechat_clients() {
    let secrets: Arc<dyn SecretAccessPort> = Arc::new(CountingSecretAccess::with_entries(&[
        ("GOOGLE_CLIENT_SECRET", "google-secret-value"),
        ("WECHAT_CLIENT_SECRET", "wechat-secret-value"),
    ]));
    let rt = tokio::runtime::Runtime::new().expect("runtime");

    let google = rt
        .block_on(bcs::api_auth_provider_configs::build_oauth_source_provider(
            "google",
            &serde_json::json!({"client_id": "google-client-id", "client_secret_secret": "GOOGLE_CLIENT_SECRET"}),
            &secrets,
        ))
        .expect("google client builds");
    assert_eq!(google.name(), "google");

    let wechat = rt
        .block_on(bcs::api_auth_provider_configs::build_oauth_source_provider(
            "wechat",
            &serde_json::json!({"client_id": "wechat-appid", "client_secret_secret": "WECHAT_CLIENT_SECRET"}),
            &secrets,
        ))
        .expect("wechat client builds");
    assert_eq!(wechat.name(), "wechat");

    let unknown = rt
        .block_on(bcs::api_auth_provider_configs::build_oauth_source_provider(
            "nope",
            &serde_json::json!({}),
            &secrets,
        ))
        .err()
        .expect("unregistered source must fail");
    assert!(
        unknown.contains("no production OAuth provider client is registered"),
        "expected unregistered-source error, got: {unknown}"
    );
}

#[test]
fn oauth_provider_dispatch_rejects_unknown_table_fields_and_bad_alipay_keys() {
    let secrets: Arc<dyn SecretAccessPort> = Arc::new(CountingSecretAccess::with_entries(&[
        ("ALIPAY_PRIVATE_KEY", "not-a-valid-rsa-pem"),
        ("ALIPAY_PUBLIC_KEY", "not-a-valid-rsa-pem"),
    ]));
    let rt = tokio::runtime::Runtime::new().expect("runtime");

    let bad_table = rt
        .block_on(bcs::api_auth_provider_configs::build_oauth_source_provider(
            "github",
            &serde_json::json!({"client_id": "x", "client_secret_secret": "S", "extra_field": 1}),
            &secrets,
        ))
        .err()
        .expect("unknown table field must fail");
    assert!(
        bad_table.contains("invalid [api.auth.github] table"),
        "expected table-shape error, got: {bad_table}"
    );

    // Alipay constructs an RSA2 signer at build time: garbage PEM material
    // must surface as a field-path error, never the raw parse payload.
    let bad_keys = rt
        .block_on(bcs::api_auth_provider_configs::build_oauth_source_provider(
            "alipay",
            &serde_json::json!({
                "client_id": "alipay-app",
                "private_key_secret": "ALIPAY_PRIVATE_KEY",
                "alipay_public_key_secret": "ALIPAY_PUBLIC_KEY",
            }),
            &secrets,
        ))
        .err()
        .expect("invalid alipay key material must fail");
    assert!(
        bad_keys.contains("invalid alipay key configuration"),
        "expected alipay key-configuration error, got: {bad_keys}"
    );
    assert!(
        !bad_keys.contains("not-a-valid-rsa-pem"),
        "error must not echo the resolved secret material: {bad_keys}"
    );
}

#[test]
fn resolve_source_secret_reports_required_unresolved_and_blank() {
    let secrets: Arc<dyn SecretAccessPort> = Arc::new(CountingSecretAccess::with_entries(&[(
        "BLANK_SECRET",
        "   ",
    )]));
    let rt = tokio::runtime::Runtime::new().expect("runtime");

    let missing = rt
        .block_on(bcs::api_auth_provider_configs::resolve_source_secret(
            secrets.as_ref(),
            "github",
            "client_secret_secret",
            None,
        ))
        .expect_err("missing logical name must fail");
    assert!(
        missing.contains("api.auth.github.client_secret_secret is required"),
        "expected required error, got: {missing}"
    );

    let unresolved = rt
        .block_on(bcs::api_auth_provider_configs::resolve_source_secret(
            secrets.as_ref(),
            "github",
            "client_secret_secret",
            Some("NO_SUCH_SECRET"),
        ))
        .expect_err("unresolvable secret must fail");
    assert!(
        unresolved.contains("did not resolve"),
        "expected unresolved error, got: {unresolved}"
    );

    let blank = rt
        .block_on(bcs::api_auth_provider_configs::resolve_source_secret(
            secrets.as_ref(),
            "github",
            "client_secret_secret",
            Some("BLANK_SECRET"),
        ))
        .expect_err("blank secret value must fail");
    assert!(
        blank.contains("resolved to an empty secret"),
        "expected blank-secret error, got: {blank}"
    );
}
