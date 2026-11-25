//! Login orchestration (plan Task 10 step 3, binding order verbatim):
//!
//! ```text
//! complete_login(command):
//!   validate enabled provider + required code
//!   pending.consume(state, browser_binding, provider, callback, configured flow)
//!   on binding failure: reply(error, no cookie changes)
//!   exchange_user -> install_identity (both error-propagating)
//!   reply always clears challenge after successful consume
//!   success additionally sets session + configured same-origin redirect
//! ```
//!
//! Logging discipline (plan §8.4 credential-log red line): every event
//! on these paths carries only stable categories and configuration
//! names — provider label, flow, and the error's sanitized `code()`.
//! Nonces, states, codes, provider URLs, and error messages never reach
//! `tracing` (a port-layer error message may quote third-party response
//! text, so only its category is logged).

use bcs_service_api::application::v1::error::ApplicationError;
use bcs_service_api::application::v1::{
    AuthFlowReply, AuthRedirectLocation, AuthProviderUrl, AuthProviderUrlList, BrowserCookieChange,
    BuildLoginUrls, CompleteOAuthLogin,
};

use crate::AuthApplicationService;

/// Current unix time in seconds (the ports take integer instants). A
/// pre-epoch clock reads as 0; the ports treat that as no lease.
pub(super) fn unix_now() -> u64 {
    let now = time::OffsetDateTime::now_utc().unix_timestamp();
    if now < 0 {
        0
    } else {
        now as u64
    }
}

/// Normalize the configured callback base the SAME way on the issue and
/// the consume side (plan §8.4 byte-identity rule): one shared join, no
/// side-specific re-normalization, so the exact per-provider callback
/// stored at issue time equals the one derived at consume time.
fn normalized_callback_base(raw: &str) -> Result<String, ApplicationError> {
    let base = raw.trim().trim_end_matches('/');
    if base.is_empty() {
        return Err(ApplicationError::invalid(
            "invalid_callback_base_url",
            "callback_base_url must not be empty",
        ));
    }
    Ok(base.to_string())
}

/// The exact per-provider callback — the single join used by both flows.
fn exact_callback(base: &str, provider: &str) -> String {
    format!("{base}/{provider}")
}

/// A flow failure that changes no cookies.
fn reply_error<T>(error: ApplicationError) -> AuthFlowReply<T> {
    AuthFlowReply {
        result: Err(error),
        cookie_changes: Vec::new(),
    }
}

/// Issue the browser-bound authorize-URL batch: keeps the provider chain
/// (subsequence) order and returns exactly ONE `SetLoginChallenge`
/// instruction — the delivery encoder overwrites the same cookie name per
/// batch, and the nonce never appears in the URL list or any log.
pub(super) async fn login_urls_flow(
    service: &AuthApplicationService,
    request: BuildLoginUrls,
) -> AuthFlowReply<AuthProviderUrlList> {
    let names = service.providers.names();
    if names.is_empty() {
        // No enabled provider: the whole URL/callback family is
        // unavailable (Task 11 maps this to the configured error path).
        return reply_error(ApplicationError::not_found(
            "auth_not_configured",
            "OAuth is not configured",
        ));
    }
    let base = match normalized_callback_base(&request.callback_base_url) {
        Ok(base) => base,
        Err(error) => return reply_error(error),
    };

    let batch = match service
        .pending
        .issue_batch(&names, &base, service.flow(), unix_now())
        .await
    {
        Ok(batch) => batch,
        Err(error) => {
            tracing::warn!(
                category = error.code(),
                "pending login batch issue failed"
            );
            // No usable batch exists, so no challenge cookie is written.
            return reply_error(error);
        }
    };

    let mut providers = Vec::with_capacity(batch.provider_states.len());
    for (provider, state) in &batch.provider_states {
        let redirect_uri = exact_callback(&base, provider);
        match service.providers.auth_url(provider, state, &redirect_uri).await {
            Ok(url) => providers.push(AuthProviderUrl {
                name: provider.clone(),
                url,
            }),
            Err(error) => {
                tracing::warn!(
                    category = error.code(),
                    "provider auth url build failed"
                );
                // Fail closed: no usable URL family means no challenge.
                return reply_error(error);
            }
        }
    }

    tracing::debug!(
        provider_count = providers.len(),
        flow = service.flow(),
        "login urls issued"
    );
    AuthFlowReply {
        result: Ok(AuthProviderUrlList { providers }),
        cookie_changes: vec![BrowserCookieChange::SetLoginChallenge {
            nonce: batch.browser_nonce,
            expires_at: batch.expires_at,
        }],
    }
}

/// Complete the OAuth callback under the required browser binding, in the
/// binding order above.
pub(super) async fn complete_login_flow(
    service: &AuthApplicationService,
    command: CompleteOAuthLogin,
) -> AuthFlowReply<AuthRedirectLocation> {
    let names = service.providers.names();
    if names.is_empty() {
        return reply_error(ApplicationError::not_found(
            "auth_not_configured",
            "OAuth is not configured",
        ));
    }
    if !names.iter().any(|name| name == &command.provider) {
        tracing::warn!(category = "provider_not_enabled", "login rejected before state consume");
        return reply_error(ApplicationError::not_found(
            "provider_not_enabled",
            "provider is not enabled in the configured chain",
        ));
    }
    let Some(code) = command.code.clone().or_else(|| command.auth_code.clone()) else {
        tracing::warn!(category = "missing_code", "login rejected before state consume");
        return reply_error(ApplicationError::invalid(
            "missing_code",
            "missing code or auth_code",
        ));
    };

    let base = match normalized_callback_base(&command.callback_base_url) {
        Ok(base) => base,
        Err(error) => return reply_error(error),
    };
    let callback = exact_callback(&base, &command.provider);

    // Binding first (plan §8.4): a rejected consume keeps the initiating
    // browser's batch alive, so a binding failure changes NO cookies.
    if let Err(error) = service
        .pending
        .consume(
            &command.state,
            &command.browser_binding.nonce,
            &command.provider,
            &callback,
            service.flow(),
            unix_now(),
        )
        .await
    {
        tracing::warn!(category = error.code(), "browser-bound login state rejected");
        return AuthFlowReply {
            result: Err(error),
            cookie_changes: Vec::new(),
        };
    }

    // The batch is consumed: everything past this point always carries
    // the challenge cleanup.
    let cleanup = vec![BrowserCookieChange::ClearLoginChallenge];

    let identity = match service
        .providers
        .exchange_user(&command.provider, &code, &callback)
        .await
    {
        Ok(identity) => identity,
        Err(error) => {
            tracing::warn!(category = error.code(), "provider code exchange failed");
            return AuthFlowReply {
                result: Err(error),
                cookie_changes: cleanup,
            };
        }
    };

    let session = match service.sessions.install_identity(identity, unix_now()).await {
        Ok(session) => session,
        Err(error) => {
            tracing::warn!(category = error.code(), "session installation failed");
            return AuthFlowReply {
                result: Err(error),
                cookie_changes: cleanup,
            };
        }
    };

    tracing::info!(
        provider = %command.provider,
        "oauth login complete"
    );
    AuthFlowReply {
        result: Ok(AuthRedirectLocation {
            location: service.success_redirect_path().to_string(),
        }),
        cookie_changes: vec![
            BrowserCookieChange::ClearLoginChallenge,
            BrowserCookieChange::SetSession {
                token: session.token,
                expires_at: session.expires_at,
            },
        ],
    }
}
