//! Production selection must reject incompatible shared-cookie signing keys.
use std::sync::Arc;
use bcs::api_auth_wiring::{select_v1_authentication, ApiAuthSelection};
use bcs::identity_wiring::memory_identity_ports;
use bcs_secret_local::InMemorySecretAccess;
use serde_json::json;

const KEY: &str = "session-key-test-material";

async fn select(legacy: serde_json::Value, explicit_oauth: bool) -> bcs::Result<ApiAuthSelection> {
    let mut config = bcs::BcsConfig::default();
    config.auth = serde_json::from_value(legacy).unwrap();
    config.api.auth = Some(serde_json::from_value(if explicit_oauth {
        json!({
            "chain": ["github"],
            "public_base_url": "https://bcs.example.com/openapi/v1/auth",
            "session_signing_key_secret": "session",
            "trusted_browser_origins": ["https://bcs.example.com"],
            "github": {"client_id": "test-client", "client_secret_secret": "client"}
        })
    } else {
        json!({"chain": ["gateway"], "gateway": {"signing_key_secret": "gateway"}})
    }).unwrap());
    select_v1_authentication(&config, Some(memory_identity_ports().auth_session_identity),
        Arc::new(InMemorySecretAccess::with_entries([
            ("session", String::new(), KEY.into()),
            ("client", String::new(), "test-client-secret".into()),
            ("gateway", String::new(), "test-gateway-key".into()),
        ]))).await
}

fn legacy(key: &str) -> serde_json::Value {
    json!({"chain": ["oauth_session"], "oauth": {
        "jwt_secret": key, "base_url": "https://bcs.example.com",
        "providers": {"github": {"client_id": "test-client", "client_secret": "test-secret"}}
    }})
}

#[tokio::test]
async fn shared_cookie_rejects_different_keys_without_disclosing_either() {
    // Both the writer-only and reader-only legacy configurations matter.
    for mode in ["both", "writer", "reader", "empty_reader"] {
        let mut auth = legacy("different-private-test-key");
        if mode == "writer" { auth["chain"] = json!(["session"]); }
        if mode == "reader" { auth["oauth"]["providers"] = json!({}); }
        if mode == "empty_reader" { auth["oauth"]["jwt_secret"] = json!(""); }
        let error = match select(auth, true).await {
            Err(error) => error.to_string(),
            Ok(_) => panic!("mismatched {mode} must fail startup"),
        };
        assert!(error.contains("session_signing_key_mismatch"));
        assert!(error.contains("auth.oauth.jwt_secret"));
        assert!(!error.contains(KEY));
        assert!(!error.contains("different-private-test-key"));
    }
}

#[tokio::test]
async fn matching_keys_and_independent_single_entries_are_valid() {
    assert!(select(legacy(KEY), true).await.is_ok());
    assert!(select(json!({"chain": ["session"]}), true).await.is_ok());
    assert!(select(legacy("legacy-only-key"), false).await.is_ok());
    let mut inactive = legacy("unused-key");
    inactive["chain"] = json!(["session"]);
    inactive["oauth"]["providers"] = json!({});
    assert!(select(inactive, true).await.is_ok());
    let mut disabled_writer = legacy("");
    disabled_writer["chain"] = json!(["session"]);
    assert!(select(disabled_writer, true).await.is_ok());
}
