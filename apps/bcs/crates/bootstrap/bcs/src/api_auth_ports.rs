//! Bootstrap port bridges for the V1 auth application ports (V1 API
//! auth plugin chain, Task 10).
//!
//! The application crate `bcs-app-auth` consumes the transport-neutral
//! `bcs_service_api::port::oauth` ports. These bridges are the ONLY
//! production translations from the already-assembled plugin chain:
//!
//! - [`ConfiguredOAuthProviderBridge`] wraps the INJECTED
//!   `Arc<dyn bcs_auth_api::OAuthProvider>` set (the same concrete
//!   providers bootstrap builds via `build_oauth_provider`) and only
//!   translates `exchange_code`/`get_user_info` results;
//! - [`StrictSessionPortBridge`] calls the Task 8 strict
//!   [`OAuthSessionEngine`] plus `ensure_identity` on the shared
//!   `AuthSessionIdentityPort`;
//! - [`PendingLoginStoreBridge`] calls the Task 9
//!  [`bcs_auth_api::PendingOAuthLoginStore`].
//!
//! Bridge discipline (binding): these adapters choose NOTHING (the
//! providers/engine/store arrive pre-injected), read NO environment
//! variables, and write NO ad hoc SQL. Third-party and store error text
//! — which can quote request bodies or tokens — is discarded at the
//! boundary and lowered to stable [`ApplicationError`] categories; the
//! underlying error is never embedded in an `Internal` message, never
//! logged here.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_auth_api::OAuthProvider;
use bcs_auth_api::login_state::{LoginBatch, LoginStateError, PendingOAuthLoginStore};
use bcs_auth_api::session::{
    AuthSessionIdentityPort, IssuedSession, SessionAuthError, SessionScope,
};
use bcs_auth_api::OAuthError;
use bcs_service_api::application::v1::error::ApplicationError;
use bcs_service_api::port::oauth::{
    BrowserSession, ExternalLoginIdentity, OAuthProviderPort, OAuthSessionPort, PendingLoginBatch,
    PendingOAuthLoginPort,
};

/// Provider-chain bridge: one instance-name lookup + vocabulary
/// translation, no provider selection of its own. The bootstrap injects
/// `(instance_name, provider)` pairs: the INSTANCE name is the public
/// identity (the legacy entry's map key, the callback path segment, the
/// recorded `src`/display source), while the concrete provider behind it
/// only supplies `auth_url`/`exchange` behavior. Sorting/priority is
/// upstream in the injection; `names()` preserves the injection order.
pub struct ConfiguredOAuthProviderBridge {
    providers: Vec<(String, Arc<dyn OAuthProvider>)>,
}

impl ConfiguredOAuthProviderBridge {
    /// Wrap the already-built provider chain (injection order kept).
    pub fn new(providers: Vec<(String, Arc<dyn OAuthProvider>)>) -> Self {
        Self { providers }
    }

    fn find(&self, name: &str) -> Result<&Arc<dyn OAuthProvider>, ApplicationError> {
        self.providers
            .iter()
            .find(|(instance, _)| instance == name)
            .map(|(_, provider)| provider)
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "provider_not_enabled",
                    "provider is not enabled in the configured chain",
                )
            })
    }
}

#[async_trait]
impl OAuthProviderPort for ConfiguredOAuthProviderBridge {
    fn names(&self) -> Vec<String> {
        self.providers
            .iter()
            .map(|(instance, _)| instance.clone())
            .collect()
    }

    async fn auth_url(
        &self,
        provider: &str,
        state: &str,
        redirect_uri: &str,
    ) -> Result<String, ApplicationError> {
        let configured = self.find(provider)?;
        Ok(configured.auth_url(state, redirect_uri))
    }

    async fn exchange_user(
        &self,
        provider: &str,
        code: &str,
        redirect_uri: &str,
    ) -> Result<ExternalLoginIdentity, ApplicationError> {
        let configured = self.find(provider)?;
        // The OAuthError bodies are third-party response text — they can
        // echo request input — so only the stable category survives here.
        let token = configured
            .exchange_code(code, redirect_uri)
            .await
            .map_err(|error| provider_exchange_category(&error))?;
        let user = configured
            .get_user_info(&token)
            .await
            .map_err(|error| provider_userinfo_category(&error))?;
        Ok(ExternalLoginIdentity {
            provider: provider.to_string(),
            external_user_id: user.id,
            name: user.name,
            avatar: user.avatar,
        })
    }
}

