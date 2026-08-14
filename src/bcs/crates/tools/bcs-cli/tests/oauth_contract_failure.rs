//! Conformance test (failure path) for the bcs-cli `oauth` provider extension
//! point (Rule 25). Separate binary so its single `inventory::submit!` does not
//! collide with the success-path binary.
//!
//! Locks the PUBLIC contract that an internal overlay provider's
//! `OAuthError { message, auth_url }` propagates verbatim through
//! `try_get_oauth_headers`, and that `on_auth_required` fires with the
//! authorization URL before the error returns — the structured output relies
//! on `auth_url` to surface a browser-authorization URL to the caller.

use std::collections::HashMap;
use std::sync::Mutex;

use async_trait::async_trait;

use bcs_cli::oauth::{
    self, AuthRequiredCallback, OAuthError, OAuthHeaderProvider, OAuthHeaderProviderRegistration,
};

const AUTH_URL: &str = "https://idp.example.test/oauth/authorize?state=abc";

static FIRE: Mutex<Option<String>> = Mutex::new(None);

struct FakeNeedAuthProvider;

#[async_trait]
impl OAuthHeaderProvider for FakeNeedAuthProvider {
    async fn get_headers(
        &self,
        _client_id: String,
        _client_secret: String,
        on_auth_required: Option<AuthRequiredCallback>,
    ) -> Result<HashMap<String, String>, OAuthError> {
        if let Some(cb) = on_auth_required {
            cb(AUTH_URL);
        }
        Err(OAuthError {
            message: "OAuth2 authorization required".to_string(),
            auth_url: Some(AUTH_URL.to_string()),
        })
    }
}

static FAKE: FakeNeedAuthProvider = FakeNeedAuthProvider;

inventory::submit! {
    OAuthHeaderProviderRegistration { provider: &FAKE }
}

#[tokio::test]
async fn provider_failure_propagates_message_and_auth_url() {
    let err = oauth::try_get_oauth_headers(
        "ignored".to_string(),
        "ignored".to_string(),
        Some(Box::new(|url: &str| {
            if let Ok(mut guard) = FIRE.lock() {
                *guard = Some(url.to_string());
            }
        })),
    )
    .await
    .err()
    .expect("provider returning Err must propagate, not fall back to the stub");

    assert_eq!(err.message, "OAuth2 authorization required");
    assert_eq!(err.auth_url.as_deref(), Some(AUTH_URL));

    let fired = FIRE.lock().ok().and_then(|g| g.clone());
    assert_eq!(fired.as_deref(), Some(AUTH_URL));
}
