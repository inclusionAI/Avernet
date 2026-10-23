//! Local desktop configuration bootstrap. Never mutates the desktop database.
use std::{fs, io::Write, path::{Path, PathBuf}};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use uuid::Uuid;

pub fn initialize(config_path: &Path, options: &toml::Table) -> Result<String> {
    let home = dirs::home_dir().context("Cannot locate home directory")?;
    initialize_at(config_path, options, &home)
}

pub fn deinitialize(config_path: &Path, options: &toml::Table) -> Result<String> {
    let home = dirs::home_dir().context("Cannot locate home directory")?;
    deinitialize_at(config_path, options, &home)
}

fn configuration_lock(config_path: &Path) -> Result<fs::File> {
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
    Ok(lock)
}

fn deinitialize_at(config_path: &Path, options: &toml::Table, home: &Path) -> Result<String> {
    if options.keys().any(|key| key != "settings_path") { bail!("Deinit accepts only --option settings_path"); }
    let _lock = configuration_lock(config_path)?;
    let original = fs::read(config_path).context("Keep the registered configuration until deinit completes")?;
    let mut config: toml::Table = toml::from_str(std::str::from_utf8(&original).context("Configuration must be UTF-8")?)
        .map_err(|_| anyhow::anyhow!("Invalid bridge TOML"))?;
    let bot = unique_qwenwork_bot(&mut config)?;
    let engine = bot.get("engine_options").and_then(toml::Value::as_table).context("QwenWork is not initialized")?;
    let engine = desktop_options(engine)?;
    let listen = engine.get("listen").and_then(toml::Value::as_str).context("Missing listener")?;
    let secret = engine.get("hook_secret").and_then(toml::Value::as_str).filter(|s| !s.is_empty()).context("Missing Hook credential")?;
    let saved = options.get("settings_path").or_else(|| engine.get("settings_path"));
    let path = match saved {
        Some(value) => PathBuf::from(value.as_str().context("settings_path must be a string")?),
        None => home.join(if engine.get("database_path").and_then(toml::Value::as_str).unwrap_or_default().contains("QwenWorkCN") { ".qwenworkcn" } else { ".qwenwork" }).join("settings.json"),
    };
    if !path.is_absolute() || path == config_path { bail!("Settings path must be absolute and distinct from bridge configuration"); }
    let Some(bytes) = read_optional(&path)? else { return Ok("No desktop settings file; nothing to remove".into()); };
    let _desktop_lock = configuration_lock(&path.with_file_name("bcs-bridge.toml"))?;
    if fs::canonicalize(&path)? == fs::canonicalize(config_path)? { bail!("Settings and bridge configuration must not refer to the same file"); }
    let mut settings: Value = serde_json::from_slice(&bytes).map_err(|_| anyhow::anyhow!("Invalid desktop settings JSON"))?;
    let url = format!("http://{listen}/hooks");
    let authorization = format!("Bearer {secret}");
    let count = settings.get("hooks").and_then(Value::as_object).into_iter().flat_map(|e| e.values())
        .filter_map(Value::as_array).flatten().filter_map(|e| e.get("hooks").and_then(Value::as_array))
        .flatten().filter(|h| owned_hook(h, &url, &authorization)).count();
    if count == 0 { return Ok("No matching bridge Hooks; nothing removed".into()); }
    remove_owned_hooks(&mut settings, &url, &authorization);
    let backup_path = backup(&path, &bytes)?;
    if fs::read(config_path)? != original || fs::read(&path)? != bytes { bail!("Configuration changed during deinit; retry"); }
    write_private(&path, &serde_json::to_vec_pretty(&settings)?)?;
    Ok(format!("Removed {count} owned Hooks. Backup: {}\nReload desktop Hooks. Disable the WeCom channel before stopping/uninstalling bridge. Bot registration and bridge configuration were retained.", backup_path.display()))
}

