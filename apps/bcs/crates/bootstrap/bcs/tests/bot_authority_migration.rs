//! Bot-authority migration compatibility (bot-authority plan Task 1.6).
//!
//! Verifies the SQLite 035 upgrade on a legacy database:
//!
//! 1. Pre-migration state (profiles, runtime refs, rules, revoked
//!    edges) survives the upgrade untouched;
//! 2. the `edge_grants` unique key swaps to
//!    `(from_id, to_id, env, grant_kind, grant_ref_id)` and a single
//!    approved-owner unique index appears per `(env, to_id)`;
//! 3. `bcs_bots.ownership_version` lands at 0 for legacy rows;
//! 4. real runtime `insert_grant` keeps its idempotent semantics
//!    under the new key — and the check-back is kind-conditioned, so
//!    owner/manager edges of the same (from, to) pair get DISTINCT
//!    ids instead of the pre-migration collapse;
//! 5. runtime friend reads stay healthy next to management edges
//!    (A16 read direction), and revoke does not resurrect via
//!    idempotent re-insert;
//! 6. re-running the migration produces no changes.
//!
//! MySQL carries the same rules as `migrations/mysql/
//! 035_bot_authority.sql` (generated-column approved-owner unique
//! index, binary collations on the new tables, 033 chain): its
//! execution is CI-only — `crates/tools/bcs-admin/src/
//! migrate_mysql_chain_tests.rs::full_mysql_migration_chain_applies_and_preserves_history`
//! (updated for the 034 chain) is the gated MySQL entry, and the
//! store's authority SQL uses dialect-matched case-exact predicates.
//! The MySQL-side idempotent check-back gain (kind condition) is
//! code-shared with SQLite below.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbSqlFlavor, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::edge_permission::{
    EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType,
};
use bcs_edge_permission_store::DbEdgeGrantStore;
use bcs_service_api::port::repo::EdgeGrantRepoPort;

const ENV: &str = "legacy-env";
const BOT: &str = "bot_legacy_1";
const HUMAN: &str = "human_88001";
const DEFAULT_REF: i64 = 1;

