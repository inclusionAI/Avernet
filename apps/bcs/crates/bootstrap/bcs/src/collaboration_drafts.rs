//! Drafts are opt-in. When enabled, signing material is either explicitly
//! supplied or initialized once beside the SQLite database.
use bcs_collaboration_draft::DraftSigningKeys;
use bcs_collaboration_store::draft::DbCollaborationDraftStore;
use bcs_service_api::application::{
    collaboration_draft::CollaborationDraftService,
    collaboration_runtime::CollaborationRuntimeService,
};
use rand::{RngCore, rngs::OsRng};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, io::Write, path::{Path, PathBuf}, sync::Arc};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct CollaborationDraftConfig {
    /// Disabled by default; no signing material or Draft storage is accessed.
    pub enabled: bool,
    pub deployment_id: String,
    pub active_key_id: String,
    /// Shared deployment override: private files with at least 32 hex-encoded
    /// bytes. Retain historical keys during rotation and across rollback.
    pub key_files: BTreeMap<String, PathBuf>,
}

// Kept in a private file, separate from both generated config and business DB.
// Deliberately no Debug implementation: key material must not reach logs.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PersistentSigningKeys {
    deployment_id: String,
    active_key_id: String,
    keys: BTreeMap<String, String>,
}

impl PersistentSigningKeys {
    fn signing(&self) -> Result<DraftSigningKeys, String> {
        if self.deployment_id.chars().count() > 120 {
            return Err("invalid draft deployment identity".into());
        }
        let keys = self.keys.iter().map(|(id, encoded)| {
            hex::decode(encoded.trim())
                .map(|key| (id.clone(), key))
                .map_err(|_| "invalid draft signing key encoding".to_string())
        }).collect::<Result<_, _>>()?;
        DraftSigningKeys::new(self.deployment_id.clone(), self.active_key_id.clone(), keys)
            .map_err(|_| "invalid draft signing key configuration".into())
    }
}

fn read_private(path: &Path) -> Result<String, String> {
    let metadata = std::fs::metadata(path)
        .map_err(|_| "cannot read draft signing key file; restore the original signing material")?;
    if !metadata.is_file() {
        return Err("draft signing keys must be stored in a private file".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err("draft signing key file must be private (mode 0600)".into());
        }
    }
    std::fs::read_to_string(path).map_err(|_| "cannot read draft signing key file".into())
}

fn default_key_path(config: &crate::config::BcsConfig) -> Result<PathBuf, String> {
    if config.database.database_type != crate::config::DatabaseType::Sqlite
        || config.database.sqlite.path == ":memory:"
    {
        return Err("Drafts require persistent signing keys; configure collaboration_drafts \
            with shared keys for a non-SQLite deployment".into());
    }
    let database = crate::plugins::resolve_sqlite_path(config);
    let database = std::fs::canonicalize(database)
        .map_err(|_| "cannot locate the persistent SQLite database for draft signing keys")?;
    Ok(database.with_extension("draft-keys.json"))
}

