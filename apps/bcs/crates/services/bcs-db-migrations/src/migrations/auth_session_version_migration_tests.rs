//! 042_auth_session_version migration tests: upgrade, no-re-clear, and
//! fresh-vs-upgraded schema equivalence (V1 API auth plugin chain).
//!
//! The version-42 migration adds the three strict-session columns to
//! `bcs_user_identities` and clears the historical opaque browser tokens
//! exactly once (spec §8.5/§9). These tests drive the REAL
//! `run_sqlite_migrations` runner so the recorded-version guard — not the
//! column guards — is what keeps a re-run from clearing live sessions a
//! second time.

use super::*;
use bcs_db_api::db_get_column_opt;
use bcs_db_local::LocalSqliteDbPlugin;

/// Record migrations 1..=41 over the runner's bootstrap tables, simulating a
/// pre-042 database, and return the database.
async fn pre_auth_session_database() -> LocalSqliteDbPlugin {
    let db = LocalSqliteDbPlugin::new().expect("sqlite db");
    run_sqlite_bootstrap_tables(&db).await.expect("bootstrap legacy tables");
    for migration in SQLITE_VERSIONED_MIGRATIONS
        .iter()
        .filter(|migration| migration.version <= 41)
    {
        apply_sqlite_migration(&db, migration)
            .await
            .expect("apply the pre-042 prefix");
    }
    run_sqlite_bootstrap_indexes(&db).await.expect("bootstrap legacy indexes");
    db
}

async fn identity_row(db: &LocalSqliteDbPlugin, user_id: &str) -> DbResult<(Option<String>, Option<i64>)> {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT token, session_expires_at FROM bcs_user_identities WHERE user_id = ?",
            vec![DbValue::from(user_id)],
        ))
        .await?;
    let Some(row) = rows.into_iter().next() else {
        panic!("identity row {user_id} must exist");
    };
    Ok((
        db_get_column_opt::<String>(&row, "token")?,
        db_get_column_opt::<i64>(&row, "session_expires_at")?,
    ))
}

/// The upgrade path: a legacy row carrying an opaque browser token is
/// invalidated exactly once, the three versioned-session columns appear with
/// their pinned defaults, and a live session written after the migration
/// survives every later migration re-run.
#[tokio::test]
async fn auth_session_version_upgrade_clears_legacy_tokens_once() -> DbResult<()> {
    let db = pre_auth_session_database().await;
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_user_identities \
         (user_id, auth_source, external_user_id, user_name, external_user_name, avatar, token, token_expire_at, env) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        vec![
            DbValue::from("upgrade-browser-user"),
            DbValue::from("github"),
            DbValue::from("upgrade-ext-1"),
            DbValue::from("升级用户"),
            DbValue::from("升级用户"),
            DbValue::from("https://avatars.example/upgrade.png"),
            DbValue::from("legacy-opaque-jwt"),
            DbValue::from("2030-01-01 00:00:00"),
            DbValue::from("dev"),
        ],
    ))
    .await?;

    let history_sql = "SELECT version, name, dialect, checksum, applied_at FROM bcs_schema_migrations WHERE version <= 41 ORDER BY version";
    let existing_history = db.query(DbStatement::new(history_sql)).await?;
    assert_eq!(existing_history.len(), 41);
    assert_eq!(
        db_get_column::<String>(existing_history.last().expect("version 41 recorded"), "name")?,
        "group_agent_cards",
    );
    let pending_before = check_sqlite_migrations(&db).await?.pending_versions;
    assert_eq!(
        pending_before
            .iter()
            .map(|migration| (migration.version, migration.name.as_str()))
            .collect::<Vec<_>>(),
        vec![(42, "auth_session_version"), (43, "delivery_session_lookup")],
        "only versions after 41 are pending on the upgrade path"
    );

    run_sqlite_migrations(&db).await?;

    // New columns exist with their pinned defaults; the legacy token and its
    // datetime expiry are cleared.
    let columns = sqlite_table_columns(&db, "bcs_user_identities").await?;
    for column in [
        "session_id",
        "session_revision",
        "session_expires_at",
    ] {
        assert!(columns.iter().any(|existing| existing == column), "missing {column}");
    }
    let (legacy_token, legacy_expires) =
        identity_row(&db, "upgrade-browser-user").await?;
    assert_eq!(legacy_token, None, "the historical opaque token is invalidated");
    assert_eq!(legacy_expires, Some(0));
    let details = db
        .query(DbStatement::with_params(
            "SELECT session_id, token_expire_at, session_revision FROM bcs_user_identities \
             WHERE user_id = ?",
            vec![DbValue::from("upgrade-browser-user")],
        ))
        .await?;
    let row = &details[0];
    assert_eq!(db_get_column_opt::<String>(row, "session_id")?, None);
    assert_eq!(db_get_column_opt::<String>(row, "token_expire_at")?, None);
    assert_eq!(db_get_column::<i64>(row, "session_revision")?, 0);

    // A live session installed after the migration must survive every later
    // migration re-run: the recorded version-42 row skips the whole body, so
    // the clear never runs a second time.
    db.execute(DbStatement::with_params(
        "UPDATE bcs_user_identities \
         SET token = ?, session_id = ?, session_revision = ?, session_expires_at = ? \
         WHERE user_id = ?",
        vec![
            DbValue::from("live-token-hash"),
            DbValue::from("live-session-id"),
            DbValue::from(7_i64),
            DbValue::from(1_900_000_000_i64),
            DbValue::from("upgrade-browser-user"),
        ],
    ))
    .await?;
    run_sqlite_migrations(&db).await?;
    run_sqlite_migrations(&db).await?;
    let (token, expires) = identity_row(&db, "upgrade-browser-user").await?;
    assert_eq!(
        token,
        Some("live-token-hash".to_string()),
        "a recorded migration never clears sessions again"
    );
    assert_eq!(expires, Some(1_900_000_000));
    assert_eq!(db.query(DbStatement::new(history_sql)).await?, existing_history,
        "OAuth upgrade must preserve every upstream migration record and checksum");
    let record = applied_sqlite_migration(&db, 42).await?.expect("version 42 recorded");
    assert_eq!(record.name, "auth_session_version");
    assert_eq!(record.dialect, "sqlite");
    Ok(())
}

