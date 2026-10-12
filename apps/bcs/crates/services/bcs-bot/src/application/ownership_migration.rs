//! Historical ownership migration application (plan Task 17).
//!
//! The application is the governed entry the maintenance binary uses. It
//! owns the argument bounds (batch page size, keyset anchor, batch id
//! checks) and delegates every storage decision to the migration Core —
//! this layer holds no repo and no store, per the layered call rules: the
//! per-Bot initialization runs through the Task 5 governed atomic lane
//! inside the Core, and the ledger batch audit is recorded by that same
//! lane.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::ownership_migration::{
    OwnershipMigrationService, validate_migration_batch, validate_migration_request,
};
use bcs_service_api::core::ownership_migration::OwnershipMigrationCoreService;
use bcs_service_api::types::bot_authority::{OwnershipCandidatePage, OwnershipMigrationReport};
use bcs_service_api::ServiceResult;

/// Governed migration use-case facade over the migration Core.
pub struct OwnershipMigration {
    core: Arc<dyn OwnershipMigrationCoreService>,
}

impl OwnershipMigration {
    /// Assemble the application over the migration Core.
    pub fn new(core: Arc<dyn OwnershipMigrationCoreService>) -> Self {
        Self { core }
    }
}

#[async_trait]
impl OwnershipMigrationService for OwnershipMigration {
    async fn inspect_batch(
        &self,
        after_bot_id: Option<String>,
        limit: u32,
    ) -> ServiceResult<OwnershipCandidatePage> {
        validate_migration_request(after_bot_id.as_deref(), limit)?;
        self.core.inspect_batch(after_bot_id, limit).await
    }

    async fn initialize_batch(
        &self,
        confirmed_candidate_ids: Vec<String>,
        batch_id: String,
    ) -> ServiceResult<OwnershipMigrationReport> {
        validate_migration_batch(&confirmed_candidate_ids, &batch_id)?;
        // The Core re-validates its bounds too (fail-closed against any
        // future caller); the application passes the request through.
        self.core
            .initialize_batch(confirmed_candidate_ids, batch_id)
            .await
    }
}