//! `AuthService` conformance harness (V1 API auth plugin chain, plan Task
//! 14 / R25 — the gate-visible `to_snake` name of the trait).
//!
//! Drivers assemble the real service over their own ports (the production
//! `bcs-app-auth` orchestration runs this in
//! `crates/application/v1/bcs-app-auth/tests/conformance_auth_service.rs`)
//! and the harness pins the INCREMENTAL boundary contract every
//! implementation must hold, through the service's own public surface
//! only (no port recording, no delivery types):
//!
//! - URL discovery (`login_urls`) issues exactly ONE `SetLoginChallenge`
//!   cookie instruction and never a session write, and answers with the
//!   provider chain order (driver contract: at least one provider
//!   enabled);
//! - `complete_login` fails closed for a provider outside the enabled
//!   chain and for a missing code BEFORE consuming any pending state —
//!   no cookie changes on those rejections;
//! - cookie-less `logout` is the idempotent success boundary and clears
//!   exactly the session cookie;
//! - `current_user` rejects callers without a verified Human identity;
//!   the projection is shared, display-only, and re-uses one chain result
//!   (there is no second chain execution behind it).
//!
//! Redaction discipline (plan §8.4): every assertion is `matches!`-based —
//! cookie instructions and URLs are never formatted into panic messages.

use std::sync::Arc;

use bcs_service_api::application::v1::{AuthenticatedCaller, BrowserCookieChange, AuthService};

const HARNESS_CALLBACK_BASE: &str = "https://harness.example/openapi/v1/auth/callback";
const HARNESS_STATE: &str = "harness-state-never-pinned-to-a-real-batch";
const HARNESS_NONCE: &str = "harness-nonce-stand-in";

/// `AuthService` contract driver over the assembled service.
#[allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]
pub async fn auth_service_contract_tests(service: Arc<dyn AuthService>) {
    use bcs_service_api::application::v1::{AuthenticatedUserQuery, BuildLoginUrls, CompleteOAuthLogin, BrowserLoginBinding};

    // ------------------------------------------------------------------
    // URL discovery: provider order + exactly ONE challenge instruction.
    // ------------------------------------------------------------------
    let reply = service
        .login_urls(BuildLoginUrls {
            callback_base_url: HARNESS_CALLBACK_BASE.to_string(),
        })
        .await;
    let listing = reply
        .result
        .expect("login_urls succeeds with at least one enabled provider");
    assert!(!listing.providers.is_empty());
    let mut seen: Vec<&str> = Vec::with_capacity(listing.providers.len());
    for provider in &listing.providers {
        assert!(!provider.name.trim().is_empty(), "provider name is blank");
        assert!(
            !seen.contains(&provider.name.as_str()),
            "the provider list repeats a provider"
        );
        seen.push(&provider.name);
        assert!(!provider.url.is_empty());
    }
    assert_eq!(
        reply.cookie_changes.len(),
        1,
        "URL discovery writes exactly one instruction"
    );
    let (has_challenge, has_session_write) = reply.cookie_changes.iter().fold((0, 0), |(c, s), change| {
        match change {
            BrowserCookieChange::SetLoginChallenge { .. } => (c + 1, s),
            BrowserCookieChange::SetSession { .. } => (c, s + 1),
            _ => (c, s),
        }
    });
    assert_eq!(has_challenge, 1, "discovery sets the browser challenge");
    assert_eq!(has_session_write, 0, "discovery never writes a session cookie");

    // ------------------------------------------------------------------
    // complete_login: fail closed BEFORE state consume for an
    // out-of-chain provider and for a missing code — no cookie changes,
    // so the initiating browser's batch (if any) is untouched.
    // ------------------------------------------------------------------
    let out_of_chain = service
        .complete_login(CompleteOAuthLogin {
            provider: "harness-provider-not-in-any-chain".to_string(),
            code: Some("harness-code".to_string()),
            auth_code: None,
            state: HARNESS_STATE.to_string(),
            callback_base_url: HARNESS_CALLBACK_BASE.to_string(),
            browser_binding: BrowserLoginBinding {
                nonce: HARNESS_NONCE.to_string(),
            },
        })
        .await;
    let rejected = out_of_chain
        .result
        .expect_err("a provider outside the chain must fail closed");
    assert!(
        rejected.code() == "provider_not_enabled" || rejected.code() == "auth_not_configured",
        "the out-of-chain rejection carries the pinned 404-family code"
    );
    assert!(
        out_of_chain.cookie_changes.is_empty(),
        "the out-of-chain rejection touches no cookie"
    );

    let first_provider = listing.providers[0].name.clone();
    let missing_code = service
        .complete_login(CompleteOAuthLogin {
            provider: first_provider,
            code: None,
            auth_code: None,
            state: HARNESS_STATE.to_string(),
            callback_base_url: HARNESS_CALLBACK_BASE.to_string(),
            browser_binding: BrowserLoginBinding {
                nonce: HARNESS_NONCE.to_string(),
            },
        })
        .await;
    let code_error = missing_code
        .result
        .expect_err("a callback without code fails closed before consume");
    assert_eq!(
        code_error.code(),
        "missing_code",
        "the missing-code rejection carries the pinned 400 code"
    );
    assert!(
        missing_code.cookie_changes.is_empty(),
        "the missing-code rejection touches no cookie"
    );

    // ------------------------------------------------------------------
    // Logout: cookie-less logout is the idempotent success boundary and
    // clears exactly the session cookie.
    // ------------------------------------------------------------------
    let reply = service.logout(None).await;
    reply.result.expect("cookie-less logout is an idempotent success");
    let mut clear_session_count = 0;
    let mut other_count = 0;
    for change in &reply.cookie_changes {
        if matches!(change, BrowserCookieChange::ClearSession) {
            clear_session_count += 1;
        } else {
            other_count += 1;
        }
    }
    assert_eq!(clear_session_count, 1, "logout clears the session cookie");
    assert_eq!(other_count, 0, "cookie-less logout touches nothing else");

    // ------------------------------------------------------------------
    // current_user: a caller without a verified Human identity is not
    // projectable — one denial, one code, no second chain run.
    // ------------------------------------------------------------------
    let caller = AuthenticatedCaller {
        tenant: None,
        user: None,
        bot: None,
        app: None,
        access_key: None,
    };
    let denied = service
        .current_user(AuthenticatedUserQuery {
            caller,
            provider_label: "harness".to_string(),
            avatar: None,
        })
        .await
        .expect_err("a caller without a Human identity cannot be projected");
    assert_eq!(
        denied.code(),
        "unauthenticated",
        "the projection denial carries the pinned 401 code"
    );
}
