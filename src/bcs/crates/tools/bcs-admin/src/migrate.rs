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

const MYSQL_UTF8MB4_MAX_INDEX_BYTES: usize = 3072;
const MYSQL_UTF8MB4_BYTES_PER_CHAR: usize = 4;

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

fn validate_mysql_index_lengths(migrations: &[Migration]) -> Result<()> {
    for migration in migrations {
        let mut current_table: Option<(String, Vec<String>)> = None;
        for line in migration.sql.lines() {
            if let Some((table_name, body)) = current_table.as_mut() {
                if line.trim_start().starts_with(") DEFAULT CHARSET = utf8mb4") {
                    validate_mysql_table_index_lengths(migration, table_name, body)?;
                    current_table = None;
                } else {
                    body.push(line.to_string());
                }
                continue;
            }

            if let Some(table_name) = parse_mysql_create_table_name(line) {
                current_table = Some((table_name, Vec::new()));
            }
        }
    }
    Ok(())
}

fn validate_mysql_table_index_lengths(
    migration: &Migration,
    table_name: &str,
    body: &[String],
) -> Result<()> {
    let mut column_chars = BTreeMap::new();
    for line in body {
        if let Some((name, chars)) = parse_mysql_char_column(line) {
            column_chars.insert(name, chars);
        }
    }

    for line in body {
        let Some((index_name, parts)) = parse_mysql_named_index(line) else {
            continue;
        };
        let indexed_chars = parts
            .iter()
            .map(|part| {
                part.prefix_chars
                    .or_else(|| column_chars.get(&part.column).copied())
                    .unwrap_or(0)
            })
            .sum::<usize>();
        let indexed_bytes = indexed_chars * MYSQL_UTF8MB4_BYTES_PER_CHAR;
        if indexed_bytes > MYSQL_UTF8MB4_MAX_INDEX_BYTES {
            bail!(
                "mysql migration {:03} ({}) index {}.{} is too long for utf8mb4: {} bytes > {} bytes",
                migration.number,
                migration.name,
                table_name,
                index_name,
                indexed_bytes,
                MYSQL_UTF8MB4_MAX_INDEX_BYTES
            );
        }
    }
    Ok(())
}

fn parse_mysql_create_table_name(line: &str) -> Option<String> {
    let rest = line
        .trim_start()
        .strip_prefix("CREATE TABLE IF NOT EXISTS `")?;
    Some(rest.split('`').next()?.to_string())
}

fn parse_mysql_char_column(line: &str) -> Option<(String, usize)> {
    let trimmed = line.trim_start();
    let (name, rest) = parse_backtick_ident(trimmed)?;
    let rest = rest.trim_start().to_ascii_lowercase();
    let type_rest = rest
        .strip_prefix("varchar(")
        .or_else(|| rest.strip_prefix("char("))?;
    let length = type_rest.split(')').next()?.parse::<usize>().ok()?;
    Some((name, length))
}

fn parse_mysql_named_index(line: &str) -> Option<(String, Vec<MysqlIndexPart>)> {
    let trimmed = line.trim_start();
    let rest = trimmed
        .strip_prefix("UNIQUE KEY `")
        .or_else(|| trimmed.strip_prefix("KEY `"))?;
    let index_name = rest.split('`').next()?.to_string();
    let columns_start = rest.find('(')?;
    let columns_end = rest.rfind(')')?;
    if columns_end <= columns_start {
        return None;
    }
    Some((
        index_name,
        parse_mysql_index_parts(&rest[columns_start + 1..columns_end]),
    ))
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct MysqlIndexPart {
    column: String,
    prefix_chars: Option<usize>,
}

fn parse_mysql_index_parts(input: &str) -> Vec<MysqlIndexPart> {
    let mut parts = Vec::new();
    let mut rest = input;
    while let Some(start) = rest.find('`') {
        rest = &rest[start..];
        let Some((column, after_column)) = parse_backtick_ident(rest) else {
            break;
        };
        let after_column = after_column.trim_start();
        let prefix_chars = after_column
            .strip_prefix('(')
            .and_then(|value| value.split(')').next())
            .and_then(|value| value.parse::<usize>().ok());
        parts.push(MysqlIndexPart {
            column,
            prefix_chars,
        });
        rest = after_column;
    }
    parts
}

fn parse_backtick_ident(input: &str) -> Option<(String, &str)> {
    let rest = input.strip_prefix('`')?;
    let end = rest.find('`')?;
    Some((rest[..end].to_string(), &rest[end + 1..]))
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct MysqlMigrationPlan {
    version: u16,
    name: String,
    checksum: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct AppliedMysqlMigration {
    version: i64,
    name: String,
    dialect: String,
    checksum: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct MysqlMigrationCheckReport {
    datasource: String,
    current_version: Option<i64>,
    target_version: Option<u16>,
    pending_versions: Vec<MysqlMigrationPlan>,
    applied_versions: Vec<AppliedMysqlMigration>,
    ignored_extra_versions: Vec<i64>,
}

fn mysql_migration_plan(migration: &Migration) -> MysqlMigrationPlan {
    MysqlMigrationPlan {
        version: migration.number,
        name: migration.name.clone(),
        checksum: mysql_declared_record_checksum(migration)
            .unwrap_or_else(|| sha256_hex(migration.sql.as_bytes())),
    }
}

fn mysql_declared_record_checksum(migration: &Migration) -> Option<String> {
    let compact = migration.sql.split_whitespace().collect::<Vec<_>>().join(" ");
    let lower = compact.to_ascii_lowercase();
    let marker = format!("values ({},", migration.number);
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
                } else {
                    break;
                }
            } else {
                value.push(inner);
            }
        }
        values.push(value);
    }
    values
}

