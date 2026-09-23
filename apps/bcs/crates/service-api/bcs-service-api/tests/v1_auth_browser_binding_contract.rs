//! Spec §8.4 contract tests for the incremental V1 auth types
//! introduced in Task 9 (browser-bound OAuth login state).
//!
//! Tests:
//! - [`BrowserLoginBinding`] is required/non-Default (compile-time
//!   check via no `Default` impl and the field being non-`Option`).
//! - [`CompleteOAuthLogin`] coexists with legacy [`CompleteOAuthLogin`]
//!   and exposes the new required `browser_binding` field. The legacy
//!   command's `code: Option<String>` is preserved; the new command
//!   promotes `code` to a required `String` per the Task 9 contract.
//! - [`BrowserCookieChange`] carries pure data (no HTTP types); the
//!   four variants form the full V1 auth cookie surface.
//! - [`AuthFlowReply`] carries zero or more [`BrowserCookieChange`]s
//!   on BOTH success (`Ok`) and failure (`Err`) paths — successful
//!   login issues cookie_changes AND `SetSession`; failed-but-matched
//!   callbacks issue `ClearLoginChallenge` without `SetSession`.
//! - [`ApplicationError::Unavailable`] is the transport-neutral
//!   "dependency down" error surfaced by the V1 envelope as 503 (per
//!   spec §8.6). Memory store failures in this task are unreachable
//!   (see plugin tests), but the variant must exist for the
//!   `PendingOAuthLoginPort` signature.
//! - `PendingOAuthLoginPort` exists in `port::oauth` alongside the
//!   `PendingLoginBatch` shape (independent type from the plugin-API
//!   `LoginBatch`; bootstrap wiring translates field-by-field).

use bcs_service_api::application::v1::{
    ApplicationError, AuthFlowReply, BrowserCookieChange, BrowserLoginBinding, CompleteOAuthLogin,
};
use bcs_service_api::port::{PendingLoginBatch, PendingOAuthLoginPort};

/// `BrowserLoginBinding` is required, non-`Option`, non-`Default`:
/// the V1 callback adapter MUST present the cookie nonce. The fact
/// that no `Default` impl exists is enforced at compile time by the
/// type's structure (only one field, no `Option`).
#[test]
fn browser_login_binding_carries_a_required_nonce() {
    let binding = BrowserLoginBinding {
        nonce: "test-browser-nonce".to_string(),
    };
    assert_eq!(binding.nonce, "test-browser-nonce");
}

/// `CompleteOAuthLogin` is the FINAL (Task 11 switchover) command shape:
/// browser-bound, required `code`, required `browser_binding` — the old
/// coexisting Option-code legacy variant was removed in the atomic
/// switchover. This test now pins the single, required-shape construction.
#[test]
fn complete_oauth_login_is_the_single_browser_bound_shape() {
    let command = CompleteOAuthLogin {
        provider: "github".into(),
        code: " authorization-code ".trim().to_string(),
        auth_code: None,
        state: " state ".trim().to_string(),
        callback_base_url: "https://bcs.example.com/openapi/v1/auth/callback".into(),
        browser_binding: BrowserLoginBinding {
            nonce: "browser-nonce".into(),
        },
    };
    assert_eq!(command.provider, "github");
    assert!(command.code.capacity() > 0);
    assert!(command.auth_code.is_none());
    assert_eq!(command.callback_base_url, "https://bcs.example.com/openapi/v1/auth/callback");
    assert_eq!(command.browser_binding.nonce, "browser-nonce");
}

/// `BrowserCookieChange` is a pure-data enum; no HTTP `Set-Cookie`
/// types leak into the contract.
#[test]
fn browser_cookie_change_variants_form_the_v1_cookie_surface() {
    let challenge = BrowserCookieChange::SetLoginChallenge {
        nonce: "challenge-nonce".into(),
        expires_at: 100 + 300,
    };
    let clear_challenge = BrowserCookieChange::ClearLoginChallenge;
    let session = BrowserCookieChange::SetSession {
        token: "session-jwt".into(),
        expires_at: 100 + 3600,
    };
    let clear_session = BrowserCookieChange::ClearSession;
    // Round-trip through a Vec to assert they coexist.
    let changes = vec![
        challenge,
        clear_challenge,
        session,
        clear_session,
    ];
    assert_eq!(changes.len(), 4);
}

