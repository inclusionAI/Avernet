//! Bootstrap integration tests for the V1 API-auth source factory
//! registry (plan Task 12, step 1).
//!
//! One additional fake descriptor (`test_external_cookie`) is REGISTERED
//! alongside the production set and an EXPLICIT `[api.auth]` config selects
//! only it. The tests prove the assembly contract:
//!
//! - `build` is invoked exactly for chain-ENABLED sources, in chain order,
//!   with the SAME value-domain JSON parameters `validate` saw (no
//!   re-conversion);
//! - the production/legacy inventory is NOT auto-added when the chain does
//!   not select it (a Gateway header yields `Missing`, not a Gateway
//!   rejection, because no Gateway source was built);
//! - unknown and duplicate registrations refuse assembly;
//! - `secret_calls` observe ONLY enabled instances — a chain without the
//!   Gateway source never reads the Gateway signing secret, and a chain
//!   without an OAuth source never reads the session signing key (N4);
//! - the configured trusted origins are composed and carried out of the
//!   assembly for the per-actual-source CSRF gate.

use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs::api_auth_registry::{
    ApiAuthBuildContext, ApiAuthCapabilities, ApiAuthSourceRegistration,
};
use bcs::api_auth_wiring::{assemble_v1_api_auth, production_registrations};
use bcs::identity_wiring::memory_identity_ports;
use bcs_api_http::{
    CredentialKind, PrincipalVerificationError, PrincipalVerifier, VerifiedRequestIdentity,
};
use bcs_config_api::api_auth::ApiAuthConfig;
use bcs_secret_local::InMemorySecretAccess;
use bcs_service_api::application::v1::{AuthenticatedCaller, AuthenticatedUserIdentity};
use bcs_service_api::port::secret::{SecretAccessError, SecretAccessPort, SecretRecord};
use serde_json::Value;

// ---------------------------------------------------------------------------
// Test instrumentation
// ---------------------------------------------------------------------------

static BUILT_NAMES: Mutex<Vec<String>> = Mutex::new(Vec::new());
static BUILT_OPTIONS: Mutex<Vec<Value>> = Mutex::new(Vec::new());
static VALIDATED_TABLES: Mutex<Vec<Value>> = Mutex::new(Vec::new());

fn take_built_names() -> Vec<String> {
    BUILT_NAMES.lock().unwrap().drain(..).collect()
}

fn take_built_options() -> Vec<Value> {
    BUILT_OPTIONS.lock().unwrap().drain(..).collect()
}

fn take_validated_tables() -> Vec<Value> {
    VALIDATED_TABLES.lock().unwrap().drain(..).collect()
}

/// Structural schema for the fake source: a table with ONLY the
/// `token_secret` logical reference. Records the exact JSON it observed so
/// tests can compare it against what `build` received.
fn validate_test_external_cookie(table: &Value) -> Result<(), String> {
    let Some(object) = table.as_object() else {
        return Err("api.auth.test_external_cookie: must be a table".to_string());
    };
    if let Some(key) = object.keys().find(|key| key.as_str() != "token_secret") {
        return Err(format!("api.auth.test_external_cookie.{key}: unknown field"));
    }
    let value = object
        .get("token_secret")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| {
            "api.auth.test_external_cookie.token_secret: required and must not be empty".to_string()
        })?;
    VALIDATED_TABLES
        .lock()
        .unwrap()
        .push(Value::String(value.to_string()));
    Ok(())
}

