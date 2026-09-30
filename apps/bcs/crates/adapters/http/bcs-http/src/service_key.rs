//! Service-invocation API keys (Part B Task 3/4, spec §9.6).
//!
//! Data model + helpers for `X-BCS-Service-Key` authentication on
//! `/services/{group_id}/sessions*` routes. Raw keys are never stored; only the
//! lowercase hex sha256 of the raw key is persisted in `BcsConfig.api_keys` and
//! built into an [`ApiKeyRegistry`] at startup. Route handlers resolve the
//! incoming header against the registry and record a [`ResolvedCaller`].

use std::sync::Arc;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// Service-invocation API key entry (spec §9.6.2).
///
/// Raw API keys are never stored in config; only the lowercase hex
/// sha256 of the raw key is persisted. The raw key arrives in the
/// `X-BCS-Service-Key` request header and is matched by
/// `sha256(raw)` against the registry built from these entries.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApiKeyEntry {
    /// Human-readable name of the key (must be globally unique).
    pub name: String,

    /// `sha256(raw_key)` as 64 lowercase hex chars (must be globally unique).
    pub sha256: String,

    /// Group IDs this key can access. Empty means admin key — can access
    /// any service group.
    #[serde(default)]
    pub bound_groups: Vec<String>,
}

/// Compile-time registry of API key entries (built once at startup).
#[derive(Clone)]
pub struct ApiKeyRegistry {
    entries: Arc<Vec<ApiKeyEntry>>,
}

impl ApiKeyRegistry {
    /// Build a registry from a slice of validated entries. Callers must
    /// run `BcsConfig::validate_api_keys` first; this constructor does
    /// not re-validate.
    pub fn new(entries: Vec<ApiKeyEntry>) -> Self {
        Self {
            entries: Arc::new(entries),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Resolve a raw key to its registry entry by sha256 lookup. Returns
    /// `None` if the key isn't recognised.
    pub fn resolve(&self, raw_key: &str) -> Option<&ApiKeyEntry> {
        let hash = sha256_hex(raw_key);
        self.entries.iter().find(|e| e.sha256 == hash)
    }
}

/// Caller identity surfaced into request extensions after auth succeeds.
#[derive(Clone, Debug)]
pub struct ResolvedCaller {
    pub key_name: String,
    /// `svc-key:<sha256[:16]>` — the stable service-caller principal
    /// recorded on `Session.caller_principal`.
    pub caller_principal: String,
}

/// Compute the lowercase hex sha256 of a raw string.
pub fn sha256_hex(raw: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(raw.as_bytes());
    hex::encode(hasher.finalize())
}

/// Build the `caller_principal` literal used in audit fields and
/// session ownership checks.
pub fn caller_principal_for(sha256_hash: &str) -> String {
    let prefix_len = sha256_hash.len().min(16);
    format!("svc-key:{}", &sha256_hash[..prefix_len])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(name: &str, sha: &str, bound: &[&str]) -> ApiKeyEntry {
        ApiKeyEntry {
            name: name.into(),
            sha256: sha.into(),
            bound_groups: bound.iter().map(|s| s.to_string()).collect(),
        }
    }

    #[test]
    fn sha256_hex_lowercase() {
        let h = sha256_hex("hello");
        // Reference value of sha256("hello")
        assert_eq!(
            h,
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        );
        assert!(h.chars().all(|c| !c.is_ascii_uppercase()));
    }

    #[test]
    fn caller_principal_truncates_to_16_chars() {
        let principal = caller_principal_for(
            "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        );
        assert_eq!(principal, "svc-key:abcdef0123456789");
    }

    #[test]
    fn caller_principal_handles_short_hash() {
        let principal = caller_principal_for("abcd");
        assert_eq!(principal, "svc-key:abcd");
    }

    #[test]
    fn registry_resolves_known_key() {
        let raw = "secret-key-value";
        let sha = sha256_hex(raw);
        let registry = ApiKeyRegistry::new(vec![entry("k1", &sha, &["g1"])]);
        assert_eq!(registry.resolve(raw).unwrap().name, "k1");
    }

    #[test]
    fn registry_rejects_unknown_key() {
        let registry = ApiKeyRegistry::new(vec![entry("k1", &sha256_hex("known"), &["g1"])]);
        assert!(registry.resolve("unknown").is_none());
    }

    #[test]
    fn empty_registry_resolves_to_none() {
        let registry = ApiKeyRegistry::new(vec![]);
        assert!(registry.is_empty());
        assert!(registry.resolve("anything").is_none());
    }
}
