//! Historical ownership migration application contract (plan Task 17).
//!
//! The application is the governed entry the `bcs-ownership-migrate`
//! maintenance binary talks to. It owns argument validation (batch bounds,
//! keyset anchor shape) and result translation; the bounded scan, the
//! conflict governance and the per-Bot Task 5 atomic initialization live in
//! the Core — the application never touches a repo or a store directly.

use async_trait::async_trait;

use crate::types::bot_authority::{OwnershipCandidatePage, OwnershipMigrationReport};
use crate::{ServiceError, ServiceResult};

/// Governed backfill use case of the one-shot authority cutover.
///
/// `inspect_batch` is the maintenance DRY-RUN: it reads current facts and
/// never writes. Human operators confirm candidates out-of-band (a file of
/// confirmed candidate ids); `initialize_batch` is the ONLY method with
/// side effects, and it re-derives every decision from current facts at
/// execution time — a confirm-file entry can never smuggle in an owner.
#[async_trait]
pub trait OwnershipMigrationService: Send + Sync {
    /// One bounded dry-run page of the candidate scan (see
    /// [`OwnershipMigrationCoreService::inspect_batch`](crate::core::OwnershipMigrationCoreService::inspect_batch)).
    ///
    /// Bound contract: `limit` must be 1..=[`MIGRATION_MAX_BATCH_SIZE`]
    /// (blank/oversized requests are rejected, never clamped);
    /// `after_bot_id`, when present, must be non-blank.
    async fn inspect_batch(
        &self,
        after_bot_id: Option<String>,
        limit: u32,
    ) -> ServiceResult<OwnershipCandidatePage>;

    /// Initialize the confirmed candidates of one governed batch (see
    /// [`OwnershipMigrationCoreService::initialize_batch`](crate::core::OwnershipMigrationCoreService::initialize_batch)).
    ///
    /// Bound contract: `batch_id` non-blank; at most
    /// [`MIGRATION_MAX_BATCH_SIZE`] confirmed ids (over-limit rejected).
    /// Any storage failure keeps the committed prefix recoverable by
    /// replaying the same `batch_id`; the maintenance command exits
    /// nonzero whenever the report carries `failed` entries.
    async fn initialize_batch(
        &self,
        confirmed_candidate_ids: Vec<String>,
        batch_id: String,
    ) -> ServiceResult<OwnershipMigrationReport>;
}

/// Shared argument guard of the migration application: every bound is
/// rejected with a machine-stable [`ServiceError::InvalidOperation`], never
/// silently clamped.
pub fn validate_migration_request(
    after_bot_id: Option<&str>,
    limit: u32,
) -> ServiceResult<()> {
    if limit == 0 {
        return Err(ServiceError::InvalidOperation {
            message: "ownership migration page limit must be at least 1".into(),
            request_id: None,
        });
    }
    let max = crate::types::bot_authority::MIGRATION_MAX_BATCH_SIZE as u32;
    if limit > max {
        return Err(ServiceError::InvalidOperation {
            message: format!(
                "ownership migration page limit {limit} exceeds the bounded batch maximum {max}; \
                 page through with after_bot_id instead"
            ),
            request_id: None,
        });
    }
    if let Some(anchor) = after_bot_id {
        if anchor.trim().is_empty() {
            return Err(ServiceError::InvalidOperation {
                message: "after_bot_id, when supplied, must be a non-blank keyset anchor".into(),
                request_id: None,
            });
        }
    }
    Ok(())
}

/// Shared batch guard of the migration application.
pub fn validate_migration_batch(
    confirmed_candidate_ids: &[String],
    batch_id: &str,
) -> ServiceResult<()> {
    if batch_id.trim().is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: "ownership migration requires a non-blank batch id".into(),
            request_id: None,
        });
    }
    if confirmed_candidate_ids.len() > crate::types::bot_authority::MIGRATION_MAX_BATCH_SIZE {
        return Err(ServiceError::InvalidOperation {
            message: format!(
                "ownership migration batch carries {} candidates; the bounded batch maximum is {}",
                confirmed_candidate_ids.len(),
                crate::types::bot_authority::MIGRATION_MAX_BATCH_SIZE,
            ),
            request_id: None,
        });
    }
    if confirmed_candidate_ids.iter().any(|id| id.trim().is_empty()) {
        return Err(ServiceError::InvalidOperation {
            message: "ownership migration candidate ids must be non-blank".into(),
            request_id: None,
        });
    }
    Ok(())
}