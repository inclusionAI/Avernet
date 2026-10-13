use super::*;

fn fixture() -> (tempfile::TempDir, PathBuf, PathBuf) {
    let root = tempfile::tempdir().unwrap();
    let database = root.path().join("Library/Application Support/QwenWorkCN/data/agents.db");
    fs::create_dir_all(database.parent().unwrap()).unwrap();
    let db = rusqlite::Connection::open(&database).unwrap();
    db.execute_batch("CREATE TABLE chats(id TEXT, ext TEXT, deleted_at INTEGER); CREATE TABLE sub_chats(session_id TEXT, chat_id TEXT);").unwrap();
    let config = root.path().join("bridge.toml");
    fs::write(&config, "provider_id='local'\nmode='plugin'\n[plugin]\nurl='ws://127.0.0.1:21001/ws/bot'\n[[bot]]\nengine='qwenwork'\nprovider_bot_ref='qwen-1'\nbot_id='bot-registered'\ntoken='keep-token'\ncwd='/tmp'\n").unwrap();
    let settings = root.path().join(".qwenworkcn/settings.json");
    fs::create_dir_all(settings.parent().unwrap()).unwrap();
    fs::write(&settings, r#"{"theme":"dark","hooks":{"PreToolUse":[{"matcher":"Read","hooks":[{"type":"command","command":"keep-me"}]}]}}"#).unwrap();
    (root, config, settings)
}

#[test]
fn detects_database_preserves_other_hooks_and_reuses_credentials_on_repeat() {
    let (root, config, settings) = fixture();
    let message = initialize_at(&config, &toml::Table::new(), root.path()).unwrap();
    assert!(message.contains("WebSocket URL: ws://127.0.0.1:52491/ws"));
    let first: toml::Table = toml::from_str(&fs::read_to_string(&config).unwrap()).unwrap();
    let engine = &first["bot"][0]["engine_options"];
    assert_ne!(engine["secret"], engine["hook_secret"]);
    assert!(message.contains(engine["secret"].as_str().unwrap()));
    assert!(!message.contains(engine["hook_secret"].as_str().unwrap()));
    assert_eq!(first["bot"][0]["token"].as_str(), Some("keep-token"));
    let hooks: Value = serde_json::from_slice(&fs::read(&settings).unwrap()).unwrap();
    assert_eq!(hooks["theme"], "dark");
    assert_eq!(hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "keep-me");
    for event in ["PreToolUse", "PostToolUse", "QueryEnd", "UserPromptSubmit", "PreCompact", "PostCompact"] {
        assert!(!hooks["hooks"][event].as_array().unwrap().is_empty());
    }
    initialize_at(&config, &toml::Table::new(), root.path()).unwrap();
    let second: toml::Table = toml::from_str(&fs::read_to_string(&config).unwrap()).unwrap();
    assert_eq!(first, second);
    let repeated: Value = serde_json::from_slice(&fs::read(&settings).unwrap()).unwrap();
    assert_eq!(hooks, repeated);
    #[cfg(unix)] {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(fs::metadata(&config).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(fs::metadata(&settings).unwrap().permissions().mode() & 0o777, 0o600);
    }
}

#[test]
fn conflicting_hook_credential_or_invalid_json_changes_neither_file() {
    let (root, config, settings) = fixture();
    let original = fs::read(&config).unwrap();
    let foreign = json!({"hooks":{"PreToolUse":[{"hooks":[{"type":"http","url":"http://127.0.0.1:52491/hooks","headers":{"Authorization":"Bearer other"}}]}]}});
    fs::write(&settings, serde_json::to_vec(&foreign).unwrap()).unwrap();
    let old = fs::read(&settings).unwrap();
    assert!(initialize_at(&config, &toml::Table::new(), root.path()).is_err());
    assert_eq!(fs::read(&config).unwrap(), original);
    assert_eq!(fs::read(&settings).unwrap(), old);
    fs::write(&settings, b"invalid json").unwrap();
    assert!(initialize_at(&config, &toml::Table::new(), root.path()).is_err());
    assert_eq!(fs::read(&config).unwrap(), original);
    assert_eq!(fs::read(&settings).unwrap(), b"invalid json");
}

