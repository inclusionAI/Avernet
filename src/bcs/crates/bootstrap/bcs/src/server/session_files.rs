//! Session-files service builders + toml/json helpers.
//!
//! Split out from server.rs as part of the V1 API auth plugin chain (Task 1) refactor. Behavior preserved exactly.

use super::*;

/// Build the session-file workspace service for the bootstrap `Services` bundle.
///
/// `db` selects the repo backend:
/// - `Some(db)` → `MySqlSessionFileStore::with_flavor(db, env, db_flavor)`; the
///   flavor MUST accompany `db` (it tells the store which SQL dialect to use
///   when projecting `created_at`/`updated_at` from `gmt_create`/`gmt_modified`).
/// - `None` → `MemorySessionFileRepo::new()` (standalone/dev mode).
///
/// The `env` passed here MUST match the env the repo writes into the `env`
/// column of `bcs_session_files`; the service uses the same env to scope
/// object keys via [`bcs_session_file::authz::derive_key`].
///
/// Share token secret is independent of `invite.token_secret`: if
/// `session_files.share.token_secret` is unset, bootstrap logs a warning and
/// generates a random 32-byte secret that does NOT survive a restart (prod
/// must set it explicitly). Mirrors the invite secret fallback contract.
pub(super) async fn build_session_files_service(
    config: &BcsConfig,
    env: String,
    db: Option<Arc<dyn bcs_db_api::DbPlugin>>,
    db_flavor: Option<DbSqlFlavor>,
    session_repo: Arc<dyn SessionRepoPort>,
) -> Arc<dyn bcs_service_api::application::session_files::SessionFileService> {
    use bcs_service_api::port::repo::SessionFileRepoPort;
    use bcs_session_file::{SessionFileServiceConfig, SessionFileServiceImpl};
    use bcs_session_file_store::{MemorySessionFileRepo, MySqlSessionFileStore};
    use bcs_storage_api::StoragePlugin;
    use bcs_storage_api::factory::{StorageBackendConfig, StoragePluginFactory};
    use bcs_storage_baas::BaasStoragePluginFactory;
    use bcs_storage_local::LocalStoragePluginFactory;

    // Backend-agnostic storage assembly: select a factory by storage_backend,
    // build the plugin from the backend pass-through table. server.rs is
    // otherwise ignorant of the backend roster (adding OSS/NAS later is one
    // factory arm here + its crate). See design-baas-plugin §「落地前置改造」.

    // Prefer the configured external endpoint, then bind:port, mirroring
    // `proposal_base_url` above.
    let bcs_base_url = config
        .bcs_endpoint
        .clone()
        .unwrap_or_else(|| format!("http://{}:{}", config.bind, config.port));

    let factory: Arc<dyn StoragePluginFactory> = match config.session_files.storage_backend.as_str()
    {
        "local" => Arc::new(LocalStoragePluginFactory),
        "baas" => Arc::new(BaasStoragePluginFactory),
        other => panic!("unknown storage_backend '{other}'"),
    };

    let backend_cfg = StorageBackendConfig {
        env: env.clone(),
        max_file_size: config.session_files.max_file_size,
        multipart_threshold: config.session_files.multipart_threshold,
        share_link_ttl: config.session_files.share_link_ttl,
        bcs_base_url: bcs_base_url.clone(),
        bots_base_dir: config.bots_base_dir.display().to_string(),
        backend: toml_table_to_json_map(&config.session_files.backend),
    };
    let storage: Arc<dyn StoragePlugin> = factory
        .build(&backend_cfg)
        .await
        .expect("storage backend build failed at bootstrap");

    let file_repo: Arc<dyn SessionFileRepoPort> = match db {
        Some(db) => {
            let flavor = db_flavor.expect("`db` present implies `db_flavor` present");
            Arc::new(MySqlSessionFileStore::with_flavor(db, env.clone(), flavor))
        }
        None => Arc::new(MemorySessionFileRepo::new()),
    };

    let share_secret = config
        .session_files
        .share
        .token_secret
        .as_deref()
        .map(|s| s.as_bytes().to_vec())
        .unwrap_or_else(|| {
            warn!(
                "session_files.share.token_secret not configured — generating random \
                 32-byte secret (share tokens will not survive restart)"
            );
            (0..32).map(|_| fastrand::u8(..)).collect()
        });

    Arc::new(SessionFileServiceImpl::new(SessionFileServiceConfig {
        storage,
        repo: file_repo,
        session_repo,
        env,
        max_size: config.session_files.max_file_size,
        multipart_threshold: config.session_files.multipart_threshold,
        bcs_base_url,
        share_secret,
        share_default_ttl: config.session_files.share.default_ttl_seconds,
        share_link_ttl: config.session_files.share_link_ttl,
        share_base_url: config.session_files.share.share_base_url.clone(),
    }))
}

