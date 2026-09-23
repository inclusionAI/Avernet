//! Composition root for the TWO OAuth auth entrypoints (Task 11, spec §8.4
//! "新旧同步使用相同浏览器绑定原语、不同 flow namespace").
//!
//! Both the V1 entrypoint (`bcs-api-http`) and the legacy entrypoint
//! (`bcs-http` oauth routes) consume the SAME `AuthApplicationService`
//! machinery over the SAME provider / session / pending-login ports. They
//! differ ONLY by the configured [`bcs_app_auth::AuthApplicationServiceConfig`]
//! (flow namespace + post-login redirect), so the legacy entry cannot be a
//! bypass for writing attacker identity into the shared `bcs_session`.
//!
//! [`OAuthProviderFactoryRegistration`] extends `build_oauth_provider`'s
//! closed provider-kind set: integration tests register a network-free mock
//! through the SAME construction path (inventory, like the auth-plugin
//! factory hook).

use std::collections::HashMap;
use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use axum::http::HeaderMap;

use bcs_api_http::{
    AuthenticationContext, CredentialKind, PrincipalVerificationError, PrincipalVerifier,
    TrustedBrowserOrigins, VerifiedRequestIdentity, VerificationAttempt,
};
use bcs_service_api::application::v1::{AuthenticatedCaller, AuthenticatedUserIdentity};
use bcs_service_api::port::secret::SecretAccessPort;

use crate::api_auth_provider_configs;
use crate::api_auth_registry::{
    ApiAuthBuildContext, ApiAuthSourceRegistration, OAuthSourceContext, validate_api_auth,
};

use bcs_app_auth::{AuthApplicationService, AuthApplicationServiceConfig};
use bcs_auth_api::{OAuthConfig, OAuthProvider, PendingOAuthLoginStore};
use bcs_service_api::application::v1::AuthService;
use bcs_service_api::port::oauth::{OAuthProviderPort, OAuthSessionPort, PendingOAuthLoginPort};

use crate::api_auth_ports::{
    OAuthProviderPortAdapter, OAuthSessionPortAdapter, PendingOAuthLoginPortAdapter,
};

/// Flow namespace of the LEGACY (pre-V1) `/auth/*` entrypoint. The
/// pending-login store isolates batches by flow, so a V1 callback can never
/// be replayed against a legacy batch or vice versa (spec §8.4).
pub const FLOW_LEGACY: &str = "legacy";

/// Flow namespace of the versioned V1 auth entrypoint.
pub const FLOW_V1: &str = "v1";

/// Two entrypoints over one secure mechanism.
pub struct OAuthEntryServices {
    /// Legacy `/auth/*` entrypoint flow.
    pub legacy: Arc<dyn AuthService>,
    /// Versioned `/openapi/v1/auth/*` entrypoint flow.
    pub v1: Arc<dyn AuthService>,
}

/// Build both entrypoint services from the same port adapters.
///
/// * `chain_providers` — the chain's configured provider instances, in the
///   reply order the login-URL list must preserve.
/// * `pending` — the browser-bound pending-login store (any impl; the flow
///   namespace distinguishes the two entrypoints inside it).
pub fn build_oauth_entry_services(
    chain_providers: Vec<(String, Arc<dyn OAuthProvider>)>,
    engine: Arc<bcs_auth_oauth::OAuthSessionEngine>,
    identities: Arc<dyn bcs_auth_api::AuthSessionIdentityPort>,
    pending: Arc<dyn PendingOAuthLoginStore>,
    config: &OAuthConfig,
) -> OAuthEntryServices {
    let (instance_names, provider_impls): (Vec<String>, Vec<Arc<dyn OAuthProvider>>) =
        chain_providers.into_iter().unzip();
    let provider_port: Arc<dyn OAuthProviderPort> = Arc::new(OAuthProviderPortAdapter::with_instance_names(
        instance_names.clone(),
        provider_impls,
    ));
    let session_port: Arc<dyn OAuthSessionPort> = Arc::new(OAuthSessionPortAdapter::new(
        engine,
        identities,
        config.env.clone(),
    ));
    let pending_port: Arc<dyn PendingOAuthLoginPort> =
        Arc::new(PendingOAuthLoginPortAdapter::new(pending));
    let make_config = |flow: &str| AuthApplicationServiceConfig {
        enabled_providers: instance_names.clone(),
        flow: flow.to_string(),
        post_login_redirect: config.success_redirect_location().to_string(),
    };
    let legacy_service = Arc::new(AuthApplicationService::new(
        provider_port.clone(),
        session_port.clone(),
        pending_port.clone(),
        make_config(FLOW_LEGACY),
    ));
    let v1_service = Arc::new(AuthApplicationService::new(
        provider_port,
        session_port,
        pending_port,
        make_config(FLOW_V1),
    ));
    OAuthEntryServices {
        legacy: legacy_service,
        v1: v1_service,
    }
}

