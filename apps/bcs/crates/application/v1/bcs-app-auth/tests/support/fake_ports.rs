//! Central recording fakes for the V1 auth application ports (plan Task 10
//! step 5: every port has a fake that records calls).
//!
//! Each integration test binary compiles this module, but no single
//! binary exercises every configurable failure, so unused fixture
//! methods in a given binary are expected.
#![allow(dead_code)]
//!
//! These doubles implement the service-api application ports in-process —
//! the crate boundary forbids linking adapters or concrete plugins even in
//! tests. Each fake records every call it receives so the orchestration
//! tests can prove ordering and "not called on rejected paths" claims.
//!
//! Deliberately NOT redacted: the recorded fields hold sentinel
//! credentials in the redaction tests; these `Debug` impls exist for test
//! assertions only and are never handed to tracing.

use std::sync::Mutex;

use async_trait::async_trait;
use bcs_service_api::application::v1::ApplicationError;
use bcs_service_api::port::oauth::{
    BrowserSession, ExternalLoginIdentity, OAuthProviderPort, OAuthSessionPort, PendingLoginBatch,
    PendingOAuthLoginPort,
};

/// One recorded `OAuthProviderPort::auth_url` call.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct AuthUrlCall {
    pub provider: String,
    pub state: String,
    pub redirect_uri: String,
}

/// One recorded `OAuthProviderPort::exchange_user` call.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ExchangeCall {
    pub provider: String,
    pub code: String,
    pub redirect_uri: String,
}

/// One recorded `OAuthSessionPort::install_identity` call.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct InstallCall {
    pub provider: String,
    pub external_user_id: String,
    pub name: Option<String>,
    pub avatar: Option<String>,
    pub now: u64,
}

/// One recorded `OAuthSessionPort::refresh` call.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct RefreshCall {
    pub token: String,
    pub now: u64,
}

/// One recorded `PendingOAuthLoginPort::issue_batch` call.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct IssueCall {
    pub providers: Vec<String>,
    pub callback_base: String,
    pub flow: String,
    pub now: u64,
}

/// One recorded `PendingOAuthLoginPort::consume` call.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ConsumeCall {
    pub state: String,
    pub browser_nonce: String,
    pub provider: String,
    pub exact_callback: String,
    pub flow: String,
    pub now: u64,
}

/// Read a recording mutex without panicking interference.
fn snapshot<T: Clone>(calls: &Mutex<Vec<T>>) -> Vec<T> {
    calls.lock().expect("fake port mutex").clone()
}

/// Count of calls recorded so far.
fn count<T>(calls: &Mutex<Vec<T>>) -> usize {
    calls.lock().expect("fake port mutex").len()
}

/// Take a configured failure, if one is still set.
fn take_error(error: &Mutex<Option<ApplicationError>>) -> Option<ApplicationError> {
    error.lock().expect("fake port mutex").take()
}

/// Recording provider-chain double: hands out authorize URLs that embed
/// the issued state and a stable external identity on exchange.
pub struct FakeProviderPort {
    names: Vec<String>,
    auth_urls: Mutex<Vec<AuthUrlCall>>,
    exchanges: Mutex<Vec<ExchangeCall>>,
    auth_url_error: Mutex<Option<ApplicationError>>,
    exchange_error: Mutex<Option<ApplicationError>>,
}

impl FakeProviderPort {
    pub fn new(names: &[&str]) -> Self {
        Self {
            names: names.iter().map(|n| n.to_string()).collect(),
            auth_urls: Mutex::new(Vec::new()),
            exchanges: Mutex::new(Vec::new()),
            auth_url_error: Mutex::new(None),
            exchange_error: Mutex::new(None),
        }
    }

    pub fn set_auth_url_error(&self, error: ApplicationError) {
        *self.auth_url_error.lock().expect("fake port mutex") = Some(error);
    }

    pub fn set_exchange_error(&self, error: ApplicationError) {
        *self.exchange_error.lock().expect("fake port mutex") = Some(error);
    }

