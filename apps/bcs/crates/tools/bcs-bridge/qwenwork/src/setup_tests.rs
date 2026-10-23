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
    assert!(message.contains("Check: curl http://127.0.0.1:52491/health"));
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
fn profiles_share_desktop_identity_but_keep_bcn_credentials_and_state_separate() {
    let (root, local, settings) = fixture();
    let pre = root.path().join("pre.toml");
    let source = fs::read_to_string(&local).unwrap();
    fs::write(&pre, format!("state_path='pre.sqlite3'\n{}", source.replace("keep-token", "pre-token").replace("bot-registered", "pre-bot"))).unwrap();
    initialize_at(&local, &toml::Table::new(), root.path()).unwrap();
    let original_hooks = fs::read(&settings).unwrap();
    initialize_at(&pre, &toml::Table::new(), root.path()).unwrap();
    let local_config: toml::Table = toml::from_str(&fs::read_to_string(&local).unwrap()).unwrap();
    let pre_config: toml::Table = toml::from_str(&fs::read_to_string(&pre).unwrap()).unwrap();
    assert_eq!(local_config["bot"][0]["engine_options"], pre_config["bot"][0]["engine_options"]);
    assert_eq!(local_config["bot"][0]["token"].as_str(), Some("keep-token"));
    assert_eq!(pre_config["bot"][0]["token"].as_str(), Some("pre-token"));
    assert_eq!(pre_config["state_path"].as_str(), Some("pre.sqlite3"));
    assert_eq!(fs::read(&settings).unwrap(), original_hooks);
    let shared: toml::Table = toml::from_str(&fs::read_to_string(settings.with_file_name("bcs-bridge.toml")).unwrap()).unwrap();
    assert!(!shared.contains_key("token"));
    assert!(!shared.contains_key("provider_id"));
    assert!(!shared.contains_key("state_path"));
    let mut stale = shared.clone();
    stale.insert("secret".into(), "stale-profile-secret".into());
    assert_eq!(desktop_options(&stale).unwrap(), shared);
}

#[test]
fn clearing_profile_configuration_preserves_shared_channel_credentials() {
    let (root, config, settings) = fixture();
    let uninitialized = fs::read(&config).unwrap();
    initialize_at(&config, &toml::Table::new(), root.path()).unwrap();
    let first = fs::read(&config).unwrap();
    let hooks = fs::read(&settings).unwrap();
    fs::remove_file(&config).unwrap();
    fs::write(&config, uninitialized).unwrap();
    initialize_at(&config, &toml::Table::new(), root.path()).unwrap();
    assert_eq!(fs::read(&config).unwrap(), first);
    assert_eq!(fs::read(&settings).unwrap(), hooks);
    #[cfg(unix)] {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(fs::metadata(settings.with_file_name("bcs-bridge.toml")).unwrap().permissions().mode() & 0o777, 0o600);
    }
}

#[test]
fn old_complete_hooks_recover_hook_credential_without_overwriting_foreign_hooks() {
    let (root, config, settings) = fixture();
    let uninitialized = fs::read(&config).unwrap();
    initialize_at(&config, &toml::Table::new(), root.path()).unwrap();
    let old: toml::Table = toml::from_str(&fs::read_to_string(&config).unwrap()).unwrap();
    let hooks = fs::read(&settings).unwrap();
    fs::remove_file(settings.with_file_name("bcs-bridge.toml")).unwrap();
    fs::write(&config, uninitialized).unwrap();
    let message = initialize_at(&config, &toml::Table::new(), root.path()).unwrap();
    assert!(message.contains("configure the channel once"));
    let new: toml::Table = toml::from_str(&fs::read_to_string(&config).unwrap()).unwrap();
    assert_eq!(old["bot"][0]["engine_options"]["hook_secret"], new["bot"][0]["engine_options"]["hook_secret"]);
    assert_ne!(old["bot"][0]["engine_options"]["secret"], new["bot"][0]["engine_options"]["secret"]);
    assert_eq!(fs::read(&settings).unwrap(), hooks);
}

#[test]
fn incomplete_legacy_hooks_are_not_adopted_and_shared_credentials_are_not_created() {
    let (root, config, settings) = fixture();
    let before = fs::read(&config).unwrap();
    let mut hooks = json!({});
    merge_hooks(&mut hooks, "http://127.0.0.1:52491/hooks", &"a".repeat(64), false).unwrap();
    hooks["hooks"].as_object_mut().unwrap().remove("Stop");
    let bytes = serde_json::to_vec(&hooks).unwrap();
    fs::write(&settings, &bytes).unwrap();
    assert!(initialize_at(&config, &toml::Table::new(), root.path()).is_err());
    assert_eq!(fs::read(&config).unwrap(), before);
    assert_eq!(fs::read(&settings).unwrap(), bytes);
    assert!(!settings.with_file_name("bcs-bridge.toml").exists());
}