/// Stable categories for `exchange_code` failures — no echoed providers.
fn provider_exchange_category(_error: &OAuthError) -> ApplicationError {
    ApplicationError::bad_gateway("token_exchange_failed", "provider code exchange failed")
}

/// Stable categories for `get_user_info` failures.
fn provider_userinfo_category(_error: &OAuthError) -> ApplicationError {
    ApplicationError::bad_gateway("userinfo_request_failed", "provider userinfo request failed")
}

/// Strict-session bridge over the Task 8 engine: a NEW login first
/// creates/confirms the identity via `ensure_identity` (never an
/// error-swallowing lookup), then installs through the engine's CAS
/// lifecycle. `env` is trusted injection-time configuration — never an
/// environment read.
///
/// The engine arrives as an `Arc` (Task 12): entries that share a chain
/// with the OAuth verifier sources hold ONE engine instance, so the login
/// lifecycle and the request-time strict verification stay consistent by
/// construction, with the state in the shared identity store.
pub struct StrictSessionPortBridge {
    engine: Arc<bcs_auth_oauth::OAuthSessionEngine>,
    identities: Arc<dyn AuthSessionIdentityPort>,
    env: String,
    // None preserves the independent legacy/compat refresh policy.
    refresh_provider_allowlist: Option<Vec<String>>,
}

impl StrictSessionPortBridge {
    pub fn new(
        engine: Arc<bcs_auth_oauth::OAuthSessionEngine>,
        identities: Arc<dyn AuthSessionIdentityPort>,
        env: String,
    ) -> Self {
        Self {
            engine,
            identities,
            env,
            refresh_provider_allowlist: None,
        }
    }

    /// Restrict explicit V1 refresh to its configured OAuth sources. An empty
    /// allowlist denies every refresh; it never means the legacy policy.
    /// Logout remains unrestricted so a disabled source's session can be revoked.
    pub fn with_refresh_provider_allowlist(mut self, providers: Vec<String>) -> Self {
        self.refresh_provider_allowlist = Some(providers);
        self
    }
}

/// Lower the closed session taxonomy onto application categories:
/// dependency outage -> `Unavailable`; denial -> `Unauthenticated`;
/// lost login race -> `Conflict` (409-side; the browser restarts its
/// batch). Display text is fixed category text only.
fn session_category(error: SessionAuthError) -> ApplicationError {
    match error {
        SessionAuthError::Invalid => ApplicationError::Unauthenticated,
        SessionAuthError::Forbidden => ApplicationError::forbidden_code(
            "session_forbidden",
            "session credential not accepted by this authentication policy",
        ),
        SessionAuthError::Unavailable => ApplicationError::unavailable(
            "auth_dependency_unavailable",
            "session identity store unavailable",
        ),
        SessionAuthError::Internal => ApplicationError::internal("session engine failure"),
        SessionAuthError::Conflict => ApplicationError::conflict(
            "login_session_conflict",
            "concurrent login completed for this identity; restart the login batch",
        ),
    }
}

fn browser_session(issued: IssuedSession) -> BrowserSession {
    BrowserSession {
        token: issued.token,
        expires_at: issued.expires_at,
    }
}

#[async_trait]
impl OAuthSessionPort for StrictSessionPortBridge {
    async fn install_identity(
        &self,
        identity: ExternalLoginIdentity,
        now: u64,
    ) -> Result<BrowserSession, ApplicationError> {
        let user_id = self
            .identities
            .ensure_identity(
                &identity.provider,
                &identity.external_user_id,
                identity.name.as_deref(),
                identity.avatar.as_deref(),
                &self.env,
            )
            .await
            .map_err(|_| {
                ApplicationError::unavailable(
                    "auth_dependency_unavailable",
                    "identity store unavailable",
                )
            })?;
        let scope = SessionScope {
            user_id,
            provider: identity.provider.clone(),
            env: self.env.clone(),
        };
        let issued = self
            .engine
            .install(scope, identity.name.clone(), now)
            .await
            .map_err(session_category)?;
        Ok(browser_session(issued))
    }

