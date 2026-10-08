//! Composition-root bridges for the V1 secure-auth ports (Task 10).
//!
//! Each adapter wraps the corresponding plugin-API surface into the
//! service-api [`bcs_service_api::port::oauth`] ports consumed by the
//! V1 SecureAuthService facade (`AuthApplicationService`).
//!
//! # Translation only
//!
//! The bridges are field-by-field translation: plugin-API types ↔
//! service-api types, error taxonomy mapping, no implementation
//! selection, no env lookup, no ad hoc SQL. The composition root (Task
//! 12) constructs each bridge from already-resolved providers / engine
//! / store; this file declares the bridge structs and the `impl` blocks
//! only. We do NOT pull secrets, select implementations, or construct
//! the plugin chain here — those concerns live in Task 12's wiring
//! code (the same place that already wires `AuthPluginChain`).
//!
//! # Error taxonomy mapping
//!
//! | Plugin-API error variant            | Bridge mapping                              |
//! |-------------------------------------|---------------------------------------------|
//! | `bcs_auth_api::LoginStateError::Invalid`       | `ApplicationError::invalid("login_state_invalid", …)` |
//! | `bcs_auth_api::LoginStateError::Unavailable`   | `ApplicationError::Unavailable`              |
//! | `bcs_auth_api::SessionStoreError::Unavailable` | `ApplicationError::Unavailable`              |
//! | `bcs_auth_api::SessionStoreError::CorruptRecord` | `ApplicationError::Internal`               |
//! | `bcs_auth_api::SessionAuthError::Invalid`      | `ApplicationError::Unauthenticated`          |
//! | `bcs_auth_api::SessionAuthError::Forbidden`    | `ApplicationError::Forbidden`                |
//! | `bcs_auth_api::SessionAuthError::Unavailable`  | `ApplicationError::Unavailable`              |
//! | `bcs_auth_api::SessionAuthError::Internal`     | `ApplicationError::Internal`                 |
//! | `bcs_auth_api::SessionAuthError::Conflict`     | `ApplicationError::Conflict`                 |
//! | `bcs_auth_api::OAuthError::TokenExchangeFailed`| `ApplicationError::BadGateway`               |
//! | `bcs_auth_api::OAuthError::UserInfoFailed`     | `ApplicationError::BadGateway`               |
//! | `bcs_auth_api::OAuthError::InvalidState`       | `ApplicationError::InvalidInput`             |
//! | `bcs_auth_api::OAuthError::ProviderNotFound`   | `ApplicationError::NotFound`                 |
//! | `bcs_auth_api::OAuthError::ConfigError`        | `ApplicationError::Internal`                 |
//!
//! Both `SessionRevoke::Revoked` and `SessionRevoke::NotCurrent` are
//! idempotent SUCCESS at the [`OAuthSessionPort`] layer: the bridge
//! surfaces `Ok(())` for either, mapping an actual store-level
//! `Err(SessionAuthError::*)` (signature-verify failure / unavailable
//! / internal) to the corresponding `ApplicationError`.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_auth_api::{
    AuthSessionIdentityPort, LoginBatch, LoginStateError, OAuthError, OAuthProvider,
    PendingOAuthLoginStore, SessionAuthError, SessionRevoke, SessionStoreError,
};
use bcs_auth_oauth::OAuthSessionEngine;
use bcs_service_api::application::v1::ApplicationError;
use bcs_service_api::port::oauth::{
    BrowserSession, ExternalLoginIdentity, OAuthProviderPort, OAuthSessionPort,
    PendingLoginBatch, PendingOAuthLoginPort,
};

// ─────────────────────────── error mappers ─────────────────────────

fn map_login_state_error(err: LoginStateError) -> ApplicationError {
    match err {
        LoginStateError::Invalid => ApplicationError::invalid(
            "login_state_invalid",
            "pending-login binding mismatch (spec §8.4)",
        ),
        LoginStateError::Unavailable => ApplicationError::unavailable("login_state_store_unavailable"),
    }
}

fn map_session_store_error(err: SessionStoreError) -> ApplicationError {
    match err {
        SessionStoreError::Unavailable => ApplicationError::unavailable("session_store_unavailable"),
        SessionStoreError::CorruptRecord => ApplicationError::internal("session_store_corrupt"),
    }
}

fn map_session_auth_error(err: SessionAuthError) -> ApplicationError {
    match err {
        SessionAuthError::Invalid => ApplicationError::Unauthenticated,
        SessionAuthError::Forbidden => {
            ApplicationError::Forbidden("session not allowed for this env".to_string())
        }
        SessionAuthError::Unavailable => ApplicationError::unavailable("session_store_unavailable"),
        SessionAuthError::Internal => ApplicationError::internal("session_internal"),
        SessionAuthError::Conflict => ApplicationError::conflict(
            "session_cas_conflict",
            "session revision conflict during install",
        ),
    }
}

