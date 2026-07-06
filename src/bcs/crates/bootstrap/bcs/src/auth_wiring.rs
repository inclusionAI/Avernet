//! Composition root for the auth plugin chain.

use std::sync::Arc;

use bcs_auth_alipay::{AlipayConfig, AlipayOAuthProvider};
use bcs_auth_api::{
    AuthConfig, AuthError, AuthPlugin, AuthPluginChain, BotInfo, BotLookupPort, LocalAuthConfig,
    OAuthConfig, OAuthProvider, UserIdentityPort,
};
use bcs_auth_local::LocalAuthPlugin;
use bcs_auth_oauth::OAuthSessionPlugin;
use bcs_auth_session::SessionTokenPlugin;
use bcs_auth_wechat::{WeChatConfig, WeChatOAuthProvider};
use bcs_config_api::{AuthChainConfig, ProviderSettings};
use bcs_service_api::BotRegistryCoreService;
use secrecy::ExposeSecret;

pub struct AuthPluginBuildContext {
    pub name: String,
    pub config: AuthConfig,
    pub bot_registry: Arc<dyn BotRegistryCoreService>,
    pub user_identity_port: Option<Arc<dyn UserIdentityPort>>,
}

pub type AuthPluginFactory = Arc<
    dyn Fn(AuthPluginBuildContext) -> Result<Option<Box<dyn AuthPlugin>>, String> + Send + Sync,
>;

pub type RegisteredAuthPluginBuild =
    fn(AuthPluginBuildContext) -> Result<Option<Box<dyn AuthPlugin>>, String>;

pub struct AuthPluginFactoryRegistration {
    pub name: &'static str,
    pub build: RegisteredAuthPluginBuild,
}

inventory::collect!(AuthPluginFactoryRegistration);

/// Build-profile default chain, applied when `[auth].chain` is empty.
///
/// This decision lives in the composition root (not the `bcs-auth-api`
/// contract crate) per Rule 14: debug builds default to local-mock auth,
/// release builds to session-token auth.
fn default_auth_chain() -> Vec<String> {
    if cfg!(debug_assertions) {
        vec!["local".to_string()]
    } else {
        vec!["session".to_string()]
    }
}

/// Resolve the configured `[auth]` section into `bcs_auth_api::AuthConfig`.
///
/// An empty `chain` means "not configured" → fall back to the build-profile
/// default ([`default_auth_chain`]: debug = `["local"]`, release =
/// `["session"]`). A non-empty `chain` takes over fully.
///
/// `env` is the runtime environment tag used to partition OAuth user
/// identities; it is woven into the resolved `OAuthConfig`.
pub fn resolve_auth_config(cfg: &AuthChainConfig, env: &str) -> AuthConfig {
    let chain = if cfg.chain.is_empty() {
        default_auth_chain()
    } else {
        cfg.chain.clone()
    };
    let allow_mock_headers = cfg.allow_mock_headers
        || std::env::var("BCS_AUTH_MOCK")
            .ok()
            .is_some_and(|value| value == "1");
    AuthConfig {
        chain,
        require_authentication: cfg.require_authentication,
        local: LocalAuthConfig {
            mock_user_id: cfg.mock_user_id.clone().or_else(|| {
                std::env::var("BCS_MOCK_USER_ID").ok().filter(|s| !s.is_empty())
            }),
            mock_user_name: cfg.mock_user_name.clone().or_else(|| {
                std::env::var("BCS_MOCK_USER_NICK_NAME").ok().filter(|s| !s.is_empty())
            }),
            allow_mock_headers,
        },
        oauth: cfg.oauth.as_ref().and_then(|o| {
            // jwt_secret is required; an absent/empty secret keeps OAuth off.
            let jwt_secret = o.jwt_secret.as_ref().map(|s| s.expose_secret().clone())?;
            let cookie_secure = o
                .cookie_secure
                .unwrap_or_else(|| OAuthConfig::default_cookie_secure(&o.base_url));
            Some(OAuthConfig {
                jwt_secret,
                idle_timeout_minutes: o.idle_timeout_minutes,
                base_url: o.base_url.clone(),
                cookie_secure,
                env: env.to_string(),
            })
        }),
    }
}

