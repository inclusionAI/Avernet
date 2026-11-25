//! `bot-authority` governance backfill tests
//! (bot-authority plan Task 4.6, spec §10.4/§10.5)
//! against the REAL migration-chained SQLite
//! schema and the REAL `BotRegistryCoreService`
//! facade (`BotCore` over the persistent store).
//!
//! MySQL note: the governance facade shares its
//! statements with the registration composites;
//! MySQL execution stays CI-gated the same way
//! (plan §6; no new ignored tests are fabricated).

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::{DbPlugin, DbStatement, DbSqlFlavor, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::types::bot_authority::{
    AuthorityActor, AuthorityMutation,
};
use bcs_service_api::{BotCapabilities, BotRegistryCoreService};

use super::{
    GovernanceDb, MAX_BATCH_IDS, build_context, initialize, inspect,
    normalize_ids,
};

type Registry = Arc<dyn BotRegistryCoreService>;

struct SqliteDb {
    db: Arc<LocalSqliteDbPlugin>,
    registry: Registry,
    _dir: tempfile::TempDir,
}

async fn sqlite_db() -> SqliteDb {
    let dir = tempfile::tempdir().expect("temp dir");
    let path = dir.path().join("governance.sqlite3");
    // The REAL code-defined migration chain: the
    // schema the inspect/apply facts live in.
    bcs::migrations::run_sqlite_migrations(&LocalSqliteDbPlugin::new_file(
        &path,
    )
    .expect("open sqlite"))
    .await
    .expect("migrations");
    let db = Arc::new(
        LocalSqliteDbPlugin::new_file(&path).expect("open sqlite"),
    );
    let repo =
        Arc::new(PersistentBotRepo::with_sql_flavor(db.clone(), DbSqlFlavor::Sqlite));
    let registry: Registry = Arc::new(bcs_bot::BotCore::with_repo(repo.clone()));
    SqliteDb {
        db,
        registry,
        _dir: dir,
    }
}

impl SqliteDb {
    async fn execute(&self, sql: &str, params: Vec<DbValue>) {
        self.db
            .execute(DbStatement::with_params(sql, params))
            .await
            .expect("seed statement");
    }

    async fn seed_bot(&self, bot_id: &str) {
        self.registry
            .create_registration_if_absent(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    ..Default::default()
                },
                "creator-A",
                &format!("token-{bot_id}"),
            )
            .await
            .expect("seed registration");
    }

    /// Seed one live Human ACTOR row (the authority Human
    /// registry, `bcs_bots`) in the instance env.
    async fn seed_human_actor(&self, user_id: &str) {
        self.execute(
            "INSERT OR IGNORE INTO bcs_bots \
             (bot_uuid, actor_kind, name, bot_info, session_token, \
              created_by, visibility, status, env, \
              registered_at, updated_at) \
             VALUES (?, 'human', ?, '{\"summary\":null}', ?, ?, \
                     'protected', 'online', ?, \
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            vec![
                DbValue::from(format!("human_{user_id}")),
                DbValue::from(user_id),
                DbValue::from(format!("fixture-token-{user_id}")),
                DbValue::from(user_id),
                DbValue::from(bcs_config::resolve_env_str()),
            ],
        )
        .await;
    }

    async fn seed_owner_edge(&self, bot_id: &str, actor_id: &str) {
        self.execute(
            "INSERT INTO edge_grants \
             (env, from_id, to_id, grant_kind, grant_ref_id, status) \
             VALUES (?, ?, ?, 'owner', 0, 'approved')",
            vec![
                DbValue::from(bcs_config::resolve_env_str()),
                DbValue::from(actor_id),
                DbValue::from(bot_id),
            ],
        )
        .await;
    }

    /// Set `created_by` WITHOUT touching ownership
    /// (the backfill reads it as the historical
    /// created record).
    async fn seed_created_by(&self, bot_id: &str, created_by: &str) {
        self.execute(
            "UPDATE bcs_bots SET created_by = ? \
             WHERE bot_uuid = ? AND env = ?",
            vec![
                DbValue::from(created_by),
                DbValue::from(bot_id),
                DbValue::from(bcs_config::resolve_env_str()),
            ],
        )
        .await;
    }

    async fn audit_count(&self) -> u64 {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT COUNT(*) AS c FROM bot_authority_changes",
                vec![],
            ))
            .await
            .expect("audit count");
        rows.first()
            .and_then(|row| row.get("c"))
            .and_then(|value| value.as_i64())
            .unwrap_or(0) as u64
    }

    async fn owner_edge_user(&self, bot_id: &str) -> Option<String> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT from_id FROM edge_grants \
                 WHERE to_id = ? AND grant_kind = 'owner' \
                 AND grant_ref_id = 0 AND status = 'approved'",
                vec![DbValue::from(bot_id)],
            ))
            .await
            .expect("owner edge read");
        rows.first().map(|row| {
            bcs_db_api::db_get_column::<String>(row, "from_id")
                .expect("owner from_id")
        })
    }

    async fn created_by(&self, bot_id: &str) -> Option<String> {
        let rows = self
            .db
            .query(DbStatement::with_params(
                "SELECT created_by FROM bcs_bots \
                 WHERE bot_uuid = ? AND env = ?",
                vec![
                    DbValue::from(bot_id),
                    DbValue::from(bcs_config::resolve_env_str()),
                ],
            ))
            .await
            .expect("created_by read");
        rows.first().map(|row| {
            bcs_db_api::db_get_column::<String>(row, "created_by")
                .expect("created_by")
        })
    }
}

