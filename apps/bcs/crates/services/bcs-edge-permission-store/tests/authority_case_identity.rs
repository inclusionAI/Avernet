//! Case-identity regression (PR #2568 review F2): every authority-lane SQL
//! predicate on `edge_grants.from_id` / `edge_grants.to_id` must compare
//! with pinned BINARY collation.
//!
//! Live MySQL keeps `edge_grants`'s LEGACY case-insensitive column
//! collation (migration 032 created the new authority tables with
//! `COLLATE utf8mb4_bin` but deliberately left the old table untouched,
//! relying on the store's dialect encapsulation — spec §5.3, the same
//! treatment the older friend-list grant queries already apply). Without
//! the pin, `USER-A` folds onto `user-a`'s approved owner/manager rows and
//! `role(USER-A)` answers a foreign identity — exactly the input
//! `can_manage` / `require_owner` consume.
//!
//! The SQLite driver mirrors the live MySQL collation by rebuilding the
//! migrated `edge_grants` table with NOCASE identity columns (SQLite's
//! default comparison is already case-sensitive, so the plain production
//! schema can never express this failure — the NOCASE rebuild is the only
//! faithful local replica of the live table's collation).

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod migrations;

use std::sync::Arc;

use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::{AuditActor, ManagerMutation, OWNER_SOURCE_ID, OWNER_SOURCE_KIND};
use bcs_edge_permission_store::DbBotAuthorityStore;
use bcs_service_api::port::repo::BotAuthorityRepoPort;
use bcs_service_api::port::repo::bot_authority::human_actor_id;

const ENV: &str = "local";

/// The harness: the full migration chain, then `edge_grants` rebuilt with
/// NOCASE identity columns (the local replica of live MySQL's legacy
/// collation on the table's `from_id` / `to_id`), plus the raw plugin
/// handle for direct seeding.
struct CaseIdentityHarness {
    db: Arc<LocalSqliteDbPlugin>,
    store: Arc<DbBotAuthorityStore>,
}

impl CaseIdentityHarness {
    async fn new() -> Self {
        let raw = LocalSqliteDbPlugin::new().expect("open local sqlite");
        migrations::run_sqlite_migrations(&raw)
            .await
            .expect("apply the full sqlite migration chain");

        // 12-step ALTER: rename, create the NOCASE twin with the exact
        // canonical column order (migration 033's shape), copy, drop,
        // restore the authority indexes.
        raw.execute(DbStatement::new(
            "ALTER TABLE edge_grants RENAME TO edge_grants_binary_rebuild",
        ))
        .await
        .expect("rename edge_grants");
        raw.execute(DbStatement::new(
            "CREATE TABLE edge_grants (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
                env TEXT NOT NULL, \
                from_id TEXT NOT NULL COLLATE NOCASE, \
                to_id TEXT NOT NULL COLLATE NOCASE, \
                grant_kind TEXT NOT NULL, \
                grant_ref_id INTEGER NOT NULL, \
                management_source_kind TEXT NOT NULL DEFAULT 'none', \
                management_source_id TEXT NOT NULL DEFAULT 'none', \
                rules TEXT, \
                status TEXT NOT NULL DEFAULT 'approved', \
                originator_policy_type TEXT NOT NULL DEFAULT 'any', \
                originator_policy_data TEXT, \
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        ))
        .await
        .expect("create the NOCASE twin table");
        raw.execute(DbStatement::new(
            "INSERT INTO edge_grants SELECT * FROM edge_grants_binary_rebuild",
        ))
        .await
        .expect("copy the migrated rows");
        raw.execute(DbStatement::new("DROP TABLE edge_grants_binary_rebuild"))
            .await
            .expect("drop the pre-rebuild table");
        for index in [
            "CREATE UNIQUE INDEX IF NOT EXISTS uk_edge_from_to_env_kind_ref_source \
             ON edge_grants(from_id, to_id, env, grant_kind, grant_ref_id, \
             management_source_kind, management_source_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uk_edge_active_owner_slot \
             ON edge_grants(env, to_id) WHERE grant_kind = 'owner' \
             AND status = 'approved'",
            "CREATE INDEX IF NOT EXISTS idx_edge_from_env_status \
             ON edge_grants(from_id, env, status)",
            "CREATE INDEX IF NOT EXISTS idx_edge_to_env_status \
             ON edge_grants(to_id, env, status)",
        ] {
            raw.execute(DbStatement::new(index))
                .await
                .expect("restore the authority indexes");
        }

        let db = Arc::new(raw);
        let store = Arc::new(DbBotAuthorityStore::sqlite(
            db.clone() as Arc<dyn DbPlugin>,
            ENV.to_string(),
        ));
        Self { db, store }
    }

