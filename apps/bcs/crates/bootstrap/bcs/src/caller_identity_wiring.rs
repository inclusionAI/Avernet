//! Composition of the `[caller_identity]` identity plugin (P1.7).
//!
//! Instance selection and configuration validation for the
//! `CallerIdentityPlugin` at the composition root: absent means no caller
//! capability (the anti-downgrade guard stays closed), present-and-invalid
//! refuses startup. There is no success Noop and no permissive default.

use std::sync::Arc;

// Link the public implementation; internal binaries link their own factories.
use bcs_caller_identity_jwt as _;

use bcs_config_api::CallerIdentityServiceConfig;
use bcs_caller_identity_api::CallerIdentityPlugin;

use crate::BcsConfig;

/// Assemble the configured identity plugin. `Ok(None)` is the absent
/// section — a legitimate "no caller capability" deployment, never a fake
/// plugin. Every failure is `BcsError::InvalidConfig` with a closed
/// message that never echoes key material.
pub(crate) fn assemble_caller_identity_plugin(
    config: &BcsConfig,
) -> crate::Result<Option<Arc<dyn CallerIdentityPlugin>>> {
    let Some(caller_config) = &config.caller_identity else {
        return Ok(None);
    };
    assemble_from_section(caller_config).map(Some)
}

fn assemble_from_section(
    caller_config: &CallerIdentityServiceConfig,
) -> crate::Result<Arc<dyn CallerIdentityPlugin>> {
    bcs_caller_identity_api::build_caller_identity_plugin(caller_config).map_err(|error| {
        crate::BcsError::InvalidConfig(format!("caller_identity plugin could not be assembled: {error}"))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_config_api::{CallerIdentityJwtConfig, CallerIdentityTrustedKeyConfig};

    const PRIVATE_KEY_A: &str = "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIJLx28TZRXq9av+DXCadGa9MFANFALD61QgkJ4ZvD2YM\n-----END PRIVATE KEY-----";
    const PUBLIC_KEY_A: &str = "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEABdQgXwZ5e+KJRyNu5FNYaj1mRtE/BzLJIzaB+RZkIgc=\n-----END PUBLIC KEY-----";
    /// 32 raw bytes, hex-encoded at build time in the fixtures below.
    const PSEUDONYM_KEY_HEX: &str = "746573742d70736575646f6e796d2d6b65792d30313233343536373839616263646566";

    fn section(mut jwt: CallerIdentityJwtConfig) -> CallerIdentityServiceConfig {
        if jwt.issuer.is_empty() {
            jwt.issuer = "bcs-test".into();
        }
        if jwt.signing_kid.is_empty() {
            jwt.signing_kid = "kid-a".into();
        }
        if jwt.signing_key_pem.is_empty() {
            jwt.signing_key_pem = PRIVATE_KEY_A.into();
        }
        if jwt.public_keys.is_empty() {
            jwt.public_keys = vec![CallerIdentityTrustedKeyConfig {
                kid: "kid-a".into(),
                public_pem: PUBLIC_KEY_A.into(),
            }];
        }
        if jwt.pseudonym_key_hex.is_empty() {
            jwt.pseudonym_key_hex = PSEUDONYM_KEY_HEX.into();
        }
        if jwt.max_ttl_seconds == 0 {
            jwt.max_ttl_seconds = 300;
        }
        CallerIdentityServiceConfig {
            plugin: "jwt".into(),
            jwt: Some(jwt),
            options: Default::default(),
        }
    }

    pub(super) fn base_section() -> CallerIdentityServiceConfig {
        section(CallerIdentityJwtConfig {
            issuer: String::new(),
            signing_kid: String::new(),
            signing_key_pem: String::new(),
            public_keys: Vec::new(),
            pseudonym_key_hex: String::new(),
            max_ttl_seconds: 0,
        })
    }

    fn test_config(section: Option<CallerIdentityServiceConfig>) -> BcsConfig {
        let mut config = BcsConfig::default();
        config.caller_identity = section;
        config
    }

    #[test]
    fn absent_section_yields_no_capability_and_no_fake_plugin() {
        let assembled = assemble_caller_identity_plugin(&test_config(None)).expect("absent is valid");
        assert!(assembled.is_none(), "absent [caller_identity] = no caller capability");
    }

    #[test]
    fn a_valid_section_assembles_the_jwt_plugin() {
        let assembled =
            assemble_caller_identity_plugin(&test_config(Some(base_section()))).expect("valid section");
        assert!(assembled.is_some(), "a complete section wires the real plugin");
    }

    #[test]
    fn present_but_incomplete_refuses_startup_fail_closed() {
        let cases: Vec<(&str, CallerIdentityJwtConfig)> = vec![
            ("empty issuer", CallerIdentityJwtConfig {
                issuer: "  ".into(),
                signing_kid: String::new(),
                signing_key_pem: String::new(),
                public_keys: Vec::new(),
                pseudonym_key_hex: String::new(),
                max_ttl_seconds: 0,
            }),
            ("empty signing kid", CallerIdentityJwtConfig {
                issuer: "bcs".into(),
                signing_kid: " ".into(),
                signing_key_pem: String::new(),
                public_keys: Vec::new(),
                pseudonym_key_hex: String::new(),
                max_ttl_seconds: 0,
            }),
            ("empty pseudonym key", CallerIdentityJwtConfig {
                issuer: "bcs".into(),
                signing_kid: "kid-a".into(),
                signing_key_pem: String::new(),
                public_keys: Vec::new(),
                pseudonym_key_hex: "   ".into(),
                max_ttl_seconds: 0,
            }),
            ("pseudonym key not hex", CallerIdentityJwtConfig {
                issuer: "bcs".into(),
                signing_kid: "kid-a".into(),
                signing_key_pem: String::new(),
                public_keys: Vec::new(),
                pseudonym_key_hex: "not-hex-ätz".into(),
                max_ttl_seconds: 0,
            }),
            ("weak pseudonym key", CallerIdentityJwtConfig {
                issuer: "bcs".into(),
                signing_kid: "kid-a".into(),
                signing_key_pem: String::new(),
                public_keys: Vec::new(),
                pseudonym_key_hex: "00112233".into(), // 4 bytes: below the minimum
                max_ttl_seconds: 0,
            }),
            ("bad signing key pem", CallerIdentityJwtConfig {
                issuer: "bcs".into(),
                signing_kid: "kid-a".into(),
                signing_key_pem: "not a pem".into(),
                public_keys: Vec::new(),
                pseudonym_key_hex: String::new(),
                max_ttl_seconds: 0,
            }),
            ("bad verification key pem", CallerIdentityJwtConfig {
                issuer: "bcs".into(),
                signing_kid: "kid-a".into(),
                signing_key_pem: PRIVATE_KEY_A.into(),
                public_keys: vec![CallerIdentityTrustedKeyConfig {
                    kid: "kid-a".into(),
                    public_pem: "not a pem".into(),
                }],
                pseudonym_key_hex: PSEUDONYM_KEY_HEX.into(),
                max_ttl_seconds: 300,
            }),
        ];
        for (name, jwt) in cases {
            let error = assemble_from_section(&section(jwt))
                .err()
                .unwrap_or_else(|| panic!("{name} must refuse startup"));
            let message = error.to_string();
            assert!(message.contains("caller_identity"), "{name}: closed refusal, got {message}");
            // Error messages never echo the rejected key material.
            assert!(!message.contains(PRIVATE_KEY_A), "{name}: key material leaked");
            assert!(!message.contains(PSEUDONYM_KEY_HEX), "{name}: pseudonym material leaked");
        }
        // Zero TTL and an empty trust set must refuse — build them after
        // the section defaults so the fill-in cannot mask the cases.
        let mut zero_ttl = base_section();
        zero_ttl.jwt.as_mut().unwrap().max_ttl_seconds = 0;
        assert!(
            assemble_from_section(&zero_ttl).is_err(),
            "zero ttl must refuse startup"
        );
        let mut no_trust = base_section();
        no_trust.jwt.as_mut().unwrap().public_keys = Vec::new();
        assert!(
            assemble_from_section(&no_trust).is_err(),
            "empty trust set must refuse startup"
        );
    }

    #[test]
    fn unregistered_plugin_fails_closed_in_public_composition() {
        let mut config = base_section();
        config.plugin = "unregistered-identity-provider".into();
        let error = assemble_from_section(&config)
            .err()
            .expect("composition refuses an unregistered plugin");
        assert!(error.to_string().contains("identity provider unavailable"));
    }

    #[test]
    fn multiple_trusted_keys_assemble_with_rotation_headroom() {
        let mut config = base_section();
        config.jwt.as_mut().unwrap().public_keys = vec![
            CallerIdentityTrustedKeyConfig { kid: "kid-a".into(), public_pem: PUBLIC_KEY_A.into() },
            CallerIdentityTrustedKeyConfig { kid: "kid-b".into(), public_pem: PUBLIC_KEY_A.into() },
        ];
        assert!(assemble_from_section(&config).is_ok(), "a multi-kid trust set accepts rotation");
    }
}
#[cfg(test)]
mod plugin_config_contract {
    use super::*;

    #[test]
    fn jwt_configuration_is_required_and_unknown_options_are_rejected() {
        let config = CallerIdentityServiceConfig {
            plugin: "jwt".into(), jwt: None, options: Default::default(),
        };
        assert!(assemble_from_section(&config).is_err());
        let mut config = super::tests::base_section();
        config.options.insert("unknown".into(), serde_json::Value::Bool(true));
        assert!(assemble_from_section(&config).is_err());
    }
}
