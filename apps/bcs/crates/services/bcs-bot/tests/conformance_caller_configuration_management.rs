//! Authenticated caller-configuration management conformance (P1.1/P1.4
//! application batch). Real consumers only: the ProviderManagementService
//! methods run against a real ProviderCore registration/auth chain
//! (memory provider store) and a real SQLite-backed BotControlPlaneCore —
//! no stubs for the services under test.

use std::sync::Arc;

use bcs_bot::application::ProviderManagement;
use bcs_bot::core::{BotCore, BotControlPlaneCore, ProviderCore};
use bcs_bot_store::{MemoryBotRepo, PersistentBotRepo};
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_db_api::{DbPlugin, DbStatement, DbValue as Value};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::{
    BotCallerConfiguration, BotControlPlaneRepoPort, CallerConfigurationSync, ProviderRepoPort,
};
use bcs_service_api::{
    BotRegistryCoreService, BotControlPlaneCoreService, ProviderAuthMode, ProviderCoreService,
    ProviderBotBindingRepoPort, ProviderBotCoreService, ProviderCredentialRepoPort,
    ProviderManagementService, RegisterProviderCommand, ServiceError,
    SyncProviderBotCallerConfigurationCommand,
};
use bcs_test_support::NoopRelationCoreService;
use serde_json::json;

fn snapshot(revision: u64, enabled: bool) -> bcs_service_api::port::repo::CallerIdentitySnapshot {
    serde_json::from_value(json!({
        "source_revision": revision,
        "caller_identity": { "enabled": enabled }
    }))
    .expect("valid snapshot")
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
            env TEXT NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0,
            UNIQUE (env, bot_uuid)
        )",
    ))
    .await
    .expect("create bindings table");
    db
}

async fn seed_bot(db: &dyn DbPlugin, bot_id: &str) {
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, bot_info, env) VALUES (?, ?, ?, ?)",
        vec![
            Value::from(bot_id.to_string()),
            Value::from(format!("Bot {bot_id}")),
            Value::from(r#"{"summary":"s"}"#.to_string()),
            Value::from(env),
        ],
    ))
    .await
    .expect("seed bot");
}

async fn seed_binding(db: &dyn DbPlugin, bot_id: &str, provider_id: &str) {
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_provider_bot_bindings (bot_uuid, provider_id, env) VALUES (?, ?, ?)",
        vec![
            Value::from(bot_id.to_string()),
            Value::from(provider_id.to_string()),
            Value::from(env),
        ],
    ))
    .await
    .expect("seed binding");
}

struct Context {
    management: ProviderManagement,
    _unwired: ProviderManagement,
    provider_store: Arc<MemoryProviderStore>,
    provider_id: String,
    admin_token: String,
    db: Arc<dyn DbPlugin>,
}

async fn context() -> Context {
    let provider_store = Arc::new(MemoryProviderStore::new());
    let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(
        tempfile::tempdir().expect("temp dir").path().to_path_buf(),
    ));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_provider_repos(
        bot_repo,
        provider_repo.clone(),
        credentials.clone(),
        bindings.clone(),
    ));
    let provider_core = Arc::new(ProviderCore::new(
        provider_repo,
        credentials,
        bindings,
        registry.clone(),
    ));
    let registered = provider_core
        .register_provider(
            "Caller Config Provider".into(),
            None,
            ProviderAuthMode::StaticBearer,
            "owner".into(),
            None,
            None,
        )
        .await
        .expect("register provider");

    let db = sqlite_bot_db().await;
    let control_plane_repo: Arc<dyn BotControlPlaneRepoPort> =
        Arc::new(PersistentBotRepo::new(db.clone()));
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        control_plane_repo,
        provider_store.clone() as Arc<dyn ProviderRepoPort>,
        provider_store.clone() as Arc<dyn ProviderBotBindingRepoPort>,
    ));
    let provider_bot_core: Arc<dyn ProviderBotCoreService> = provider_core.clone();
    let base = || {
        ProviderManagement::new(
            provider_core.clone(),
            provider_bot_core.clone(),
            registry.clone(),
            Arc::new(NoopRelationCoreService),
        )
    };
    let management = base().with_control_plane(control_plane);
    let provider_id = registered.provider.provider_id.clone();
    let admin_token = registered.provider_admin_token.clone();
    seed_binding(db.as_ref(), "bot-a", &provider_id).await;
    seed_bot(db.as_ref(), "bot-a").await;
    Context {
        management,
        _unwired: base(),
        provider_store,
        provider_id,
        admin_token,
        db,
    }
}

fn command(ctx: &Context, bot_id: &str, revision: u64, enabled: bool) -> SyncProviderBotCallerConfigurationCommand {
    SyncProviderBotCallerConfigurationCommand::new(
        ctx.provider_id.clone(),
        bot_id.to_string(),
        bcs_config::resolve_env_str(),
        snapshot(revision, enabled),
    )
}