    pub fn auth_url_calls(&self) -> Vec<AuthUrlCall> {
        snapshot(&self.auth_urls)
    }

    pub fn exchange_calls(&self) -> Vec<ExchangeCall> {
        snapshot(&self.exchanges)
    }

    pub fn exchange_count(&self) -> usize {
        count(&self.exchanges)
    }
}

#[async_trait]
impl OAuthProviderPort for FakeProviderPort {
    fn names(&self) -> Vec<String> {
        self.names.clone()
    }

    async fn auth_url(
        &self,
        provider: &str,
        state: &str,
        redirect_uri: &str,
    ) -> Result<String, ApplicationError> {
        self.auth_urls.lock().expect("fake port mutex").push(AuthUrlCall {
            provider: provider.to_string(),
            state: state.to_string(),
            redirect_uri: redirect_uri.to_string(),
        });
        if let Some(error) = take_error(&self.auth_url_error) {
            return Err(error);
        }
        Ok(format!("https://provider.test/{provider}/authorize?state={state}"))
    }

    async fn exchange_user(
        &self,
        provider: &str,
        code: &str,
        redirect_uri: &str,
    ) -> Result<ExternalLoginIdentity, ApplicationError> {
        self.exchanges.lock().expect("fake port mutex").push(ExchangeCall {
            provider: provider.to_string(),
            code: code.to_string(),
            redirect_uri: redirect_uri.to_string(),
        });
        if let Some(error) = take_error(&self.exchange_error) {
            return Err(error);
        }
        Ok(ExternalLoginIdentity {
            provider: provider.to_string(),
            external_user_id: "ext-user-1".to_string(),
            name: Some("Test User".to_string()),
            avatar: Some("https://cdn.test/avatar.png".to_string()),
        })
    }
}

/// Recording strict-session double: issues deterministic browser
/// sessions, records install/refresh/revoke with their arguments.
pub struct FakeSessionPort {
    installs: Mutex<Vec<InstallCall>>,
    refreshes: Mutex<Vec<RefreshCall>>,
    revokes: Mutex<Vec<String>>,
    install_error: Mutex<Option<ApplicationError>>,
    refresh_error: Mutex<Option<ApplicationError>>,
    revoke_error: Mutex<Option<ApplicationError>>,
}

impl FakeSessionPort {
    pub fn new() -> Self {
        Self {
            installs: Mutex::new(Vec::new()),
            refreshes: Mutex::new(Vec::new()),
            revokes: Mutex::new(Vec::new()),
            install_error: Mutex::new(None),
            refresh_error: Mutex::new(None),
            revoke_error: Mutex::new(None),
        }
    }

    pub fn set_install_error(&self, error: ApplicationError) {
        *self.install_error.lock().expect("fake port mutex") = Some(error);
    }

    pub fn set_refresh_error(&self, error: ApplicationError) {
        *self.refresh_error.lock().expect("fake port mutex") = Some(error);
    }

    pub fn set_revoke_error(&self, error: ApplicationError) {
        *self.revoke_error.lock().expect("fake port mutex") = Some(error);
    }

    pub fn install_calls(&self) -> Vec<InstallCall> {
        snapshot(&self.installs)
    }

    pub fn install_count(&self) -> usize {
        count(&self.installs)
    }

    pub fn refresh_calls(&self) -> Vec<RefreshCall> {
        snapshot(&self.refreshes)
    }

    pub fn revoke_calls(&self) -> Vec<String> {
        snapshot(&self.revokes)
    }

    pub fn revoke_count(&self) -> usize {
        count(&self.revokes)
    }
}

