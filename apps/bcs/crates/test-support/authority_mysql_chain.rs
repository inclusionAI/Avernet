//! MySQL conformance-gate bootstrap for the bot-authority
//! plan §6 gates (Task 8 / spec §6).
//!
//! Shared by the four plan §6 gate entries via
//! `#[path = ".../authority_mysql_chain.rs"]` — deliberately
//! NOT compiled into `bcs-test-support` (test binaries only).
//!
//! The ignored MySQL conformance gates run against an ISOLATED,
//! disposable test database: the URL comes from
//! `BCS_TEST_MYSQL_URL` (missing URL fails the gate immediately —
//! 启动缺URL立即失败), the target database must start EMPTY
//! (the full migration chain requires an empty database), and
//! the FULL MySQL migration chain (`migrations/mysql/001..N`)
//! is applied on top so every store/composite gate exercises the
//! real post-migration schema.
//!
//! The chain application mirrors `bcs-admin migrate` (statement
//! splitting, checksum records in `bcs_schema_migrations`) so the
//! resulting database is indistinguishable from a migrated
//! production schema. The URL is never logged.

#![allow(dead_code)]

use std::fs;
use std::path::PathBuf;
use std::sync::Arc;

use bcs_config_api::mysql::MysqlConnectionConfig;
use bcs_config_api::{MysqlDbConfig, StatementProtocol};
use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
use mysql_async::Opts;
use sha2::{Digest, Sha256};

/// One disposable MySQL test database with the full migration
/// chain applied. The caller owns closing the pool.
pub struct MysqlConformanceDb {
    pub plugin: Arc<MysqlDbPlugin>,
    manager: MysqlDbManager,
}

impl MysqlConformanceDb {
    /// Open the disposable database named by `BCS_TEST_MYSQL_URL`.
    ///
    /// Fail-fast rules (plan §6): the URL must be present, it must
    /// carry a database name, and that database must be EMPTY — a
    /// dirty database is refused instead of silently re-migrating
    /// (migration chains must not run concurrently/serially on the
    /// same dirty database).
    pub async fn open_empty() -> Self {
        let url = std::env::var("BCS_TEST_MYSQL_URL").expect(
            "BCS_TEST_MYSQL_URL must be set to run the ignored MySQL conformance gate",
        );
        let opts =
            Opts::from_url(&url).expect("BCS_TEST_MYSQL_URL must be a valid MySQL URL");
        let mut config = MysqlDbConfig::new()
            .with_database(opts.db_name().expect(
                "BCS_TEST_MYSQL_URL must include an isolated disposable database name",
            ))
            .with_connection(MysqlConnectionConfig {
                connection_type: "direct".to_string(),
                host: Some(opts.ip_or_hostname().to_string()),
                port: Some(opts.tcp_port()),
                user: opts.user().map(str::to_string),
                password: opts.pass().map(str::to_string),
                extra: Default::default(),
            })
            .with_statement_protocol(StatementProtocol::Text);
        config.pool_size = 4;
        config.min_pool_size = 1;

        let manager =
            MysqlDbManager::new(config).await.expect("open empty conformance MySQL database");
        let plugin = Arc::new(MysqlDbPlugin::new(manager.clone(), "bcs"));
        assert_empty_schema(plugin.as_ref()).await;
        Self { plugin, manager }
    }

    /// Apply the full MySQL migration chain and close nothing.
    pub async fn apply_full_migration_chain(&self) -> Vec<u16> {
        apply_full_mysql_migration_chain(self.plugin.as_ref()).await
    }

    pub async fn close(self) {
        self.manager.close().await;
    }
}

async fn assert_empty_schema(db: &dyn DbPlugin) {
    let rows = db
        .query(DbStatement::new(
            "SELECT COUNT(*) AS table_count FROM information_schema.tables \
             WHERE table_schema = DATABASE()",
        ))
        .await
        .expect("inspect conformance database tables");
    let table_count: i64 =
        bcs_db_api::db_get_column(&rows[0], "table_count").expect("table_count");
    assert_eq!(
        table_count, 0,
        "MySQL conformance gate requires an EMPTY disposable test database; \
         found {table_count} existing table(s)"
    );
}

/// Read every `migrations/mysql/NNN_name.sql` of the BCS root and
/// apply them in version order on an EMPTY database, recording the
/// same `bcs_schema_migrations` history rows `bcs-admin migrate`
/// writes. Returns the applied version numbers in order.
pub async fn apply_full_mysql_migration_chain(db: &dyn DbPlugin) -> Vec<u16> {
    // Pin the deterministic default engine mode so the chain
    // applies identically regardless of the test server's
    // global `sql_mode` configuration.
    db.execute(DbStatement::new(
        "SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION'",
    ))
    .await
    .expect("session defaults for migration chain");

    let migrations = load_mysql_migration_files();
    let mut applied = Vec::new();
    for migration in migrations {
        let statements = split_sql_statements(&migration.sql);
        assert!(
            !statements.is_empty(),
            "MySQL migration {:03} ({}) contains no executable SQL",
            migration.version,
            migration.name
        );
        for statement in statements {
            db.execute(DbStatement::new(&statement))
                .await
                .unwrap_or_else(|error| {
                    panic!(
                        "apply mysql migration {:03} ({}) failed: {error}\nstatement: {statement}",
                        migration.version, migration.name
                    )
                });
        }
        record_mysql_migration(db, &migration).await;
        applied.push(migration.version);
    }
    applied
}

