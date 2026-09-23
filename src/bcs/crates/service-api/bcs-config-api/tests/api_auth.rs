//! Shape and field-validity tests for the `[api.auth]` configuration contract
//! (Task 6, spec §4 of v1-api-auth-plugin-chain-design.md).
//!
//! These tests live in the contract crate because they cover pure data
//! deserialization and field-shape validation — no bootstrap wiring knowledge
//! is required. Registry-driven cross-checks (every chain entry must reference
//! a registered source; OAuth requires `public_base_url`; cookie sources
//! require `trusted_browser_origins`; ...) live in the bootstrap test file
//! `crates/bootstrap/bcs/tests/api_auth_config.rs`. Here we verify only what
//! spec §4.2 calls the "field-shape" validations that don't need the registry:
//! chain name rules, reserved-name exclusivity, success_redirect_path,
//! session_idle_timeout_minutes, trusted_browser_origins and public_base_url.

use bcs_config_api::{ApiAuthConfig, ApiConfig, GatewayApiAuthConfig};

// ---------------------------------------------------------------------------
// Verbatim task-brief TOML shape (chain + plugin table both present)
// ---------------------------------------------------------------------------

#[test]
fn api_auth_chain_and_plugin_table_toml_shape_verbatim() {
    // The user-facing TOML shape from the task brief: chain lists source
    // names; [api.auth.<name>] tables carry per-plugin parameters. Raw
    // toml::Value side-step the typed deserializer so the structural shape
    // is locked independently of any future struct refactors.
    let value: toml::Value = toml::from_str(
        r#"[api.auth]
chain = ["gateway", "cookie"]

[api.auth.cookie]
client_id = "test-client"
"#,
    )
    .expect("parse api.auth example");

    let chain = value["api"]["auth"]["chain"]
        .as_array()
        .expect("chain is array");
    assert_eq!(chain.len(), 2);
    assert_eq!(chain[0].as_str(), Some("gateway"));
    assert_eq!(chain[1].as_str(), Some("cookie"));
    // The cookie table is reachable as [api.auth.cookie].
    assert_eq!(
        value["api"]["auth"]["cookie"]["client_id"].as_str(),
        Some("test-client")
    );
}

// ---------------------------------------------------------------------------
// Reserved field classification helper
// ---------------------------------------------------------------------------

#[test]
fn reserved_field_names_lists_every_common_primary_key() {
    let names = ApiAuthConfig::reserved_field_names();
    // The seven common reserved keys per spec §4.1.
    for expected in [
        "chain",
        "public_base_url",
        "session_signing_key_secret",
        "session_idle_timeout_minutes",
        "success_redirect_path",
        "trusted_browser_origins",
    ] {
        assert!(
            names.contains(&expected),
            "expected reserved field '{expected}' to be listed"
        );
    }
}

#[test]
fn is_reserved_field_name_recognizes_common_keys_and_rejects_plugin_names() {
    for reserved in [
        "chain",
        "public_base_url",
        "session_signing_key_secret",
        "session_idle_timeout_minutes",
        "success_redirect_path",
        "trusted_browser_origins",
    ] {
        assert!(
            ApiAuthConfig::is_reserved_field_name(reserved),
            "'{reserved}' must be a reserved common field name"
        );
    }
    // Plugin / built-in source names are NOT reserved common fields.
    for plugin in ["gateway", "alipay", "github", "google", "wechat", "cookie"] {
        assert!(
            !ApiAuthConfig::is_reserved_field_name(plugin),
            "'{plugin}' must not be reported as a reserved common field name"
        );
    }
}

// ---------------------------------------------------------------------------
// ApiConfig defaults (absent [api.auth] = compat)
// ---------------------------------------------------------------------------

#[test]
fn api_config_default_has_no_auth_section() {
    let cfg = ApiConfig::default();
    assert!(cfg.auth.is_none(), "default ApiConfig must have auth = None");
}

// ---------------------------------------------------------------------------
// ApiAuthConfig — chain name rules per spec §4.2
// ---------------------------------------------------------------------------

#[test]
fn empty_chain_array_is_rejected_by_validate() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = []
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "k"
"#,
    )
    .expect("parse empty chain");
    let err = cfg.validate().expect_err("empty chain rejected");
    assert!(
        err.contains("chain") && err.contains("empty"),
        "expected chain-empty error, got: {err}"
    );
}

