use bcs_config_api::BcsFuseConfig;

#[test]
fn bcsfuse_config_default_is_disabled_with_local_url() {
    let cfg = BcsFuseConfig::default();
    assert!(!cfg.enabled);
    assert!(cfg.url.starts_with("http://"));
    assert!(cfg.fusion_timeout_ms > 0);
}

#[test]
fn bcsfuse_config_serde_roundtrip() {
    let original = BcsFuseConfig::default();
    let json = serde_json::to_string(&original).expect("serialize");
    let back: BcsFuseConfig = serde_json::from_str(&json).expect("deserialize");
    assert_eq!(back.url, original.url);
    assert_eq!(back.enabled, original.enabled);
}

#[test]
fn bcsfuse_auth_uses_a_serializable_reference_and_redacts_resolved_material() {
    let mut cfg: BcsFuseConfig = serde_json::from_str(
        r#"{"authorization_ref":"bcsfuse-auth"}"#,
    )
    .expect("deserialize auth token secret reference");
    cfg.set_resolved_authorization("test-token".to_string());

    assert_eq!(cfg.auth_token(), Some("test-token"));
    assert!(!format!("{cfg:?}").contains("test-token"));
    let serialized = serde_json::to_string(&cfg).expect("serialize");
    assert!(serialized.contains("bcsfuse-auth"));
    assert!(!serialized.contains("test-token"));
}