fn mutation(tag: &str) -> AuthorityMutation {
    AuthorityMutation {
        mutation_id: format!("governance-{tag}"),
        actor: AuthorityActor::System {
            name: "bcs-admin".to_string(),
        },
    }
}

#[tokio::test]
async fn normalize_ids_dedups_and_enforces_the_batch_cap() {
    let ids = normalize_ids(&["b1".into(), "b1".into(), " b2 ".into()])
        .expect("normalized");
    assert_eq!(ids, vec!["b1".to_string(), "b2".to_string()]);

    let error = normalize_ids(&[]).unwrap_err();
    assert!(format!("{error}").contains("--bot-id"));

    let oversized: Vec<String> = (0..=MAX_BATCH_IDS)
        .map(|index| format!("bot-{index}"))
        .collect();
    let error = normalize_ids(&oversized).unwrap_err();
    assert!(
        format!("{error}").contains("at most"),
        "the batch cap is explicit: {error}"
    );
}

/// The four-way classification from the REAL store
/// rows, and ZERO writes during the inspect.
#[tokio::test]
async fn inspect_classifies_all_buckets_with_zero_writes() {
    let db = sqlite_db().await;

    // Backfillable: version 0, created record points
    // at a Human with a live actor row, no contradicting
    // owner edge.
    db.seed_human_actor("creator-human").await;
    db.seed_bot("bot-clean").await;
    db.seed_created_by("bot-clean", "creator-human").await;

    // Missing Human: the created record has no live
    // Human actor row.
    db.seed_bot("bot-no-human").await;
    db.seed_created_by("bot-no-human", "nobody").await;

    // Relation conflict: created record contradicted
    // by an approved owner edge for ANOTHER actor.
    db.seed_human_actor("creator-human").await;
    db.seed_bot("bot-conflict").await;
    db.seed_created_by("bot-conflict", "creator-human").await;
    db.seed_owner_edge("bot-conflict", "human_otherone")
        .await;

    // Already initialized: version > 0 with a
    // committed owner.
    db.seed_human_actor("committed-owner").await;
    db.seed_bot("bot-initialized").await;
    db.seed_created_by("bot-initialized", "committed-owner")
        .await;
    db.registry
        .initialize_ownership(
            "bot-initialized",
            "committed-owner",
            mutation("seed-init"),
        )
        .await
        .expect("seed initialize");

    let before_audits = db.audit_count().await;
    let before_edges = count_rows(&db).await;

    let governance_db = GovernanceDb::sqlite(db.db.clone());
    let context = build_context(&governance_db);
    let report = inspect(
        &context,
        &governance_db,
        &[
            "bot-clean".to_string(),
            "bot-no-human".to_string(),
            "bot-conflict".to_string(),
            "bot-initialized".to_string(),
            "bot-ghost".to_string(),
        ],
    )
    .await
    .expect("inspect report");

    assert!(report.contains("bot_id=bot-clean state=backfillable"));
    assert!(report.contains("bot_id=bot-no-human state=missing_human"));
    assert!(report.contains("bot_id=bot-conflict state=relation_conflict"));
    assert!(
        report.contains("bot_id=bot-initialized state=already_initialized")
    );
    assert!(report.contains("bot_id=bot-ghost state=unknown"));

    // ZERO writes: audit rows AND edges AND versions
    // byte-identical.
    assert_eq!(db.audit_count().await, before_audits);
    assert_eq!(count_rows(&db).await, before_edges);
    let version = db
        .db
        .query(DbStatement::with_params(
            "SELECT ownership_version FROM bcs_bots \
             WHERE bot_uuid = 'bot-clean'",
            vec![],
        ))
        .await
        .expect("version read");
    assert_eq!(
        version
            .first()
            .and_then(|row| row.get("ownership_version"))
            .and_then(|value| value.as_i64())
            .unwrap_or(-1),
        0,
        "the version-0 row stays version 0"
    );
}