#[test]
fn missing_chain_field_is_a_deserialize_error() {
    // An explicit `[api.auth]` table with no chain is the spec's
    // "显式空表不是兼容" case: it must fail — at the deserializer level
    // because `chain` is a required field on `ApiAuthConfig`.
    let err = toml::from_str::<ApiAuthConfig>("").expect_err("missing chain");
    assert!(
        err.to_string().contains("chain"),
        "expected missing-field 'chain' error, got: {err}"
    );
}

#[test]
fn duplicate_chain_entries_are_rejected() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway", "gateway"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "k"

[gateway]
signing_key_secret = "principal-signing-key"
"#,
    )
    .expect("parse duplicate chain");
    let err = cfg.validate().expect_err("duplicate chain rejected");
    assert!(
        err.contains("duplicate"),
        "expected duplicate-chain error, got: {err}"
    );
}

#[test]
fn chain_entry_starting_with_digit_is_rejected() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["9gateway"]

[9gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse bad-name chain");
    let err = cfg.validate().expect_err("name rule violation rejected");
    assert!(
        err.contains("chain") && err.to_string().contains("9gateway"),
        "expected name-rule error mentioning '9gateway', got: {err}"
    );
}

#[test]
fn chain_entry_with_uppercase_is_rejected() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["Gateway"]

[Gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse uppercase-name chain");
    let err = cfg.validate().expect_err("uppercase name rejected");
    assert!(
        err.to_string().contains("Gateway"),
        "expected name-rule error mentioning 'Gateway', got: {err}"
    );
}

#[test]
fn chain_entry_with_hyphen_is_rejected() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["my-cookie"]

[my-cookie]
client_id = "k"
"#,
    )
    .expect("parse hyphen-name chain");
    let err = cfg.validate().expect_err("hyphen name rejected");
    assert!(
        err.to_string().contains("my-cookie"),
        "expected name-rule error mentioning 'my-cookie', got: {err}"
    );
}

#[test]
fn reserved_field_name_used_as_chain_entry_is_rejected() {
    // A reserved common field name cannot be used as a source name, even
    // though it satisfies the lowercase-letter-first name rule.
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["chain"]
"#,
    )
    .expect("parse reserved-name-as-source chain");
    let err = cfg.validate().expect_err("reserved name rejected as source");
    assert!(
        err.contains("reserved"),
        "expected reserved-name-as-source error, got: {err}"
    );
}

#[test]
fn valid_lower_first_underscore_chain_names_are_accepted_by_name_rules() {
    // Valid names: lowercase letter first; rest = lowercase letters, digits,
    // underscores. ApiAuthConfig::validate() runs only shape checks (no
    // registry), so names not in the production inventory still pass at
    // this layer.
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway", "cookie_v2"]

[gateway]
signing_key_secret = "k"

[cookie_v2]
client_id = "k"
"#,
    )
    .expect("parse valid names");
    cfg.validate()
        .unwrap_or_else(|e| panic!("valid names should pass shape validation: {e}"));
}

// ---------------------------------------------------------------------------
// success_redirect_path
// ---------------------------------------------------------------------------

#[test]
fn success_redirect_path_defaults_to_root() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse default success_redirect_path");
    assert_eq!(cfg.success_redirect_path, "/");
}

#[test]
fn success_redirect_path_rejects_protocol_relative() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
success_redirect_path = "//evil.example.com/"

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg.validate().expect_err("reject //");
    assert!(
        err.contains("success_redirect_path"),
        "expected success_redirect_path error, got: {err}"
    );
}

#[test]
fn success_redirect_path_rejects_absolute_url() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
success_redirect_path = "https://evil.example.com/"

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    assert!(cfg.validate().is_err());
}

#[test]
fn success_redirect_path_rejects_no_leading_slash() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
success_redirect_path = "dashboard"

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    assert!(cfg.validate().is_err());
}

#[test]
fn success_redirect_path_accepts_single_slash_site_relative() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
success_redirect_path = "/dashboard?login=success"

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    cfg.validate()
        .unwrap_or_else(|e| panic!("site-relative redirect should pass shape validation: {e}"));
}

// ---------------------------------------------------------------------------
// session_idle_timeout_minutes
// ---------------------------------------------------------------------------

#[test]
fn session_idle_timeout_minutes_defaults_to_30() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    assert_eq!(cfg.session_idle_timeout_minutes, 30);
}

#[test]
fn session_idle_timeout_minutes_zero_is_rejected() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
session_idle_timeout_minutes = 0

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg.validate().expect_err("reject 0 timeout");
    assert!(err.contains("session_idle_timeout_minutes"));
}