#[async_trait]
impl OAuthSessionPort for FakeSessionPort {
    async fn install_identity(
        &self,
        identity: ExternalLoginIdentity,
        now: u64,
    ) -> Result<BrowserSession, ApplicationError> {
        self.installs.lock().expect("fake port mutex").push(InstallCall {
            provider: identity.provider,
            external_user_id: identity.external_user_id,
            name: identity.name,
            avatar: identity.avatar,
            now,
        });
        if let Some(error) = take_error(&self.install_error) {
            return Err(error);
        }
        Ok(BrowserSession {
            token: "issued-session-token".to_string(),
            expires_at: 4_100_000_000,
        })
    }

    async fn refresh(&self, token: &str, now: u64) -> Result<BrowserSession, ApplicationError> {
        self.refreshes.lock().expect("fake port mutex").push(RefreshCall {
            token: token.to_string(),
            now,
        });
        if let Some(error) = take_error(&self.refresh_error) {
            return Err(error);
        }
        Ok(BrowserSession {
            token: "rotated-session-token".to_string(),
            expires_at: 4_100_000_500,
        })
    }

    async fn revoke(&self, token: &str) -> Result<(), ApplicationError> {
        self.revokes.lock().expect("fake port mutex").push(token.to_string());
        if let Some(error) = take_error(&self.revoke_error) {
            return Err(error);
        }
        Ok(())
    }
}

/// Recording browser-bound pending-login double: mirrors the Task 9
/// contract by handing out one nonce plus one deterministic state per
/// requested provider, and by recording every consume attempt including
/// the exact callback bytes the orchestration derived.
pub struct FakePendingLoginPort {
    issues: Mutex<Vec<IssueCall>>,
    consumes: Mutex<Vec<ConsumeCall>>,
    issue_error: Mutex<Option<ApplicationError>>,
    consume_error: Mutex<Option<ApplicationError>>,
}

impl FakePendingLoginPort {
    pub fn new() -> Self {
        Self {
            issues: Mutex::new(Vec::new()),
            consumes: Mutex::new(Vec::new()),
            issue_error: Mutex::new(None),
            consume_error: Mutex::new(None),
        }
    }

    pub fn set_issue_error(&self, error: ApplicationError) {
        *self.issue_error.lock().expect("fake port mutex") = Some(error);
    }

    pub fn set_consume_error(&self, error: ApplicationError) {
        *self.consume_error.lock().expect("fake port mutex") = Some(error);
    }

    pub fn issue_calls(&self) -> Vec<IssueCall> {
        snapshot(&self.issues)
    }

    pub fn consume_calls(&self) -> Vec<ConsumeCall> {
        snapshot(&self.consumes)
    }

    pub fn consume_count(&self) -> usize {
        count(&self.consumes)
    }
}

#[async_trait]
impl PendingOAuthLoginPort for FakePendingLoginPort {
    async fn issue_batch(
        &self,
        providers: &[String],
        callback_base: &str,
        flow: &str,
        now: u64,
    ) -> Result<PendingLoginBatch, ApplicationError> {
        self.issues.lock().expect("fake port mutex").push(IssueCall {
            providers: providers.to_vec(),
            callback_base: callback_base.to_string(),
            flow: flow.to_string(),
            now,
        });
        if let Some(error) = take_error(&self.issue_error) {
            return Err(error);
        }
        let provider_states = providers
            .iter()
            .map(|provider| (provider.clone(), format!("state-for-{provider}")))
            .collect();
        Ok(PendingLoginBatch {
            browser_nonce: "browser-nonce-fresh".to_string(),
            expires_at: 4_100_000_000,
            provider_states,
        })
    }

    async fn consume(
        &self,
        state: &str,
        browser_nonce: &str,
        provider: &str,
        exact_callback: &str,
        flow: &str,
        now: u64,
    ) -> Result<(), ApplicationError> {
        self.consumes.lock().expect("fake port mutex").push(ConsumeCall {
            state: state.to_string(),
            browser_nonce: browser_nonce.to_string(),
            provider: provider.to_string(),
            exact_callback: exact_callback.to_string(),
            flow: flow.to_string(),
            now,
        });
        if let Some(error) = take_error(&self.consume_error) {
            return Err(error);
        }
        Ok(())
    }
}
