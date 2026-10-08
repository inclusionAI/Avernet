//! Shared infrastructure for BCS integration and E2E tests.
//!
//! This module provides common utilities for:
//! - Process management (BCS server, optional Moltis gateway)
//! - bcs-cli invocation with token discovery
//! - HTTP API helpers
//!
//! Token flow:
//! - E2E tests (with Moltis): BCN plugin sets BCN_BOT_TOKEN env var and creates session file
//! - Non-E2E tests: Tests manage tokens directly via --token argument

#![allow(dead_code)]

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::Duration;

use tempfile::TempDir;

// ============================================================================
// Constants
// ============================================================================

const TEST_GROUP_SESSION_WS_SIGNING_KEY: &str =
    "test-only-group-session-key-at-least-32-bytes";
const TEST_GATEWAY_PRINCIPAL_SIGNING_KEY: &str =
    "test-only-gateway-principal-signing-key";

/// Get next available port by asking the OS for a free one.
///
/// Binds to port 0 (OS-assigned), reads the port, then releases it.
/// This avoids conflicts when tests run in parallel across separate processes,
/// where a static counter would reset to the same base value in each process.
pub fn next_port() -> u16 {
    let listener = std::net::TcpListener::bind("127.0.0.1:0")
        .expect("Failed to bind to get free port");
    let port = listener.local_addr().unwrap().port();
    drop(listener);
    port
}

// ============================================================================
// Process Manager
// ============================================================================

/// Manage external processes with automatic cleanup
///
/// IMPORTANT: All spawned processes are tracked internally and will be killed
/// when this struct is dropped, regardless of test success or failure.
pub struct ProcessManager {
    processes: Vec<Child>,
}

impl ProcessManager {
    pub fn new() -> Self {
        Self { processes: vec![] }
    }

    /// Start BCS server on a random port
    ///
    /// The process is tracked immediately after spawning to ensure cleanup
    /// even if health check fails.
    pub async fn start_bcs(&mut self, data_dir: &PathBuf) -> Result<u16, Box<dyn std::error::Error + Send + Sync>> {
        let port = next_port();

        // Build BCS binary path
        let bcs_bin = std::env::current_exe()?
            .parent()
            .and_then(|p| p.parent())
            .map(|p| p.join("bcs"))
            .ok_or("Could not find bcs binary")?;

        // Create configs directory with bcs-config.json
        // -c flag specifies the directory containing bcs-config.json
        let configs_dir = data_dir.join("configs");
        std::fs::create_dir_all(&configs_dir)?;

        let config_content = serde_json::json!({
            "bind": "127.0.0.1",
            "port": port,
            "bots_base_dir": data_dir.to_str().unwrap_or(""),
            "dingtalk_accounts": [],
            "secret": {
                "provider": "env",
                "providers": {
                    "env": {
                        "prefix": "BCS_SECRET_"
                    }
                }
            }
        });
        let config_file = configs_dir.join("bcs-config.json");
        std::fs::write(&config_file, serde_json::to_string_pretty(&config_content)?)?;

        let mut cmd = Command::new(&bcs_bin);
        cmd.arg("-c").arg(&configs_dir)
            .env("BCS_DATA_DIR", data_dir)
            .env(
                "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE",
                TEST_GATEWAY_PRINCIPAL_SIGNING_KEY,
            )
            .env(
                "BCS_SECRET_BCN_GROUP_SESSION_WS_JWT",
                TEST_GROUP_SESSION_WS_SIGNING_KEY,
            )
            // External-process integration tests pass per-request mock user
            // headers; enable the debug-only mock path without inheriting an
            // ambient default user from the parent shell.
            .env("BCS_AUTH_MOCK", "1")
            .env_remove("BCS_MOCK_USER_ID")
            .env_remove("BCS_MOCK_USER_NICK_NAME")
            .env_remove("BCS_MOCK_USER_CHANNEL")
            // Run from the data dir
            .current_dir(data_dir)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        // Spawn and track immediately - ensures cleanup on failure
        let mut child = cmd.spawn()?;

        // Capture and print stdout/stderr in background
        if let Some(stdout) = child.stdout.take() {
            std::thread::spawn(move || {
                use std::io::{BufRead, BufReader};
                let reader = BufReader::new(stdout);
                for line in reader.lines().flatten() {
                    println!("[BCS] {}", line);
                }
            });
        }
        if let Some(stderr) = child.stderr.take() {
            std::thread::spawn(move || {
                use std::io::{BufRead, BufReader};
                let reader = BufReader::new(stderr);
                for line in reader.lines().flatten() {
                    eprintln!("[BCS] {}", line);
                }
            });
        }

        self.processes.push(child);

        // Wait for server to be ready
        let client = reqwest::Client::new();
        let url = format!("http://127.0.0.1:{}/health", port);
        for _ in 0..30 {
            match client
                .get(&url)
                .timeout(Duration::from_secs(1))
                .send()
                .await
            {
                Ok(response) if response.status().is_success() => {
                    println!("[BCS] Server ready on port {}", port);
                    return Ok(port);
                }
                Ok(_) => tokio::time::sleep(Duration::from_millis(100)).await,
                Err(_) => tokio::time::sleep(Duration::from_millis(100)).await,
            }
        }

        Err(format!("BCS server did not start on port {}", port).into())
    }