#[test]
fn session_idle_timeout_minutes_huge_value_rejects_overflow_conversion() {
    // Pick a positive value that fits `u64` minutes but overflows when
    // converted to seconds (× 60): `u64::MAX / 60 + 1`.
    let huge: u64 = u64::MAX / 60 + 1;
    let src = format!(
        "chain = [\"gateway\"]\nsession_idle_timeout_minutes = {huge}\n\n[gateway]\nsigning_key_secret = \"k\"\n"
    );
    let cfg: ApiAuthConfig = toml::from_str(&src).expect("parse");
    let err = cfg.validate().expect_err("reject overflow timeout");
    assert!(err.contains("session_idle_timeout_minutes"));
}

// ---------------------------------------------------------------------------
// trusted_browser_origins
// ---------------------------------------------------------------------------

#[test]
fn trusted_browser_origins_rejects_wildcard_host() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
trusted_browser_origins = ["https://*.example.com"]

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg.validate().expect_err("reject wildcard");
    assert!(err.contains("trusted_browser_origins"));
}

#[test]
fn trusted_browser_origins_rejects_userinfo() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
trusted_browser_origins = ["https://user:pass@example.com"]

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg.validate().expect_err("reject userinfo");
    assert!(err.contains("trusted_browser_origins"));
}

#[test]
fn trusted_browser_origins_rejects_path() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
trusted_browser_origins = ["https://example.com/dashboard"]

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg.validate().expect_err("reject path");
    assert!(err.contains("trusted_browser_origins"));
}

#[test]
fn trusted_browser_origins_rejects_query_fragment() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
trusted_browser_origins = ["https://example.com/?foo=bar", "https://example.com/#frag"]

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg.validate().expect_err("reject query/fragment");
    assert!(err.contains("trusted_browser_origins"));
}

#[test]
fn trusted_browser_origins_rejects_non_http_scheme() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
trusted_browser_origins = ["ftp://example.com"]

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg.validate().expect_err("reject non-http(s)");
    assert!(err.contains("trusted_browser_origins"));
}

#[test]
fn trusted_browser_origins_accepts_explicit_port() {
    // Scheme + host + explicit port is the canonical exact-match origin.
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
trusted_browser_origins = ["https://workbench.example.com:8443"]

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    cfg.validate()
        .unwrap_or_else(|e| panic!("explicit-port origin should pass shape validation: {e}"));
}

// ---------------------------------------------------------------------------
// public_base_url
// ---------------------------------------------------------------------------

#[test]
fn public_base_url_rejects_relative_url() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
public_base_url = "/openapi/v1/auth"

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg.validate().expect_err("reject relative");
    assert!(err.contains("public_base_url"));
}

#[test]
fn public_base_url_rejects_userinfo() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
public_base_url = "https://user@bcs.example.com/openapi/v1/auth"

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg.validate().expect_err("reject userinfo");
    assert!(err.contains("public_base_url"));
}

#[test]
fn public_base_url_rejects_query_or_fragment() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
public_base_url = "https://bcs.example.com/openapi/v1/auth?tenant=x"

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg.validate().expect_err("reject query");
    assert!(err.contains("public_base_url"));
}

#[test]
fn public_base_url_rejects_non_http_scheme() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
public_base_url = "ftp://bcs.example.com/openapi/v1/auth"

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg.validate().expect_err("reject ftp");
    assert!(err.contains("public_base_url"));
}

// ---------------------------------------------------------------------------
// plugin_tables carried through flatten
// ---------------------------------------------------------------------------

#[test]
fn api_auth_config_captures_unknown_plugin_tables_in_plugin_tables_map() {
    // The cookie table from the brief's verbatim shape is captured (along
    // with any built-in table, like `gateway`). The contract layer does
    // not validate whether the name is registered — only the bootstrap
    // registry does that. Here we assert that the unknown-key route is
    // visible as an entry of `plugin_tables`.
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway", "cookie"]

[gateway]
signing_key_secret = "k"

[cookie]
client_id = "test-client"
"#,
    )
    .expect("parse");
    assert!(cfg.plugin_tables.contains_key("cookie"));
    assert!(cfg.plugin_tables.contains_key("gateway"));
    assert_eq!(
        cfg.plugin_tables["cookie"]["client_id"].as_str(),
        Some("test-client")
    );
}

// ---------------------------------------------------------------------------
// ApiConfig wrapper
// ---------------------------------------------------------------------------

