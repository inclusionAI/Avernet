use std::sync::Arc;
use axum::{Router, body::{Body, to_bytes}, http::{Request, StatusCode}};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::{BotCore, ProviderCore, ProviderManagement};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_http::{router::build_router, state::{ChainUserIdentityPort, HttpAppState}};
use bcs_service_api::{BotDeliveryTarget, BotRegistryCoreService};
use bcs_services_container::Services;
use bcs_test_support::NoopRelationCoreService;
use serde_json::{json, Value};
use tower::ServiceExt;

struct App {
    router: Router,
    registry: Arc<BotCore>,
    _dir: tempfile::TempDir,
}

fn app() -> App {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(MemoryProviderStore::new());
    let registry = Arc::new(BotCore::with_provider_repos(
        Arc::new(MemoryBotRepo::with_base_dir(dir.path().to_path_buf())),
        store.clone(), store.clone(), store.clone()));
    let core = Arc::new(ProviderCore::new(store.clone(), store.clone(), store, registry.clone()));
    let management = Arc::new(ProviderManagement::new(core.clone(), core.clone(),
        registry.clone(), Arc::new(NoopRelationCoreService)));
    let services = Services::builder().registry(registry.clone()).provider_core(core.clone())
        .provider_bot_core(core).provider_management(management).build_for_test();
    let auth = AuthPluginChain::new(vec![Box::new(StaticAuthPlugin::with_principal(
        AuthPrincipal { user_id: Some("alice".into()), ..Default::default() }))]);
    let state = HttpAppState::new(services).with_user_identity(
        Arc::new(ChainUserIdentityPort::new(Arc::new(auth))));
    App { router: build_router(state), registry, _dir: dir }
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
    (status, serde_json::from_slice(&bytes).unwrap())
}

async fn provider(app: &App, endpoint: Option<&str>) -> Value {
    let (status, value) = request(app, "POST", "/providers", None,
        json!({"name":"provider", "webhook_url":endpoint, "auth":{"mode":"static_bearer"}})).await;
    assert_eq!(status, StatusCode::OK, "{value}");
    value
}

fn bots_path(provider: &Value) -> String {
    format!("/providers/{}/bots", provider["provider_id"].as_str().unwrap())
}

#[tokio::test]
async fn bot_url_patch_sets_clears_and_rejects_mixed_writes() {
    let app = app();
    let p = provider(&app, Some("https://shared.example.com/webhook")).await;
    let token = p["provider_admin_token"].as_str();
    let (status, created) = request(&app, "POST", &bots_path(&p), token,
        json!({"name":"a", "owners":["alice"], "provider_bot_ref":"a"})).await;
    assert_eq!(status, StatusCode::OK, "{created}");
    let path = format!("{}/a", bots_path(&p));
    let (status, updated) = request(&app, "PATCH", &path, token,
        json!({"webhook_url":"https://a.example.com/webhook"})).await;
    assert_eq!(status, StatusCode::OK, "{updated}");
    assert_eq!(updated["webhook_url"], "https://a.example.com/webhook");
    assert!(matches!(app.registry.resolve_delivery_target(created["bot_uuid"].as_str().unwrap())
        .await.unwrap(), BotDeliveryTarget::HttpProvider { webhook_url, .. }
        if webhook_url == "https://a.example.com/webhook"));
    let (status, _) = request(&app, "PATCH", &path, token,
        json!({"webhook_url":"https://b.example.com/webhook", "name":"changed"})).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let (_, bots) = request(&app, "GET", &bots_path(&p), token, Value::Null).await;
    assert!(bots.to_string().contains("https://a.example.com/webhook"));
    let (_, cleared) = request(&app, "PATCH", &path, token, json!({"webhook_url":null})).await;
    assert!(cleared["webhook_url"].is_null());
}

#[tokio::test]
async fn provider_without_shared_url_registers_bots_with_independent_urls() {
    let app = app();
    let p = provider(&app, None).await;
    let token = p["provider_admin_token"].as_str();
    let (status, _) = request(&app, "POST", &bots_path(&p), token,
        json!({"name":"a", "owners":["alice"], "provider_bot_ref":"a"})).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let body = json!({"name":"a", "owners":["alice"], "provider_bot_ref":"a",
        "webhook_url":"https://a.example.com/webhook"});
    let (status, created) = request(&app, "POST", &bots_path(&p), token, body.clone()).await;
    assert_eq!(status, StatusCode::OK, "{created}");
    assert_eq!(created["webhook_url"], body["webhook_url"]);
    assert_eq!(request(&app, "POST", &bots_path(&p), token, body.clone()).await.0, StatusCode::OK);
    let mut changed = body;
    changed["webhook_url"] = json!("https://b.example.com/webhook");
    assert_eq!(request(&app, "POST", &bots_path(&p), token, changed).await.0, StatusCode::CONFLICT);
    assert_eq!(request(&app, "PATCH", &format!("{}/a", bots_path(&p)), token,
        json!({"webhook_url":null})).await.0, StatusCode::BAD_REQUEST);
}
