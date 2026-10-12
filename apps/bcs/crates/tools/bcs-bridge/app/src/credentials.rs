//! Provider webhook credentials for gateway mode.
//!
//! A [`CredentialSource`] supplies the Bearer token BCS presents on webhook
//! delivery to a gateway Provider. The default [`EnvCredentials`] reads a
//! runtime JSON registry from `BCS_BRIDGE_PROVIDER_TOKENS`; distributions may
//! supply another source (for example credentials embedded at build time),
//! reusing [`ProviderTokens`] for parsing and validation.

use std::collections::BTreeMap;
use std::fmt;

use anyhow::{Result, anyhow, bail};
use serde::de::{self, MapAccess, Visitor};

/// Runtime registry read by [`EnvCredentials`].
pub const PROVIDER_TOKENS_ENV: &str = "BCS_BRIDGE_PROVIDER_TOKENS";
const MAX_REGISTRY_BYTES: usize = 1024 * 1024;
pub(crate) const MAX_TOKEN_BYTES: usize = 16 * 1024;

/// Supplies gateway webhook credentials; consulted by `register` and `start`
/// only for gateway-mode configurations.
pub trait CredentialSource: Send + Sync {
    fn webhook_token(&self, provider_id: &str) -> Result<String>;
}

/// Reads the `BCS_BRIDGE_PROVIDER_TOKENS` JSON object (Provider ID -> token).
pub struct EnvCredentials;

impl CredentialSource for EnvCredentials {
    fn webhook_token(&self, provider_id: &str) -> Result<String> {
        let runtime = read_env(PROVIDER_TOKENS_ENV)?;
        let tokens = ProviderTokens::from_sources("{}", runtime.as_deref(), PROVIDER_TOKENS_ENV)?;
        Ok(tokens.token_for(provider_id)?.to_owned())
    }
}

/// Read an optional environment variable that must be UTF-8 when present.
pub fn read_env(name: &str) -> Result<Option<String>> {
    match std::env::var(name) {
        Ok(value) => Ok(Some(value)),
        Err(std::env::VarError::NotPresent) => Ok(None),
        Err(std::env::VarError::NotUnicode(_)) => bail!("{name} must contain UTF-8 JSON"),
    }
}

/// A validated Provider ID -> webhook token registry.
pub struct ProviderTokens {
    credentials: BTreeMap<String, String>,
    /// Variable name of the registry, for errors.
    source: &'static str,
}

impl ProviderTokens {
    /// Parse a base registry (`compiled`, e.g. embedded at build time; `"{}"`
    /// when there is none) and apply `runtime` entries over it by Provider ID.
    /// `source` names the registry in error messages.
    pub fn from_sources(compiled: &str, runtime: Option<&str>, source: &'static str) -> Result<Self> {
        let mut credentials = parse_registry(compiled, "compiled", source)?;
        if let Some(runtime) = runtime {
            credentials.extend(parse_registry(runtime, "runtime", source)?);
        }
        Ok(Self { credentials, source })
    }

    pub fn token_for(&self, provider_id: &str) -> Result<&str> {
        self.credentials.get(provider_id).map(String::as_str).ok_or_else(|| {
            anyhow!("Provider has no webhook credential configured in {}", self.source)
        })
    }
}

impl fmt::Debug for ProviderTokens {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.debug_struct("ProviderTokens")
            .field("provider_count", &self.credentials.len())
            .field("source", &self.source)
            .finish_non_exhaustive()
    }
}

// serde_json's normal map deserializer silently keeps the last duplicate key.
// Ambiguous credential sources must fail before runtime overrides are applied.
struct RegistryVisitor;

impl<'de> Visitor<'de> for RegistryVisitor {
    type Value = BTreeMap<String, String>;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("an object mapping unique Provider IDs to credentials")
    }

    fn visit_map<M: MapAccess<'de>>(self, mut entries: M) -> std::result::Result<Self::Value, M::Error> {
        let mut result = BTreeMap::new();
        while let Some((provider, credential)) = entries.next_entry::<String, String>()? {
            if result.insert(provider, credential).is_some() {
                return Err(de::Error::custom("duplicate Provider ID"));
            }
        }
        Ok(result)
    }
}

fn parse_registry(source: &str, origin: &str, name: &str) -> Result<BTreeMap<String, String>> {
    if source.len() > MAX_REGISTRY_BYTES {
        bail!("{origin} {name} exceeds the 1 MiB size limit");
    }
    let mut deserializer = serde_json::Deserializer::from_str(source);
    // Discard serde diagnostics: malformed values could contain credentials.
    let credentials = serde::Deserializer::deserialize_map(&mut deserializer, RegistryVisitor)
        .map_err(|_| anyhow!("{origin} {name} must be a JSON object with unique Provider IDs and string credentials"))?;
    deserializer.end().map_err(|_| anyhow!("{origin} {name} contains invalid JSON"))?;
    for (provider, credential) in &credentials {
        if provider.trim().is_empty() {
            bail!("{origin} {name} contains a blank Provider ID");
        }
        if !valid_bearer_credential(credential) {
            bail!("{origin} {name} contains an empty or invalid Bearer credential");
        }
    }
    Ok(credentials)
}

