//! Composition entry of the governed historical-ownership cutover
//! (plan Task 17): the `bcs-ownership-migrate` maintenance binary and the
//! integration tests assemble the migration application through THIS
//! module, never through private constructors.
//!
//! Datasource discipline: the config-driven entry loads the datasource
//! through the SAME config chain the BCS server uses
//! (`InfrastructurePlugins::from_config`) and wires Bot + relation repos of
//! the SAME datasource — the migration never mixes stores. The repo-level
//! entry lets tests and local governance runs supply the production memory
//! twin instead; both assemble the same Core + application layers.

use std::sync::Arc;

use bcs_db_api::DbSqlFlavor;
use bcs_bot::application::OwnershipMigration;
use bcs_bot::core::OwnershipMigrationCore;
use bcs_bot_store::PersistentBotRepo;
use bcs_relation_store::DbRelationStore;
use bcs_service_api::application::ownership_migration::OwnershipMigrationService;
use bcs_service_api::port::repo::{BotRepoPort, RelationRepoPort};

use crate::config::BcsConfig;
use crate::plugins::{DbPluginKind, InfrastructurePlugins};

/// Assemble the governed migration application over one same-datasource
/// repo pair (the binary's composition unit, also the test entry).
pub fn ownership_migration_service_with_repos(
    bots: Arc<dyn BotRepoPort>,
    relations: Arc<dyn RelationRepoPort>,
) -> Arc<dyn OwnershipMigrationService> {
    let core: Arc<OwnershipMigrationCore> = Arc::new(OwnershipMigrationCore::new(bots, relations));
    Arc::new(OwnershipMigration::new(core))
}

/// Assemble the governed migration application from the SAME config the
/// BCS server loads (only the configured datasource; no private flags).
pub async fn ownership_migration_service_from_config(
    config: &BcsConfig,
) -> crate::Result<Arc<dyn OwnershipMigrationService>> {
    let plugins = InfrastructurePlugins::from_config(config).await?;
    let db_kind = plugins.db_kind();
    let db = plugins.db().ok_or_else(|| {
        crate::BcsError::StorageInitError(
            "ownership migration requires a configured database datasource".into(),
        )
    })?;
    match db_kind {
        DbPluginKind::LocalSqlite => {
            let bots: Arc<dyn BotRepoPort> = Arc::new(PersistentBotRepo::with_sql_flavor(
                db.clone(),
                DbSqlFlavor::Sqlite,
            ));
            let relations: Arc<dyn RelationRepoPort> = Arc::new(DbRelationStore::sqlite(db.clone()));
            Ok(ownership_migration_service_with_repos(bots, relations))
        }
        DbPluginKind::Mysql => {
            let bots: Arc<dyn BotRepoPort> = Arc::new(PersistentBotRepo::with_sql_flavor(
                db.clone(),
                DbSqlFlavor::Mysql,
            ));
            let relations: Arc<dyn RelationRepoPort> = Arc::new(DbRelationStore::mysql(db.clone()));
            Ok(ownership_migration_service_with_repos(bots, relations))
        }
        DbPluginKind::External(provider) => Err(crate::BcsError::StorageInitError(format!(
            "external database plugin '{provider}' has no ownership migration wiring"
        ))),
    }
}