#[test]
fn ambiguity_requires_explicit_database_and_unknown_options_fail() {
    let (root, config, settings) = fixture();
    let cn = root.path().join("Library/Application Support/QwenWorkCN/data/agents.db");
    let global = root.path().join("Library/Application Support/QwenWork/data/agents.db");
    fs::create_dir_all(global.parent().unwrap()).unwrap(); fs::copy(&cn, &global).unwrap();
    assert!(initialize_at(&config, &toml::Table::new(), root.path()).is_err());
    let options = toml::Table::from_iter([("database_path".into(), cn.to_str().unwrap().into()), ("settings_path".into(), settings.to_str().unwrap().into())]);
    initialize_at(&config, &options, root.path()).unwrap();
    let unknown = toml::Table::from_iter([("unkown".into(), "x".into())]);
    assert!(initialize_at(&config, &unknown, root.path()).is_err());
}

#[test]
fn interaction_hooks_use_the_long_question_timeout() {
    let mut settings = json!({});
    merge_hooks(&mut settings, "http://127.0.0.1:52491/hooks", "test", true).unwrap();
    assert_eq!(settings["hooks"]["PreToolUse"][1]["matcher"], "AskUserQuestion");
    assert_eq!(settings["hooks"]["PreToolUse"][1]["hooks"][0]["timeout"], 300);
}

#[test]
fn listener_change_removes_only_previously_owned_hooks_and_is_idempotent() {
    let (root, config, settings) = fixture();
    initialize_at(&config, &toml::Table::new(), root.path()).unwrap();
    let before: toml::Table = toml::from_str(&fs::read_to_string(&config).unwrap()).unwrap();
    let old_engine = &before["bot"][0]["engine_options"];
    let mut hooks: Value = serde_json::from_slice(&fs::read(&settings).unwrap()).unwrap();
    hooks["hooks"]["PreToolUse"].as_array_mut().unwrap().push(json!({"matcher":"Read", "hooks":[{
        "type":"http", "url":"http://127.0.0.1:52491/hooks", "headers":{"Authorization":"Bearer foreign"}
    }]}));
    fs::write(&settings, serde_json::to_vec(&hooks).unwrap()).unwrap();
    let options = toml::Table::from_iter([("listen".into(), "127.0.0.1:52495".into())]);
    let message = initialize_at(&config, &options, root.path()).unwrap();
    assert!(message.contains("WebSocket URL: ws://127.0.0.1:52495/ws"));
    let after: toml::Table = toml::from_str(&fs::read_to_string(&config).unwrap()).unwrap();
    assert_eq!(before["bot"][0]["token"], after["bot"][0]["token"]);
    assert_eq!(old_engine["hook_secret"], after["bot"][0]["engine_options"]["hook_secret"]);
    let updated: Value = serde_json::from_slice(&fs::read(&settings).unwrap()).unwrap();
    for entries in updated["hooks"].as_object().unwrap().values() {
        let all: Vec<_> = entries.as_array().unwrap().iter().flat_map(|e| e["hooks"].as_array().unwrap()).collect();
        assert_eq!(all.iter().filter(|h| h["url"] == "http://127.0.0.1:52495/hooks").count(), 1);
        for hook in all.iter().filter(|h| h["url"] == "http://127.0.0.1:52491/hooks") {
            assert_eq!(hook["headers"]["Authorization"], "Bearer foreign");
        }
    }
    assert!(updated.to_string().contains("keep-me"));
    initialize_at(&config, &options, root.path()).unwrap();
    let repeated: Value = serde_json::from_slice(&fs::read(&settings).unwrap()).unwrap();
    assert_eq!(updated, repeated);
}

#[test]
fn listener_migration_conflict_preserves_both_original_files() {
    let (root, config, settings) = fixture();
    initialize_at(&config, &toml::Table::new(), root.path()).unwrap();
    let mut hooks: Value = serde_json::from_slice(&fs::read(&settings).unwrap()).unwrap();
    hooks["hooks"]["QueryEnd"].as_array_mut().unwrap().push(json!({"hooks":[{
        "type":"http", "url":"http://127.0.0.1:52495/hooks", "headers":{"Authorization":"Bearer foreign"}
    }]}));
    fs::write(&settings, serde_json::to_vec(&hooks).unwrap()).unwrap();
    let original_config = fs::read(&config).unwrap();
    let original_settings = fs::read(&settings).unwrap();
    let options = toml::Table::from_iter([("listen".into(), "127.0.0.1:52495".into())]);
    assert!(initialize_at(&config, &options, root.path()).is_err());
    assert_eq!(fs::read(&config).unwrap(), original_config);
    assert_eq!(fs::read(&settings).unwrap(), original_settings);
}
