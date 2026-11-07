use std::{net::TcpListener, path::Path, process::Stdio, time::Duration};

use reqwest::Client;
use serde_json::json;
use tokio::{process::Command, time::{Instant, sleep, timeout}};

fn command(home: &Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_bcs-bridge"));
    command.kill_on_drop(true).env("HOME", home).env_remove("BRIDGE_CONFIG")
        .env("BCS_BRIDGE_PROVIDER_TOKENS", json!({"cli-provider":"test-webhook-secret", "other-provider":"other-webhook-secret"}).to_string());
    command
}

#[tokio::test]
async fn help_describes_start_without_requiring_config() {
    let home = tempfile::tempdir().unwrap();
    let output = command(home.path()).arg("--help").output().await.unwrap();
    assert!(output.status.success());
    assert!(String::from_utf8_lossy(&output.stdout).contains("start"));
    assert!(!home.path().join(".bcn-bridge").exists());
}

#[tokio::test]
async fn missing_config_explains_which_file_is_required() {
    let home = tempfile::tempdir().unwrap();
    let output = command(home.path()).arg("start").output().await.unwrap();
    assert!(!output.status.success());
    let error = String::from_utf8_lossy(&output.stderr);
    assert!(error.contains("--config"), "{error}");
    assert!(error.contains("bridge.toml"), "{error}");
    assert!(error.contains("onboarding"), "{error}");
    assert!(!home.path().join(".bcn-bridge").exists());
}

