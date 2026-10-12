//! Shared SQL harness of the plan Task 17 migration suite
//! (`tests/ownership_migration.rs`): real-database builders (the FULL
//! migration chain), legacy-library seeding (created_by rows, version-0
//! bots, transferred/deleted bots, creator-conflict shapes), row-projection
//! assertions (never mocked booleans) and the governed wiring assembly the
//! `bcs-ownership-migrate` binary itself uses.

#![allow(dead_code)]

use std::path::Path;
use std::sync::Arc;

use bcs_bot_store::PersistentBotRepo;
use bcs_relation_store::DbRelationStore;
use bcs_service_api::application::ownership_migration::OwnershipMigrationService;
use bcs_service_api::port::repo::{BotRepoPort, RelationRepoPort};
use bcs_db_api::{DbPlugin, DbStatement, DbValue, db_get_column};
use bcs_db_local::LocalSqliteDbPlugin;

pub(crate) fn env_str() -> String {
    bcs::resolve_env()
}

/// In-process SQLite database with the FULL migration chain installed — the
/// schema an upgrading production library has.
pub(crate) async fn migrated_sqlite() -> Arc<dyn DbPlugin> {
    let db = LocalSqliteDbPlugin::new().expect("open local sqlite");
    bcs::migrations::run_sqlite_migrations(&db)
        .await
        .expect("apply the full sqlite migration chain");
    Arc::new(db)
}

/// File-backed SQLite database with the FULL migration chain installed (the
/// binary tests seed it, then the real subprocess re-opens the same file).
pub(crate) async fn migrated_sqlite_file(path: &Path) -> Arc<dyn DbPlugin> {
    let db = LocalSqliteDbPlugin::new_file(path).expect("open sqlite file");
    bcs::migrations::run_sqlite_migrations(&db)
        .await
        .expect("apply the full sqlite migration chain");
    Arc::new(db)
}

pub(crate) async fn scalar(db: &dyn DbPlugin, sql: &str, params: Vec<DbValue>) -> i64 {
    let rows = db
        .query(DbStatement::with_params(sql, params))
        .await
        .unwrap_or_else(|error| panic!("scalar query failed: {error}\n{sql}"));
    let row = rows.first().unwrap_or_else(|| panic!("no rows: {sql}"));
    db_get_column::<i64>(row, "value").expect("scalar column value")
}

/// Seed a legacy Human actor row (`human_<user>`, the D11 id-by-prefix shape
/// `ensure_human_actor` writes) on the migrated chain.
pub(crate) async fn seed_human(db: &dyn DbPlugin, user: &str, env: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, env, name, actor_kind, created_by, visibility, status, session_token) \
         VALUES (?, ?, ?, 'human', ?, 'protected', 'online', ?)",
        vec![
            DbValue::from(format!("human_{user}")),
            DbValue::from(env),
            DbValue::from(user.to_string()),
            DbValue::from(user.to_string()),
            DbValue::from(format!("stoken-human-{user}")),
        ],
    ))
    .await
    .expect("seed human actor row");
}

/// Seed a legacy physical Bot row at `ownership_version` with the given
/// `created_by` (NULL-able). Liveness is controlled by `deleted`;
/// `actor_kind` selects the physical-Bot vs Human-self-row shape.
#[allow(clippy::too_many_arguments)]
pub(crate) async fn seed_bot(
    db: &dyn DbPlugin,
    bot_id: &str,
    created_by: Option<&str>,
    ownership_version: i64,
    deleted: bool,
    actor_kind: &str,
    env: &str,
) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, env, name, actor_kind, created_by, visibility, status, \
         session_token, is_deleted, ownership_version) \
         VALUES (?, ?, ?, ?, ?, 'protected', 'online', ?, ?, ?)",
        vec![
            DbValue::from(bot_id.to_string()),
            DbValue::from(env),
            DbValue::from(bot_id.to_string()),
            DbValue::from(actor_kind),
            created_by.map(DbValue::from).unwrap_or(DbValue::Null),
            DbValue::from(format!("stoken-{bot_id}")),
            DbValue::from(if deleted { 1 } else { 0 }),
            DbValue::from(ownership_version),
        ],
    ))
    .await
    .unwrap_or_else(|error| panic!("seed bot {bot_id}: {error}"));
}

