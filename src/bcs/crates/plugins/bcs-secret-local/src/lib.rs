//! Local / mock implementations of [`SecretAccessPort`].
//!
//! Three flavors:
//! - [`InMemorySecretAccess`]  — explicit `name -> SecretRecord` map, used by tests.
//! - [`EnvSecretAccess`]       — reads `${prefix}<NAME>` env vars at construction
//!   (snapshotted, so tests can mutate env without racing).
//! - [`NoopSecretAccess`]      — every call returns `SecretAccessError::Unavailable`.
//!   Useful as the default when the composition root hasn't wired anything.

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::port::secret::{SecretAccessError, SecretAccessPort, SecretRecord};
use serde::Deserialize;
use tokio::sync::RwLock;
use tracing::warn;

// ── In-memory ─────────────────────────────────────────────────────────

#[derive(Default, Clone)]
pub struct InMemorySecretAccess {
    inner: Arc<RwLock<HashMap<String, StoredSecret>>>,
}

/// Stored value without the redundant `name` field (the key is the name).
#[derive(Debug, Clone, Default)]
struct StoredSecret {
    user: String,
    value: String,
}

impl InMemorySecretAccess {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_entries<I, K>(entries: I) -> Self
    where
        I: IntoIterator<Item = (K, String, String)>,
        K: Into<String>,
    {
        let map = entries
            .into_iter()
            .map(|(name, user, value)| (name.into(), StoredSecret { user, value }))
            .collect();
        Self {
            inner: Arc::new(RwLock::new(map)),
        }
    }

    /// Load `{name -> {user, value}}` entries from a JSON file.
    pub async fn load_json_file(&self, path: impl AsRef<Path>) -> Result<usize, SecretAccessError> {
        #[derive(Deserialize)]
        struct Entry {
            #[serde(default)]
            user: String,
            value: String,
        }

        let contents = tokio::fs::read_to_string(path.as_ref())
            .await
            .map_err(|e| SecretAccessError::Unavailable(format!("read secret file: {e}")))?;
        let parsed: HashMap<String, Entry> = serde_json::from_str(&contents)
            .map_err(|e| SecretAccessError::Unavailable(format!("parse secret file: {e}")))?;

        let mut guard = self.inner.write().await;
        let added = parsed.len();
        for (name, entry) in parsed {
            guard.insert(
                name,
                StoredSecret {
                    user: entry.user,
                    value: entry.value,
                },
            );
        }
        Ok(added)
    }

    pub async fn insert(&self, name: impl Into<String>, user: impl Into<String>, value: impl Into<String>) {
        self.inner.write().await.insert(
            name.into(),
            StoredSecret {
                user: user.into(),
                value: value.into(),
            },
        );
    }
}

#[async_trait]
impl SecretAccessPort for InMemorySecretAccess {
    async fn get_secret(&self, name: &str) -> Result<SecretRecord, SecretAccessError> {
        if name.is_empty() {
            return Err(SecretAccessError::InvalidInput("secret name is empty".into()));
        }
        let guard = self.inner.read().await;
        match guard.get(name) {
            Some(stored) => Ok(SecretRecord {
                name: name.to_string(),
                user: stored.user.clone(),
                value: stored.value.clone(),
            }),
            None => Err(SecretAccessError::NotFound(name.into())),
        }
    }
}

// ── Environment-backed ────────────────────────────────────────────────

/// Reads `${prefix}<SECRET_NAME>` env vars. `-` / `.` / `/` in secret names
/// are normalised to `_` before lookup, mirroring common shell conventions.
#[derive(Clone)]
pub struct EnvSecretAccess {
    prefix: String,
    snapshot: Arc<HashMap<String, String>>,
}

impl EnvSecretAccess {
    /// Snapshots the current process env so subsequent mutations stay invisible.
    pub fn new(prefix: impl Into<String>) -> Self {
        let snapshot = std::env::vars().collect::<HashMap<_, _>>();
        Self {
            prefix: prefix.into(),
            snapshot: Arc::new(snapshot),
        }
    }