/// Inventory-extendable OAuth provider factory (mirrors the auth-plugin
/// factory hook in `auth_wiring`). Built-in kinds are attempted first.
pub struct OAuthProviderFactoryRegistration {
    /// Build the provider for instance `name` from `cfg`; `None` lets the
    /// next registration try.
    pub build: fn(name: &str, cfg: &bcs_config_api::ProviderSettings) -> Option<Arc<dyn OAuthProvider>>,
}

inventory::collect!(OAuthProviderFactoryRegistration);

/// Compat-mode trusted-browser-origin list injected into BOTH auth
/// entrypoints: the finite exact set from `cors.allowed_origins`, excluding
/// wildcard and `null` entries and any
/// entry that does not parse as an absolute http(s) URL. An empty result
/// means cookie-backed refresh/logout is rejected fail-closed (spec §9
/// 兼容性范围修正 — documented tightening).
pub fn compat_trusted_browser_origins(raw: &[String]) -> Option<Arc<Vec<String>>> {
    let origins: Vec<String> = raw
        .iter()
        .filter(|origin| {
            let trimmed = origin.trim();
            !trimmed.is_empty()
                && trimmed != "*"
                && trimmed != "null"
                && !trimmed.contains('*')
                && (trimmed.starts_with("http://") || trimmed.starts_with("https://"))
        })
        .map(|origin| origin.trim().trim_end_matches('/').to_string())
        .collect();
    if origins.is_empty() {
        None
    } else {
        Some(Arc::new(origins))
    }
}

/// The chain's (instance name, provider) pairs in the legacy reply order
/// (alphabetical instance names — the pre-switchover /auth/url behavior).
pub fn provider_instance_map(
    providers: HashMap<String, Arc<dyn OAuthProvider>>,
) -> Vec<(String, Arc<dyn OAuthProvider>)> {
    let mut names: Vec<String> = providers.keys().cloned().collect();
    names.sort();
    names
        .into_iter()
        .filter_map(|name| providers.get(&name).cloned().map(|p| (name, p)))
        .collect()
}

// ───────────────────────────────────────────────────────────────────────────
// Task 12 — source build fns + `[api.auth]` assembly (spec §6)
// ───────────────────────────────────────────────────────────────────────────

/// Build fn for the built-in `gateway` source (spec §4.3: the NEW chain's
/// gateway secret comes from `api.auth.gateway.signing_key_secret` resolved
/// through the SecretAccessPort — the legacy `[gateway_principal]` sources
/// apply only to compat mode). Wraps the EXISTING gateway verifier
/// construction from `server::gateway_trust`.
pub fn build_gateway_source(
    ctx: ApiAuthBuildContext,
) -> Pin<Box<dyn Future<Output = Result<Arc<dyn PrincipalVerifier>, String>> + Send>> {
    Box::pin(async move {
        let table = api_auth_provider_configs::parse_gateway_table(&ctx.options)?;
        let material = api_auth_provider_configs::resolve_source_secret(
            ctx.secret_access.as_ref(),
            "gateway",
            "signing_key_secret",
            table.signing_key_secret.as_deref(),
        )
        .await?;
        let legacy = crate::GatewayPrincipalConfig {
            issuers: table.issuers,
            audience: table.audience,
            key_id: table.key_id,
            signing_key_env: crate::GatewayPrincipalConfig::default().signing_key_env,
            signing_key_secret: None,
        };
        crate::server::build_gateway_principal_verifier(&legacy, Some(&material))
            .map(|verifier| verifier as Arc<dyn PrincipalVerifier>)
            .map_err(|e| e.to_string())
    })
}

