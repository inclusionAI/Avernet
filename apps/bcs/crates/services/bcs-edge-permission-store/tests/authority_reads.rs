//! Strict `BotAuthorityRepoPort` reads against
//! [`DbBotAuthorityStore`] on SQLite (bot-authority plan
//! Task 1.2/1.3/1.5).
//!
//! The fixture DB carries the post-migration `edge_grants`
//! shape (unique key `(from_id, to_id, env, grant_kind,
//! grant_ref_id)`, partial approved-owner unique index), the
//! `bcs_bots.ownership_version` column, and the two authority
//! tables — mirroring `migrations/sqlite/037_bot_authority.sql`.
//! The upgrade-path behaviors themselves are covered by
//! `crates/bootstrap/bcs/tests/bot_authority_migration.rs`.
//!
//! MySQL note: the same store runs on MySQL with the
//! `migrations/mysql/036_bot_authority.sql` chain in CI;
//! locally there is no MySQL server, so these cases run on
//! SQLite only (plan §6).

#![allow(clippy::expect_used, clippy::unwrap_used)]

use bcs_db_api::{DbStatement, DbValue};
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::types::bot_authority::{BotAccessRelation, BotRoleQuery};
use bcs_test_support::bot_authority_sqlite::{execute, new_fixture};
use bcs_test_support::contract::bot_authority_contract::BotAuthorityContractFixture;
use bcs_test_support::contract::bot_authority_contract::run_bot_authority_contract;

const ENV: &str = "authority-env";
const OTHER_ENV: &str = "authority-env-other";

#[tokio::test]
async fn authority_repo_contract_sqlite() {
    let fixture = new_fixture(ENV).await;
    run_bot_authority_contract(&fixture, ENV, OTHER_ENV).await;
}

/// Storage failures must surface as `Err` — never a `None`
/// or empty success (plan Task 1 named case).
#[tokio::test]
async fn storage_error_is_not_none() {
    let fixture = new_fixture(ENV).await;
    execute(fixture.db.as_ref(),
        "INSERT INTO bcs_bots (bot_uuid, env, name, ownership_version) \
         VALUES ('bot_broken', ?, 'x', 1)", vec![DbValue::from(ENV)]).await;
    execute(fixture.db.as_ref(),
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, \
         rules, status, originator_policy_type) \
         VALUES (?, 'human_broken', 'bot_broken', 'manager', 0, NULL, 'approved', 'same_as_from')",
        vec![DbValue::from(ENV)]).await;

    // Remove the authority SoR mid-flight: every read must fail,
    // not silently deny.
    fixture
        .db
        .execute(DbStatement::new("DROP TABLE edge_grants"))
        .await
        .expect("drop edge_grants to simulate storage failure");

    let repo = fixture.authority_repo();
    let role = repo.role("human_broken", "bot_broken").await;
    assert!(role.is_err(), "role on broken storage must error, got {role:?}");
    let batch = repo
        .roles_for(&[BotRoleQuery {
            user_id: "human_broken".into(),
            bot_id: "bot_broken".into(),
        }])
        .await;
    assert!(batch.is_err(), "roles_for on broken storage must error, got {batch:?}");
    let ownership = repo.ownership("bot_broken").await;
    assert!(
        ownership.is_err(),
        "ownership on broken storage must error, got {ownership:?}"
    );
}

/// The MySQL flavor is exercised in CI with the real 033 chain;
/// locally we can still pin that the store binds its env (user
/// params cannot widen scope) through a second store instance.
#[tokio::test]
async fn store_env_binding_cannot_be_widened() {
    let fixture = new_fixture(ENV).await;
    let other = DbBotAuthorityStore::new(
        fixture.db.clone(),
        bcs_db_api::DbSqlFlavor::Sqlite,
        OTHER_ENV.to_string(),
    );
    fixture.seed_bot(ENV, "bot_bound", 1).await;
    fixture
        .grant_edge(ENV, "bot_bound", "human_owner", BotAccessRelation::Owner)
        .await;
    assert_eq!(
        fixture.authority_repo().role("human_owner", "bot_bound").await.unwrap(),
        Some(BotAccessRelation::Owner)
    );
    assert_eq!(
        other.role("human_owner", "bot_bound").await.unwrap(),
        None,
        "another env's store sees nothing from this env"
    );
}