/// Fresh vs upgraded: a database that runs the whole chain from empty and a
/// database that comes from the 1..=41 prefix end with the same
/// `bcs_user_identities` columns and the same recorded version history.
#[tokio::test]
async fn auth_session_schema_is_equivalent_fresh_and_upgraded() -> DbResult<()> {
    let fresh = LocalSqliteDbPlugin::new()?;
    run_sqlite_migrations(&fresh).await?;

    let upgraded = pre_auth_session_database().await;
    run_sqlite_migrations(&upgraded).await?;

    let fresh_columns = sqlite_table_columns(&fresh, "bcs_user_identities").await?;
    let upgraded_columns = sqlite_table_columns(&upgraded, "bcs_user_identities").await?;
    assert_eq!(
        fresh_columns, upgraded_columns,
        "fresh and upgraded databases must expose the same identity schema"
    );
    for column in [
        "session_id",
        "session_revision",
        "session_expires_at",
    ] {
        assert!(fresh_columns.iter().any(|existing| existing == column));
    }

    async fn history(db: &LocalSqliteDbPlugin) -> DbResult<Vec<bcs_db_api::DbRow>> {
        db.query(DbStatement::new(
            "SELECT version, name, dialect, checksum FROM bcs_schema_migrations ORDER BY version",
        ))
        .await
    }
    assert_eq!(history(&fresh).await?, history(&upgraded).await?);
    assert_eq!(history(&fresh).await?, history(&upgraded).await?);

    assert_eq!(
        current_sqlite_version(&fresh, true).await?,
        Some(sqlite_target_version())
    );
    assert_eq!(
        current_sqlite_version(&upgraded, true).await?,
        Some(sqlite_target_version())
    );
    Ok(())
}

/// The published MySQL 042 file is additive on the identity table, clears the
/// historical tokens exactly once, and matches the SQLite column semantics
/// modulo dialect. The tooling (`bcs-admin db migrate`) records the version
/// and its file checksum; the file records nothing itself.
#[test]
fn mysql_auth_session_version_migration_mirrors_sqlite_semantics() {
    let migration = include_str!("../../../../../migrations/mysql/042_auth_session_version.sql");
    for fragment in [
        "ADD COLUMN `session_id` VARCHAR(64) DEFAULT NULL",
        "ADD COLUMN `session_revision` BIGINT NOT NULL DEFAULT 0",
        "ADD COLUMN `session_expires_at` BIGINT NOT NULL DEFAULT 0",
    ] {
        assert!(
            migration.contains(fragment),
            "mysql 042 must add {fragment} in one ALTER"
        );
    }
    assert!(
        migration.contains("UPDATE `bcs_user_identities` SET `token` = NULL, `token_expire_at` = NULL"),
        "mysql 042 must clear the historical opaque tokens"
    );
    assert!(
        !migration.contains("bcs_schema_migrations"),
        "the apply tooling records the version; the file records nothing itself"
    );
    assert!(
        !migration.contains("CREATE INDEX") && !migration.contains("ADD INDEX"),
        "the migration adds no index: the existing uk_user_id unique index stays the scope address"
    );
    let sqlite = include_str!("../../../../../migrations/sqlite/042_auth_session_version.sql");
    for column in ["session_id", "session_revision", "session_expires_at"] {
        assert!(
            sqlite.contains(&format!("ADD COLUMN {column} ")),
            "sqlite 042 must add {column}"
        );
    }
    assert!(sqlite.contains("UPDATE bcs_user_identities SET token = NULL, token_expire_at = NULL"));
}
