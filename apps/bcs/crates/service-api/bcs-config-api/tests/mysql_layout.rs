use bcs_config_api::mysql::{DataSourceConfig, MysqlDbConfig, StatementProtocol};

#[test]
fn datasource_config_builder_round_trip() {
    let cfg = DataSourceConfig::new("testdb", "testuser")
        .with_password("testpass")
        .with_host("10.0.0.1")
        .with_port(3306)
        .with_statement_protocol(StatementProtocol::Prepared);
    assert_eq!(cfg.name, "testdb");
    assert_eq!(cfg.database, "testdb");
    assert_eq!(cfg.user, "testuser");
    assert_eq!(cfg.password, "testpass");
    assert_eq!(cfg.host, "10.0.0.1");
    assert_eq!(cfg.port, 3306);
    assert_eq!(cfg.statement_protocol, StatementProtocol::Prepared);
}

#[test]
fn datasource_statement_protocol_defaults_to_text() {
    let cfg = DataSourceConfig::new("testdb", "testuser");
    assert_eq!(cfg.statement_protocol, StatementProtocol::Text);
}

#[test]
fn datasource_statement_protocol_deserializes_from_snake_case() {
    let json = r#"{
        "name": "testdb",
        "database": "testdb",
        "user": "testuser",
        "statement_protocol": "prepared"
    }"#;
    let cfg: DataSourceConfig = serde_json::from_str(json).expect("deserialize datasource");
    assert_eq!(cfg.statement_protocol, StatementProtocol::Prepared);
}

#[test]
fn mysql_config_default_keeps_existing_behavior() {
    let cfg = MysqlDbConfig::new();
    assert_eq!(cfg.database, "bcs");
}

#[test]
fn mysql_config_serde_roundtrip() {
    let original = MysqlDbConfig::new().with_database("bcs");
    let json = serde_json::to_string(&original).expect("serialize");
    let back: MysqlDbConfig = serde_json::from_str(&json).expect("deserialize");
    assert_eq!(back.database, "bcs");
}