fn initialize_at(config_path: &Path, overrides: &toml::Table, home: &Path) -> Result<String> {
    for key in overrides.keys() {
        if !["database_path", "settings_path", "listen"].contains(&key.as_str()) {
            bail!("Unknown initializer option '{key}'");
        }
    }
    let _lock = configuration_lock(config_path)?;
    let original = fs::read(config_path).context("Register the qwenwork Bot before initializing")?;
    let text = std::str::from_utf8(&original).context("Bridge configuration must be UTF-8")?;
    let mut document: toml::Table = toml::from_str(text).map_err(|_| anyhow::anyhow!("Invalid bridge TOML"))?;
    let bot = unique_qwenwork_bot(&mut document)?;
    let mut engine = bot.get("engine_options").and_then(toml::Value::as_table).cloned().unwrap_or_default();
    if let Some(value) = overrides.get("database_path") { engine.insert("database_path".into(), value.clone()); }
    if !engine.contains_key("database_path") {
        let candidates = ["QwenWorkCN", "QwenWork"].map(|name| home.join("Library/Application Support").join(name).join("data/agents.db"));
        let existing: Vec<_> = candidates.iter().filter(|p| p.is_file()).collect();
        if existing.len() != 1 { bail!("Cannot uniquely identify QwenWork database; pass --option database_path=/absolute/path/agents.db"); }
        engine.insert("database_path".into(), existing[0].to_str().context("Database path must be UTF-8")?.into());
    }
    let settings_path = match overrides.get("settings_path").or_else(|| engine.get("settings_path")) {
        Some(value) => PathBuf::from(value.as_str().context("settings_path must be a string")?),
        None => home.join(if engine.get("database_path").and_then(toml::Value::as_str).unwrap_or_default().contains("QwenWorkCN") { ".qwenworkcn" } else { ".qwenwork" }).join("settings.json"),
    };
    if !settings_path.is_absolute() || settings_path == config_path { bail!("Settings path must be absolute and distinct from bridge configuration"); }
    let shared_path = settings_path.with_file_name("bcs-bridge.toml");
    if shared_path == config_path { bail!("Shared desktop settings must be distinct from profile configuration"); }
    if fs::symlink_metadata(&shared_path).is_ok_and(|m| m.file_type().is_symlink()) { bail!("Shared desktop configuration must not be a symlink"); }
    fs::create_dir_all(settings_path.parent().context("Settings require a parent directory")?)?;
    let _desktop_lock = configuration_lock(&shared_path)?;
    let old_shared = read_optional(&shared_path)?;
    if let Some(bytes) = &old_shared { engine.extend(parse_shared(bytes)?); }
    let mut previous_hook = engine.get("listen").and_then(toml::Value::as_str)
        .zip(engine.get("hook_secret").and_then(toml::Value::as_str))
        .map(|(listen, secret)| (format!("http://{listen}/hooks"), format!("Bearer {secret}")));
    for key in ["database_path", "listen"] {
        if let Some(value) = overrides.get(key) { engine.insert(key.into(), value.clone()); }
    }
    engine.entry("listen").or_insert("127.0.0.1:52491".into());
    engine.entry("bot_id").or_insert(format!("bridge-qwenwork-{}", Uuid::new_v4().simple()).into());
    let recover_hook = old_shared.is_none() && engine.get("hook_secret").and_then(toml::Value::as_str)
        .is_none_or(|s| s.is_empty() || s.starts_with('<'));
    for key in ["secret", "hook_secret"] {
        if engine.get(key).and_then(toml::Value::as_str).is_none_or(|s| s.is_empty() || s.starts_with('<')) {
            engine.insert(key.into(), format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple()).into());
        }
    }
    let old_settings = match fs::read(&settings_path) {
        Ok(bytes) => Some(bytes),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        Err(e) => return Err(e).context("Cannot read desktop settings"),
    };
    if old_settings.is_some() && fs::canonicalize(&settings_path)? == fs::canonicalize(config_path)? { bail!("Settings and bridge configuration must not refer to the same file"); }
    let mut settings: Value = match &old_settings {
        Some(bytes) => serde_json::from_slice(bytes).map_err(|_| anyhow::anyhow!("Invalid desktop settings JSON; no files changed"))?,
        None => json!({}),
    };
    let mut recovered = false;
    if recover_hook {
        let listen = engine.get("listen").and_then(toml::Value::as_str).context("Invalid listener")?;
        let url = format!("http://{listen}/hooks");
        if let Some(secret) = recover_hook_credential(&settings, &url)? {
            previous_hook = Some((url, format!("Bearer {secret}")));
            engine.insert("hook_secret".into(), secret.into());
            recovered = true;
        }
    }
    let parsed = super::Options::parse(&engine).map_err(|_| anyhow::anyhow!("Invalid qwenwork options; check the database path and loopback listener"))?;
    if parsed.secret == parsed.hook_secret { bail!("Channel and Hook credentials must be different"); }
    super::session::validate(&parsed.database_path).map_err(|_| anyhow::anyhow!("Unsupported QwenWork database schema"))?;
    let mut failure_hooks = Vec::new();
    if let Some((url, authorization)) = &previous_hook {
        if let Some(entries) = settings.pointer("/hooks/PostToolUseFailure").and_then(Value::as_array) {
            for entry in entries {
                let mut entry = entry.clone();
                if let Some(list) = entry.get_mut("hooks").and_then(Value::as_array_mut) {
                    list.retain(|hook| owned_hook(hook, url, authorization));
                    for hook in list.iter_mut() {
                        hook["url"] = format!("http://{}/hooks", parsed.listen).into();
                        hook["headers"]["Authorization"] = format!("Bearer {}", parsed.hook_secret).into();
                    }
                    if !list.is_empty() { failure_hooks.push(entry); }
                }
            }
        }
    }
    if let Some((url, authorization)) = previous_hook {
        remove_owned_hooks(&mut settings, &url, &authorization);
    }
    merge_hooks(&mut settings, &format!("http://{}/hooks", parsed.listen), &parsed.hook_secret, parsed.mcp_socket_path.is_some())?;
    if !failure_hooks.is_empty() {
        settings["hooks"].as_object_mut().context("Invalid hooks")?
            .entry("PostToolUseFailure").or_insert(json!([])).as_array_mut()
            .context("Invalid failure hooks")?.extend(failure_hooks);
    }
    engine.insert("settings_path".into(), settings_path.to_str().context("Settings path must be UTF-8")?.into());
    let shared_bytes = toml::to_string_pretty(&engine)?.into_bytes();
    bot.insert("engine_options".into(), engine.into());
    let config_bytes = toml::to_string_pretty(&document).context("Cannot serialize bridge configuration")?.into_bytes();
    let settings_bytes = serde_json::to_vec_pretty(&settings).context("Cannot serialize desktop settings")?;
    let config_backup = backup(config_path, &original)?;
    let settings_backup = old_settings.as_ref().map(|bytes| backup(&settings_path, bytes)).transpose()?;
    // Reject concurrent changes rather than silently overwriting another editor.
    if fs::read(config_path)? != original || read_optional(&settings_path)? != old_settings || read_optional(&shared_path)? != old_shared { bail!("Configuration changed during initialization; retry"); }
    write_private(config_path, &config_bytes)?;
    if let Err(error) = write_private(&settings_path, &settings_bytes) {
        write_private(config_path, &original).context("Desktop settings write failed and bridge rollback failed; recover from backup")?;
        return Err(error).context("Cannot save desktop Hooks; bridge configuration restored");
    }
    if let Err(error) = write_private(&shared_path, &shared_bytes) {
        write_private(config_path, &original).context("Shared settings write failed and profile rollback failed; recover from backup")?;
        if let Some(bytes) = &old_settings { write_private(&settings_path, bytes)?; }
        else { fs::remove_file(&settings_path)?; }
        return Err(error).context("Cannot save shared desktop settings; profile and Hooks restored");
    }
    let recovery_note = if recovered { "Recovered existing bridge Hooks. Previous channel credentials were unavailable; configure the channel once with the values below.\n" } else { "" };
    Ok(format!("QwenWork initialization complete.\n{recovery_note}Shared desktop config: {}\nBridge config: {}\nConfig backup: {}\nSettings: {}\nSettings backup: {}\n\nFill these values in QwenWork's WeCom Bot channel (keep Secret private):\nWebSocket URL: ws://{}/ws\nBot ID: {}\nSecret: {}\n\nReload desktop Hooks, start bridge with the selected --config, then enable the channel.\nCheck: curl http://{}/health\n", shared_path.display(), config_path.display(), config_backup.display(), settings_path.display(), settings_backup.map(|p| p.display().to_string()).unwrap_or_else(|| "new settings file".into()), parsed.listen, parsed.bot_id, parsed.secret, parsed.listen))
}

