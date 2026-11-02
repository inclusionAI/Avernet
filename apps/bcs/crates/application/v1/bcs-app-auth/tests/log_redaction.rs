//! Application log-redaction tests (plan Task 10 step 4a): successful
//! login, state rejection, exchange failure, and session-store write
//! failure, each under a DEBUG-level capturing subscriber.
//!
//! The fakes deliberately carry sentinel credentials INTO their error
//! message text (a realistic third-party failure quotes request
//! input). The red line: the captured workflow logs must contain NONE
//! of the input nonce/state/code/token sentinels and never a full
//! authorize URL (it embeds the state) — while every failure path must
//! still record its STABLE CATEGORY text, so an empty log can never fake
//! a pass. The sentinel-carrying fakes are not weakened to dodge the
//! assertions.

mod support;

use std::sync::Arc;

use bcs_service_api::application::v1::{
    ApplicationError, BuildLoginUrls, PresentedSession, AuthService,
};

use support::fake_ports::{FakePendingLoginPort, FakeProviderPort, FakeSessionPort};
use support::log_capture::install_debug_capture;
use support::{auth_service, bound_login, TEST_CALLBACK_BASE};

const NONCE_SENTINEL: &str = "browser-nonce-fresh";
const STATE_SENTINEL: &str = "state-for-github";
const CODE_SENTINEL: &str = "oauth-code-sentinel-1";
const TOKEN_SENTINEL: &str = "session-token-sentinel-9";
/// The authorize-URL host the fake provider builds URLs under.
const AUTHORIZE_HOST: &str = "provider.test";

fn assert_no_sentinels(logs: &str, subject: &str) {
    assert!(
        !logs.contains(NONCE_SENTINEL),
        "{subject}: logs leaked the browser nonce"
    );
    assert!(
        !logs.contains(STATE_SENTINEL),
        "{subject}: logs leaked the one-time state"
    );
    assert!(
        !logs.contains(CODE_SENTINEL),
        "{subject}: logs leaked the authorization code"
    );
    assert!(
        !logs.contains(TOKEN_SENTINEL),
        "{subject}: logs leaked a session token"
    );
    assert!(
        !logs.contains(AUTHORIZE_HOST),
        "{subject}: logs leaked a full authorize URL (embeds the state)"
    );
}

#[tokio::test]
async fn successful_login_logs_categories_without_credentials() {
    let providers = Arc::new(FakeProviderPort::new(&["github"]));
    let sessions = Arc::new(FakeSessionPort::new());
    let pending = Arc::new(FakePendingLoginPort::new());
    let service = auth_service(providers, sessions, pending);

    let (buffer, _guard) = install_debug_capture();

    let urls = service
        .login_urls(BuildLoginUrls {
            callback_base_url: TEST_CALLBACK_BASE.to_string(),
        })
        .await;
    assert!(urls.result.is_ok());
    let reply = service
        .complete_login(bound_login("github", CODE_SENTINEL, STATE_SENTINEL, NONCE_SENTINEL))
        .await;
    assert!(reply.result.is_ok(), "flow under test must succeed");

    let logs = buffer.captured();
    assert_no_sentinels(&logs, "successful login");
    assert!(
        logs.contains("oauth login complete"),
        "the success path logs a stable completion mark (no vacuous capture)"
    );
}

#[tokio::test]
async fn state_rejection_logs_the_category_without_credentials() {
    let providers = Arc::new(FakeProviderPort::new(&["github"]));
    let sessions = Arc::new(FakeSessionPort::new());
    let pending = Arc::new(FakePendingLoginPort::new());
    pending.set_consume_error(ApplicationError::invalid(
        "invalid_login_state",
        "state bound to nonce NONCE-LEAK and provider github in callback https://other.example",
    ));
    let service = auth_service(providers, sessions, pending);

    let (buffer, _guard) = install_debug_capture();

    let reply = service
        .complete_login(bound_login("github", CODE_SENTINEL, "state-of-other-browser", "nonce-of-other-browser"))
        .await;
    assert!(reply.result.is_err(), "flow under test must fail");

    let logs = buffer.captured();
    assert_no_sentinels(&logs, "state rejection");
    assert!(
        logs.contains("browser-bound login state rejected"),
        "the rejection path logs its stable category"
    );
    assert!(
        !logs.contains("state-of-other-browser"),
        "the rejected state value itself never reaches the logs"
    );
}

#[tokio::test]
async fn exchange_failure_logs_the_category_but_never_the_sentinel_error_text() {
    let providers = Arc::new(FakeProviderPort::new(&["github"]));
    // The fake provider error DELIBERATELY embeds the code and a token
    // sentinel (third-party error bodies quote request input).
    providers.set_exchange_error(ApplicationError::bad_gateway(
        "token_exchange_failed",
        "provider rejected code=oauth-code-sentinel-1 access_token=session-token-sentinel-9",
    ));
    let sessions = Arc::new(FakeSessionPort::new());
    let pending = Arc::new(FakePendingLoginPort::new());
    let service = auth_service(providers, sessions, pending);

    let (buffer, _guard) = install_debug_capture();

    let reply = service
        .complete_login(bound_login("github", CODE_SENTINEL, STATE_SENTINEL, NONCE_SENTINEL))
        .await;
    assert!(reply.result.is_err(), "flow under test must fail");

    let logs = buffer.captured();
    assert_no_sentinels(&logs, "exchange failure");
    assert!(
        logs.contains("token_exchange_failed"),
        "the exchange failure logs its stable category (no vacuous capture)"
    );
}

#[tokio::test]
async fn session_store_write_failure_logs_the_category_without_credentials() {
    let providers = Arc::new(FakeProviderPort::new(&["github"]));
    let sessions = Arc::new(FakeSessionPort::new());
    // The fake store failure DELIBERATELY embeds a token sentinel.
    sessions.set_install_error(ApplicationError::unavailable(
        "auth_dependency_unavailable",
        "session write failed for issued session-token-sentinel-9 at shard db-b-7",
    ));
    let pending = Arc::new(FakePendingLoginPort::new());
    let service = auth_service(providers, sessions.clone(), pending);

    let (buffer, _guard) = install_debug_capture();

    let reply = service
        .complete_login(bound_login("github", CODE_SENTINEL, STATE_SENTINEL, NONCE_SENTINEL))
        .await;
    assert!(reply.result.is_err(), "flow under test must fail");

    let logs = buffer.captured();
    assert_no_sentinels(&logs, "session write failure");
    assert!(
        logs.contains("session installation failed"),
        "the write-failure path logs its stable category"
    );
    assert!(
        logs.contains("auth_dependency_unavailable"),
        "the dependency category is visible in the logs"
    );
    assert!(
        !logs.contains("db-b-7"),
        "underlying store topology never reaches the logs"
    );

    // The refresh port path must be just as category-only: present a
    // sentinel-bearing token and fail the store explicitly.
    sessions.set_refresh_error(ApplicationError::unavailable(
        "auth_dependency_unavailable",
        "session read failed for token session-token-sentinel-9",
    ));
    let refresh = service
        .refresh_session(PresentedSession {
            token: TOKEN_SENTINEL.to_string(),
        })
        .await;
    assert!(refresh.result.is_err());
    let later_logs = buffer.captured();
    assert_no_sentinels(&later_logs, "refresh failure");
    assert!(
        later_logs.contains("session refresh rejected"),
        "the refresh failure logs its stable category"
    );
}