struct MySqlMigrationFile {
    version: u16,
    name: String,
    sql: String,
}

/// Load `migrations/mysql/*.sql` from the BCS root in version
/// order. Duplicate version numbers fail fast (bcs-admin
/// `reject_duplicate_numbers` semantics).
fn load_mysql_migration_files() -> Vec<MySqlMigrationFile> {
    let dir = bcs_root().join("migrations").join("mysql");
    let mut migrations = Vec::new();
    for entry in
        fs::read_dir(&dir).unwrap_or_else(|_| panic!("read {}", dir.display()))
    {
        let entry = entry.expect("migration dir entry");
        let path = entry.path();
        if path.extension().and_then(|value| value.to_str()) != Some("sql") {
            continue;
        }
        let file_name = path.file_stem().expect("file stem").to_string_lossy().into_owned();
        let (version, name) = file_name
            .split_once('_')
            .and_then(|(prefix, name)| {
                prefix.parse::<u16>().ok().map(|number| (number, name.to_string()))
            })
            .unwrap_or_else(|| panic!("migration file name mismatch: {file_name}"));
        let sql = fs::read_to_string(&path)
            .unwrap_or_else(|_| panic!("read migration {}", path.display()));
        migrations.push(MySqlMigrationFile { version, name, sql });
    }
    migrations.sort_by(|left, right| left.version.cmp(&right.version));
    let mut seen = std::collections::BTreeSet::new();
    for migration in &migrations {
        assert!(
            seen.insert(migration.version),
            "duplicate MySQL migration version {:03} in migrations/mysql",
            migration.version
        );
    }
    migrations
}

fn bcs_root() -> PathBuf {
    // crates/test-support/bcs-test-support -> apps/bcs
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("..")
}

/// Map a MySQL-ism-free statement string out of one migration body
/// — mirroring `bcs-admin`'s `split_sql_statements` state machine
/// (quotes, backticks, line and block comments).
fn split_sql_statements(sql: &str) -> Vec<String> {
    let mut statements = Vec::new();
    let mut current = String::new();
    let mut chars = sql.chars().peekable();
    let mut state = SqlSplitState::Normal;

    while let Some(ch) = chars.next() {
        match state {
            SqlSplitState::Normal => match ch {
                ';' => push_sql_statement(&mut statements, &mut current),
                '\'' | '"' | '`' => {
                    current.push(ch);
                    state = match ch {
                        '\'' => SqlSplitState::SingleQuote,
                        '"' => SqlSplitState::DoubleQuote,
                        _ => SqlSplitState::Backtick,
                    };
                }
                '-' if chars.peek() == Some(&'-') => {
                    current.push(ch);
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                    state = SqlSplitState::LineComment;
                }
                '#' => {
                    current.push(ch);
                    state = SqlSplitState::LineComment;
                }
                '/' if chars.peek() == Some(&'*') => {
                    current.push(ch);
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                    state = SqlSplitState::BlockComment;
                }
                _ => current.push(ch),
            },
            SqlSplitState::SingleQuote => {
                current.push(ch);
                if ch == '\\' {
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                } else if ch == '\'' && chars.peek() != Some(&'\'') {
                    state = SqlSplitState::Normal;
                } else if ch == '\'' {
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                }
            }
            SqlSplitState::DoubleQuote => {
                current.push(ch);
                if ch == '\\' {
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                } else if ch == '"' && chars.peek() != Some(&'"') {
                    state = SqlSplitState::Normal;
                } else if ch == '"' {
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                }
            }
            SqlSplitState::Backtick => {
                current.push(ch);
                if ch == '`' && chars.peek() == Some(&'`') {
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                } else if ch == '`' {
                    state = SqlSplitState::Normal;
                }
            }
            SqlSplitState::LineComment => {
                current.push(ch);
                if ch == '\n' {
                    state = SqlSplitState::Normal;
                }
            }
            SqlSplitState::BlockComment => {
                current.push(ch);
                if ch == '*' && chars.peek() == Some(&'/') {
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                    state = SqlSplitState::Normal;
                }
            }
        }
    }
    push_sql_statement(&mut statements, &mut current);
    statements
}

fn push_sql_statement(statements: &mut Vec<String>, current: &mut String) {
    if sql_statement_has_code(current) {
        statements.push(current.trim().to_string());
    }
    current.clear();
}