async fn count_rows(db: &SqliteDb) -> u64 {
    db.db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS c FROM edge_grants",
            vec![],
        ))
        .await
        .expect("edge count")
        .first()
        .and_then(|row| row.get("c"))
        .and_then(|value| value.as_i64())
        .unwrap_or(0) as u64
}

#[tokio::test]
async fn apply_initializes_only_listed_and_repeats_echo_the_current_owner() {
    let db = sqlite_db().await;
    db.seed_human_actor("creator-human").await;
    db.seed_human_actor("creator-b").await;
    db.seed_bot("bot-target").await;
    db.seed_created_by("bot-target", "creator-human").await;
    // NOT listed stays untouched.
    db.seed_bot("bot-not-listed").await;
    db.seed_created_by("bot-not-listed", "creator-b").await;

    let governance_db = GovernanceDb::sqlite(db.db.clone());
    let context = build_context(&governance_db);
    let report = initialize(
        &context,
        &governance_db,
        &["bot-target".to_string()],
    )
    .await
    .expect("apply report");
    assert!(report.contains("bot_id=bot-target state=backfilled"));
    assert_eq!(db.audit_count().await, 1, "one initialize audit");

    // The repeated apply reports the CURRENT owner and
    // writes NOTHING (no created_by rewrite, no second
    // initialize audit).
    let report = initialize(
        &context,
        &governance_db,
        &["bot-target".to_string()],
    )
    .await
    .expect("repeat report");
    assert!(
        report.contains("bot_id=bot-target state=already_initialized"),
        "repeat echoes the current state: {report}"
    );
    assert_eq!(db.audit_count().await, 1);
    assert_eq!(
        db.owner_edge_user("bot-target").await,
        Some("human_creator-human".to_string())
    );
    assert_eq!(
        db.created_by("bot-target").await,
        Some("creator-human".to_string()),
        "created_by is never rewritten by the backfill"
    );
    // The unlisted bot never gains an owner edge or
    // version.
    assert_eq!(db.owner_edge_user("bot-not-listed").await, None);
}

/// 缺 Human / 冲突 bots in an EXPLICIT list are
/// reported per item and NEVER partially written.
#[tokio::test]
async fn apply_reports_missing_and_conflict_per_item_without_writes() {
    let db = sqlite_db().await;
    db.seed_human_actor("creator-human").await;
    db.seed_bot("bot-missing").await;
    db.seed_created_by("bot-missing", "nobody").await;
    db.seed_bot("bot-conflict").await;
    db.seed_created_by("bot-conflict", "creator-human").await;
    db.seed_owner_edge("bot-conflict", "human_otherone")
        .await;

    let governance_db = GovernanceDb::sqlite(db.db.clone());
    let context = build_context(&governance_db);
    let before_audits = db.audit_count().await;
    let report = initialize(
        &context,
        &governance_db,
        &[
            "bot-missing".to_string(),
            "bot-conflict".to_string(),
        ],
    )
    .await
    .expect("apply report");
    assert!(report.contains("bot_id=bot-missing state=missing_human"));
    assert!(
        report
            .contains("bot_id=bot-conflict state=relation_conflict")
    );
    assert!(report.contains("rejected=2"));
    assert_eq!(db.audit_count().await, before_audits);
    assert_eq!(db.owner_edge_user("bot-conflict").await, Some("human_otherone".to_string()));
}