#[test]
fn api_config_parses_auth_section_when_present() {
    let cfg: ApiConfig = toml::from_str(
        r#"[auth]
chain = ["gateway"]

[auth.gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let auth = cfg.auth.expect("auth is present");
    assert_eq!(auth.chain, vec!["gateway".to_string()]);
}

#[test]
fn api_config_treats_absent_auth_as_compat() {
    let cfg: ApiConfig = toml::from_str("").expect("empty ApiConfig");
    assert!(cfg.auth.is_none());
}

// ---------------------------------------------------------------------------
// GatewayApiAuthConfig (built-in typed plugin-table struct)
// ---------------------------------------------------------------------------

#[test]
fn gateway_config_defaults_match_spec() {
    let g = GatewayApiAuthConfig::default();
    assert_eq!(g.issuers, vec!["gateway".to_string(), "backend".to_string()]);
    assert_eq!(g.audience, "bcs");
    assert_eq!(g.key_id, "bare");
    assert!(g.signing_key_secret.is_none());
}

#[test]
fn gateway_config_validate_rejects_blank_signing_key_secret() {
    let mut g = GatewayApiAuthConfig::default();
    g.signing_key_secret = Some("  ".to_string());
    let err = g.validate().expect_err("reject blank secret");
    assert!(err.contains("signing_key_secret"));
}

#[test]
fn gateway_config_validate_rejects_empty_issuers() {
    let mut g = GatewayApiAuthConfig::default();
    g.issuers = vec![];
    let err = g.validate().expect_err("reject empty issuers");
    assert!(err.contains("issuers"));
}

#[test]
fn gateway_config_validate_rejects_duplicate_issuers() {
    let mut g = GatewayApiAuthConfig::default();
    g.issuers = vec!["gateway".to_string(), "gateway".to_string()];
    let err = g.validate().expect_err("reject duplicate issuers");
    assert!(err.contains("issuers"));
}

#[test]
fn gateway_config_validate_rejects_blank_issuer_entry() {
    let mut g = GatewayApiAuthConfig::default();
    g.issuers = vec!["gateway".to_string(), "  ".to_string()];
    let err = g.validate().expect_err("reject blank issuer");
    assert!(err.contains("issuers"));
}

#[test]
fn gateway_config_rejects_unknown_field() {
    // GatewayApiAuthConfig is a built-in typed table; unknown fields
    // under [api.auth.gateway] must be rejected at the deserializer level.
    let err = toml::from_str::<GatewayApiAuthConfig>(
        r#"signing_key_secret = "k"
rogue_field = true
"#,
    )
    .expect_err("reject unknown gateway field");
    assert!(err.to_string().contains("unknown field"));
}

// ---------------------------------------------------------------------------
// Field-shape branches not previously exercised: an empty chain ENTRY (not
// just an empty chain array), unparseable origin / public_base_url values,
// and the gateway table's blank audience / key_id rules.
// ---------------------------------------------------------------------------

#[test]
fn empty_chain_entry_is_rejected_by_validate() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway", ""]

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg.validate().expect_err("empty chain entry rejected");
    assert!(
        err.contains("chain entries must not be empty"),
        "expected empty-entry error, got: {err}"
    );
}

#[test]
fn trusted_browser_origins_rejects_unparseable_url() {
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
trusted_browser_origins = ["not a url at all"]

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg.validate().expect_err("reject unparseable origin");
    assert!(
        err.contains("not a valid URL"),
        "expected invalid-URL error, got: {err}"
    );
}

#[test]
fn public_base_url_rejects_unparseable_url() {
    // The OAuth-common fields are shape-validated even without an OAuth
    // source in chain at the contract layer; build the config as it would
    // parse and hit the URL-parse branch directly.
    let cfg: ApiAuthConfig = toml::from_str(
        r#"chain = ["gateway"]
public_base_url = "not a url at all"

[gateway]
signing_key_secret = "k"
"#,
    )
    .expect("parse");
    let err = cfg
        .validate()
        .expect_err("reject unparseable public_base_url");
    assert!(
        err.contains("public_base_url is not a valid URL"),
        "expected invalid-URL error, got: {err}"
    );
}

#[test]
fn gateway_table_rejects_blank_audience_and_key_id() {
    let mut g = GatewayApiAuthConfig::default();
    g.signing_key_secret = Some("signing-key".to_string());
    g.audience = "   ".to_string();
    let err = g.validate().expect_err("blank audience rejected");
    assert!(
        err.contains("api.auth.gateway.audience must not be blank"),
        "expected blank-audience error, got: {err}"
    );

    let mut g = GatewayApiAuthConfig::default();
    g.signing_key_secret = Some("signing-key".to_string());
    g.key_id = String::new();
    let err = g.validate().expect_err("blank key_id rejected");
    assert!(
        err.contains("api.auth.gateway.key_id must not be blank"),
        "expected blank-key_id error, got: {err}"
    );
}
