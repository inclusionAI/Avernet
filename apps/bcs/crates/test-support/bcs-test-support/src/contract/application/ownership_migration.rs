//! `OwnershipMigrationService` shared conformance harness (plan Task 18
//! R25, plan 公共命名: `ownership_migration_service_contract_tests`).
//!
//! The driver supplies the PRODUCTION migration application (over any
//! core/backend); this suite pins the application's own argument guards
//! (§Task 17): every bound is a machine-stable `InvalidOperation`
//! rejection — never a silent clamp — and the guards fire BEFORE the core
//! is consulted (a driver may wrap its core, but no valid-looking request
//! may be echo-shaped into a smaller one).

use bcs_service_api::application::ownership_migration::OwnershipMigrationService;
use bcs_service_api::types::bot_authority::MIGRATION_MAX_BATCH_SIZE;
use bcs_service_api::ServiceError;

fn assert_invalid_operation(result: bcs_service_api::ServiceResult<impl Sized>, what: &str) {
    match result {
        Err(ServiceError::InvalidOperation { .. }) => {}
        Err(other) => panic!("{what} must be an InvalidOperation rejection, got: {other}"),
        Ok(_) => panic!("{what} must be rejected, never silently accepted or clamped"),
    }
}

/// The shared `OwnershipMigrationService` conformance suite. The bounds
/// use the REAL constant [`MIGRATION_MAX_BATCH_SIZE`] — implementations
/// cannot drift their own limits.
pub async fn ownership_migration_service_contract_tests(service: &dyn OwnershipMigrationService) {
    // Page bounds: zero and over-limit pages never run (page with
    // after_bot_id instead); a blank anchor is rejected as well.
    assert_invalid_operation(service.inspect_batch(None, 0).await, "a zero page limit");
    let over_limit = MIGRATION_MAX_BATCH_SIZE as u32 + 1;
    assert_invalid_operation(
        service.inspect_batch(None, over_limit).await,
        "an over-limit page",
    );
    assert_invalid_operation(
        service.inspect_batch(Some(" ".into()), 10).await,
        "a blank after_bot_id anchor",
    );

    // Batch guards: blank batch ids, over-limit batches and blank
    // candidate ids are rejected before any write.
    assert_invalid_operation(
        service.initialize_batch(vec!["bot-candidate".into()], " ".into()).await,
        "a blank batch id",
    );
    let ids: Vec<String> = (0..MIGRATION_MAX_BATCH_SIZE + 1)
        .map(|index| format!("migration-contract-bot-{index}"))
        .collect();
    assert_invalid_operation(
        service.initialize_batch(ids, "migration-contract-batch".into()).await,
        "an over-limit batch",
    );
    assert_invalid_operation(
        service.initialize_batch(vec![" ".into()], "migration-contract-batch".into()).await,
        "a blank candidate id",
    );
}