//! Strict caller-identity extraction conformance (P2-A first slice). The
//! extraction runs through the real `AuthPluginChain` with test plugins; the
//! runtime材料 invariant (非序列化、Debug 脱敏) comes from the service-api
//! contract itself, so these tests lock the adapter boundary only:
//! same-authentication pairing, trusted-window enforcement, and the closed
//! failure taxonomy with no display-data fallback.

use std::sync::Arc;

use bcs_auth_api::{AuthError, AuthPlugin, AuthPluginChain, AuthPrincipal};
use std::sync::Mutex as _;
use bcs_http::state::{CallerIngressExtractionError, ChainUserIdentityPort};
use axum::http::HeaderMap;

const NOW: i64 = 1_780_000_000_000;

enum StubResult {
    Miss,
    Principal(AuthPrincipal),
    Failed,
}

struct StubPlugin {
    can: bool,
    result: StubResult,
}

impl StubPlugin {
    fn miss() -> Self {
        Self { can: true, result: StubResult::Miss }
    }
}

#[async_trait::async_trait]
impl AuthPlugin for StubPlugin {
    fn name(&self) -> &'static str {
        "stub-ingress-plugin"
    }

    fn can_authenticate(&self, headers: &HeaderMap) -> bool {
        let _ = headers;
        self.can
    }

    async fn authenticate(
        &self,
        _headers: &HeaderMap,
    ) -> Result<Option<AuthPrincipal>, AuthError> {
        match &self.result {
            StubResult::Miss => Ok(None),
            StubResult::Principal(principal) => Ok(Some(principal.clone())),
            StubResult::Failed => Err(AuthError::InvalidToken("stub hard failure".to_string())),
        }
    }

    fn priority(&self) -> u8 {
        100
    }
}

fn human_principal(staff_no: &str, user_name: Option<&str>, expires: Option<u64>) -> AuthPrincipal {
    AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: user_name.map(str::to_string),
        human_expires_at_ms: expires,
        ..Default::default()
    }
}

#[tokio::test]
async fn strict_chain_produces_pair_from_same_authentication() {
    let chain = Arc::new(AuthPluginChain::new(vec![Box::new(StubPlugin {
        can: true,
        result: StubResult::Principal(human_principal("12345678", Some("Alice"), Some(NOW as u64 + 300_000))),
    })]));
    let port = ChainUserIdentityPort::new(chain);

    let request = port
        .extract_caller_request(&HeaderMap::new(), NOW)
        .await
        .expect("ok");
    // Same authentication: display sender matches principal identity data.
    assert_eq!(request.sender.staff_no.as_deref(), Some("12345678"));
    assert_eq!(request.sender.nick_name.as_deref(), Some("Alice"));
    // The ingress carries the trusted window, not "now + something".
    assert_eq!(request.ingress.authenticated_at_ms(), NOW);
    assert_eq!(request.ingress.expires_at_ms(), NOW + 300_000);
    assert!(!request.ingress.is_expired(NOW));
    assert!(request.ingress.is_expired(NOW + 300_000));
    // The trusted ingress itself never reveals the staff number; the paired
    // `sender` is the display projection routes already consume and is held
    // to the ordinary display-data rules, not the runtime-only redaction.
    let debug = format!("{:?}", request.ingress);
    assert!(debug.contains("<redacted>"), "staff number must be redacted: {debug}");
    assert!(!debug.contains("12345678"), "ingress leaked staff number: {debug}");
}

#[tokio::test]
async fn no_chain_match_is_unsupported_not_synthesized() {
    let port = ChainUserIdentityPort::new(Arc::new(AuthPluginChain::new(vec![Box::new(
        StubPlugin::miss(),
    )])));
    let error = port
        .extract_caller_request(&HeaderMap::new(), NOW)
        .await
        .unwrap_err();
    assert_eq!(error, CallerIngressExtractionError::Unsupported);
}

#[tokio::test]
async fn missing_trusted_window_is_unsupported_even_for_authenticated_humans() {
    let chain = Arc::new(AuthPluginChain::new(vec![Box::new(StubPlugin {
        can: true,
        result: StubResult::Principal(human_principal("12345678", Some("Alice"), None)),
    })]));
    let port = ChainUserIdentityPort::new(chain);
    // Display-only identity (no session expiry) cannot become caller material.
    let error = port
        .extract_caller_request(&HeaderMap::new(), NOW)
        .await
        .unwrap_err();
    assert_eq!(error, CallerIngressExtractionError::Unsupported);
}

#[tokio::test]
async fn machine_principals_and_auth_failures_reject_strictly() {
    let mut machine = human_principal("12345678", None, Some(NOW as u64 + 60_000));
    machine.bot_uuid = Some("bot-1".to_string());
    let port = ChainUserIdentityPort::new(Arc::new(AuthPluginChain::new(vec![Box::new(
        StubPlugin { can: true, result: StubResult::Principal(machine) },
    )])));
    assert_eq!(
        port.extract_caller_request(&HeaderMap::new(), NOW).await.unwrap_err(),
        CallerIngressExtractionError::Unsupported
    );

    // A hard auth failure from the chain is AuthenticationFailed, not a
    // display-only pass.
    let failing = StubPlugin { can: true, result: StubResult::Failed };
    let port = ChainUserIdentityPort::new(Arc::new(AuthPluginChain::new(vec![Box::new(
        failing,
    )])));
    assert_eq!(
        port.extract_caller_request(&HeaderMap::new(), NOW).await.unwrap_err(),
        CallerIngressExtractionError::AuthenticationFailed
    );

    // An authenticated human WITHOUT a user_id is not callable source material
    // — display-only synthetic caller identity is forbidden.
    let mut nameless = human_principal("12345678", Some("Alice"), Some(NOW as u64 + 60_000));
    nameless.user_id = None;
    let port = ChainUserIdentityPort::new(Arc::new(AuthPluginChain::new(vec![Box::new(
        StubPlugin { can: true, result: StubResult::Principal(nameless) },
    )])));
    assert_eq!(
        port.extract_caller_request(&HeaderMap::new(), NOW).await.unwrap_err(),
        CallerIngressExtractionError::Unsupported
    );
}

#[tokio::test]
async fn trusted_window_expiry_is_checked_against_the_caller_clock() {
    let chain = Arc::new(AuthPluginChain::new(vec![Box::new(StubPlugin {
        can: true,
        result: StubResult::Principal(human_principal("12345678", Some("Alice"), Some(1_780_000_000_100))),
    })]));
    let port = ChainUserIdentityPort::new(chain);
    // 1500s ahead of principal: trusted window not yet expired.
    assert!(port
        .extract_caller_request(&HeaderMap::new(), NOW)
        .await
        .is_ok());
    // Past the trusted window: Expired.
    let expired = port
        .extract_caller_request(&HeaderMap::new(), NOW + 300_000)
        .await
        .unwrap_err();
    assert_eq!(expired, CallerIngressExtractionError::Expired);
}