/// The transferred-owner protection: a version>0 Bot
/// whose committed owner is B while the created
/// record says A is echoed as ALREADY-INITIALIZED
/// with owner B — the apply NEVER resets the owner.
#[tokio::test]
async fn apply_never_resets_a_transferred_owner() {
    let db = sqlite_db().await;
    db.seed_human_actor("creator-b").await;
    db.seed_bot("bot-transferred").await;
    db.seed_created_by("bot-transferred", "creator-A")
        .await;
    // Owner B is the committed owner (a completed transfer
    // produced this state). Seeded directly — the initialize
    // composite now refuses anything but the stored creator
    // record, by design.
    db.execute(
        "UPDATE bcs_bots SET ownership_version = 1 WHERE bot_uuid = ?",
        vec![DbValue::from("bot-transferred")],
    )
    .await;
    db.seed_owner_edge("bot-transferred", "human_creator-b").await;

    let governance_db = GovernanceDb::sqlite(db.db.clone());
    let context = build_context(&governance_db);
    let report = initialize(
        &context,
        &governance_db,
        &["bot-transferred".to_string()],
    )
    .await
    .expect("apply report");
    assert!(
        report.contains("bot_id=bot-transferred state=already_initialized"),
        "{report}"
    );
    assert!(
        report.contains("owner=creator-b"),
        "the CURRENT owner B is echoed, not the created record"
    );
    assert_eq!(
        db.owner_edge_user("bot-transferred").await,
        Some("human_creator-b".to_string())
    );
    // And re-applying with creator A's created record
    // does not flip the owner back: a different-owner
    // initialize is refused, the owner stays B.
    let error = db
        .registry
        .initialize_ownership(
            "bot-transferred",
            "creator-A",
            mutation("as-created-by"),
        )
        .await
        .expect_err("different owner is refused");
    assert!(matches!(error, bcs_service_api::ServiceError::Conflict(_)));
    assert_eq!(
        db.owner_edge_user("bot-transferred").await,
        Some("human_creator-b".to_string())
    );
}

// ===========================================================================
// The governance mutation-id generator must produce ids
// WITHIN the audit column's width (`MUTATION_ID_MAX_LEN` —
// migration 033: `bot_authority_changes.mutation_id`
// VARCHAR(64), binary collation). A generated id wider than
// the column fails the audit INSERT on real MySQL with
// ERROR 1406 AFTER the payload writes — the whole backfill
// apply rolls back and a retry can never succeed. The
// generator must NOT truncate a hash-mix of the bot id
// either: the Bot identity already lives in the audit
// row's own `bot_id` column.
// ===========================================================================

#[test]
fn generated_mutation_ids_fit_the_audit_column_width() {
    use bcs_service_api::types::bot_authority::MUTATION_ID_MAX_LEN;

    // A long Bot id (the DDL gives the Bot ids
    // VARCHAR(256)) must not be embedded into the
    // mutation id any more.
    for bot_id in [
        "b".to_string(),
        format!("bot-{}", "x".repeat(42)),
        format!("bot-{}", "x".repeat(256)),
    ] {
        let id = super::mutation_for(&bot_id).mutation_id;
        assert!(
            id.len() <= MUTATION_ID_MAX_LEN,
            "backfill mutation id '{id}' ({} bytes) exceeds the \
             audit column width {MUTATION_ID_MAX_LEN}",
            id.len()
        );
    }
}

#[test]
fn backfill_mutation_ids_stay_unique_and_labeled() {
    let first = super::mutation_for("bot-a").mutation_id;
    let second = super::mutation_for("bot-a").mutation_id;
    assert_ne!(first, second, "each apply needs its own id");
    assert!(
        first.starts_with("backfill-"),
        "the id keeps its provenance label: {first}"
    );
}