async fn load_applied_mysql_migrations(
    db: &dyn DbPlugin,
) -> Result<Vec<AppliedMysqlMigration>> {
    if !mysql_schema_migrations_exists(db).await? {
        return Ok(Vec::new());
    }
    let rows = db
        .query(DbStatement::new(
            "SELECT version, name, dialect, checksum FROM bcs_schema_migrations ORDER BY version",
        ))
        .await
        .map_err(|err| anyhow!("query mysql bcs_schema_migrations: {}", err))?;
    rows.into_iter()
        .map(|row| {
            Ok(AppliedMysqlMigration {
                version: db_get_column(&row, "version")?,
                name: db_get_column(&row, "name")?,
                dialect: db_get_column(&row, "dialect")?,
                checksum: db_get_column(&row, "checksum")?,
            })
        })
        .collect::<bcs_db_api::DbResult<Vec<_>>>()
        .map_err(|err| anyhow!("read mysql bcs_schema_migrations row: {}", err))
}

async fn mysql_schema_migrations_exists(db: &dyn DbPlugin) -> Result<bool> {
    let rows = db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS table_count FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = ?",
            vec![DbValue::from("bcs_schema_migrations")],
        ))
        .await
        .map_err(|err| anyhow!("query mysql information_schema.tables: {}", err))?;
    let count = rows
        .first()
        .map(|row| db_get_column::<i64>(row, "table_count"))
        .transpose()
        .map_err(|err| anyhow!("read mysql information_schema.tables count: {}", err))?
        .unwrap_or(0);
    Ok(count > 0)
}

fn build_mysql_migration_report(
    datasource: String,
    plans: Vec<MysqlMigrationPlan>,
    applied_versions: Vec<AppliedMysqlMigration>,
    fail_on_extra_versions: bool,
) -> Result<MysqlMigrationCheckReport> {
    let plans_by_version = plans
        .iter()
        .map(|plan| (i64::from(plan.version), plan))
        .collect::<BTreeMap<_, _>>();
    let applied_by_version = applied_versions
        .iter()
        .map(|applied| (applied.version, applied))
        .collect::<BTreeMap<_, _>>();
    let mut extra_versions = Vec::new();

    for applied in &applied_versions {
        let Some(plan) = plans_by_version.get(&applied.version) else {
            extra_versions.push(applied.version);
            continue;
        };
        validate_mysql_migration_record(plan, applied)?;
    }

    if fail_on_extra_versions && !extra_versions.is_empty() {
        let versions = extra_versions
            .iter()
            .map(|version| format!("{version:03}"))
            .collect::<Vec<_>>()
            .join(", ");
        bail!("database has migration versions not present locally: {versions}");
    }

    let pending_versions = plans
        .iter()
        .filter(|plan| !applied_by_version.contains_key(&i64::from(plan.version)))
        .cloned()
        .collect::<Vec<_>>();
    Ok(MysqlMigrationCheckReport {
        datasource,
        current_version: applied_versions.iter().map(|applied| applied.version).max(),
        target_version: plans.iter().map(|plan| plan.version).max(),
        pending_versions,
        applied_versions,
        ignored_extra_versions: extra_versions,
    })
}

