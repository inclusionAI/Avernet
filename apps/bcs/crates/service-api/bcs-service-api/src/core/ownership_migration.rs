//! Historical ownership migration Core contract (plan Task 17, spec §16.1).
//!
//! The Core owns the bounded migration facts: the keyset candidate scan over
//! live physical version-0 Bots, the governance conflict derivation that
//! never lets a stale candidate or a creator suffix authorize anything, and
//! the per-Bot initialization executed through the Task 5 governed atomic
//! lane (`initialize_existing_ownership`) with the batch recorded on the
//! `bot_ownership_initializations` ledger. Applications own argument
//! validation and result translation; the storage facts live behind the Bot
//! and relation repo ports.

use async_trait::async_trait;

use crate::core::error::ServiceResult;
use crate::types::bot_authority::{OwnershipCandidatePage, OwnershipMigrationReport};

/// Governed backfill Core of the one-shot authority cutover.
///
/// Both methods operate on CURRENT re-read facts (scan and execution
/// re-verify version and owner independently), so a candidate snapshot is
/// never an authorization: only a fresh `Ready` classification may reach the
/// initialization lane, and every historical/conflicted/excluded shape lands
/// in the governance report instead.
#[async_trait]
pub trait OwnershipMigrationCoreService: Send + Sync {
    /// One bounded page of the migration dry-run scan: live physical
    /// version-0 Bots of the store's env, ordered by `bot_uuid`, strictly
    /// after `after_bot_id` (keyset pagination), at most `limit` rows.
    ///
    /// The page contains ready AND conflicted candidates (conflicts are
    /// governance entries, never hidden), each with the derived candidate
    /// owner (legal `created_by`) or `None` when the missing source is
    /// legal, plus the machine-read reason. `next_bot_id` is the last row's
    /// id while more rows may follow. The scan never writes.
    async fn inspect_batch(
        &self,
        after_bot_id: Option<String>,
        limit: u32,
    ) -> ServiceResult<OwnershipCandidatePage>;

    /// Initialize the operator-confirmed candidates of one governed batch.
    ///
    /// `confirmed_candidate_ids` only carries Bot ids — never owners: the
    /// candidate owner is re-derived from current `created_by` + legacy
    /// creator corroborance + live-Human evidence at execution time, and any
    /// re-verification conflict is attributed into the report instead of
    /// migrating. `batch_id` is the record/replay key: committed rows of a
    /// partially failed earlier attempt with the SAME batch id are
    /// recovered from the initialization ledger and reported as
    /// `initialized`, and every other confirmed Bot is classified fresh
    /// (already-initialized/transferred Bots are verified and skipped with
    /// their owner and version untouched).
    ///
    /// Per-Bot storage failures are `failed` entries (the batch continues;
    /// the maintenance command must exit nonzero once any exists); the
    /// report always accounts for every confirmed id exactly once.
    async fn initialize_batch(
        &self,
        confirmed_candidate_ids: Vec<String>,
        batch_id: String,
    ) -> ServiceResult<OwnershipMigrationReport>;
}