use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionInfo {
    pub bot_uuid: Option<String>,
    pub token: String,
    pub bcs_url: String,
}

#[derive(Debug, Clone)]
pub struct SessionStore {
    path: PathBuf,
}

impl SessionStore {
    pub fn new(profile_dir: &Path) -> Self {
        Self {
            path: profile_dir.join(".bcs").join("session.json"),
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn load(&self) -> Result<Option<SessionInfo>> {
        if !self.path.exists() {
            return Ok(None);
        }
        let content = fs::read_to_string(&self.path)
            .with_context(|| format!("failed to read {}", self.path.display()))?;
        let session: SessionInfo = serde_json::from_str(&content)
            .with_context(|| format!("failed to parse {}", self.path.display()))?;
        if session.token.trim().is_empty() {
            return Ok(None);
        }
        Ok(Some(session))
    }

    pub fn save(&self, session: &SessionInfo) -> Result<()> {
        let parent = self
            .path
            .parent()
            .context("session path has no parent directory")?;
        fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
        let temporary = self.path.with_extension("json.tmp");
        let content = serde_json::to_vec_pretty(session)?;

        let mut options = OpenOptions::new();
        options.create(true).truncate(true).write(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options
            .open(&temporary)
            .with_context(|| format!("failed to open {}", temporary.display()))?;
        file.write_all(&content)
            .with_context(|| format!("failed to write {}", temporary.display()))?;
        file.sync_all()
            .with_context(|| format!("failed to sync {}", temporary.display()))?;
        fs::rename(&temporary, &self.path).with_context(|| {
            format!(
                "failed to replace {} with {}",
                self.path.display(),
                temporary.display()
            )
        })?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn session_round_trip() {
        let temp = tempfile::tempdir().unwrap_or_else(|error| panic!("{error}"));
        let store = SessionStore::new(temp.path());
        let expected = SessionInfo {
            bot_uuid: Some("bot-1".to_string()),
            token: "secret".to_string(),
            bcs_url: "ws://127.0.0.1:21000/ws/bot".to_string(),
        };

        store
            .save(&expected)
            .unwrap_or_else(|error| panic!("{error}"));
        let actual = store
            .load()
            .unwrap_or_else(|error| panic!("{error}"))
            .unwrap_or_else(|| panic!("session is missing"));

        assert_eq!(actual.bot_uuid, expected.bot_uuid);
        assert_eq!(actual.token, expected.token);
        assert_eq!(actual.bcs_url, expected.bcs_url);
    }

    #[test]
    fn missing_and_empty_tokens_are_not_restored() {
        let temp = tempfile::tempdir().unwrap_or_else(|error| panic!("{error}"));
        let store = SessionStore::new(temp.path());

        assert!(
            store
                .load()
                .unwrap_or_else(|error| panic!("missing token should be accepted: {error}"))
                .is_none()
        );
        fs::create_dir_all(
            store
                .path()
                .parent()
                .unwrap_or_else(|| panic!("session path should have a parent")),
        )
        .unwrap_or_else(|error| panic!("session directory should be created: {error}"));
        fs::write(
            store.path(),
            r#"{"bot_uuid":null,"token":"  ","bcs_url":"ws://localhost"}"#,
        )
        .unwrap_or_else(|error| panic!("empty session fixture should be written: {error}"));

        assert!(
            store
                .load()
                .unwrap_or_else(|error| panic!("empty token should be accepted: {error}"))
                .is_none()
        );
        assert!(store.path().ends_with(".bcs/session.json"));
    }
}
