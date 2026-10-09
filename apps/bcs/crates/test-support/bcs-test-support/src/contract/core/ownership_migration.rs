//! `OwnershipMigrationCoreService` shared conformance harness (plan Task
//! 18 R25, plan 公共命名: `ownership_migration_core_service_contract_tests`).
//!
//! The driver supplies the PRODUCTION core plus a small driver-backed
//! seeding/observation surface ([`OwnershipMigrationCoreHarness`]): every
//! assertion runs against the REAL store the driver mounted — the keyset
//! dry-run pages over live seeded Bots, the batch apply runs the governed
//! atomic initialization (batch ledger + replay recovery), and the same
//! `batch_id` replay returns the original committed counts without
//! re-executing a per-Bot initialization a second time.

use async_trait::async_trait;

use bcs_service_api::core::ownership_migration::OwnershipMigrationCoreService;
use bcs_service_api::core::error::ServiceResult;

/// Driver surface the shared migration core suite needs: seed ONE more
/// ready candidate (live, physical, version 0, with a legal `created_by`
/// owner source) and observe a Bot's committed ownership version.
#[async_trait]
pub trait OwnershipMigrationCoreHarness: Send + Sync {
    /// The production core under conformance.
    fn core(&self) -> &dyn OwnershipMigrationCoreService;

    /// Seed one more ready migration candidate; returns its bot id.
    async fn seed_ready_candidate(&self) -> ServiceResult<String>;

    /// After the grow_counter seeding: the current ownership version of
    /// one Bot (any backend-appropriate read).
    async fn ownership_version(&self, bot_id: &str) -> ServiceResult<u64>;

    /// How many ownership-initialization ledger rows one Bot carries
    /// (replay recovery must keep this at exactly one per applied batch).
    async fn initialization_ledger_count(&self, bot_id: &str) -> ServiceResult<u64>;

    /// The store's env binding (candidates are env-bound; the harness
    /// never migrates rows outside it).
    fn env(&self) -> &str;
}

/// The shared `OwnershipMigrationCoreService` conformance suite.
pub async fn ownership_migration_core_service_contract_tests(
    h: &dyn OwnershipMigrationCoreHarness,
) {
    let core = h.core();
    let first = h.seed_ready_candidate().await.expect("seed first candidate");
    let second = h.seed_ready_candidate().await.expect("seed second candidate");

    // Dry-run: a keyset page reports both candidates as Ready with the
    // candidate owner from `created_by`; the page is read-only (versions
    // stay at zero until an explicit apply).
    let page = core
        .inspect_batch(None, MIGRATION_PAGE)
        .await
        .expect("inspect the seeded candidates");
    assert!(
        page.candidates
            .iter()
            .any(|candidate| candidate.bot_id == first),
        "the first seeded candidate must be reported by the dry-run page"
    );
    assert!(
        page.candidates
            .iter()
            .any(|candidate| candidate.bot_id == second),
        "the second seeded candidate must be reported by the dry-run page"
    );
    for candidate in &page.candidates {
        assert_eq!(candidate.env, h.env(), "candidates stay env-bound");
    }
    assert_eq!(
        h.ownership_version(&first).await.expect("first version"),
        0,
        "the dry-run never initializes"
    );

    // Governed apply: one bounded batch, both seeds initialized, version 1,
    // exactly one ledger row per Bot.
    let batch_id = "ownership-migration-contract-batch".to_string();
    let report = core
        .initialize_batch(vec![first.clone(), second.clone()], batch_id.clone())
        .await
        .expect("apply the governed batch");
    assert_eq!(
        report.initialized.len(),
        2,
        "both ready candidates initialized: {:?}",
        &report
    );
    for bot_id in [&first, &second] {
        assert_eq!(
            h.ownership_version(bot_id).await.expect("version after apply"),
            1,
            "the governed atomic initialization set version 1"
        );
        assert_eq!(
            h.initialization_ledger_count(bot_id).await.expect("ledger count"),
            1,
            "one initialization ledger row per applied Bot"
        );
    }

    // Same-batch replay: the SAME committed counts return, never a second
    // execution (ledger stays at one row per Bot, versions untouched).
    let replay = core
        .initialize_batch(vec![first.clone(), second.clone()], batch_id)
        .await
        .expect("replay the same batch id");
    assert_eq!(
        replay.initialized.len(),
        2,
        "a same-batch replay returns the committed counts"
    );
    for bot_id in [&first, &second] {
        assert_eq!(
            h.initialization_ledger_count(bot_id).await.expect("ledger count after replay"),
            1,
            "replays never re-execute a per-Bot initialization"
        );
    }
}

/// Page bound used by this suite's inspect calls (within the bounded scan
/// contract; implementations keep their own machine-stable limit).
const MIGRATION_PAGE: u32 = 50;