#[tokio::test]
async fn capability_query_authenticates_then_projects() {
    let ctx = context().await;
    // Bad credentials never learn capability content.
    assert!(ctx
        .management
        .get_provider_caller_capabilities(&ctx.provider_id, "wrong-token")
        .await
        .is_err());
    // Authenticated + undeclared: None, never "supports caller".
    assert_eq!(
        ctx.management
            .get_provider_caller_capabilities(&ctx.provider_id, &ctx.admin_token)
            .await
            .expect("undeclared read"),
        None
    );
    // Declare and read back through the same chain.
    ctx.provider_store
        .update_provider_metadata(
            &ctx.provider_id,
            None,
            Some(r#"{"caller_capabilities":{"supports_caller":true,"supports_isolation_binding":true,"supports_observe_inject":true,"token_visibility":"runtime_only"}}"#),
            None,
            1_780_000_000_000,
        )
        .await
        .expect("declare");
    let caps = ctx
        .management
        .get_provider_caller_capabilities(&ctx.provider_id, &ctx.admin_token)
        .await
        .expect("declared read")
        .expect("declared");
    assert!(caps.supports_caller && caps.supports_isolation_binding && caps.supports_observe_inject);
}

#[tokio::test]
async fn configuration_sync_and_readback_follow_the_matrix() {
    let ctx = context().await;
    // First effective revision applies.
    let outcome = ctx
        .management
        .sync_provider_bot_caller_configuration(command(&ctx, "bot-a", 7, true), &ctx.admin_token)
        .await
        .expect("sync");
    assert!(matches!(outcome, CallerConfigurationSync::Applied { .. }));
    // Same revision, same content is idempotent.
    let outcome = ctx
        .management
        .sync_provider_bot_caller_configuration(command(&ctx, "bot-a", 7, true), &ctx.admin_token)
        .await
        .expect("sync");
    assert!(matches!(outcome, CallerConfigurationSync::Unchanged { .. }));
    // Authenticated read-back round-trips the stored snapshot.
    let config = ctx
        .management
        .get_provider_bot_caller_configuration(&ctx.provider_id, "bot-a", &bcs_config::resolve_env_str(), &ctx.admin_token)
        .await
        .expect("read");
    assert_eq!(config, BotCallerConfiguration { snapshot: Some(snapshot(7, true)) });
    // Explicit disable at a higher revision applies too.
    let outcome = ctx
        .management
        .sync_provider_bot_caller_configuration(command(&ctx, "bot-a", 8, false), &ctx.admin_token)
        .await
        .expect("sync");
    assert!(matches!(outcome, CallerConfigurationSync::Applied { .. }));
}

#[tokio::test]
async fn out_of_scope_targets_are_closed_errors() {
    let ctx = context().await;
    // Unbound Bot: out of scope, never "unconfigured".
    seed_bot(ctx.db.as_ref(), "bot-other").await;
    let error = ctx
        .management
        .get_provider_bot_caller_configuration(&ctx.provider_id, "bot-other", &bcs_config::resolve_env_str(), &ctx.admin_token)
        .await
        .unwrap_err();
    assert!(matches!(error, ServiceError::InvalidOperation { .. }));
    let error = ctx
        .management
        .sync_provider_bot_caller_configuration(command(&ctx, "bot-other", 1, true), &ctx.admin_token)
        .await
        .unwrap_err();
    assert!(matches!(error, ServiceError::InvalidOperation { .. }));
}

#[tokio::test]
async fn env_mismatch_and_auth_failures_reject_before_any_write() {
    let ctx = context().await;
    let mut bad_env = command(&ctx, "bot-a", 1, true);
    bad_env.env = "not-this-env".to_string();
    assert!(ctx
        .management
        .sync_provider_bot_caller_configuration(bad_env, &ctx.admin_token)
        .await
        .is_err());
    assert!(ctx
        .management
        .sync_provider_bot_caller_configuration(command(&ctx, "bot-a", 1, true), "wrong-token")
        .await
        .is_err());
    // Nothing was written by the rejected attempts.
    let config = ctx
        .management
        .get_provider_bot_caller_configuration(&ctx.provider_id, "bot-a", &bcs_config::resolve_env_str(), &ctx.admin_token)
        .await
        .expect("read untouched target");
    assert_eq!(config, BotCallerConfiguration { snapshot: None });
}

#[tokio::test]
async fn broken_provider_declaration_blocks_sync() {
    let ctx = context().await;
    ctx.provider_store
        .update_provider_metadata(
            &ctx.provider_id,
            None,
            Some(r#"{"caller_capabilities":{"supports_caller":"not-a-bool"}}"#),
            None,
            1_780_000_000_000,
        )
        .await
        .expect("break declaration");
    assert!(ctx
        .management
        .sync_provider_bot_caller_configuration(command(&ctx, "bot-a", 1, true), &ctx.admin_token)
        .await
        .is_err());
}

#[tokio::test]
async fn unwired_control_plane_fails_closed() {
    let ctx = context().await;
    let error = ctx
        ._unwired
        .get_provider_bot_caller_configuration(&ctx.provider_id, "bot-a", &bcs_config::resolve_env_str(), &ctx.admin_token)
        .await
        .unwrap_err();
    assert!(matches!(error, ServiceError::InvalidOperation { .. }));
}