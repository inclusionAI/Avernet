//! Route-level contract tests for the Provider caller-configuration
//! sub-resource (`GET/PATCH /providers/{provider_id}/bots/{ref}/caller`,
//! spec §10.7.2). Full real chain: HTTP router → ProviderManagementService
//! (real ProviderCore registration/auth) → BotControlPlaneCoreService → a
//! real SQLite-backed BotControlPlaneRepoPort. No stub on any service under
//! test.

use std::sync::Arc;

use axum::{
    Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::{Bot, BotControlPlaneCore, BotCore, ProviderCore, ProviderManagement};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore, PersistentBotRepo};
use bcs_config::resolve_env_str;
use bcs_db_api::{DbPlugin, DbStatement, DbValue as DbParam};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState, UserIdentityPort},
};
use bcs_services_container::Services;
use bcs_service_api::port::repo::BotControlPlaneRepoPort;
use bcs_service_api::{
    BotRegistryCoreService, BotControlPlaneCoreService, ProviderBotBinding,
    ProviderBotBindingRepoPort, ProviderBotCoreService, ProviderCoreService,
    ProviderCredentialRepoPort, ProviderRepoPort,
};
use serde_json::{Value, json};
use tower::ServiceExt;

async fn response_json(response: axum::response::Response) -> Value {
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    serde_json::from_slice(&body).unwrap()
}

async fn sqlite_bot_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            bot_uuid TEXT NOT NULL,
            name TEXT NOT NULL,
            bot_info TEXT,
            env TEXT,
            actor_kind TEXT NOT NULL DEFAULT 'bot',
            status TEXT NOT NULL DEFAULT 'online',
            is_deleted INTEGER NOT NULL DEFAULT 0,
            UNIQUE (bot_uuid, env)
        )",
    ))
    .await
    .expect("create bots table");
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_provider_bot_bindings (
            bot_uuid TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            provider_bot_ref TEXT NOT NULL,
            env TEXT NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0,
            webhook_url TEXT,
            UNIQUE (env, bot_uuid),
            UNIQUE (env, provider_id, provider_bot_ref)
        )",
    ))
    .await
    .expect("create bindings table");
    db
}

/// Seed one canonical scoped Bot: the sqlite row backs the control-plane
/// storage CAS; the same binding goes into the provider store that resolves
/// runtime refs. Both describe one truth, never two authorities.
async fn seed_scoped_bot(
    db: &dyn DbPlugin,
    bindings: &Arc<dyn ProviderBotBindingRepoPort>,
    bot_id: &str,
    provider_bot_ref: &str,
    provider_id: &str,
    disabled: bool,
) {
    let env = resolve_env_str();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, bot_info, env) VALUES (?, ?, ?, ?)",
        vec![
            DbParam::from(bot_id.to_string()),
            DbParam::from(format!("Bot {bot_id}")),
            DbParam::from(r#"{"summary":"s"}"#.to_string()),
            DbParam::from(env.clone()),
        ],
    ))
    .await
    .expect("seed bot row");
        db.execute(DbStatement::with_params(
        "INSERT INTO bcs_provider_bot_bindings \
         (bot_uuid, provider_id, provider_bot_ref, env, disabled) VALUES (?, ?, ?, ?, ?)",
        vec![
            DbParam::from(bot_id.to_string()),
            DbParam::from(provider_id.to_string()),
            DbParam::from(provider_bot_ref.to_string()),
            DbParam::from(env),
            DbParam::from(i64::from(disabled)),
        ],
    ))
    .await
    .expect("seed sqlite binding");
    bindings
        .insert_binding(ProviderBotBinding {
            bot_uuid: bot_id.to_string(),
            webhook_url: None,
            provider_id: provider_id.to_string(),
            provider_bot_ref: provider_bot_ref.to_string(),
            disabled,
            created_at: 0,
            updated_at: 0,
        })
        .await
        .expect("seed provider-store binding");
}

struct CallerApp {
    app: Router,
    db: Arc<dyn DbPlugin>,
    bindings: Arc<dyn ProviderBotBindingRepoPort>,
}

