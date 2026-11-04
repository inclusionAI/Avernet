//! Acceptance for the downstream registration seam (PR #3489 review):
//! a source submitted from OUTSIDE the production inventory via
//! `inventory::submit!` must merge into `production_registrations()` and
//! flow through the REAL production selection path
//! (`select_v1_authentication`) — validate when its table is present,
//! build when chained — with no manual vec composition at the call site.
//!
//! The fake source mimics an overlay SSO-cookie source: cookie-capable,
//! non-OAuth, marker-header driven. This file is its own test binary, so
//! the submission cannot pollute other suites' production inventories.

use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::http::HeaderMap;
use bcs::api_auth_registry::{
    ApiAuthBuildContext, ApiAuthCapabilities, ApiAuthSourceRegistration,
    ApiAuthSourceRegistrationFactory,
};
use bcs::api_auth_wiring::{ApiAuthSelection, production_registrations, select_v1_authentication};
use bcs::identity_wiring::memory_identity_ports;
use bcs_api_http::{
    CredentialKind, PrincipalVerificationError, PrincipalVerifier, VerifiedRequestIdentity,
};
use bcs_secret_local::InMemorySecretAccess;
use bcs_service_api::application::v1::{AuthenticatedCaller, AuthenticatedUserIdentity};
use serde_json::Value;

#[allow(dead_code)] // shared e2e helpers; this suite uses create_test_config only
mod helpers;

static VALIDATED_TABLES: Mutex<Vec<Value>> = Mutex::new(Vec::new());
static BUILT_NAMES: Mutex<Vec<String>> = Mutex::new(Vec::new());

fn validate_overlay_cookie(table: &Value) -> Result<(), String> {
    let Some(object) = table.as_object() else {
        return Err("must be a table".to_string());
    };
    if let Some(key) = object.keys().find(|key| key.as_str() != "marker") {
        return Err(format!("{key}: unknown field"));
    }
    let value = object
        .get("marker")
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| "marker: required and must not be empty".to_string())?;
    VALIDATED_TABLES
        .lock()
        .unwrap()
        .push(Value::String(value.to_string()));
    Ok(())
}

fn build_overlay_cookie(
    ctx: ApiAuthBuildContext,
) -> Pin<Box<dyn Future<Output = Result<Arc<dyn PrincipalVerifier>, String>> + Send>> {
    Box::pin(async move {
        BUILT_NAMES.lock().unwrap().push(ctx.name.clone());
        Ok(Arc::new(OverlayCookieVerifier) as Arc<dyn PrincipalVerifier>)
    })
}

struct OverlayCookieVerifier;

#[async_trait]
impl PrincipalVerifier for OverlayCookieVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        let mut values = headers.get_all("x-overlay-cookie").iter();
        let Some(first) = values.next() else {
            return Err(PrincipalVerificationError::Missing);
        };
        if values.next().is_some() || first.to_str().unwrap_or_default().is_empty() {
            return Err(PrincipalVerificationError::Invalid(
                "overlay cookie must be a single, non-empty marker".into(),
            ));
        }
        let caller = AuthenticatedCaller {
            tenant: None,
            user: Some(AuthenticatedUserIdentity {
                id: "overlay-user-1".into(),
                username: "overlay-user-1".into(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        };
        Ok(VerifiedRequestIdentity::pin(
            caller,
            "overlay_cookie",
            CredentialKind::SsoCookie,
        ))
    }
}

fn overlay_cookie_registration() -> ApiAuthSourceRegistration {
    ApiAuthSourceRegistration {
        name: "overlay_cookie".to_string(),
        capabilities: ApiAuthCapabilities {
            uses_browser_cookie: true,
            is_oauth_provider: false,
        },
        validate: validate_overlay_cookie,
        build: build_overlay_cookie,
    }
}

inventory::submit! { ApiAuthSourceRegistrationFactory(overlay_cookie_registration) }

const OVERLAY_CHAIN_BODY: &str = r#"
chain = ["overlay_cookie"]
trusted_browser_origins = ["https://workbench.example.com"]

[overlay_cookie]
marker = "overlay-marker"
"#;

fn marker_headers() -> HeaderMap {
    let mut headers = HeaderMap::new();
    headers.insert(
        "x-overlay-cookie",
        axum::http::HeaderValue::from_static("present"),
    );
    headers
}

#[tokio::test]
async fn inventory_submitted_source_flows_through_production_selection() {
    // Type-level: the submission merged into the production inventory
    // alongside the code-owned gateway + OAuth sources.
    let registrations = production_registrations();
    assert!(
        registrations.iter().any(|reg| reg.name == "overlay_cookie"),
        "inventory-submitted source must merge into the production inventory; got: {:?}",
        registrations
            .iter()
            .map(|reg| reg.name.as_str())
            .collect::<Vec<_>>()
    );

    // The REAL production selection entry: no manually composed vec.
    let bots_dir = tempfile::TempDir::new().expect("bots dir");
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.api.auth = Some(toml::from_str(OVERLAY_CHAIN_BODY).expect("overlay chain parses"));
    let selection = select_v1_authentication(
        &config,
        Some(memory_identity_ports().auth_session_identity),
        Arc::new(InMemorySecretAccess::with_entries(
            std::iter::empty::<(String, String, String)>(),
        )),
    )
    .await
    .expect("production selection assembles the chained overlay source");
    let ApiAuthSelection::Explicit(built) = selection else {
        panic!("[api.auth] present => explicit selection");
    };

    // Validated and built exactly once, both pinned to the source's own
    // table/name.
    assert_eq!(
        VALIDATED_TABLES.lock().unwrap().as_slice(),
        [Value::String("overlay-marker".to_string())],
        "validate ran against the submitted source's table"
    );
    assert_eq!(
        BUILT_NAMES.lock().unwrap().as_slice(),
        ["overlay_cookie"],
        "build ran for the chain-enabled submitted source only"
    );

    // Dispatch: the marker header authenticates with pinned provenance;
    // a bare request stays Missing.
    let verified = built
        .verifier
        .verify(&marker_headers())
        .await
        .expect("overlay source answers its marker");
    assert_eq!(verified.authentication_context.source, "overlay_cookie");
    assert_eq!(
        verified.authentication_context.credential_kind,
        CredentialKind::SsoCookie
    );
    assert!(
        matches!(
            built.verifier.verify(&HeaderMap::new()).await,
            Err(PrincipalVerificationError::Missing)
        ),
        "no credential keeps the chain Missing"
    );
}