fn map_oauth_error(err: OAuthError) -> ApplicationError {
    match err {
        OAuthError::TokenExchangeFailed(msg) => ApplicationError::bad_gateway(
            "oauth_exchange_failed",
            format!("token exchange failed: {msg}"),
        ),
        OAuthError::UserInfoFailed(msg) => ApplicationError::bad_gateway(
            "oauth_userinfo_failed",
            format!("userinfo failed: {msg}"),
        ),
        OAuthError::InvalidState(msg) => {
            ApplicationError::invalid("oauth_invalid_state", format!("invalid state: {msg}"))
        }
        OAuthError::ProviderNotFound(name) => {
            ApplicationError::not_found("provider_not_found", format!("provider {name} not configured"))
        }
        OAuthError::ConfigError(msg) => ApplicationError::internal(format!("oauth config error: {msg}")),
    }
}

// ─────────────────────────── OAuthProviderPort ──────────────────────

/// Bridge: a set of plugin-API OAuth providers wrapped as the
/// service-api [`OAuthProviderPort`].
///
/// Dispatches each call to the matching provider by `provider` name.
/// The composition root (Task 12) injects the configured chain
/// (typically `build_oauth_provider` outcomes keyed by their `name`
/// per the resolved `provider.kind`); the bridge itself performs NO
/// selection, NO kind matching, NO env inspection. Unknown providers
/// surface as `ApplicationError::not_found("provider_not_found", …)`
/// — the application layer rejects unknown providers BEFORE the
/// pending-login consume step, so this branch should never fire
/// during a well-formed flow.
pub struct OAuthProviderPortAdapter {
    providers: HashMap<String, Arc<dyn OAuthProvider>>,
    /// Chain subsequence order; `names()` MUST preserve this exact
    /// order so the V1 facade's `login_urls` reply ordering matches
    /// the configured `enabled_providers` order.
    order: Vec<String>,
}

impl OAuthProviderPortAdapter {
    /// Construct from the chain's provider list. Order is preserved
    /// from the input `Vec` (the composition root's responsibility).
    pub fn new(providers: Vec<Arc<dyn OAuthProvider>>) -> Self {
        let mut map: HashMap<String, Arc<dyn OAuthProvider>> = HashMap::new();
        let mut order: Vec<String> = Vec::with_capacity(providers.len());
        for provider in providers {
            let name = provider.name().to_string();
            order.push(name.clone());
            map.insert(name, provider);
        }
        Self { providers: map, order }
    }

    /// Key providers by their CONFIGURED INSTANCE names (e.g. two `github`
    /// instances named `github-internal` / `github-partner`). Task 11
    /// fidelity: the legacy /auth/url listing and identity install used
    /// instance names, so two instances of the same kind stay distinct.
    pub fn with_instance_names(
        instance_names: Vec<String>,
        providers: Vec<Arc<dyn OAuthProvider>>,
    ) -> Self {
        assert_eq!(
            instance_names.len(),
            providers.len(),
            "instance names and providers must pair one-to-one"
        );
        let mut map: HashMap<String, Arc<dyn OAuthProvider>> = HashMap::new();
        let mut order: Vec<String> = Vec::with_capacity(instance_names.len());
        for (name, provider) in instance_names.into_iter().zip(providers.into_iter()) {
            order.push(name.clone());
            map.insert(name, provider);
        }
        Self { providers: map, order }
    }

    fn lookup(&self, provider: &str) -> Result<&Arc<dyn OAuthProvider>, ApplicationError> {
        self.providers.get(provider).ok_or_else(|| {
            ApplicationError::not_found(
                "provider_not_found",
                format!("provider '{provider}' not configured"),
            )
        })
    }
}

#[async_trait]
impl OAuthProviderPort for OAuthProviderPortAdapter {
    async fn names(&self) -> Vec<String> {
        self.order.clone()
    }

    async fn auth_url(
        &self,
        provider: &str,
        state: &str,
        redirect_uri: &str,
    ) -> Result<String, ApplicationError> {
        let p = self.lookup(provider)?;
        // The plugin-API `auth_url` is infallible (pure string building);
        // no error mapping needed.
        Ok(p.auth_url(state, redirect_uri))
    }

    async fn exchange_user(
        &self,
        provider: &str,
        code: &str,
        redirect_uri: &str,
    ) -> Result<ExternalLoginIdentity, ApplicationError> {
        let p = self.lookup(provider)?;
        let token = p
            .exchange_code(code, redirect_uri)
            .await
            .map_err(map_oauth_error)?;
        let user_info = p.get_user_info(&token).await.map_err(map_oauth_error)?;
        Ok(ExternalLoginIdentity {
            // The REQUESTED (instance) provider name — the install scope and
            // the identity row partition by the configured instance, not the
            // provider kind, so named instances stay distinct end-to-end.
            provider: provider.to_string(),
            external_user_id: user_info.id,
            name: user_info.name,
            // The legacy OAuth provider surface exposes email but the
            // V1 SecureAuthService does not authorize on email and does
            // not project it through `AuthUserInfo` — drop it.
            avatar: user_info.avatar,
        })
    }
}