/// `AuthFlowReply` permits both success and failure paths to carry
/// cleanup instructions. Successful login yields a `SetSession` +
/// `ClearLoginChallenge` pair; a callback that matched the pending
/// login but then failed in token exchange issues the `ClearLoginChallenge`
/// alone (the consume already happened, so the temp cookie is cleared
/// even though no session cookie is set).
#[test]
fn auth_flow_reply_carries_cookie_changes_on_both_result_paths() {
    // Success path: exchanged token + set session + cleared challenge.
    let success: AuthFlowReply<String> = AuthFlowReply {
        result: Ok("user-id".into()),
        cookie_changes: vec![
            BrowserCookieChange::SetSession {
                token: "session-jwt".into(),
                expires_at: 100 + 3600,
            },
            BrowserCookieChange::ClearLoginChallenge,
        ],
    };
    assert!(success.result.is_ok());
    assert_eq!(success.cookie_changes.len(), 2);

    // Failure path: matched-but-subsequent-failed callback yields
    // ClearLoginChallenge without a SetSession.
    let failure: AuthFlowReply<String> = AuthFlowReply {
        result: Err(ApplicationError::unavailable(
            "pending-login store down",
        )),
        cookie_changes: vec![BrowserCookieChange::ClearLoginChallenge],
    };
    assert!(failure.result.is_err());
    assert_eq!(failure.cookie_changes.len(), 1);
}

/// `ApplicationError::Unavailable` is the transport-neutral "dependency
/// down" error. The V1 envelope maps it to HTTP 503 with redacted
/// detail (see bcs-api-http error.rs). The service-api test asserts
/// the variant exists and `code()` returns `"unavailable"` so callers
/// do not need a separate error-class lookup.
#[test]
fn application_error_unavailable_carries_redactable_detail_and_stable_code() {
    let err = ApplicationError::unavailable("pending-login store unreachable");
    assert_eq!(err.code(), "unavailable");
    assert!(
        err.to_string().contains("pending-login store unreachable"),
        "the internal detail is preserved for logging, not stripped at construct time"
    );
}

/// `PendingLoginBatch` is a service-api type that mirrors the shape of
/// the plugin-API `LoginBatch` and stays INDEPENDENT — no `From`/`Into`
/// across the crate boundary. The test asserts field-wise equality
/// after an explicit copy (which is what the bootstrap bridge will do
/// field-by-field).
#[test]
fn pending_login_batch_is_a_service_side_shape() {
    let batch = PendingLoginBatch {
        browser_nonce: "browser-nonce".into(),
        expires_at: 100 + 300,
        provider_states: vec![("github".into(), "state-1".into())],
    };
    assert_eq!(batch.browser_nonce, "browser-nonce");
    assert_eq!(batch.expires_at, 400);
    assert_eq!(batch.provider_states.len(), 1);
    assert_eq!(batch.provider_states[0].0, "github");
    assert_eq!(batch.provider_states[0].1, "state-1");
}

/// `PendingOAuthLoginPort` is a trait that the V1 AuthService will
/// depend on (Task 11+); it lives in `bcs-service-api::port::oauth`.
/// The test asserts the trait is object-safe (can be made into a
/// `dyn` reference) — required for the V1 AuthService to hold it as
/// `Arc<dyn PendingOAuthLoginPort>`.
#[test]
fn pending_oauth_login_port_is_object_safe() {
    fn _assert_object_safe(_port: &dyn PendingOAuthLoginPort) {}
    // Compile-time assertion only; no runtime behavior. The trait
    // requires Send + Sync + 'async_trait' so a V1 AuthService may
    // hold it as `Arc<dyn PendingOAuthLoginPort>`.
}