fn parse_shared(bytes: &[u8]) -> Result<toml::Table> {
    let table: toml::Table = toml::from_str(std::str::from_utf8(bytes).context("Shared desktop configuration must be UTF-8")?)
        .map_err(|_| anyhow::anyhow!("Invalid shared desktop configuration"))?;
    if table.keys().any(|key| !["listen", "bot_id", "secret", "hook_secret", "database_path", "settings_path", "mcp_socket_path"].contains(&key.as_str())) {
        bail!("Shared desktop configuration contains unsupported fields");
    }
    for key in ["listen", "bot_id", "secret", "hook_secret", "database_path", "settings_path"] {
        if table.get(key).and_then(toml::Value::as_str).is_none_or(|s| s.is_empty()) {
            bail!("Shared desktop configuration is incomplete; restore its backup");
        }
    }
    Ok(table)
}

/// Runtime profiles retain their BCN identity, but resolve desktop options from
/// the shared local configuration so an older profile cannot revive old secrets.
pub(super) fn desktop_options(options: &toml::Table) -> Result<toml::Table> {
    let mut result = options.clone();
    if let Some(path) = options.get("settings_path").and_then(toml::Value::as_str) {
        if let Some(bytes) = read_optional(&Path::new(path).with_file_name("bcs-bridge.toml"))? {
            result.extend(parse_shared(&bytes)?);
        }
    }
    Ok(result)
}