    async fn refresh(&self, token: &str, now: u64) -> Result<BrowserSession, ApplicationError> {
        let issued = self
            .engine
            .refresh_with_provider_check(token, now, |provider| {
                self.refresh_provider_allowlist.as_ref().map_or(true, |allowed| {
                    allowed.iter().any(|name| name == provider)
                })
            })
            .await
            .map_err(session_category)?;
        Ok(browser_session(issued))
    }

    async fn revoke(&self, token: &str) -> Result<(), ApplicationError> {
        // Both revoke outcomes (revoked / not-current) are port-level
        // idempotent successes; only genuine failures lower to errors.
        self.engine
            .revoke(token)
            .await
            .map_err(session_category)?;
        Ok(())
    }
}

/// Pending-login bridge over the Task 9 store: field-for-field batch
/// translation and the closed store taxonomy lowered to application
/// categories.
pub struct PendingLoginStoreBridge {
    store: Arc<dyn PendingOAuthLoginStore>,
}

impl PendingLoginStoreBridge {
    pub fn new(store: Arc<dyn PendingOAuthLoginStore>) -> Self {
        Self { store }
    }
}

fn pending_login_batch(batch: LoginBatch) -> PendingLoginBatch {
    PendingLoginBatch {
        browser_nonce: batch.browser_nonce,
        expires_at: batch.expires_at,
        provider_states: batch.provider_states,
    }
}

fn pending_login_category(error: LoginStateError) -> ApplicationError {
    match error {
        LoginStateError::Invalid => ApplicationError::invalid(
            "invalid_login_state",
            "browser-bound login state rejected",
        ),
        LoginStateError::Unavailable => ApplicationError::unavailable(
            "auth_dependency_unavailable",
            "pending login store unavailable",
        ),
    }
}

#[async_trait]
impl PendingOAuthLoginPort for PendingLoginStoreBridge {
    async fn issue_batch(
        &self,
        providers: &[String],
        callback_base: &str,
        flow: &str,
        now: u64,
    ) -> Result<PendingLoginBatch, ApplicationError> {
        self.store
            .issue_batch(providers, callback_base, flow, now)
            .await
            .map(pending_login_batch)
            .map_err(pending_login_category)
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
        self.store
            .consume(
                state,
                browser_nonce,
                provider,
                exact_callback,
                flow,
                now,
            )
            .await
            .map_err(pending_login_category)
    }
}

#[cfg(test)]
mod tests {
    //! Bridge vocabulary checks with the real Task 9 memory store and the
    //! real Task 8 engine; the only doubles are the injected provider
    //! plugin and the identity port (both are what bootstrap injects).

    use std::time::{SystemTime, UNIX_EPOCH};

    use async_trait::async_trait;
    use bcs_auth_api::oauth_types::{OAuthError, OAuthToken, ProviderUserInfo};
    use bcs_auth_api::session::SessionStoreError;
    use bcs_jwt::OAuthSessionJwt;
    use bcs_service_api::application::v1::error::ApplicationError;

    use super::*;

