use bcs_config::{MysqlDbLoader, RedisCacheLoader};
use bcs_config_api::{StatementProtocol, RedisAuthMode};

#[allow(unsafe_code)]
fn set_env(key: &str, val: &str) {
    // SAFETY: tests in this file restore the process-global env they mutate.
    unsafe { std::env::set_var(key, val) }
}

#[allow(unsafe_code)]
fn unset_env(key: &str) {
    // SAFETY: tests in this file restore the process-global env they mutate.
    unsafe { std::env::remove_var(key) }
}

#[test]
fn redis_loader_reads_acl_username_from_env() {
    let backup = (
        std::env::var("BCS_REDIS_AUTH_MODE").ok(),
        std::env::var("BCS_REDIS_USERNAME").ok(),
        std::env::var("BCS_REDIS_PASSWORD").ok(),
    );

    set_env("BCS_REDIS_AUTH_MODE", "redis");
    set_env("BCS_REDIS_USERNAME", "bcs");
    set_env("BCS_REDIS_PASSWORD", "redis-pass");

    let cfg = RedisCacheLoader::config_from_env().expect("load env");
    let credentials = cfg.auth_credentials().unwrap().unwrap();
    assert_eq!(cfg.auth_mode, RedisAuthMode::Redis);
    assert_eq!(credentials.username.as_deref(), Some("bcs"));
    assert_eq!(credentials.password, "redis-pass");

    unset_env("BCS_REDIS_AUTH_MODE");
    unset_env("BCS_REDIS_USERNAME");
    unset_env("BCS_REDIS_PASSWORD");
    if let Some(value) = backup.0 {
        set_env("BCS_REDIS_AUTH_MODE", &value);
    }
    if let Some(value) = backup.1 {
        set_env("BCS_REDIS_USERNAME", &value);
    }
    if let Some(value) = backup.2 {
        set_env("BCS_REDIS_PASSWORD", &value);
    }
}

#[tokio::test]
async fn mysql_loader_reads_yaml_file() {
    let dir = tempfile::tempdir().expect("tmpdir");
    let path = dir.path().join("mysql.yaml");
    std::fs::write(
        &path,
        r#"
database: bcs
connection:
  type: direct
  user: bcs_user
"#,
    )
    .expect("write");

    let cfg = MysqlDbLoader::from_yaml(&path).await.expect("load");
    assert_eq!(cfg.database, "bcs");
    assert_eq!(cfg.connection.user.as_deref(), Some("bcs_user"));
}

#[test]
fn mysql_loader_reads_statement_protocol_from_env() {
    let backup = (
        std::env::var("BCS_DB_NAME").ok(),
        std::env::var("BCS_DB_USER").ok(),
        std::env::var("BCS_DB_STATEMENT_PROTOCOL").ok(),
    );

    set_env("BCS_DB_NAME", "bcs");
    set_env("BCS_DB_USER", "bcs_user");
    set_env("BCS_DB_STATEMENT_PROTOCOL", "prepared");

    let cfg = MysqlDbLoader::from_env().expect("load env");
    assert_eq!(
        cfg.statement_protocol,
        StatementProtocol::Prepared
    );

    unset_env("BCS_DB_NAME");
    unset_env("BCS_DB_USER");
    unset_env("BCS_DB_STATEMENT_PROTOCOL");
    if let Some(value) = backup.0 {
        set_env("BCS_DB_NAME", &value);
    }
    if let Some(value) = backup.1 {
        set_env("BCS_DB_USER", &value);
    }
    if let Some(value) = backup.2 {
        set_env("BCS_DB_STATEMENT_PROTOCOL", &value);
    }
}
