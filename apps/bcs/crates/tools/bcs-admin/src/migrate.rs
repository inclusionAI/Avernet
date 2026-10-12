use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::{self, Write},
    path::{Path, PathBuf},
};

use anyhow::{Context, Result, anyhow, bail};
use bcs::{BcsConfig, DatabaseType};
use bcs_db_api::{DbPlugin, DbStatement, DbValue, db_get_column};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_db_mysql::{MysqlDbManager, MysqlDbPlugin};
use clap::{Args, ValueEnum};
use sha2::{Digest, Sha256};

use crate::bcs_root;

mod history;
mod index;
use history::*;
use index::*;

#[derive(Debug, Clone)]
pub struct MigrateGlobalArgs {
    pub config_dir: Option<PathBuf>,
    pub config_file: Option<PathBuf>,
}

#[derive(Debug, Args)]
pub struct MigrateArgs {
    /// Migration dialect. When omitted, bcs-admin infers it from config for
    /// check/apply modes and keeps MySQL as the emit-sql default.
    #[arg(long, value_enum)]
    pub dialect: Option<MigrationDialect>,

    /// Directory containing numbered SQL migration files.
    /// Defaults to `migrations/mysql`.
    #[arg(long)]
    pub migrations_dir: Option<PathBuf>,

    /// SQLite database file. Defaults to `[database.sqlite].path` from config.
    #[arg(long)]
    pub sqlite_path: Option<PathBuf>,

    /// Emit SQL without connecting to a database.
    #[arg(long)]
    pub emit_sql: bool,

    /// Check local migration files/definitions without connecting to a database.
    #[arg(long)]
    pub check_files: bool,

    /// Check the configured database migration state without applying changes.
    #[arg(long)]
    pub check_db: bool,

    /// Apply migrations.
    #[arg(long)]
    pub apply: bool,

    /// Confirm apply mode without prompting.
    #[arg(short = 'y', long)]
    pub yes: bool,

    /// Select one migration number. May be repeated.
    #[arg(long)]
    pub only: Vec<u16>,

    /// Select migrations with number >= this value.
    #[arg(long)]
    pub from: Option<u16>,

