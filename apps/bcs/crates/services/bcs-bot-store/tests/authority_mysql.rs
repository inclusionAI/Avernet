//! MySQL conformance gate for the trusted
//! registration / governance / retirement lifecycle
//! composites (bot-authority plan Task 8 / §6).
//!
//! The plan §6 named entry: the SAME owned scenario
//! set as `authority_lifecycle.rs` (shared via
//! `common/authority_lifecycle.rs`) runs the REAL
//! composite transactions through
//! [`bcs_bot_store::PersistentBotRepo`],
//! [`bcs_bot_store::DbProviderStore`] and the
//! authority/profile stores in the MySQL dialect,
//! over the FULL MySQL migration chain — including
//! the deterministic infrastructure-failure
//! rollback scenarios. Locally there is no MySQL
//! server, so this entry is `#[ignore]`d and the
//! SQLite suite proves the scenario parity on
//! SQLite (plan §6).
//!
//! Each scenario runs against its OWN freshly
//! applied migration chain (the chain re-applies on
//! a dropped-and-empty schema): scenarios stay as
//! isolated as the per-scenario fresh SQLite
//! fixture — and the migration chain never runs on
//! a dirty database.
//!
//! `BCS_TEST_MYSQL_URL` must point at an ISOLATED
//! disposable EMPTY MySQL database; a missing URL
//! fails the entry immediately (启动缺URL立即失败)
//! and the URL is never logged.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use bcs_db_api::{DbPlugin, DbStatement};
#[path = "../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;

use mysql_chain::{MysqlConformanceDb, report_mysql_gate_scenarios};

#[path = "common/authority_lifecycle.rs"]
mod lifecycle;

use lifecycle::new_mysql_fixture;

/// The full plan §6 scenario inventory for the
/// lifecycle composite: v1 Human chain (9), v2
/// Provider chain (4), runtime propagation (1),
/// governance initialize_ownership (2), Provider
/// retirement incl. the cleanup-pending retry and
/// the in-tx pending-identity races (11).
const SCENARIOS: [&'static str; 30] = [
    "trusted_v1_registration_commits_the_whole_state",
    "trusted_v1_reused_bot_id_never_resets_owner",
    "trusted_v1_tombstoned_bot_id_never_resurrects",
    "owner_edge_failure_rolls_back_everything",
    "profile_failure_rolls_back_everything",
    "audit_failure_rolls_back_everything",
    "human_actor_write_failure_rolls_back_everything",
    "registration_creates_the_owner_actor_row_and_the_owner_can_manage",
    "mutation_must_match_the_verified_initiator",
    "governance_initialize_backfills_once_and_is_idempotent",
    "governance_initialize_refuses_missing_and_deleted_bots",
    "governance_initialize_refuses_creator_mismatch",
    "governance_initialize_never_targets_a_human_actor_row",
    "trusted_v2_registration_commits_binding_owner_and_authority",
    "trusted_v2_binding_failure_rolls_back_everything",
    "trusted_v2_owner_write_failure_rolls_back_everything",
    "committed_registration_does_not_activate_runtime",
    "provider_retire_metadata_branch_commits_the_whole_state",
    "provider_retire_failure_is_atomic",
    "provider_retire_retry_is_idempotent_without_duplicate_audit",
    "provider_retire_binding_fallback_branch_disables_binding",
    "provider_retire_legacy_unbound_and_branch_mismatch",
    "normal_retire_revokes_authority_and_never_chains_provider",
    "retired_bot_id_never_resurrects_through_lifecycle_writes",
    "retire_serializes_against_the_transfer_accept",
    "retire_closes_the_pending_identity_seen_in_tx",
    "provider_retire_closes_the_pending_identity_seen_in_tx",
    "retire_retries_when_the_pending_vanishes_between_preread_and_tx",
    "retire_with_no_pendings_writes_no_closure_audit",
    // The owner-actor liveness guard rides the same composite
    // family as the v1 registration scenarios above; appended
    // to keep the established positional indexing untouched.
    "trusted_v1_registration_refuses_a_tombstoned_owner",
];

/// Plan §6 MySQL gate: the lifecycle composite
/// scenarios over the migrated MySQL chain.
#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL; CI runs the plan §6 MySQL gate"]
async fn provider_lifecycle_mysql_conformance() {
    let database = MysqlConformanceDb::open_empty().await;
    let applied = database.apply_full_migration_chain().await;
    assert!(
        !applied.is_empty(),
        "full MySQL migration chain applied ({} versions)",
        applied.len()
    );

    // One real scenario run per entry — the exact
    // order of `SCENARIOS`.
    for scenario in SCENARIOS {
        reset_fresh_chain(&database).await;
        let fixture = new_mysql_fixture(database.plugin.clone()).await;
        run_scenario(scenario, &fixture).await;
    }

    assert_eq!(SCENARIOS.len(), 30, "lifecycle scenario inventory");
    report_mysql_gate_scenarios(
        "provider-lifecycle",
        &SCENARIOS,
    );

    database.close().await;
}