/// Adapt `BotRegistryCoreService` to the session plugin's `BotLookupPort`.
pub struct BotRegistryLookup {
    registry: Arc<dyn BotRegistryCoreService>,
}

impl BotRegistryLookup {
    pub fn new(registry: Arc<dyn BotRegistryCoreService>) -> Self {
        Self { registry }
    }
}

#[async_trait::async_trait]
impl BotLookupPort for BotRegistryLookup {
    async fn find_bot_by_token(&self, token: &str) -> Result<Option<BotInfo>, AuthError> {
        match self.registry.find_bot_by_token(token).await {
            Some(bot_uuid) => {
                let owner_id = self
                    .registry
                    .get(&bot_uuid)
                    .await
                    .and_then(|b| b.created_by);
                Ok(Some(BotInfo { bot_uuid, owner_id }))
            }
            None => Ok(None),
        }
    }

    async fn find_bot_by_agent_code(
        &self,
        agent_code: &str,
    ) -> Result<Option<BotInfo>, AuthError> {
        match self.registry.find_bot_by_agent_code(agent_code).await {
            Some(bot_uuid) => {
                let owner_id = self.registry.get(&bot_uuid).await.and_then(|b| b.created_by);
                Ok(Some(BotInfo { bot_uuid, owner_id }))
            }
            None => Ok(None),
        }
    }
}

fn build_builtin_auth_plugin(
    name: &str,
    config: &AuthConfig,
    bot_registry: Arc<dyn BotRegistryCoreService>,
    user_identity_port: Option<Arc<dyn UserIdentityPort>>,
) -> Result<Option<Box<dyn AuthPlugin>>, ()> {
    match name {
        "session" => {
            let lookup = Arc::new(BotRegistryLookup::new(bot_registry));
            Ok(Some(Box::new(SessionTokenPlugin::new(lookup))))
        }
        "local" => Ok(Some(Box::new(LocalAuthPlugin::from_config(&config.local)))),
        // Provider-agnostic OAuth session plugin: one instance verifies
        // sessions from any provider (the issuing provider is recorded in
        // the JWT). The plugin name is intentionally NOT a provider name —
        // `oauth_session` enables session verification, it does not select
        // Google/GitHub. Provider selection happens via `[auth.oauth.*]`.
        "oauth_session" => {
            if let (Some(oauth), Some(port)) = (&config.oauth, &user_identity_port) {
                Ok(Some(Box::new(OAuthSessionPlugin::new(
                    oauth.jwt_secret.clone(),
                    Arc::clone(port),
                ))))
            } else {
                tracing::warn!(
                    "oauth_session plugin requested but OAuth config or UserIdentityPort missing"
                );
                Ok(None)
            }
        }
        _ => Err(()),
    }
}

/// Build the priority-ordered chain from config and optional external factories.
pub fn try_build_auth_chain_with_factories(
    config: &AuthConfig,
    bot_registry: Arc<dyn BotRegistryCoreService>,
    user_identity_port: Option<Arc<dyn UserIdentityPort>>,
    extension_factories: &[AuthPluginFactory],
) -> Result<AuthPluginChain, String> {
    let mut plugins: Vec<Box<dyn AuthPlugin>> = Vec::new();
    for name in &config.chain {
        match build_builtin_auth_plugin(
            name,
            config,
            Arc::clone(&bot_registry),
            user_identity_port.clone(),
        ) {
            Ok(Some(plugin)) => {
                plugins.push(plugin);
                continue;
            }
            Ok(None) => continue,
            Err(()) => {}
        }

        let mut handled = false;
        for factory in extension_factories {
            let ctx = AuthPluginBuildContext {
                name: name.clone(),
                config: config.clone(),
                bot_registry: Arc::clone(&bot_registry),
                user_identity_port: user_identity_port.clone(),
            };
            match factory(ctx) {
                Ok(Some(plugin)) => {
                    plugins.push(plugin);
                    handled = true;
                    break;
                }
                Ok(None) => {}
                Err(error) => {
                    tracing::warn!(
                        plugin = %name,
                        error = %error,
                        "Auth extension factory failed"
                    );
                }
            }
        }
        if !handled {
            for registration in inventory::iter::<AuthPluginFactoryRegistration> {
                if registration.name != name {
                    continue;
                }
                let ctx = AuthPluginBuildContext {
                    name: name.clone(),
                    config: config.clone(),
                    bot_registry: Arc::clone(&bot_registry),
                    user_identity_port: user_identity_port.clone(),
                };
                match (registration.build)(ctx) {
                    Ok(Some(plugin)) => {
                        plugins.push(plugin);
                        handled = true;
                        break;
                    }
                    Ok(None) => {}
                    Err(error) => {
                        tracing::warn!(
                            plugin = %name,
                            error = %error,
                            "Registered auth plugin factory failed"
                        );
                    }
                }
            }
        }
        if !handled {
            let message = format!("unknown auth plugin in config: {name}");
            if config.require_authentication {
                return Err(message);
            }
            tracing::warn!("{}", message);
        }
    }
    Ok(AuthPluginChain::new(plugins))
}

