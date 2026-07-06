//! Chain-resolution contract tests (Rule 25), exercised through the Local /
//! Static / Noop plugin implementations.
//!
//! These live here (not in `bcs-auth-api`) because the chain semantics can only
//! be verified with concrete `AuthPlugin` implementations, which now reside in
//! `bcs-auth-local` (Local/Static) and `bcs-test-support` (Noop).

use axum::http::HeaderMap;
use bcs_auth_api::{AuthPlugin, AuthPluginChain, AuthPrincipal, AuthSource, LocalAuthConfig};
use bcs_auth_local::{LocalAuthPlugin, StaticAuthPlugin};
use bcs_test_support::NoopAuthPlugin;

/// Contract: Empty chain returns Ok(AuthResult { principal: None }) (anonymous).
#[tokio::test]
async fn empty_chain_returns_none() {
    let chain = AuthPluginChain::new(vec![]);
    let result = chain.authenticate(&HeaderMap::new()).await;
    assert!(result.is_ok(), "Empty chain should return Ok");
    assert!(
        result.unwrap().principal.is_none(),
        "Empty chain should return principal = None"
    );
}

/// Contract: First plugin that returns Some(_) wins (priority order).
#[tokio::test]
async fn first_match_wins() {
    let plugin1 = Box::new(StaticAuthPlugin::with_user_id("user1")) as Box<dyn AuthPlugin>;
    let plugin2 = Box::new(StaticAuthPlugin::with_user_id("user2")) as Box<dyn AuthPlugin>;
    let chain = AuthPluginChain::new(vec![plugin1, plugin2]);

    let result = chain.authenticate(&HeaderMap::new()).await.expect("chain authenticate");

    assert_eq!(
        result.principal.as_ref().unwrap().user_id,
        Some("user1".to_string())
    );
}

/// Contract: NoopAuthPlugin always returns None (never matches).
#[tokio::test]
async fn noop_plugin_never_matches() {
    let noop = Box::new(NoopAuthPlugin) as Box<dyn AuthPlugin>;
    let chain = AuthPluginChain::new(vec![noop]);
    let result = chain.authenticate(&HeaderMap::new()).await.expect("chain authenticate");
    assert!(result.principal.is_none(), "NoopAuthPlugin should return None");
}

/// Contract: LocalAuthPlugin always matches (returns its fixed principal).
#[tokio::test]
async fn local_plugin_always_matches() {
    let config = LocalAuthConfig {
        mock_user_id: Some("12345".to_string()),
        mock_user_name: Some("LocalUser".to_string()),
        allow_mock_headers: false,
    };
    let local = Box::new(LocalAuthPlugin::from_config(&config)) as Box<dyn AuthPlugin>;
    let chain = AuthPluginChain::new(vec![local]);

    let result = chain.authenticate(&HeaderMap::new()).await.expect("chain authenticate");

    let principal = result.principal.expect("LocalAuthPlugin should return Some");
    assert_eq!(principal.user_id, Some("12345".to_string()));
    assert_eq!(principal.user_name, Some("LocalUser".to_string()));
    assert_eq!(principal.source_name.as_deref(), Some("Local"));
}

/// Contract: Chain stops at first match, skipping later plugins.
#[tokio::test]
async fn chain_stops_at_first_match() {
    let plugin1 = Box::new(NoopAuthPlugin) as Box<dyn AuthPlugin>;
    let plugin2 = Box::new(StaticAuthPlugin::with_user_id("winner")) as Box<dyn AuthPlugin>;
    let plugin3 = Box::new(StaticAuthPlugin::with_user_id("skipped")) as Box<dyn AuthPlugin>;
    let chain = AuthPluginChain::new(vec![plugin1, plugin2, plugin3]);

    let result = chain.authenticate(&HeaderMap::new()).await.expect("chain authenticate");

    let principal = result.principal.expect("Should match plugin2");
    assert_eq!(principal.user_id, Some("winner".to_string()));
}

/// Contract: Multiple Noop plugins followed by Static → Static wins.
#[tokio::test]
async fn multiple_noops_then_static() {
    let noop1 = Box::new(NoopAuthPlugin) as Box<dyn AuthPlugin>;
    let noop2 = Box::new(NoopAuthPlugin) as Box<dyn AuthPlugin>;
    let static_plugin = Box::new(StaticAuthPlugin::with_user_id("final")) as Box<dyn AuthPlugin>;
    let chain = AuthPluginChain::new(vec![noop1, noop2, static_plugin]);

    let result = chain.authenticate(&HeaderMap::new()).await.expect("chain authenticate");

    let principal = result.principal.expect("Should match static_plugin");
    assert_eq!(principal.user_id, Some("final".to_string()));
}