fn valid_bearer_credential(credential: &str) -> bool {
    // RFC 6750 b64token permits these ASCII characters and trailing '=' only.
    let unpadded = credential.trim_end_matches('=');
    !unpadded.is_empty() && credential.len() <= MAX_TOKEN_BYTES && unpadded.bytes().all(|byte| {
        byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~' | b'+' | b'/')
    })
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use serde_json::json;

    fn tokens(compiled: &str, runtime: Option<&str>) -> Result<ProviderTokens> {
        ProviderTokens::from_sources(compiled, runtime, PROVIDER_TOKENS_ENV)
    }

    #[test]
    fn compiled_registry_is_used_without_runtime_override() {
        let registry = tokens(r#"{"prv_a":"compiled-a","prv_b":"compiled-b"}"#, None).unwrap();
        assert_eq!(registry.token_for("prv_a").unwrap(), "compiled-a");
        assert_eq!(registry.token_for("prv_b").unwrap(), "compiled-b");
    }

    #[test]
    fn runtime_registry_overrides_by_provider_and_preserves_other_entries() {
        let registry = tokens(
            r#"{"prv_a":"compiled-a","prv_b":"compiled-b"}"#,
            Some(r#"{"prv_a":"runtime-a","prv_c":"runtime-c"}"#),
        ).unwrap();
        assert_eq!(registry.token_for("prv_a").unwrap(), "runtime-a");
        assert_eq!(registry.token_for("prv_b").unwrap(), "compiled-b");
        assert_eq!(registry.token_for("prv_c").unwrap(), "runtime-c");
    }

    #[test]
    fn empty_registry_and_unknown_provider_fail_at_selection() {
        for (compiled, runtime) in [("{}", None), ("{}", Some("{}"))] {
            let registry = tokens(compiled, runtime).unwrap();
            let error = registry.token_for("prv_missing").unwrap_err().to_string();
            assert!(error.contains("BCS_BRIDGE_PROVIDER_TOKENS"));
        }
    }

    #[test]
    fn empty_runtime_object_keeps_compiled_entries() {
        let registry = tokens(r#"{"prv_a":"compiled-a"}"#, Some("{}")).unwrap();
        assert_eq!(registry.token_for("prv_a").unwrap(), "compiled-a");
    }

    #[test]
    fn rejects_invalid_registry_sources_before_merging() {
        for source in [
            "", " ", "null", "[]", r#"{"prv_a":null}"#, r#"{"prv_a":12}"#,
            r#"{"prv_a":"first","prv_a":"second"}"#,
            r#"{"prv_a":"first","prv_\u0061":"second"}"#,
            r#"{"":"token"}"#, r#"{" \t\n":"token"}"#, r#"{"\u2003":"token"}"#,
            r#"{"prv_a":"token"} trailing"#,
        ] {
            assert!(tokens(source, Some(r#"{"prv_a":"valid"}"#)).is_err(), "invalid compiled registry accepted");
            assert!(tokens("{}", Some(source)).is_err(), "invalid runtime registry accepted");
        }
    }

    #[test]
    fn rejects_empty_and_invalid_bearer_credentials() {
        for value in ["", " ", "token with space", "Bearer token", "token\n", "token\r", "token\t", "token:bad", "token\0", "秘密", "=", "a=b"] {
            let source = json!({"prv_a": value}).to_string();
            assert!(tokens(&source, None).is_err(), "invalid bearer credential accepted");
            assert!(tokens("{}", Some(&source)).is_err(), "invalid runtime credential accepted");
        }
    }

    #[test]
    fn accepts_all_bearer_token_characters_and_trailing_padding() {
        let value = "AZaz09-._~+/==";
        let source = json!({"prv_a": value}).to_string();
        assert_eq!(tokens(&source, None).unwrap().token_for("prv_a").unwrap(), value);
    }

    #[test]
    fn registry_errors_and_debug_do_not_disclose_credentials() {
        let secret = "test-secret-do-not-disclose";
        for source in [format!(r#"{{"prv_a":"{secret}""#), format!(r#"{{"prv_a":"{secret}","prv_a":"other"}}"#), format!(r#"{{"prv_a":["{secret}"]}}"#)] {
            let error = tokens(&source, None).unwrap_err();
            assert!(!format!("{error:#}").contains(secret));
            assert!(!format!("{error:?}").contains(secret));
        }
        let source = json!({"prv_a": secret}).to_string();
        let registry = tokens(&source, None).unwrap();
        assert!(!format!("{registry:?}").contains(secret));
        assert!(!format!("{:#}", registry.token_for(secret).unwrap_err()).contains(secret));
    }

    #[test]
    fn bounds_registry_input() {
        let source = json!({"prv_a": "x".repeat(2 * 1024 * 1024)}).to_string();
        assert!(tokens(&source, None).is_err());
        assert!(tokens("{}", Some(&source)).is_err());
    }

    #[test]
    #[cfg(unix)]
    fn load_rejects_non_utf8_environment() {
        const CHILD_FLAG: &str = "BCS_BRIDGE_TEST_NON_UTF8_CREDENTIALS";
        if std::env::var_os(CHILD_FLAG).is_some() {
            let error = EnvCredentials.webhook_token("prv_a").unwrap_err();
            assert!(error.to_string().contains("UTF-8"));
            return;
        }
        use std::os::unix::ffi::OsStringExt;
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "credentials::tests::load_rejects_non_utf8_environment", "--nocapture"])
            .env(CHILD_FLAG, "1")
            .env("BCS_BRIDGE_PROVIDER_TOKENS", std::ffi::OsString::from_vec(vec![0xff]))
            .output().unwrap();
        assert!(output.status.success(), "child environment validation failed: {}", String::from_utf8_lossy(&output.stdout));
    }
}
