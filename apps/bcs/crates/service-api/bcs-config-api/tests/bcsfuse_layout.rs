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

#[test]
fn catalog_api_version_defaults_to_v1_and_rejects_unknown_versions() {
    use bcs_config_api::BcsFuseApiVersion;
    let legacy: BcsFuseConfig = serde_json::from_str(r#"{"enabled":true}"#).unwrap();
    assert_eq!(legacy.api_version, BcsFuseApiVersion::V1);
    let cards: BcsFuseConfig = serde_json::from_str(r#"{"api_version":"v2"}"#).unwrap();
    assert_eq!(cards.api_version, BcsFuseApiVersion::V2);
    assert!(serde_json::from_str::<BcsFuseConfig>(r#"{"api_version":"v3"}"#).is_err());
}