// ---- Legacy created_by fallback (migration window shim, SQL arm) ----
//
// Mirrors the memory arm's scenario in
// `bcs-bot-store/tests/memory_authority.rs` so the two stores stay
// behavior-identical for the shim too: a version-0 legacy Bot answers
// role reads with its legacy `created_by` fact when the flag is ON;
// flag OFF, unrelated Humans, tombstoned rows, and version > 0
// integrity all keep the strict semantics.

#[tokio::test]
async fn legacy_created_by_fallback_resolves_only_the_legacy_creator() {
    let fixture = new_fixture(ENV).await;
    // Version-0 legacy Bot with a creator record; its Human actor row
    // deliberately does NOT exist (unmigrated operators predate the
    // authority Human registry, and the shim must still authorize).
    execute(
        fixture.db.as_ref(),
        "INSERT INTO bcs_bots (bot_uuid, env, name, created_by, ownership_version) \
         VALUES ('legacy-bot', ?, 'x', '85020', 0)",
        vec![DbValue::from(ENV)],
    )
    .await;
    let repo = DbBotAuthorityStore::new(
        fixture.db.clone(),
        bcs_db_api::DbSqlFlavor::Sqlite,
        ENV.to_string(),
    )
    .with_legacy_created_by_fallback(true);

    assert_eq!(
        repo.role("human_85020", "legacy-bot").await.expect("role on"),
        Some(BotAccessRelation::Owner),
    );
    assert_eq!(
        repo.roles_for(&[BotRoleQuery {
            user_id: "human_85020".into(),
            bot_id: "legacy-bot".into(),
        }])
        .await
        .expect("roles_for on"),
        vec![Some(BotAccessRelation::Owner)],
    );
    assert_eq!(repo.role("human_99999", "legacy-bot").await.expect("other human"), None);
    assert_eq!(repo.role("85020", "legacy-bot").await.expect("no prefix"), None);

    // Flag OFF (default): strict deny revives.
    let strict = DbBotAuthorityStore::new(
        fixture.db.clone(),
        bcs_db_api::DbSqlFlavor::Sqlite,
        ENV.to_string(),
    );
    assert_eq!(strict.role("human_85020", "legacy-bot").await.expect("role off"), None);
    assert_eq!(
        strict
            .roles_for(&[BotRoleQuery {
                user_id: "human_85020".into(),
                bot_id: "legacy-bot".into(),
            }])
            .await
            .expect("roles_for off"),
        vec![None],
    );
}

#[tokio::test]
async fn legacy_fallback_never_shadows_initialized_or_deleted_bots() {
    let fixture = new_fixture(ENV).await;
    let repo = DbBotAuthorityStore::new(
        fixture.db.clone(),
        bcs_db_api::DbSqlFlavor::Sqlite,
        ENV.to_string(),
    )
    .with_legacy_created_by_fallback(true);

    // Initialized Bot without a complete owner edge stays a hard
    // integrity error — the shim never papers over corrupt state.
    execute(
        fixture.db.as_ref(),
        "INSERT INTO bcs_bots (bot_uuid, env, name, created_by, ownership_version) \
         VALUES ('broken-bot', ?, 'x', '85020', 1)",
        vec![DbValue::from(ENV)],
    )
    .await;
    let role = repo.role("human_85020", "broken-bot").await;
    assert!(role.is_err(), "initialized-without-owner keeps the integrity error: {role:?}");

    // Tombstoned Bot denies even to its legacy creator.
    execute(
        fixture.db.as_ref(),
        "INSERT INTO bcs_bots (bot_uuid, env, name, created_by, ownership_version, is_deleted) \
         VALUES ('dead-bot', ?, 'x', '85020', 0, 1)",
        vec![DbValue::from(ENV)],
    )
    .await;
    assert_eq!(repo.role("human_85020", "dead-bot").await.expect("dead role"), None);
}
