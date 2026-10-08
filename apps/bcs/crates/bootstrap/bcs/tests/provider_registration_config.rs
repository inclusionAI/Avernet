use bcs::BcsConfig;

#[test]
fn existing_config_defaults_to_no_self_service_providers() {
    let mut old_config = serde_json::to_value(BcsConfig::default()).unwrap();
    old_config["openapi_v1"]
        .as_object_mut()
        .unwrap()
        .remove("registration_self_service_provider_ids");
    let config: BcsConfig = serde_json::from_value(old_config).unwrap();
    assert!(
        config
            .openapi_v1
            .registration_self_service_provider_ids
            .is_empty()
    );
}

#[test]
fn explicit_self_service_allowlist_roundtrips() {
    let mut value = serde_json::to_value(BcsConfig::default()).unwrap();
    value["openapi_v1"]["registration_self_service_provider_ids"] =
        serde_json::json!(["poolab-provider-id"]);
    let config: BcsConfig = serde_json::from_value(value).unwrap();
    assert_eq!(
        config.openapi_v1.registration_self_service_provider_ids,
        ["poolab-provider-id"]
    );
    let encoded = serde_json::to_value(&config).unwrap();
    assert_eq!(
        encoded["openapi_v1"]["registration_self_service_provider_ids"][0],
        "poolab-provider-id"
    );
}