/// A legacy database in the pre-035 shape: `bcs_bots` and
/// `permission_profiles` / `edge_grants` WITHOUT the later
/// columns/indexes, seeded with pre-existing profile/ref/rules/revoked
/// rows. The migration chain itself creates the remaining schema and
/// indexes.
async fn legacy_db() -> Arc<LocalSqliteDbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("open sqlite"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (\
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            bot_uuid TEXT NOT NULL, \
            name TEXT NOT NULL DEFAULT '', \
            bot_info TEXT DEFAULT NULL, \
            session_token TEXT DEFAULT NULL, \
            registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            env TEXT DEFAULT NULL, \
            visibility TEXT NOT NULL DEFAULT 'public', \
            created_by TEXT DEFAULT NULL, \
            actor_kind TEXT NOT NULL DEFAULT 'bot', \
            status TEXT NOT NULL DEFAULT 'online', \
            is_deleted INTEGER NOT NULL DEFAULT 0, \
            agent_code TEXT DEFAULT NULL, \
            task_claim_mode INTEGER NOT NULL DEFAULT 0, \
            task_dream_mode INTEGER NOT NULL DEFAULT 0, \
            user_visibility TEXT NOT NULL DEFAULT 'protected', \
            friend_ext TEXT DEFAULT NULL, \
            friend_check_in_strategy TEXT NOT NULL DEFAULT 'APPROVAL')",
    ))
    .await
    .expect("legacy bcs_bots");
    db.execute(DbStatement::new(
        "CREATE TABLE permission_profiles (\
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            bot_id TEXT NOT NULL, env TEXT NOT NULL, \
            name TEXT NOT NULL DEFAULT 'default', \
            description TEXT, rules_template TEXT NOT NULL DEFAULT '[]', \
            revision INTEGER NOT NULL DEFAULT 1, digest TEXT NOT NULL DEFAULT '', \
            is_default INTEGER NOT NULL DEFAULT 0, \
            status TEXT NOT NULL DEFAULT 'active', \
            created_by TEXT NOT NULL DEFAULT 'system', updated_by TEXT, \
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    ))
    .await
    .expect("legacy permission_profiles");
    // Old unique key WITHOUT grant_kind (014 shape).
    db.execute(DbStatement::new(
        "CREATE TABLE edge_grants (\
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            env TEXT NOT NULL, from_id TEXT NOT NULL, to_id TEXT NOT NULL, \
            grant_kind TEXT NOT NULL, grant_ref_id INTEGER NOT NULL, \
            rules TEXT, status TEXT NOT NULL DEFAULT 'approved', \
            originator_policy_type TEXT NOT NULL DEFAULT 'any', \
            originator_policy_data TEXT, \
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    ))
    .await
    .expect("legacy edge_grants");
    // The 014-era unique key is an index (version-13 shape),
    // not a table constraint, so version 35 can drop it.
    db.execute(DbStatement::new(
        "CREATE UNIQUE INDEX uk_edge_from_to_env_ref \
            ON edge_grants (from_id, to_id, env, grant_ref_id)",
    ))
    .await
    .expect("legacy unique key");

    for (bot, profile_ref) in
        [(BOT, DEFAULT_REF), ("bot_legacy_2", DEFAULT_REF + 1)]
    {
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_bots (bot_uuid, env) VALUES (?, ?)",
            vec![DbValue::from(bot), DbValue::from(ENV)],
        ))
        .await
        .expect("seed legacy bot");
        db.execute(DbStatement::with_params(
            "INSERT INTO permission_profiles \
             (id, bot_id, env, rules_template, digest, is_default, created_by) \
             VALUES (?, ?, ?, '[]', 'seed', 1, 'system')",
            vec![
                DbValue::from(profile_ref),
                DbValue::from(bot),
                DbValue::from(ENV),
            ],
        ))
        .await
        .expect("seed legacy profile");
        // Friend edge on the target's default profile ref.
        db.execute(DbStatement::with_params(
            "INSERT INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, status) \
             VALUES (?, ?, ?, 'permission_profile', ?, 'approved')",
            vec![
                DbValue::from(ENV),
                DbValue::from(HUMAN),
                DbValue::from(bot),
                DbValue::from(profile_ref),
            ],
        ))
        .await
        .expect("seed profile edge");
        // Inline-rules edge for the same pair.
        db.execute(DbStatement::with_params(
            "INSERT INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, rules, status) \
             VALUES (?, ?, ?, 'rules', 77, '[{\"tool\":\"x\",\"effect\":\"allow\"}]', 'approved')",
            vec![
                DbValue::from(ENV),
                DbValue::from(HUMAN),
                DbValue::from(bot),
            ],
        ))
        .await
        .expect("seed rules edge");
    }
    // A revoked runtime edge must survive the upgrade as revoked.
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants \
         (env, from_id, to_id, grant_kind, grant_ref_id, status) \
         VALUES (?, ?, ?, 'permission_profile', ?, 'revoked')",
        vec![
            DbValue::from(ENV),
            DbValue::from("human_9999"),
            DbValue::from(BOT),
            DbValue::from(DEFAULT_REF + 12),
        ],
    ))
    .await
    .expect("seed revoked edge");
    db
}

fn edge(
    from: &str,
    to: &str,
    kind: GrantKind,
    ref_id: u64,
    status: EdgeStatus,
) -> EdgeGrant {
    EdgeGrant {
        edge_id: 0,
        env: ENV.to_string(),
        from_id: from.to_string(),
        to_id: to.to_string(),
        grant_kind: kind,
        grant_ref_id: ref_id,
        rules: None,
        status,
        originator_policy_type: if matches!(kind, GrantKind::Owner | GrantKind::Manager) {
            OriginatorPolicyType::SameAsFrom
        } else {
            OriginatorPolicyType::Any
        },
        originator_policy_data: None,
    }
}

