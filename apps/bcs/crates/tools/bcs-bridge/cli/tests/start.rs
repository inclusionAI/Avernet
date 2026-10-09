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
    assert!(!home.path().join(".bcn-bridge").exists());
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