    async fn run(&self, sql: &str, params: Vec<DbValue>) {
        self.db
            .execute(DbStatement::with_params(sql, params))
            .await
            .expect("seed statement executes");
    }

    async fn scalar(&self, sql: &str, params: Vec<DbValue>) -> i64 {
        let rows = self
            .db
            .query(DbStatement::with_params(sql, params))
            .await
            .expect("count query answers");
        rows.first()
            .and_then(|row| row.get_i64("n").ok().flatten())
            .unwrap_or(0)
    }

    /// One initialized live Bot row at the given ownership version.
    async fn seed_bot(&self, bot_id: &str) {
        self.run(
            "INSERT INTO bcs_bots (bot_uuid, name, env, ownership_version) \
             VALUES (?, ?, ?, 1)",
            vec![
                DbValue::from(bot_id),
                DbValue::from(bot_id),
                DbValue::from(ENV),
            ],
        )
        .await;
    }

    /// One live same-env Human actor row.
    async fn seed_human(&self, user_id: &str) {
        self.run(
            "INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, status) \
             VALUES (?, ?, ?, 'human', 'online')",
            vec![
                DbValue::from(human_actor_id(user_id)),
                DbValue::from(user_id),
                DbValue::from(ENV),
            ],
        )
        .await;
    }

    /// One APPROVED owner/manager edge with explicit identity texts.
    async fn seed_edge(&self, from_id: &str, to_id: &str, kind: &str, source_kind: &str, source_id: &str) {
        self.run(
            "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, \
             rules, status, originator_policy_type, originator_policy_data, \
             management_source_kind, management_source_id) \
             VALUES (?, ?, ?, ?, 0, NULL, 'approved', 'same_as_from', NULL, ?, ?)",
            vec![
                DbValue::from(ENV),
                DbValue::from(from_id),
                DbValue::from(to_id),
                DbValue::from(kind),
                DbValue::from(source_kind),
                DbValue::from(source_id),
            ],
        )
        .await;
    }
}

#[tokio::test]
async fn role_reads_never_fold_a_case_variant_onto_foreign_rows() {
    let harness = CaseIdentityHarness::new().await;
    let store = harness.store.clone();

    // The live legal state: bot-a initialized at version 1, with ONE
    // approved owner edge whose identity text differs in case (User-A,
    // not user-a).
    harness.seed_bot("bot-a").await;
    harness
        .seed_edge("human_User-A", "bot-a", "owner", OWNER_SOURCE_KIND, OWNER_SOURCE_ID)
        .await;

    // The binary identity rule: none of user-a's case variants holds the
    // User-A edge. Pre-fix, `role` matched the row under NOCASE and
    // answered `Some(Owner)` for the foreign identity.
    for probe in ["user-a", "USER-A", "user-A"] {
        let role = store.role(probe, "bot-a").await.expect("role read answers");
        assert!(
            role.is_none(),
            "role({probe}) matched a foreign-case owner edge: {role:?}"
        );
    }

    // The exact stored identity still reads (the pin is on collation, not
    // on the stored byte text).
    let exact = store.role("User-A", "bot-a").await.expect("role read answers");
    assert!(exact.is_some(), "the exact stored identity must still read");

    // The batch read keeps the same pair discipline.
    let roles = store
        .roles_for(&[("user-a".to_string(), "bot-a".to_string())])
        .await
        .expect("roles_for read answers");
    assert_eq!(
        roles,
        vec![None],
        "roles_for matched a foreign-case owner edge"
    );
}

#[tokio::test]
async fn ownership_reads_never_fold_a_case_variant_bot_onto_foreign_rows() {
    let harness = CaseIdentityHarness::new().await;
    let store = harness.store.clone();

    // bot-a initialized at version 1; its ONLY owner edge is stored under
    // the case-variant to_id BOT-A, so the strict owner slot for the exact
    // bot-a must treat the initialized bot as having NO approved owner row
    // (the fail-closed consistency branch), never resolve through the
    // variant row's owner.
    harness.seed_bot("bot-a").await;
    harness
        .seed_edge(
            &human_actor_id("user-a"),
            "BOT-A",
            "owner",
            OWNER_SOURCE_KIND,
            OWNER_SOURCE_ID,
        )
        .await;

    let ownership = store.ownership("bot-a").await;
    assert!(
        ownership.is_err(),
        "ownership('bot-a') must not resolve through the case-variant BOT-A \
         edge: {ownership:?}"
    );
}

