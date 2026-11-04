//! Composition root for the explicit `[api.auth]` V1 authentication chain
//! (V1 API auth plugin chain, plan Task 12).
//!
//! This module is the "bootstrap bridge" the spec §6 names: it owns the
//! registration inventory (`production_registrations`), the ordered
//! assembly (`assemble_v1_api_auth`, the plan's initialization order:
//! discover descriptors -> merged-schema validation -> resolve ONLY the
//! enabled sources' secrets -> construct the identity/session/provider
//! clients -> construct the application facade -> publish routes with the
//! composed verifier), and the two source verifiers that wrap the shared
//! secured primitives:
//!
//! - the Gateway source REUSES the existing signed-principal verifier and
//!   keeps its fixed provenance;
//! - the OAuth session sources wrap the ONE shared strict engine and pin
//!   `OAuthSessionCookie` provenance only when the verified provider
//!   matches the source's own registration name (spec §7.2 dispatch).
//!
//! N4 secret discipline is structural here: a chain without the Gateway
//! source never resolves the Gateway signing secret (the friend-auth-sync
//! resigner keeps its own, independent `[gateway_principal]` contract), and
//! no secret value ever reaches a Debug/Display/tracing surface — the
//! build context carries the secret PORT, and resolved material stays
//! transient inside a source's build (errors carry logical field paths).

use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

use async_trait::async_trait;
use axum::http::HeaderMap;
use bcs_api_http::v1::gateway_principal::{GatewayPrincipalTokenVerifier, GatewayPrincipalTrust};
use bcs_api_http::{
    CompositePrincipalVerifier, CredentialKind, PrincipalVerificationError, PrincipalVerifier,
    TrustedBrowserOrigins, VerificationAttempt, VerifiedRequestIdentity,
};
use bcs_auth_api::session::{AuthSessionIdentityPort, SessionAuthError, SessionSnapshot};
use bcs_auth_api::{BCS_SESSION_COOKIE, OAuthProvider};
use bcs_auth_oauth::OAuthSessionEngine;
use bcs_config_api::api_auth::ApiAuthConfig;
use bcs_service_api::application::v1::{AuthenticatedCaller, AuthenticatedUserIdentity, AuthService};
use bcs_service_api::port::secret::SecretAccessPort;

use crate::api_auth_provider_configs as provider_configs;
use crate::api_auth_registry::{
    ApiAuthBuildContext, ApiAuthCapabilities, ApiAuthSourceRegistration,
    ApiAuthSourceRegistrationFactory, OAuthSourceContext, validate_api_auth,
};

/// The explicit `[api.auth]` assembly result handed to the server
/// composition: the ordered composite verifier plus everything the mounted
/// surfaces need. `origins` is present exactly when the chain enables a
/// cookie-kind source (`TrustedBrowserOrigins::new` already rejected an
/// invalid list before anything publishes); note the Task 7 constructor
/// refuses an empty list, so "no cookie source" must stay `None` here
/// rather than an empty `TrustedBrowserOrigins`.
pub struct BuiltApiAuth {
    /// The ordered composite verifier (chain order), wrapped later by the
    /// composition root in the Stateful outer gate.
    pub verifier: Arc<dyn PrincipalVerifier>,
    /// The V1 auth application facade when the chain enables an OAuth
    /// source; `None` keeps `/user` served through the delivery projection.
    pub auth_service: Option<Arc<dyn AuthService>>,
    /// Trusted browser origins for the per-actual-source CSRF gate.
    pub origins: Option<TrustedBrowserOrigins>,
    /// The externally-addressable V1 auth base URL the facade serves
    /// (`api.auth.public_base_url`, normalized once at validation).
    pub public_base_url: Option<String>,
}

/// What the V1 auth router state should mount for the login family when an
/// explicit `[api.auth]` chain enabled an OAuth source. The compat path
/// (`None` selection) keeps the existing `[auth.oauth]`-derived mount.
pub struct ExplicitApiAuthFacade {
    pub auth_service: Arc<dyn AuthService>,
    pub public_base_url: String,
}