/// Build the priority-ordered chain from config and optional external factories.
pub fn build_auth_chain_with_factories(
    config: &AuthConfig,
    bot_registry: Arc<dyn BotRegistryCoreService>,
    user_identity_port: Option<Arc<dyn UserIdentityPort>>,
    extension_factories: &[AuthPluginFactory],
) -> AuthPluginChain {
    match try_build_auth_chain_with_factories(
        config,
        bot_registry,
        user_identity_port,
        extension_factories,
    ) {
        Ok(chain) => chain,
        Err(error) => {
            tracing::warn!(error = %error, "Auth chain build failed");
            AuthPluginChain::new(Vec::new())
        }
    }
}

/// Build the priority-ordered chain from config.
pub fn build_auth_chain(
    config: &AuthConfig,
    bot_registry: Arc<dyn BotRegistryCoreService>,
    user_identity_port: Option<Arc<dyn UserIdentityPort>>,
) -> AuthPluginChain {
    build_auth_chain_with_factories(config, bot_registry, user_identity_port, &[])
}

/// Construct one `OAuthProvider` from a configured provider instance.
///
/// This `match` over `kind` is the single place that names concrete provider
/// crates — the compile-time closed set of supported provider types. Adding a
/// provider type means adding one arm here (plus its crate). `kind` defaults to
/// the instance `name` when omitted (the common 1:1 case).
///
/// Returns `Err` for an unknown `kind` or an empty `client_id`, so a
/// misconfiguration fails fast at startup rather than silently dropping the
/// provider (which would surface as a runtime 404).
pub fn build_oauth_provider(
    name: &str,
    cfg: &ProviderSettings,
) -> Result<Arc<dyn OAuthProvider>, String> {
    let kind = cfg.resolved_kind(name);
    let client_id = cfg.client_id.clone();
    if client_id.trim().is_empty() {
        return Err(format!(
            "auth.oauth.providers.{name}: client_id must not be empty"
        ));
    }
    let client_secret = cfg
        .client_secret
        .as_ref()
        .map(|s| s.expose_secret().clone())
        .unwrap_or_default();

    match kind {
        "google" => Ok(Arc::new(bcs_auth_google::GoogleOAuthProvider::new(
            bcs_auth_google::GoogleOAuthConfig {
                client_id,
                client_secret,
            },
        ))),
        "github" => Ok(Arc::new(bcs_auth_github::GitHubOAuthProvider::new(
            bcs_auth_github::GitHubOAuthConfig {
                client_id,
                client_secret,
            },
        ))),
        "wechat" => {
            let secret = cfg
                .client_secret
                .as_ref()
                .map(|s| s.expose_secret().clone())
                .unwrap_or_default();
            if secret.is_empty() {
                return Err(format!(
                    "auth.oauth.providers.{name}: wechat requires client_secret"
                ));
            }
            let provider = WeChatOAuthProvider::new(WeChatConfig {
                appid: client_id,
                secret,
            });
            Ok(Arc::new(provider) as Arc<dyn OAuthProvider>)
        }
        "alipay" => {
            let private_key_pem = cfg
                .private_key
                .as_ref()
                .map(|s| s.expose_secret().clone())
                .unwrap_or_default();
            let alipay_public_key_pem = cfg
                .alipay_public_key
                .as_ref()
                .map(|s| s.expose_secret().clone())
                .unwrap_or_default();
            if private_key_pem.is_empty() {
                return Err(format!(
                    "auth.oauth.providers.{name}: alipay requires private_key"
                ));
            }
            if alipay_public_key_pem.is_empty() {
                return Err(format!(
                    "auth.oauth.providers.{name}: alipay requires alipay_public_key"
                ));
            }
            let provider = AlipayOAuthProvider::new(AlipayConfig {
                app_id: client_id,
                private_key_pem,
                alipay_public_key_pem,
            })
            .map_err(|e| format!("auth.oauth.providers.{name}: alipay key error: {e}"))?;
            Ok(Arc::new(provider) as Arc<dyn OAuthProvider>)
        }
        other => Err(format!(
            "auth.oauth.providers.{name}: unknown provider kind '{other}'"
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderMap;
    use bcs_auth_api::AuthPrincipal;

    struct TestAuthPlugin;

    #[async_trait::async_trait]
    impl AuthPlugin for TestAuthPlugin {
        fn can_authenticate(&self, _headers: &HeaderMap) -> bool {
            false
        }

        async fn authenticate(
            &self,
            _headers: &HeaderMap,
        ) -> Result<Option<AuthPrincipal>, AuthError> {
            Ok(None)
        }

        fn priority(&self) -> u8 {
            1
        }

        fn name(&self) -> &'static str {
            "custom_auth"
        }
    }

    fn registered_test_auth_plugin(
        ctx: AuthPluginBuildContext,
    ) -> Result<Option<Box<dyn AuthPlugin>>, String> {
        if ctx.name == "registered_auth" {
            Ok(Some(Box::new(TestAuthPlugin)))
        } else {
            Ok(None)
        }
    }

    inventory::submit! {
        AuthPluginFactoryRegistration {
            name: "registered_auth",
            build: registered_test_auth_plugin,
        }
    }

    #[test]
    fn extension_factory_handles_unknown_plugin_name() {
        let mut config = AuthConfig::default();
        config.chain = vec!["custom_auth".to_string()];
        let data_dir = tempfile::tempdir().expect("tempdir");
        let bot_registry: Arc<dyn BotRegistryCoreService> =
            Arc::new(bcs_bot::BotCore::with_base_dir(data_dir.path().to_path_buf()));
        let factory: AuthPluginFactory = Arc::new(|ctx| {
            assert_eq!(ctx.config.chain, vec!["custom_auth".to_string()]);
            if ctx.name == "custom_auth" {
                Ok(Some(Box::new(TestAuthPlugin)))
            } else {
                Ok(None)
            }
        });

        let chain = build_auth_chain_with_factories(&config, bot_registry, None, &[factory]);

        assert_eq!(chain.plugin_names(), vec!["custom_auth"]);
    }

    #[test]
    fn registered_factory_handles_unknown_plugin_name() {
        let mut config = AuthConfig::default();
        config.chain = vec!["registered_auth".to_string()];
        let data_dir = tempfile::tempdir().expect("tempdir");
        let bot_registry: Arc<dyn BotRegistryCoreService> =
            Arc::new(bcs_bot::BotCore::with_base_dir(data_dir.path().to_path_buf()));

        let chain = build_auth_chain(&config, bot_registry, None);

        assert_eq!(chain.plugin_names(), vec!["custom_auth"]);
    }

    #[test]
    fn strict_auth_rejects_unknown_plugin_name() {
        let mut config = AuthConfig::default();
        config.chain = vec!["missing_auth".to_string()];
        config.require_authentication = true;
        let data_dir = tempfile::tempdir().expect("tempdir");
        let bot_registry: Arc<dyn BotRegistryCoreService> =
            Arc::new(bcs_bot::BotCore::with_base_dir(data_dir.path().to_path_buf()));

        let error = match try_build_auth_chain_with_factories(&config, bot_registry, None, &[]) {
            Ok(_) => panic!("strict auth should reject missing plugin"),
            Err(error) => error,
        };

        assert!(error.contains("missing_auth"));
    }
}
