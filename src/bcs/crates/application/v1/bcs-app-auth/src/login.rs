//! Login flow orchestration: `login_urls` + `complete_login`.
//!
//! Both flows consume the pending-login port (spec §8.4 atomicity) and
//! the OAuth provider port. The cookie-change sequencing is the single
//! authoritative statement of the spec §8.4 contract:
//!
//! - `login_urls` success → `[SetLoginChallenge]`. Pending store
//!   failure (or per-provider URL building failure) → empty cookie
//!   changes (the challenge was never sent to the browser, so there is
//!   nothing to clear).
//! - `complete_login` binding failure (consume `Err`) → empty cookie
//!   changes (mismatch state must NOT consume any other batch, and the
//!   browser still owns its own challenge cookie — the spec's
//!   cross-browser defense).
//! - `complete_login` post-consume failure (exchange or install error)
//!   → `[ClearLoginChallenge]` (the matched consume is durable, so the
//!   matched callback's challenge is always cleared even when a later
//!   step fails).
//! - `complete_login` success → `[SetSession, ClearLoginChallenge]`.

use std::time::{SystemTime, UNIX_EPOCH};

use bcs_service_api::application::v1::{
    AuthFlowReply, AuthProviderUrl, AuthRedirect, BrowserCookieChange, CompleteOAuthLogin,
    BuildLoginUrls,
};
use bcs_service_api::application::v1::ApplicationError;

use crate::AuthApplicationService;

/// Read the current wall-clock unix timestamp (seconds). Bounds to 0
/// on clock-skew; the value is only used to drive pending-login expiry
/// and session install `now` arguments, both of which the bridges
/// re-validate against their own clock.
pub(crate) fn current_unix_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0)
}

/// Build the configured chain's login-URL list and issue a fresh
/// pending-login batch (spec §8.4 "选择登录方式").
///
/// Preserves the configured
/// [`AuthApplicationServiceConfig::enabled_providers`] subsequence
/// order in the reply so the UI renders the chain in the configured
/// order regardless of provider priority.
pub(crate) async fn login_urls(
    svc: &AuthApplicationService,
    command: BuildLoginUrls,
) -> AuthFlowReply<Vec<AuthProviderUrl>> {
    let config = svc.config();
    let providers = config.enabled_providers.clone();
    let now = current_unix_seconds();

    let batch = match svc
        .pending()
        .issue_batch(&providers, &command.callback_base_url, &config.flow, now)
        .await
    {
        Ok(batch) => batch,
        Err(error) => {
            return AuthFlowReply {
                result: Err(error),
                // Store failure = no challenge set on the browser; nothing
                // to clear (the spec's "已通过匹配并消费的 callback" clause does
                // NOT apply — no callback has matched yet).
                cookie_changes: Vec::new(),
            };
        }
    };

    let callback_base = command.callback_base_url.trim_end_matches('/');
    let mut urls = Vec::with_capacity(batch.provider_states.len());
    for (provider, state) in batch.provider_states.iter() {
        let redirect_uri = format!("{callback_base}/{provider}");
        match svc.provider().auth_url(provider, state, &redirect_uri).await {
            Ok(url) => urls.push(AuthProviderUrl {
                name: provider.clone(),
                url,
            }),
            Err(error) => {
                // URL building failed for one provider. The browser never
                // received the SetLoginChallenge from this reply, and the
                // pending state on the server will simply expire when no
                // callback arrives. The previous cookie (if any) will be
                // overwritten on the next login attempt.
                return AuthFlowReply {
                    result: Err(error),
                    cookie_changes: Vec::new(),
                };
            }
        }
    }

    AuthFlowReply {
        result: Ok(urls),
        cookie_changes: vec![BrowserCookieChange::SetLoginChallenge {
            nonce: batch.browser_nonce,
            expires_at: batch.expires_at,
        }],
    }
}

/// Complete a browser-bound OAuth callback (spec §8.4 "完成登录").
///
/// Orchestration order — see the module-level docs for the authoritative
/// cookie-change sequencing matrix.
pub(crate) async fn complete_login(
    svc: &AuthApplicationService,
    command: CompleteOAuthLogin,
) -> AuthFlowReply<AuthRedirect> {
    let config = svc.config();

    // Validate the enabled provider. The command's `code` is already a
    // required `String` (V1 callback surfaces a 400 from the delivery
    // adapter before constructing this command); no Option handling here.
    if !config
        .enabled_providers
        .iter()
        .any(|candidate| candidate == &command.provider)
    {
        return AuthFlowReply {
            result: Err(ApplicationError::invalid(
                "unknown_oauth_provider",
                format!("Provider '{}' is not enabled for this auth flow", command.provider),
            )),
            // Unknown provider = no consume attempt = no cookie changes.
            cookie_changes: Vec::new(),
        };
    }

    let callback_base = command.callback_base_url.trim_end_matches('/');
    let exact_callback = format!("{callback_base}/{}", command.provider);
    let now = current_unix_seconds();

    // Step 1: atomically consume the pending-login batch. A mismatched
    // binding MUST NOT consume the batch (cross-browser defense) and
    // burns nothing on the browser side — no cookie changes follow.
    if let Err(error) = svc
        .pending()
        .consume(
            &command.state,
            &command.browser_binding.nonce,
            &command.provider,
            &exact_callback,
            &config.flow,
            now,
        )
        .await
    {
        return AuthFlowReply {
            result: Err(error),
            cookie_changes: Vec::new(),
        };
    }

    // Step 2: exchange the auth code for an upstream external identity.
    // From this point forward, the consume is durable; any later
    // failure MUST clear the challenge per spec §8.4 ("已通过匹配并消费的
    // callback 无论成功失败都清除临时 Cookie").
    let identity = match svc
        .provider()
        .exchange_user(&command.provider, &command.code, &exact_callback)
        .await
    {
        Ok(identity) => identity,
        Err(error) => {
            return AuthFlowReply {
                result: Err(error),
                cookie_changes: vec![BrowserCookieChange::ClearLoginChallenge],
            };
        }
    };

    // Step 3: install the session (issue JWT + CAS store). Same §8.4
    // cleanup rule applies on failure.
    let session = match svc.session_port().install_identity(identity, now).await {
        Ok(session) => session,
        Err(error) => {
            return AuthFlowReply {
                result: Err(error),
                cookie_changes: vec![BrowserCookieChange::ClearLoginChallenge],
            };
        }
    };

    // Success: redirect to the configured post-login URL; set the session
    // cookie (with expiry) and clear the temporary login-challenge cookie.
    AuthFlowReply {
        result: Ok(AuthRedirect {
            location: config.post_login_redirect.clone(),
            // The old `set_cookie` field is preserved for V1 JSON shape
            // stability but carries no payload — all cookie mutations
            // are explicit `BrowserCookieChange`s on this reply.
            set_cookie: String::new(),
        }),
        cookie_changes: vec![
            BrowserCookieChange::SetSession {
                token: session.token,
                expires_at: session.expires_at,
            },
            BrowserCookieChange::ClearLoginChallenge,
        ],
    }
}