fn recover_hook_credential(settings: &Value, url: &str) -> Result<Option<String>> {
    let events = ["PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit", "QueryEnd", "PreCompact", "PostCompact"];
    let mut selected = serde_json::Map::new();
    let mut secret: Option<String> = None;
    for event in events {
        let mut matches = Vec::new();
        for entry in settings.get("hooks").and_then(|hooks| hooks.get(event)).and_then(Value::as_array).into_iter().flatten() {
            let mut entry = entry.clone();
            let Some(list) = entry.get_mut("hooks").and_then(Value::as_array_mut) else { continue; };
            list.retain(|h| h.get("type").and_then(Value::as_str) == Some("http") && h.get("url").and_then(Value::as_str) == Some(url));
            for hook in list.iter() {
                let value = hook.pointer("/headers/Authorization").and_then(Value::as_str)
                    .and_then(|s| s.strip_prefix("Bearer ")).context("Existing listener has unrecognized Hooks; no files changed")?;
                if value.len() != 64 || !value.bytes().all(|b| b.is_ascii_hexdigit()) || secret.as_ref().is_some_and(|s| s != value) {
                    bail!("Existing listener has conflicting Hook credentials; no files changed");
                }
                secret = Some(value.to_owned());
            }
            if !list.is_empty() { matches.push(entry); }
        }
        selected.insert(event.into(), matches.into());
    }
    let Some(secret) = secret else { return Ok(None); };
    for interaction in [false, true] {
        let mut expected = json!({});
        merge_hooks(&mut expected, url, &secret, interaction)?;
        if expected["hooks"] == Value::Object(selected.clone()) { return Ok(Some(secret)); }
    }
    bail!("Existing listener Hooks do not match a complete bridge setup; no files changed")
}

fn owned_hook(hook: &Value, url: &str, authorization: &str) -> bool {
    hook.get("type").and_then(Value::as_str) == Some("http")
        && hook.get("url").and_then(Value::as_str) == Some(url)
        && hook.pointer("/headers/Authorization").and_then(Value::as_str) == Some(authorization)
}

fn remove_owned_hooks(settings: &mut Value, url: &str, authorization: &str) {
    let Some(events) = settings.get_mut("hooks").and_then(Value::as_object_mut) else { return; };
    for entries in events.values_mut().filter_map(Value::as_array_mut) {
        for entry in entries.iter_mut() {
            if let Some(list) = entry.get_mut("hooks").and_then(Value::as_array_mut) {
                list.retain(|hook| !owned_hook(hook, url, authorization));
            }
        }
        entries.retain(|entry| entry.get("hooks").and_then(Value::as_array).is_none_or(|hooks| !hooks.is_empty()));
    }
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
    let resolved;
    let path = if fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_symlink()) {
        resolved = fs::canonicalize(path).context("Cannot resolve configuration symlink")?;
        resolved.as_path()
    } else { path };
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

/// Keep the single-QwenWork initialization contract across V1 and V2 layouts.
fn unique_qwenwork_bot(document: &mut toml::Table) -> Result<&mut toml::Table> {
    let groups: Vec<&mut toml::Table> = if document.contains_key("provider") || document.contains_key("schema_version") {
        if document.get("schema_version").and_then(toml::Value::as_integer) != Some(2)
            || document.keys().any(|key| !["schema_version", "provider"].contains(&key.as_str())) {
            bail!("Invalid V2 bridge configuration");
        }
        document.get_mut("provider").and_then(toml::Value::as_array_mut).context("Missing Providers")?
            .iter_mut().map(|group| group.as_table_mut().context("Invalid Provider")).collect::<Result<_>>()?
    } else { vec![document] };
    let mut candidates = Vec::new();
    for group in groups {
        for bot in group.get_mut("bot").and_then(toml::Value::as_array_mut).context("No registered Bots")? {
            let bot = bot.as_table_mut().context("Invalid Bot")?;
            if bot.get("engine").and_then(toml::Value::as_str) == Some("qwenwork") {
                candidates.push(bot);
            }
        }
    }
    if candidates.len() != 1 { bail!("Initialization/deinit requires exactly one qwenwork Bot in the selected configuration"); }
    Ok(candidates.remove(0))
}