/// Seed an approved owner edge (the exact v2 role-edge shape `initialize`
/// commits) from human `<user>` to the Bot.
pub(crate) async fn seed_owner_edge(db: &dyn DbPlugin, user: &str, bot_id: &str, env: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
         originator_policy_type, originator_policy_data, management_source_kind, management_source_id) \
         VALUES (?, ?, ?, 'owner', 0, NULL, 'approved', 'same_as_from', NULL, 'owner', 'owner')",
        vec![
            DbValue::from(env),
            DbValue::from(format!("human_{user}")),
            DbValue::from(bot_id.to_string()),
        ],
    ))
    .await
    .expect("seed owner edge");
}

/// Seed a legacy creator claim in `bcs_actor_relations` (the v1
/// `ensure_owner_edges` shape the migration cross-checks `created_by` against).
pub(crate) async fn seed_creator_claim(db: &dyn DbPlugin, user: &str, bot_id: &str, env: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_actor_relations (from_id, to_id, env, is_creator) VALUES (?, ?, ?, 1)",
        vec![
            DbValue::from(format!("human_{user}")),
            DbValue::from(bot_id.to_string()),
            DbValue::from(env),
        ],
    ))
    .await
    .expect("seed creator claim");
}

pub(crate) async fn ownership_version_of(db: &dyn DbPlugin, bot_id: &str, env: &str) -> i64 {
    scalar(
        db,
        "SELECT ownership_version AS value FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
        vec![DbValue::from(bot_id), DbValue::from(env)],
    )
    .await
}

pub(crate) async fn created_by_of(db: &dyn DbPlugin, bot_id: &str, env: &str) -> Option<String> {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT created_by FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
            vec![DbValue::from(bot_id), DbValue::from(env)],
        ))
        .await
        .expect("read created_by");
    let row = rows.first().expect("bot row");
    row.get_string("created_by").ok().flatten()
}

pub(crate) async fn approved_owner_edge_count(db: &dyn DbPlugin, bot_id: &str, env: &str) -> i64 {
    scalar(
        db,
        "SELECT COUNT(*) AS value FROM edge_grants \
         WHERE env = ? AND to_id = ? AND grant_kind = 'owner' AND status = 'approved'",
        vec![DbValue::from(env), DbValue::from(bot_id)],
    )
    .await
}

pub(crate) async fn owner_edge_claimant(db: &dyn DbPlugin, bot_id: &str, env: &str) -> Option<String> {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT from_id FROM edge_grants \
             WHERE env = ? AND to_id = ? AND grant_kind = 'owner' AND status = 'approved'",
            vec![DbValue::from(env), DbValue::from(bot_id)],
        ))
        .await
        .expect("read owner edge claimant");
    rows.first()
        .and_then(|row| row.get_string("from_id").ok().flatten())
}

pub(crate) async fn initialization_audit_count(db: &dyn DbPlugin, bot_id: &str, env: &str) -> i64 {
    scalar(
        db,
        "SELECT COUNT(*) AS value FROM bot_ownership_initializations WHERE env = ? AND bot_id = ?",
        vec![DbValue::from(env), DbValue::from(bot_id)],
    )
    .await
}

pub(crate) async fn default_profile_count(db: &dyn DbPlugin, bot_id: &str, env: &str) -> i64 {
    scalar(
        db,
        "SELECT COUNT(*) AS value FROM permission_profiles \
         WHERE env = ? AND bot_id = ? AND is_default = 1 AND status = 'active'",
        vec![DbValue::from(env), DbValue::from(bot_id)],
    )
    .await
}

/// Full authority-state fingerprint of one Bot (dry-run witness): version,
/// owner-edge count, initialization history, default profiles and the
/// `created_by` value. Byte-compare before/after.
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct AuthorityFingerprint {
    pub bot_id: String,
    pub version: i64,
    pub owner_edges: i64,
    pub initializations: i64,
    pub default_profiles: i64,
    pub created_by: Option<String>,
}

pub(crate) async fn fingerprint(db: &dyn DbPlugin, bot_id: &str, env: &str) -> AuthorityFingerprint {
    AuthorityFingerprint {
        bot_id: bot_id.to_string(),
        version: ownership_version_of(db, bot_id, env).await,
        owner_edges: approved_owner_edge_count(db, bot_id, env).await,
        initializations: initialization_audit_count(db, bot_id, env).await,
        default_profiles: default_profile_count(db, bot_id, env).await,
        created_by: created_by_of(db, bot_id, env).await,
    }
}

