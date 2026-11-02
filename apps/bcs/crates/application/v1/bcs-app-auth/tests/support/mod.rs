//! Shared fixtures for the bcs-app-auth integration tests.
//!
//! Compiled once per integration test binary; each binary uses a
//! different subset of the fixtures, so unused items are expected.
#![allow(dead_code)]

pub mod fake_ports;
pub mod log_capture;

use std::sync::Arc;

use bcs_service_api::application::v1::{BrowserLoginBinding, CompleteOAuthLogin};
use bcs_service_api::port::oauth::{OAuthProviderPort, OAuthSessionPort, PendingOAuthLoginPort};

use fake_ports::{FakePendingLoginPort, FakeProviderPort, FakeSessionPort};

/// Configured flow namespace the V1 entry hands to the pending-login port.
pub const TEST_FLOW: &str = "v1";

/// Public callback base (delivery passes `{public_base_url}/callback`).
pub const TEST_CALLBACK_BASE: &str = "https://bcs.example.com/openapi/v1/auth/callback";

/// Configured same-origin success redirect path.
pub const TEST_SUCCESS_REDIRECT: &str = "/workbench";

/// The exact per-provider callback for the configured base (plan §8.4).
pub fn expected_exact_callback(provider: &str) -> String {
    format!("{TEST_CALLBACK_BASE}/{provider}")
}

/// A real, required browser binding — never `Option` or a default-empty
/// nonce (plan Task 9 step 4: test doubles must provide a real binding).
pub fn bound_login(provider: &str, code: &str, state: &str, nonce: &str) -> CompleteOAuthLogin {
    CompleteOAuthLogin {
        provider: provider.to_string(),
        code: Some(code.to_string()),
        auth_code: None,
        state: state.to_string(),
        callback_base_url: TEST_CALLBACK_BASE.to_string(),
        browser_binding: BrowserLoginBinding {
            nonce: nonce.to_string(),
        },
    }
}

/// The application service wired with the shared recording fakes and the
/// configured same-origin redirect + flow namespace. The same `Arc`s stay
/// readable by the test for call-recording assertions.
#[allow(clippy::needless_pass_by_value)]
pub fn auth_service(
    providers: Arc<FakeProviderPort>,
    sessions: Arc<FakeSessionPort>,
    pending: Arc<FakePendingLoginPort>,
) -> bcs_app_auth::AuthApplicationService {
    bcs_app_auth::AuthApplicationService::new(
        providers as Arc<dyn OAuthProviderPort>,
        sessions as Arc<dyn OAuthSessionPort>,
        pending as Arc<dyn PendingOAuthLoginPort>,
        TEST_SUCCESS_REDIRECT.to_string(),
        TEST_FLOW.to_string(),
    )
}
