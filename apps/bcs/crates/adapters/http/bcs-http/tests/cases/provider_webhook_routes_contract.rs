use std::sync::Arc;
use axum::{Router, body::{Body, to_bytes}, http::{Request, StatusCode}};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::{BotControlPlaneCore, BotCore, ProviderCore, ProviderManagement};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_bot_store::provider::MemoryBotProviderStore;
use bcs_http::{router::build_router, state::{ChainUserIdentityPort, HttpAppState}};
use bcs_service_api::{
    BotDeliveryTarget, BotRegistryCoreService, ProviderBotBinding, ProviderBotBindingRepoPort,
    ProviderBotConnectionMode, ProviderBotCoreService, RegisterProviderBotParams,
};
use bcs_services_container::Services;
use bcs_test_support::NoopRelationCoreService;
use serde_json::{json, Value};
use tower::ServiceExt;

struct App {
    router: Router,
    registry: Arc<BotCore>,
    core: Arc<ProviderCore>,
    bindings: Arc<MemoryProviderStore>,
    _dir: tempfile::TempDir,
}

fn app() -> App {
    let dir = tempfile::tempdir().unwrap();
    let store = Arc::new(MemoryProviderStore::new());
    let bots = Arc::new(MemoryBotRepo::with_base_dir(dir.path().to_path_buf()));
    let metadata = Arc::new(MemoryBotProviderStore::new(bots.clone(), store.clone()));
    let control_plane = BotControlPlaneCore::new(bots.clone(), store.clone(), store.clone())
        .with_bot_provider_repo(metadata.clone());
    let registry = Arc::new(BotCore::with_provider_repos(bots,
        store.clone(), store.clone(), store.clone()));
    let core = Arc::new(ProviderCore::new(store.clone(), store.clone(), store.clone(), registry.clone())
        .with_bot_provider_repo(metadata));
    let management = Arc::new(ProviderManagement::new(core.clone(), core.clone(),
        registry.clone(), Arc::new(NoopRelationCoreService)).with_control_plane(Arc::new(control_plane)));
    let services = Services::builder().registry(registry.clone()).provider_core(core.clone())
        .provider_bot_core(core.clone()).provider_management(management).build_for_test();
    let auth = AuthPluginChain::new(vec![Box::new(StaticAuthPlugin::with_principal(
        AuthPrincipal { user_id: Some("alice".into()), ..Default::default() }))]);
    let state = HttpAppState::new(services).with_user_identity(
        Arc::new(ChainUserIdentityPort::new(Arc::new(auth))));
    App { router: build_router(state), registry, core, bindings: store, _dir: dir }
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

async fn bot_detail(app: &App, provider: &Value, query: &str, token: Option<&str>) -> (StatusCode, Value) {
    let id = provider["provider_id"].as_str().unwrap();
    request(app, "GET", &format!("/providers/{id}/bot-detail?{query}"), token, Value::Null).await
}

#[tokio::test]
async fn provider_bot_detail_requires_its_own_admin_and_registration() {
    let app = app();
    let a = provider(&app, Some("https://a.example.com/webhook")).await;
    let b = provider(&app, Some("https://b.example.com/webhook")).await;
    let token = a["provider_admin_token"].as_str();
    let mut ids = Vec::new();
    for p in [&a, &b] {
        let (status, bot) = request(&app, "POST", &bots_path(p), p["provider_admin_token"].as_str(),
            json!({"name":"Reviewer", "summary":"Reviews code", "owners":["alice"], "provider_bot_ref":"shared"})).await;
        assert_eq!(status, StatusCode::OK, "{bot}");
        ids.push(bot["bot_uuid"].as_str().unwrap().to_owned());
    }
    for query in ["provider_bot_ref=shared".to_owned(), format!("bot_uuid={}", ids[0])] {
        let (status, bot) = bot_detail(&app, &a, &query, token).await;
        assert_eq!(status, StatusCode::OK, "{bot}");
        assert_eq!(bot, json!({
            "provider_id": a["provider_id"], "bot_uuid": ids[0], "provider_bot_ref": "shared",
            "created_by": "alice", "name": "Reviewer", "summary": "Reviews code", "visibility": "protected",
            "domains": [], "skills": [], "scopes": [],
        }));
    }
    assert_eq!(bot_detail(&app, &a, &format!("bot_uuid={}", ids[1]), token).await.0, StatusCode::NOT_FOUND);
    let (status, _) = request(&app, "POST", &bots_path(&b), b["provider_admin_token"].as_str(),
        json!({"name":"Foreign", "owners":["alice"], "provider_bot_ref":"foreign-only"})).await;
    assert_eq!(status, StatusCode::OK);
    app.registry.register("unbound".into(), Default::default()).await.unwrap();
    for query in ["bot_uuid=unbound", "provider_bot_ref=unbound", "provider_bot_ref=foreign-only", "provider_bot_ref=absent"] {
        assert_eq!(bot_detail(&app, &a, query, token).await.0, StatusCode::NOT_FOUND);
    }
    assert_eq!(bot_detail(&app, &a, "provider_bot_ref=shared", None).await.0, StatusCode::UNAUTHORIZED);
    assert_eq!(bot_detail(&app, &a, "provider_bot_ref=shared", Some("wrong")).await.0, StatusCode::UNAUTHORIZED);
    assert_eq!(bot_detail(&app, &a, "provider_bot_ref=shared", b["provider_admin_token"].as_str()).await.0, StatusCode::FORBIDDEN);
    for query in ["", "bot_uuid=", "bot_uuid=one&provider_bot_ref=two"] {
        assert_eq!(bot_detail(&app, &a, query, token).await.0, StatusCode::BAD_REQUEST);
    }
    let (status, _) = request(&app, "DELETE", &format!("{}/shared", bots_path(&a)), token, Value::Null).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(bot_detail(&app, &a, "provider_bot_ref=shared", token).await.0, StatusCode::NOT_FOUND);
    assert_eq!(bot_detail(&app, &a, &format!("bot_uuid={}", ids[0]), token).await.0, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn provider_bot_detail_finds_plugin_registration_without_delivery_binding() {
    let app = app();
    let p = provider(&app, None).await;
    let id = p["provider_id"].as_str().unwrap();
    let token = p["provider_admin_token"].as_str().unwrap();
    let (bot, _) = app.core.register_provider_bot_with_bot_uuid(id, token, RegisterProviderBotParams {
        bot_name: "Plugin".into(), owners: vec!["plugin-owner".into()],
        provider_bot_ref: "plugin-ref".into(), connection_mode: ProviderBotConnectionMode::Plugin,
        ..Default::default()
    }).await.unwrap();
    assert!(app.bindings.get_binding_by_bot_uuid(&bot.bot_uuid).await.unwrap().is_none());
    for query in [format!("bot_uuid={}", bot.bot_uuid), "provider_bot_ref=plugin-ref".into()] {
        let (status, detail) = bot_detail(&app, &p, &query, Some(token)).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(detail["bot_uuid"], bot.bot_uuid);
        assert_eq!(detail["created_by"], "plugin-owner");
    }
}

#[tokio::test]
async fn list_provider_bots_returns_each_creator_and_keeps_bindings_without_a_creator() {
    let app = app();
    let p = provider(&app, Some("https://shared.example.com/webhook")).await;
    let path = bots_path(&p);
    let token = p["provider_admin_token"].as_str();
    for (bot_ref, creator) in [("one", "bob"), ("two", "carol")] {
        let (status, bot) = request(&app, "POST", &path, token,
            json!({"name": bot_ref, "owners": [creator], "provider_bot_ref": bot_ref})).await;
        assert_eq!(status, StatusCode::OK, "{bot}");
    }
    app.registry.register("legacy".into(), Default::default()).await.unwrap();
    for id in ["legacy", "missing"] {
        app.bindings.insert_binding(ProviderBotBinding {
            bot_uuid: id.into(),
            provider_id: p["provider_id"].as_str().unwrap().into(),
            provider_bot_ref: id.into(),
            webhook_url: None,
            disabled: false,
            created_at: 1,
            updated_at: 1,
        }).await.unwrap();
    }

    let (status, response) = request(&app, "GET", &path, token, Value::Null).await;
    assert_eq!(status, StatusCode::OK, "{response}");
    let items = response["items"].as_array().unwrap();
    assert_eq!(items.len(), 4);
    for (bot_ref, expected) in [
        ("one", json!("bob")), ("two", json!("carol")),
        ("legacy", Value::Null), ("missing", Value::Null),
    ] {
        let item = items.iter().find(|bot| bot["provider_bot_ref"] == bot_ref).unwrap();
        assert_eq!(item.get("created_by"), Some(&expected), "{item}");
    }
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
