use std::sync::Arc;
use axum::{Router, body::{Body, to_bytes}, http::{Request, StatusCode}};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::{BotCore, ProviderCore, ProviderManagement};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_http::{router::build_router, state::{ChainUserIdentityPort, HttpAppState}};
use bcs_services_container::Services;
use bcs_test_support::NoopRelationCoreService;
use serde_json::{Value, json};
use tower::ServiceExt;

struct App { router: Router, _dir: tempfile::TempDir }

fn app() -> App {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(MemoryProviderStore::new());
    let registry = Arc::new(BotCore::with_provider_repos(
        Arc::new(MemoryBotRepo::with_base_dir(dir.path().to_path_buf())),
        store.clone(), store.clone(), store.clone()));
    let core = Arc::new(ProviderCore::new(store.clone(), store.clone(), store, registry.clone()));
    let management = Arc::new(ProviderManagement::new(core.clone(), core.clone(),
        registry.clone(), Arc::new(NoopRelationCoreService)));
    let services = Services::builder().registry(registry).provider_core(core.clone())
        .provider_bot_core(core).provider_management(management).build_for_test();
    let auth = AuthPluginChain::new(vec![Box::new(StaticAuthPlugin::with_principal(
        AuthPrincipal { user_id: Some("alice".into()), ..Default::default() }))]);
    let state = HttpAppState::new(services).with_user_identity(
        Arc::new(ChainUserIdentityPort::new(Arc::new(auth))));
    App { router: build_router(state), _dir: dir }
}

async fn request(app: &App, method: &str, path: &str, token: Option<&str>, body: Value)
    -> (StatusCode, Value)
{
    let mut builder = Request::builder().method(method).uri(path)
        .header("content-type", "application/json");
    if let Some(token) = token { builder = builder.header("authorization", format!("Bearer {token}")); }
    let response = app.router.clone().oneshot(builder.body(Body::from(body.to_string())).unwrap())
        .await.unwrap();
    let status = response.status();
    let bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    (status, serde_json::from_slice(&bytes).unwrap_or(Value::Null))
}

async fn register(app: &App, slug: Value, mode: &str, version: &str) -> Value {
    let (status, result) = request(app, "POST", "/providers", None, json!({
        "name":"Coding Provider", "slug":slug, "auth":{"mode":mode},
        "protocol_version":version, "webhook_url":"https://example.org/private-hook",
        "admin_callback_url":"https://example.org/private-callback"
    })).await;
    assert_eq!(status, StatusCode::OK, "{result}");
    result
}

#[tokio::test]
async fn public_slug_lookup_returns_only_basic_info_for_all_auth_modes() {
    let app = app();
    for (slug, mode, version) in [("coding-one", "static_bearer", "1.0"),
        ("coding-two", "agentpass", "2.0"), ("coding-three", "provider_admin", "2.0")]
    {
        let registered = register(&app, json!(slug), mode, version).await;
        let (status, info) = request(&app, "GET", &format!("/providers/by-slug/{slug}"),
            None, Value::Null).await;
        assert_eq!(status, StatusCode::OK, "{info}");
        assert_eq!(info["provider_id"], registered["provider_id"]);
        assert_eq!(info["slug"], slug);
        assert_eq!(info["name"], "Coding Provider");
        assert_eq!(info["auth_mode"], mode);
        assert_eq!(info["protocol_version"], version);
        assert_eq!(info["enabled"], true);
        assert!(info["created_at"].as_u64().unwrap() > 0);
        assert!(info["updated_at"].as_u64().unwrap() >= info["created_at"].as_u64().unwrap());
        let keys = info.as_object().unwrap().keys().map(String::as_str).collect::<Vec<_>>();
        assert_eq!(keys, vec!["auth_mode", "created_at", "enabled", "name", "protocol_version",
            "provider_id", "slug", "updated_at"]);
    }
}

#[tokio::test]
async fn missing_and_invalid_slugs_have_distinct_errors() {
    let app = app();
    assert_eq!(request(&app, "GET", "/providers/by-slug/missing", None, Value::Null).await.0,
        StatusCode::NOT_FOUND);
    for slug in ["Upper", "under_score", "-leading", "trailing-", "with%20space"] {
        assert_eq!(request(&app, "GET", &format!("/providers/by-slug/{slug}"),
            None, Value::Null).await.0, StatusCode::BAD_REQUEST, "{slug}");
    }
}

#[tokio::test]
async fn registration_rejects_invalid_and_duplicate_slugs() {
    let app = app();
    for slug in ["".to_string(), "UPPER".into(), "a_b".into(), "-a".into(), "a-".into(),
        "a".repeat(65), "中文".into()]
    {
        assert_eq!(request(&app, "POST", "/providers", None,
            json!({"name":"Invalid", "slug":slug, "auth":{"mode":"static_bearer"}}))
            .await.0, StatusCode::BAD_REQUEST, "{slug}");
    }
    register(&app, json!("duplicate"), "static_bearer", "1.0").await;
    assert_eq!(request(&app, "POST", "/providers", None,
        json!({"name":"Duplicate", "slug":"duplicate", "auth":{"mode":"static_bearer"}}))
        .await.0, StatusCode::CONFLICT);
    register(&app, json!("a".repeat(64)), "static_bearer", "1.0").await;
    register(&app, json!("x"), "static_bearer", "1.0").await;
}

