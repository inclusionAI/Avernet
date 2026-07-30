//! Public OAuth extension point for the BCS CLI.
//!
//! The office-network OAuth SDK (`agent-client-sdk`, lib name `oauthsdk`) is
//! intentionally NOT part of the public workspace. This module is the
//! product-neutral extension point: it exposes an [`OAuthHeaderProvider`]
//! trait gathered at link time via `inventory`.
//!
//! - Public / OSS builds link no registration and fall back to a stub error.
//! - Internal builds (the ocb overlay `crates/tools/bcs-cli-oauth-ant`) submit
//!   an [`OAuthHeaderProviderRegistration`] that performs the real AgentPass
//!   OAuth2 flow.
//!
//! The free functions and types (`try_get_oauth_headers`, `set_structured_mode`,
//! `default_oauth_client_id`/`default_oauth_client_secret`, `OAuthError`,
//! `AuthRequiredCallback`) keep the SAME signatures as the legacy stub so
//! `main.rs` calls are unchanged.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};

use async_trait::async_trait;

/// OAuth error that carries the auth_url when available,
/// so callers can include it in structured output.
#[derive(Debug)]
pub struct OAuthError {
    pub message: String,
    pub auth_url: Option<String>,
}

impl std::fmt::Display for OAuthError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for OAuthError {}

static STRUCTURED_MODE: AtomicBool = AtomicBool::new(false);

/// Notify the office-flow module that structured (JSON) output is active.
///
/// Public builds ignore this; the internal Ant provider reads it to decide
/// whether to print human-facing prompts during the authorization wait.
pub fn set_structured_mode(enabled: bool) {
    STRUCTURED_MODE.store(enabled, Ordering::Relaxed);
}

/// Whether structured (JSON) output is active.
///
/// The office-flow provider (internal overlay crate) reads this to suppress
/// human-facing prompts that would pollute structured output.
pub fn is_structured_mode() -> bool {
    STRUCTURED_MODE.load(Ordering::Relaxed)
}

/// Hook invoked when OAuth2 "NeedAuth" is detected, before polling begins.
/// Gives the caller a chance to emit output (e.g. structured JSON) with the
/// auth_url immediately.
pub type AuthRequiredCallback = Box<dyn FnOnce(&str) + Send>;

/// Link-time-resolved OAuth2 header provider.
///
/// Implementations live outside the public workspace (e.g. the internal
/// `bcs-cli-oauth-ant` crate) and self-register via
/// [`OAuthHeaderProviderRegistration`].
#[async_trait]
pub trait OAuthHeaderProvider: Send + Sync {
    /// Compiled-in default OAuth2 client id (RFC 6749 §2.1 public client id).
    /// Public builds return `""`; internal builds return the real app id.
    fn default_client_id(&self) -> &'static str {
        ""
    }

    /// Compiled-in default OAuth2 client secret. Public builds return `""`.
    fn default_client_secret(&self) -> &'static str {
        ""
    }

    /// Obtain OAuth2 auth headers for the office-network gateway.
    ///
    /// `client_id`/`client_secret` are the resolved credentials (env override
    /// or the provider's compiled-in defaults); `on_auth_required` is invoked
    /// once with the authorization URL before the provider begins polling.
    async fn get_headers(
        &self,
        client_id: String,
        client_secret: String,
        on_auth_required: Option<AuthRequiredCallback>,
    ) -> Result<HashMap<String, String>, OAuthError>;
}

/// Self-registration wrapper gathered via `inventory` from an overlay crate.
///
/// An internal build submits:
///
/// ```ignore
/// static PROVIDER: AntOfficeOAuth = AntOfficeOAuth;
/// inventory::submit! {
///     bcs_cli::oauth::OAuthHeaderProviderRegistration {
///         provider: &PROVIDER,
///     }
/// }
/// ```
pub struct OAuthHeaderProviderRegistration {
    pub provider: &'static dyn OAuthHeaderProvider,
}

inventory::collect!(OAuthHeaderProviderRegistration);

/// The first provider linked into this binary, if any.
///
/// Public/OSS builds link no registration → `None` → [`get_oauth_headers`] falls
/// back to the public-build stub. An internal build links a registration → real
/// Ant OAuth flow.
fn registered_provider() -> Option<&'static dyn OAuthHeaderProvider> {
    inventory::iter::<OAuthHeaderProviderRegistration>
        .into_iter()
        .next()
        .map(|registration| registration.provider)
}

/// Default OAuth2 client id resolved from any linked provider, else `""`.
///
/// Signature unchanged from the legacy stub; `main.rs::resolve_oauth_credentials`
/// keeps calling `oauth::default_oauth_client_id()`.
pub fn default_oauth_client_id() -> &'static str {
    registered_provider()
        .map(|p| p.default_client_id())
        .unwrap_or("")
}

/// Default OAuth2 client secret resolved from any linked provider, else `""`.
pub fn default_oauth_client_secret() -> &'static str {
    registered_provider()
        .map(|p| p.default_client_secret())
        .unwrap_or("")
}

/// Get OAuth2 authentication headers via the linked provider, or the
/// public-build stub error when no provider is linked.
pub async fn get_oauth_headers(
    client_id: String,
    client_secret: String,
    on_auth_required: Option<AuthRequiredCallback>,
) -> Result<HashMap<String, String>, OAuthError> {
    if let Some(provider) = registered_provider() {
        provider
            .get_headers(client_id, client_secret, on_auth_required)
            .await
    } else {
        // Public build: no Ant SDK linked. The message is kept verbatim so
        // `classify_auth_error_message` and the structured-output contract stay
        // identical to the legacy stub.
        let _ = (client_id, client_secret, on_auth_required);
        Err(OAuthError {
            message:
                "CLI OAuth via the internal office-network SDK is not available in the public build"
                    .to_string(),
            auth_url: None,
        })
    }
}

pub async fn try_get_oauth_headers(
    client_id: String,
    client_secret: String,
    on_auth_required: Option<AuthRequiredCallback>,
) -> Result<HashMap<String, String>, OAuthError> {
    get_oauth_headers(client_id, client_secret, on_auth_required).await
}

#[cfg(test)]
mod tests {
    use super::*;

    // Public builds link no `OAuthHeaderProviderRegistration`, so the office
    // flow must fall back to the stub error. This locks the OSS behavior so an
    // accidental leak of the Ant SDK into the public crate shows up here.
    #[tokio::test]
    async fn public_build_falls_back_to_stub_without_provider() {
        let err = try_get_oauth_headers("ignored".to_string(), "ignored".to_string(), None)
            .await
            .unwrap_err();
        assert!(
            err.message
                .contains("is not available in the public build"),
            "unexpected stub message: {}",
            err.message
        );
        assert!(err.auth_url.is_none());
    }
}
