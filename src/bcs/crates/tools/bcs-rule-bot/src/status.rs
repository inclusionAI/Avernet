use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::Serialize;

#[derive(Debug, Clone)]
pub struct StatusUpdate {
    pub profile: String,
    pub name: String,
    pub behavior: String,
    pub state: InstanceState,
    pub bot_uuid: Option<String>,
    pub last_error: Option<String>,
}

#[derive(Debug, Clone)]
pub enum StatusCommand {
    Update(StatusUpdate),
    Touch(String),
}

#[derive(Debug, Clone, Copy)]
pub enum InstanceState {
    Starting,
    Connected,
    Reconnecting,
    Error,
    Stopped,
}

impl InstanceState {
    fn as_str(self) -> &'static str {
        match self {
            Self::Starting => "starting",
            Self::Connected => "connected",
            Self::Reconnecting => "reconnecting",
            Self::Error => "error",
            Self::Stopped => "stopped",
        }
    }
}

#[derive(Debug, Serialize)]
struct HostStatus {
    pid: u32,
    manifest: String,
    updated_at: u64,
    bots: BTreeMap<String, BotStatus>,
}

#[derive(Debug, Serialize)]
struct BotStatus {
    name: String,
    state: String,
    behavior: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    bot_uuid: Option<String>,
    last_heartbeat_at: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    last_error: Option<String>,
}

pub struct StatusReporter {
    path: PathBuf,
    manifest: PathBuf,
    bots: BTreeMap<String, BotStatus>,
}

impl StatusReporter {
    pub fn new(path: PathBuf, manifest: PathBuf) -> Self {
        Self {
            path,
            manifest,
            bots: BTreeMap::new(),
        }
    }

    pub fn apply(&mut self, update: StatusUpdate) -> Result<()> {
        self.bots.insert(
            update.profile,
            BotStatus {
                name: update.name,
                state: update.state.as_str().to_string(),
                behavior: update.behavior,
                bot_uuid: update.bot_uuid,
                last_heartbeat_at: now_ms(),
                last_error: update.last_error,
            },
        );
        self.write()
    }

    pub fn touch(&mut self, profile: &str) -> Result<()> {
        if let Some(status) = self.bots.get_mut(profile) {
            status.last_heartbeat_at = now_ms();
        }
        self.write()
    }

    fn write(&self) -> Result<()> {
        let parent = self
            .path
            .parent()
            .context("status path has no parent directory")?;
        fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
        let status = HostStatus {
            pid: std::process::id(),
            manifest: self.manifest.display().to_string(),
            updated_at: now_ms(),
            bots: self.bots.clone(),
        };
        write_json_atomic(&self.path, &status)
    }
}

impl Clone for BotStatus {
    fn clone(&self) -> Self {
        Self {
            name: self.name.clone(),
            state: self.state.clone(),
            behavior: self.behavior.clone(),
            bot_uuid: self.bot_uuid.clone(),
            last_heartbeat_at: self.last_heartbeat_at,
            last_error: self.last_error.clone(),
        }
    }
}

fn write_json_atomic(path: &Path, value: &impl Serialize) -> Result<()> {
    let temporary = path.with_extension("json.tmp");
    let content = serde_json::to_vec_pretty(value)?;
    fs::write(&temporary, content)
        .with_context(|| format!("failed to write {}", temporary.display()))?;
    fs::rename(&temporary, path)
        .with_context(|| format!("failed to replace {}", path.display()))?;
    Ok(())
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_millis() as u64)
}
