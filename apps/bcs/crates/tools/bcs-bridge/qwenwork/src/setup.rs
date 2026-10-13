//! Local desktop configuration bootstrap. Never mutates the desktop database.
use std::{fs, io::Write, path::{Path, PathBuf}};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use uuid::Uuid;

pub fn initialize(config_path: &Path, options: &toml::Table) -> Result<String> {
    let home = dirs::home_dir().context("Cannot locate home directory")?;
    initialize_at(config_path, options, &home)
}

fn initialize_at(config_path: &Path, overrides: &toml::Table, home: &Path) -> Result<String> {
    for key in overrides.keys() {
        if !["database_path", "settings_path", "listen"].contains(&key.as_str()) {
            bail!("Unknown initializer option '{key}'");
        }
    }
    let mut lock_name = config_path.as_os_str().to_owned();
    lock_name.push(".init.lock");
    let mut lock_options = fs::OpenOptions::new();
    lock_options.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)] {
        use std::os::unix::fs::OpenOptionsExt;
        lock_options.mode(0o600);
    }
    let lock = lock_options.open(PathBuf::from(lock_name)).context("Cannot lock bridge initialization; register first")?;
    lock.try_lock().context("Another bridge initialization is already running")?;
    let original = fs::read(config_path).context("Register the qwenwork Bot before initializing")?;
    let text = std::str::from_utf8(&original).context("Bridge configuration must be UTF-8")?;
    let mut document: toml::Table = toml::from_str(text).map_err(|_| anyhow::anyhow!("Invalid bridge TOML"))?;
    let bots = document.get_mut("bot").and_then(toml::Value::as_array_mut).context("No registered Bots in configuration")?;
    let indexes: Vec<_> = bots.iter().enumerate().filter(|(_, b)| b.get("engine").and_then(toml::Value::as_str) == Some("qwenwork")).map(|(i, _)| i).collect();
    if indexes.len() != 1 { bail!("Initialization requires exactly one qwenwork Bot in the selected configuration"); }
    let bot = bots[indexes[0]].as_table_mut().context("Invalid Bot configuration")?;
    let mut engine = bot.get("engine_options").and_then(toml::Value::as_table).cloned().unwrap_or_default();
    for key in ["database_path", "listen"] {
        if let Some(value) = overrides.get(key) { engine.insert(key.into(), value.clone()); }
    }
    engine.entry("listen").or_insert("127.0.0.1:52491".into());
    engine.entry("bot_id").or_insert(format!("bridge-qwenwork-{}", Uuid::new_v4().simple()).into());
    for key in ["secret", "hook_secret"] {
        if engine.get(key).and_then(toml::Value::as_str).is_none_or(|s| s.is_empty() || s.starts_with('<')) {
            engine.insert(key.into(), format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple()).into());
        }
    }
    if !engine.contains_key("database_path") {
        let candidates = ["QwenWorkCN", "QwenWork"].map(|name| home.join("Library/Application Support").join(name).join("data/agents.db"));
        let existing: Vec<_> = candidates.iter().filter(|p| p.is_file()).collect();
        if existing.len() != 1 { bail!("Cannot uniquely identify QwenWork database; pass --option database_path=/absolute/path/agents.db"); }
        engine.insert("database_path".into(), existing[0].to_str().context("Database path must be UTF-8")?.into());
    }
    let parsed = super::Options::parse(&engine).map_err(|_| anyhow::anyhow!("Invalid qwenwork options; check the database path and loopback listener"))?;
    if parsed.secret == parsed.hook_secret { bail!("Channel and Hook credentials must be different"); }
    super::session::validate(&parsed.database_path).map_err(|_| anyhow::anyhow!("Unsupported QwenWork database schema"))?;
    let settings_path = match overrides.get("settings_path") {
        Some(value) => PathBuf::from(value.as_str().context("settings_path must be a string")?),
        None => home.join(if parsed.database_path.to_string_lossy().contains("QwenWorkCN") { ".qwenworkcn" } else { ".qwenwork" }).join("settings.json"),
    };
    if !settings_path.is_absolute() || settings_path == config_path { bail!("Settings path must be absolute and distinct from bridge configuration"); }
    let old_settings = match fs::read(&settings_path) {
        Ok(bytes) => Some(bytes),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        Err(e) => return Err(e).context("Cannot read desktop settings"),
    };
    let mut settings: Value = match &old_settings {
        Some(bytes) => serde_json::from_slice(bytes).map_err(|_| anyhow::anyhow!("Invalid desktop settings JSON; no files changed"))?,
        None => json!({}),
    };
    merge_hooks(&mut settings, &format!("http://{}/hooks", parsed.listen), &parsed.hook_secret, parsed.mcp_socket_path.is_some())?;
    bot.insert("engine_options".into(), engine.into());
    let config_bytes = toml::to_string_pretty(&document).context("Cannot serialize bridge configuration")?.into_bytes();
    let settings_bytes = serde_json::to_vec_pretty(&settings).context("Cannot serialize desktop settings")?;
    let config_backup = backup(config_path, &original)?;
    let settings_backup = old_settings.as_ref().map(|bytes| backup(&settings_path, bytes)).transpose()?;
    // Reject concurrent changes rather than silently overwriting another editor.
    if fs::read(config_path)? != original || read_optional(&settings_path)? != old_settings { bail!("Configuration changed during initialization; retry"); }
    write_private(config_path, &config_bytes)?;
    if let Err(error) = write_private(&settings_path, &settings_bytes) {
        write_private(config_path, &original).context("Desktop settings write failed and bridge rollback failed; recover from backup")?;
        return Err(error).context("Cannot save desktop Hooks; bridge configuration restored");
    }
    Ok(format!("QwenWork initialization complete.\nBridge config: {}\nConfig backup: {}\nSettings: {}\nSettings backup: {}\n\nFill these values in QwenWork's WeCom Bot channel (keep Secret private):\nWebSocket URL: ws://{}/ws\nBot ID: {}\nSecret: {}\n\nReload desktop Hooks, start bridge with the selected --config, then enable the channel.\nCheck: curl http://{}/health\n", config_path.display(), config_backup.display(), settings_path.display(), settings_backup.map(|p| p.display().to_string()).unwrap_or_else(|| "new settings file".into()), parsed.listen, parsed.bot_id, parsed.secret, parsed.listen))
}