    /// Build from an explicit map (test helper).
    pub fn from_map(prefix: impl Into<String>, env: HashMap<String, String>) -> Self {
        Self {
            prefix: prefix.into(),
            snapshot: Arc::new(env),
        }
    }

    fn env_key(&self, name: &str) -> String {
        let mut k = self.prefix.clone();
        for ch in name.chars() {
            k.push(match ch {
                '-' | '.' | '/' => '_',
                other => other.to_ascii_uppercase(),
            });
        }
        k
    }
}

#[async_trait]
impl SecretAccessPort for EnvSecretAccess {
    async fn get_secret(&self, name: &str) -> Result<SecretRecord, SecretAccessError> {
        if name.is_empty() {
            return Err(SecretAccessError::InvalidInput("secret name is empty".into()));
        }
        match self.snapshot.get(&self.env_key(name)) {
            Some(value) => Ok(SecretRecord {
                name: name.to_string(),
                user: String::new(),
                value: value.clone(),
            }),
            None => Err(SecretAccessError::NotFound(name.into())),
        }
    }
}

// ── Noop ──────────────────────────────────────────────────────────────

/// Always reports `SecretAccessError::Unavailable`. Default safety net when
/// the composition root hasn't wired a real backend yet.
#[derive(Default, Clone, Copy)]
pub struct NoopSecretAccess;

#[async_trait]
impl SecretAccessPort for NoopSecretAccess {
    async fn get_secret(&self, _name: &str) -> Result<SecretRecord, SecretAccessError> {
        warn!("NoopSecretAccess::get_secret called; backend is disabled");
        Err(SecretAccessError::Unavailable(
            "no secret backend configured".into(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn in_memory_round_trip() {
        let plugin = InMemorySecretAccess::with_entries([(
            "jwt_signing_key".to_string(),
            "svc".to_string(),
            "s3cr3t".to_string(),
        )]);
        let s = plugin.get_secret("jwt_signing_key").await.unwrap();
        assert_eq!(s.name, "jwt_signing_key");
        assert_eq!(s.user, "svc");
        assert_eq!(s.value, "s3cr3t");
    }

    #[tokio::test]
    async fn in_memory_reports_missing_as_not_found() {
        let plugin = InMemorySecretAccess::new();
        let err = plugin.get_secret("nope").await.unwrap_err();
        assert!(matches!(err, SecretAccessError::NotFound(name) if name == "nope"));
    }

    #[tokio::test]
    async fn env_plugin_uppercases_and_normalises_separators() {
        let mut env = HashMap::new();
        env.insert("BCS_SECRET_JWT_KEY".into(), "abc".into());
        let plugin = EnvSecretAccess::from_map("BCS_SECRET_", env);
        let s = plugin.get_secret("jwt-key").await.unwrap();
        assert_eq!(s.value, "abc");
        assert!(s.user.is_empty());
    }

    #[tokio::test]
    async fn noop_is_always_unavailable() {
        let plugin = NoopSecretAccess;
        assert!(matches!(
            plugin.get_secret("x").await,
            Err(SecretAccessError::Unavailable(_))
        ));
    }

    #[tokio::test]
    async fn rejects_empty_name() {
        let plugin = InMemorySecretAccess::new();
        assert!(matches!(
            plugin.get_secret("").await,
            Err(SecretAccessError::InvalidInput(_))
        ));
    }

    #[tokio::test]
    async fn json_file_loads_entries() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("secrets.json");
        std::fs::write(
            &path,
            r#"{"k1": {"user": "u", "value": "v"}, "k2": {"value": "x"}}"#,
        )
        .unwrap();

        let plugin = InMemorySecretAccess::new();
        let n = plugin.load_json_file(&path).await.unwrap();
        assert_eq!(n, 2);
        assert_eq!(plugin.get_secret("k1").await.unwrap().value, "v");
        assert_eq!(plugin.get_secret("k2").await.unwrap().user, "");
    }
}