    fn unix_now() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or_default()
    }

    struct FailingProvider;

    fn exchange_error_with_sentinel_text() -> OAuthError {
        // Third-party error bodies quote request input; the bridge must
        // let this text die at the boundary.
        OAuthError::TokenExchangeFailed("rejected code=super-secret-code".to_string())
    }

    #[async_trait]
    impl OAuthProvider for FailingProvider {
        fn name(&self) -> &str {
            "github"
        }

        fn auth_url(&self, state: &str, _redirect_uri: &str) -> String {
            format!("https://provider.test/github/authorize?state={state}")
        }

        async fn exchange_code(&self, _code: &str, _redirect_uri: &str) -> Result<OAuthToken, OAuthError> {
            Err(exchange_error_with_sentinel_text())
        }

        async fn get_user_info(&self, _token: &OAuthToken) -> Result<ProviderUserInfo, OAuthError> {
            Err(OAuthError::UserInfoFailed("token=super-secret".to_string()))
        }
    }

    #[tokio::test]
    async fn provider_bridge_lowering_discards_sentinel_laden_text() {
        let bridge = ConfiguredOAuthProviderBridge::new(vec![
            ("github".to_string(), Arc::new(FailingProvider)),
        ]);

        assert_eq!(bridge.names(), vec!["github".to_string()]);

        let error = bridge
            .exchange_user("github", "code-x", "https://cb.example/github")
            .await
            .expect_err("the failing provider always fails");
        assert_eq!(error.code(), "token_exchange_failed");

        let lowered = format!("{error}");
        assert!(
            !lowered.contains("super-secret-code"),
            "the provider's raw error text must die at the bridge"
        );

        // Unknown provider is a stable not-found category.
        let error = bridge
            .auth_url("gitlab", "state-x", "https://cb.example/gitlab")
            .await
            .expect_err("gitlab is not injected");
        assert_eq!(error.code(), "provider_not_enabled");
    }

    struct FailingIdentityPort;

    #[async_trait]
    impl AuthSessionIdentityPort for FailingIdentityPort {
        async fn ensure_identity(
            &self,
            _provider: &str,
            _external_user_id: &str,
            _name: Option<&str>,
            _avatar: Option<&str>,
            _env: &str,
        ) -> Result<String, SessionStoreError> {
            Err(SessionStoreError::Unavailable)
        }

        async fn read_session_revision(
            &self,
            _scope: &SessionScope,
        ) -> Result<u64, SessionStoreError> {
            Err(SessionStoreError::Unavailable)
        }

        async fn get_session_by_hash(
            &self,
            _scope: &SessionScope,
            _hash: &str,
            _now: u64,
        ) -> Result<Option<bcs_auth_api::session::SessionSnapshot>, SessionStoreError> {
            Err(SessionStoreError::Unavailable)
        }

        async fn install_login_session(
            &self,
            _command: bcs_auth_api::session::InstallSession,
        ) -> Result<bcs_auth_api::session::SessionWrite, SessionStoreError> {
            Err(SessionStoreError::Unavailable)
        }

        async fn rotate_session(
            &self,
            _command: bcs_auth_api::session::RotateSession,
        ) -> Result<bcs_auth_api::session::SessionWrite, SessionStoreError> {
            Err(SessionStoreError::Unavailable)
        }

        async fn revoke_session(
            &self,
            _scope: &SessionScope,
            _session_id: &str,
        ) -> Result<bcs_auth_api::session::SessionRevoke, SessionStoreError> {
            Err(SessionStoreError::Unavailable)
        }
    }

    #[tokio::test]
    async fn session_bridge_lowering_maps_dependency_outage_to_unavailable() {
        let jwt = OAuthSessionJwt::new("bridge-test-secret");
        let engine = std::sync::Arc::new(bcs_auth_oauth::OAuthSessionEngine::new(
            jwt,
            Arc::new(FailingIdentityPort),
            "test-env".to_string(),
            1800,
        ));
        let bridge = StrictSessionPortBridge::new(
            engine,
            Arc::new(FailingIdentityPort),
            "test-env".to_string(),
        );

        let error = bridge
            .install_identity(
                ExternalLoginIdentity {
                    provider: "github".to_string(),
                    external_user_id: "ext-user-1".to_string(),
                    name: None,
                    avatar: None,
                },
                unix_now(),
            )
            .await
            .expect_err("the identity port is down");
        assert!(matches!(error, ApplicationError::Unavailable { .. }));
        assert_eq!(error.code(), "auth_dependency_unavailable");
    }

    #[tokio::test]
    async fn pending_bridge_lowering_keeps_to_two_categories() {
        let bridge = PendingLoginStoreBridge::new(Arc::new(
            bcs_auth_oauth::MemoryPendingOAuthLoginStore::new(),
        ));

        // Real store: a wrong-browser consume stays invalid.
        let batch = bridge
            .issue_batch(
                &["github".to_string()],
                "https://bcs.example.com/openapi/v1/auth/callback",
                "v1",
                100,
            )
            .await
            .expect("real issue succeeds");
        let (provider, state) = &batch.provider_states[0];
        let error = bridge
            .consume(
                state,
                "nonce-of-other-browser",
                provider,
                "https://bcs.example.com/openapi/v1/auth/callback/github",
                "v1",
                101,
            )
            .await
            .expect_err("wrong browser");
        assert_eq!(error.code(), "invalid_login_state");

        // ... and the initiating browser still completes byte-identically.
        bridge
            .consume(
                state,
                &batch.browser_nonce,
                provider,
                "https://bcs.example.com/openapi/v1/auth/callback/github",
                "v1",
                102,
            )
            .await
            .expect("initiating browser completes");
    }
}
