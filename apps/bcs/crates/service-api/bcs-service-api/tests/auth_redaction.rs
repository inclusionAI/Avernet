//! Redaction tests for the Task 9 auth service types (plan §8.4
//! credential-log red line): `BrowserLoginBinding`, `CompleteOAuthLogin`,
//! and `BrowserCookieChange` all carry login credentials (nonce, state,
//! code, session token), and even the outer `AuthFlowReply` envelope
//! must format them away. Asserted with sentinel credentials constructed
//! into every field; failure messages deliberately do not echo the
//! formatted value.

use bcs_service_api::application::v1::{
    ApplicationError, AuthFlowReply, BrowserCookieChange, BrowserLoginBinding, CompleteOAuthLogin,
};
use bcs_service_api::port::oauth::{BrowserSession, PendingLoginBatch};

/// Fixed sentinel credentials (plan Task 9 step 3a).
const NONCE_SENTINEL: &str = "nonce-sentinel";
const STATE_SENTINEL: &str = "state-sentinel";
const CODE_SENTINEL: &str = "code-sentinel";
const TOKEN_SENTINEL: &str = "token-sentinel";

/// Real, required browser binding — no `Option` or default-empty escape.
fn bound_login() -> CompleteOAuthLogin {
    CompleteOAuthLogin {
        provider: "github".to_string(),
        code: Some(CODE_SENTINEL.to_string()),
        auth_code: None,
        state: STATE_SENTINEL.to_string(),
        callback_base_url: "https://bcs.example.com/openapi/v1/auth/callback".to_string(),
        browser_binding: BrowserLoginBinding {
            nonce: NONCE_SENTINEL.to_string(),
        },
    }
}

/// Every cookie-instruction variant; sentinels where a variant carries
/// credential material.
fn cookie_changes() -> Vec<BrowserCookieChange> {
    vec![
        BrowserCookieChange::SetLoginChallenge {
            nonce: NONCE_SENTINEL.to_string(),
            expires_at: 400,
        },
        BrowserCookieChange::ClearLoginChallenge,
        BrowserCookieChange::SetSession {
            token: TOKEN_SENTINEL.to_string(),
            expires_at: 400,
        },
        BrowserCookieChange::ClearSession,
    ]
}

fn assert_no_sentinels(debug: &str, subject: &str) {
    assert!(
        !debug.contains(NONCE_SENTINEL),
        "{subject} debug leaked the nonce sentinel"
    );
    assert!(
        !debug.contains(STATE_SENTINEL),
        "{subject} debug leaked the state sentinel"
    );
    assert!(
        !debug.contains(CODE_SENTINEL),
        "{subject} debug leaked the code sentinel"
    );
    assert!(
        !debug.contains(TOKEN_SENTINEL),
        "{subject} debug leaked the token sentinel"
    );
}

#[test]
fn browser_login_binding_debug_omits_nonce() {
    let binding = BrowserLoginBinding {
        nonce: NONCE_SENTINEL.to_string(),
    };
    let debug = format!("{binding:?}");
    assert!(debug.contains("BrowserLoginBinding"), "debug names the type");
    assert_no_sentinels(&debug, "BrowserLoginBinding");
}

#[test]
fn bound_oauth_login_debug_omits_credentials() {
    let login = bound_login();
    let debug = format!("{login:?}");
    assert!(debug.contains("CompleteOAuthLogin"), "debug names the type");
    assert_no_sentinels(&debug, "CompleteOAuthLogin");
}

#[test]
fn browser_cookie_change_debug_omits_credentials() {
    for change in cookie_changes() {
        let debug = format!("{change:?}");
        assert!(
            debug.contains("BrowserCookieChange"),
            "debug names the enum"
        );
        assert_no_sentinels(&debug, "BrowserCookieChange");
    }
}

#[test]
fn auth_flow_reply_envelope_hides_credentials_on_success() {
    let reply: AuthFlowReply<()> = AuthFlowReply {
        result: Ok(()),
        cookie_changes: cookie_changes(),
    };
    let debug = format!("{reply:?}");
    assert!(debug.contains("AuthFlowReply"), "debug names the envelope");
    assert_no_sentinels(&debug, "AuthFlowReply(success)");
}

#[test]
fn auth_flow_reply_envelope_hides_credentials_on_failure() {
    let reply: AuthFlowReply<()> = AuthFlowReply {
        result: Err(ApplicationError::invalid("invalid_state", "login state rejected")),
        cookie_changes: vec![BrowserCookieChange::ClearLoginChallenge],
    };
    let debug = format!("{reply:?}");
    assert!(debug.contains("AuthFlowReply"), "debug names the envelope");
    assert_no_sentinels(&debug, "AuthFlowReply(failure)");
}

#[test]
fn pending_login_batch_debug_omits_credentials() {
    let batch = PendingLoginBatch {
        browser_nonce: NONCE_SENTINEL.to_string(),
        expires_at: 400,
        provider_states: vec![("github".to_string(), STATE_SENTINEL.to_string())],
    };
    let debug = format!("{batch:?}");
    assert!(debug.contains("PendingLoginBatch"), "debug names the type");
    assert_no_sentinels(&debug, "PendingLoginBatch");
}

#[test]
fn browser_session_debug_omits_token() {
    let session = BrowserSession {
        token: TOKEN_SENTINEL.to_string(),
        expires_at: 400,
    };
    let debug = format!("{session:?}");
    assert!(debug.contains("BrowserSession"), "debug names the type");
    assert_no_sentinels(&debug, "BrowserSession");
}
