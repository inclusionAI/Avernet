//! `AuthService` contract harness.
//!
//! Exercises the V1 OpenAPI auth facade's high-level orchestration:
//! `login_urls` → `complete_login` → `refresh_session` → `logout`
//! with cookie-change assertions per spec §8.4 and §8.5. The harness
//! takes a `&dyn AuthService` plus the sample inputs (callback base,
//! provider, state, nonce, code) that the conformance test
//! pre-programs its fakes to recognize. Real-impl conformance drivers
//! (e.g. for `AuthApplicationService`) set up the three sub-port fakes
//! and pass the constructed service into this harness.

use bcs_service_api::application::v1::{
    AuthFlowReply, AuthProviderUrl, AuthRedirect, BrowserCookieChange, BuildLoginUrls,
    CompleteOAuthLogin, PresentedSession,
};
use bcs_service_api::application::v1::auth::AuthService;

/// Conformance suite for [`AuthService`].
///
/// Verifies the contract-shape public behaviors:
///
/// 1. `login_urls` returns a non-empty provider URL list that includes
///    the configured sample provider, AND emits a
///    `SetLoginChallenge` cookie change (spec §8.4 — every login URL
///    response sets the `__Host-bcs_oauth_login` challenge cookie).
/// 2. `complete_login` returns `Ok(AuthRedirect)` on a happy-path
///    callback (the harness checks the result is Ok and that the
///    `cookie_changes` set is non-empty — at minimum the application
///    MUST emit `SetSession` on success per spec §8.4).
/// 3. `refresh_session` returns `Ok(SessionRenewal)` and emits a
///    `SetSession` cookie change on success (the harness exercises
///    this against the token the conformance test pre-programs the
///    session port to recognize).
/// 4. `logout(Some(token))` returns `Ok(())` and emits a `ClearSession`
///    cookie change (spec §8.5 — successful logout clears the cookie).
/// 5. `logout(None)` is the idempotent no-cookie logout boundary and
///    still returns `Ok(())`.
pub async fn auth_service_contract_tests(
    svc: &dyn AuthService,
    callback_base_url: &str,
    sample_provider: &str,
    sample_state: &str,
    sample_browser_nonce: &str,
    sample_code: &str,
    sample_presented_token: &str,
) {
    // 1. login_urls
    let login_reply = svc
        .login_urls(BuildLoginUrls {
            callback_base_url: callback_base_url.to_string(),
        })
        .await;
    assert!(
        login_reply.result.is_ok(),
        "login_urls happy-path MUST be Ok"
    );
    let urls: &[AuthProviderUrl] = login_reply.result.as_ref().unwrap();
    assert!(
        !urls.is_empty(),
        "login_urls MUST return a non-empty provider URL list"
    );
    assert!(
        urls.iter().any(|u| u.name == sample_provider),
        "login_urls MUST include the configured sample provider {sample_provider}"
    );
    assert!(
        login_reply
            .cookie_changes
            .iter()
            .any(|c| matches!(c, BrowserCookieChange::SetLoginChallenge { .. })),
        "login_urls MUST emit a SetLoginChallenge cookie change (spec §8.4)"
    );

    // 2. complete_login
    let login_reply: AuthFlowReply<AuthRedirect> = svc
        .complete_login(CompleteOAuthLogin {
            provider: sample_provider.to_string(),
            code: sample_code.to_string(),
            auth_code: None,
            state: sample_state.to_string(),
            callback_base_url: callback_base_url.to_string(),
            browser_binding: bcs_service_api::application::v1::BrowserLoginBinding {
                nonce: sample_browser_nonce.to_string(),
            },
        })
        .await;
    assert!(
        login_reply.result.is_ok(),
        "complete_login happy-path MUST be Ok"
    );
    assert!(
        !login_reply.cookie_changes.is_empty(),
        "complete_login MUST emit at least one cookie change (SetSession + ClearLoginChallenge per spec §8.4)"
    );
    let has_set_session = login_reply
        .cookie_changes
        .iter()
        .any(|c| matches!(c, BrowserCookieChange::SetSession { .. }));
    let has_clear_challenge = login_reply
        .cookie_changes
        .iter()
        .any(|c| matches!(c, BrowserCookieChange::ClearLoginChallenge));
    assert!(
        has_set_session,
        "complete_login success MUST emit a SetSession cookie change"
    );
    assert!(
        has_clear_challenge,
        "complete_login success MUST emit a ClearLoginChallenge cookie change (spec §8.4)"
    );

    // 3. refresh_session — exercise against the supplied token; the
    //    conformance test pre-programs the session port to return a
    //    rotated token for this input.
    let refresh_reply = svc
        .refresh_session(PresentedSession {
            token: sample_presented_token.to_string(),
        })
        .await;
    assert!(
        refresh_reply.result.is_ok(),
        "refresh_session against the recognized token MUST be Ok"
    );
    assert!(
        refresh_reply
            .cookie_changes
            .iter()
            .any(|c| matches!(c, BrowserCookieChange::SetSession { .. })),
        "refresh_session success MUST emit a SetSession cookie change"
    );

    // 4. logout Some(token)
    let logout_reply = svc
        .logout(Some(PresentedSession {
            token: sample_presented_token.to_string(),
        }))
        .await;
    assert!(
        logout_reply.result.is_ok(),
        "logout with a presented token MUST be Ok"
    );
    assert!(
        logout_reply
            .cookie_changes
            .iter()
            .any(|c| matches!(c, BrowserCookieChange::ClearSession)),
        "logout with a presented token MUST emit ClearSession"
    );

    // 5. logout None — idempotent boundary; no ClearSession required
    let logout_none_reply = svc.logout(None).await;
    assert!(
        logout_none_reply.result.is_ok(),
        "logout with no presented token MUST be Ok (idempotent boundary)"
    );
}
