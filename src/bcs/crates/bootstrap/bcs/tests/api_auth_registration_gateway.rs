//! Task 12 — gateway-source assembly and construction-path unification.
//!
//! Split from `api_auth_registration.rs` (OAuth dispatch + selection lives
//! there; this file proves the gateway source's build/secret scoping and the
//! unified injection of the SAME composite verifier Arc into the V1 ApiState
//! and the standalone connection-token router construction).

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use async_trait::async_trait;
use axum::http::{HeaderMap, HeaderValue};
use serial_test::serial;

use bcs::api_auth_registry::default_api_auth_registrations;
use bcs::api_auth_wiring::{ApiAuthAssemblyInputs, build_api_auth};
use bcs::{BcsConfig, BcsServer};
use bcs_auth_api::AuthSessionIdentityPort;
use bcs_config_api::ApiAuthConfig;
use bcs_service_api::port::secret::{SecretAccessError, SecretAccessPort, SecretRecord};

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

fn auth_from_toml(src: &str) -> ApiAuthConfig {
    toml::from_str(src).unwrap_or_else(|e| panic!("parse ApiAuthConfig: {e}"))
}

fn memory_sessions() -> Arc<dyn AuthSessionIdentityPort> {
    Arc::new(bcs::identity_session_wiring::RepoAuthSessionIdentityPort::new(
        Arc::new(bcs_user_identity::MemoryUserIdentityRepo::new()),
        Arc::new(bcs_user_identity::MemoryUserIdentityRepo::new()),
    ))
}

fn inputs(
    auth: ApiAuthConfig,
    secret_access: Arc<CountingSecretAccess>,
) -> ApiAuthAssemblyInputs {
    ApiAuthAssemblyInputs {
        registrations: default_api_auth_registrations(),
        config: auth,
        secret_access,
        sessions: memory_sessions(),
        env: "local".to_string(),
        oauth: None,
        session_signing_material: None,
    }
}

// ---------------------------------------------------------------------------
// Secret resolution scoping
// ---------------------------------------------------------------------------

#[tokio::test]
async fn gateway_only_chain_resolves_exactly_one_secret() {
    let auth = auth_from_toml(
        r#"chain = ["gateway"]

[gateway]
signing_key_secret = "gw-signing"
"#,
    );
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[(
        "gw-signing",
        "gateway-principal-test-key",
    )]));
    let built = build_api_auth(inputs(auth, secrets.clone()))
        .await
        .expect("gateway-only chain builds");
    assert_eq!(secrets.reads(), 1, "exactly gateway's signing key is read");
    assert_eq!(built.sources, vec!["gateway".to_string()]);
    // Gateway-only: no OAuth service — /user works via the delivery
    // projection instead (spec §6: the V1 AuthService consumes the new
    // assembly only when an OAuth source is in chain).
    assert!(
        built.auth_service.is_none(),
        "gateway-only assembly must not attach an OAuth AuthService"
    );
    // The composite is the gateway verifier: no gateway principal header →
    // Missing (gateway yields Missing, not Invalid, without a header).
    let outcome = built.verifier.verify(&HeaderMap::new()).await;
    assert!(matches!(
        outcome,
        Err(bcs_api_http::PrincipalVerificationError::Missing)
    ));
}

#[tokio::test]
async fn non_enabled_source_secret_is_never_read() {
    let auth = auth_from_toml(
        r#"chain = ["gateway"]

[gateway]
signing_key_secret = "gw-signing"

[alipay]
client_id = "unused-id"
private_key_secret = "alipay-never-read"
alipay_public_key_secret = "alipay-public-never-read"
"#,
    );
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[
        ("gw-signing", "gateway-principal-test-key"),
        ("alipay-never-read", "unused"),
        ("alipay-public-never-read", "unused"),
    ]));
    let built = build_api_auth(inputs(auth, secrets.clone()))
        .await
        .expect("gateway chain with unused alipay table builds");
    assert_eq!(
        secrets.reads(),
        1,
        "present-but-not-in-chain alipay table's secrets must NOT be read"
    );
    assert_eq!(built.sources, vec!["gateway".to_string()]);
}

#[tokio::test]
async fn gateway_secret_error_carries_only_field_path() {
    let auth = auth_from_toml(
        r#"chain = ["gateway"]

[gateway]
signing_key_secret = "missing-gw-signing"
"#,
    );
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[]));
    let err = build_api_auth(inputs(auth, secrets))
        .await
        .expect_err("missing gateway secret fails startup");
    assert!(
        err.contains("api.auth.gateway.signing_key_secret"),
        "error must carry the logical field path, got: {err}"
    );
    assert!(
        !err.contains("secret not found"),
        "error must not leak the backend error detail, got: {err}"
    );
}

// ---------------------------------------------------------------------------
// Gateway source resolves the Human through the assembled composite
// ---------------------------------------------------------------------------