#[tokio::test]
async fn patch_renames_slug_and_duplicate_patch_preserves_all_metadata() {
    let app = app();
    let first = register(&app, json!("first"), "static_bearer", "1.0").await;
    register(&app, json!("taken"), "static_bearer", "1.0").await;
    let path = format!("/providers/{}", first["provider_id"].as_str().unwrap());
    let token = first["provider_admin_token"].as_str();
    assert_eq!(request(&app, "PATCH", &path, token,
        json!({"slug":"taken", "name":"Changed", "protocol_version":"2.0"})).await.0,
        StatusCode::CONFLICT);
    let (_, before) = request(&app, "GET", "/providers/by-slug/first", None, Value::Null).await;
    assert_eq!(before["name"], "Coding Provider");
    assert_eq!(before["protocol_version"], "1.0");
    let (status, after) = request(&app, "PATCH", &path, token, json!({"slug":"renamed"})).await;
    assert_eq!(status, StatusCode::OK, "{after}");
    assert_eq!(after["slug"], "renamed");
    assert_eq!(after["protocol_version"], "1.0");
    assert_eq!(request(&app, "GET", "/providers/by-slug/first", None, Value::Null).await.0,
        StatusCode::NOT_FOUND);
    assert_eq!(request(&app, "GET", "/providers/by-slug/renamed", None, Value::Null).await.0,
        StatusCode::OK);
    let (_, preserved) = request(&app, "PATCH", &path, token, json!({"slug":null})).await;
    assert_eq!(preserved["slug"], "renamed");
    assert_eq!(request(&app, "PATCH", &path, token, json!({"slug":"Invalid"})).await.0,
        StatusCode::BAD_REQUEST);
    assert_eq!(request(&app, "PATCH", &path, None, json!({"slug":"unauthorized"})).await.0,
        StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn disabled_provider_remains_public_and_status_changes_are_immediate() {
    let app = app();
    let registered = register(&app, json!("enabled-test"), "provider_admin", "2.0").await;
    let path = format!("/providers/{}", registered["provider_id"].as_str().unwrap());
    let token = registered["provider_admin_token"].as_str();
    for (operation, enabled) in [("disable", false), ("enable", true)] {
        assert_eq!(request(&app, "POST", &format!("{path}/{operation}"), token, json!({})).await.0,
            StatusCode::OK);
        let (status, info) = request(&app, "GET", "/providers/by-slug/enabled-test",
            None, Value::Null).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(info["enabled"], enabled);
    }
}

#[tokio::test]
async fn legacy_registration_allows_multiple_providers_without_slugs() {
    let app = app();
    for index in 0..2 {
        let (status, registered) = request(&app, "POST", "/providers", None,
            json!({"name":"Legacy", "auth":{"mode":"static_bearer"}})).await;
        assert_eq!(status, StatusCode::OK);
        let path = format!("/providers/{}", registered["provider_id"].as_str().unwrap());
        let (_, info) = request(&app, "GET", &path, registered["provider_admin_token"].as_str(),
            Value::Null).await;
        assert!(info["slug"].is_null());
        assert_eq!(info["protocol_version"], "1.0");
        let (status, _) = request(&app, "PATCH", &path, registered["provider_admin_token"].as_str(),
            json!({"slug":format!("legacy-{index}")})).await;
        assert_eq!(status, StatusCode::OK);
    }
}

#[tokio::test]
async fn invalid_callback_does_not_reserve_a_registration_slug() {
    let app = app();
    let (status, _) = request(&app, "POST", "/providers", None, json!({
        "name":"Invalid callback", "slug":"retryable", "auth":{"mode":"static_bearer"},
        "admin_callback_url":"invalid"
    })).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(request(&app, "GET", "/providers/by-slug/retryable", None, Value::Null).await.0,
        StatusCode::NOT_FOUND);
    register(&app, json!("retryable"), "static_bearer", "1.0").await;
}

#[tokio::test]
async fn invalid_callback_patch_preserves_slug_and_all_metadata() {
    let app = app();
    let registered = register(&app, json!("callback-original"), "static_bearer", "1.0").await;
    let path = format!("/providers/{}", registered["provider_id"].as_str().unwrap());
    let token = registered["provider_admin_token"].as_str();
    let (_, before) = request(&app, "GET", &path, token, Value::Null).await;
    let (status, _) = request(&app, "PATCH", &path, token, json!({
        "slug":"callback-renamed", "name":"Changed", "protocol_version":"2.0",
        "admin_callback_url":"invalid"
    })).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let (_, after) = request(&app, "GET", &path, token, Value::Null).await;
    assert_eq!(after, before);
    assert_eq!(request(&app, "GET", "/providers/by-slug/callback-original", None,
        Value::Null).await.0, StatusCode::OK);
    assert_eq!(request(&app, "GET", "/providers/by-slug/callback-renamed", None,
        Value::Null).await.0, StatusCode::NOT_FOUND);
}