async fn caller_app() -> CallerApp {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let registry_bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    let registry = Arc::new(BotCore::with_provider_repos(
        registry_bot_repo,
        provider_repo.clone(),
        credentials.clone(),
        bindings.clone(),
    ));
    let registry_service: Arc<dyn BotRegistryCoreService> = registry.clone();
    let provider_core = Arc::new(ProviderCore::new(
        provider_repo,
        credentials,
        bindings.clone(),
        registry_service.clone(),
    ));
    let provider_core_service: Arc<dyn ProviderCoreService> = provider_core.clone();
    let provider_bot_core: Arc<dyn ProviderBotCoreService> = provider_core.clone();
    let db = sqlite_bot_db().await;
    let control_plane_repo: Arc<dyn BotControlPlaneRepoPort> =
        Arc::new(PersistentBotRepo::new(db.clone()));
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        control_plane_repo,
        provider_store.clone() as Arc<dyn ProviderRepoPort>,
        provider_store.clone() as Arc<dyn ProviderBotBindingRepoPort>,
    ));
    let provider_management = Arc::new(
        ProviderManagement::new(
            provider_core_service.clone(),
            provider_bot_core.clone(),
            registry_service.clone(),
            Arc::new(bcs_test_support::NoopRelationCoreService),
        )
        .with_control_plane(control_plane),
    );
    let services = Services::builder()
        .bot_query(Arc::new(Bot::new(registry_service.clone())))
        .registry(registry_service.clone())
        .provider_core(provider_core_service)
        .provider_bot_core(provider_bot_core)
        .provider_management(provider_management)
        .build_for_test();
    let principal = AuthPrincipal {
        user_id: Some("11111111".to_string()),
        user_name: Some("Admin".to_string()),
        ..Default::default()
    };
    let user_identity: Arc<dyn UserIdentityPort> = Arc::new(ChainUserIdentityPort::new(Arc::new(
        AuthPluginChain::new(vec![Box::new(StaticAuthPlugin::with_principal(principal))]),
    )));
    let state = HttpAppState::new(services).with_user_identity(user_identity);
    CallerApp {
        app: build_router(state),
        db,
        bindings,
    }
}

async fn register_provider(app: &Router) -> Value {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/providers")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "name": "Caller Config Provider",
                        "auth": { "mode": "static_bearer" }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    response_json(response).await
}

fn snapshot_body(revision: u64, enabled: bool) -> String {
    json!({
        "snapshot": {
            "source_revision": revision,
            "caller_identity": { "enabled": enabled }
        }
    })
    .to_string()
}

async fn request(
    app: &Router,
    method: &str,
    uri: &str,
    admin_token: &str,
    body: Body,
) -> axum::response::Response {
    app.clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(uri)
                .header("authorization", format!("Bearer {admin_token}"))
                .header("content-type", "application/json")
                .body(body)
                .unwrap(),
        )
        .await
        .unwrap()
}