async fn query_u64(
    db: &dyn DbPlugin,
    sql: &str,
    params: Vec<DbValue>,
) -> u64 {
    db.query(DbStatement::with_params(sql, params))
        .await
        .expect("query")
        .into_iter()
        .next()
        .map(|row| match row.get("total") {
            Some(DbValue::I64(value)) => value.unsigned_abs(),
            other => panic!("total shape {other:?}"),
        })
        .unwrap_or(0)
}

async fn index_names(db: &dyn DbPlugin, table: &str) -> Vec<String> {
    db.query(DbStatement::new(&format!("PRAGMA index_list({table})")))
        .await
        .expect("index list")
        .into_iter()
        .map(|row| match row.get("name") {
            Some(DbValue::String(name)) => name.clone(),
            other => panic!("index name shape {other:?}"),
        })
        .collect()
}

#[tokio::test]
async fn bot_authority_upgrade_preserves_legacy_rows_and_extends_runtime() {
    let db = legacy_db().await;
    let before_edges =
        query_u64(db.as_ref(), "SELECT COUNT(*) AS total FROM edge_grants", vec![]).await;

    bcs::migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("run migration chain with 037");

    // The complete chain is recorded exactly once; a re-run changes nothing.
    let history = db
        .query(DbStatement::new(
            "SELECT version, name, dialect, checksum FROM bcs_schema_migrations ORDER BY version",
        ))
        .await
        .expect("history");
    assert_eq!(history.len() as i64, bcs::migrations::sqlite_target_version(), "full chain recorded");
    let authority = history.iter()
        .find(|row| row.get("version") == Some(&DbValue::I64(37)))
        .expect("bot_authority present");
    assert_eq!(
        authority.get("name"),
        Some(&DbValue::String("bot_authority".into()))
    );
    bcs::migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("idempotent re-run");
    let history_again = db
        .query(DbStatement::new(
            "SELECT version, name, dialect, checksum FROM bcs_schema_migrations ORDER BY version",
        ))
        .await
        .expect("history");
    assert_eq!(history, history_again, "migration re-run produces no changes");

    // Legacy rows all survived (5 seeded edges), statuses intact.
    let after_edges = query_u64(
        db.as_ref(),
        "SELECT COUNT(*) AS total FROM edge_grants \
         WHERE grant_kind IN ('permission_profile', 'rules')",
        vec![],
    )
    .await;
    assert_eq!(after_edges, before_edges, "runtime rows preserved");
    let revoked = query_u64(
        db.as_ref(),
        "SELECT COUNT(*) AS total FROM edge_grants \
         WHERE status = 'revoked' AND from_id = 'human_9999'",
        vec![],
    )
    .await;
    assert_eq!(revoked, 1, "revoked legacy row stays revoked");

    // bcs_bots.ownership_version lands at 0 for legacy bots (no
    // fabricated initialization).
    let versions = db
        .query(DbStatement::new(
            "SELECT bot_uuid, ownership_version FROM bcs_bots ORDER BY bot_uuid",
        ))
        .await
        .expect("versions");
    assert_eq!(versions.len(), 2);
    for row in &versions {
        assert_eq!(row.get("ownership_version"), Some(&DbValue::I64(0)));
    }

    // New unique key present, old unique key gone; approved-owner
    // constraint present.
    let indexes = index_names(db.as_ref(), "edge_grants").await;
    assert!(
        indexes.contains(&"uk_edge_from_to_env_kind_ref".to_string()),
        "new unique key present: {indexes:?}"
    );
    assert!(
        !indexes.contains(&"uk_edge_from_to_env_ref".to_string()),
        "old unique key dropped: {indexes:?}"
    );
    assert!(
        indexes.contains(&"uk_edge_approved_owner".to_string()),
        "approved-owner constraint present: {indexes:?}"
    );
    let new_tables = db
        .query(DbStatement::new(
            "SELECT name FROM sqlite_master WHERE type = 'table' \
             AND name IN ('bot_ownership_transfers', 'bot_authority_changes')",
        ))
        .await
        .expect("new tables");
    assert_eq!(new_tables.len(), 2, "both authority tables exist");

    // --- Real runtime store on the migrated chain ---------------
    let store = DbEdgeGrantStore::new(db.clone(), DbSqlFlavor::Sqlite);

    // Same-subject owner/manager/profile/rules rows coexist: with the
    // pre-035 key the two management edges (ref 0) would have
    // collapsed into the profile edge's slot / blocked each other.
    let owner_id = store
        .insert_grant(edge(HUMAN, BOT, GrantKind::Owner, 0, EdgeStatus::Approved))
        .await
        .expect("insert owner edge");
    let manager_id = store
        .insert_grant(edge(HUMAN, BOT, GrantKind::Manager, 0, EdgeStatus::Approved))
        .await
        .expect("insert manager edge");
    let profile_id = store
        .insert_grant(edge(
            HUMAN,
            BOT,
            GrantKind::PermissionProfile,
            DEFAULT_REF as u64,
            EdgeStatus::Approved,
        ))
        .await
        .expect("insert profile edge");
    let rules_id = store
        .insert_grant(edge(HUMAN, BOT, GrantKind::Rules, 91, EdgeStatus::Approved))
        .await
        .expect("insert rules edge");
    assert_ne!(owner_id, manager_id, "owner and manager get distinct ids");
    assert_ne!(manager_id, profile_id, "management and profile edges distinct");
    assert_ne!(profile_id, rules_id, "profile and rules edges distinct");

    // Idempotent check-back is kind-conditioned (SQLite conflicts
    // return no last_insert_id, so the SELECT decides): inserting
    // the same owner edge again returns the owner row, not the
    // manager or profile row.
    assert_eq!(
        store
            .insert_grant(edge(HUMAN, BOT, GrantKind::Owner, 0, EdgeStatus::Approved))
            .await
            .expect("idempotent owner insert"),
        owner_id,
        "owner check-back stays on the owner row"
    );
    assert_eq!(
        store
            .insert_grant(edge(HUMAN, BOT, GrantKind::Manager, 0, EdgeStatus::Approved))
            .await
            .expect("idempotent manager insert"),
        manager_id,
        "manager check-back stays on the manager row"
    );
    let management_rows = query_u64(
        db.as_ref(),
        "SELECT COUNT(*) AS total FROM edge_grants \
         WHERE from_id = ? AND to_id = ? AND env = ? \
           AND grant_kind IN ('owner', 'manager')",
        vec![
            DbValue::from(HUMAN),
            DbValue::from(BOT),
            DbValue::from(ENV),
        ],
    )
    .await;
    assert_eq!(management_rows, 2, "no duplicate rows from idempotent inserts");

    // Approved-owner uniqueness: a second approved owner on the bot
    // is rejected by the partial unique index — but merged with
    // a revoked owner restoral is allowed (same-subject row reuse).
    assert!(
        store
            .insert_grant(edge("human_88002", BOT, GrantKind::Owner, 0, EdgeStatus::Approved))
            .await
            .is_err(),
        "second approved owner must violate the unique constraint"
    );

    // Runtime revoke + no-resurrect: revoking the manager edge and
    // re-inserting it is idempotent and keeps it revoked.
    store
        .revoke_grant(manager_id, ENV)
        .await
        .expect("revoke manager edge");
    assert_eq!(
        store
            .insert_grant(edge(HUMAN, BOT, GrantKind::Manager, 0, EdgeStatus::Approved))
            .await
            .expect("re-insert after revoke returns the same row"),
        manager_id,
        "revoked row is reused, never duplicated"
    );
    let manager_status = db
        .query(DbStatement::with_params(
            "SELECT status FROM edge_grants WHERE id = ?",
            vec![DbValue::from(manager_id)],
        ))
        .await
        .expect("manager row")
        .remove(0);
    assert_eq!(
        manager_status.get("status"),
        Some(&DbValue::String("revoked".into())),
        "idempotent re-insert does not resurrect a revoked edge"
    );

    // A16 read direction: runtime friend reads ignore management
    // edges — `has_friend_edge` still keys on the default profile
    // ref, and the human's friend list does not change shape.
    assert!(
        store.has_friend_edge(HUMAN, BOT, ENV).await,
        "legacy friend edge still resolves"
    );
    let friends = store.list_friends(HUMAN, ENV).await;
    assert!(
        friends.contains(&BOT.to_string()),
        "legacy friend rows still list: {friends:?}"
    );
    assert!(
        !friends.contains(&HUMAN.to_string()),
        "management edges never appear as friend edges"
    );

    // A16 both directions: owner/manager rows never enter the
    // runtime grant surface — `human_88002` holds ONLY an
    // approved owner edge on BOT, so runtime admission sees
    // nothing.
    assert!(
        !store.is_authorized("human_88002", BOT, ENV).await,
        "an owner edge must not be a runtime authorization"
    );
    assert!(
        store
            .list_active_grants("human_88002", BOT, ENV)
            .await
            .is_empty(),
        "runtime grant list must exclude management edges"
    );
    // Anti-corruption: two approved owners on one bot from the
    // pre-migration shape make the approved-owner unique-index
    // creation FAIL loudly during the upgrade (dirty legacy data
    // cannot slip past 035 silently).
    let dup = legacy_db().await;
    for human in ["human_88002", "human_88003"] {
        dup.execute(DbStatement::with_params(
            "INSERT INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, status) \
             VALUES (?, ?, ?, 'owner', 0, 'approved')",
            vec![
                DbValue::from(ENV),
                DbValue::from(human),
                DbValue::from(BOT),
            ],
        ))
        .await
        .expect("seed duplicate owners pre-migration");
    }
    let result = bcs::migrations::run_sqlite_migrations(dup.as_ref()).await;
    assert!(
        result.is_err(),
        "duplicate approved owners must fail the approved-owner index creation loudly"
    );
    // Distinct owners on distinct bots stay fine under the same
    // partial index.
    let separated = legacy_db().await;
    for (human, bot) in [("human_88002", BOT), ("human_88003", "bot_legacy_2")] {
        separated
            .execute(DbStatement::with_params(
                "INSERT INTO edge_grants \
                 (env, from_id, to_id, grant_kind, grant_ref_id, status) \
                 VALUES (?, ?, ?, 'owner', 0, 'approved')",
                vec![
                    DbValue::from(ENV),
                    DbValue::from(human),
                    DbValue::from(bot),
                ],
            ))
            .await
            .expect("seed owners on distinct bots pre-migration");
    }
    bcs::migrations::run_sqlite_migrations(separated.as_ref())
        .await
        .expect("distinct owners migrate cleanly");
}

#[tokio::test]
async fn fresh_db_gets_bot_authority_schema_directly() {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite"));
    bcs::migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("fresh chain");
    let indexes = index_names(db.as_ref(), "edge_grants").await;
    assert!(indexes.contains(&"uk_edge_from_to_env_kind_ref".to_string()));
    assert!(!indexes.contains(&"uk_edge_from_to_env_ref".to_string()));
    assert!(indexes.contains(&"uk_edge_approved_owner".to_string()));
    let columns = db
        .query(DbStatement::new("PRAGMA table_info(bcs_bots)"))
        .await
        .expect("columns");
    assert!(
        columns
            .iter()
            .any(|row| row.get("name") == Some(&DbValue::String("ownership_version".into()))),
        "fresh bcs_bots carries ownership_version"
    );
}