#[tokio::test]
async fn manager_write_guards_never_fold_a_case_variant_onto_foreign_slots() {
    let harness = CaseIdentityHarness::new().await;
    let store = harness.store.clone();

    // bot-a initialized; its owner edge is stored under the CASE-VARIANT
    // actress User-A, so the canonical user-a holds NO current role.
    harness.seed_bot("bot-a").await;
    harness.seed_human("user-b").await;
    harness
        .seed_edge("human_User-A", "bot-a", "owner", OWNER_SOURCE_KIND, OWNER_SOURCE_ID)
        .await;

    // The mutation-lane actor guard (`ar EXISTS` over from_id) must refuse
    // user-a: the User-A edge is a different identity. Pre-fix, NOCASE
    // let user-a act with the foreign owner edge.
    let denied = store
        .mutate_manager(
            AuditActor::Human {
                user_id: "user-a".to_string(),
            },
            "bot-a",
            ManagerMutation::GrantDirect {
                user_id: "user-b".to_string(),
            },
        )
        .await;
    match denied {
        Err(bcs_service_api::ServiceError::Authority(
            bcs_service_api::types::error::AuthorityError::Forbidden(message),
        )) => {
            assert!(message.contains("user-a"), "denial names the actor: {message}");
        }
        other => panic!(
            "the case-variant User-A owner edge must not authorize user-a: {other:?}"
        ),
    }
}

#[tokio::test]
async fn revoke_never_touches_a_case_variant_subject_row() {
    let harness = CaseIdentityHarness::new().await;
    let store = harness.store.clone();

    // Legal target state: canonical owner user-a, live human user-b, plus
    // a DIRECT manual manager edge held by the CASE-VARIANT user-b
    // (User-B); user-b itself holds nothing.
    harness.seed_bot("bot-a").await;
    harness.seed_human("user-b").await;
    harness
        .seed_edge(
            &human_actor_id("user-a"),
            "bot-a",
            "owner",
            OWNER_SOURCE_KIND,
            OWNER_SOURCE_ID,
        )
        .await;
    harness
        .seed_edge("human_User-B", "bot-a", "manager", "direct", "manual")
        .await;

    // Revoking user-b must be a verified NO-OP: the User-B edge is a
    // different identity. Pre-fix, the validated count and the changing
    // UPDATE both folded under NOCASE, reported changed=true and revoked
    // the foreign User-B row.
    let revoked = store
        .mutate_manager(
            AuditActor::Human {
                user_id: "user-a".to_string(),
            },
            "bot-a",
            ManagerMutation::RevokeNonTeam {
                user_id: "user-b".to_string(),
            },
        )
        .await
        .expect("the live revoke answers");
    assert!(
        !revoked.changed,
        "revoking user-b must not fold onto the foreign User-B edge"
    );

    let survivors = harness
        .scalar(
            "SELECT COUNT(*) AS n FROM edge_grants \
             WHERE env = ? AND from_id = 'human_User-B' \
               AND grant_kind = 'manager' AND status = 'approved'",
            vec![DbValue::from(ENV)],
        )
        .await;
    assert_eq!(survivors, 1, "the foreign-case User-B edge must survive");
}

#[tokio::test]
async fn list_managers_keeps_case_variants_distinct() {
    let harness = CaseIdentityHarness::new().await;
    let store = harness.store.clone();

    harness.seed_bot("bot-a").await;
    harness
        .seed_edge(
            &human_actor_id("user-a"),
            "bot-a",
            "owner",
            OWNER_SOURCE_KIND,
            OWNER_SOURCE_ID,
        )
        .await;
    harness
        .seed_edge("human_User-B", "bot-a", "manager", "direct", "manual")
        .await;

    let list = store
        .list_managers("bot-a", 0, 10)
        .await
        .expect("list answers");
    assert_eq!(list.owner_user_id, "user-a");
    assert_eq!(list.managers.len(), 1);
    assert_eq!(list.managers[0].user_id, "User-B");
}