/// The §7.2 OAuth cookie-source verifier.
///
/// Wraps the SHARED strict [`bcs_auth_oauth::OAuthSessionEngine`]: every
/// OAuth source in the chain verifies the same `bcs_session` cookie through
/// the same engine instance, per source only applying its own name
/// comparison on top.
///
/// # Request-local caching contract (spec §7.2, do not weaken)
///
/// The strict engine verification result (Ok or Err) is cached in the
/// per-request [`VerificationAttempt`] ONLY — it is created fresh by every
/// [`CompositePrincipalVerifier::verify`] call and never stored in any
/// process-shared object. This struct never caches verified users across
/// requests: a second OAuth source re-serving the SAME request reuses the
/// attempt cache, a later REQUEST re-verifies from scratch.
struct OAuthCookieSourceVerifier {
    /// Fixed by the registration — never derived from request data (spec
    /// §7.4: `authentication_context.source` comes from the bootstrap-
    /// injected descriptor only).
    source: String,
    oauth: Arc<OAuthSourceContext>,
}

impl OAuthCookieSourceVerifier {
    fn map_engine_error(
        &self,
        error: bcs_auth_api::SessionAuthError,
    ) -> PrincipalVerificationError {
        match error {
            // Signature/claims/store mismatch or env mismatch → 401; chain
            // stops (spec §7.2: 无效会话在首次 OAuth 处理时立即 401).
            bcs_auth_api::SessionAuthError::Invalid => PrincipalVerificationError::Invalid,
            // Store fault is a dependency failure, NOT an unmatch (spec §7.2
            // step 2: DB 错误不是未匹配) → 503.
            bcs_auth_api::SessionAuthError::Unavailable => {
                PrincipalVerificationError::Unavailable
            }
            // The engine never emits Forbidden for verify; Conflict is
            // install-only. Anything else is a contract violation → Internal.
            _ => PrincipalVerificationError::Internal,
        }
    }

    /// §7.3 Human projection: `user.id` = internal identity id; `username`
    /// = trusted non-empty name falling back to the internal id;
    /// `display_name` may be absent; tenant/bot/app/access_key all empty.
    ///
    /// `provider` is the VERIFIED session issuer (the store-confirmed
    /// `scope.provider`, never an unverified client-supplied value). It is
    /// baked into the identity's `authentication_context.source` BEFORE the
    /// cache step so a later chain position can never attribute another
    /// provider's session to its own provenance (spec §7.2 step 4).
    fn project_human(
        &self,
        provider: &str,
        snapshot: &bcs_auth_api::SessionSnapshot,
    ) -> VerifiedRequestIdentity {
        let user_id = snapshot.scope.user_id.clone();
        // §7.3: username 优先使用可信非空名称 (the upstream-confirmed display
        // name, then the store's account name), else the internal id.
        let trusted_name = snapshot
            .display_name
            .as_deref()
            .map(str::trim)
            .filter(|name| !name.is_empty())
            .map(str::to_string)
            .or_else(|| {
                let account = snapshot.username.trim();
                (!account.is_empty()).then(|| account.to_string())
            })
            .unwrap_or_else(|| user_id.clone());
        VerifiedRequestIdentity {
            caller: AuthenticatedCaller {
                tenant: None,
                user: Some(AuthenticatedUserIdentity {
                    id: user_id,
                    username: trusted_name,
                    display_name: snapshot.display_name.clone(),
                    full_name: None,
                }),
                bot: None,
                app: None,
                access_key: None,
            },
            authentication_context: AuthenticationContext {
                source: provider.to_string(),
                credential_kind: CredentialKind::OAuthSessionCookie,
            },
            // Trusted display metadata from the strict session snapshot —
            // never authorization input (review 2026-09-20 #7: /auth/user
            // must keep returning the avatar under the explicit chain).
            display: bcs_api_http::DisplayMetadata {
                avatar: snapshot.avatar.clone(),
            },
        }
    }
}