/// The server-level selection outcome: absent `[api.auth]` keeps the
/// compat, Gateway-only protected routes exactly as before (but still on
/// the secured shared primitives); a present section is fully assembled
/// and replaces the selection outright (spec §4.3).
pub enum ApiAuthSelection {
    /// No `[api.auth]`: legacy selection, changed in no way by this task.
    Compat,
    /// Explicit chain assembled from `production_registrations`.
    Explicit(Box<BuiltApiAuth>),
}

/// The registered production inventory (spec §4.2/§5): the code-owned
/// Gateway header source plus the supported OAuth provider instances, THEN
/// any registrations downstream crates submitted via `inventory::submit!`
/// (the same discovery pattern the other plugin factories use). The chain
/// still decides order and enablement; an external name that is illegal,
/// reserved, or collides with an existing registration stays startup-fatal
/// via `index_registrations` during assembly.
pub fn production_registrations() -> Vec<ApiAuthSourceRegistration> {
    let mut registrations = vec![
        ApiAuthSourceRegistration {
            name: "gateway".to_string(),
            capabilities: ApiAuthCapabilities {
                uses_browser_cookie: false,
                is_oauth_provider: false,
            },
            validate: provider_configs::validate_gateway_table,
            build: build_gateway_source,
        },
        ApiAuthSourceRegistration {
            name: "alipay".to_string(),
            capabilities: ApiAuthCapabilities {
                uses_browser_cookie: true,
                is_oauth_provider: true,
            },
            validate: |table| provider_configs::validate_alipay_table("alipay", table),
            build: build_oauth_session_source,
        },
        ApiAuthSourceRegistration {
            name: "github".to_string(),
            capabilities: ApiAuthCapabilities {
                uses_browser_cookie: true,
                is_oauth_provider: true,
            },
            validate: |table| provider_configs::validate_oauth_client_table("github", table),
            build: build_oauth_session_source,
        },
        ApiAuthSourceRegistration {
            name: "google".to_string(),
            capabilities: ApiAuthCapabilities {
                uses_browser_cookie: true,
                is_oauth_provider: true,
            },
            validate: |table| provider_configs::validate_oauth_client_table("google", table),
            build: build_oauth_session_source,
        },
        ApiAuthSourceRegistration {
            name: "wechat".to_string(),
            capabilities: ApiAuthCapabilities {
                uses_browser_cookie: true,
                is_oauth_provider: true,
            },
            validate: |table| provider_configs::validate_oauth_client_table("wechat", table),
            build: build_oauth_session_source,
        },
    ];
    registrations.extend(
        inventory::iter::<ApiAuthSourceRegistrationFactory>
            .into_iter()
            .map(|factory| (factory.0)()),
    );
    registrations
}

/// Gateway source build: REUSES the existing signed-principal verifier with
/// trust fields from the validated `[gateway]` table (spec §4.2
/// defaults included) and the signing key resolved from the source's OWN
/// logical secret reference. Provenance stays pinned inside the reused
/// verifier; nothing here can be influenced by request content.
fn build_gateway_source(
    ctx: ApiAuthBuildContext,
) -> Pin<Box<dyn Future<Output = Result<Arc<dyn PrincipalVerifier>, String>> + Send>> {
    Box::pin(async move {
        let params = provider_configs::gateway_source_params(&ctx.options)?;
        // The resolved signing material below exists transiently for this
        // construction only: it is never logged, embedded in an error, or
        // stored on the shared verifier.
        let signing_key = provider_configs::resolve_source_secret(
            &params.signing_key_secret,
            "api.auth.gateway.signing_key_secret",
            &ctx.secret_access,
        )
        .await?;
        let trust = GatewayPrincipalTrust::new(params.issuers, params.audience, params.key_id)
            .map_err(|_| {
                "api.auth.gateway: trust configuration is invalid (category: invalid_trust_configuration)"
                    .to_string()
            })?;
        let verifier = GatewayPrincipalTokenVerifier::new(signing_key.as_bytes(), trust)
            .map_err(|_| {
                "api.auth.gateway.signing_key_secret: resolved material is empty (category: blank_value)"
                    .to_string()
            })?;
        Ok(Arc::new(verifier) as Arc<dyn PrincipalVerifier>)
    })
}

