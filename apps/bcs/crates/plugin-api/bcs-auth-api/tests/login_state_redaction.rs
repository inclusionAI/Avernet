//! Redaction tests for the pending-login contract types (plan §8.4
//! credential-log red line): a `LoginBatch` carries the raw browser nonce
//! and one-time states, so its `Debug` may show only the expiry and
//! provider count — sentinels for those credentials must never format.

use bcs_auth_api::login_state::LoginBatch;

#[test]
fn login_batch_debug_omits_credentials() {
    let batch = LoginBatch {
        browser_nonce: "nonce-sentinel".into(),
        expires_at: 400,
        provider_states: vec![("github".into(), "state-sentinel".into())],
    };
    let debug = format!("{batch:?}");
    assert!(debug.contains("LoginBatch"));
    assert!(!debug.contains("nonce-sentinel"));
    assert!(!debug.contains("state-sentinel"));
}
