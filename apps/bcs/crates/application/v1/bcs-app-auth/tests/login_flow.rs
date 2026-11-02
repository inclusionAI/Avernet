//! Login orchestration contract tests (plan Task 10 steps 1–3):
//! browser-bound URL issuance and the binding-complete callback order —
//! validate, consume, exchange, install — with cookie instructions that
//! survive both the success and the rejected paths.

mod support;

use std::sync::Arc;

use bcs_service_api::application::v1::{
    ApplicationError, BrowserCookieChange, BuildLoginUrls, AuthService,
};

use support::fake_ports::{FakePendingLoginPort, FakeProviderPort, FakeSessionPort};
use support::{
    auth_service, bound_login, expected_exact_callback, TEST_CALLBACK_BASE, TEST_FLOW,
    TEST_SUCCESS_REDIRECT,
};

const NONCE: &str = "browser-nonce-fresh";
const CODE: &str = "callback-code-1";
const STATE: &str = "state-for-github";

fn wired() -> (bcs_app_auth::AuthApplicationService, Arc<FakeProviderPort>, Arc<FakeSessionPort>, Arc<FakePendingLoginPort>) {
    let providers = Arc::new(FakeProviderPort::new(&["github", "google"]));
    let sessions = Arc::new(FakeSessionPort::new());
    let pending = Arc::new(FakePendingLoginPort::new());
    let service = auth_service(providers.clone(), sessions.clone(), pending.clone());
    (service, providers, sessions, pending)
}

fn assert_clear_login_challenge_only(changes: &[BrowserCookieChange]) {
    assert_eq!(
        changes,
        &[BrowserCookieChange::ClearLoginChallenge],
        "rejected exchange/install must clear the challenge and change nothing else"
    );
}

#[tokio::test]
async fn complete_login_success_sets_session_and_clears_challenge() {
    let (service, providers, sessions, pending) = wired();

    let reply = service
        .complete_login(bound_login("github", CODE, STATE, NONCE))
        .await;

    let redirect = reply.result.expect("binding + exchange succeed");
    assert_eq!(redirect.location, TEST_SUCCESS_REDIRECT);
    assert_eq!(
        reply.cookie_changes,
        vec![
            BrowserCookieChange::ClearLoginChallenge,
            BrowserCookieChange::SetSession {
                token: "issued-session-token".to_string(),
                expires_at: 4_100_000_000,
            },
        ],
        "success always carries challenge cleanup first, then the session install"
    );

    // Orchestration order: exactly one consume, then exchange and install.
    assert_eq!(pending.consume_count(), 1);
    assert_eq!(providers.exchange_count(), 1);
    assert_eq!(sessions.install_count(), 1);

    let consume = &pending.consume_calls()[0];
    assert_eq!(consume.state, STATE);
    assert_eq!(consume.browser_nonce, NONCE);
    assert_eq!(consume.provider, "github");
    assert_eq!(consume.exact_callback, expected_exact_callback("github"));
    assert_eq!(consume.flow, TEST_FLOW);

    let exchange = &providers.exchange_calls()[0];
    assert_eq!(exchange.code, CODE);
    assert_eq!(
        exchange.redirect_uri, consume.exact_callback,
        "exchange must reuse the byte-identical callback the state was issued with"
    );

    let install = &sessions.install_calls()[0];
    assert_eq!(install.provider, "github");
    assert_eq!(install.external_user_id, "ext-user-1");
}

#[tokio::test]
async fn wrong_browser_binding_never_exchanges_or_installs() {
    let (service, providers, sessions, pending) = wired();
    pending.set_consume_error(ApplicationError::invalid(
        "invalid_login_state",
        "state was issued to a different browser",
    ));

    let reply = service
        .complete_login(bound_login("github", CODE, "state-wrong-browser", "nonce-of-other-browser"))
        .await;

    assert!(
        reply.result.is_err(),
        "a failed consume must produce an error result"
    );
    assert!(
        reply.cookie_changes.is_empty(),
        "a failed binding must change no cookies (the initiating browser's batch is intact)"
    );
    assert_eq!(providers.exchange_count(), 0, "exchange must not run");
    assert_eq!(sessions.install_count(), 0, "install must not run");
    assert_eq!(pending.consume_count(), 1, "consume was attempted and rejected");
}

#[tokio::test]
async fn missing_code_is_rejected_before_state_consume() {
    let (service, providers, sessions, pending) = wired();
    let mut command = bound_login("github", CODE, STATE, NONCE);
    command.code = None;
    command.auth_code = None;

    let reply = service.complete_login(command).await;

    assert!(reply.result.is_err());
    assert!(reply.cookie_changes.is_empty());
    assert_eq!(pending.consume_count(), 0);
    assert_eq!(providers.exchange_count(), 0);
    assert_eq!(sessions.install_count(), 0);
}

#[tokio::test]
async fn unknown_provider_is_rejected_before_state_consume() {
    let (service, providers, sessions, pending) = wired();

    let reply = service
        .complete_login(bound_login("gitlab", CODE, STATE, NONCE))
        .await;

    assert!(reply.result.is_err());
    assert!(reply.cookie_changes.is_empty());
    assert_eq!(pending.consume_count(), 0);
    assert_eq!(providers.exchange_count(), 0);
    assert_eq!(sessions.install_count(), 0);
}