/// Strict cookie-carrier extraction (spec §7.1: 启用来源的重复、空白或格式
/// 错误的 credential carrier 不得当成凭据缺失; contract §4: enabled
/// source 的 blank/malformed credential 必须拒绝且不能 fallback).
///
/// Returns:
/// - `Ok(None)` — truly NO `bcs_session` cookie present at all: absent
///   credential → `Missing`, the chain may continue to later sources.
/// - `Ok(Some(token))` — exactly one non-blank carrier.
/// - `Err(())` — the carrier is PRESENT but ambiguous or malformed: blank
///   value (`bcs_session=`), value-less (`bcs_session`), or duplicate
///   carriers, or an unreadable Cookie header → `Invalid` (401, no fallback).
fn extract_strict_session_cookie(headers: &HeaderMap) -> Result<Option<String>, ()> {
    let mut found: Option<String> = None;
    let mut duplicated = false;
    for header in headers.get_all("cookie") {
        // Reject unreadable Cookie headers rather than hiding a potentially
        // present session carrier and falling through to a later source.
        let values = header.to_str().map_err(|_| ())?;
        for pair in values.split(';') {
            let mut parts = pair.splitn(2, '=');
            let name = parts.next().unwrap_or("").trim();
            if name != bcs_auth_api::BCS_SESSION_COOKIE {
                continue;
            }
            let value = parts.next().map(str::trim).unwrap_or("");
            if found.is_some() || duplicated {
                duplicated = true;
                continue;
            }
            found = Some(value.to_string());
        }
    }
    match found {
        None => Ok(None),
        // Present but blank / value-less → malformed carrier, NOT absent.
        Some(value) if value.is_empty() => Err(()),
        Some(_) if duplicated => Err(()),
        Some(value) => Ok(Some(value)),
    }
}

#[async_trait::async_trait]
impl PrincipalVerifier for OAuthCookieSourceVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        let mut attempt = VerificationAttempt::default();
        self.verify_in_attempt(headers, &mut attempt).await
    }

    /// §7.2 dispatch:
    /// 1. Reuse the cached strict result for this request if present.
    /// 2. Extract the cookie strictly (absent → Missing; duplicate → Invalid).
    /// 3. Run the shared engine's strict verify ONCE; cache the outcome.
    /// 4. Verified provider outside the chain allowlist → Forbidden (403).
    /// 5. Verified provider ≠ this source → Missing (a later OAuth source
    ///    may match).
    /// 6. Match → Ok with the registration-fixed provenance.
    async fn verify_in_attempt(
        &self,
        headers: &HeaderMap,
        attempt: &mut VerificationAttempt,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        if let Some(cached) = &attempt.oauth_session {
            return match cached {
                Ok(identity)
                    if identity.authentication_context.source == self.source =>
                {
                    Ok(identity.clone())
                }
                Ok(_) => Err(PrincipalVerificationError::Missing),
                Err(error) => Err(error.clone()),
            };
        }

        let token = match extract_strict_session_cookie(headers) {
            Err(()) => {
                // Present but blank / value-less / duplicate carriers:
                // malformed credential for an enabled source → Invalid
                // (terminal 401; the chain must NOT continue to later
                // sources, contract §4).
                let outcome = Err(PrincipalVerificationError::Invalid);
                attempt.oauth_session = Some(outcome.clone());
                return outcome;
            }
            Ok(None) => {
                // Truly no credential for this carrier class → Missing. The
                // chain continues; cached so a later OAuth position does
                // not re-extract either.
                let outcome = Err(PrincipalVerificationError::Missing);
                attempt.oauth_session = Some(outcome.clone());
                return outcome;
            }
            Ok(Some(token)) => token,
        };

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or_default();
        let outcome = match self.oauth.engine.verify(&token, now).await {
            Ok(snapshot) => {
                let provider = snapshot.scope.provider.clone();
                if !self.oauth.allowed_providers.iter().any(|p| *p == provider) {
                    // Valid session of a provider the new chain does not
                    // enable → explicit policy rejection, 403, no fallback
                    // (spec §7.2 step 3).
                    Err(PrincipalVerificationError::Forbidden)
                } else {
                    Ok(self.project_human(&provider, &snapshot))
                }
            }
            Err(error) => Err(self.map_engine_error(error)),
        };
        attempt.oauth_session = Some(outcome.clone());

        match outcome {
            Ok(identity) if identity.authentication_context.source == self.source => Ok(identity),
            Ok(_) => Err(PrincipalVerificationError::Missing),
            Err(error) => Err(error),
        }
    }
}

/// Build fn shared by the built-in OAuth sources (`alipay` / `github` /
/// `google` / `wechat`). Each builds an OAuth cookie verifier keyed to ITS
/// source name over the SHARED engine; no per-source JWT logic is
/// constructed (spec §5: OAuth providers 共享严格会话校验实现).
pub fn build_oauth_source(
    ctx: ApiAuthBuildContext,
) -> Pin<Box<dyn Future<Output = Result<Arc<dyn PrincipalVerifier>, String>> + Send>> {
    Box::pin(async move {
        let oauth = ctx.oauth.clone().ok_or_else(|| {
            format!(
                "api.auth.{}: an OAuth source requires the shared OAuth session engine \
                 (api.auth.session_signing_key_secret must be configured and resolvable)",
                ctx.name
            )
        })?;
        let verifier: Arc<dyn PrincipalVerifier> = Arc::new(OAuthCookieSourceVerifier {
            source: ctx.name.clone(),
            oauth,
        });
        Ok(verifier)
    })
}