fn write_profile(home: &Path, name: &str) -> std::path::PathBuf {
    let directory = home.join(".bcn-bridge/profiles").join(name);
    std::fs::create_dir_all(&directory).unwrap();
    let path = directory.join("bridge.toml");
    std::fs::write(&path, r#"
provider_id = "plugin-provider"
mode = "plugin"
state_path = "profile.sqlite3"
bot = []
[plugin]
url = "ws://127.0.0.1:21000/ws/bot"
"#).unwrap();
    path
}

#[tokio::test]
async fn implicit_start_discovers_the_only_profile_and_resolves_its_state_path() {
    let home = tempfile::tempdir().unwrap();
    let config = write_profile(home.path(), "codex-prod");
    std::fs::create_dir_all(home.path().join(".bcn-bridge/profiles/empty")).unwrap();
    let output = timeout(Duration::from_secs(5), command(home.path()).arg("start").output()).await.unwrap().unwrap();
    let error = String::from_utf8_lossy(&output.stderr);
    assert!(output.status.success(), "{error}");
    assert!(error.contains("Using bridge profile: codex-prod"), "{error}");
    assert!(config.parent().unwrap().join("profile.sqlite3").exists());
    assert!(!home.path().join(".bcn-bridge/bridge.toml").exists());
}

#[tokio::test]
async fn ambiguous_profiles_list_sorted_start_commands_without_starting_a_daemon() {
    let home = tempfile::tempdir().unwrap();
    write_profile(home.path(), "codex-prod");
    write_profile(home.path(), "codex-pre");
    let output = command(home.path()).args(["start", "--daemon"]).output().await.unwrap();
    assert!(!output.status.success());
    let error = String::from_utf8_lossy(&output.stderr);
    let pre = error.find("bcs-bridge --profile codex-pre start --daemon").expect("pre choice");
    let prod = error.find("bcs-bridge --profile codex-prod start --daemon").expect("prod choice");
    assert!(pre < prod, "{error}");
    assert!(!home.path().join(".bcn-bridge/bridge.log").exists());
}

#[tokio::test]
async fn default_and_explicit_selectors_never_fall_back_to_discovered_profiles() {
    for selector in ["default", "profile", "config", "environment"] {
        let home = tempfile::tempdir().unwrap();
        write_profile(home.path(), "codex-prod");
        let mut cmd = command(home.path());
        cmd.arg("start");
        match selector {
            "default" => std::fs::write(home.path().join(".bcn-bridge/bridge.toml"), "invalid TOML").unwrap(),
            "profile" => { cmd.args(["--profile", "missing"]); },
            "config" => { cmd.arg("--config").arg(home.path().join("missing.toml")); },
            "environment" => { cmd.env("BRIDGE_CONFIG", home.path().join("missing.toml")); },
            _ => unreachable!(),
        }
        let output = cmd.output().await.unwrap();
        assert!(!output.status.success(), "{selector}");
        let error = String::from_utf8_lossy(&output.stderr);
        assert!(!error.contains("Using bridge profile"), "{selector}: {error}");
        assert!(!home.path().join(".bcn-bridge/profiles/codex-prod/profile.sqlite3").exists());
    }
}

#[cfg(unix)]
#[tokio::test]
async fn start_uses_home_config_or_explicit_override_and_shuts_down() {
    for explicit in [false, true] {
        let home = tempfile::tempdir().unwrap();
        let socket = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = socket.local_addr().unwrap();
        let config = if explicit {
            home.path().join("custom.toml")
        } else {
            let dir = home.path().join(".bcn-bridge");
            std::fs::create_dir(&dir).unwrap();
            dir.join("bridge.toml")
        };
        let legacy = if explicit { "bcs_to_provider_token = 'legacy-private-webhook-secret'" } else { "" };
        std::fs::write(&config, format!(r#"
provider_id = "cli-provider"
mode = "gateway"
listen = "{address}"
state_path = "state.sqlite3"
{legacy}
[[bot]]
provider_bot_ref = "agent-001"
engine = "claude-code"
cwd = {}
"#, json!(home.path()))).unwrap();
        let mut cmd = command(home.path());
        cmd.arg("start");
        if explicit {
            cmd.env("BRIDGE_CONFIG", home.path().join("must-not-load.toml"));
            cmd.arg("--config").arg(&config);
        }
        drop(socket);
        let mut child = cmd.stdout(Stdio::null()).stderr(Stdio::null())
            .kill_on_drop(true).spawn().unwrap();
        let client = Client::builder().no_proxy().timeout(Duration::from_millis(250)).build().unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            assert!(child.try_wait().unwrap().is_none(), "CLI exited before listening");
            if let Ok(response) = client.post(format!("http://{address}/webhook"))
                .bearer_auth("test-webhook-secret")
                .json(&json!({"type":"req","id":"ping","method":"bot.ping",
                    "to_bot":{"provider_id":"cli-provider","provider_bot_ref":"agent-001"}}))
                .send().await {
                assert!(response.status().is_success());
                assert_eq!(response.json::<serde_json::Value>().await.unwrap()["ok"], true);
                break;
            }
            assert!(Instant::now() < deadline, "CLI did not bind its configured address");
            sleep(Duration::from_millis(20)).await;
        }
        assert!(config.parent().unwrap().join("state.sqlite3").exists());
        for (provider, token) in [("cli-provider", "legacy-private-webhook-secret"), ("cli-provider", "other-webhook-secret"),
            ("other-provider", "other-webhook-secret"), ("other-provider", "test-webhook-secret")] {
            let response = client.post(format!("http://{address}/webhook")).bearer_auth(token)
                .json(&json!({"type":"req","id":"unauthorized-ping","method":"bot.ping",
                    "to_bot":{"provider_id":provider,"provider_bot_ref":"agent-001"}}))
                .send().await.unwrap();
            assert!(response.status().is_client_error(), "Provider/token mismatch was accepted");
        }
        let status = Command::new("kill").arg("-TERM").arg(child.id().unwrap().to_string())
            .status().await.unwrap();
        assert!(status.success());
        assert!(timeout(Duration::from_secs(5), child.wait()).await.unwrap().unwrap().success());
    }
}

#[tokio::test]
async fn configured_legacy_token_cannot_bypass_an_absent_or_invalid_provider_registry() {
    for registry in ["{}", "invalid", r#"{"other-provider":"other-token"}"#, r#"{"cli-provider":" "}"#] {
        let home = tempfile::tempdir().unwrap();
        let config = home.path().join("bridge.toml");
        std::fs::write(&config, r#"
provider_id = "cli-provider"
listen = "127.0.0.1:0"
bcs_to_provider_token = "legacy-private-webhook-secret"
state_path = "must-not-create.sqlite3"
bot = []
"#).unwrap();
        let output = timeout(Duration::from_secs(3), command(home.path()).env("BCS_BRIDGE_PROVIDER_TOKENS", registry)
            .arg("start").arg("--config").arg(&config).output()).await
            .expect("missing registry must fail before the server starts").unwrap();
        assert!(!output.status.success());
        let error = String::from_utf8_lossy(&output.stderr);
        assert!(error.contains("BCS_BRIDGE_PROVIDER_TOKENS"), "{error}");
        assert!(!error.contains("legacy-private-webhook-secret"));
        assert!(!home.path().join("must-not-create.sqlite3").exists());
    }
}

#[tokio::test]
async fn plugin_start_does_not_require_a_valid_gateway_credential_registry() {
    let home = tempfile::tempdir().unwrap();
    let config = home.path().join("plugin.toml");
    std::fs::write(&config, r#"
provider_id = "plugin-provider"
mode = "plugin"
state_path = "plugin.sqlite3"
bot = []
[plugin]
url = "ws://127.0.0.1:21000/ws/bot"
"#).unwrap();
    // No bots: service initializes its state and exits without a remote connection.
    let output = timeout(Duration::from_secs(3), command(home.path())
        .env("BCS_BRIDGE_PROVIDER_TOKENS", "deliberately-invalid-json")
        .arg("start").arg("--config").arg(&config).output()).await.unwrap().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    assert!(home.path().join("plugin.sqlite3").exists());
}

#[cfg(unix)]
#[tokio::test]
async fn codex_cwd_override_reaches_daemon_without_rewriting_default_or_bot_identity() {
    use std::os::unix::fs::PermissionsExt;
    let home = tempfile::tempdir().unwrap();
    let a = home.path().join("project-a");
    let b = home.path().join("project-b");
    std::fs::create_dir(&a).unwrap();
    std::fs::create_dir(&b).unwrap();
    let executable = home.path().join("fake-codex");
    let log = home.path().join("engine-cwd");
    std::fs::write(&executable, "#!/bin/sh\npwd > \"$BRIDGE_TEST_CWD_LOG\"\nexit 1\n").unwrap();
    std::fs::set_permissions(&executable, std::fs::Permissions::from_mode(0o700)).unwrap();
    let socket = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = socket.local_addr().unwrap();
    let config = home.path().join("bridge.toml");
    let original = format!(r#"
provider_id = "cli-provider"
listen = "{address}"
state_path = "state.sqlite3"
[registration]
provider_auth = "static-bearer"
[[bot]]
provider_bot_ref = "fixed-reference"
bot_id = "fixed-bot"
token = "fixed-token"
engine = "codex"
engine_bin = {}
cwd = {}
"#, json!(executable), json!(a));
    std::fs::write(&config, &original).unwrap();
    drop(socket);
    let client = Client::builder().no_proxy().timeout(Duration::from_secs(5)).build().unwrap();
    for (cwd_override, expected) in [(Some(&b), &b), (None, &a)] {
        let mut start = command(home.path());
        start.env("BRIDGE_TEST_CWD_LOG", &log).args(["start", "--daemon", "--config"]).arg(&config);
        if let Some(cwd) = cwd_override { start.arg("--cwd").arg(cwd); }
        let started = start.output().await.unwrap();
        assert!(started.status.success(), "{}", String::from_utf8_lossy(&started.stderr));
        let response = client.post(format!("http://{address}/webhook"))
            .bearer_auth("test-webhook-secret").header("X-BCN-Protocol-Version", "2.0").json(&json!({
                "type":"req","id":"run-cwd","method":"chat.send","session_id":"same-bcs-session",
                "to_bot":{"provider_id":"cli-provider","provider_bot_ref":"fixed-reference"},
                "message":{"role":"user","content":[{"type":"text","text":"test"}]}
            })).send().await;
        // Always stop the child, including on a failed webhook request.
        let body = match response { Ok(response) => response.text().await, Err(error) => Err(error) };
        let stopped = command(home.path()).arg("stop").output().await.unwrap();
        assert!(stopped.status.success());
        let body = body.unwrap();
        let actual = std::fs::read_to_string(&log).unwrap_or_else(|error| panic!("engine was not called: {error}; response: {body}"));
        assert_eq!(Path::new(actual.trim()).canonicalize().unwrap(), expected.canonicalize().unwrap());
        assert_eq!(std::fs::read_to_string(&config).unwrap(), original);
    }
}

#[tokio::test]
async fn workspace_override_rejects_non_codex_missing_policy_and_non_directory() {
    let home = tempfile::tempdir().unwrap();
    let config = home.path().join("test.toml");
    let file = home.path().join("plain-file");
    std::fs::write(&file, "test").unwrap();
    for (engine, policy, cwd, expected) in [
        ("claude-code", "provider_auth='static-bearer'", home.path(), "one native Codex Bot"),
        ("codex", "", home.path(), "registered identity policy"),
        ("codex", "provider_auth='static-bearer'", file.as_path(), "must be a directory"),
    ] {
        let text = format!("provider_id='cli-provider'\nlisten='127.0.0.1:21000'\n[registration]\n{policy}\n[[bot]]\nprovider_bot_ref='fixed'\nengine='{engine}'\ncwd={}\n", json!(home.path()));
        std::fs::write(&config, &text).unwrap();
        let output = command(home.path()).args(["start", "--config"]).arg(&config).arg("--cwd").arg(cwd).output().await.unwrap();
        assert!(!output.status.success());
        assert!(String::from_utf8_lossy(&output.stderr).contains(expected), "{}", String::from_utf8_lossy(&output.stderr));
        assert_eq!(std::fs::read_to_string(&config).unwrap(), text);
        assert!(!home.path().join(".bcn-bridge").exists());
    }
}