async fn resolve_keys(
    config: &crate::config::BcsConfig,
    settings: &CollaborationDraftConfig,
    db: &dyn bcs_db_api::DbPlugin,
) -> Result<PersistentSigningKeys, String> {
    if !settings.deployment_id.is_empty() || !settings.active_key_id.is_empty()
        || !settings.key_files.is_empty()
    {
        if settings.deployment_id.trim().is_empty() || settings.active_key_id.trim().is_empty()
            || settings.key_files.is_empty()
        {
            return Err("draft signing overrides require deployment_id, active_key_id and key_files".into());
        }
        let keys = settings.key_files.iter().map(|(id, path)| {
            read_private(path).map(|value| (id.clone(), value))
        }).collect::<Result<_, _>>()?;
        return Ok(PersistentSigningKeys {
            deployment_id: settings.deployment_id.clone(),
            active_key_id: settings.active_key_id.clone(),
            keys,
        });
    }
    let path = default_key_path(config)?;
    // try_exists propagates permissions/I/O failures, not just missing files.
    if !path.try_exists().map_err(|_| "cannot inspect draft signing key file")? {
        if DbCollaborationDraftStore::has_persisted_drafts(db).await
            .map_err(|_| "cannot check existing drafts before initializing signing keys")?
            && !path.try_exists().map_err(|_| "cannot inspect draft signing key file")?
        {
            return Err("draft signing keys are missing for existing drafts; restore the \
                original signing file or configure collaboration_drafts with the original keys".into());
        }
        let mut key = [0u8; 32];
        OsRng.try_fill_bytes(&mut key).map_err(|_| "cannot generate draft signing key")?;
        let initial = PersistentSigningKeys {
            deployment_id: uuid::Uuid::new_v4().to_string(),
            active_key_id: "primary".into(),
            keys: BTreeMap::from([("primary".into(), hex::encode(key))]),
        };
        let parent = path.parent().ok_or("draft signing file has no parent")?;
        // tempfile creates mode 0600. Publish atomically without replacing a
        // concurrent initializer; every process then reads the winning file.
        let mut temporary = tempfile::NamedTempFile::new_in(parent)
            .map_err(|_| "cannot create persistent draft signing file")?;
        let bytes = serde_json::to_vec(&initial).map_err(|_| "cannot encode draft signing file")?;
        temporary.write_all(&bytes).and_then(|_| temporary.as_file().sync_all())
            .map_err(|_| "cannot persist draft signing file")?;
        match temporary.persist_noclobber(&path) {
            Ok(_) => {
                #[cfg(unix)]
                std::fs::File::open(parent).and_then(|dir| dir.sync_all())
                    .map_err(|_| "cannot sync draft signing directory")?;
            }
            Err(error) if error.error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(_) => return Err("cannot publish persistent draft signing file".into()),
        }
    }
    serde_json::from_str(&read_private(&path)?)
        .map_err(|_| "invalid persistent draft signing file; restore the original file".into())
}