// ─────────────────────────── OAuthSessionPort ───────────────────────

/// Bridge: a fully constructed Task 8 `OAuthSessionEngine` +
/// `AuthSessionIdentityPort` pair, wrapped as the service-api
/// [`OAuthSessionPort`].
///
/// `install_identity` first calls `AuthSessionIdentityPort::ensure_identity`
/// to obtain the internal `user_id` (the JWT subject), then calls
/// `OAuthSessionEngine::install` with a freshly formed `SessionScope`
/// bound to `(user_id, provider, env)` and the upstream display name.
/// The display name is dual-purpose: it is the `name` claim inside the
/// signed JWT and the write-through field on the identity row on first
/// create — see [`AuthSessionIdentityPort::ensure_identity`] for the
/// semantics.
///
/// `refresh` and `revoke` delegate directly to the engine. `revoke`
/// collapses `SessionRevoke::Revoked` / `SessionRevoke::NotCurrent`
/// into `Ok(())` per the spec's idempotent-logout contract.
pub struct OAuthSessionPortAdapter {
    engine: Arc<OAuthSessionEngine>,
    identities: Arc<dyn AuthSessionIdentityPort>,
    env: String,
}

impl OAuthSessionPortAdapter {
    pub fn new(
        engine: Arc<OAuthSessionEngine>,
        identities: Arc<dyn AuthSessionIdentityPort>,
        env: String,
    ) -> Self {
        Self { engine, identities, env }
    }
}

#[async_trait]
impl OAuthSessionPort for OAuthSessionPortAdapter {
    async fn install_identity(
        &self,
        identity: ExternalLoginIdentity,
        now: u64,
    ) -> Result<BrowserSession, ApplicationError> {
        // 1) Ensure the identity row exists (idempotent create-or-confirm).
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
            .map_err(map_session_store_error)?;

        // 2) Install a new session against the resolved `user_id`.
        let scope = bcs_auth_api::SessionScope {
            user_id,
            provider: identity.provider.clone(),
            env: self.env.clone(),
        };
        let issued = self
            .engine
            .install(scope, identity.name, now)
            .await
            .map_err(map_session_auth_error)?;
        Ok(BrowserSession {
            token: issued.token,
            expires_at: issued.expires_at,
        })
    }

    async fn refresh(&self, token: &str, now: u64) -> Result<BrowserSession, ApplicationError> {
        let issued = self
            .engine
            .refresh(token, now)
            .await
            .map_err(map_session_auth_error)?;
        Ok(BrowserSession {
            token: issued.token,
            expires_at: issued.expires_at,
        })
    }

    async fn revoke(&self, token: &str) -> Result<(), ApplicationError> {
        // Both store outcomes (Revoked / NotCurrent) collapse to idempotent
        // success. The engine's `revoke` returns SessionAuthError ONLY on
        // genuine failures (signature verify, unavailable, internal) —
        // the application layer surfaces those as `ApplicationError`, not
        // as the in-band `SessionRevoke` enum.
        let outcome = self
            .engine
            .revoke(token)
            .await
            .map_err(map_session_auth_error)?;
        match outcome {
            SessionRevoke::Revoked | SessionRevoke::NotCurrent => Ok(()),
        }
    }
}

// ───────────────────────── PendingOAuthLoginPort ───────────────────

/// Bridge: a Task 9 `PendingOAuthLoginStore` wrapped as the service-api
/// [`PendingOAuthLoginPort`]. Field-for-field translation only.
pub struct PendingOAuthLoginPortAdapter {
    store: Arc<dyn PendingOAuthLoginStore>,
}

impl PendingOAuthLoginPortAdapter {
    pub fn new(store: Arc<dyn PendingOAuthLoginStore>) -> Self {
        Self { store }
    }
}

#[async_trait]
impl PendingOAuthLoginPort for PendingOAuthLoginPortAdapter {
    async fn issue_batch(
        &self,
        providers: &[String],
        callback_base: &str,
        flow: &str,
        now: u64,
    ) -> Result<PendingLoginBatch, ApplicationError> {
        let batch: LoginBatch = self
            .store
            .issue_batch(providers, callback_base, flow, now)
            .await
            .map_err(map_login_state_error)?;
        Ok(PendingLoginBatch {
            browser_nonce: batch.browser_nonce,
            expires_at: batch.expires_at,
            provider_states: batch.provider_states,
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
        self.store
            .consume(state, browser_nonce, provider, exact_callback, flow, now)
            .await
            .map_err(map_login_state_error)
    }
}