/// Contract: AuthPrincipal contains source_name from the matched plugin.
#[tokio::test]
async fn auth_result_preserves_source() {
    let mut principal = AuthPrincipal::new(AuthSource::AgentPass);
    principal.user_id = Some("user".to_string());
    let plugin = Box::new(StaticAuthPlugin::with_principal(principal)) as Box<dyn AuthPlugin>;
    let chain = AuthPluginChain::new(vec![plugin]);

    let result = chain.authenticate(&HeaderMap::new()).await.expect("chain authenticate");

    assert_eq!(
        result.principal.unwrap().source_name.as_deref(),
        Some("AgentPass")
    );
}

/// Contract: Chain with only Noop plugins returns None.
#[tokio::test]
async fn all_noop_chain_returns_none() {
    let chain = AuthPluginChain::new(vec![
        Box::new(NoopAuthPlugin) as Box<dyn AuthPlugin>,
        Box::new(NoopAuthPlugin) as Box<dyn AuthPlugin>,
        Box::new(NoopAuthPlugin) as Box<dyn AuthPlugin>,
    ]);

    let result = chain.authenticate(&HeaderMap::new()).await.expect("chain authenticate");
    assert!(result.principal.is_none(), "All-Noop chain should return None");
}

/// Contract: StaticAuthPlugin with_principal returns the exact principal.
#[tokio::test]
async fn static_plugin_returns_exact_principal() {
    let mut expected = AuthPrincipal::new(AuthSource::SessionToken);
    expected.user_id = Some("test_user".to_string());
    expected.user_name = Some("Test User".to_string());
    expected.bot_uuid = Some("bot-123".to_string());
    expected.owner_id = Some("owner-456".to_string());

    let plugin = StaticAuthPlugin::with_principal(expected.clone());
    let result = plugin.authenticate(&HeaderMap::new()).await.expect("authenticate");

    let principal = result.expect("StaticAuthPlugin should return Some");
    assert_eq!(principal.user_id, Some("test_user".to_string()));
    assert_eq!(principal.user_name, Some("Test User".to_string()));
    assert_eq!(principal.bot_uuid, Some("bot-123".to_string()));
    assert_eq!(principal.owner_id, Some("owner-456".to_string()));
    assert_eq!(principal.source_name.as_deref(), Some("SessionToken"));
}

/// Contract: can_authenticate is a hint (no side effects).
#[tokio::test]
async fn can_authenticate_is_hint_not_guarantee() {
    let noop = NoopAuthPlugin;
    let headers = HeaderMap::new();
    assert!(!noop.can_authenticate(&headers));

    let static_plugin = StaticAuthPlugin::with_user_id("test");
    assert!(static_plugin.can_authenticate(&headers));
}

/// Contract: LocalAuthPlugin with None config returns None.
#[tokio::test]
async fn local_plugin_none_config_returns_none() {
    let config = LocalAuthConfig {
        mock_user_id: None,
        mock_user_name: None,
        allow_mock_headers: false,
    };
    let local = LocalAuthPlugin::from_config(&config);
    let result = local.authenticate(&HeaderMap::new()).await.expect("authenticate");
    assert!(result.is_none(), "LocalAuthPlugin with None config should return None");
}

/// Contract: StaticAuthPlugin with_user_id sets user_id and source_name.
#[tokio::test]
async fn static_plugin_with_user_id_sets_fields() {
    let plugin = StaticAuthPlugin::with_user_id("alice");
    let result = plugin.authenticate(&HeaderMap::new()).await.expect("authenticate");

    let principal = result.expect("StaticAuthPlugin should return Some");
    assert_eq!(principal.user_id, Some("alice".to_string()));
    assert_eq!(principal.source_name.as_deref(), Some("Local")); // StaticAuthPlugin uses Local source
}

/// Contract: Chain resolves to first successful plugin, short-circuiting the rest.
#[tokio::test]
async fn chain_short_circuits_on_first_success() {
    let chain = AuthPluginChain::new(vec![
        Box::new(NoopAuthPlugin) as Box<dyn AuthPlugin>,
        Box::new(StaticAuthPlugin::with_user_id("success")) as Box<dyn AuthPlugin>,
        Box::new(NoopAuthPlugin) as Box<dyn AuthPlugin>,
    ]);

    let result = chain.authenticate(&HeaderMap::new()).await.expect("chain authenticate");
    let principal = result.principal.expect("Should match the static plugin");
    assert_eq!(principal.user_id, Some("success".to_string()));
}
