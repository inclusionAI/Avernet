//! Sibling guard for the downstream registration seam: an
//! `inventory::submit!` registration whose name collides with a code-owned
//! production source must stay startup-fatal — merging downstream
//! registrations must not weaken `index_registrations`.
//!
//! Separate test binary on purpose: this binary's extra `gateway`
//! submission makes EVERY assembly of `production_registrations()` fail
//! here, without affecting any other suite.

use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

use bcs::api_auth_registry::{
    ApiAuthBuildContext, ApiAuthCapabilities, ApiAuthSourceRegistration,
    ApiAuthSourceRegistrationFactory,
};
use bcs::api_auth_wiring::{assemble_v1_api_auth, production_registrations};
use bcs::identity_wiring::memory_identity_ports;
use bcs_api_http::PrincipalVerifier;
use bcs_config_api::api_auth::ApiAuthConfig;
use bcs_secret_local::InMemorySecretAccess;
use serde_json::Value;

fn accept_any(_: &Value) -> Result<(), String> {
    Ok(())
}

fn never_build(
    ctx: ApiAuthBuildContext,
) -> Pin<Box<dyn Future<Output = Result<Arc<dyn PrincipalVerifier>, String>> + Send>> {
    let _ = ctx;
    Box::pin(async { Err("a colliding registration must never build".to_string()) })
}

fn colliding_gateway_registration() -> ApiAuthSourceRegistration {
    ApiAuthSourceRegistration {
        name: "gateway".to_string(),
        capabilities: ApiAuthCapabilities {
            uses_browser_cookie: false,
            is_oauth_provider: false,
        },
        validate: accept_any,
        build: never_build,
    }
}

inventory::submit! { ApiAuthSourceRegistrationFactory(colliding_gateway_registration) }

#[tokio::test]
async fn colliding_inventory_name_is_startup_fatal() {
    let config: ApiAuthConfig =
        toml::from_str("chain = [\"gateway\"]\n").expect("gateway chain config parses");
    let result = assemble_v1_api_auth(
        &config,
        &production_registrations(),
        memory_identity_ports().auth_session_identity,
        "test-env".to_string(),
        Arc::new(InMemorySecretAccess::with_entries(
            std::iter::empty::<(String, String, String)>(),
        )),
    )
    .await;
    let Err(error) = result else {
        panic!("an inventory name colliding with a production source must refuse assembly");
    };
    assert!(
        error.contains("duplicate registration name `gateway`"),
        "unexpected error: {error}"
    );
}