pub async fn build(
    config: &crate::config::BcsConfig,
    db: Arc<dyn bcs_db_api::DbPlugin>,
    validator: Arc<dyn CollaborationRuntimeService>,
) -> Result<Option<Arc<dyn CollaborationDraftService>>, String> {
    let Some(settings) = config.collaboration_drafts.as_ref().filter(|settings| settings.enabled) else {
        return Ok(None);
    };
    let keys = resolve_keys(config, settings, db.as_ref()).await?;
    let signing = keys.signing()?;
    let repo = Arc::new(DbCollaborationDraftStore::new(db, keys.deployment_id));
    let core = Arc::new(bcs_collaboration_draft::CollaborationDraftCore::new(repo, signing));
    Ok(Some(Arc::new(bcs_app_collaboration_draft::CollaborationDraftApplication::new(
        core, validator, config.llm.is_enabled(),
    ))))
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_collaboration_store::MemoryCollaborationStore;
    use bcs_service_api::{
        application::collaboration_draft::{DraftAccess, DraftContent},
        JudgeDecision, JudgeEvaluatorPort, JudgeRequest, ServiceResult,
    };

    struct UnusedJudge;
    #[async_trait::async_trait]
    impl JudgeEvaluatorPort for UnusedJudge {
        async fn judge(&self, _: JudgeRequest) -> ServiceResult<JudgeDecision> {
            panic!("configuration must not execute a workflow")
        }
    }

    fn validator() -> Arc<dyn CollaborationRuntimeService> {
        let services = bcs_services_container::Services::builder().build_for_test();
        let memory = Arc::new(MemoryCollaborationStore::new());
        Arc::new(bcs_collaboration_runtime::CollaborationRuntime::new(
            memory.clone(), memory.clone(), memory.clone(), memory,
            services.group, services.session_management, services.bot_delivery,
            Arc::new(UnusedJudge),
        ))
    }

    async fn database(path: &Path) -> Arc<dyn bcs_db_api::DbPlugin> {
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new_file(path).unwrap());
        crate::migrations::run_sqlite_migrations_with_report(db.as_ref()).await.unwrap();
        db
    }

    #[tokio::test]
    async fn disabled_config_skips_draft_storage_and_signing_initialization() {
        let dir = tempfile::tempdir().unwrap();
        // No migrations: any attempt to inspect Draft storage would fail.
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        for database_type in [crate::config::DatabaseType::Sqlite, crate::config::DatabaseType::Mysql] {
            for section in [None, Some(""), Some("enabled = false"),
                Some("deployment_id = 'old-deployment'\nactive_key_id = 'old-key'\nkey_files = { old-key = 'missing.hex' }"),
                Some("enabled = false\ndeployment_id = 'incomplete-override'\nkey_files = { missing = 'missing.hex' }"),
            ] {
                let mut value = serde_json::to_value(crate::config::BcsConfig::default()).unwrap();
                value.as_object_mut().unwrap().remove("collaboration_drafts");
                if let Some(section) = section {
                    value["collaboration_drafts"] = serde_json::to_value(
                        toml::from_str::<toml::Value>(section).unwrap(),
                    ).unwrap();
                }
                let mut config: crate::config::BcsConfig = serde_json::from_value(value).unwrap();
                config.database.database_type = database_type.clone();
                // No database file exists, so automatic key initialization would also fail.
                config.database.sqlite.path = dir.path().join("bcs.db").display().to_string();
                assert!(build(&config, db.clone(), validator()).await.unwrap().is_none());
                assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 0);
            }
        }
    }

    #[test]
    fn config_requires_boolean_enabled_and_rejects_unknown_fields() {
        assert!(!CollaborationDraftConfig::default().enabled);
        for input in ["enabled = 'false'", "enabled = false\nenabeld = true"] {
            assert!(toml::from_str::<CollaborationDraftConfig>(input).is_err());
        }
        let settings: CollaborationDraftConfig = toml::from_str("enabled = true").unwrap();
        let encoded = serde_json::to_value(settings).unwrap();
        assert!(serde_json::from_value::<CollaborationDraftConfig>(encoded).unwrap().enabled);
    }

    #[tokio::test]
    async fn checked_in_local_config_enables_drafts_while_example_stays_disabled() {
        for (source, enabled) in [
            (include_str!("../../../../configs/bcs-config-local.toml"), true),
            (include_str!("../../../../configs/bcs-config-example.toml"), false),
        ] {
            let mut config: crate::config::BcsConfig = toml::from_str(source).unwrap();
            assert_eq!(config.collaboration_drafts.as_ref().unwrap().enabled, enabled);
            assert_eq!(config.database.database_type, if enabled {
                crate::config::DatabaseType::Sqlite
            } else {
                crate::config::DatabaseType::Mysql
            });
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("bcs.db");
            config.database.sqlite.path = path.display().to_string();
            let db = if enabled {
                database(&path).await
            } else {
                Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap())
            };
            assert_eq!(build(&config, db, validator()).await.unwrap().is_some(), enabled);
            assert_eq!(path.with_extension("draft-keys.json").exists(), enabled);
        }
    }

    #[tokio::test]
    async fn enabled_config_rejects_partial_signing_overrides() {
        let dir = tempfile::tempdir().unwrap();
        let mut config = crate::config::BcsConfig::default();
        config.database.sqlite.path = dir.path().join("bcs.db").display().to_string();
        let db = database(Path::new(&config.database.sqlite.path)).await;
        for field in ["deployment_id = 'custom'", "active_key_id = 'key'", "key_files = { key = 'missing.hex' }"] {
            config.collaboration_drafts = Some(toml::from_str(&format!("enabled = true\n{field}")).unwrap());
            let error = build(&config, db.clone(), validator()).await.err().unwrap();
            assert!(error.contains("overrides require"));
            assert!(!default_key_path(&config).unwrap().exists());
        }
    }

    #[tokio::test]
    async fn enabled_drafts_survive_restart_and_reject_lost_signing_material() {
        let dir = tempfile::tempdir().unwrap();
        let mut config = crate::config::BcsConfig::default();
        config.collaboration_drafts = Some(toml::from_str("enabled = true").unwrap());
        config.database.sqlite.path = dir.path().join("bcs.db").display().to_string();
        let db = database(Path::new(&config.database.sqlite.path)).await;
        let service = build(&config, db.clone(), validator()).await.unwrap().unwrap();
        let id = uuid::Uuid::new_v4().to_string();
        service.create("owner", &id, DraftContent {
            definition_yaml: "name: 原文\n".into(), title: None,
        }).await.unwrap();
        let reference = service.reference("owner", &id, 1).await.unwrap();
        let path = default_key_path(&config).unwrap();
        let original = std::fs::read(&path).unwrap();
        drop(service);
        drop(db);
        let db = database(Path::new(&config.database.sqlite.path)).await;
        config.collaboration_drafts.as_mut().unwrap().enabled = false;
        assert!(build(&config, db.clone(), validator()).await.unwrap().is_none());
        assert_eq!(std::fs::read(&path).unwrap(), original);
        config.collaboration_drafts.as_mut().unwrap().enabled = true;
        let service = build(&config, db.clone(), validator()).await.unwrap().unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), original);
        assert_eq!(service.reference("owner", &id, 1).await.unwrap().read_token, reference.read_token);
        assert_eq!(service.read(DraftAccess::ReadToken(reference.read_token), &id, 1)
            .await.unwrap().definition_yaml, "name: 原文\n");
        std::fs::write(&path, "broken").unwrap();
        assert!(build(&config, db.clone(), validator()).await.is_err());
        config.collaboration_drafts.as_mut().unwrap().enabled = false;
        assert!(build(&config, db.clone(), validator()).await.unwrap().is_none());
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "broken");
        config.collaboration_drafts.as_mut().unwrap().enabled = true;
        std::fs::remove_file(&path).unwrap();
        assert!(build(&config, db, validator()).await.err().unwrap().contains("existing drafts"));
        assert!(!path.exists());
    }

    #[tokio::test]
    async fn concurrent_initialization_uses_one_private_persistent_key() {
        let dir = tempfile::tempdir().unwrap();
        let mut config = crate::config::BcsConfig::default();
        config.collaboration_drafts = Some(toml::from_str("enabled = true").unwrap());
        config.database.sqlite.path = dir.path().join("bcs.db").display().to_string();
        let db = database(Path::new(&config.database.sqlite.path)).await;
        let (a, b) = tokio::join!(
            build(&config, db.clone(), validator()),
            build(&config, db.clone(), validator()),
        );
        let (a, b) = (a.unwrap().unwrap(), b.unwrap().unwrap());
        let id = uuid::Uuid::new_v4().to_string();
        a.create("owner", &id, DraftContent { definition_yaml: "".into(), title: None }).await.unwrap();
        assert_eq!(a.reference("owner", &id, 1).await.unwrap().read_token,
            b.reference("owner", &id, 1).await.unwrap().read_token);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let path = default_key_path(&config).unwrap();
            assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
            assert!(build(&config, db, validator()).await.is_err());
        }
    }

    #[tokio::test]
    async fn shared_deployments_require_valid_explicit_keys() {
        let mut config = crate::config::BcsConfig::default();
        config.collaboration_drafts = Some(toml::from_str("enabled = true").unwrap());
        config.database.database_type = crate::config::DatabaseType::Mysql;
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        assert!(build(&config, db.clone(), validator()).await.err().unwrap().contains("shared keys"));
        let key = tempfile::NamedTempFile::new().unwrap();
        std::fs::write(key.path(), hex::encode([7u8; 32])).unwrap();
        config.collaboration_drafts = Some(CollaborationDraftConfig {
            enabled: true,
            deployment_id: "shared-deployment".into(),
            active_key_id: "key".into(),
            key_files: BTreeMap::from([("key".into(), key.path().into())]),
        });
        assert!(build(&config, db.clone(), validator()).await.unwrap().is_some());
        config.collaboration_drafts.as_mut().unwrap().active_key_id = "missing".into();
        assert!(build(&config, db.clone(), validator()).await.is_err());
        config.collaboration_drafts.as_mut().unwrap().active_key_id = "key".into();
        std::fs::write(key.path(), "too-short").unwrap();
        assert!(build(&config, db.clone(), validator()).await.is_err());
        std::fs::write(key.path(), hex::encode([7u8; 32])).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(key.path(), std::fs::Permissions::from_mode(0o644)).unwrap();
            assert!(build(&config, db, validator()).await.is_err());
        }
    }

    #[tokio::test]
    async fn failed_storage_check_does_not_create_signing_material() {
        let dir = tempfile::tempdir().unwrap();
        let mut config = crate::config::BcsConfig::default();
        config.collaboration_drafts = Some(toml::from_str("enabled = true").unwrap());
        let path = dir.path().join("bcs.db");
        config.database.sqlite.path = path.display().to_string();
        // A real database with absent migrations must fail instead of assuming
        // it is a new deployment and creating an unrelated identity.
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new_file(&path).unwrap());
        let result = build(&config, db, validator()).await;
        assert!(result.err().unwrap().contains("cannot check existing drafts"));
        assert!(!default_key_path(&config).unwrap().exists());
    }
}