/// Blocking bridge for sync entry points (`Default::default()` and
/// `new_with_outbound_url_guards`) that cannot `.await`.  Spawns a
/// dedicated OS thread to hold the temp tokio runtime so this works even
/// when the calling thread already runs a tokio runtime (e.g. tests).
/// The production path (`new_with_infrastructure`) is already async and
/// calls [`build_session_files_service`] directly, without this overhead.
pub(super) fn build_session_files_service_blocking(
    config: &BcsConfig,
    env: String,
    db: Option<Arc<dyn bcs_db_api::DbPlugin>>,
    db_flavor: Option<DbSqlFlavor>,
    session_repo: Arc<dyn SessionRepoPort>,
) -> Arc<dyn bcs_service_api::application::session_files::SessionFileService> {
    std::thread::scope(|s| {
        s.spawn(|| {
            tokio::runtime::Runtime::new()
                .expect("temp runtime for storage build")
                .block_on(build_session_files_service(
                    config,
                    env,
                    db,
                    db_flavor,
                    session_repo,
                ))
        })
        .join()
        .expect("storage build thread panicked")
    })
}

#[allow(clippy::too_many_arguments)]
pub(super) fn build_eventing_runtime_blocking(
    config: &BcsConfig,
    repo: Arc<dyn bcs_service_api::port::repo::EventRepoPort>,
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    collaboration_runtime: Arc<dyn bcs_service_api::CollaborationRuntimeService>,
    registry: Arc<dyn BotRegistryCoreService>,
    allow_local_test_endpoints: bool,
) -> crate::Result<crate::eventing_wiring::EventingRuntime> {
    std::thread::scope(|scope| {
        scope
            .spawn(|| {
                tokio::runtime::Runtime::new()
                    .expect("temp runtime for Eventing build")
                    .block_on(crate::eventing_wiring::build_eventing_runtime(
                        config,
                        repo,
                        groups,
                        sessions,
                        collaboration_runtime,
                        registry,
                        allow_local_test_endpoints,
                    ))
            })
            .join()
            .expect("Eventing build thread panicked")
    })
}

pub(super) fn local_eventing_endpoints_allowed() -> bool {
    matches!(
        crate::config_loader::Environment::resolve(),
        crate::config_loader::Environment::Local | crate::config_loader::Environment::Dev
    )
}

/// Convert a `toml::Table` (config pass-through) into a `serde_json::Map`.
pub(super) fn toml_table_to_json_map(table: &toml::Table) -> serde_json::Map<String, serde_json::Value> {
    let mut out = serde_json::Map::new();
    for (k, v) in table {
        out.insert(k.clone(), toml_value_to_json(v));
    }
    out
}

pub(super) fn toml_value_to_json(v: &toml::Value) -> serde_json::Value {
    match v {
        toml::Value::String(s) => serde_json::Value::String(s.clone()),
        toml::Value::Integer(i) => serde_json::Value::Number((*i).into()),
        toml::Value::Float(f) => serde_json::json!(f),
        toml::Value::Boolean(b) => serde_json::Value::Bool(*b),
        toml::Value::Table(t) => serde_json::Value::Object(toml_table_to_json_map(t)),
        toml::Value::Array(a) => {
            serde_json::Value::Array(a.iter().map(toml_value_to_json).collect())
        }
        toml::Value::Datetime(d) => serde_json::Value::String(d.to_string()),
    }
}

/// Spawn the Pending-sweep background task for the session-file workspace.
///
/// Mirrors the timeout/token-expiry scanner pattern: a tokio interval task
/// that calls `sweep_expired_pending()` every 300s, logs results, and
/// swallows errors so a transient backend hiccup never tears down the loop.
pub(super) fn spawn_session_files_pending_sweep(
    service: Arc<dyn bcs_service_api::application::session_files::SessionFileService>,
) {
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(300));
        interval.tick().await; // consume the immediate first tick
        loop {
            interval.tick().await;
            match service.sweep_expired_pending().await {
                Ok(n) if n > 0 => info!(swept = n, "session file pending sweep"),
                Ok(_) => {}
                Err(e) => warn!(error = ?e, "session file pending sweep error"),
            }
        }
    });
}

pub(super) fn register_eventing_lifecycles(
    lifecycle: &Arc<Mutex<LifecycleOrchestrator>>,
    eventing_lifecycle: Option<&Arc<dyn ServiceLifecycle>>,
    provisioning_lifecycle: Option<&Arc<dyn ServiceLifecycle>>,
) {
    let mut guard = lifecycle
        .try_lock()
        .expect("orchestrator should be uncontended at registration time");
    if let Some(service) = eventing_lifecycle {
        guard.register("eventing", service.clone());
    }
    if let Some(service) = provisioning_lifecycle {
        guard.register("group-provisioning-reconciler", service.clone());
    }
}
