use std::collections::BTreeMap;

// ---------------------------------------------------------------------------
// Caller identity service configuration (spec §10.13.7, P1.7/P2-B assembly).
//
// The composition root reads this optional section; when absent, no caller
// capability is assembled and the anti-downgrade guard stays closed. When
// present, the selected plugin validates its fields and refuses invalid values (fail
// closed — never a permissive default and never a success Noop).
// ---------------------------------------------------------------------------

/// Speaking-lease sizing (spec §10.13.9, §15.99). One server-wide idle
/// window: it is granted at every acquire/preempt AND required at message
/// submission, so raising it widens both the hold window and the
/// submission grace. Frontends read `expiresAt_ms` per lease — they pick
/// the configured value up with zero changes.
#[derive(Debug, Clone, PartialEq, Eq, ::serde::Serialize, ::serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SpeakingLeaseConfig {
    pub idle_timeout_ms: i64,
}

impl Default for SpeakingLeaseConfig {
    fn default() -> Self {
        // Frozen legacy constant value: 60s, identical to the pre-§15.99
        // hard-coded `LEASE_IDLE_TIMEOUT_MS`.
        Self { idle_timeout_ms: 60_000 }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, ::serde::Serialize, ::serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CallerIdentityServiceConfig {
    /// Name of one linked Caller Identity Plugin factory. Unknown names fail at startup.
    pub plugin: String,
    /// Required only for the JWT plugin; internal plugins do not need dummy JWT keys.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub jwt: Option<CallerIdentityJwtConfig>,
    /// Implementation-owned settings. The selected factory must validate their schema.
    #[serde(default)]
    pub options: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, PartialEq, Eq, ::serde::Serialize, ::serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CallerIdentityJwtConfig {
    pub issuer: String,
    /// Key id signing headers and required in the trusted verification set.
    pub signing_kid: String,
    /// Ed25519 private key PEM (PKCS#8). Configuration-injected material;
    /// production deployments inject via secret provisioning rather than
    /// committing files.
    pub signing_key_pem: String,
    /// Trusted verification keys — at least one must match `signing_kid`.
    pub public_keys: Vec<CallerIdentityTrustedKeyConfig>,
    /// Independent pseudonym-derivation secret; must not be derived from the
    /// signing keys. Minimum 16 bytes, hex encoded.
    pub pseudonym_key_hex: String,
    /// Longest subject/proof lifetime seconds (spec example: 300).
    pub max_ttl_seconds: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, ::serde::Serialize, ::serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CallerIdentityTrustedKeyConfig {
    pub kid: String,
    pub public_pem: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn external_plugins_need_no_jwt_and_unknown_top_level_fields_fail() {
        let config: CallerIdentityServiceConfig = toml::from_str(
            "plugin = 'private-provider'\n[options]\nregion = 'test'",
        ).unwrap();
        assert!(config.jwt.is_none());
        assert_eq!(config.options["region"], "test");
        assert!(toml::from_str::<CallerIdentityServiceConfig>(
            "plugin = 'jwt'\nunknown = true",
        ).is_err());
    }

    #[test]
    fn existing_jwt_table_still_deserializes() {
        let config: CallerIdentityServiceConfig = toml::from_str(r#"
plugin = "jwt"
[jwt]
issuer = "test"
signing_kid = "test-key"
signing_key_pem = "injected-at-startup"
public_keys = [{kid = "test-key", public_pem = "injected-at-startup"}]
pseudonym_key_hex = "injected-at-startup"
max_ttl_seconds = 300
"#).unwrap();
        assert!(config.options.is_empty());
        assert_eq!(config.jwt.unwrap().max_ttl_seconds, 300);
    }
}
