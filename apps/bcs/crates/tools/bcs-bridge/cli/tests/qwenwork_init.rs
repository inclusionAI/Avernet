use std::fs;
use std::process::Command;

#[test]
fn init_is_wired_into_the_public_binary_and_does_not_construct_a_listener() {
    let root = tempfile::tempdir().unwrap();
    let config = root.path().join("qwenwork.toml");
    let settings = root.path().join("settings.json");
    let database = root.path().join("agents.db");
    let db = rusqlite::Connection::open(&database).unwrap();
    db.execute_batch("CREATE TABLE chats(id TEXT, ext TEXT, deleted_at INTEGER); CREATE TABLE sub_chats(session_id TEXT, chat_id TEXT);").unwrap();
    fs::write(&config, "provider_id='test'\nmode='plugin'\n[plugin]\nurl='ws://127.0.0.1/ws/bot'\n[[bot]]\nengine='qwenwork'\nprovider_bot_ref='qwen'\nbot_id='test-bot'\ncwd='/tmp'\n").unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_bcs-bridge")).env("HOME", root.path())
        .arg("--config").arg(&config).args(["init", "--engine", "qwenwork", "--option"])
        .arg(format!("database_path={}", database.display())).arg("--option")
        .arg(format!("settings_path={}", settings.display())).output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("WebSocket URL: ws://127.0.0.1:52491/ws"));
    assert!(stdout.contains("Bot ID: bridge-qwenwork-"));
    assert!(settings.exists());
    let parsed: toml::Value = toml::from_str(&fs::read_to_string(&config).unwrap()).unwrap();
    assert_eq!(parsed["bot"][0]["engine_options"]["database_path"].as_str(), database.to_str());
    let original = fs::read(&config).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_bcs-bridge")).env("HOME", root.path()).arg("--config").arg(&config)
        .args(["deinit", "--engine", "qwenwork"]).output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    assert!(String::from_utf8_lossy(&output.stdout).contains("Removed 7 owned Hooks"));
    assert_eq!(fs::read(&config).unwrap(), original);
}

#[test]
fn deinit_help_is_wired_without_loading_configuration() {
    let output = Command::new(env!("CARGO_BIN_EXE_bcs-bridge")).args(["deinit", "--help"]).output().unwrap();
    assert!(output.status.success());
    assert!(String::from_utf8_lossy(&output.stdout).contains("--engine"));
}
