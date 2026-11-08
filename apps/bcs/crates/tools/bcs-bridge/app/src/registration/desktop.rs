use std::{fs, path::{Path, PathBuf}};
use anyhow::{Context, bail};

use crate::multi_config::Document;

fn options(path: &Path, engine: &str) -> anyhow::Result<Option<toml::Table>> {
    let source = Document::load(path)?;
    let bots: Vec<_> = source.groups.iter().filter_map(|group| group.get("bot").and_then(toml::Value::as_array))
        .flatten().filter(|bot| bot.get("engine").and_then(toml::Value::as_str) == Some(engine)).collect();
    if bots.len() != 1 { return Ok(None); }
    let mut options = bots[0].get("engine_options").and_then(toml::Value::as_table).cloned();
    if engine == "qwenwork" {
        if let Some(settings) = options.as_ref().and_then(|o| o.get("settings_path")).and_then(toml::Value::as_str) {
            match fs::read(Path::new(settings).with_file_name("bcs-bridge.toml")) {
                Ok(bytes) => {
                    let shared: toml::Table = toml::from_str(std::str::from_utf8(&bytes).context("Invalid shared desktop configuration")?)
                        .map_err(|_| anyhow::anyhow!("Invalid shared desktop configuration"))?;
                    if shared.keys().any(|key| !["listen", "bot_id", "secret", "hook_secret", "database_path", "settings_path", "mcp_socket_path"].contains(&key.as_str())) {
                        bail!("Shared desktop configuration contains unsupported fields");
                    }
                    if let Some(options) = options.as_mut() { options.extend(shared); }
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(_) => bail!("Cannot read shared desktop configuration"),
            }
        }
    }
    Ok(options)
}

fn entries(directory: &Path) -> anyhow::Result<Vec<fs::DirEntry>> {
    let iter = match fs::read_dir(directory) {
        Ok(iter) => iter,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(_) => bail!("Cannot inspect saved bridge configurations"),
    };
    let mut result = Vec::new();
    for entry in iter {
        if result.len() == 128 { bail!("Too many saved configurations to select desktop settings safely"); }
        result.push(entry.context("Cannot inspect saved bridge configuration")?);
    }
    Ok(result)
}

/// Only desktop options cross environments; no BCN credentials or state are copied.
pub(super) async fn existing_options(root: &Path, target: &Path, engine: &str) -> anyhow::Result<Option<toml::Table>> {
    #[cfg(unix)]
    if let Some(active) = crate::instance::current_configuration(root).await? {
        if let Some(options) = options(&active, engine)? { return Ok(Some(options)); }
    }
    if engine != "qwenwork" { return Ok(None); }
    let mut candidates: Vec<PathBuf> = Vec::new();
    for entry in entries(root)? {
        let path = entry.path();
        if entry.file_type()?.is_file() && path.extension().is_some_and(|extension| extension == "toml")
            && !entry.file_name().to_string_lossy().starts_with('.') { candidates.push(path); }
    }
    let profiles = root.join("profiles");
    if fs::symlink_metadata(&profiles).is_ok_and(|meta| meta.file_type().is_symlink()) {
        bail!("Saved profiles directory must not be a symlink");
    }
    for entry in entries(&profiles)? {
        if !entry.file_type()?.is_dir() { continue; }
        let path = entry.path().join("bridge.toml");
        if fs::symlink_metadata(&path).is_ok_and(|meta| meta.file_type().is_file()) { candidates.push(path); }
    }
    let mut selected = None;
    for candidate in candidates {
        if candidate == target { continue; }
        if let Some(options) = options(&candidate, engine)? {
            if selected.as_ref().is_some_and(|previous| previous != &options) {
                bail!("Saved QwenWork profiles have conflicting desktop settings; start the intended existing profile before registering another environment");
            }
            selected = Some(options);
        }
    }
    Ok(selected)
}