#[tokio::test]
async fn get_returns_null_snapshot_for_unconfigured_bound_bot() {
    let app = caller_app().await;
    let provider = register_provider(&app.app).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    seed_scoped_bot(app.db.as_ref(), &app.bindings, "bot-route", "runtime-route-x", provider_id, false).await;
    let uri = format!("/providers/{provider_id}/bots/runtime-route-x/caller");

    let response = request(&app.app, "GET", &uri, admin_token, Body::empty()).await;
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response_json(response).await, json!({ "snapshot": null }));

    // Bad credentials are rejected before any config bytes flow.
    let response = request(&app.app, "GET", &uri, "wrong-token", Body::empty()).await;
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn patch_and_get_round_trip_through_the_full_chain() {
    let app = caller_app().await;
    let provider = register_provider(&app.app).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    seed_scoped_bot(app.db.as_ref(), &app.bindings, "bot-route", "runtime-route-x", provider_id, false).await;
    let uri = format!("/providers/{provider_id}/bots/runtime-route-x/caller");

    // Apply.
    let response = request(
        &app.app,
        "PATCH",
        &uri,
        admin_token,
        Body::from(snapshot_body(7, true)),
    ).await;
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["sync_status"], "applied");
    assert_eq!(body["snapshot"]["source_revision"], 7);

    // GET rounds the stored snapshot back out.
    let response = request(&app.app, "GET", &uri, admin_token, Body::empty()).await;
    let body = response_json(response).await;
    assert_eq!(body["snapshot"]["source_revision"], 7);
    assert_eq!(body["snapshot"]["caller_identity"]["enabled"], true);
    assert_eq!(body["snapshot"]["caller_identity"]["minimum_isolation"], "caller_container");

    // Same revision, same content: idempotent.
    let response = request(
        &app.app,
        "PATCH",
        &uri,
        admin_token,
        Body::from(snapshot_body(7, true)),
    ).await;
    assert_eq!(response_json(response).await["sync_status"], "unchanged");

    // Lower revision: stale, with the authoritative snapshot echoed.
    let response = request(
        &app.app,
        "PATCH",
        &uri,
        admin_token,
        Body::from(snapshot_body(6, true)),
    ).await;
    let body = response_json(response).await;
    assert_eq!(body["sync_status"], "stale");
    assert_eq!(body["snapshot"]["source_revision"], 7);

    // Same revision, different content: 409 with the fixed closed code.
    let response = request(
        &app.app,
        "PATCH",
        &uri,
        admin_token,
        Body::from(snapshot_body(7, false)),
    ).await;
    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = response_json(response).await;
    assert_eq!(
        body.get("error")
            .or_else(|| body.get("message"))
            .and_then(Value::as_str),
        Some("caller_configuration_revision_conflict")
    );

    // Higher revision with an explicit disable applies.
    let response = request(
        &app.app,
        "PATCH",
        &uri,
        admin_token,
        Body::from(snapshot_body(8, false)),
    ).await;
    let body = response_json(response).await;
    assert_eq!(body["sync_status"], "applied");
    assert_eq!(body["snapshot"]["caller_identity"]["enabled"], false);
}

#[tokio::test]
async fn unknown_refs_and_disabled_bindings_are_404() {
    let app = caller_app().await;
    let provider = register_provider(&app.app).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    seed_scoped_bot(app.db.as_ref(), &app.bindings, "bot-off", "runtime-off-ref", provider_id, true).await;

    for bot_ref in ["no-such-ref", "runtime-off-ref"] {
        let response = request(
            &app.app,
            "GET",
            &format!("/providers/{provider_id}/bots/{bot_ref}/caller"),
            admin_token,
            Body::empty(),
        ).await;
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{bot_ref}");

        let response = request(
            &app.app,
            "PATCH",
            &format!("/providers/{provider_id}/bots/{bot_ref}/caller"),
            admin_token,
            Body::from(snapshot_body(1, true)),
        ).await;
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{bot_ref}");
    }
}

#[tokio::test]
async fn invalid_bodies_and_credential_smuggles_rejected_before_sync() {
    let app = caller_app().await;
    let provider = register_provider(&app.app).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let admin_token = provider["provider_admin_token"].as_str().unwrap();
    seed_scoped_bot(app.db.as_ref(), &app.bindings, "bot-route", "runtime-route-x", provider_id, false).await;
    let uri = format!("/providers/{provider_id}/bots/runtime-route-x/caller");

    for bad_body in [
        json!({ "source_revision": 1, "caller_identity": { "enabled": true } }),
        json!({ "snapshot": { "source_revision": 1 } }),
        json!({ "snapshot": { "source_revision": 1, "caller_identity": { "enabled": true }, "cookie": "x" } }),
        json!({ "snapshot": { "source_revision": 1, "caller_identity": { "enabled": true } }, "authorization": "Bearer x" }),
        json!({ "snapshot": { "source_revision": "not-a-number", "caller_identity": { "enabled": true } } }),
    ] {
        let response = request(&app.app, "PATCH", &uri, admin_token, Body::from(bad_body.to_string())).await;
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "body: {bad_body}");
    }

    // Nothing was written by the rejected patches.
    let response = request(&app.app, "GET", &uri, admin_token, Body::empty()).await;
    assert_eq!(response_json(response).await, json!({ "snapshot": null }));
}