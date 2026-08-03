use bcs_config_api::BcsFuseConfig;

#[test]
fn bcsfuse_config_default_is_disabled_with_local_url() {
    let cfg = BcsFuseConfig::default();
    assert!(!cfg.enabled);
    assert!(cfg.url.starts_with("http://"));
    assert!(cfg.fusion_timeout_ms > 0);
    assert_eq!(cfg.sync_max_attempts, 3);
    assert_eq!(cfg.sync_retry_base_delay_ms, 1_000);
}

#[test]
fn bcsfuse_config_serde_roundtrip() {
    let original = BcsFuseConfig::default();
    let json = serde_json::to_string(&original).expect("serialize");
    let back: BcsFuseConfig = serde_json::from_str(&json).expect("deserialize");
    assert_eq!(back.url, original.url);
    assert_eq!(back.enabled, original.enabled);
    assert_eq!(back.sync_max_attempts, original.sync_max_attempts);
    assert_eq!(
        back.sync_retry_base_delay_ms,
        original.sync_retry_base_delay_ms
    );
}

#[test]
fn bcsfuse_config_without_retry_fields_uses_defaults() {
    let cfg: BcsFuseConfig =
        serde_json::from_str(r#"{"enabled":true}"#).expect("deserialize legacy config");
    assert_eq!(cfg.sync_max_attempts, 3);
    assert_eq!(cfg.sync_retry_base_delay_ms, 1_000);
}

#[test]
fn bcsfuse_config_ignores_unknown_fields() {
    let cfg: BcsFuseConfig =
        serde_json::from_str(r#"{"enabled":true,"sync_retry_base_delai_ms":1}"#)
            .expect("unknown bcsfuse fields must remain backward compatible");
    assert!(cfg.enabled);
    assert_eq!(cfg.sync_retry_base_delay_ms, 1_000);
}

#[test]
fn bcsfuse_config_rejects_zero_sync_attempts() {
    let cfg = BcsFuseConfig {
        sync_max_attempts: 0,
        ..BcsFuseConfig::default()
    };
    assert_eq!(
        cfg.validate().unwrap_err(),
        "bcsfuse.sync_max_attempts must be at least 1"
    );

    let cfg = BcsFuseConfig {
        sync_max_attempts: 11,
        sync_retry_base_delay_ms: 60_001,
        ..BcsFuseConfig::default()
    };
    assert!(cfg.validate().is_ok());
}