#[test]
fn shared_desktop_lock_blocks_other_profile_initialization_without_changes() {
    let (root, config, settings) = fixture();
    let before_config = fs::read(&config).unwrap();
    let before_settings = fs::read(&settings).unwrap();
    let shared = settings.with_file_name("bcs-bridge.toml");
    let _lock = configuration_lock(&shared).unwrap();
    assert!(initialize_at(&config, &toml::Table::new(), root.path()).is_err());
    assert_eq!(fs::read(&config).unwrap(), before_config);
    assert_eq!(fs::read(&settings).unwrap(), before_settings);
    assert!(!shared.exists());
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
    assert!(message.contains("Check: curl http://127.0.0.1:52495/health"));
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

#[test]
fn optional_failure_hooks_survive_repeat_and_migrate_with_matcher_and_timeout() {
    let (root, config, settings) = fixture();
    initialize_at(&config, &toml::Table::new(), root.path()).unwrap();
    let cfg: toml::Table = toml::from_str(&fs::read_to_string(&config).unwrap()).unwrap();
    let auth = format!("Bearer {}", cfg["bot"][0]["engine_options"]["hook_secret"].as_str().unwrap());
    let mut value: Value = serde_json::from_slice(&fs::read(&settings).unwrap()).unwrap();
    assert!(value["hooks"].get("PostToolUseFailure").is_none());
    value["hooks"]["PostToolUseFailure"] = json!([{"matcher":"Read", "hooks":[{
        "type":"http", "url":"http://127.0.0.1:52491/hooks", "headers":{"Authorization":auth}, "timeout":7
    }]}]);
    fs::write(&settings, serde_json::to_vec(&value).unwrap()).unwrap();
    for listen in ["127.0.0.1:52491", "127.0.0.1:52497"] {
        let options = toml::Table::from_iter([("listen".into(), listen.into())]);
        initialize_at(&config, &options, root.path()).unwrap();
        let migrated: Value = serde_json::from_slice(&fs::read(&settings).unwrap()).unwrap();
        let failure = &migrated["hooks"]["PostToolUseFailure"];
        assert_eq!(failure.as_array().unwrap().len(), 1);
        assert_eq!(failure[0]["matcher"], "Read");
        assert_eq!(failure[0]["hooks"][0]["url"], format!("http://{listen}/hooks"));
        assert_eq!(failure[0]["hooks"][0]["timeout"], 7);
    }
}

#[cfg(unix)]
#[test]
fn deinit_preserves_symlink_and_foreign_hooks_without_database_access() {
    let (root, config, settings) = fixture();
    let target = root.path().join("real-settings.json");
    fs::rename(&settings, &target).unwrap();
    std::os::unix::fs::symlink(&target, &settings).unwrap();
    initialize_at(&config, &toml::Table::new(), root.path()).unwrap();
    let original_config = fs::read(&config).unwrap();
    let mut value: Value = serde_json::from_slice(&fs::read(&settings).unwrap()).unwrap();
    value["hooks"]["PostToolUseFailure"] = json!([{"hooks":[{"type":"http", "url":"http://127.0.0.1:52491/hooks", "headers":{"Authorization":"Bearer foreign"}}]}]);
    fs::write(&target, serde_json::to_vec(&value).unwrap()).unwrap();
    fs::remove_file(root.path().join("Library/Application Support/QwenWorkCN/data/agents.db")).unwrap();
    let message = deinitialize_at(&config, &toml::Table::new(), root.path()).unwrap();
    assert!(message.contains("Removed 7 owned Hooks"));
    assert!(fs::symlink_metadata(&settings).unwrap().file_type().is_symlink());
    assert_eq!(fs::read(&config).unwrap(), original_config);
    let after: Value = serde_json::from_slice(&fs::read(&target).unwrap()).unwrap();
    assert_eq!(after["theme"], "dark");
    assert_eq!(after["hooks"]["PostToolUseFailure"], value["hooks"]["PostToolUseFailure"]);
    assert!(after.to_string().contains("keep-me"));
    assert!(deinitialize_at(&config, &toml::Table::new(), root.path()).unwrap().contains("nothing removed"));
}

#[cfg(unix)]
#[test]
fn init_rejects_settings_symlink_to_registered_config_without_writes() {
    let (root, config, settings) = fixture();
    let original = fs::read(&config).unwrap();
    fs::remove_file(&settings).unwrap();
    std::os::unix::fs::symlink(&config, &settings).unwrap();
    assert!(initialize_at(&config, &toml::Table::new(), root.path()).unwrap_err().to_string().contains("same file"));
    assert_eq!(fs::read(&config).unwrap(), original);
    assert!(fs::symlink_metadata(&settings).unwrap().file_type().is_symlink());
}

#[test]
fn v2_init_and_deinit_find_the_unique_bot_and_preserve_other_provider_identity() {
    let (root, config, settings) = fixture();
    let source: toml::Table = toml::from_str(&fs::read_to_string(&config).unwrap()).unwrap();
    let other: toml::Table = toml::from_str("provider_id='legacy'\nstate_path='legacy.db'\nprovider_auth='agentpass'\n[[bot]]\nengine='codex'\nprovider_bot_ref='parent'\nbot_id='old-id'\ntoken='old-token'\ncwd='/tmp'").unwrap();
    let doc = toml::Table::from_iter([("schema_version".into(),2.into()),("provider".into(),toml::Value::Array(vec![other.clone().into(),source.into()]))]);
    fs::write(&config,toml::to_string(&doc).unwrap()).unwrap();
    initialize_at(&config,&toml::Table::new(),root.path()).unwrap();
    let updated:toml::Table=toml::from_str(&fs::read_to_string(&config).unwrap()).unwrap();
    assert_eq!(updated["provider"][0],toml::Value::Table(other));
    assert_eq!(updated["provider"][1]["bot"][0]["token"].as_str(),Some("keep-token"));
    let bytes=fs::read(&config).unwrap();
    deinitialize_at(&config,&toml::Table::new(),root.path()).unwrap();
    assert_eq!(fs::read(&config).unwrap(),bytes);
    assert!(!fs::read_to_string(&settings).unwrap().contains("/hooks"));
}

#[test]
fn init_and_deinit_require_one_qwenwork_bot_without_writes() {
    for layout in ["legacy", "v2-one-provider", "v2-two-providers"] {
        for count in [0, 2] {
            let (root, config, settings) = fixture();
            initialize_at(&config, &toml::Table::new(), root.path()).unwrap();
            let mut source: toml::Value = toml::from_str(&fs::read_to_string(&config).unwrap()).unwrap();
            if count == 0 { source["bot"][0]["engine"] = "codex".into(); }
            let mut second = source["bot"][0].clone();
            second["bot_id"] = "another-bot".into();
            second["provider_bot_ref"] = "qwen-2".into();
            second["engine_options"]["listen"] = "127.0.0.1:52492".into();
            if count == 2 && layout != "v2-two-providers" {
                source["bot"].as_array_mut().unwrap().push(second.clone());
            }
            let document = if layout == "legacy" { source } else {
                let mut providers = vec![source.clone()];
                if layout == "v2-two-providers" {
                    source["provider_id"] = "another-provider".into();
                    source["bot"] = toml::Value::Array(vec![second]);
                    providers.push(source);
                }
                toml::Value::Table(toml::Table::from_iter([
                    ("schema_version".into(), 2.into()), ("provider".into(), providers.into()),
                ]))
            };
            fs::write(&config, toml::to_string(&document).unwrap()).unwrap();
            let original = fs::read(&config).unwrap();
            let hooks = fs::read(&settings).unwrap();
            let shared_path = settings.with_file_name("bcs-bridge.toml");
            let shared = fs::read(&shared_path).unwrap();
            for cleanup in [false, true] {
                let result = if cleanup { deinitialize_at(&config, &toml::Table::new(), root.path()) }
                    else { initialize_at(&config, &toml::Table::new(), root.path()) };
                assert!(result.unwrap_err().to_string().contains("exactly one qwenwork Bot"), "{layout}, {count}, {cleanup}");
                assert_eq!(fs::read(&config).unwrap(), original);
                assert_eq!(fs::read(&settings).unwrap(), hooks);
                assert_eq!(fs::read(&shared_path).unwrap(), shared);
            }
        }
    }
}

#[test]
fn init_and_deinit_reject_bot_id_options_without_writes() {
    let (root, config, settings) = fixture();
    initialize_at(&config, &toml::Table::new(), root.path()).unwrap();
    let original = fs::read(&config).unwrap();
    let hooks = fs::read(&settings).unwrap();
    let shared_path = settings.with_file_name("bcs-bridge.toml");
    let shared = fs::read(&shared_path).unwrap();
    let options = toml::Table::from_iter([("bot_id".into(), "bot-registered".into())]);
    assert!(initialize_at(&config, &options, root.path()).unwrap_err().to_string().contains("Unknown initializer option"));
    assert!(deinitialize_at(&config, &options, root.path()).unwrap_err().to_string().contains("Deinit accepts only"));
    assert_eq!(fs::read(&config).unwrap(), original);
    assert_eq!(fs::read(&settings).unwrap(), hooks);
    assert_eq!(fs::read(&shared_path).unwrap(), shared);
}