    /// Start a Moltis gateway process with skill setup (for E2E tests only)
    ///
    /// The process is tracked immediately after spawning to ensure cleanup
    /// even if connection verification fails.
    ///
    /// Returns (bot_uuid, token) from BCN plugin's session file.
    pub async fn start_moltis(
        &mut self,
        bcs_port: u16,
        bot_port: u16,
        bot_name: &str,
        data_dir: &PathBuf,
    ) -> Result<(String, String), Box<dyn std::error::Error + Send + Sync>> {
        // Build Moltis binary path - use CARGO_MANIFEST_DIR for workspace root
        let manifest_dir = std::env::var("CARGO_MANIFEST_DIR")
            .expect("CARGO_MANIFEST_DIR not set");
        let manifest_path = PathBuf::from(&manifest_dir);
        let workspace_root = manifest_path
            .parent()
            .and_then(|p| p.parent())
            .ok_or("Could not find workspace root")?;
        let moltis_bin = workspace_root
            .join("submodules")
            .join("moltis")
            .join("target")
            .join("debug")
            .join("moltis");
        let bcs_cli_bin = workspace_root
            .join("target")
            .join("debug")
            .join("bcs-cli");
        let skill_template = workspace_root
            .join("crates")
            .join("bcs-cli")
            .join("SKILL.md");

        // Create config directory
        let bot_dir = data_dir.join(bot_name);
        let config_dir = bot_dir.join("config");
        std::fs::create_dir_all(&config_dir)?;
        std::fs::create_dir_all(bot_dir.join("workspace"))?;

        // Setup bcs-coordination skill
        setup_bcs_skill(&bot_dir, bot_name, &bcs_cli_bin, &skill_template)?;

        // Create moltis.toml config with LLM provider settings
        // Matches the config from test.sh start_bot function
        let config_content = format!(
            r#"
[server]
bind = "127.0.0.1"
port = {}

[tls]
enabled = false

[skills]
search_paths = ["{}/skills"]
auto_load = ["bcs-coordination"]

[tools.exec]
approval_mode = "never"
security_level = "permissive"

# Enable custom LLM provider (requires provider_keys.json)
[providers."custom-antchat-alipay-com"]
enabled = true

# Disable ollama to prevent it being selected as default
[providers.ollama]
enabled = false

# BCS channel for bot-to-bot communication
[channels.bcn.my-bot]
url = "ws://127.0.0.1:{}/ws/bot"
bot_id = "{}"
bot_name = "{}"
dm_policy = "open"
model = "Kimi-K2-Thinking"
enable_streaming = true
heartbeat_interval_secs = 60
reconnect_interval_secs = 5
connection_timeout_secs = 30
"#,
            bot_port, bot_dir.display(), bcs_port, bot_name, bot_name
        );

        std::fs::write(config_dir.join("moltis.toml"), config_content)?;

        // Copy provider_keys.json from global config if it exists
        // This is required for LLM to work
        let global_provider_keys = std::env::var("HOME")
            .map(|h| std::path::PathBuf::from(h).join(".config/moltis/provider_keys.json"))
            .unwrap_or_else(|_| std::path::PathBuf::from("/dev/null"));

        if global_provider_keys.exists() {
            if let Ok(keys) = std::fs::read_to_string(&global_provider_keys) {
                std::fs::write(config_dir.join("provider_keys.json"), keys)?;
                println!("Copied provider_keys.json for {}", bot_name);
            }
        } else {
            println!("Warning: No provider_keys.json found at {:?} - LLM may not work", global_provider_keys);
        }

        // Create a simple SOUL.md that tells the bot to use skill for onboarding
        let soul_content = format!(
            r#"
你是 {} 的个人 AI 助手。

当收到 BCS 的 onboarding 指令时：
1. 使用 bcs-coordination 技能注册到 BCS
2. 执行: ./bcs-cli onboard --name "{}" --summary "..." --skills "..."

始终使用 skill 来执行 BCS 相关操作。
"#,
            bot_name, bot_name
        );
        std::fs::write(bot_dir.join("SOUL.md"), soul_content)?;

        let mut cmd = Command::new(&moltis_bin);
        let bot_name_for_log = bot_name.to_string();
        cmd.env("MOLTIS_CONFIG_DIR", &config_dir)
            .env("MOLTIS_DATA_DIR", &bot_dir)
            .env("MOLTIS_WORKSPACE_PATH", bot_dir.join("workspace"))
            .env("MOLTIS_BCS_URL", format!("http://127.0.0.1:{}", bcs_port))
            .env("MOLTIS_BOT_ID", bot_name)
            .env("MOLTIS_PORT", bot_port.to_string())
            .env("RUST_LOG", "debug")
            .arg("--port")
            .arg(bot_port.to_string())
            .stdout(Stdio::null())
            .stderr(Stdio::null());

        // Spawn and track immediately - ensures cleanup on failure
        let mut child = cmd.spawn()?;

        // Capture and print stdout/stderr in background
        if let Some(stdout) = child.stdout.take() {
            let name = bot_name_for_log.clone();
            std::thread::spawn(move || {
                use std::io::{BufRead, BufReader};
                let reader = BufReader::new(stdout);
                for line in reader.lines().flatten() {
                    println!("[Moltis/{}] {}", name, line);
                }
            });
        }
        if let Some(stderr) = child.stderr.take() {
            let name = bot_name_for_log.clone();
            std::thread::spawn(move || {
                use std::io::{BufRead, BufReader};
                let reader = BufReader::new(stderr);
                for line in reader.lines().flatten() {
                    eprintln!("[Moltis/{}] {}", name, line);
                }
            });
        }

        self.processes.push(child);

        // Wait for gateway to be ready and BCN to connect
        let client = reqwest::Client::new();
        let health_url = format!("http://127.0.0.1:{}/health", bot_port);

        for _ in 0..100 {
            // Check health endpoint
            if client.get(&health_url).timeout(Duration::from_secs(1)).send().await.is_ok() {
                // Also wait for BCN to connect to BCS
                tokio::time::sleep(Duration::from_millis(200)).await;

                // Check for session file which indicates successful connection
                let session_file = data_dir.join(bot_name).join(".bcs").join("session.json");
                if session_file.exists() {
                    let content = std::fs::read_to_string(&session_file)?;
                    let session: serde_json::Value = serde_json::from_str(&content)?;
                    let bot_uuid = session["bot_uuid"].as_str().unwrap_or("").to_string();
                    let token = session["token"].as_str().unwrap_or("").to_string();
                    if !bot_uuid.is_empty() && !token.is_empty() {
                        // Wait a bit to ensure session is stable
                        tokio::time::sleep(Duration::from_millis(500)).await;

                        // Re-read to ensure token hasn't changed
                        let content2 = std::fs::read_to_string(&session_file)?;
                        let session2: serde_json::Value = serde_json::from_str(&content2)?;
                        let token2 = session2["token"].as_str().unwrap_or("").to_string();
                        if token == token2 {
                            println!("Moltis '{}' started on port {}, bot_uuid={}", bot_name, bot_port, bot_uuid);
                            return Ok((bot_uuid, token));
                        }
                    }
                }
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }

        Err(format!("Moltis gateway did not start properly on port {}", bot_port).into())
    }

    /// Kill all managed processes
    pub fn kill_all(&mut self) {
        for child in &mut self.processes {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.processes.clear();
    }

    /// Kill and remove the last process (for reconnection tests)
    pub fn kill_last(&mut self) {
        if let Some(mut child) = self.processes.pop() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Drop for ProcessManager {
    fn drop(&mut self) {
        self.kill_all();
    }
}

// ============================================================================
// Skill Setup Helper (for E2E tests)
// ============================================================================

/// Setup bcs-coordination skill for a bot (mirrors test.sh setup_bcs_skill)
fn setup_bcs_skill(
    bot_dir: &PathBuf,
    bot_id: &str,
    bcs_cli_bin: &PathBuf,
    skill_template: &PathBuf,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let skill_dir = bot_dir.join("skills").join("bcs-coordination");
    std::fs::create_dir_all(&skill_dir)?;

    // Read and customize SKILL.md template
    if skill_template.exists() {
        let template = std::fs::read_to_string(skill_template)?;
        // Replace placeholder with actual bot_id
        let customized = template.replace("<你的Bot ID>", bot_id);
        std::fs::write(skill_dir.join("SKILL.md"), customized)?;
    } else {
        // Create a minimal skill if template not found
        let minimal_skill = format!(
            r#"---
name: bcs-coordination
description: BCS coordination skill for {}
allowed_tools:
  - exec
---

Use ./bcs-cli to interact with BCS.

Onboarding:
./bcs-cli onboard --name "{}" --summary "..." --skills "..."
"#,
            bot_id, bot_id
        );
        std::fs::write(skill_dir.join("SKILL.md"), minimal_skill)?;
    }

    // Copy bcs-cli binary to skill directory
    if bcs_cli_bin.exists() {
        std::fs::copy(bcs_cli_bin, skill_dir.join("bcs-cli"))?;
        // Make executable on Unix
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(skill_dir.join("bcs-cli"), std::fs::Permissions::from_mode(0o755))?;
        }
    }

    Ok(())
}

// ============================================================================
// HTTP API Helpers
// ============================================================================

/// Get bot list from BCS via HTTP API
pub async fn get_bots(bcs_url: &str, token: &str) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let client = reqwest::Client::new();
    let response = client
        .get(&format!("{}/bots", bcs_url))
        .header("Authorization", format!("Bearer {}", token))
        .timeout(Duration::from_secs(5))
        .send()
        .await?;

    let status = response.status();
    if !status.is_success() {
        return Err(format!("Failed to get bots: {}", status).into());
    }

    let body = response.text().await?;
    let bots: serde_json::Value = serde_json::from_str(&body)?;
    Ok(bots)
}

// ============================================================================
// bcs-cli Helpers
// ============================================================================

/// Run bcs-cli without any token (for health check only)
/// Used in non-E2E tests where no BCN plugin is running.
pub fn run_bcs_cli_no_token(
    bcs_url: &str,
    args: &[&str],
) -> Result<String, Box<dyn std::error::Error>> {
    let cli_bin = std::env::current_exe()?
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.join("bcs-cli"))
        .ok_or("Could not find bcs-cli binary")?;

    let url_arg = format!("--url={}", bcs_url);

    let mut cmd = Command::new(&cli_bin);
    cmd.arg(&url_arg)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let output = cmd.output()?;

    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        Err(format!(
            "bcs-cli failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ).into())
    }
}

/// Run bcs-cli with explicit --token argument.
/// Used in non-E2E tests where tests manage tokens directly.
#[allow(dead_code)]
pub fn run_bcs_cli_with_token(
    bcs_url: &str,
    token: &str,
    args: &[&str],
) -> Result<String, Box<dyn std::error::Error>> {
    let cli_bin = std::env::current_exe()?
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.join("bcs-cli"))
        .ok_or("Could not find bcs-cli binary")?;

    let url_arg = format!("--url={}", bcs_url);

    let mut cmd = Command::new(&cli_bin);
    cmd.arg(&url_arg)
        .args(args)
        .arg("--token")
        .arg(token)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let output = cmd.output()?;

    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        Err(format!(
            "bcs-cli failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ).into())
    }
}

/// Run bcs-cli with token provided via environment variable (BCN_BOT_TOKEN).
/// This mimics how BCN plugin prepares the environment for bcs-cli in E2E tests.
pub fn run_bcs_cli_with_env_token(
    bcs_url: &str,
    token: &str,
    bot_data_dir: &PathBuf,
    args: &[&str],
) -> Result<String, Box<dyn std::error::Error>> {
    let cli_bin = std::env::current_exe()?
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.join("bcs-cli"))
        .ok_or("Could not find bcs-cli binary")?;

    let url_arg = format!("--url={}", bcs_url);

    let mut cmd = Command::new(&cli_bin);
    cmd.arg(&url_arg)
        .args(args)
        // BCN plugin sets these environment variables for bcs-cli
        .env("BCN_BOT_TOKEN", token)
        .env("BOT_DATA_DIR", bot_data_dir);

    cmd.stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let output = cmd.output()?;

    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        Err(format!(
            "bcs-cli failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ).into())
    }
}

/// Run bcs-cli using session file from disk (BCN plugin creates this).
/// This mimics how bcs-cli discovers token from session file in E2E tests.
pub fn run_bcs_cli_with_session_file(
    bcs_url: &str,
    bot_data_dir: &PathBuf,
    args: &[&str],
) -> Result<String, Box<dyn std::error::Error>> {
    let cli_bin = std::env::current_exe()?
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.join("bcs-cli"))
        .ok_or("Could not find bcs-cli binary")?;

    let url_arg = format!("--url={}", bcs_url);

    let mut cmd = Command::new(&cli_bin);
    cmd.arg(&url_arg)
        .args(args)
        // bcs-cli will look for session file at $BOT_DATA_DIR/.bcs/session.json
        .env("BOT_DATA_DIR", bot_data_dir);

    cmd.stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let output = cmd.output()?;

    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        Err(format!(
            "bcs-cli failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ).into())
    }
}

/// Send a message to Moltis gateway to trigger skill-based onboarding (E2E only)
pub async fn trigger_skill_onboarding(
    bot_port: u16,
    bot_name: &str,
    summary: &str,
    skills: &str,
) -> Result<String, Box<dyn std::error::Error>> {
    let client = reqwest::Client::new();

    // Moltis gateway endpoints for session management
    let gateway_url = format!("http://127.0.0.1:{}", bot_port);

    // Create a session first
    let create_session_url = format!("{}/api/sessions", gateway_url);
    let session_response = client
        .post(&create_session_url)
        .json(&serde_json::json!({"title": "onboarding-test"}))
        .timeout(Duration::from_secs(10))
        .send()
        .await;

    let session_key = match session_response {
        Ok(resp) if resp.status().is_success() => {
            let body = resp.text().await?;
            let json: serde_json::Value = serde_json::from_str(&body).unwrap_or(serde_json::json!({}));
            json["key"].as_str().unwrap_or("default").to_string()
        }
        _ => "onboarding-test".to_string(),
    };

    // Send a message instructing the bot to onboard using its skill
    let onboarding_instruction = format!(
        "请使用 bcs-coordination 技能注册到 BCS。\n\
         执行: ./bcs-cli onboard --name \"{}\" --summary \"{}\" --skills \"{}\"",
        bot_name, summary, skills
    );

    let send_url = format!("{}/api/sessions/{}/messages", gateway_url, session_key);
    let response = client
        .post(&send_url)
        .json(&serde_json::json!({
            "role": "user",
            "content": onboarding_instruction
        }))
        .timeout(Duration::from_secs(60))
        .send()
        .await?;

    if !response.status().is_success() {
        return Err(format!("Failed to send onboarding message: {}", response.status()).into());
    }

    // Wait for response
    tokio::time::sleep(Duration::from_secs(2)).await;

    // Get the bot's response
    let history_url = format!("{}/api/sessions/{}/messages", gateway_url, session_key);
    let history_response = client
        .get(&history_url)
        .timeout(Duration::from_secs(10))
        .send()
        .await?;

    let body = history_response.text().await?;
    Ok(body)
}

// ============================================================================
// Moltis CLI Helpers (for user-to-bot communication)
// ============================================================================

/// Get the path to moltis CLI binary
fn get_moltis_cli_path() -> Result<PathBuf, Box<dyn std::error::Error>> {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR")
        .expect("CARGO_MANIFEST_DIR not set");
    let manifest_path = PathBuf::from(&manifest_dir);
    let workspace_root = manifest_path
        .parent()
        .and_then(|p| p.parent())
        .ok_or("Could not find workspace root")?;
    Ok(workspace_root
        .join("submodules")
        .join("moltis")
        .join("target")
        .join("debug")
        .join("moltis"))
}

/// Send a message to a bot via Moltis gateway (simulates user talking to bot).
///
/// This mirrors the test.sh `send_and_wait` function but returns immediately.
/// Use `get_bot_response` to poll for the bot's reply.
///
/// # Arguments
/// * `bot_port` - The port the bot's Moltis gateway is listening on
/// * `session_key` - Session key for the conversation
/// * `message` - The message to send
pub fn send_to_bot(
    bot_port: u16,
    session_key: &str,
    message: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let moltis_cli = get_moltis_cli_path()?;
    let gateway_url = format!("http://127.0.0.1:{}", bot_port);

    let mut cmd = Command::new(&moltis_cli);
    cmd.arg("--log-level")
        .arg("error")
        .arg("sessions")
        .arg("send")
        .arg(session_key)
        .arg(message)
        .arg("--gateway")
        .arg(&gateway_url)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let output = cmd.output()?;

    if !output.status.success() {
        return Err(format!(
            "moltis-cli send failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ).into());
    }

    Ok(())
}

/// Get the last response from a bot via Moltis gateway.
///
/// Polls the session history for the assistant's response after the user's message.
///
/// # Arguments
/// * `bot_port` - The port the bot's Moltis gateway is listening on
/// * `session_key` - Session key for the conversation
/// * `sent_message` - The message we sent (to find in history)
pub fn get_bot_response(
    bot_port: u16,
    session_key: &str,
    sent_message: &str,
) -> Result<Option<String>, Box<dyn std::error::Error>> {
    let moltis_cli = get_moltis_cli_path()?;
    let gateway_url = format!("http://127.0.0.1:{}", bot_port);

    let mut cmd = Command::new(&moltis_cli);
    cmd.arg("--log-level")
        .arg("error")
        .arg("sessions")
        .arg("history")
        .arg(session_key)
        .arg("--limit")
        .arg("10")
        .arg("--json")
        .arg("--gateway")
        .arg(&gateway_url)
        .env("SENT_MSG", sent_message)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let output = cmd.output()?;

    if !output.status.success() {
        return Err(format!(
            "moltis-cli history failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ).into());
    }

    let stdout = String::from_utf8_lossy(&output.stdout);

    // Parse the JSON response to find the assistant's reply
    let history: serde_json::Value = serde_json::from_str(&stdout)?;

    let mut found_sent = false;
    if let Some(messages) = history.as_array() {
        for msg in messages {
            let role = msg.get("role").and_then(|r| r.as_str()).unwrap_or("");

            if role == "user" && !found_sent {
                let content = msg.get("content").and_then(|c| c.as_str()).unwrap_or("");
                if sent_message == content || content.contains(sent_message) || sent_message.contains(content) {
                    found_sent = true;
                    continue;
                }
            }

            if role == "assistant" && found_sent {
                // Get content (can be string or array)
                if let Some(content) = msg.get("content") {
                    if let Some(text) = content.as_str() {
                        if !text.trim().is_empty() {
                            return Ok(Some(text.to_string()));
                        }
                    } else if let Some(parts) = content.as_array() {
                        for part in parts {
                            if part.get("type").and_then(|t| t.as_str()) == Some("text") {
                                if let Some(text) = part.get("text").and_then(|t| t.as_str()) {
                                    if !text.trim().is_empty() {
                                        return Ok(Some(text.to_string()));
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    Ok(None)
}

/// Send a message to a bot and wait for response (combines send_to_bot + polling).
///
/// This mirrors the test.sh `send_and_wait` function.
///
/// # Arguments
/// * `bot_port` - The port the bot's Moltis gateway is listening on
/// * `session_key` - Session key for the conversation
/// * `message` - The message to send
/// * `max_wait_secs` - Maximum seconds to wait for response
pub async fn send_and_wait_for_response(
    bot_port: u16,
    session_key: &str,
    message: &str,
    max_wait_secs: u64,
) -> Result<String, Box<dyn std::error::Error>> {
    // Send the message
    send_to_bot(bot_port, session_key, message)?;

    // Poll for response
    let mut elapsed = 0;
    let poll_interval = 1;

    while elapsed < max_wait_secs {
        tokio::time::sleep(Duration::from_secs(poll_interval)).await;
        elapsed += poll_interval;

        if let Some(response) = get_bot_response(bot_port, session_key, message)? {
            return Ok(response);
        }
    }

    Err(format!("No response from bot on port {} after {}s", bot_port, max_wait_secs).into())
}

// ============================================================================
// Test fixture helpers
// ============================================================================

/// Create a temporary directory for test data
pub fn create_temp_dir() -> (TempDir, PathBuf) {
    let temp_dir = TempDir::new().expect("Failed to create temp dir");
    let data_dir = temp_dir.path().to_path_buf();
    (temp_dir, data_dir)
}
