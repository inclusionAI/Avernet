//! Rule 25 driver: the governed historical-ownership migration (plan
//! Task 17/18).
//!
//! Runs the SHARED conformance suites against the PRODUCTION
//! `OwnershipMigration` application and `OwnershipMigrationCore` core,
//! over the REAL SQLite migration chain (`bcs::migrations::run_sqlite_migrations`,
//! the exact schema production startup builds). The application slot is
//! assembled exactly like the `bcs-ownership-migrate` maintenance binary
//! assembles it (`bcs::ownership_migration_wiring`) — the same
//! composition entry — over the same selected-datasource repos.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::DbPlugin;
use bcs_service_api::application::ownership_migration::OwnershipMigrationService;
use bcs_service_api::core::error::ServiceResult;
use bcs_service_api::core::ownership_migration::OwnershipMigrationCoreService;
use bcs_service_api::port::repo::{BotRepoPort, RelationRepoPort};

use bcs_test_support::contract::application::ownership_migration::ownership_migration_service_contract_tests;
use bcs_test_support::contract::core::ownership_migration::{
    OwnershipMigrationCoreHarness, ownership_migration_core_service_contract_tests,
};

#[path = "ownership_migration_common.rs"]
mod support_ownership_migration;
use support_ownership_migration::{
    initialization_audit_count, migrated_sqlite, ownership_version_of, seed_bot, seed_human,
};

const SEED_USER: &str = "migration-conformance-user";

struct SqliteMigrationHarness {
    db: Arc<dyn DbPlugin>,
    core: Arc<dyn OwnershipMigrationCoreService>,
    env: String,
    seeded: std::sync::atomic::AtomicUsize,
}

#[async_trait]
impl OwnershipMigrationCoreHarness for SqliteMigrationHarness {
    fn core(&self) -> &dyn OwnershipMigrationCoreService {
        self.core.as_ref()
    }

    async fn seed_ready_candidate(&self) -> ServiceResult<String> {
        let index = self.seeded.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let bot_id = format!("migration-conformance-bot-{index}");
        let env = self.env.clone();
        if index == 0 {
            seed_human(self.db.as_ref(), SEED_USER, &env).await;
        }
        seed_bot(self.db.as_ref(), &bot_id, Some(SEED_USER), 0, false, "bot", &env).await;
        Ok(bot_id)
    }

    async fn ownership_version(&self, bot_id: &str) -> ServiceResult<u64> {
        Ok(ownership_version_of(self.db.as_ref(), bot_id, &self.env).await as u64)
    }

    async fn initialization_ledger_count(&self, bot_id: &str) -> ServiceResult<u64> {
        Ok(initialization_audit_count(self.db.as_ref(), bot_id, &self.env).await as u64)
    }

    fn env(&self) -> &str {
        &self.env
    }
}

fn migration_bots_repo(db: Arc<dyn DbPlugin>) -> Arc<dyn BotRepoPort> {
    Arc::new(bcs_bot_store::PersistentBotRepo::with_sql_flavor(
        db,
        bcs_db_api::DbSqlFlavor::Sqlite,
    ))
}

fn migration_relations_repo(db: Arc<dyn DbPlugin>) -> Arc<dyn RelationRepoPort> {
    Arc::new(bcs_relation_store::DbRelationStore::sqlite(db))
}

fn migration_core(db: Arc<dyn DbPlugin>) -> Arc<dyn OwnershipMigrationCoreService> {
    Arc::new(bcs_bot::core::OwnershipMigrationCore::new(
        migration_bots_repo(db.clone()),
        migration_relations_repo(db),
    ))
}

#[tokio::test]
async fn conformance_ownership_migration_over_the_real_wiring() {
    let db = migrated_sqlite().await;
    let env = support_ownership_migration::env_str();
    let core = migration_core(db.clone());

    // The PRODUCTION application object over the PRODUCTION core: the
    // same assembly the maintenance binary performs.
    let service: Arc<dyn OwnershipMigrationService> = Arc::new(
        bcs_bot::application::OwnershipMigration::new(core.clone()),
    );

    // Application contract: the machine-stable argument guards fire as
    // InvalidOperation rejections on the REAL application object — never
    // a silent clamp.
    ownership_migration_service_contract_tests(service.as_ref()).await;

    // Core contract: dry-run keyset pages, the governed bounded batch
    // apply (version 1, single ledger row per Bot) and the same-batch
    // replay semantics, all against the REAL library reads/writes.
    let harness = SqliteMigrationHarness {
        db,
        core,
        env,
        seeded: std::sync::atomic::AtomicUsize::new(0),
    };
    ownership_migration_core_service_contract_tests(&harness).await;
}