/// Assembly inputs for the `[api.auth]` composition (spec §6).
pub struct ApiAuthAssemblyInputs {
    /// The registered source inventory to compose against (production:
    /// [`crate::api_auth_registry::default_api_auth_registrations`]).
    pub registrations: Vec<ApiAuthSourceRegistration>,
    /// The final merged `[api.auth]` configuration.
    pub config: bcs_config_api::ApiAuthConfig,
    /// Secret resolution port. Called ONLY for enabled instances.
    pub secret_access: Arc<dyn SecretAccessPort>,
    /// The strict identity/session port shared by the whole chain.
    pub sessions: Arc<dyn bcs_auth_api::AuthSessionIdentityPort>,
    /// Session-issuer environment partition.
    pub env: String,
    /// The ONE shared strict engine of the chain family (built by the
    /// caller over `sessions`; see `build_api_auth_oauth_engine`). Required
    /// when an OAuth source is in chain.
    pub oauth: Option<Arc<bcs_auth_oauth::OAuthSessionEngine>>,
    /// Signing material backing `oauth` (the V1 facade signs refresh/revoke
    /// cookies with the same secret the engine verifies). When `None`, the
    /// assembly resolves `api.auth.session_signing_key_secret` through the
    /// secret port itself.
    pub session_signing_material: Option<String>,
}

/// The assembled `[api.auth]` artifact, published to the V1 ApiState and the
/// router construct-together at startup (spec §6: 有序 Composite
/// PrincipalVerifier + facade + CSRF origins over the SAME assembly).
pub struct BuiltApiAuth {
    /// Composite over the built source verifiers in CHAIN order.
    pub verifier: Arc<dyn PrincipalVerifier>,
    /// V1 OAuth login facade. `None` is VALID (gateway-only chains): /user
    /// then resolves the Human through the verifier's delivery projection,
    /// never 404.
    pub auth_service: Option<Arc<dyn bcs_service_api::application::v1::AuthService>>,
    /// CSRF allowlist for cookie-backed requests (from
    /// `api.auth.trusted_browser_origins`).
    pub origins: TrustedBrowserOrigins,
    /// The built source names, in chain order (diagnostics/tests).
    pub sources: Vec<String>,
    /// The configured `api.auth.public_base_url` (base for the V1 auth
    /// routes), present when OAuth sources are in chain.
    pub public_base_url: Option<String>,
}

impl std::fmt::Debug for BuiltApiAuth {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BuiltApiAuth")
            .field("sources", &self.sources)
            .field("has_auth_service", &self.auth_service.is_some())
            .field("public_base_url", &self.public_base_url)
            .field("origins", &"<TrustedBrowserOrigins>")
            .field("verifier", &"<PrincipalVerifier>")
            .finish()
    }
}