/// Mint a valid Gateway-signed Human principal token (same claim shape as
/// the shared api-contract fixture: `type: "user"` + `subject`).
fn mint_gateway_human_token(signing_material: &str) -> String {
    use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let mut header = Header::new(Algorithm::HS256);
    header.typ = Some("JWT".to_string());
    header.kid = Some("bare".to_string()); // default api.auth.gateway.key_id
    encode(
        &header,
        &serde_json::json!({
            "iss": "gateway",
            "aud": "bcs",
            "iat": now - 1,
            "exp": now + 60,
            "principals": [{
                "type": "user",
                "tenant": null,
                "subject": {
                    "id": "user-1",
                    "username": "alice",
                    "display_name": "Alice",
                    "full_name": null,
                    "tenant_id": null
                }
            }]
        }),
        &EncodingKey::from_secret(signing_material.as_bytes()),
    )
    .expect("gateway test token signs")
}

#[tokio::test]
async fn gateway_only_chain_resolves_human_through_the_new_verifier() {
    let auth = auth_from_toml(
        r#"chain = ["gateway"]

[gateway]
signing_key_secret = "gw-signing"
"#,
    );
    let material = "gateway-human-resolution-test-key";
    let secrets = Arc::new(CountingSecretAccess::with_entries(&[("gw-signing", material)]));
    let built = build_api_auth(inputs(auth, secrets))
        .await
        .expect("gateway chain builds");

    // auth_service None is VALID: /user still resolves the Human through
    // the verifier's delivery projection.
    assert!(built.auth_service.is_none());
    let mut headers = HeaderMap::new();
    headers.insert(
        "x-avernet-principal",
        HeaderValue::from_str(&mint_gateway_human_token(material)).expect("header value"),
    );
    let verified = built
        .verifier
        .verify(&headers)
        .await
        .expect("gateway resolves");
    assert_eq!(verified.authentication_context.source, "gateway");
    assert!(matches!(
        verified.authentication_context.credential_kind,
        bcs_api_http::CredentialKind::GatewayPrincipalHeader
    ));
    let user = verified.caller.user.expect("Human caller");
    assert_eq!(user.id, "user-1");
    assert_eq!(user.username, "alice");
    assert_eq!(built.sources, vec!["gateway".to_string()]);
}

// ---------------------------------------------------------------------------
// Construction-path integration: unified injection into the real server
// ---------------------------------------------------------------------------

#[tokio::test]
#[serial]
async fn construction_publishes_the_same_verifier_arc_into_state_and_api() {
    // Secret provider = env; the gateway signing material is injected via
    // BCS_SECRET_GW_SIGNING (snapshot taken at construction time). The
    // LEGACY [gateway_principal] contract keeps its own key requirement
    // (spec §4.3: legacy consumers keep their own contract), so its env
    // material is also provided.
    let saved = std::env::var("BCS_SECRET_GW_SIGNING").ok();
    let saved_legacy = std::env::var("AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE").ok();
    unsafe {
        std::env::set_var("BCS_SECRET_GW_SIGNING", "construction-path-gateway-key");
        std::env::set_var(
            "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE",
            "legacy-compat-principal-key",
        );
    }
    let dir = tempfile::tempdir().expect("tempdir");
    let config: BcsConfig = toml::from_str(&format!(
        r#"
bots_base_dir = "{dir}"

[secret]
provider = "env"

[api.auth]
chain = ["gateway"]

[api.auth.gateway]
signing_key_secret = "GW_SIGNING"
"#,
        dir = dir.path().display()
    ))
    .expect("config parses");

    let server = BcsServer::new(config);
    let state = server.state();

    // The assembly is Some and carries exactly the enabled source.
    let built = state
        .built_api_auth
        .as_ref()
        .expect("[api.auth] active → built assembly present");
    assert_eq!(built.sources, vec!["gateway".to_string()]);
    assert!(
        built.auth_service.is_none(),
        "gateway-only has no OAuth facade"
    );

    // Unified injection: the V1 ApiState and the retained verifier field
    // hold the SAME Arc — the standalone connection-token router, which
    // reads the retained field, therefore verifies with the composite too.
    assert!(
        std::sync::Arc::ptr_eq(&built.verifier, &state.openapi_v1.principal_verifier),
        "openapi_v1 state must carry the composite Arc"
    );
    assert!(
        std::sync::Arc::ptr_eq(&built.verifier, &state.gateway_principal_verifier),
        "standalone connection-token router must carry the SAME composite Arc"
    );

    // End-to-end: the minted Gateway Human token resolves through the
    // published verifier with the Gateway provenance.
    let mut headers = HeaderMap::new();
    headers.insert(
        "x-avernet-principal",
        HeaderValue::from_str(&mint_gateway_human_token("construction-path-gateway-key"))
            .expect("header value"),
    );
    let verified = state
        .openapi_v1
        .principal_verifier
        .verify(&headers)
        .await
        .expect("gateway token resolves through the construction-published composite");
    assert_eq!(verified.authentication_context.source, "gateway");

    if let Some(v) = saved {
        unsafe { std::env::set_var("BCS_SECRET_GW_SIGNING", v) };
    } else {
        unsafe { std::env::remove_var("BCS_SECRET_GW_SIGNING") };
    }
    if let Some(v) = saved_legacy {
        unsafe { std::env::set_var("AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE", v) };
    } else {
        unsafe { std::env::remove_var("AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE") };
    }
}
