//! Central conformance drills for the auth application ports (plan
//! Task 10 step 5): every port method exercised through the
//! orchestration with the shared recording fakes, asserting the
//! recorded arguments and — on rejected paths — that the NEXT port in
//! the chain was never touched.

mod support;

use std::sync::Arc;

use bcs_service_api::application::v1::{BuildLoginUrls, PresentedSession, AuthService};

use support::fake_ports::{FakePendingLoginPort, FakeProviderPort, FakeSessionPort};
use support::{auth_service, bound_login, expected_exact_callback, TEST_CALLBACK_BASE, TEST_FLOW};

#[tokio::test]
async fn every_flow_method_reaches_every_port_with_the_expected_arguments() {
    let providers = Arc::new(FakeProviderPort::new(&["github", "google"]));
    let sessions = Arc::new(FakeSessionPort::new());
    let pending = Arc::new(FakePendingLoginPort::new());
    let service = auth_service(providers.clone(), sessions.clone(), pending.clone());

    // -- login_urls: issue_batch + one auth_url per chain entry --------
    let urls = service
        .login_urls(BuildLoginUrls {
            callback_base_url: TEST_CALLBACK_BASE.to_string(),
        })
        .await;
    assert!(urls.result.is_ok());

    let issues = pending.issue_calls();
    assert_eq!(issues.len(), 1);
    assert_eq!(issues[0].providers, vec!["github".to_string(), "google".to_string()]);
    assert_eq!(issues[0].callback_base, TEST_CALLBACK_BASE);
    assert_eq!(issues[0].flow, TEST_FLOW);

    let auth_urls = providers.auth_url_calls();
    assert_eq!(auth_urls.len(), 2);
    assert_eq!(auth_urls[0].provider, "github");
    assert_eq!(auth_urls[0].state, "state-for-github");
    assert_eq!(auth_urls[0].redirect_uri, expected_exact_callback("github"));
    assert_eq!(auth_urls[1].provider, "google");
    assert_eq!(auth_urls[1].redirect_uri, expected_exact_callback("google"));

    // -- complete_login: consume -> exchange -> install, in recording --
    let reply = service
        .complete_login(bound_login("github", "code-77", "state-for-github", "browser-nonce-fresh"))
        .await;
    assert!(reply.result.is_ok());

    let consumes = pending.consume_calls();
    assert_eq!(consumes.len(), 1);
    assert_eq!(consumes[0].state, "state-for-github");
    assert_eq!(consumes[0].browser_nonce, "browser-nonce-fresh");
    assert_eq!(consumes[0].provider, "github");
    assert_eq!(consumes[0].exact_callback, expected_exact_callback("github"));
    assert_eq!(consumes[0].flow, TEST_FLOW);
    assert!(consumes[0].now > 0, "the configured clock feeds the TTL");

    let exchanges = providers.exchange_calls();
    assert_eq!(exchanges.len(), 1);
    assert_eq!(exchanges[0].code, "code-77");
    assert_eq!(exchanges[0].redirect_uri, expected_exact_callback("github"));

    let installs = sessions.install_calls();
    assert_eq!(installs.len(), 1);
    assert_eq!(installs[0].provider, "github");
    assert_eq!(installs[0].external_user_id, "ext-user-1");
    assert_eq!(installs[0].name.as_deref(), Some("Test User"));
    assert!(installs[0].now > 0);

    // -- refresh: session port with the presented token --------------
    let refreshed = service
        .refresh_session(PresentedSession {
            token: "session-1".to_string(),
        })
        .await;
    assert!(refreshed.result.is_ok());
    let refreshes = sessions.refresh_calls();
    assert_eq!(refreshes.len(), 1);
    assert_eq!(refreshes[0].token, "session-1");
    assert!(refreshes[0].now > 0);

    // -- logout: revoke only when a session is presented --------------
    let closed = service
        .logout(Some(PresentedSession {
            token: "session-1".to_string(),
        }))
        .await;
    assert!(closed.result.is_ok());
    assert_eq!(sessions.revoke_calls(), vec!["session-1".to_string()]);

    let guest = service.logout(None).await;
    assert!(guest.result.is_ok());
    assert_eq!(
        sessions.revoke_count(),
        1,
        "the cookie-less idempotent exit never calls revoke"
    );
}

#[tokio::test]
async fn no_rejected_path_ever_touches_the_next_port_downstream() {
    // Binding failure: exchange AND install stay untouched.
    let providers = Arc::new(FakeProviderPort::new(&["github"]));
    let sessions = Arc::new(FakeSessionPort::new());
    let pending = Arc::new(FakePendingLoginPort::new());
    pending.set_consume_error(bcs_service_api::application::v1::ApplicationError::invalid(
        "invalid_login_state",
        "bound to another browser",
    ));
    let service = auth_service(providers.clone(), sessions.clone(), pending.clone());
    let rejected = service
        .complete_login(bound_login("github", "code-77", "state-for-github", "other-browser"))
        .await;
    assert!(rejected.result.is_err());
    assert_eq!(providers.exchange_count(), 0);
    assert_eq!(sessions.install_count(), 0);

    // Exchange failure (fresh wires): install stays untouched.
    let providers = Arc::new(FakeProviderPort::new(&["github"]));
    providers.set_exchange_error(bcs_service_api::application::v1::ApplicationError::bad_gateway(
        "token_exchange_failed",
        "provider code exchange failed",
    ));
    let service = auth_service(providers, sessions.clone(), pending);
    let rejected = service
        .complete_login(bound_login("github", "code-77", "state-for-github", "browser-nonce-fresh"))
        .await;
    assert!(rejected.result.is_err());
    assert_eq!(sessions.install_count(), 0);
}
