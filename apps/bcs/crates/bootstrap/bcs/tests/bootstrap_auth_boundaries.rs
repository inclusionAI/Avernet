#[test]
fn config_and_migration_exports_remain_stable() {
    let config = bcs::BcsConfig::default();
    assert_eq!(config.gateway_principal.audience, "bcs");
    assert_eq!(bcs::migrations::sqlite_target_version(), 43);
}