    /// Select migrations with number <= this value.
    #[arg(long)]
    pub to: Option<u16>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub enum MigrationDialect {
    Mysql,
    Sqlite,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum MigrationMode {
    EmitSql,
    CheckFiles,
    CheckDb,
    Apply,
}

impl MigrationMode {
    fn from_args(args: &MigrateArgs) -> Result<Self> {
        let selected = [args.emit_sql, args.check_files, args.check_db, args.apply]
            .into_iter()
            .filter(|value| *value)
            .count();
        if selected != 1 {
            bail!(
                "choose exactly one migration mode: --emit-sql, --check-files, --check-db, or --apply"
            );
        }
        if args.emit_sql {
            Ok(Self::EmitSql)
        } else if args.check_files {
            Ok(Self::CheckFiles)
        } else if args.check_db {
            Ok(Self::CheckDb)
        } else {
            Ok(Self::Apply)
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Migration {
    pub number: u16,
    pub name: String,
    pub path: PathBuf,
    pub sql: String,
}

pub async fn run_migrate(args: &MigrateArgs, global: &MigrateGlobalArgs) -> Result<()> {
    let mode = MigrationMode::from_args(args)?;
    let dialect = resolve_dialect(args, global, mode)?;

    match dialect {
        MigrationDialect::Mysql => match mode {
            MigrationMode::EmitSql => {
                let sql = emit_migration_sql(args)?;
                print!("{sql}");
                Ok(())
            }
            MigrationMode::CheckFiles => {
                let summary = check_mysql_migration_files(args)?;
                println!("{summary}");
                Ok(())
            }
            MigrationMode::CheckDb => {
                let summary = check_mysql_migration_state(args, global).await?;
                println!("{summary}");
                Ok(())
            }
            MigrationMode::Apply => {
                let summary = apply_mysql_migrations(args, global).await?;
                println!("{summary}");
                Ok(())
            }
        },
        MigrationDialect::Sqlite => run_sqlite_migrate(args, global, mode).await,
    }
}

pub fn emit_migration_sql(args: &MigrateArgs) -> Result<String> {
    let migrations = load_selected_migrations(args)?;
    let mut output = String::new();
    for migration in migrations {
        output.push_str(&format!(
            "-- Migration {:03}: {}\n",
            migration.number, migration.name
        ));
        output.push_str(migration.sql.trim());
        output.push_str("\n\n");
    }
    Ok(output)
}

pub fn check_mysql_migration_files(args: &MigrateArgs) -> Result<String> {
    let migrations = load_selected_migrations(args)?;
    reject_duplicate_numbers(&migrations)?;
    validate_mysql_index_lengths(&migrations)?;
    let baseline = migrations
        .iter()
        .find(|migration| migration.number == 1)
        .ok_or_else(|| anyhow!("missing MySQL/OceanBase baseline migration 001"))?;

    if !baseline
        .sql
        .contains("CREATE TABLE IF NOT EXISTS `bcs_schema_migrations`")
    {
        bail!("baseline migration 001 does not create bcs_schema_migrations");
    }
    if !baseline
        .sql
        .contains("INSERT IGNORE INTO `bcs_schema_migrations`")
    {
        bail!("baseline migration 001 does not record bcs_schema_migrations version 1");
    }
    if !baseline
        .sql
        .contains("VALUES (1, 'init_schema', 'mysql'")
    {
        bail!("baseline migration 001 does not record the mysql init_schema baseline");
    }

    let versions = migrations
        .iter()
        .map(|migration| format!("{:03}", migration.number))
        .collect::<Vec<_>>()
        .join(", ");
    let checksum = mysql_migration_plan(baseline).checksum;
    Ok(format!(
        "MySQL/OceanBase migration files check ok\nmigrations={}\nversions={}\nbaseline_checksum={}",
        migrations.len(),
        versions,
        checksum
    ))
}

pub fn load_selected_migrations(args: &MigrateArgs) -> Result<Vec<Migration>> {
    let selection = MigrationSelection::from_args(args)?;
    let dir = args
        .migrations_dir
        .clone()
        .unwrap_or_else(|| bcs_root().join("migrations").join("mysql"));
    let mut migrations = Vec::new();
    let should_reject_duplicate_numbers = !matches!(selection, MigrationSelection::All);

    for entry in fs::read_dir(&dir)
        .with_context(|| format!("read migrations dir '{}'", dir.display()))?
    {
        let entry = entry?;
        if !entry.file_type()?.is_file() {
            continue;
        }
        let path = entry.path();
        if path.extension().and_then(|value| value.to_str()) != Some("sql") {
            continue;
        }
        let Some((number, name)) = parse_migration_file_name(&path) else {
            continue;
        };
        if !selection.includes(number) {
            continue;
        }
        let sql = fs::read_to_string(&path)
            .with_context(|| format!("read migration '{}'", path.display()))?;
        migrations.push(Migration {
            number,
            name,
            path,
            sql,
        });
    }

    if should_reject_duplicate_numbers {
        reject_duplicate_numbers(&migrations)?;
    }
    migrations.sort_by(|left, right| {
        left.number
            .cmp(&right.number)
            .then_with(|| left.name.cmp(&right.name))
    });
    if migrations.is_empty() {
        bail!("no migration files matched the requested selection");
    }
    Ok(migrations)
}

async fn check_mysql_migration_state(
    args: &MigrateArgs,
    global: &MigrateGlobalArgs,
) -> Result<String> {
    let migrations = load_selected_migrations(args)?;
    reject_duplicate_numbers(&migrations)?;
    let plans = migrations.iter().map(mysql_migration_plan).collect::<Vec<_>>();
    let all_selected = args.only.is_empty() && args.from.is_none() && args.to.is_none();
    let mysql_db = open_configured_mysql_db(global).await?;
    let result = async {
        let applied = load_applied_mysql_migrations(&mysql_db.plugin).await?;
        let report = build_mysql_migration_report(
            mysql_db.datasource_name.clone(),
            plans,
            applied,
            all_selected,
        )?;
        Ok(format_mysql_check_report(&report))
    }
    .await;
    mysql_db.manager.close().await;
    result
}

struct ConfiguredMysqlDb {
    manager: MysqlDbManager,
    plugin: MysqlDbPlugin,
    datasource_name: String,
}

async fn open_configured_mysql_db(global: &MigrateGlobalArgs) -> Result<ConfiguredMysqlDb> {
    let config = load_bcs_config(global)?;
    let mysql = config.database.mysql.clone();
    if config.database.database_type != DatabaseType::Mysql {
        bail!("configured database type is not mysql");
    }
    let datasource_name = mysql.datasource_name();

    let manager = MysqlDbManager::new(mysql)
        .await
        .map_err(|err| anyhow!("open mysql datasource '{}': {}", datasource_name, err))?;
    let plugin = MysqlDbPlugin::new(manager.clone(), datasource_name.clone());
    Ok(ConfiguredMysqlDb {
        manager,
        plugin,
        datasource_name,
    })
}

async fn apply_mysql_migrations(
    args: &MigrateArgs,
    global: &MigrateGlobalArgs,
) -> Result<String> {
    let migrations = load_selected_migrations(args)?;
    reject_duplicate_numbers(&migrations)?;
    validate_mysql_index_lengths(&migrations)?;
    let migrations_by_version = migrations
        .into_iter()
        .map(|migration| (migration.number, migration))
        .collect::<BTreeMap<_, _>>();
    let plans = migrations_by_version
        .values()
        .map(mysql_migration_plan)
        .collect::<Vec<_>>();
    let all_selected = args.only.is_empty() && args.from.is_none() && args.to.is_none();
    let mysql_db = open_configured_mysql_db(global).await?;
    let result = async {
        let applied = load_applied_mysql_migrations(&mysql_db.plugin).await?;
        let report = build_mysql_migration_report(
            mysql_db.datasource_name.clone(),
            plans.clone(),
            applied,
            all_selected,
        )?;
        if report.pending_versions.is_empty() {
            return Ok(format_mysql_apply_report(&report, &[]));
        }
        if !confirm_mysql_apply(args.yes, &report)? {
            return Ok(format_mysql_apply_cancelled(&report));
        }

        let mut applied_plans = Vec::new();
        for plan in report.pending_versions.clone() {
            let migration = migrations_by_version.get(&plan.version).ok_or_else(|| {
                anyhow!(
                    "selected migration {:03} is missing from the loaded migration set",
                    plan.version
                )
            })?;
            apply_mysql_migration(&mysql_db.plugin, migration, &plan).await?;
            applied_plans.push(plan);
        }

        let applied = load_applied_mysql_migrations(&mysql_db.plugin).await?;
        let report = build_mysql_migration_report(
            mysql_db.datasource_name.clone(),
            plans,
            applied,
            all_selected,
        )?;
        Ok(format_mysql_apply_report(&report, &applied_plans))
    }
    .await;
    mysql_db.manager.close().await;
    result
}

async fn apply_mysql_migration(
    db: &dyn DbPlugin,
    migration: &Migration,
    plan: &MysqlMigrationPlan,
) -> Result<()> {
    let statements = split_sql_statements(&migration.sql);
    if statements.is_empty() {
        bail!(
            "mysql migration {:03} ({}) contains no executable SQL statements",
            migration.number,
            migration.name
        );
    }
    for (index, statement) in statements.into_iter().enumerate() {
        db.execute(DbStatement::new(statement))
            .await
            .map_err(|err| {
                anyhow!(
                    "apply mysql migration {:03} ({}) statement {}: {}",
                    migration.number,
                    migration.name,
                    index + 1,
                    err
                )
            })?;
    }
    ensure_mysql_migration_record(db, plan).await
}

async fn ensure_mysql_migration_record(
    db: &dyn DbPlugin,
    plan: &MysqlMigrationPlan,
) -> Result<()> {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT name, dialect, checksum FROM bcs_schema_migrations WHERE version = ?",
            vec![DbValue::from(i64::from(plan.version))],
        ))
        .await
        .map_err(|err| {
            anyhow!(
                "query mysql migration record {:03} after apply: {}",
                plan.version,
                err
            )
        })?;
    if let Some(row) = rows.first() {
        let applied = AppliedMysqlMigration {
            version: i64::from(plan.version),
            name: db_get_column(row, "name")?,
            dialect: db_get_column(row, "dialect")?,
            checksum: db_get_column(row, "checksum")?,
        };
        validate_mysql_migration_record(plan, &applied)?;
        return Ok(());
    }

    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_schema_migrations (version, name, dialect, checksum) VALUES (?, ?, 'mysql', ?)",
        vec![
            DbValue::from(i64::from(plan.version)),
            DbValue::from(plan.name.as_str()),
            DbValue::from(plan.checksum.as_str()),
        ],
    ))
    .await
    .map_err(|err| {
        anyhow!(
            "record mysql migration {:03} ({}) after apply: {}",
            plan.version,
            plan.name,
            err
        )
    })?;
    Ok(())
}

fn confirm_mysql_apply(yes: bool, report: &MysqlMigrationCheckReport) -> Result<bool> {
    if yes {
        return Ok(true);
    }

    eprintln!("{}", format_mysql_apply_confirmation(report));
    eprint!("Apply pending MySQL/OceanBase migrations? [y/N] ");
    io::stderr().flush()?;
    let mut answer = String::new();
    let bytes = io::stdin().read_line(&mut answer)?;
    if bytes == 0 {
        bail!("confirmation required; pass -y/--yes to apply non-interactively");
    }
    Ok(is_yes_confirmation(&answer))
}

fn is_yes_confirmation(answer: &str) -> bool {
    matches!(answer.trim().to_ascii_lowercase().as_str(), "y" | "yes")
}

fn format_mysql_apply_confirmation(report: &MysqlMigrationCheckReport) -> String {
    let mut output = format!(
        "About to apply MySQL/OceanBase migrations\ndatasource={}\ncurrent_version={}\ntarget_version={}\npending_versions={}",
        report.datasource,
        format_version(report.current_version),
        report
            .target_version
            .map(|version| version.to_string())
            .unwrap_or_else(|| "<none>".to_string()),
        report.pending_versions.len()
    );
    for plan in &report.pending_versions {
        output.push_str(&format!(
            "\n- {:03} {} checksum={}",
            plan.version, plan.name, plan.checksum
        ));
    }
    output
}

fn format_mysql_apply_cancelled(report: &MysqlMigrationCheckReport) -> String {
    format!(
        "MySQL/OceanBase migration apply cancelled\ndatasource={}\npending_versions={}",
        report.datasource,
        report.pending_versions.len()
    )
}

fn format_mysql_apply_report(
    report: &MysqlMigrationCheckReport,
    applied_plans: &[MysqlMigrationPlan],
) -> String {
    let mut output = format!(
        "MySQL/OceanBase migrations applied\ndatasource={}\ncurrent_version={}\ntarget_version={}\napplied_versions={}\npending_versions={}",
        report.datasource,
        format_version(report.current_version),
        report
            .target_version
            .map(|version| version.to_string())
            .unwrap_or_else(|| "<none>".to_string()),
        applied_plans.len(),
        report.pending_versions.len()
    );
    for plan in applied_plans {
        output.push_str(&format!(
            "\n- {:03} {} checksum={}",
            plan.version, plan.name, plan.checksum
        ));
    }
    output
}

fn split_sql_statements(sql: &str) -> Vec<String> {
    let mut statements = Vec::new();
    let mut current = String::new();
    let mut chars = sql.chars().peekable();
    let mut state = SqlSplitState::Normal;

    while let Some(ch) = chars.next() {
        match state {
            SqlSplitState::Normal => match ch {
                ';' => {
                    push_sql_statement(&mut statements, &mut current);
                }
                '\'' => {
                    current.push(ch);
                    state = SqlSplitState::SingleQuote;
                }
                '"' => {
                    current.push(ch);
                    state = SqlSplitState::DoubleQuote;
                }
                '`' => {
                    current.push(ch);
                    state = SqlSplitState::Backtick;
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
                } else if ch == '\'' {
                    if chars.peek() == Some(&'\'') {
                        if let Some(next) = chars.next() {
                            current.push(next);
                        }
                    } else {
                        state = SqlSplitState::Normal;
                    }
                }
            }
            SqlSplitState::DoubleQuote => {
                current.push(ch);
                if ch == '\\' {
                    if let Some(next) = chars.next() {
                        current.push(next);
                    }
                } else if ch == '"' {
                    if chars.peek() == Some(&'"') {
                        if let Some(next) = chars.next() {
                            current.push(next);
                        }
                    } else {
                        state = SqlSplitState::Normal;
                    }
                }
            }
            SqlSplitState::Backtick => {
                current.push(ch);
                if ch == '`' {
                    if chars.peek() == Some(&'`') {
                        if let Some(next) = chars.next() {
                            current.push(next);
                        }
                    } else {
                        state = SqlSplitState::Normal;
                    }
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
                    let _ = chars.next();
                    state = SqlSplitState::Normal;
                }
            }
            SqlSplitState::SingleQuote | SqlSplitState::DoubleQuote | SqlSplitState::Backtick => {
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

async fn run_sqlite_migrate(
    args: &MigrateArgs,
    global: &MigrateGlobalArgs,
    mode: MigrationMode,
) -> Result<()> {
    let sqlite_path = resolve_sqlite_path(args, global)?;

    match mode {
        MigrationMode::EmitSql => {
            let summary = emit_sqlite_migration_plan(&sqlite_path).await?;
            print!("{summary}");
            Ok(())
        }
        MigrationMode::CheckFiles => {
            println!("{}", check_sqlite_migration_definitions());
            Ok(())
        }
        MigrationMode::CheckDb => {
            let summary = check_sqlite_migration_state(&sqlite_path).await?;
            println!("{summary}");
            Ok(())
        }
        MigrationMode::Apply => {
            let db = open_sqlite_db(&sqlite_path)?;
            let report = bcs::migrations::run_sqlite_migrations_with_report(&db)
                .await
                .map_err(|err| anyhow!("run sqlite migrations: {}", err))?;
            println!("{}", format_sqlite_apply_report(&sqlite_path, &report));
            Ok(())
        }
    }
}

fn resolve_dialect(
    args: &MigrateArgs,
    global: &MigrateGlobalArgs,
    mode: MigrationMode,
) -> Result<MigrationDialect> {
    if let Some(dialect) = args.dialect {
        return Ok(dialect);
    }
    if args.sqlite_path.is_some() {
        return Ok(MigrationDialect::Sqlite);
    }
    if global.config_dir.is_some()
        || global.config_file.is_some()
        || matches!(mode, MigrationMode::CheckDb | MigrationMode::Apply)
    {
        let config = load_bcs_config(global)?;
        return Ok(match &config.database.database_type {
            DatabaseType::Sqlite => MigrationDialect::Sqlite,
            DatabaseType::Mysql => MigrationDialect::Mysql,
            DatabaseType::Other(provider) => {
                bail!("configured database type '{}' is not supported by bcs-admin migrations", provider)
            }
        });
    }
    Ok(MigrationDialect::Mysql)
}

fn resolve_sqlite_path(args: &MigrateArgs, global: &MigrateGlobalArgs) -> Result<PathBuf> {
    if let Some(path) = args.sqlite_path.as_ref() {
        return Ok(path.clone());
    }
    let config = load_bcs_config(global)?;
    Ok(PathBuf::from(config.database.sqlite.path))
}

fn load_bcs_config(global: &MigrateGlobalArgs) -> Result<BcsConfig> {
    if let Some(config_file) = global.config_file.as_ref() {
        return BcsConfig::from_file(config_file)
            .map_err(|err| anyhow!("load config file '{}': {}", config_file.display(), err));
    }

    let default_config_dir = PathBuf::from("configs");
    let config_dir = global.config_dir.as_ref().unwrap_or(&default_config_dir);
    BcsConfig::try_load_with_env(Some(config_dir))
        .map_err(|err| anyhow!("load config dir '{}': {}", config_dir.display(), err))
}

fn check_sqlite_migration_definitions() -> String {
    format!(
        "SQLite migration definitions check ok\ntarget_version={}\nmigrations={}\nnote=SQLite migrations are code-defined in crates/bootstrap/bcs/src/migrations.rs; use --check-db to inspect a SQLite database file",
        bcs::migrations::sqlite_target_version(),
        bcs::migrations::sqlite_migration_count()
    )
}

async fn check_sqlite_migration_state(sqlite_path: &Path) -> Result<String> {
    if !sqlite_path.exists() {
        return Ok(format!(
            "SQLite migration check ok\ndatabase={}\ncurrent_version=<none>\ntarget_version={}\npending_versions={}\nnote=fresh database will be created by --apply or BCS startup",
            sqlite_path.display(),
            bcs::migrations::sqlite_target_version(),
            bcs::migrations::sqlite_migration_count()
        ));
    }

    let db = open_sqlite_db(sqlite_path)?;
    let report = bcs::migrations::check_sqlite_migrations(&db)
        .await
        .map_err(|err| anyhow!("check sqlite migrations: {}", err))?;
    Ok(format_sqlite_check_report(sqlite_path, &report))
}

async fn emit_sqlite_migration_plan(sqlite_path: &Path) -> Result<String> {
    if !sqlite_path.exists() {
        return Ok(format!(
            "-- SQLite migration plan (diagnostic)\n-- database: {}\n-- database file does not exist; --apply or BCS startup will create the fresh v{} baseline.\n",
            sqlite_path.display(),
            bcs::migrations::sqlite_target_version()
        ));
    }

    let db = open_sqlite_db(sqlite_path)?;
    let report = bcs::migrations::check_sqlite_migrations(&db)
        .await
        .map_err(|err| anyhow!("check sqlite migrations: {}", err))?;
    let mut output = String::new();
    output.push_str("-- SQLite migration plan (diagnostic)\n");
    output.push_str(&format!("-- database: {}\n", sqlite_path.display()));
    output.push_str("-- Actual apply uses the code-defined SQLite migration runner.\n");
    if report.pending_versions.is_empty() {
        output.push_str("-- No pending SQLite migrations.\n");
        return Ok(output);
    }
    for migration in &report.pending_versions {
        output.push_str(&format!(
            "-- Migration {:03}: {} checksum={}\n",
            migration.version, migration.name, migration.checksum
        ));
        if migration.statements.is_empty() {
            output.push_str("-- DDL is code-defined; --apply runs the guarded schema changes before recording this version.\n");
        } else {
            for statement in &migration.statements {
                output.push_str(statement.trim());
                output.push_str(";\n");
            }
        }
    }
    Ok(output)
}

fn open_sqlite_db(sqlite_path: &Path) -> Result<LocalSqliteDbPlugin> {
    LocalSqliteDbPlugin::new_file(sqlite_path)
        .map_err(|err| anyhow!("open sqlite database '{}': {}", sqlite_path.display(), err))
}

fn format_sqlite_check_report(
    sqlite_path: &Path,
    report: &bcs::migrations::SqliteMigrationReport,
) -> String {
    let pending = format_sqlite_plans(&report.pending_versions);
    format!(
        "SQLite migration check ok\ndatabase={}\ncurrent_version={}\ntarget_version={}\npending_versions={}{}",
        sqlite_path.display(),
        format_version(report.current_version),
        report.target_version,
        report.pending_versions.len(),
        pending
    )
}

fn format_sqlite_apply_report(
    sqlite_path: &Path,
    report: &bcs::migrations::SqliteMigrationReport,
) -> String {
    let applied = format_sqlite_plans(&report.applied_versions);
    format!(
        "SQLite migrations applied\ndatabase={}\ncurrent_version={}\ntarget_version={}\napplied_versions={}\nrepaired_columns={}{}",
        sqlite_path.display(),
        format_version(report.current_version),
        report.target_version,
        report.applied_versions.len(),
        report.repaired_columns.len(),
        applied
    )
}

fn format_sqlite_plans(plans: &[bcs::migrations::SqliteMigrationPlan]) -> String {
    if plans.is_empty() {
        return String::new();
    }
    let mut output = String::new();
    for plan in plans {
        output.push_str(&format!(
            "\n- {:03} {} checksum={} statements={} repairs={}",
            plan.version,
            plan.name,
            plan.checksum,
            plan.statements.len(),
            if plan.repairs.is_empty() {
                "none".to_string()
            } else {
                plan.repairs.join(",")
            }
        ));
    }
    output
}

fn format_version(version: Option<i64>) -> String {
    version
        .map(|version| version.to_string())
        .unwrap_or_else(|| "<none>".to_string())
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hex::encode(hasher.finalize())
}

fn parse_migration_file_name(path: &Path) -> Option<(u16, String)> {
    let stem = path.file_stem()?.to_str()?;
    let (prefix, name) = stem.split_once('_')?;
    let number = prefix.parse::<u16>().ok()?;
    Some((number, name.to_string()))
}

fn reject_duplicate_numbers(migrations: &[Migration]) -> Result<()> {
    let mut names_by_number: BTreeMap<u16, Vec<String>> = BTreeMap::new();
    for migration in migrations {
        names_by_number
            .entry(migration.number)
            .or_default()
            .push(migration.path.display().to_string());
    }
    let duplicates: Vec<_> = names_by_number
        .into_iter()
        .filter(|(_number, paths)| paths.len() > 1)
        .collect();
    if duplicates.is_empty() {
        return Ok(());
    }

    let details = duplicates
        .into_iter()
        .map(|(number, paths)| format!("{number:03}: {}", paths.join(", ")))
        .collect::<Vec<_>>()
        .join("; ");
    bail!("duplicate migration numbers selected: {details}")
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum MigrationSelection {
    All,
    Only(BTreeSet<u16>),
    Range {
        from: Option<u16>,
        to: Option<u16>,
    },
}

impl MigrationSelection {
    fn from_args(args: &MigrateArgs) -> Result<Self> {
        if !args.only.is_empty() && (args.from.is_some() || args.to.is_some()) {
            bail!("--only cannot be combined with --from or --to");
        }
        if !args.only.is_empty() {
            return Ok(Self::Only(args.only.iter().copied().collect()));
        }
        if let (Some(from), Some(to)) = (args.from, args.to)
            && from > to
        {
            bail!("--from must be less than or equal to --to");
        }
        if args.from.is_some() || args.to.is_some() {
            return Ok(Self::Range {
                from: args.from,
                to: args.to,
            });
        }
        Ok(Self::All)
    }

    fn includes(&self, number: u16) -> bool {
        match self {
            Self::All => true,
            Self::Only(numbers) => numbers.contains(&number),
            Self::Range { from, to } => {
                from.is_none_or(|from| number >= from)
                    && to.is_none_or(|to| number <= to)
            }
        }
    }
}

#[cfg(test)]
mod tests;

#[cfg(test)]
#[path = "migrate_fixed_loop_tests.rs"]
mod fixed_loop_mysql_tests;

#[cfg(test)]
#[path = "migrate_016_tests.rs"]
mod merged_016_mysql_tests;

#[cfg(test)]
#[path = "migrate_human_input_index_tests.rs"]
mod human_input_index_mysql_tests;

#[cfg(test)]
#[path = "migrate_mysql_chain_tests.rs"]
mod mysql_chain_tests;

#[cfg(test)]
#[path = "migrate_history_window_tests.rs"]
mod history_window_tests;