/// OAuth session source build: wraps the ONE shared strict engine the
/// bootstrap constructed for the chain's OAuth sources. The verifier
/// carries no identity of its own (the dispatch test asserts the
/// request-local cache, not any process-shared state).
fn build_oauth_session_source(
    ctx: ApiAuthBuildContext,
) -> Pin<Box<dyn Future<Output = Result<Arc<dyn PrincipalVerifier>, String>> + Send>> {
    Box::pin(async move {
        let Some(oauth) = ctx.oauth.as_ref() else {
            return Err(format!(
                "api.auth.{}: OAuth session context unavailable (category: oauth_context_missing)",
                ctx.name
            ));
        };
        Ok(Arc::new(OAuthSessionSourceVerifier {
            source_name: ctx.name.clone(),
            context: Arc::clone(oauth),
        }) as Arc<dyn PrincipalVerifier>)
    })
}

/// Delivery verifier for ONE registered OAuth session source: the request
/// cookie is verified ONCE per request against the shared strict engine
/// (signature + store + revision + revocation), the verified provider is
/// checked against the chain allowlist, and the final provenance is pinned
/// only by the wrapper whose registration name equals the VERIFIED provider
/// (spec §7.2). Nothing about this object survives between requests.
pub struct OAuthSessionSourceVerifier {
    source_name: String,
    context: Arc<OAuthSourceContext>,
}

#[async_trait]
impl PrincipalVerifier for OAuthSessionSourceVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        let mut attempt = VerificationAttempt::new();
        self.verify_in_attempt(headers, &mut attempt).await
    }

    async fn verify_in_attempt(
        &self,
        headers: &HeaderMap,
        attempt: &mut VerificationAttempt,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        // ONE strict result per request: later OAuth positions replay the
        // cached outcome instead of re-running the engine (N5 budget).
        if let Some(cached) = attempt.cached_oauth_session() {
            return resolve_cached_position(&self.source_name, cached);
        }
        // Carrier hygiene (spec §7.1): no cookie is NotApplicable; a
        // blank/duplicated cookie is a malformed credential (401-side),
        // never a silent "missing".
        let token = match strict_session_cookie(headers) {
            Ok(None) => return Err(PrincipalVerificationError::Missing),
            Err(()) => {
                let error = PrincipalVerificationError::Invalid(
                    "OAuth session cookie is malformed or presented more than once".into(),
                );
                attempt.store_oauth_session(Err(error.clone()));
                return Err(error);
            }
            Ok(Some(token)) => token,
        };
        let snapshot = match self.context.engine.verify(&token, unix_now()).await {
            Ok(snapshot) => snapshot,
            // Dependency outage (store unreachable): the strict 503-side
            // category, never a fallback to a later source.
            Err(SessionAuthError::Unavailable) => {
                attempt.store_oauth_session(Err(PrincipalVerificationError::DependencyUnavailable));
                return Err(PrincipalVerificationError::DependencyUnavailable);
            }
            // Foreign-env token: not a policy-refused valid identity, an
            // invalid credential for this deployment (401-side).
            Err(SessionAuthError::Forbidden) => {
                let error = PrincipalVerificationError::Invalid(
                    "OAuth session credential is not accepted in this environment".into(),
                );
                attempt.store_oauth_session(Err(error.clone()));
                return Err(error);
            }
            Err(SessionAuthError::Invalid) => {
                let error = PrincipalVerificationError::Invalid(
                    "OAuth session credential was rejected".into(),
                );
                attempt.store_oauth_session(Err(error.clone()));
                return Err(error);
            }
            Err(SessionAuthError::Internal) | Err(SessionAuthError::Conflict) => {
                let error = PrincipalVerificationError::Internal(
                    "OAuth session verification failed internally".into(),
                );
                attempt.store_oauth_session(Err(error.clone()));
                return Err(error);
            }
        };
        // A strictly verified credential for an unlisted source does not
        // participate in this chain. Cache Missing as well: later OAuth
        // positions must neither repeat the SELECT nor block a Gateway.
        let provider = snapshot.scope.provider.clone();
        if !self.context.allowed_providers.iter().any(|allowed| allowed == &provider) {
            attempt.store_oauth_session(Err(PrincipalVerificationError::Missing));
            return Err(PrincipalVerificationError::Missing);
        }
        // Pin the CURRENT verified provider in the request-local cache: a
        // LATER position must see exactly this provenance and may only
        // confirm it, never rewrite it when skipping.
        let identity = VerifiedRequestIdentity::pin(
            caller_from_snapshot(&snapshot),
            provider.clone(),
            CredentialKind::OAuthSessionCookie,
        ).with_avatar(snapshot.avatar.clone());
        attempt.store_oauth_session(Ok(identity.clone()));
        if provider != self.source_name {
            // Valid, allowed, but a different source's credential: continue
            // to the matching OAuth position.
            return Err(PrincipalVerificationError::Missing);
        }
        Ok(identity)
    }
}