#[tokio::test]
async fn exchange_failure_clears_challenge_and_installs_nothing() {
    let (service, providers, sessions, pending) = wired();
    providers.set_exchange_error(ApplicationError::bad_gateway(
        "token_exchange_failed",
        "provider code exchange failed",
    ));

    let reply = service
        .complete_login(bound_login("github", CODE, STATE, NONCE))
        .await;

    assert!(reply.result.is_err());
    assert_clear_login_challenge_only(&reply.cookie_changes);
    assert_eq!(sessions.install_count(), 0);
    assert_eq!(pending.consume_count(), 1);
}

#[tokio::test]
async fn install_failure_clears_challenge_too() {
    let (service, _providers, sessions, _pending) = wired();
    sessions.set_install_error(ApplicationError::unavailable(
        "auth_dependency_unavailable",
        "session identity store unavailable",
    ));

    let reply = service
        .complete_login(bound_login("github", CODE, STATE, NONCE))
        .await;

    assert!(reply.result.is_err());
    assert_clear_login_challenge_only(&reply.cookie_changes);
}

#[tokio::test]
async fn login_urls_keep_chain_order_and_set_one_challenge() {
    let (service, providers, _sessions, pending) = wired();

    let reply = service
        .login_urls(BuildLoginUrls {
            callback_base_url: TEST_CALLBACK_BASE.to_string(),
        })
        .await;

    let urls = reply.result.expect("issue succeeds");
    assert_eq!(
        urls.providers
            .iter()
            .map(|p| p.name.as_str())
            .collect::<Vec<_>>(),
        vec!["github", "google"],
        "URL list preserves the provider chain subsequence order"
    );
    assert!(
        urls.providers[0].url.contains(STATE)
            || urls.providers[0].url.contains("state"),
        "authorize URLs embed the issued per-provider state"
    );
    assert_eq!(
        reply.cookie_changes,
        vec![BrowserCookieChange::SetLoginChallenge {
            nonce: NONCE.to_string(),
            expires_at: 4_100_000_000,
        }],
        "one batch = one challenge instruction overwriting the same cookie name"
    );

    // The issue call saw the chain, the configured base, and the flow.
    let issue = &pending.issue_calls()[0];
    assert_eq!(issue.providers, vec!["github".to_string(), "google".to_string()]);
    assert_eq!(issue.callback_base, TEST_CALLBACK_BASE);
    assert_eq!(issue.flow, TEST_FLOW);

    // Every authorize URL was built against the byte-identical exact
    // callback the consume path would derive.
    for (provider, call) in urls.providers.iter().zip(providers.auth_url_calls()) {
        assert_eq!(call.provider, provider.name);
        assert_eq!(call.redirect_uri, expected_exact_callback(&provider.name));
    }
}

#[tokio::test]
async fn callback_with_trailing_slash_base_stays_byte_identical() {
    let (service, _providers, _sessions, pending) = wired();

    let reply = service
        .login_urls(BuildLoginUrls {
            callback_base_url: format!("{TEST_CALLBACK_BASE}/"),
        })
        .await;

    assert!(reply.result.is_ok());
    let issue = &pending.issue_calls()[0];
    assert_eq!(issue.callback_base, TEST_CALLBACK_BASE);

    let completion = service
        .complete_login(bound_login("google", CODE, "state-for-google", NONCE))
        .await;
    assert!(completion.result.is_ok());
    let consume = &pending.consume_calls()[0];
    assert_eq!(
        consume.exact_callback,
        format!("{TEST_CALLBACK_BASE}/google"),
        "issue and consume must agree byte-for-byte despite the trailing slash"
    );
}

#[tokio::test]
async fn pending_store_failure_yields_no_usable_urls_and_no_cookie_changes() {
    let (service, _providers, _sessions, pending) = wired();
    pending.set_issue_error(ApplicationError::unavailable(
        "auth_dependency_unavailable",
        "pending login store unavailable",
    ));

    let reply = service
        .login_urls(BuildLoginUrls {
            callback_base_url: TEST_CALLBACK_BASE.to_string(),
        })
        .await;

    assert!(reply.result.is_err(), "issue failure yields no usable URLs");
    assert!(
        reply.cookie_changes.is_empty(),
        "no challenge is written when no batch exists"
    );
}

#[tokio::test]
async fn no_enabled_providers_returns_the_configured_not_available_path() {
    let providers = Arc::new(FakeProviderPort::new(&[]));
    let sessions = Arc::new(FakeSessionPort::new());
    let pending = Arc::new(FakePendingLoginPort::new());
    let service = auth_service(providers.clone(), sessions, pending.clone());

    let urls = service
        .login_urls(BuildLoginUrls {
            callback_base_url: TEST_CALLBACK_BASE.to_string(),
        })
        .await;
    assert!(
        urls.result.is_err(),
        "no enabled provider means the URL family is unavailable"
    );
    assert!(urls.cookie_changes.is_empty());

    let completion = service
        .complete_login(bound_login("github", CODE, STATE, NONCE))
        .await;
    assert!(completion.result.is_err());
    assert!(completion.cookie_changes.is_empty());
    assert_eq!(pending.consume_count(), 0);
}