/// Compose the `[api.auth]` chain (spec §6 composition order):
///
/// 1. validate the merged config against the registered inventory;
/// 2. build each chain source's verifier in chain order (unknown /
///    duplicate / build failure = STARTUP error, never partial);
/// 3. resolve secrets only for enabled instances;
/// 4. when an OAuth source is in chain, build the V1 login facade from the
///    same shared engine + pending store + identity port.
pub async fn build_api_auth(inputs: ApiAuthAssemblyInputs) -> Result<BuiltApiAuth, String> {
    let ApiAuthAssemblyInputs {
        registrations,
        config,
        secret_access,
        sessions,
        env,
        oauth,
        session_signing_material,
    } = inputs;

    // Phase 1: contract + registry validation (unknown sources, duplicates,
    // cross-checks). This rejects BEFORE any build or secret access.
    validate_api_auth(&config, &registrations)?;

    let by_name: HashMap<&str, &ApiAuthSourceRegistration> = registrations
        .iter()
        .map(|r| (r.name, r))
        .collect();
    let oauth_sources: Vec<String> = config
        .chain
        .iter()
        .filter(|name| {
            by_name
                .get(name.as_str())
                .is_some_and(|r| r.capabilities.is_oauth_provider)
        })
        .cloned()
        .collect();

    // Shared OAuth context: ONE engine + the chain OAuth allowlist.
    let oauth_context: Option<Arc<OAuthSourceContext>> = if oauth_sources.is_empty() {
        None
    } else {
        let engine = oauth
            .as_ref()
            .ok_or_else(|| {
                "api.auth: an OAuth source is in chain but the shared OAuth session engine \
                 was not assembled"
                    .to_string()
            })?
            .clone();
        Some(Arc::new(OAuthSourceContext {
            engine,
            allowed_providers: oauth_sources.clone(),
        }))
    };

    // Phase 2: build each enabled source in chain order. Build failures are
    // startup failures (spec §4.3); nothing is published on failure.
    let mut verifiers: Vec<Arc<dyn PrincipalVerifier>> = Vec::with_capacity(config.chain.len());
    let mut sources = Vec::with_capacity(config.chain.len());
    for name in &config.chain {
        let registration = by_name.get(name.as_str()).copied().ok_or_else(|| {
            format!(
                "api.auth.chain references unknown source '{name}'; \
                 it is not registered in the source inventory"
            )
        })?;
        let context = ApiAuthBuildContext {
            name: name.clone(),
            options: config.plugin_tables.get(name).cloned().unwrap_or(serde_json::Value::Null),
            secret_access: secret_access.clone(),
            sessions: sessions.clone(),
            oauth: oauth_context.clone(),
        };
        let verifier = (registration.build)(context).await?;
        verifiers.push(verifier);
        sources.push(name.clone());
    }

    // Phase 3: the V1 login facade — only when an OAuth source is in chain.
    // Gateway-only → auth_service None is VALID.
    let (auth_service, public_base_url) = if let Some(engine) = oauth {
        let mut providers: Vec<(String, Arc<dyn bcs_auth_api::OAuthProvider>)> =
            Vec::with_capacity(oauth_sources.len());
        for name in &oauth_sources {
            let options = config
                .plugin_tables
                .get(name)
                .cloned()
                .unwrap_or(serde_json::Value::Null);
            let provider = api_auth_provider_configs::build_oauth_source_provider(
                name,
                &options,
                &secret_access,
            )
            .await?;
            providers.push((name.clone(), provider));
        }
        let material = match session_signing_material {
            Some(material) => material,
            None => {
                api_auth_provider_configs::resolve_source_secret(
                    secret_access.as_ref(),
                    "",
                    "session_signing_key_secret",
                    config.session_signing_key_secret.as_deref(),
                )
                .await?
            }
        };
        let base_url = config.public_base_url.clone().unwrap_or_default();
        let oauth_config = bcs_auth_api::OAuthConfig {
            jwt_secret: material,
            idle_timeout_minutes: config.session_idle_timeout_minutes,
            cookie_secure: bcs_auth_api::OAuthConfig::default_cookie_secure(&base_url),
            env,
            success_redirect_path: config.success_redirect_path.clone(),
            base_url: base_url.clone(),
        };
        let entry = build_oauth_entry_services(
            providers,
            engine,
            sessions,
            Arc::new(bcs_auth_oauth::MemoryPendingOAuthLoginStore::new()),
            &oauth_config,
        );
        (Some(entry.v1), Some(base_url))
    } else {
        (None, None)
    };

    let origins = TrustedBrowserOrigins::new(
        config.trusted_browser_origins.clone().unwrap_or_default(),
    )
    .map_err(|e| format!("api.auth.trusted_browser_origins: {e}"))?;

    Ok(BuiltApiAuth {
        verifier: Arc::new(bcs_api_http::CompositePrincipalVerifier::new(verifiers)),
        auth_service,
        origins,
        sources,
        public_base_url,
    })
}

/// Construct the ONE strict engine for the `[api.auth]` chain family over
/// the shared identity/session port. All OAuth sources of the chain verify
/// through this single instance; the legacy `[auth.oauth]` chain's engine
/// (a different signing secret) stays a separate instance.
pub fn build_api_auth_oauth_engine(
    signing_material: &str,
    sessions: Arc<dyn bcs_auth_api::AuthSessionIdentityPort>,
    env: String,
    idle_secs: u64,
) -> Arc<bcs_auth_oauth::OAuthSessionEngine> {
    Arc::new(bcs_auth_oauth::OAuthSessionEngine::new(
        bcs_jwt::OAuthSessionJwt::new(signing_material),
        sessions,
        env,
        idle_secs,
    ))
}