/// Cached-result replay for a later OAuth position: only the position whose
/// registration name equals the cached (verified, provider-equal) source
/// succeeds; an error result replays unchanged; a foreign provider stays
/// Missing. The cache itself is never mutated here.
fn resolve_cached_position(
    source_name: &str,
    cached: Result<VerifiedRequestIdentity, PrincipalVerificationError>,
) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
    match cached {
        Ok(identity) => {
            if identity.authentication_context.source == source_name {
                Ok(identity)
            } else {
                Err(PrincipalVerificationError::Missing)
            }
        }
        Err(error) => Err(error),
    }
}

/// Human caller projection from the trusted snapshot (spec §7.3):
/// `username` prefers a trusted non-empty name and falls back to the
/// internal identity ID as the stable display identifier; `display_name`
/// passes through when provided; the tenant stays default, never invented
/// from the environment, headers, or third-party account fields.
fn caller_from_snapshot(snapshot: &SessionSnapshot) -> AuthenticatedCaller {
    let username = if snapshot.username.trim().is_empty() {
        snapshot.scope.user_id.clone()
    } else {
        snapshot.username.clone()
    };
    AuthenticatedCaller {
        tenant: None,
        user: Some(AuthenticatedUserIdentity {
            id: snapshot.scope.user_id.clone(),
            username,
            display_name: snapshot.display_name.clone(),
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

/// Extract the `bcs_session` cookie under the carried adapter obligations:
/// `Ok(None)` = no cookie at all; `Err(())` = the cookie exists but its
/// value is empty or appears more than once (a malformed credential, never
/// an empty token handed to the engine); `Ok(Some(token))` is the single
/// non-empty value.
fn strict_session_cookie(headers: &HeaderMap) -> Result<Option<String>, ()> {
    let mut found: Option<String> = None;
    for value in headers.get_all(axum::http::header::COOKIE) {
        let raw = value.to_str().map_err(|_| ())?;
        for pair in raw.split(';') {
            let mut parts = pair.splitn(2, '=');
            let name = match parts.next() {
                Some(name) => name.trim(),
                None => continue,
            };
            if name != BCS_SESSION_COOKIE {
                continue;
            }
            let value = match parts.next() {
                Some(value) => value.trim(),
                None => return Err(()),
            };
            if value.is_empty() || found.replace(value.to_string()).is_some() {
                return Err(());
            }
        }
    }
    Ok(found)
}

/// Wall-clock seconds at this delivery boundary (the engine still takes
/// the injected `now` parameter; this is the single read for requests).
fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or_default()
}

/// Assemble the explicit `[api.auth]` chain from a validated-ready config
/// and the registration inventory. This runs the whole initialization
/// order; every failure is a startup-fatal, field-path-only error.
///
/// This source-only entry and production [`select_v1_authentication`] share
/// the same assembly; production also checks any legacy session signing key.
/// Assembly tests may inject fake descriptors. It never invents a
/// source that is not in BOTH the chain and the registrations, and it
/// resolves a secret only after the merged-schema validation accepted the
/// configuration.
pub async fn assemble_v1_api_auth(
    config: &ApiAuthConfig,
    registrations: &[ApiAuthSourceRegistration],
    identities: Arc<dyn AuthSessionIdentityPort>,
    env: String,
    secret_access: Arc<dyn SecretAccessPort>,
) -> Result<BuiltApiAuth, String> {
    assemble_with_legacy_session_key(config, registrations, identities, env, secret_access, None).await
}

/// The production path additionally checks the shared-cookie signing key
/// against any configured legacy session reader/writer. The resolved key is
/// read once and never returned or included in diagnostics.
async fn assemble_with_legacy_session_key(
    config: &ApiAuthConfig,
    registrations: &[ApiAuthSourceRegistration],
    identities: Arc<dyn AuthSessionIdentityPort>,
    env: String,
    secret_access: Arc<dyn SecretAccessPort>,
    legacy_session_key: Option<&str>,
) -> Result<BuiltApiAuth, String> {
    // Discover + merged-schema validation (Task 6's contract, unchanged).
    let validated = validate_api_auth(config, registrations)?;

    // Origin composition happens BEFORE anything is published: an invalid
    // list is a startup failure, and a cookieless chain composes none.
    let origins = if validated.trusted_browser_origins.is_empty() {
        None
    } else {
        Some(TrustedBrowserOrigins::new(validated.trusted_browser_origins.clone())?)
    };

    // The shared strict OAuth engine exists exactly when an OAuth source is
    // enabled; `idle_secs` arrives AS-IS from Task 6's one conversion, and
    // the allowlist is the chain's OAuth subsequence order.
    let oauth_context: Option<Arc<OAuthSourceContext>> = match &validated.oauth {
        Some(oauth) => {
            let signing_key = provider_configs::resolve_source_secret(
                &oauth.session_signing_key_secret,
                "api.auth.session_signing_key_secret",
                &secret_access,
            )
            .await?;
            if legacy_session_key.is_some_and(|legacy| legacy != signing_key) {
                return Err("api.auth.session_signing_key_secret: must resolve to the same key as auth.oauth.jwt_secret while legacy OAuth sessions are enabled (category: session_signing_key_mismatch)".into());
            }
            let engine = Arc::new(OAuthSessionEngine::new(
                bcs_jwt::OAuthSessionJwt::new(&signing_key),
                Arc::clone(&identities),
                env.clone(),
                oauth.idle_secs,
            ));
            let allowed_providers = validated
                .chain
                .iter()
                .filter(|name| {
                    registrations
                        .iter()
                        .find(|registration| registration.name == **name)
                        .is_some_and(|registration| registration.capabilities.is_oauth_provider)
                })
                .cloned()
                .collect();
            Some(Arc::new(OAuthSourceContext {
                engine,
                allowed_providers,
            }))
        }
        None => None,
    };

    // Source construction in CHAIN order: each registration's build
    // resolves only its own secrets, from the already-validated options.
    // The same pass constructs the OAuth provider CLIENTS (also only for
    // enabled instances), referencing the same shared handles.
    let mut verifiers: Vec<Arc<dyn PrincipalVerifier>> = Vec::new();
    let mut providers: Vec<(String, Arc<dyn OAuthProvider>)> = Vec::new();
    for name in &validated.chain {
        let registration = registrations
            .iter()
            .find(|registration| &registration.name == name)
            .expect("validate_api_auth already rejected unregistered chain sources");
        let options = validated
            .plugin_options
            .get(name)
            .cloned()
            .unwrap_or(serde_json::Value::Null);
        let context = ApiAuthBuildContext {
            name: name.clone(),
            options: options.clone(),
            secret_access: Arc::clone(&secret_access),
            sessions: Arc::clone(&identities),
            oauth: oauth_context.clone(),
        };
        verifiers.push((registration.build)(context).await?);
        if registration.capabilities.is_oauth_provider {
            let schema = match name.as_str() {
                "alipay" => provider_configs::OAuthTableSchema::AlipayKeys,
                _ => provider_configs::OAuthTableSchema::ClientCredentials,
            };
            providers.push((
                name.clone(),
                provider_configs::build_enabled_oauth_client(name, &options, schema, &secret_access)
                    .await?,
            ));
        }
    }

    // Application facade: constructed only when an OAuth source is enabled
    // — the login family's provider bridge over the SAME engine, so the
    // sessions the facade installs are exactly the ones the chain accepts.
    let validated_oauth = validated.oauth.clone();
    let auth_service = match (validated_oauth.as_ref(), oauth_context) {
        (Some(_), Some(oauth_context)) => {
            let provider_bridge = Arc::new(crate::api_auth_ports::ConfiguredOAuthProviderBridge::new(
                providers,
            ));
            let session_bridge = Arc::new(crate::api_auth_ports::StrictSessionPortBridge::new(
                Arc::clone(&oauth_context.engine),
                Arc::clone(&identities),
                env,
            ).with_refresh_provider_allowlist(oauth_context.allowed_providers.clone()));
            let pending = Arc::new(crate::api_auth_ports::PendingLoginStoreBridge::new(Arc::new(
                bcs_auth_oauth::MemoryPendingOAuthLoginStore::new(),
            )));
            let success_redirect_path = validated_oauth
                .as_ref()
                .expect("matched against Some")
                .success_redirect_path
                .clone();
            Some(
                Arc::new(bcs_app_auth::AuthApplicationService::new(
                    provider_bridge,
                    session_bridge,
                    pending,
                    success_redirect_path,
                    "v1".to_string(),
                )) as Arc<dyn AuthService>,
            )
        }
        _ => None,
    };

    Ok(BuiltApiAuth {
        verifier: Arc::new(CompositePrincipalVerifier::new(verifiers)),
        auth_service,
        origins,
        public_base_url: validated_oauth.map(|oauth| oauth.public_base_url),
    })
}

/// Select and (when the explicit section is present) fully assemble the V1
/// authentication sources for a server startup. `Ok(ApiAuthSelection::Compat)`
/// means the compat path keeps the unchanged Gateway-only selection;
/// `Ok(Explicit(..))` hands the caller the assembled chain. The identity
/// ports come from whichever store the deployment built (memory or DB) —
/// sessions and identity rows stay where they already live.
pub async fn select_v1_authentication(
    config: &crate::BcsConfig,
    identities: Option<Arc<dyn AuthSessionIdentityPort>>,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<ApiAuthSelection> {
    let Some(auth) = config.api.auth.as_ref() else {
        return Ok(ApiAuthSelection::Compat);
    };
    let Some(identities) = identities else {
        return Err(crate::BcsError::InvalidConfig(
            "[api.auth] requires the strict session identity store, which is unavailable in this deployment"
                .to_string(),
        ));
    };
    // Match the legacy OAuth identity namespace, including the local fallback.
    let env = crate::config_loader::Environment::resolve().as_str().to_string();
    use secrecy::ExposeSecret;
    let legacy_session_key = config.auth.oauth.as_ref().and_then(|oauth| {
        let key = oauth.jwt_secret.as_ref()?.expose_secret().as_str();
        let reader_enabled = config.auth.chain.iter().any(|name| name == "oauth_session");
        let writer_enabled = !oauth.providers.is_empty() && !key.is_empty();
        (reader_enabled || writer_enabled).then_some(key)
    });
    let built = assemble_with_legacy_session_key(
        auth,
        &production_registrations(),
        identities,
        env,
        secret_access,
        legacy_session_key,
    )
    .await
    .map_err(crate::BcsError::InvalidConfig)?;
    Ok(ApiAuthSelection::Explicit(Box::new(built)))
}

/// Wrap a final composite in the SAME production Stateful outer gate the
/// server composition mounts (principal revalidation against the Bot
/// registry, `LegacyBrowserBoundary` refusal) so integration fixtures
/// exercise the real delivery chain without re-implementing the wrapper
/// (V1 API auth plan, Task 13 acceptance fixtures).
#[cfg(any(test, feature = "test-utils"))]
pub fn stateful_verifier_for_tests(
    inner: Arc<dyn PrincipalVerifier>,
    registry: Arc<dyn bcs_service_api::core::BotRegistryCoreService>,
) -> Arc<dyn PrincipalVerifier> {
    Arc::new(crate::principal_state::StatefulPrincipalVerifier { inner, registry })
}

/// The production native-browser principal adapter (`/collaboration/user`),
/// exposed for integration fixtures so they mount the REAL legacy Human
/// chain wrapper instead of approximating it (V1 API auth plan, Task 13
/// N2 compatibility fixtures).
#[cfg(any(test, feature = "test-utils"))]
pub fn browser_verifier_for_tests(
    chain: Arc<bcs_auth_api::AuthPluginChain>,
) -> Arc<dyn PrincipalVerifier> {
    Arc::new(crate::browser_principal::BrowserPrincipalVerifier(chain))
}

#[cfg(test)]
mod tests;
