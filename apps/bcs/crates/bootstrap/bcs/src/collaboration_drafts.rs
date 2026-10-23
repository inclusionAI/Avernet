//! Drafts are opt-in and persist their per-revision read grants in the database.
use bcs_collaboration_store::draft::DbCollaborationDraftStore;
use bcs_service_api::application::{
    collaboration_draft::CollaborationDraftService,
    collaboration_runtime::CollaborationRuntimeService,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct CollaborationDraftConfig {
    pub enabled: bool,
}

pub fn build(
    config: &crate::config::BcsConfig,
    db: Arc<dyn bcs_db_api::DbPlugin>,
    validator: Arc<dyn CollaborationRuntimeService>,
) -> Option<Arc<dyn CollaborationDraftService>> {
    if !config.collaboration_drafts.as_ref().is_some_and(|settings| settings.enabled) {
        return None;
    }
    let repo = Arc::new(DbCollaborationDraftStore::new(db));
    let core = Arc::new(bcs_collaboration_draft::CollaborationDraftCore::new(repo));
    Some(Arc::new(bcs_app_collaboration_draft::CollaborationDraftApplication::new(
        core, validator, config.llm.is_enabled(),
    )))
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_collaboration_store::MemoryCollaborationStore;
    use bcs_service_api::{
        application::collaboration_draft::{DraftAccess, DraftContent},
        JudgeDecision, JudgeEvaluatorPort, JudgeRequest, ServiceResult,
    };
    use std::path::Path;

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

    #[test]
    fn configuration_has_only_the_enabled_switch() {
        assert!(!CollaborationDraftConfig::default().enabled);
        for input in ["enabled = 'false'", "enabeld = true", "deployment_id = 'old'",
            "active_key_id = 'key'", "key_files = { key = 'missing.hex' }"]
        {
            assert!(toml::from_str::<CollaborationDraftConfig>(input).is_err());
        }
        for enabled in [false, true] {
            let config: CollaborationDraftConfig = toml::from_str(&format!("enabled = {enabled}")).unwrap();
            assert_eq!(serde_json::to_value(config).unwrap(), serde_json::json!({"enabled": enabled}));
        }
    }

    #[test]
    fn both_database_types_use_only_the_enabled_switch_without_startup_io() {
        // No schema or database file: construction must never read storage or key files.
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        for database_type in [crate::config::DatabaseType::Sqlite, crate::config::DatabaseType::Mysql] {
            let mut config = crate::config::BcsConfig::default();
            config.database.database_type = database_type;
            assert!(build(&config, db.clone(), validator()).is_none());
            config.collaboration_drafts = Some(CollaborationDraftConfig::default());
            assert!(build(&config, db.clone(), validator()).is_none());
            config.collaboration_drafts.as_mut().unwrap().enabled = true;
            assert!(build(&config, db.clone(), validator()).is_some());
        }
    }

    #[test]
    fn checked_in_local_config_enables_drafts_while_example_stays_disabled() {
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        for (source, enabled) in [
            (include_str!("../../../../configs/bcs-config-local.toml"), true),
            (include_str!("../../../../configs/bcs-config-example.toml"), false),
        ] {
            let mut value = crate::config_loader::parse_config_content("toml", source, "checked-in config").unwrap();
            crate::config_loader::expand_env_vars(&mut value).unwrap();
            let config: crate::config::BcsConfig = serde_json::from_value(value).unwrap();
            assert_eq!(build(&config, db.clone(), validator()).is_some(), enabled);
        }
    }

    #[tokio::test]
    async fn restart_and_disable_preserve_grants_without_key_files() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bcs.db");
        let mut config = crate::config::BcsConfig::default();
        config.collaboration_drafts = Some(CollaborationDraftConfig { enabled: true });
        config.database.sqlite.path = path.display().to_string();
        let db = database(&path).await;
        let service = build(&config, db.clone(), validator()).unwrap();
        let id = uuid::Uuid::new_v4().to_string();
        service.create("owner", &id, DraftContent {
            definition_yaml: "原文\r\n😀\n".into(), title: None,
        }).await.unwrap();
        let reference = service.reference("owner", &id, 1).await.unwrap();
        assert!(!path.with_extension("draft-keys.json").exists());
        drop(service);
        drop(db);
        // Leftover files from the removed mechanism are neither read nor deleted.
        let old_file = path.with_extension("draft-keys.json");
        std::fs::write(&old_file, "obsolete").unwrap();
        let db = database(&path).await;
        config.collaboration_drafts.as_mut().unwrap().enabled = false;
        assert!(build(&config, db.clone(), validator()).is_none());
        config.collaboration_drafts.as_mut().unwrap().enabled = true;
        let service = build(&config, db.clone(), validator()).unwrap();
        let replica = build(&config, db, validator()).unwrap();
        assert_eq!(replica.reference("owner", &id, 1).await.unwrap().read_token, reference.read_token);
        assert_eq!(service.read(DraftAccess::ReadToken(reference.read_token.clone()), &id, 1)
            .await.unwrap().definition_yaml, "原文\r\n😀\n");
        replica.revoke("owner", &id, 1).await.unwrap();
        assert!(service.read(DraftAccess::ReadToken(reference.read_token), &id, 1).await.is_err());
        assert_eq!(std::fs::read_to_string(old_file).unwrap(), "obsolete");
    }

    #[tokio::test]
    async fn storage_write_failure_is_reported() {
        let mut config = crate::config::BcsConfig::default();
        config.collaboration_drafts = Some(CollaborationDraftConfig { enabled: true });
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        let service = build(&config, db, validator()).unwrap();
        let result = service.create("owner", &uuid::Uuid::new_v4().to_string(), DraftContent {
            definition_yaml: "".into(), title: None,
        }).await;
        assert!(matches!(result, Err(bcs_service_api::application::collaboration_draft::DraftError::ServiceUnavailable)));
    }
}