/// The fake source's build: resolves ITS ONE secret through the context's
/// secret PORT (recording nothing about the value) and returns a delivery
/// verifier pinned to its registration name.
fn build_test_external_cookie(
    ctx: ApiAuthBuildContext,
) -> Pin<Box<dyn Future<Output = Result<Arc<dyn PrincipalVerifier>, String>> + Send>> {
    Box::pin(async move {
        BUILT_NAMES.lock().unwrap().push(ctx.name.clone());
        BUILT_OPTIONS.lock().unwrap().push(ctx.options.clone());
        let reference = ctx
            .options
            .get("token_secret")
            .and_then(Value::as_str)
            .map(str::to_string)
            .ok_or_else(|| {
                "api.auth.test_external_cookie.token_secret: required (category: missing_field)"
                    .to_string()
            })?;
        ctx.secret_access
            .get_secret(&reference)
            .await
            .map_err(|_| {
                "api.auth.test_external_cookie.token_secret: referenced secret unavailable (category: secret_unavailable)"
                    .to_string()
            })?;
        Ok(Arc::new(TestExternalCookieVerifier) as Arc<dyn PrincipalVerifier>)
    })
}

/// The fake delivery verifier: answers ONLY its marker header, pinning the
/// registered source name and a cookie-kind credential (so the assembly
/// must also compose trusted origins).
struct TestExternalCookieVerifier;

