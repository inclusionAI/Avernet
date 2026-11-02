//! `[api.auth]` schema tests: fixture-locked TOML shapes (the verbatim
//! fixtures from the plan), plugin-table retention for the registry phase,
//! name rules, and the pure minutes -> seconds conversion.

use bcs_config_api::api_auth::{
    ApiAuthConfig, ApiConfig, is_valid_api_source_name, validate_idle_timeout_minutes,
};

#[test]
fn explicit_auth_requires_chain_but_absence_preserves_compatibility() {
    // 此 fixture 只锁定当前 ApiConfig 的 auth 缺省语义；若新增必填字段，
    // 在 fixture 中显式提供它们，保留“省略 auth”这一断言，不给新字段补默认值。
    let compatible: ApiConfig = toml::from_str("").unwrap();
    assert!(compatible.auth.is_none());
    assert!(toml::from_str::<ApiConfig>("[auth]").is_err());
}

#[test]
fn retains_plugin_tables_for_registry_validation() {
    let api: ApiConfig = toml::from_str(r#"
[auth]
chain = ["gateway", "cookie"]
[auth.cookie]
client_id = "test-client"
"#).unwrap();
    assert_eq!(api.auth.unwrap().chain, vec!["gateway", "cookie"]);
}

#[test]
fn plugin_tables_and_unknown_scalars_are_retained_verbatim() {
    // Everything that is not a reserved public field must reach the registry
    // phase unmodified: plugin tables, nested arrays/objects, unknown public
    // scalars (the registry classifies those as unregistered sources), and
    // integers above the f64-exact range must stay exact integers.
    let api: ApiConfig = toml::from_str(r#"
[auth]
chain = ["gateway"]
mystery_scalar = true

[auth.cookie]
client_id = "test-client"
big = 9007199254740993

[auth.cookie.nested]
tags = ["a", "b"]
"#).unwrap();
    let auth = api.auth.unwrap();
    assert_eq!(auth.plugins["cookie"]["client_id"], "test-client");
    assert_eq!(
        auth.plugins["cookie"]["big"].as_i64(),
        Some(9007199254740993),
        "big integer must not round-trip through f64"
    );
    assert_eq!(auth.plugins["cookie"]["nested"]["tags"][0], "a");
    assert_eq!(auth.plugins["mystery_scalar"], true);
}

#[test]
fn reserved_public_field_names_cannot_carry_plugin_tables() {
    // Reserved public field names cannot be reused as plugin names: serde
    // routes those keys into the typed fields, so a table under a reserved
    // name is a type error at parse time and never reaches the registry.
    let err = toml::from_str::<ApiConfig>(r#"
[auth]
chain = ["gateway"]

[auth.success_redirect_path]
value = "/"
"#);
    assert!(err.is_err());
}

#[test]
fn oauth_and_origin_public_fields_default_to_absent() {
    let cfg: ApiAuthConfig = toml::from_str(r#"chain = ["gateway"]"#).unwrap();
    assert_eq!(cfg.public_base_url, None);
    assert_eq!(cfg.session_signing_key_secret, None);
    assert_eq!(cfg.success_redirect_path, None);
    assert_eq!(cfg.session_idle_timeout_minutes, None);
    assert!(cfg.trusted_browser_origins.is_empty());
}

#[test]
fn invalid_timeout_numbers_fail_at_parse_time() {
    // Negative numbers, floats, and string digits never reach the registry
    // conversion: the typed field rejects them while parsing the section.
    for body in [
        r#"chain = ["gateway"]
session_idle_timeout_minutes = -5"#,
        r#"chain = ["gateway"]
session_idle_timeout_minutes = 1.5"#,
        r#"chain = ["gateway"]
session_idle_timeout_minutes = "30""#,
    ] {
        assert!(
            toml::from_str::<ApiAuthConfig>(body).is_err(),
            "expected parse rejection for: {body}"
        );
    }
    // The i64-exact boundary values round-trip as integers.
    let cfg: ApiAuthConfig = toml::from_str(
        "chain = [\"gateway\"]\nsession_idle_timeout_minutes = 9007199254740993",
    )
    .unwrap();
    assert_eq!(cfg.session_idle_timeout_minutes, Some(9007199254740993));
}

#[test]
fn chain_shape_rejects_empty_duplicate_and_illegal_names() {
    for (body, label) in [
        (r#"chain = []"#, "empty"),
        (r#"chain = ["gateway", "gateway"]"#, "duplicate"),
        (r#"chain = ["Gateway"]"#, "uppercase"),
        (r#"chain = ["1gateway"]"#, "leading digit"),
        (r#"chain = ["gate-way"]"#, "hyphen"),
        (r#"chain = ["gateway", ""]"#, "empty entry"),
    ] {
        let cfg: ApiAuthConfig = toml::from_str(body).unwrap();
        assert!(
            cfg.validate_chain_shape().is_err(),
            "expected chain-shape rejection ({label}): {body}"
        );
    }
    let ok: ApiAuthConfig = toml::from_str(r#"chain = ["gateway", "cookie_1"]"#).unwrap();
    assert_eq!(ok.validate_chain_shape(), Ok(()));
}

#[test]
fn source_name_rule_is_a_lowercase_identifier() {
    assert!(is_valid_api_source_name("gateway"));
    assert!(is_valid_api_source_name("cookie_sso_1"));
    assert!(!is_valid_api_source_name(""));
    assert!(!is_valid_api_source_name("Gateway"));
    assert!(!is_valid_api_source_name("1gateway"));
    assert!(!is_valid_api_source_name("gate-way"));
    assert!(!is_valid_api_source_name("gate way"));
}

#[test]
fn timeout_converts_minutes_to_seconds_purely() {
    assert_eq!(validate_idle_timeout_minutes(30).unwrap(), 1800);
    assert_eq!(validate_idle_timeout_minutes(1).unwrap(), 60);
    // The conversion stays exact above the f64-exact integer range.
    assert_eq!(validate_idle_timeout_minutes(9007199254740993).unwrap(), 9007199254740993 * 60);
    let zero = validate_idle_timeout_minutes(0).unwrap_err();
    assert!(zero.contains("must be positive"), "unexpected error: {zero}");
    let overflow = validate_idle_timeout_minutes(9223372036854775807).unwrap_err();
    assert!(
        overflow.contains("overflows seconds"),
        "unexpected error: {overflow}"
    );
}
