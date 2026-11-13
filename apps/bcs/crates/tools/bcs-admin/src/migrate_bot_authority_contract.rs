use super::*;

const ORIGINAL_CHECKSUM: &str =
    "d8f1487e6d0e2d9e28f12e354637dff6a36d7b05e23f8a6e2641162d8c4227ec";

#[test]
fn bot_authority_generated_columns_use_oceanbase_safe_expressions() -> Result<()> {
    let migrations = load_selected_migrations(&chain_args())?;
    let migration = migrations.iter().find(|migration| migration.number == 36).unwrap();
    let sql = migration.sql.split_whitespace().collect::<Vec<_>>().join(" ");
    for definition in [
        "authority_owner_log VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin \
         GENERATED ALWAYS AS (CASE WHEN grant_kind = 'owner' AND status = 'approved' \
         THEN to_id ELSE NULL END) VIRTUAL COMMENT 'authority 唯一约束的生成列'",
        "`pending_log` VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin \
         GENERATED ALWAYS AS (CASE WHEN status = 'pending' \
         THEN bot_id ELSE NULL END) VIRTUAL COMMENT 'pending 唯一约束的生成列'",
    ] {
        assert!(sql.contains(definition), "missing OceanBase-safe definition: {definition}");
    }
    assert!(!sql.contains("CONVERT("));
    assert!(sql.contains("ADD UNIQUE KEY uk_edge_approved_owner (env, authority_owner_log)"));
    assert!(sql.contains("UNIQUE KEY `uk_transfer_pending` (`env`, `pending_log`)"));
    Ok(())
}

#[test]
fn bot_authority_correction_preserves_only_exact_archived_history() -> Result<()> {
    let migrations = load_selected_migrations(&chain_args())?;
    let plan = mysql_migration_plan(migrations.iter().find(|migration| migration.number == 36).unwrap());
    assert_ne!(plan.checksum, ORIGINAL_CHECKSUM, "036 must use the corrected generated columns");
    let original = fs::read(bcs_root().join("migrations/legacy/mysql/036_bot_authority.sql"))?;
    assert_eq!(sha256_hex(&original), ORIGINAL_CHECKSUM);
    let record = AppliedMysqlMigration {
        version: 36, name: "bot_authority".into(), dialect: "mysql".into(),
        checksum: ORIGINAL_CHECKSUM.into(),
    };
    let report = build_mysql_migration_report(
        "test".into(), vec![plan.clone()], vec![record.clone()], true,
    )?;
    assert!(report.pending_versions.is_empty());
    assert_eq!(report.applied_versions, vec![record.clone()]);
    for field in ["name", "checksum", "dialect", "version"] {
        let mut wrong = record.clone();
        match field {
            "name" => wrong.name.push_str("-wrong"),
            "checksum" => wrong.checksum = "0".repeat(64),
            "dialect" => wrong.dialect = "sqlite".into(),
            _ => wrong.version = 35,
        }
        assert!(build_mysql_migration_report(
            "test".into(), vec![plan.clone()], vec![wrong], true,
        ).is_err(), "{field}");
    }
    let mut changed = plan;
    changed.checksum = "f".repeat(64);
    assert!(validate_mysql_migration_record(&changed, &record).is_err());
    Ok(())
}
