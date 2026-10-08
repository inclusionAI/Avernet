use super::*;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct MysqlMigrationPlan {
    pub(super) version: u16,
    pub(super) name: String,
    pub(super) checksum: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct AppliedMysqlMigration {
    pub(super) version: i64,
    pub(super) name: String,
    pub(super) dialect: String,
    pub(super) checksum: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct MysqlMigrationCheckReport {
    pub(super) datasource: String,
    pub(super) current_version: Option<i64>,
    pub(super) target_version: Option<u16>,
    pub(super) pending_versions: Vec<MysqlMigrationPlan>,
    pub(super) applied_versions: Vec<AppliedMysqlMigration>,
    pub(super) ignored_extra_versions: Vec<i64>,
}

pub(super) fn mysql_migration_plan(migration: &Migration) -> MysqlMigrationPlan {
    MysqlMigrationPlan {
        version: migration.number,
        name: migration.name.clone(),
        checksum: mysql_declared_record_checksum(migration)
            .unwrap_or_else(|| sha256_hex(migration.sql.as_bytes())),
    }
}

pub(super) fn mysql_declared_record_checksum(migration: &Migration) -> Option<String> {
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

pub(super) fn parse_single_quoted_values(input: &str) -> Vec<String> {
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

pub(super) async fn load_applied_mysql_migrations(
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

pub(super) async fn mysql_schema_migrations_exists(db: &dyn DbPlugin) -> Result<bool> {
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

pub(super) fn build_mysql_migration_report(
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

pub(super) fn validate_mysql_migration_record(
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
    if plan.version == 16 && applied.version == 16
        && plan.name == "session_callback_lease_and_chat_runs"
    {
        let legacy_sql = match applied.name.as_str() {
            "session_callback_lease" => Some(include_str!("../../../../../migrations/legacy/mysql/016_session_callback_lease.sql")),
            "chat_runs" => Some(include_str!("../../../../../migrations/legacy/mysql/016_chat_runs.sql")),
            _ => None,
        };
        if let Some(sql) = legacy_sql {
            let expected = sha256_hex(sql.as_bytes());
            if applied.checksum != expected {
                bail!(
                    "mysql migration checksum mismatch for legacy version 016 ({}): applied={}, archived={}",
                    applied.name, applied.checksum, expected
                );
            }
            bail!(
                "legacy mysql migration 016 ({}) requires explicit reconciliation before the merged migration can be accepted; verify both callback leases and chat runs; see migrations/reconciliation/016-session-callback-and-chat-runs.md. No migration record was changed",
                applied.name
            );
        }
    }
    if applied.name != plan.name {
        bail!(
            "mysql migration name mismatch for version {:03}: applied={}, current={}",
            applied.version,
            applied.name,
            plan.name
        );
    }
    if applied.checksum != plan.checksum
        && !is_legacy_human_input_index_record(plan, applied)
        && !is_legacy_mysql_syntax_record(plan, applied)
    {
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

// These exact baseline/index revisions retain their historical records.
// The current checksums are pinned too, so future edits are not accepted
// automatically. Columns removed from 001 belong to 011/020. Existing tables
// and indexes are left unchanged; index-size repairs are deployment-controlled.
pub(super) fn is_legacy_human_input_index_record(
    plan: &MysqlMigrationPlan,
    applied: &AppliedMysqlMigration,
) -> bool {
    if applied.version != i64::from(plan.version) || applied.dialect != "mysql"
        || applied.name != plan.name
    {
        return false;
    }
    matches!(
        (plan.version, plan.name.as_str(), plan.checksum.as_str(), applied.checksum.as_str()),
        (1, "init_schema",
            "a7f351ed88f95eb233e535f5fda9226a161fea5fc2af84d97ff2e2593a57a1d3",
            "b3de64c97b982a735230f6c55e966e4404eb509d70a0b7fd8f11dfa43e3452a7")
        | (8, "human_input_im_requests",
            "efc4dc1b457f7ce1f20c78394d1af4b1ca2af7bee963c8cff4f4587660728c96",
            "0e10c711afc436cf59d2393e3d5c88b9c24e73be72f4e984044840e6f798ebd5")
    )
}

// Explicit syntax-only corrections authorized for the archived historical SQL.
// Pin BOTH checksums: neither unknown history nor future file edits are accepted.
// (version, name, current checksum, archived checksum)
pub(super) const MYSQL_SYNTAX_REVISIONS: &[(u16, &str, &str, &str)] = &[
    (2, "add_owner_bot_id",
        "4190aed200a1cf880321bec17ca50ca2835822a90e03df234d1f98ae2d80d588",
        "b0ee5d777bf79f7c04e676ea61cac4553e0c0be1db3fb3bc2849978104f2ffec"),
    (7, "add_human_input_runtime",
        "2a1685ae578fdfe06401321107dab9007266fb367495cae03fbbc662cdfac69c",
        "4cea1e1ff6db55afa1ac5c7b10823c57f4876c20b033703f98940eb25e4ee19c"),
    (11, "group_participant_tags",
        "41e0f84544b1fdc53f840af30e43dc108dc4408c91ee0ceca1e9011e591ae0b1",
        "3f8148d618395fd2b45312c08287422d4e3b8fdc17587e29f5c80a55a3962053"),
    (13, "add_bot_task_modes",
        "956f7cb936e293feb98281d86d78c978432c6c2ed0a75a4c766dc381dde353b3",
        "fddb6f03977b313c6f5d1bed1ad4a735fb1c439f1b35c0a4436372a90dc1ad3e"),
    (15, "add_bot_internal_attributes",
        "b65ac46e25683050fd34c791f2223f7bbd3de59e359a70907579eabec1e48ec8",
        "247e163a9fd7c4b4c0e023757f72a978857722f9ea6eebee5dc607dbfae44060"),
    (17, "state_machine_rerun_lineage",
        "8128dedd4f597c00e8b290cc2046e8417ce40e41a7e75b993080370b3d1b393c",
        "942f9d52fd2438bfe65badc561c7d0d840d2ce46d164268cb5d60199c258dfd1"),
    (18, "one_shot_opening_message_override",
        "1e0a66ad71d6dd5cf98f0b180401dfdeb6e6e2e5984fc690bd67904922ab533d",
        "57b4534262cd9a30f42e5eeda7cc5953a3c56fc731d3d75c0a540c4d4b98862a"),
    (20, "human_participant_message_visibility",
        "24df88c5a34c7f1c547820b8b4483f1dabedd1373a158dc1bbf2e262ee0c60d1",
        "9423d10481b072df738befc9e8987a70808beba3f199921ee60c8b7463448e8c"),
];

pub(super) fn is_legacy_mysql_syntax_record(plan: &MysqlMigrationPlan, applied: &AppliedMysqlMigration) -> bool {
    applied.version == i64::from(plan.version)
        && applied.dialect == "mysql"
        && applied.name == plan.name
        && MYSQL_SYNTAX_REVISIONS.iter().any(|&(version, name, current, archived)| {
            plan.version == version && plan.name == name
                && plan.checksum == current && applied.checksum == archived
        })
}

pub(super) fn format_mysql_check_report(report: &MysqlMigrationCheckReport) -> String {
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