#[async_trait]
impl PrincipalVerifier for TestExternalCookieVerifier {
    async fn verify(
        &self,
        headers: &axum::http::HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        let mut values = headers.get_all("x-test-external-cookie").iter();
        let Some(first) = values.next() else {
            return Err(PrincipalVerificationError::Missing);
        };
        if values.next().is_some() || first.to_str().unwrap_or_default().is_empty() {
            return Err(PrincipalVerificationError::Invalid(
                "test external cookie must be a single, non-empty marker".into(),
            ));
        }
        let caller = AuthenticatedCaller {
            tenant: None,
            user: Some(AuthenticatedUserIdentity {
                id: "fake-user-1".into(),
                username: "fake-user-1".into(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        };
        Ok(VerifiedRequestIdentity::pin(
            caller,
            "test_external_cookie",
            CredentialKind::SsoCookie,
        ))
    }
}

/// Wraps [`InMemorySecretAccess`] and records every logical name requested
/// through the port (clones share one call log). WHICH secrets get resolved
/// is exactly what N4 and the assembly contract constrain.
#[derive(Clone)]
struct CountingSecretAccess {
    store: InMemorySecretAccess,
    calls: Arc<Mutex<Vec<String>>>,
}

impl CountingSecretAccess {
    fn new(entries: impl IntoIterator<Item = (&'static str, &'static str)>) -> Self {
        let calls = Arc::new(Mutex::new(Vec::new()));
        CountingSecretAccess {
            store: InMemorySecretAccess::with_entries(
                entries
                    .into_iter()
                    .map(|(name, value)| (name.to_string(), String::new(), value.to_string())),
            ),
            calls,
        }
    }

    fn requested_names(&self) -> Vec<String> {
        self.calls.lock().unwrap().clone()
    }

    /// Hand the port-typed handle to the assembly while keeping a clone
    /// for the call-log assertions.
    fn into_port(self) -> Arc<dyn SecretAccessPort> {
        Arc::new(self)
    }
}

#[async_trait]
impl SecretAccessPort for CountingSecretAccess {
    async fn get_secret(&self, name: &str) -> Result<SecretRecord, SecretAccessError> {
        self.calls.lock().unwrap().push(name.to_string());
        self.store.get_secret(name).await
    }
}

fn fake_registration() -> ApiAuthSourceRegistration {
    ApiAuthSourceRegistration {
        name: "test_external_cookie".to_string(),
        capabilities: ApiAuthCapabilities {
            uses_browser_cookie: true,
            is_oauth_provider: false,
        },
        validate: validate_test_external_cookie,
        build: build_test_external_cookie,
    }
}

/// All registrations: the production set PLUS the fake descriptor.
fn all_registrations() -> Vec<ApiAuthSourceRegistration> {
    let mut registrations = production_registrations();
    registrations.push(fake_registration());
    registrations
}

fn api_auth_section(body: &str) -> ApiAuthConfig {
    toml::from_str(body).expect("test [api.auth] body parses")
}

// ---------------------------------------------------------------------------
// Assembly fixtures
// ---------------------------------------------------------------------------

/// Explicit chain selecting ONLY the fake external-cookie source.
const FAKE_ONLY_BODY: &str = r#"
chain = ["test_external_cookie"]
trusted_browser_origins = ["https://workbench.example.com"]

[test_external_cookie]
token_secret = "fake-cookie-secret"
"#;

/// A gateway marker header the fake chain must NOT answer: only a BUILT
/// Gateway source would inspect it (and would reject this token).
fn gateway_marker_headers() -> axum::http::HeaderMap {
    let mut headers = axum::http::HeaderMap::new();
    headers.insert(
        "x-avernet-principal",
        axum::http::HeaderValue::from_static("not-an-actual-gateway-token"),
    );
    headers
}

fn marker_headers() -> axum::http::HeaderMap {
    let mut headers = axum::http::HeaderMap::new();
    headers.insert(
        "x-test-external-cookie",
        axum::http::HeaderValue::from_static("present"),
    );
    headers
}

// The recorded build/validate/secret-call fixtures are process-global;
// keep the assembly tests serialized so their assertions cannot interleave.
#[tokio::test]
#[serial_test::serial]
async fn explicit_chain_builds_only_the_selected_source() {
    take_built_names();
    take_built_options();
    take_validated_tables();

    let access = CountingSecretAccess::new([
        ("fake-cookie-secret", "resolved-fake-value"),
        ("gateway-signing-key", "resolved-gateway-value"),
        ("session-signing-key", "resolved-session-value"),
    ]);
    let identities = memory_identity_ports().auth_session_identity;
    let built = assemble_v1_api_auth(
        &api_auth_section(FAKE_ONLY_BODY),
        &all_registrations(),
        identities,
        "test-env".to_string(),
        Arc::new(access.clone()),
    )
    .await
    .expect("explicit fake-only chain assembles");

    // Build was invoked exactly once, for the chain-selected source only:
    // the production/legacy inventory (gateway, alipay, github, google,
    // wechat) is NOT auto-built.
    assert_eq!(take_built_names(), vec!["test_external_cookie"]);

    // The build received the SAME value-domain JSON the descriptor's
    // validate ran against (no TOML/JSON/float/string round-trip).
    let validated = take_validated_tables();
    let built_options = take_built_options();
    assert_eq!(validated.len(), 1, "validate ran for the enabled table");
    assert_eq!(built_options.len(), 1, "build ran for the enabled source");
    assert_eq!(
        built_options[0]
            .get("token_secret")
            .and_then(Value::as_str)
            .map(str::to_string),
        validated[0].as_str().map(str::to_string),
        "build received the identical validated parameter value"
    );

    // Secret resolution saw ONLY the enabled instance. The Gateway signing
    // secret and the OAuth session signing key were NEVER read.
    assert_eq!(
        access.requested_names(),
        vec!["fake-cookie-secret".to_string()],
        "only the enabled source's secret may be resolved"
    );

    // A cookie-kind source requires the composed trusted origins to leave
    // the assembly (TrustedBrowserOrigins::new already validated them).
    let origins = built
        .origins
        .as_ref()
        .expect("cookie source chain carries trusted browser origins");
    assert_eq!(origins.origins(), ["https://workbench.example.com"]);

    // Dispatch: the marker header authenticates with the pinned provenance;
    // a bare request is Missing; a Gateway header is Missing too (the
    // Gateway source was not auto-added — otherwise it would have answered
    // Invalid, because that token cannot pass signature verification).
    let verified = built
        .verifier
        .verify(&marker_headers())
        .await
        .expect("fake source answers its marker");
    assert_eq!(
        verified.authentication_context.source,
        "test_external_cookie"
    );
    assert_eq!(
        verified.authentication_context.credential_kind,
        CredentialKind::SsoCookie
    );
    assert!(
        matches!(
            built.verifier.verify(&axum::http::HeaderMap::new()).await,
            Err(PrincipalVerificationError::Missing)
        ),
        "no credential keeps the chain Missing"
    );
    assert!(
        matches!(
            built.verifier.verify(&gateway_marker_headers()).await,
            Err(PrincipalVerificationError::Missing)
        ),
        "the legacy gateway inventory must not be auto-added to an explicit chain"
    );

    // No OAuth source was enabled: no V1 auth facade; /user stays served by
    // the delivery projection (auth_service is None, not an error).
    assert!(built.auth_service.is_none());
}

#[tokio::test]
#[serial_test::serial]
async fn chain_with_gateway_builds_it_and_reads_only_enabled_secrets() {
    take_built_names();
    let body = r#"
chain = ["gateway", "test_external_cookie"]
trusted_browser_origins = ["https://workbench.example.com"]

[gateway]
issuers = ["gateway"]
audience = "bcs"
key_id = "bare"
signing_key_secret = "gateway-signing-key"

[test_external_cookie]
token_secret = "fake-cookie-secret"
"#;
    let access = CountingSecretAccess::new([
        ("fake-cookie-secret", "resolved-fake-value"),
        ("gateway-signing-key", "resolved-gateway-value"),
        ("session-signing-key", "resolved-session-value"),
    ]);
    let identities = memory_identity_ports().auth_session_identity;
    let built = assemble_v1_api_auth(
        &api_auth_section(body),
        &all_registrations(),
        identities,
        "test-env".to_string(),
        Arc::new(access.clone()),
    )
    .await
    .expect("explicit gateway+fake chain assembles");

    // Build ran for both enabled sources — the recorded fake build plus the
    // Gateway key resolution in CHAIN order (the production Gateway build
    // does not record into the fake's instrumentation; its secret request
    // order is the observable proof).
    assert_eq!(take_built_names(), vec!["test_external_cookie".to_string()]);
    // The enabled Gateway source's signing key IS resolved (chain order),
    // but no OAuth source is enabled, so the session signing key still is
    // NEVER read.
    assert_eq!(
        access.requested_names(),
        vec![
            "gateway-signing-key".to_string(),
            "fake-cookie-secret".to_string(),
        ],
        "secret resolution follows the chain's enabled instances only"
    );
    assert!(
        built.auth_service.is_none(),
        "no OAuth source enabled means no auth facade"
    );
    assert!(
        built.origins.is_some(),
        "the chain enables a cookie source, so origins must be composed"
    );
}

#[tokio::test]
#[serial_test::serial]
async fn duplicate_registrations_are_rejected_at_assembly() {
    let identities = memory_identity_ports().auth_session_identity;
    let mut duplicated = all_registrations();
    duplicated.push(fake_registration());
    let outcome = assemble_v1_api_auth(
        &api_auth_section(FAKE_ONLY_BODY),
        &duplicated,
        identities,
        "test-env".to_string(),
        CountingSecretAccess::new([("fake-cookie-secret", "value")]).into_port(),
    )
    .await;
    let error = match outcome {
        Err(error) => error,
        Ok(_) => panic!("duplicate registration names must refuse the assembly"),
    };
    assert!(
        error.contains("duplicate registration name"),
        "closed refusal message, got: {error}"
    );
}

#[tokio::test]
#[serial_test::serial]
async fn unregistered_chain_source_is_rejected_at_assembly() {
    take_built_names();
    let identities = memory_identity_ports().auth_session_identity;
    let outcome = assemble_v1_api_auth(
        &api_auth_section(r#"chain = ["not_a_registered_source"]"#),
        &all_registrations(),
        identities,
        "test-env".to_string(),
        CountingSecretAccess::new([]).into_port(),
    )
    .await;
    let error = match outcome {
        Err(error) => error,
        Ok(_) => panic!("an unregistered chain source must refuse the assembly"),
    };
    assert!(
        error.contains("not registered"),
        "closed refusal message, got: {error}"
    );
    assert!(take_built_names().is_empty(), "no build runs on refusal");
}