/// The governed migration application wired over real SQL stores through the
/// binary's own composition entry (`bcs::ownership_migration_wiring`).
pub(crate) fn migration_service_over_sql(
    db: Arc<dyn DbPlugin>,
) -> (Arc<dyn OwnershipMigrationService>, Arc<dyn BotRepoPort>, Arc<dyn RelationRepoPort>) {
    let bots: Arc<dyn BotRepoPort> = Arc::new(PersistentBotRepo::with_sql_flavor(
        db.clone(),
        bcs_db_api::DbSqlFlavor::Sqlite,
    ));
    let relations: Arc<dyn RelationRepoPort> = Arc::new(DbRelationStore::sqlite(db));
    let service =
        bcs::ownership_migration_wiring::ownership_migration_service_with_repos(
            bots.clone(),
            relations.clone(),
        );
    (service, bots, relations)
}

/// A complete legacy library, seeded through the real migration chain:
/// - `user-a`/`user-b`/`user-c` (live Humans) and clean version-0 bots;
/// - conflict rows: missing creator, bare runtime, multi creator,
///   claim mismatch, missing Human, corrupted v0-with-owner-edge,
///   corrupted v1-without-owner-edge;
/// - excluded rows: a Human self row and a deleted bot;
/// - a transferred bot (version > 0, owner != created_by) and an
///   already-initialized bot still owned by created_by.
pub(crate) async fn seed_legacy_library(db: &dyn DbPlugin, env: &str) {
    seed_human(db, "user-a", env).await;
    seed_human(db, "user-b", env).await;
    seed_human(db, "user-c", env).await;

    seed_bot(db, "bot-clean-a", Some("user-a"), 0, false, "bot", env).await;
    seed_creator_claim(db, "user-a", "bot-clean-a", env).await;
    seed_bot(db, "bot-clean-b", Some("user-a"), 0, false, "bot", env).await;

    // Transferred after an earlier initialization: version > 0 and the owner
    // edge points at user-b, NOT created_by (user-a).
    seed_bot(db, "bot-transferred", Some("user-a"), 3, false, "bot", env).await;
    seed_owner_edge(db, "user-b", "bot-transferred", env).await;
    // Initialized and still owned by created_by: rerun only verifies/skips.
    seed_bot(db, "bot-owned", Some("user-a"), 1, false, "bot", env).await;
    seed_owner_edge(db, "user-a", "bot-owned", env).await;

    // Missing creator: created_by is absent; never auto-claimed by suffix.
    seed_bot(db, "bot-nocreator", None, 0, false, "bot", env).await;
    // Bare runtime row: no Human anywhere in its provenance.
    seed_bot(db, "bot-runtime", None, 0, false, "bot", env).await;
    // Multiple conflicting legacy creator claims.
    seed_bot(db, "bot-multicreator", Some("user-a"), 0, false, "bot", env).await;
    seed_creator_claim(db, "user-a", "bot-multicreator", env).await;
    seed_creator_claim(db, "user-c", "bot-multicreator", env).await;
    // A single legacy claim that contradicts the current created_by.
    seed_bot(db, "bot-claimmismatch", Some("user-a"), 0, false, "bot", env).await;
    seed_creator_claim(db, "user-c", "bot-claimmismatch", env).await;
    // Missing Human: created_by references a user with no live human row.
    seed_bot(db, "bot-missinghuman", Some("user-ghost"), 0, false, "bot", env).await;
    // Corrupted: version 0 but an approved owner edge already exists.
    seed_bot(db, "bot-corrupt-v0", Some("user-a"), 0, false, "bot", env).await;
    seed_owner_edge(db, "user-a", "bot-corrupt-v0", env).await;
    // Corrupted: initialized version but no owner edge at all.
    seed_bot(db, "bot-corrupt-v1", Some("user-a"), 2, false, "bot", env).await;
    // Excluded rows: Human self row and a deleted bot.
    seed_bot(db, "human_self_row", Some("user-a"), 0, false, "human", env).await;
    seed_bot(db, "bot-deleted", Some("user-a"), 0, true, "bot", env).await;
}

pub(crate) const BINARY_ENV: &str = "local";

pub(crate) async fn seed_binary_library(db: &dyn DbPlugin) {
    seed_human(db, "user-a", BINARY_ENV).await;
    seed_bot(db, "bot-bin-clean", Some("user-a"), 0, false, "bot", BINARY_ENV).await;
    seed_creator_claim(db, "user-a", "bot-bin-clean", BINARY_ENV).await;
    seed_bot(db, "bot-bin-nocreator", None, 0, false, "bot", BINARY_ENV).await;
}

pub(crate) async fn reopen_fingerprint(db_path: &Path, bot_id: &str) -> AuthorityFingerprint {
    let db = LocalSqliteDbPlugin::new_file(db_path).expect("reopen the sqlite library");
    fingerprint(&db, bot_id, BINARY_ENV).await
}