fn sql_statement_has_code(statement: &str) -> bool {
    let mut chars = statement.chars().peekable();
    let mut state = SqlSplitState::Normal;
    while let Some(ch) = chars.next() {
        match state {
            SqlSplitState::Normal => match ch {
                ch if ch.is_whitespace() => {}
                '-' if chars.peek() == Some(&'-') => {
                    let _ = chars.next();
                    state = SqlSplitState::LineComment;
                }
                '#' => state = SqlSplitState::LineComment,
                '/' if chars.peek() == Some(&'*') => {
                    let _ = chars.next();
                    state = SqlSplitState::BlockComment;
                }
                _ => return true,
            },
            SqlSplitState::LineComment => {
                if ch == '\n' {
                    state = SqlSplitState::Normal;
                }
            }
            SqlSplitState::BlockComment => {
                if ch == '*' && chars.peek() == Some(&'/') {
                    state = SqlSplitState::Normal;
                }
            }
            SqlSplitState::SingleQuote | SqlSplitState::DoubleQuote
            | SqlSplitState::Backtick => {
                return true;
            }
        }
    }
    false
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SqlSplitState {
    Normal,
    SingleQuote,
    DoubleQuote,
    Backtick,
    LineComment,
    BlockComment,
}

async fn record_mysql_migration(db: &dyn DbPlugin, migration: &MySqlMigrationFile) {
    let checksum = declared_record_checksum(migration.version, &migration.sql)
        .unwrap_or_else(|| {
            let mut hasher = Sha256::new();
            hasher.update(migration.sql.as_bytes());
            let digest = hasher.finalize();
            format!("{digest:x}")
        });
    // Mirror `bcs-admin` `ensure_mysql_migration_record` verbatim:
    // the baseline migration 001 self-records its history row
    // (`INSERT IGNORE INTO bcs_schema_migrations ... VALUES
    // (1, ...)` inside the migration body itself), so an existing
    // record for
    // the version must be validated instead of re-inserted — an
    // unconditional INSERT collides on the `version` primary key.
    let rows = db
        .query(DbStatement::with_params(
            "SELECT name, dialect, checksum FROM bcs_schema_migrations WHERE version = ?",
            vec![DbValue::from(i64::from(migration.version))],
        ))
        .await
        .unwrap_or_else(|error| {
            panic!(
                "query mysql migration record {:03} ({}) after apply: {error}",
                migration.version, migration.name
            )
        });
    if let Some(row) = rows.first() {
        let name: String = bcs_db_api::db_get_column(row, "name")
            .expect("read recorded migration name");
        let dialect: String = bcs_db_api::db_get_column(row, "dialect")
            .expect("read recorded migration dialect");
        let recorded: String = bcs_db_api::db_get_column(row, "checksum")
            .expect("read recorded migration checksum");
        assert_eq!(
            dialect, "mysql",
            "mysql migration {:03} ({}) recorded with dialect {dialect}",
            migration.version, migration.name
        );
        assert_eq!(
            name, migration.name,
            "mysql migration {:03} recorded as {name}, expected {}",
            migration.version, migration.name
        );
        assert_eq!(
            recorded, checksum,
            "mysql migration {:03} ({}) recorded checksum mismatch",
            migration.version, migration.name
        );
        return;
    }

    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_schema_migrations (version, name, dialect, checksum) \
         VALUES (?, ?, 'mysql', ?)",
        vec![
            DbValue::from(i64::from(migration.version)),
            DbValue::from(migration.name.as_str()),
            DbValue::from(checksum.as_str()),
        ],
    ))
    .await
    .unwrap_or_else(|error| {
        panic!(
            "record mysql migration {:03} ({}) after apply: {error}",
            migration.version, migration.name
        )
    });
}

/// The declared `bcs_schema_migrations` checksum embedded in some
/// migration files (`values (N, 'mysql', '<checksum>')`),
/// mirroring `bcs-admin`'s `mysql_declared_record_checksum`.
fn declared_record_checksum(version: u16, sql: &str) -> Option<String> {
    let compact = sql.split_whitespace().collect::<Vec<_>>().join(" ");
    let lower = compact.to_ascii_lowercase();
    let marker = format!("values ({version},");
    let start = lower.find(&marker)?;
    let values = parse_single_quoted_values(&compact[start + marker.len()..]);
    if values.len() < 3 || values[1] != "mysql" {
        return None;
    }
    Some(values[2].clone())
}

fn parse_single_quoted_values(input: &str) -> Vec<String> {
    let mut values = Vec::new();
    let mut chars = input.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch != '\'' {
            continue;
        }
        let mut value = String::new();
        while let Some(inner) = chars.next() {
            if inner == '\'' {
                if chars.peek() == Some(&'\'') {
                    let _ = chars.next();
                    value.push('\'');
                    continue;
                }
                break;
            }
            value.push(inner);
        }
        values.push(value);
    }
    values
}

/// Stable harness-marker line for the CI scenario-inventory check
/// (`authority-mysql-gate=<gate> scenarios=<n>`). Prints one
/// `authority-mysql-scenario:<name>` line per scenario at
/// `--nocapture` verbose pressure-free diagnosis granularity and
/// the single summary line the CI step greps.
pub fn report_mysql_gate_scenarios(gate: &str, scenarios: &[&str]) {
    for scenario in scenarios {
        println!("authority-mysql-scenario:{scenario}");
    }
    println!(
        "authority-mysql-gate={gate} scenarios={}",
        scenarios.len()
    );
}