fn merge_hooks(settings: &mut Value, url: &str, secret: &str, interaction: bool) -> Result<()> {
    let root = settings.as_object_mut().context("Desktop settings must be a JSON object")?;
    let hooks = root.entry("hooks").or_insert(json!({})).as_object_mut().context("Desktop hooks must be an object")?;
    let authorization = format!("Bearer {secret}");
    for event in ["PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit", "QueryEnd", "PreCompact", "PostCompact"] {
        let entries = hooks.entry(event).or_insert(json!([])).as_array_mut().context("Desktop hook entries must be arrays")?;
        let mut preserved = Vec::new();
        for entry in entries.iter() {
            let mut entry = entry.clone();
            let list = entry.get_mut("hooks").and_then(Value::as_array_mut).context("Desktop hook entry requires a hooks array")?;
            for hook in list.iter().filter(|h| h.get("type").and_then(Value::as_str) == Some("http") && h.get("url").and_then(Value::as_str) == Some(url)) {
                if hook.pointer("/headers/Authorization").and_then(Value::as_str) != Some(&authorization) {
                    bail!("Existing Hooks use this listener with another credential; reuse that bridge config or choose --option listen=127.0.0.1:<free-port>");
                }
            }
            list.retain(|h| !(h.get("type").and_then(Value::as_str) == Some("http") && h.get("url").and_then(Value::as_str) == Some(url)));
            if !list.is_empty() { preserved.push(entry); }
        }
        let http = |timeout| json!({"type":"http","url":url,"headers":{"Authorization":authorization},"timeout":timeout});
        if event == "PreToolUse" && interaction {
            preserved.push(json!({"matcher":"^(?!AskUserQuestion$).*","hooks":[http(3)]}));
            preserved.push(json!({"matcher":"AskUserQuestion","hooks":[http(300)]}));
        } else if event == "PreToolUse" || event == "PostToolUse" {
            preserved.push(json!({"matcher":".*","hooks":[http(3)]}));
        } else { preserved.push(json!({"hooks":[http(3)]})); }
        *entries = preserved;
    }
    Ok(())
}

fn read_optional(path: &Path) -> Result<Option<Vec<u8>>> {
    match fs::read(path) {
        Ok(bytes) => Ok(Some(bytes)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e).context("Cannot inspect configuration"),
    }
}

fn backup(path: &Path, contents: &[u8]) -> Result<PathBuf> {
    let mut name = path.as_os_str().to_owned();
    name.push(format!(".backup-{}", Uuid::new_v4().simple()));
    let backup = PathBuf::from(name);
    write_private(&backup, contents)?;
    Ok(backup)
}

fn write_private(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent).context("Cannot create configuration directory")?;
    let mut temp = tempfile::NamedTempFile::new_in(parent).context("Cannot prepare private configuration file")?;
    #[cfg(unix)] {
        use std::os::unix::fs::PermissionsExt;
        temp.as_file().set_permissions(fs::Permissions::from_mode(0o600))?;
    }
    temp.write_all(bytes)?;
    temp.as_file().sync_all()?;
    temp.persist(path).map_err(|_| anyhow::anyhow!("Cannot replace configuration file"))?;
    Ok(())
}

#[cfg(test)]
#[path = "setup_tests.rs"]
mod tests;