async fn run_scenario(scenario: &str, fixture: &lifecycle::Fixture) {
    match scenario {
        name if name == SCENARIOS[0] => lifecycle::trusted_v1_registration_commits_the_whole_state(fixture).await,
        name if name == SCENARIOS[1] => lifecycle::trusted_v1_reused_bot_id_never_resets_owner(fixture).await,
        name if name == SCENARIOS[2] => lifecycle::trusted_v1_tombstoned_bot_id_never_resurrects(fixture).await,
        name if name == SCENARIOS[3] => lifecycle::owner_edge_failure_rolls_back_everything(fixture).await,
        name if name == SCENARIOS[4] => lifecycle::profile_failure_rolls_back_everything(fixture).await,
        name if name == SCENARIOS[5] => lifecycle::audit_failure_rolls_back_everything(fixture).await,
        name if name == SCENARIOS[6] => lifecycle::human_actor_write_failure_rolls_back_everything(fixture).await,
        name if name == SCENARIOS[7] => lifecycle::registration_creates_the_owner_actor_row_and_the_owner_can_manage(fixture).await,
        name if name == SCENARIOS[8] => lifecycle::mutation_must_match_the_verified_initiator(fixture).await,
        name if name == SCENARIOS[9] => lifecycle::governance_initialize_backfills_once_and_is_idempotent(fixture).await,
        name if name == SCENARIOS[10] => lifecycle::governance_initialize_refuses_missing_and_deleted_bots(fixture).await,
        name if name == SCENARIOS[11] => lifecycle::governance_initialize_refuses_creator_mismatch(fixture).await,
        name if name == SCENARIOS[12] => lifecycle::governance_initialize_never_targets_a_human_actor_row(fixture).await,
        name if name == SCENARIOS[13] => lifecycle::trusted_v2_registration_commits_binding_owner_and_authority(fixture).await,
        name if name == SCENARIOS[14] => lifecycle::trusted_v2_binding_failure_rolls_back_everything(fixture).await,
        name if name == SCENARIOS[15] => lifecycle::trusted_v2_owner_write_failure_rolls_back_everything(fixture).await,
        name if name == SCENARIOS[16] => lifecycle::committed_registration_does_not_activate_runtime(fixture).await,
        name if name == SCENARIOS[17] => lifecycle::provider_retire_metadata_branch_commits_the_whole_state(fixture).await,
        name if name == SCENARIOS[18] => lifecycle::provider_retire_failure_is_atomic(fixture).await,
        name if name == SCENARIOS[19] => lifecycle::provider_retire_retry_is_idempotent_without_duplicate_audit(fixture).await,
        name if name == SCENARIOS[20] => lifecycle::provider_retire_binding_fallback_branch_disables_binding(fixture).await,
        name if name == SCENARIOS[21] => lifecycle::provider_retire_legacy_unbound_and_branch_mismatch(fixture).await,
        name if name == SCENARIOS[22] => lifecycle::normal_retire_revokes_authority_and_never_chains_provider(fixture).await,
        name if name == SCENARIOS[23] => lifecycle::retired_bot_id_never_resurrects_through_lifecycle_writes(fixture).await,
        name if name == SCENARIOS[24] => lifecycle::retire_serializes_against_the_transfer_accept(fixture).await,
        name if name == SCENARIOS[25] => lifecycle::retire_closes_the_pending_identity_seen_in_tx(fixture).await,
        name if name == SCENARIOS[26] => lifecycle::provider_retire_closes_the_pending_identity_seen_in_tx(fixture).await,
        name if name == SCENARIOS[27] => lifecycle::retire_retries_when_the_pending_vanishes_between_preread_and_tx(fixture).await,
        name if name == SCENARIOS[28] => lifecycle::retire_with_no_pendings_writes_no_closure_audit(fixture).await,
        name if name == SCENARIOS[29] => lifecycle::trusted_v1_registration_refuses_a_tombstoned_owner(fixture).await,
        other => panic!("unknown lifecycle scenario: {other}"),
    }
}

/// Drop every table so the next migration chain
/// application starts from a truly EMPTY schema —
/// the chain must never run on a dirty database.
async fn reset_fresh_chain(database: &MysqlConformanceDb) {
    let db = database.plugin.as_ref();
    db.execute(DbStatement::new("SET FOREIGN_KEY_CHECKS = 0"))
        .await
        .expect("disable FK checks for the reset");
    let rows = db
        .query(DbStatement::new(
            "SELECT table_name AS t FROM information_schema.tables \
             WHERE table_schema = DATABASE()",
        ))
        .await
        .expect("list tables");
    for row in rows {
        let table: String = bcs_db_api::db_get_column(&row, "t")
            .expect("table name");
        db.execute(DbStatement::new(format!("DROP TABLE `{table}`")))
            .await
            .unwrap_or_else(|error| panic!("drop table {table}: {error}"));
    }
    db.execute(DbStatement::new("SET FOREIGN_KEY_CHECKS = 1"))
        .await
        .expect("re-enable FK checks");
    let applied = database.apply_full_migration_chain().await;
    assert!(
        !applied.is_empty(),
        "fresh MySQL migration chain applied ({} versions)",
        applied.len()
    );
}