fn validate_mysql_migration_record(
    plan: &MysqlMigrationPlan,
    applied: &AppliedMysqlMigration,
) -> Result<()> {
    if applied.dialect != "mysql" {
        bail!(
            "mysql migration dialect mismatch for version {:03}: applied={}",
            applied.version,
            applied.dialect
        );
    }
    if applied.name != plan.name {
        bail!(
            "mysql migration name mismatch for version {:03}: applied={}, current={}",
            applied.version,
            applied.name,
            plan.name
        );
    }
    if applied.checksum != plan.checksum {
        bail!(
            "mysql migration checksum mismatch for version {:03} ({}): applied={}, current={}",
            applied.version,
            applied.name,
            applied.checksum,
            plan.checksum
        );
    }
    Ok(())
}

fn format_mysql_check_report(report: &MysqlMigrationCheckReport) -> String {
    let mut output = format!(
        "MySQL/OceanBase migration check ok\ndatasource={}\ncurrent_version={}\ntarget_version={}\napplied_versions={}\npending_versions={}",
        report.datasource,
        format_version(report.current_version),
        report
            .target_version
            .map(|version| version.to_string())
            .unwrap_or_else(|| "<none>".to_string()),
        report.applied_versions.len(),
        report.pending_versions.len()
    );
    if !report.ignored_extra_versions.is_empty() {
        output.push_str("\nignored_extra_versions=");
        output.push_str(
            &report
                .ignored_extra_versions
                .iter()
                .map(|version| format!("{version:03}"))
                .collect::<Vec<_>>()
                .join(","),
        );
    }
    for plan in &report.pending_versions {
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
            output.push_str("-- No ALTER statements are required before recording this version.\n");
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
mod tests {
    use super::*;
    use bcs_db_api::{DbPlugin, DbStatement, db_get_column};

    fn write_migration(
        dir: &Path,
        name: &str,
        sql: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        fs::write(dir.join(name), sql)?;
        Ok(())
    }

    fn migrate_args(dir: &Path) -> MigrateArgs {
        MigrateArgs {
            dialect: None,
            migrations_dir: Some(dir.to_path_buf()),
            sqlite_path: None,
            emit_sql: true,
            check_files: false,
            check_db: false,
            apply: false,
            yes: false,
            only: Vec::new(),
            from: None,
            to: None,
        }
    }

    #[test]
    fn emit_sql_selects_only_requested_migration() -> Result<(), Box<dyn std::error::Error>> {
        let temp_dir = tempfile::tempdir()?;
        write_migration(temp_dir.path(), "001_first.sql", "SELECT 1;")?;
        write_migration(temp_dir.path(), "016_templates.sql", "SELECT 16;")?;

        let mut args = migrate_args(temp_dir.path());
        args.only = vec![16];
        let sql = emit_migration_sql(&args)?;

        assert!(sql.contains("Migration 016"));
        assert!(sql.contains("SELECT 16;"));
        assert!(!sql.contains("SELECT 1;"));
        Ok(())
    }

    #[test]
    fn rejects_duplicate_numbers_in_selection() -> Result<(), Box<dyn std::error::Error>> {
        let temp_dir = tempfile::tempdir()?;
        write_migration(temp_dir.path(), "012_a.sql", "SELECT 1;")?;
        write_migration(temp_dir.path(), "012_b.sql", "SELECT 2;")?;

        let mut args = migrate_args(temp_dir.path());
        args.only = vec![12];
        let error = load_selected_migrations(&args)
        .err()
        .ok_or_else(|| io::Error::other("expected duplicate error"))?;

        assert!(error.to_string().contains("duplicate migration numbers"));
        Ok(())
    }

    #[test]
    fn emit_sql_allows_duplicate_numbers_for_inspection() -> Result<(), Box<dyn std::error::Error>> {
        let temp_dir = tempfile::tempdir()?;
        write_migration(temp_dir.path(), "012_a.sql", "SELECT 1;")?;
        write_migration(temp_dir.path(), "012_b.sql", "SELECT 2;")?;

        let args = migrate_args(temp_dir.path());
        let sql = emit_migration_sql(&args)?;

        assert!(sql.contains("012: a"));
        assert!(sql.contains("012: b"));
        assert!(sql.contains("SELECT 1;"));
        assert!(sql.contains("SELECT 2;"));
        Ok(())
    }

    #[test]
    fn migration_mode_requires_exactly_one_mode() {
        let mut args = migrate_args(Path::new("."));
        args.emit_sql = false;
        let error = MigrationMode::from_args(&args).expect_err("missing mode should fail");
        assert!(error.to_string().contains("choose exactly one"));

        args.emit_sql = true;
        args.check_files = true;
        let error = MigrationMode::from_args(&args).expect_err("multiple modes should fail");
        assert!(error.to_string().contains("choose exactly one"));
    }

    #[test]
    fn mysql_check_files_validates_baseline_record() -> Result<(), Box<dyn std::error::Error>> {
        let temp_dir = tempfile::tempdir()?;
        write_migration(
            temp_dir.path(),
            "001_init_schema.sql",
            "CREATE TABLE IF NOT EXISTS `bcs_schema_migrations` (
                `version` int(11) NOT NULL,
                PRIMARY KEY (`version`)
            );
            INSERT IGNORE INTO `bcs_schema_migrations` (`version`, `name`, `dialect`, `checksum`)
            VALUES (1, 'init_schema', 'mysql', 'abc');",
        )?;

        let mut args = migrate_args(temp_dir.path());
        args.emit_sql = false;
        args.check_files = true;

        let summary = check_mysql_migration_files(&args)?;

        assert!(summary.contains("MySQL/OceanBase migration files check ok"));
        assert!(summary.contains("versions=001"));
        Ok(())
    }

    #[test]
    fn mysql_check_files_rejects_overlong_utf8mb4_index() -> Result<(), Box<dyn std::error::Error>> {
        let temp_dir = tempfile::tempdir()?;
        write_migration(
            temp_dir.path(),
            "001_init_schema.sql",
            "CREATE TABLE IF NOT EXISTS `bcs_schema_migrations` (
                `version` int(11) NOT NULL,
                PRIMARY KEY (`version`)
            ) DEFAULT CHARSET = utf8mb4;
            INSERT IGNORE INTO `bcs_schema_migrations` (`version`, `name`, `dialect`, `checksum`)
            VALUES (1, 'init_schema', 'mysql', 'abc');
            CREATE TABLE IF NOT EXISTS `too_wide` (
                `name` varchar(1024) NOT NULL,
                KEY `idx_name` (`name`)
            ) DEFAULT CHARSET = utf8mb4;",
        )?;

        let mut args = migrate_args(temp_dir.path());
        args.emit_sql = false;
        args.check_files = true;
        let error = check_mysql_migration_files(&args)
            .expect_err("overlong utf8mb4 index should fail static validation");

        assert!(error.to_string().contains("too_wide.idx_name"));
        Ok(())
    }

    #[test]
    fn mysql_migration_plan_uses_declared_record_checksum() -> Result<(), Box<dyn std::error::Error>> {
        let temp_dir = tempfile::tempdir()?;
        write_migration(
            temp_dir.path(),
            "001_init_schema.sql",
            "CREATE TABLE IF NOT EXISTS `bcs_schema_migrations` (
                `version` int(11) NOT NULL,
                PRIMARY KEY (`version`)
            );
            INSERT IGNORE INTO `bcs_schema_migrations` (`version`, `name`, `dialect`, `checksum`)
            VALUES (1, 'init_schema', 'mysql', 'declared-checksum');",
        )?;

        let args = migrate_args(temp_dir.path());
        let migrations = load_selected_migrations(&args)?;
        let plan = mysql_migration_plan(&migrations[0]);

        assert_eq!(plan.checksum, "declared-checksum");
        Ok(())
    }

    #[test]
    fn mysql_check_report_lists_pending_when_no_rows() -> Result<(), Box<dyn std::error::Error>> {
        let plans = vec![MysqlMigrationPlan {
            version: 1,
            name: "init_schema".to_string(),
            checksum: "abc".to_string(),
        }];

        let report = build_mysql_migration_report("bcs".to_string(), plans, Vec::new(), true)?;
        let summary = format_mysql_check_report(&report);

        assert!(summary.contains("current_version=<none>"));
        assert!(summary.contains("target_version=1"));
        assert!(summary.contains("pending_versions=1"));
        assert!(summary.contains("- 001 init_schema checksum=abc"));
        Ok(())
    }

    #[test]
    fn mysql_check_report_rejects_checksum_mismatch() {
        let plans = vec![MysqlMigrationPlan {
            version: 1,
            name: "init_schema".to_string(),
            checksum: "abc".to_string(),
        }];
        let applied = vec![AppliedMysqlMigration {
            version: 1,
            name: "init_schema".to_string(),
            dialect: "mysql".to_string(),
            checksum: "bad".to_string(),
        }];

        let error = build_mysql_migration_report("bcs".to_string(), plans, applied, true)
            .expect_err("checksum mismatch should fail");

        assert!(error.to_string().contains("checksum mismatch"));
    }

    #[test]
    fn mysql_apply_yes_confirmation_accepts_only_explicit_yes() {
        assert!(is_yes_confirmation("y"));
        assert!(is_yes_confirmation("Y\n"));
        assert!(is_yes_confirmation("yes"));
        assert!(is_yes_confirmation("YES"));
        assert!(!is_yes_confirmation(""));
        assert!(!is_yes_confirmation("n"));
        assert!(!is_yes_confirmation("sure"));
    }

    #[test]
    fn mysql_sql_splitter_ignores_semicolons_inside_literals_and_comments() {
        let statements = split_sql_statements(
            "-- comment ;\nCREATE TABLE `a;b` (`c` varchar(10) DEFAULT ';');\n\
             INSERT INTO t VALUES ('x; y'); /* block ; */\n\
             # comment ;\n",
        );

        assert_eq!(statements.len(), 2);
        assert!(statements[0].contains("CREATE TABLE"));
        assert!(statements[0].contains("DEFAULT ';'"));
        assert!(statements[1].contains("INSERT INTO t"));
        assert!(statements[1].contains("'x; y'"));
    }

    #[test]
    fn mysql_apply_report_lists_applied_versions() -> Result<(), Box<dyn std::error::Error>> {
        let plan = MysqlMigrationPlan {
            version: 1,
            name: "init_schema".to_string(),
            checksum: "abc".to_string(),
        };
        let report = build_mysql_migration_report(
            "bcs".to_string(),
            vec![plan.clone()],
            vec![AppliedMysqlMigration {
                version: 1,
                name: "init_schema".to_string(),
                dialect: "mysql".to_string(),
                checksum: "abc".to_string(),
            }],
            true,
        )?;

        let summary = format_mysql_apply_report(&report, &[plan]);

        assert!(summary.contains("MySQL/OceanBase migrations applied"));
        assert!(summary.contains("current_version=1"));
        assert!(summary.contains("applied_versions=1"));
        assert!(summary.contains("pending_versions=0"));
        assert!(summary.contains("- 001 init_schema checksum=abc"));
        Ok(())
    }

    #[tokio::test]
    async fn sqlite_check_missing_file_does_not_create_file() -> Result<(), Box<dyn std::error::Error>> {
        let temp_dir = tempfile::tempdir()?;
        let sqlite_path = temp_dir.path().join("missing.db");

        let summary = check_sqlite_migration_state(&sqlite_path).await?;

        assert!(summary.contains("pending_versions=1"));
        assert!(!sqlite_path.exists());
        Ok(())
    }

    #[test]
    fn sqlite_check_files_reports_code_defined_migrations() {
        let summary = check_sqlite_migration_definitions();

        assert!(summary.contains("SQLite migration definitions check ok"));
        assert!(summary.contains("target_version=1"));
    }

    #[tokio::test]
    async fn sqlite_apply_creates_baseline_record() -> Result<(), Box<dyn std::error::Error>> {
        let temp_dir = tempfile::tempdir()?;
        let sqlite_path = temp_dir.path().join("bcs.db");
        let mut args = migrate_args(Path::new("."));
        args.dialect = Some(MigrationDialect::Sqlite);
        args.sqlite_path = Some(sqlite_path.clone());
        args.emit_sql = false;
        args.apply = true;
        let global = MigrateGlobalArgs {
            config_dir: None,
            config_file: None,
        };

        run_migrate(&args, &global).await?;

        let db = LocalSqliteDbPlugin::new_file(&sqlite_path)?;
        let rows = db
            .query(DbStatement::new(
                "SELECT version, name, dialect FROM bcs_schema_migrations",
            ))
            .await?;
        assert_eq!(rows.len(), 1);
        assert_eq!(db_get_column::<i64>(&rows[0], "version")?, 1);
        assert_eq!(db_get_column::<String>(&rows[0], "name")?, "init_schema");
        assert_eq!(db_get_column::<String>(&rows[0], "dialect")?, "sqlite");
        Ok(())
    }
}
