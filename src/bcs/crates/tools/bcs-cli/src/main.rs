//! BCS CLI - Command-line tools for Bot Coordination Service.
//!
//! This binary provides CLI commands for:
//! - Bot lifecycle (onboard)
//! - Bot discovery (list, discover)
//! - Group collaboration (request, confirm, create)
//! - Cross-bot communication (chat)
//!
//! # Configuration
//!
//! BCS CLI can be configured via:
//! 1. Command-line arguments (highest priority)
//! 2. Environment variables: MOLTIS_BCS_URL, BCS_COOKIE
//! 3. Session file: $BOT_DATA_DIR/.bcs/session.json
//! 4. Default local URL (lowest priority)
//!
//! # Environment-Based Configuration
//!
//! Set `env` environment variable to switch between environments:
//! - `env=dev` (default) - development environment
//! - `env=pre` - pre-production environment
//! - `env=prod` - production environment
//!
//! # Token Discovery
//!
//! Token is discovered in the following order:
//! 1. `--token` CLI argument
//! 2. `BCN_BOT_TOKEN` environment variable (set by BCN plugin)
//! 3. `$BOT_DATA_DIR/.bcs/session.json` file (written by BCN plugin)
//!
//! Note: bcs-cli is stateless - it only READS session files, never writes.
//! Session persistence is handled by the BCN plugin (moltis-bcn crate).
//!
//! # Usage
//!
//! ```bash
//! # Token auto-discovered from env var or session file
//! bcs-cli onboard --name "My Bot" --summary "An assistant bot"
//!
//! # Or specify token explicitly
//! bcs-cli onboard --token <token> --name "My Bot" --summary "An assistant bot"
//!
//! # Use pre-production environment
//! env=pre bcs-cli health
//! ```

mod agentpass;
mod oauth;

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use anyhow::{Result, anyhow};
#[cfg(debug_assertions)]
use clap::ArgAction;
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use serde_json::json;
use tracing::{Level, debug, info};
use tracing_subscriber::FmtSubscriber;

use bcs_cli::BcsClient;
use bcs_protocol::{BCS_PROTOCOL_VERSION, BotConnectParams};

// disable agentpass, agentpass token should be auto injected into the http headers
const AUTH_VIA_AGENT_PASS: bool = false;

#[derive(Debug, Serialize, Default)]
struct StructuredResult {
    status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    network_env: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    auth_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    timeout_secs: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    log_file: Option<String>,
}

fn is_structured_mode(cli: &Cli) -> bool {
    // Default is structured (JSON) mode. --no-json disables it.
    // --json flag kept for backward compatibility (OpenClaw already passes it).
    !cli.no_json
}

fn build_log_file_path() -> PathBuf {
    let dir = std::env::var("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
        .join(".bcs/logs");
    dir.join("bcs-cli.log")
}

fn emit_structured_result(result: &StructuredResult) {
    println!(
        "{}",
        serde_json::to_string(result)
            .unwrap_or_else(|_| "{\"status\":\"request_failed\"}".to_string())
    );
}

fn classify_auth_error_message(msg: &str) -> &'static str {
    if msg.contains("timed out locally") {
        "auth_timeout"
    } else if msg.contains("expired on server") || msg.contains("authorization expired") {
        "auth_expired"
    } else if msg.contains("auth URL")
        || msg.contains("NeedAuth")
        || msg.contains("authorization required")
    {
        "auth_required"
    } else if msg.contains("OAuth2") || msg.contains("OAuth") {
        "auth_failed"
    } else {
        "request_failed"
    }
}

/// Resolve OAuth2 client credentials: env var override > compiled-in defaults.
fn resolve_oauth_credentials() -> (String, String) {
    let client_id = std::env::var("BCS_OAUTH_CLIENT_ID")
        .unwrap_or_else(|_| oauth::default_oauth_client_id().to_string());
    let client_secret = std::env::var("BCS_OAUTH_CLIENT_SECRET")
        .unwrap_or_else(|_| oauth::default_oauth_client_secret().to_string());
    (client_id, client_secret)
}

/// Get current environment from `AGENTCLAW_ENV` or `env` variable.
/// Priority: `AGENTCLAW_ENV` > `env` > `SERVER_ENV` chain > "dev" (default)
fn get_current_env() -> String {
    std::env::var("AGENTCLAW_ENV")
        .or_else(|_| std::env::var("env"))
        .unwrap_or_else(|_| bcs_config::resolve_env_str())
}

// ============================================================================
// Token Discovery
// ============================================================================

/// Session info saved by BCN plugin (read-only for bcs-cli).
#[derive(Debug, Clone, Serialize, Deserialize)]
struct SessionInfo {
    #[serde(default)]
    pub bot_uuid: Option<String>,
    pub token: String,
    #[serde(default)]
    pub bcs_url: Option<String>,
    #[serde(default)]
    pub api_base_url: Option<String>,
}

/// Network environment for BCS CLI.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum NetworkEnv {
    /// Office network - requires OAuth2 authentication through company gateway.
    Office,
    /// Production network - uses bot token only (default).
    Prod,
}

fn session_file_path(data_dir: impl AsRef<Path>) -> PathBuf {
    data_dir.as_ref().join(".bcs").join("session.json")
}

fn get_optional_session_file_path() -> Option<PathBuf> {
    std::env::var("BOT_DATA_DIR").ok().map(session_file_path)
}

fn load_session_info_from_path(session_file: &Path) -> Result<Option<SessionInfo>> {
    if !session_file.exists() {
        return Ok(None);
    }

    let content = std::fs::read_to_string(session_file)
        .map_err(|e| anyhow!("Failed to read session file {:?}: {}", session_file, e))?;

    let session: SessionInfo = serde_json::from_str(&content)
        .map_err(|e| anyhow!("Failed to parse session file {:?}: {}", session_file, e))?;

    Ok(Some(session))
}

fn load_optional_session_info() -> Result<Option<SessionInfo>> {
    let Some(session_file) = get_optional_session_file_path() else {
        return Ok(None);
    };
    load_session_info_from_path(&session_file)
}

fn normalize_bcs_api_url(raw: &str) -> Option<String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }

    let mut url = reqwest::Url::parse(trimmed).ok()?;
    match url.scheme() {
        "ws" => url.set_scheme("http").ok()?,
        "wss" => url.set_scheme("https").ok()?,
        "http" | "https" => {}
        _ => return None,
    }

    url.set_query(None);
    url.set_fragment(None);

    let normalized_path = match url.path() {
        "/" | "" => "/".to_string(),
        path if path.ends_with("/ws/bot") => {
            let stripped = path.trim_end_matches("/ws/bot").trim_end_matches('/');
            if stripped.is_empty() {
                "/".to_string()
            } else {
                stripped.to_string()
            }
        }
        path => path.trim_end_matches('/').to_string(),
    };
    url.set_path(&normalized_path);

    Some(url.as_str().trim_end_matches('/').to_string())
}

fn normalize_bcs_ws_url(raw: &str) -> Option<String> {
    let normalized_api_url = normalize_bcs_api_url(raw)?;
    let mut url = reqwest::Url::parse(&normalized_api_url).ok()?;
    match url.scheme() {
        "http" => url.set_scheme("ws").ok()?,
        "https" => url.set_scheme("wss").ok()?,
        "ws" | "wss" => {}
        _ => return None,
    }

    let ws_path = match url.path() {
        "/" | "" => "/ws/bot".to_string(),
        path => format!("{}/ws/bot", path.trim_end_matches('/')),
    };
    url.set_path(&ws_path);

    Some(url.to_string())
}

fn normalize_url_from_source(raw: String, source: &str) -> Result<String> {
    normalize_bcs_api_url(&raw)
        .ok_or_else(|| anyhow!("Invalid BCS API URL from {}: {}", source, raw))
}

fn resolve_env_bcs_url() -> Result<Option<String>> {
    if let Ok(url) = std::env::var("BCS_API_BASE_URL") {
        return normalize_url_from_source(url, "BCS_API_BASE_URL").map(Some);
    }

    if let Ok(url) = std::env::var("MOLTIS_BCS_URL") {
        return normalize_url_from_source(url, "MOLTIS_BCS_URL").map(Some);
    }

    Ok(None)
}

fn resolve_session_bcs_url() -> Result<Option<String>> {
    let Some(session) = load_optional_session_info()? else {
        return Ok(None);
    };

    let Some(raw_url) = session
        .api_base_url
        .as_deref()
        .or(session.bcs_url.as_deref())
    else {
        return Ok(None);
    };

    normalize_url_from_source(raw_url.to_string(), "$BOT_DATA_DIR/.bcs/session.json").map(Some)
}

fn resolve_bcs_url(cli: &Cli) -> Result<String> {
    if let Some(url) = cli.url.as_ref() {
        return normalize_url_from_source(url.clone(), "--url");
    }

    if let Some(url) = resolve_env_bcs_url()? {
        return Ok(url);
    }

    if let Some(url) = resolve_session_bcs_url()? {
        return Ok(url);
    }

    let default_url = "http://127.0.0.1:21000";
    info!(
        "No BCS URL configured, defaulting to local BCS: {}",
        default_url
    );
    normalize_url_from_source(default_url.to_string(), "default (local)")
}

/// Discover authentication token from various sources.
///
/// Priority:
/// 1. Explicit token argument (--token)
/// 2. BCN_BOT_TOKEN environment variable (set by BCN plugin for child processes)
/// 3. $BOT_DATA_DIR/.bcs/session.json file (written by BCN plugin)
///
/// Returns an empty string when no token source is available, allowing the CLI
/// to proceed without authentication (the server will reject if auth is required).
///
/// Note: bcs-cli is stateless - it only READS session files, never writes.
fn discover_token(explicit_token: Option<&str>) -> Result<String> {
    // 1. Use explicit token if provided
    if let Some(token) = explicit_token {
        if !token.is_empty() {
            return Ok(token.to_string());
        }
    }

    // 2. Check BCN_BOT_TOKEN environment variable (set by BCN plugin for child processes)
    if let Ok(token) = std::env::var("BCN_BOT_TOKEN") {
        if !token.is_empty() {
            debug!("Using BCN_BOT_TOKEN from environment");
            return Ok(token);
        }
    }

    // 3. Check session file in BOT_DATA_DIR (optional — missing dir or file is not an error)
    if let Some(session_file) = get_optional_session_file_path() {
        if let Some(session) = load_session_info_from_path(&session_file)? {
            if !session.token.is_empty() {
                debug!("Using token from session file");
                return Ok(session.token);
            }
        }
    }

    // No token found — return empty to allow unauthenticated requests.
    debug!("No token found, proceeding without authentication");
    Ok(String::new())
}

/// Get optional token from CLI argument.
fn get_token(token_arg: Option<&str>) -> Result<String> {
    discover_token(token_arg)
}

/// Resolve the current bot's UUID from session file.
///
/// Priority:
/// 1. $BOT_DATA_DIR/.bcs/session.json `bot_uuid` field
///
/// This is used by friend/visibility commands that operate on "my" bot
/// without requiring the user to specify their own bot_uuid.
fn resolve_my_bot_uuid() -> Result<String> {
    if let Ok(Some(session)) = load_optional_session_info() {
        if let Some(ref uuid) = session.bot_uuid {
            if !uuid.is_empty() {
                return Ok(uuid.clone());
            }
        }
    }

    Err(anyhow!(
        "Cannot resolve current bot UUID.\n\
         Ensure $BOT_DATA_DIR/.bcs/session.json contains a valid bot_uuid field.\n\
         This file is created after bot.connect succeeds."
    ))
}

/// Parse a `--input` / `--meta` style argument: a `@file.json` reference is
/// loaded from disk; anything else is parsed directly as JSON. The leading
/// `@` is *only* a file marker when followed by a non-empty path.
fn parse_json_arg(raw: &str) -> Result<serde_json::Value> {
    if let Some(path) = raw.strip_prefix('@') {
        if path.is_empty() {
            return Err(anyhow!("'@' must be followed by a file path"));
        }
        let content = std::fs::read_to_string(path)
            .map_err(|e| anyhow!("Failed to read {}: {}", path, e))?;
        serde_json::from_str(&content)
            .map_err(|e| anyhow!("Invalid JSON in {}: {}", path, e))
    } else {
        serde_json::from_str(raw).map_err(|e| anyhow!("Invalid JSON literal: {}", e))
    }
}

fn merge_baas_session_id_into_meta(
    meta: Option<serde_json::Value>,
    baas_session_id: Option<&str>,
) -> Result<Option<serde_json::Value>> {
    let Some(baas_session_id) = baas_session_id else {
        return Ok(meta);
    };

    let mut meta_object = match meta {
        Some(serde_json::Value::Object(map)) => map,
        None => serde_json::Map::new(),
        Some(_) => {
            return Err(anyhow!(
                "--meta must be a JSON object when --baas-session-id is set"
            ));
        }
    };

    let callback_target = meta_object.remove("callback_target");
    let mut callback_target_object = match callback_target {
        Some(serde_json::Value::Object(map)) => map,
        None => serde_json::Map::new(),
        Some(_) => {
            return Err(anyhow!(
                "meta.callback_target must be a JSON object when --baas-session-id is set"
            ));
        }
    };
    callback_target_object.insert(
        "baas_session_id".to_string(),
        serde_json::Value::String(baas_session_id.to_string()),
    );
    meta_object.insert(
        "callback_target".to_string(),
        serde_json::Value::Object(callback_target_object),
    );

    Ok(Some(serde_json::Value::Object(meta_object)))
}

/// Split a service session id of the form `{group_id}:{8_hex}` into its
/// `(group_id, session_id)` parts. `group_arg` always wins when supplied;
/// otherwise the colon split must succeed.
fn split_service_sid<'a>(
    sid: &'a str,
    group_arg: Option<&'a str>,
) -> Result<(&'a str, &'a str)> {
    if let Some(group) = group_arg {
        return Ok((group, sid));
    }
    match sid.split_once(':') {
        Some((gid, _)) if !gid.is_empty() => Ok((gid, sid)),
        _ => Err(anyhow!(
            "Cannot infer group from session id '{}'. Expected '{{group}}:{{8_hex}}' \
             or pass --group explicitly.",
            sid
        )),
    }
}

/// Poll a service-invocation session until it completes or the overall
/// budget runs out. Backoff: 500ms initial, doubles each iteration, capped
/// at 5_000ms. Returns the final session JSON, or an error wrapping the
/// last fetched payload when the timeout fires.
async fn wait_for_service_completion(
    client: &BcsClient,
    group_id: &str,
    session_id: &str,
    overall_timeout_ms: u64,
) -> Result<serde_json::Value> {
    let started = std::time::Instant::now();
    let budget = std::time::Duration::from_millis(overall_timeout_ms);
    let mut delay_ms: u64 = 500;

    loop {
        let session = client
            .service_session_status(group_id, session_id)
            .await?;

        let is_done = session
            .get("status")
            .and_then(|v| v.as_str())
            .map(|s| s == "completed")
            .unwrap_or(false);
        if is_done {
            return Ok(session);
        }

        if started.elapsed() >= budget {
            let payload = serde_json::to_string(&session).unwrap_or_default();
            return Err(anyhow!(
                "Timed out after {} ms waiting for service session {} to complete. Last status: {}",
                overall_timeout_ms,
                session_id,
                payload
            ));
        }

        let remaining = budget.saturating_sub(started.elapsed());
        let sleep_for = std::time::Duration::from_millis(delay_ms).min(remaining);
        tokio::time::sleep(sleep_for).await;
        delay_ms = (delay_ms.saturating_mul(2)).min(5_000);
    }
}

/// Render a service-invocation session for human-readable output. The
/// `header` line precedes the session id (e.g. `"Invocation submitted"`,
/// `"Invocation completed"`, `"Service session"`).
fn service_session_summary_lines(session: &serde_json::Value, header: &str) -> Vec<String> {
    let mut lines = Vec::new();
    let sid = session
        .get("session_id")
        .and_then(|v| v.as_str())
        .unwrap_or("?");
    lines.push(format!("{}: {}", header, sid));
    if let Some(group_id) = session.get("group_id").and_then(|v| v.as_str()) {
        lines.push(format!("  Group:    {}", group_id));
    }
    if let Some(status) = session.get("status").and_then(|v| v.as_str()) {
        lines.push(format!("  Status:   {}", status));
    }
    if let Some(kind) = session.get("session_kind").and_then(|v| v.as_str()) {
        lines.push(format!("  Kind:     {}", kind));
    }
    if let Some(reused) = session.get("reused").and_then(|v| v.as_bool()) {
        lines.push(format!("  Reused:   {}", reused));
    }
    let run_id = session
        .get("state_machine_run_id")
        .and_then(|v| v.as_str())
        .or_else(|| {
            session
                .get("state_machine_run")
                .and_then(|v| v.get("run"))
                .and_then(|v| v.get("run_id"))
                .and_then(|v| v.as_str())
        });
    if let Some(run_id) = run_id {
        lines.push(format!("  StateRun: {}", run_id));
    }
    if let Some(run_status) = session
        .get("state_machine_run")
        .and_then(|v| v.get("run"))
        .and_then(|v| v.get("status"))
        .and_then(|v| v.as_str())
    {
        lines.push(format!("  RunStatus: {}", run_status));
    }
    if let Some(output) = session.get("output") {
        if !output.is_null() {
            let pretty = serde_json::to_string_pretty(output).unwrap_or_default();
            // UTF-8 safe truncation per src/bcs/CLAUDE.md
            let preview: &str = match pretty.char_indices().nth(500) {
                Some((idx, _)) => &pretty[..idx],
                None => &pretty,
            };
            lines.push(format!("  Output:   {}", preview));
            if pretty.len() > preview.len() {
                lines.push("            (truncated)".to_string());
            }
        }
    }
    if let Some(err) = session.get("error_message").and_then(|v| v.as_str()) {
        if !err.is_empty() {
            lines.push(format!("  Error:    {}", err));
        }
    }
    if let Some(cb) = session.get("callback_status").and_then(|v| v.as_str()) {
        if !cb.is_empty() {
            lines.push(format!("  Callback: {}", cb));
        }
    }
    lines
}

fn print_service_session_summary(session: &serde_json::Value, header: &str) {
    for line in service_session_summary_lines(session, header) {
        println!("{}", line);
    }
}
/// Auto-detect network environment by probing the BCS URL.
///
/// Sends an unauthenticated GET to `$bcs_url/health`. If the Spanner gateway
/// intercepts with a login redirect (body contains "USER_NOT_LOGIN"),
/// we're on the office network and need OAuth2. Otherwise, assume prod.
/// Localhost URLs skip the probe entirely (always Prod).
async fn detect_network_env(bcs_url: &str, structured_mode: bool) -> NetworkEnv {
    if is_localhost_url(bcs_url) {
        if !structured_mode {
            eprintln!("[network] localhost URL, skipping probe → Prod");
        }
        return NetworkEnv::Prod;
    }

    let url = format!("{}/health", bcs_url);
    if !structured_mode {
        eprintln!("[network] probing {} ...", url);
    }

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .unwrap_or_default();

    match client.get(&url).send().await {
        Ok(resp) => {
            let status = resp.status();
            let headers = resp.headers().clone();
            let body = resp.text().await.unwrap_or_default();

            // Detect Spanner gateway interception:
            // 1. Body contains login error code (HTTP 200 with JSON error)
            // 2. HTTP 401 Unauthorized (gateway auth rejection)
            // 3. Redirect to login page (HTTP 302 with login URL)
            let is_gateway = body.contains("USER_NOT_LOGIN")
                || body.contains("buserviceErrorCode")
                || status == reqwest::StatusCode::UNAUTHORIZED
                || (status.is_redirection()
                    && headers
                        .get("location")
                        .and_then(|v| v.to_str().ok())
                        .map_or(false, |loc| loc.contains("Login") || loc.contains("login")));

            if is_gateway {
                if !structured_mode {
                    eprintln!(
                        "[network] gateway login required (HTTP {}) → Office",
                        status
                    );
                }
                NetworkEnv::Office
            } else {
                if !structured_mode {
                    eprintln!("[network] direct response (HTTP {}) → Prod", status);
                }
                NetworkEnv::Prod
            }
        }
        Err(e) => {
            if !structured_mode {
                eprintln!("[network] probe failed: {} → Prod", e);
            }
            NetworkEnv::Prod
        }
    }
}

/// Check if a URL points to localhost.
fn is_localhost_url(url: &str) -> bool {
    let without_scheme = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"))
        .unwrap_or(url);
    let host = without_scheme.split('/').next().unwrap_or("");
    let host = host.split(':').next().unwrap_or(host);
    host == "localhost" || host == "127.0.0.1" || host == "::1"
}

const BCS_CLI_VERSION: &str = concat!(
    env!("CARGO_PKG_VERSION"),
    " (",
    env!("GIT_COMMIT_HASH"),
    " ",
    env!("BUILD_DATE"),
    ")",
);

/// CLI tools for Bot Coordination Service.
#[derive(Parser)]
#[command(name = "bcs-cli")]
#[command(version = BCS_CLI_VERSION)]
#[command(about = "CLI tools for Bot Coordination Service", long_about = None)]
struct Cli {
    /// BCS API base URL (also reads BCS_API_BASE_URL/MOLTIS_BCS_URL)
    #[arg(short, long, env = "MOLTIS_BCS_URL")]
    url: Option<String>,

    /// Cookie header for authentication (also reads BCS_COOKIE)
    #[arg(short, long, env = "BCS_COOKIE")]
    cookie: Option<String>,

    /// Log level
    #[arg(short, long, default_value = "info")]
    log_level: String,

    /// Output in JSON format (default: enabled). Use --no-json for interactive/human mode.
    #[arg(short, long)]
    json: bool,

    /// Disable JSON output for interactive/human use
    #[arg(long)]
    no_json: bool,

    /// Debug mode - print HTTP request/response details
    #[cfg(debug_assertions)]
    #[arg(short = 'D', long, env = "BCS_DEBUG", action = ArgAction::SetTrue)]
    debug: bool,

    #[command(subcommand)]
    command: Commands,
}

/// Print HTTP request in debug mode
macro_rules! debug_request {
    ($debug:expr, $method:expr, $endpoint:expr, $body:expr) => {
        if $debug {
            eprintln!("\x1b[2m[→BCS] {} {}", $method, $endpoint);
            if !$body.is_null() {
                eprintln!(
                    "    Body: {}",
                    serde_json::to_string(&$body).unwrap_or_default()
                );
            }
            eprintln!("\x1b[0m");
        }
    };
}

/// Print HTTP response in debug mode
macro_rules! debug_response {
    ($debug:expr, $status:expr, $body:expr) => {
        if $debug {
            eprintln!("\x1b[2m[←BCS] Status: {}", $status);
            eprintln!(
                "    {}",
                serde_json::to_string_pretty(&$body).unwrap_or_default()
            );
            eprintln!("\x1b[0m");
        }
    };
}

/// Parse skills input - supports JSON object array, JSON string array, and comma-separated format.
///
/// Examples:
/// - `[{"name":"sql","description":"SQL analysis"}]` → structured Skill objects
/// - `["sql","debug"]` → Skill objects with name only
/// - `"sql, debug"` → Skill objects with name only
fn parse_skills_input(input: &str) -> Vec<bcs_protocol::Skill> {
    if let Ok(value) = serde_json::from_str::<serde_json::Value>(input) {
        if let Some(arr) = value.as_array() {
            let skills: Vec<bcs_protocol::Skill> = arr
                .iter()
                .filter_map(|v| match v {
                    serde_json::Value::String(s) => Some(bcs_protocol::Skill::new(s)),
                    serde_json::Value::Object(_) => serde_json::from_value(v.clone()).ok(),
                    _ => None,
                })
                .collect();
            if !skills.is_empty() {
                return skills;
            }
        }
    }
    input
        .split(',')
        .map(|s| bcs_protocol::Skill::new(s.trim()))
        .filter(|s| !s.name.is_empty())
        .collect()
}

/// Skill→BCS interactive debug
macro_rules! skill_debug_request {
    ($debug:expr, $method:expr, $endpoint:expr, $body:expr) => {
        if $debug {
            eprintln!("[Skill→BCS] {} {}", $method, $endpoint);
            if !$body.is_null() {
                eprintln!("    {}", serde_json::to_string(&$body).unwrap_or_default());
            }
        }
    };
}

/// BCS→Skill response debug
macro_rules! skill_debug_response {
    ($debug:expr, $status:expr, $body:expr) => {
        if $debug {
            eprintln!("[BCS→Skill] Status: {}", $status);
            eprintln!(
                "    {}",
                serde_json::to_string_pretty(&$body).unwrap_or_default()
            );
        }
    };
}

#[derive(Subcommand)]
enum Commands {
    /// Health check for BCS (no authentication required)
    Health,

    /// Connect to BCS network via HTTP (alternative to WebSocket)
    /// Returns a session token for subsequent API calls.
    Connect {
        /// Optional token from previous session for reconnection
        #[arg(short, long)]
        token: Option<String>,

        /// Optional preconfigured bot_id
        #[arg(long)]
        bot_id: Option<String>,
    },

    /// Onboard to the BCS network - register bot details after WebSocket connection
    Onboard {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Bot display name
        #[arg(short = 'n', long)]
        name: String,

        /// Bot capability summary
        #[arg(long)]
        summary: Option<String>,

        /// Skills (comma-separated or JSON array)
        #[arg(short, long)]
        skills: Option<String>,

        /// Domains (comma-separated)
        #[arg(short, long)]
        domains: Option<String>,

        /// Scopes (comma-separated)
        #[arg(long)]
        scopes: Option<String>,

        /// Channel bindings for message routing (JSON format)
        /// Example: '{"antding":{"binding_key":"11111111"},"wechat":{"binding_key":"vid_1294"}}'
        #[arg(long)]
        binding_channels: Option<String>,

        /// Output a web registration URL instead of calling the onboard API directly.
        #[arg(long)]
        web: bool,
    },

    /// List all registered bots
    List {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,
    },

    /// Get a specific bot's info
    Get {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Bot UUID to get info for
        bot_uuid: String,
    },

    /// Discover bots by query
    Discover {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Search query
        #[arg(short, long)]
        query: Option<String>,

        /// Filter by skills (comma-separated)
        #[arg(short, long, hide = true)]
        skills: Option<String>,

        /// Filter by visibility ("public" or "protected")
        #[arg(long)]
        visibility: Option<String>,

        /// Return bots that this bot can collaborate with (public + friends).
        /// Pass a bot_uuid to filter by collaboration eligibility.
        #[arg(long)]
        collaborate_bot: Option<String>,

        /// Organization code for scoped discovery.
        #[arg(long)]
        organization_code: Option<String>,

        /// Organization member role filter. Requires --organization-code.
        #[arg(long)]
        role: Option<String>,
    },

    /// Update bot status
    UpdateStatus {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Status (idle/busy/offline)
        #[arg(short, long)]
        status: String,

        /// Dynamic summary
        #[arg(short = 'm', long)]
        summary: Option<String>,

        /// Load (0.0-1.0)
        #[arg(short, long)]
        load: Option<f32>,
    },

    /// Request group help - create a collaboration proposal
    RequestGroupHelp {
        /// Authentication token (auto-discovered if not provided)
        #[arg(long)]
        token: Option<String>,

        /// Topic for the group collaboration
        #[arg(short, long)]
        topic: String,

        /// Suggested participants (comma-separated)
        #[arg(short, long)]
        participants: Option<String>,

        /// Suggested driver (currently ignored by server; driver is always the requesting bot)
        #[arg(long)]
        driver: Option<String>,
    },

    /// Confirm a group help proposal
    ConfirmGroupHelp {
        /// Confirm URL (full URL with token)
        #[arg(short, long)]
        url: String,
    },

    /// Create a group directly
    CreateGroup {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Group ID (optional, auto-generated if not provided)
        #[arg(short, long, hide = true)]
        id: Option<String>,

        /// Driver bot ID
        #[arg(long)]
        driver: String,

        /// Participants (comma-separated bot UUIDs, e.g. "bot1,20260412_abc:100005")
        #[arg(short, long)]
        participants: String,

        /// Group context (optional description of collaboration goal/background)
        #[arg(long)]
        context: Option<String>,

        /// Group topic (sets the group label)
        #[arg(long)]
        topic: Option<String>,
    },

    /// Get group info
    GetGroup {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Group ID
        #[arg(long)]
        id: String,
    },

    /// Fuse contexts from participants
    Fuse {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Group ID
        #[arg(long)]
        group: String,

        /// Question to fuse for
        #[arg(short, long)]
        question: String,

        /// Participants (comma-separated bot IDs)
        #[arg(short, long)]
        participants: String,

        /// Focus area
        #[arg(short, long)]
        focus: Option<String>,
    },

    /// List groups, optionally limited to groups the current bot participates in
    ListGroups {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// List only groups that include the current bot from the session file
        #[arg(long)]
        mine: bool,
    },

    /// Add a member to an existing group
    AddMember {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Group ID
        #[arg(long)]
        group: String,

        /// Bot UUID to add
        #[arg(long)]
        bot_uuid: String,

        /// Role (driver/consultant)
        #[arg(short, long)]
        role: Option<String>,
    },

    /// Chat with another bot (1:1 message via BCS routing)
    ///
    /// Uses the async submit + long-poll flow so long-running bot tasks
    /// (e.g. multi-step agent reasoning) are not subject to any single HTTP
    /// timeout on the client side.
    #[command(visible_alias = "invoke")]
    Chat {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Target bot UUID (the bot's unique identifier assigned by BCS)
        #[arg(short = 'b', long)]
        bot_uuid: String,

        /// Message to send
        #[arg(short, long)]
        message: String,

        /// Overall wait budget in milliseconds (up to 24 hours).
        ///
        /// Defaults to 30 minutes for the blocking flow, or 60 seconds when
        /// `--detach` is set (the budget for waiting on the bot's first ack).
        #[arg(long, value_parser = clap::value_parser!(u64).range(1..=86_400_000))]
        timeout_ms: Option<u64>,

        /// Optional stable session identifier. When provided, multiple calls
        /// land in the same session on the bot side so context is shared.
        #[arg(long)]
        session_id: Option<String>,

        /// Provider routing tag. Repeat to send multiple tags.
        #[arg(long = "tag", value_name = "TAG")]
        tags: Vec<String>,

        /// Response content mode: full or after-last-tool-call.
        #[arg(long, value_parser = ["full", "after-last-tool-call"], default_value = "after-last-tool-call")]
        response_mode: Option<String>,

        /// Per-poll HTTP wait budget in milliseconds.
        #[arg(long, default_value_t = 15_000u64, hide = true)]
        poll_wait_ms: u64,

        /// Detach after the bot acknowledges the message (first chat event).
        /// The run keeps executing on the server; the CLI returns without
        /// waiting for the full response.
        #[arg(long, default_value_t = false)]
        detach: bool,

        /// Organization code for scoped A2A chat. This is request metadata only.
        #[arg(long)]
        organization_code: Option<String>,
    },

    /// Update group status (coordinator/originator only)
    /// Use this to mark a group as completed or closed.
    GroupStatus {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Group ID
        #[arg(short, long)]
        group: String,

        /// New status (active/completed/closed/inactive)
        #[arg(short, long)]
        status: String,

        /// Optional reason for status change
        #[arg(short, long)]
        reason: Option<String>,
    },

    /// Terminate a group session (driver only)
    /// This marks the group as completed and broadcasts termination to participants.
    TerminateGroup {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        /// Group ID
        #[arg(short, long)]
        group: String,
    },

    /// Manage bot friendships (request, accept, reject, list)
    Friend {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        #[command(subcommand)]
        command: FriendCommands,
    },

    /// Manage channel (IM bridge) bindings
    Channel {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        #[command(subcommand)]
        command: ChannelCommands,
    },

    /// Get or set bot visibility (auto-resolves bot UUID from token)
    Visibility {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        #[command(subcommand)]
        command: VisibilityCommands,
    },

    /// Manage sessions within a group (create, list, get, chat, messages)
    Session {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        #[command(subcommand)]
        command: SessionCommands,
    },

    /// Drive a service-invocation flow on a group with `service_spec` set.
    ///
    /// Requires a bot token. The server records the caller as `bot:<bot_id>`.
    Service {
        /// Authentication token (auto-discovered if not provided)
        #[arg(short, long)]
        token: Option<String>,

        #[command(subcommand)]
        command: ServiceCommands,
    },
}

#[derive(Subcommand)]
enum ChannelCommands {
    /// Bind a DingTalk robot to a group or bot target
    Bind {
        /// DingTalk robot account_ref
        #[arg(long)]
        account: String,

        /// Target kind: group or bot
        #[arg(long, default_value = "group")]
        target_kind: String,

        /// Target group_id or bot_id
        #[arg(long)]
        target_id: String,

        /// DingTalk group scope: conversation_shared or per_sender
        #[arg(long)]
        group_chat_scope: Option<String>,

        /// Outbound visibility: full_transcript or lead_only
        #[arg(long, default_value = "lead_only")]
        visibility: String,

        /// Runtime environment label
        #[arg(long, default_value = "dev")]
        env: String,

        /// DingTalk robotCode
        #[arg(long)]
        robot_code: String,

        /// DingTalk client id
        #[arg(long)]
        client_id: String,

        /// DingTalk client secret
        #[arg(long)]
        client_secret: String,

        /// DingTalk send mode: normal or streaming_card
        #[arg(long, default_value = "normal")]
        send_mode: String,

        /// Required when --send-mode=streaming_card
        #[arg(long)]
        card_template_id: Option<String>,

        /// Message type: markdown or text
        #[arg(long, default_value = "markdown")]
        message_type: String,
    },

    /// List channel bindings
    List,

    /// Delete a channel binding
    Unbind {
        /// Binding id
        #[arg(long)]
        id: String,
    },
}

fn build_channel_bind_payload(command: &ChannelCommands) -> Result<serde_json::Value> {
    let ChannelCommands::Bind {
        account,
        target_kind,
        target_id,
        group_chat_scope,
        visibility,
        env,
        robot_code,
        client_id,
        client_secret,
        send_mode,
        card_template_id,
        message_type,
    } = command else {
        return Err(anyhow!("channel bind payload requires bind command"));
    };

    let target = match target_kind.as_str() {
        "group" => json!({ "group": { "group_id": target_id } }),
        "bot" => json!({ "bot": { "bot_id": target_id } }),
        other => {
            return Err(anyhow!(
                "unsupported target-kind {}; expected group or bot",
                other
            ));
        }
    };

    let send_mode = match send_mode.as_str() {
        "normal" => json!({
            "mode": "normal",
            "message_type": message_type,
        }),
        "streaming_card" => {
            let Some(card_template_id) = card_template_id.as_deref() else {
                return Err(anyhow!(
                    "--card-template-id is required when --send-mode=streaming_card"
                ));
            };
            json!({
                "mode": "streaming_card",
                "card_template_id": card_template_id,
                "fallback_message_type": message_type,
            })
        }
        other => {
            return Err(anyhow!(
                "unsupported send-mode {}; expected normal or streaming_card",
                other
            ));
        }
    };

    let mut payload = json!({
        "channel_type": "ding_talk",
        "account_ref": account,
        "target": target,
        "outbound_visibility": visibility,
        "env": env,
        "config": {
            "channel_type": "ding_talk",
            "robot_code": robot_code,
            "client_id": client_id,
            "client_secret": client_secret,
            "send_mode": send_mode,
        }
    });

    if let Some(group_chat_scope) = group_chat_scope {
        payload["group_chat_scope"] = json!(group_chat_scope);
    }

    Ok(payload)
}

fn redact_channel_bind_debug_payload(payload: &serde_json::Value) -> serde_json::Value {
    let mut redacted = payload.clone();
    if let Some(config) = redacted
        .get_mut("config")
        .and_then(|value| value.as_object_mut())
    {
        if config.contains_key("client_secret") {
            config.insert("client_secret".to_string(), json!("<redacted>"));
        }
    }
    redacted
}

#[derive(Subcommand)]
enum FriendCommands {
    /// Send a friend request to another bot
    Request {
        /// Target bot UUID to send friend request to
        #[arg(long)]
        bot_uuid: String,
    },

    /// Accept a friend request
    Accept {
        /// Friend request ID to accept
        #[arg(long)]
        request_id: String,
    },

    /// Reject a friend request
    Reject {
        /// Friend request ID to reject
        #[arg(long)]
        request_id: String,
    },

    /// List friends of the current bot (auto-resolves from session if not specified)
    List {
        /// Bot UUID (optional, auto-resolved from session if not provided)
        #[arg(long)]
        bot_uuid: Option<String>,
    },

    /// List friend requests (received, sent, or all)
    Requests {
        /// Direction: received (default), sent, all
        #[arg(short, long, default_value = "received")]
        direction: String,

        /// Filter by status: pending, accepted, rejected
        #[arg(short, long)]
        status: Option<String>,
    },
}

#[derive(Subcommand)]
enum VisibilityCommands {
    /// Get current bot's visibility (auto-resolves from session if not specified)
    Get {
        /// Bot UUID (optional, auto-resolved from session if not provided)
        #[arg(long)]
        bot_uuid: Option<String>,
    },

    /// Set current bot's visibility (auto-resolves from session if not specified)
    Set {
        /// Visibility value (public, protected, or private)
        #[arg(long)]
        value: String,

        /// Bot UUID (optional, auto-resolved from session if not provided)
        #[arg(long)]
        bot_uuid: Option<String>,
    },
}

#[derive(Subcommand)]
enum SessionCommands {
    /// Create a new session under a group.
    ///
    /// The server assigns a random session id of the form `{group_id}:{8_hex}`.
    /// Note: this id will not be the same as the legacy fallback session
    /// (`{group_id}:00000000`) that some clients expect; once any session
    /// exists the fallback is no longer auto-created on list.
    Create {
        /// Group ID this session belongs to
        #[arg(long)]
        group: String,

        /// Optional session title (shown in UI)
        #[arg(long)]
        title: Option<String>,

        /// Session kind: chat (default) or service_invocation
        #[arg(long)]
        kind: Option<String>,

        /// Optional input payload (JSON)
        #[arg(long)]
        input: Option<String>,

        /// Optional metadata (JSON)
        #[arg(long)]
        meta: Option<String>,
    },

    /// List sessions under a group.
    List {
        /// Group ID
        #[arg(long)]
        group: String,

        /// Filter by status (running or completed)
        #[arg(long)]
        status: Option<String>,

        /// Search query — substring match against session title
        #[arg(short, long)]
        q: Option<String>,

        /// Filter by participant (bot_uuid or human actor_id)
        #[arg(long)]
        participant: Option<String>,

        /// Pagination offset
        #[arg(long)]
        offset: Option<u64>,

        /// Pagination limit
        #[arg(long)]
        limit: Option<u64>,
    },

    /// Get a single session by id.
    Get {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,
    },

    /// Send a chat message into a session.
    /// Caller is resolved from the bearer token.
    Chat {
        /// Session ID
        #[arg(short, long)]
        session: String,

        /// Message text
        #[arg(short, long)]
        message: String,
    },

    /// Fetch message history for a session.
    Messages {
        /// Session ID
        session: String,

        /// View as a specific bot (filters visibility per participant mode)
        #[arg(long)]
        view_bot: Option<String>,

        /// Limit the number of messages returned
        #[arg(long)]
        limit: Option<u64>,

        /// Return messages with timestamp strictly less than this (Unix ms)
        #[arg(long)]
        before: Option<u64>,
    },

    /// Update session title.
    Patch {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,

        /// New session title
        #[arg(long)]
        title: String,
    },

    /// Complete a running chat session (driver-only).
    ///
    /// ServiceInvocation sessions are rejected; use `service` commands instead.
    Complete {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,

        /// Output payload (JSON literal or @path/to/file.json)
        #[arg(long)]
        output: Option<String>,

        /// Error message (marks the session as failed)
        #[arg(long)]
        error: Option<String>,
    },

    /// Add a bot participant to a session.
    AddMember {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,

        /// Bot UUID to add
        #[arg(long)]
        bot_uuid: String,

        /// Role: driver, consultant, observer, manager, worker
        #[arg(short, long)]
        role: Option<String>,
    },

    /// Remove a participant from a session.
    RemoveMember {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,

        /// Bot UUID (or human actor_id) to remove
        bot_uuid: String,
    },

    /// Update a participant's mode in a session.
    ///
    /// Valid modes: auto, muted, present, absent.
    /// For human actors not yet in the session, the server auto-adds them
    /// as Observer before applying the mode.
    SetMemberMode {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,

        /// Bot UUID (or human actor_id) to update
        bot_uuid: String,

        /// New mode: auto, muted, present, absent
        #[arg(long)]
        mode: String,
    },

    /// Create an invite link for a session.
    ///
    /// Returns a short-lived token that allows human users to join the session.
    InviteLink {
        /// Session ID (format: {group_id}:{8_hex})
        session: String,

        /// Link expiration time in seconds
        #[arg(long, value_name = "TTL")]
        ttl_seconds: Option<u64>,
    },
}

#[derive(Subcommand)]
enum ServiceCommands {
    /// Kick off (or reactivate) a service-invocation session on a group.
    ///
    /// The group must have a `service_spec` set (otherwise the server
    /// returns 400). By default this command short-polls until the session
    /// reaches `status="completed"` or the timeout fires; pass `--detach`
    /// to return immediately after the 202 with the session id.
    Invoke {
        /// Target group id (must have `service_spec` configured)
        #[arg(short = 'g', long)]
        group: String,

        /// Input payload as a JSON literal or `@path/to/file.json`
        #[arg(long)]
        input: Option<String>,

        /// Metadata as a JSON literal or `@path/to/file.json`
        #[arg(long)]
        meta: Option<String>,

        /// Reactivate an existing session id instead of allocating a new one
        #[arg(long)]
        session_id: Option<String>,

        /// BaaS conversation session id for callback delivery
        #[arg(long)]
        baas_session_id: Option<String>,

        /// Opaque caller-supplied id (recorded on the session row)
        #[arg(long)]
        caller_id: Option<String>,

        /// Session title (shown in UI)
        #[arg(long)]
        title: Option<String>,

        /// Return after the 202 instead of polling for completion
        #[arg(long, default_value_t = false)]
        detach: bool,

        /// Overall wait budget in milliseconds when blocking (default 30 min)
        #[arg(long, value_parser = clap::value_parser!(u64).range(1..=86_400_000))]
        timeout_ms: Option<u64>,
    },

    /// Single-shot poll for a service-invocation session.
    Status {
        /// Session id of the form `{group}:{8_hex}`
        sid: String,

        /// Override the group id (defaults to the prefix of `sid` before ':')
        #[arg(long)]
        group: Option<String>,
    },

    /// Block until a service-invocation session completes (or times out).
    Wait {
        /// Session id of the form `{group}:{8_hex}`
        sid: String,

        /// Override the group id (defaults to the prefix of `sid` before ':')
        #[arg(long)]
        group: Option<String>,

        /// Overall wait budget in milliseconds (default 30 min)
        #[arg(long, value_parser = clap::value_parser!(u64).range(1..=86_400_000))]
        timeout_ms: Option<u64>,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    // Setup logging
    let level = match cli.log_level.to_lowercase().as_str() {
        "trace" => Level::TRACE,
        "debug" => Level::DEBUG,
        "info" => Level::INFO,
        "warn" => Level::WARN,
        "error" => Level::ERROR,
        _ => Level::INFO,
    };
    let structured_mode = is_structured_mode(&cli);
    let log_file = build_log_file_path();

    // Signal structured mode to oauth.rs (which can't access the CLI struct)
    if structured_mode {
        oauth::set_structured_mode(true);
    }

    // Always write logs to file regardless of --no-json flag
    if let Some(parent) = log_file.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_file)?;
    FmtSubscriber::builder()
        .with_max_level(level)
        .with_target(false)
        .with_writer(std::sync::Mutex::new(file))
        .compact()
        .init();

    // Get current environment (defaults to "dev")
    let env = get_current_env();

    // Determine BCS URL: CLI arg > env var > session.json > default
    // Note: bcs_url resolution is deferred for commands that don't need it (e.g., --web mode).
    let bcs_url = resolve_bcs_url(&cli).unwrap_or_default();

    // Determine cookie: CLI arg > env var
    let bcs_cookie = cli
        .cookie
        .or_else(|| std::env::var("BCS_COOKIE").ok());

    // Debug mode: print all HTTP communications (only available in debug builds)
    #[cfg(debug_assertions)]
    let debug = cli.debug || std::env::var("BCS_DEBUG").is_ok_and(|v| v == "true");
    #[cfg(not(debug_assertions))]
    let debug = false;
    if debug {
        eprintln!(
            "\x1b[2m[→BCS] Environment: {} | BCS URL: {}\x1b[0m",
            env, bcs_url
        );
        if bcs_cookie.is_some() {
            eprintln!("\x1b[2m[→BCS] Cookie: (set)\x1b[0m");
        }
    }

    // Resolve network environment and OAuth headers.
    // Priority: tc_sdb_nenv env var > auto-detect via health probe.
    // Skip network probing when bcs_url is empty (e.g., --web mode doesn't need BCS API).
    let (_network_env, oauth_headers): (NetworkEnv, Option<HashMap<String, String>>) = if bcs_url
        .is_empty()
    {
        (NetworkEnv::Prod, None)
    } else {
        let env = match std::env::var("tc_sdb_nenv").ok().as_deref() {
            Some("production") => NetworkEnv::Prod,
            Some(_) => NetworkEnv::Office,
            None => detect_network_env(&bcs_url, structured_mode).await,
        };
        if debug {
            eprintln!("\x1b[2m[→BCS] Network: {:?}\x1b[0m", env);
        }

        let headers = if env == NetworkEnv::Office {
            if AUTH_VIA_AGENT_PASS {
                let err_msg = if let Ok(bot_data_dir) = std::env::var("BOT_DATA_DIR") {
                    let bot_data_path = Path::new(&bot_data_dir);

                    if let Ok(Some(session)) = load_optional_session_info() {
                        if let Some(ref bot_uuid) = session.bot_uuid {
                            if !session.token.is_empty() && !bot_uuid.is_empty() {
                                let summary = agentpass::load_bot_summary(bot_data_path);

                                info!("尝试 AgentPass 注册，bot_uuid: {}", bot_uuid);

                                match agentpass::try_register_and_auth(
                                    &session.token,
                                    bot_uuid,
                                    &summary,
                                    structured_mode,
                                )
                                .await
                                {
                                    Ok(()) => {
                                        info!("AgentPass 注册流程完成");
                                        None
                                    }
                                    Err(e) => Some(format!("AgentPass 注册失败: {}", e)),
                                }
                            } else {
                                Some(
                                    "AgentPass auth failed: token or bot_uuid is empty".to_string(),
                                )
                            }
                        } else {
                            Some("AgentPass auth failed: session has no bot_uuid".to_string())
                        }
                    } else {
                        Some("AgentPass auth failed: no session info found (check BOT_DATA_DIR/.bcs/session.json)".to_string())
                    }
                } else {
                    Some("AgentPass auth failed: BOT_DATA_DIR not set".to_string())
                };

                if let Some(msg) = err_msg {
                    if structured_mode {
                        emit_structured_result(&StructuredResult {
                            status: "agentpass_auth_failed".to_string(),
                            message: Some(msg),
                            network_env: Some("office".to_string()),
                            auth_url: None,
                            timeout_secs: None,
                            log_file: Some(log_file.display().to_string()),
                        });
                        return Ok(());
                    } else {
                        return Err(anyhow!(msg));
                    }
                }

                info!("auth_via_agent_pass enabled, skipping OAuth");
                None
            } else {
                info!("Office network detected, obtaining OAuth2 authentication...");
                let (client_id, client_secret) = resolve_oauth_credentials();
                let log_file_str = log_file.display().to_string();
                let on_auth_required: Option<oauth::AuthRequiredCallback> = if structured_mode {
                    Some(Box::new(move |auth_url: &str| {
                        emit_structured_result(&StructuredResult {
                            status: "auth_required".to_string(),
                            message: Some("OAuth2 authorization required. Waiting for browser authorization...".to_string()),
                            network_env: Some("office".to_string()),
                            auth_url: Some(auth_url.to_string()),
                            timeout_secs: Some(120),
                            log_file: Some(log_file_str),
                        });
                        use std::io::Write;
                        let _ = std::io::stdout().flush();
                    }))
                } else {
                    None
                };
                match oauth::try_get_oauth_headers(client_id, client_secret, on_auth_required).await
                {
                    Ok(headers) => Some(headers),
                    Err(e) => {
                        if structured_mode {
                            emit_structured_result(&StructuredResult {
                                status: classify_auth_error_message(&e.message).to_string(),
                                message: Some(e.message),
                                network_env: Some("office".to_string()),
                                auth_url: e.auth_url,
                                timeout_secs: Some(120),
                                log_file: Some(log_file.display().to_string()),
                            });
                            return Ok(());
                        }
                        return Err(e.into());
                    }
                }
            }
        } else {
            None
        };
        (env, headers)
    };

    /// Create a BCS client with optional cookie and OAuth headers.
    /// An empty `token` means no bot token is available; the client is built
    /// without Bearer auth (cookie / OAuth headers, if any, still apply).
    fn create_client(
        bcs_url: &str,
        token: &str,
        cookie: Option<&str>,
        oauth_headers: Option<&HashMap<String, String>>,
    ) -> BcsClient {
        let mut client = if let Some(oauth_headers) = oauth_headers {
            let mut client = if token.is_empty() {
                let mut c = BcsClient::new(bcs_url);
                c.set_oauth_headers(oauth_headers.clone());
                c
            } else {
                BcsClient::with_token_and_oauth(bcs_url, token, oauth_headers.clone())
            };
            if let Some(cookie) = cookie {
                client.set_cookie(cookie);
            }
            client
        } else if token.is_empty() {
            let mut client = BcsClient::new(bcs_url);
            if let Some(cookie) = cookie {
                client.set_cookie(cookie);
            }
            client
        } else if let Some(cookie) = cookie {
            BcsClient::with_token_and_cookie(bcs_url, token, cookie)
        } else {
            BcsClient::with_token(bcs_url, token)
        };
        client.set_client_identity(format!("bcs-cli/{}", env!("CARGO_PKG_VERSION")));
        client
    }

    match cli.command {
        Commands::Health => {
            let mut client = BcsClient::new(&bcs_url);
            if let Some(ref cookie) = bcs_cookie {
                client.set_cookie(cookie);
            }
            if let Some(ref headers) = oauth_headers {
                client.set_oauth_headers(headers.clone());
            }
            let healthy = match client.health_check().await {
                Ok(h) => h,
                Err(e) => {
                    // Under structured_mode, a network/connection error must
                    // surface as a structured JSON result (honoring the output
                    // contract), not a raw traceback on stderr. Human mode
                    // propagates the error unchanged.
                    if structured_mode {
                        let result = StructuredResult {
                            status: "unhealthy".to_string(),
                            message: Some(format!("BCS health check failed: {}", e)),
                            ..Default::default()
                        };
                        emit_structured_result(&result);
                        std::process::exit(1);
                    } else {
                        return Err(e);
                    }
                }
            };
            if structured_mode {
                let result = StructuredResult {
                    status: if healthy { "healthy".to_string() } else { "unhealthy".to_string() },
                    message: Some(format!("BCS is {} at {}", if healthy { "healthy" } else { "unhealthy" }, bcs_url)),
                    ..Default::default()
                };
                emit_structured_result(&result);
                if !healthy {
                    std::process::exit(1);
                }
            } else {
                if healthy {
                    println!("✓ BCS is healthy at {}", bcs_url);
                } else {
                    println!("✗ BCS health check failed");
                    std::process::exit(1);
                }
            }
        }

        Commands::Connect { token, bot_id } => {
            let mut client = BcsClient::new(&bcs_url);
            if let Some(ref cookie) = bcs_cookie {
                client.set_cookie(cookie);
            }
            if let Some(ref headers) = oauth_headers {
                client.set_oauth_headers(headers.clone());
            }

            // Auto-discover existing session token to avoid duplicate registration.
            // Priority: explicit --token > BCN_BOT_TOKEN env > session.json
            let resolved_token =
                token
                    .or_else(|| {
                        std::env::var("BCN_BOT_TOKEN")
                            .ok()
                            .filter(|t| !t.is_empty())
                    })
                    .or_else(|| {
                        get_optional_session_file_path()
                        .and_then(|p| load_session_info_from_path(&p).ok().flatten())
                        .and_then(|s| if s.token.is_empty() { None } else {
                            info!("Found existing session token from session file, will reconnect");
                            Some(s.token)
                        })
                    });

            // If we found a session token and no bot_id override, warn the user
            // that this bot is already registered (will reconnect instead of creating new).
            let already_registered = resolved_token.is_some() && bot_id.is_none();

            let params = BotConnectParams {
                token: resolved_token,
                bot_id,
                protocol_version: Some(BCS_PROTOCOL_VERSION),
                client_kind: None,
            };

            debug_request!(
                debug,
                "POST",
                "/bots/connect",
                json!({
                    "token": params.token.as_ref().map(|_| "***"),
                    "bot_id": &params.bot_id
                })
            );

            let result = client.connect(params).await?;

            // Save session.json for subsequent commands
            {
                let session_bcs_url =
                    normalize_bcs_ws_url(&bcs_url).unwrap_or_else(|| bcs_url.clone());
                let session = SessionInfo {
                    bot_uuid: Some(result.bot_uuid.clone()),
                    token: result.token.clone(),
                    bcs_url: Some(session_bcs_url),
                    api_base_url: Some(bcs_url.clone()),
                };
                let session_path = get_optional_session_file_path();
                if let Some(path) = session_path {
                    if let Some(parent) = path.parent() {
                        let _ = std::fs::create_dir_all(parent);
                    }
                    if let Err(e) = std::fs::write(
                        &path,
                        serde_json::to_string_pretty(&session).unwrap_or_default(),
                    ) {
                        eprintln!("Warning: failed to save session to {:?}: {}", path, e);
                    } else {
                        debug!("Session saved to {:?}", path);
                    }
                }
            }

            debug_response!(
                debug,
                "200",
                json!({
                    "is_new": result.is_new,
                    "bot_uuid": &result.bot_uuid
                })
            );

            if structured_mode {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                if already_registered && !result.is_new {
                    println!("ℹ Bot already registered, reconnecting:");
                } else if result.is_new {
                    println!("✓ New bot connected to BCS network:");
                } else {
                    println!("✓ Bot reconnected to BCS network:");
                }
                println!("  Bot UUID: {}", result.bot_uuid);
                println!("  Token: {}...", &result.token[..8.min(result.token.len())]);
                if result.is_new {
                    println!("\n  Save this token for reconnection!");
                }
            }
        }

        Commands::Onboard {
            token,
            name,
            summary,
            skills,
            domains,
            scopes,
            binding_channels,
            web,
        } => {
            if web {
                let token = get_token(token.as_deref())?;

                let skills_vec = skills
                    .as_deref()
                    .map(parse_skills_input)
                    .unwrap_or_default();

                let domains_vec = domains.as_deref().map(|d| {
                    d.split(',')
                        .map(|dom| dom.trim().to_string())
                        .collect::<Vec<_>>()
                });
                let scopes_vec = scopes.as_deref().map(|s| {
                    s.split(',')
                        .map(|sc| sc.trim().to_string())
                        .collect::<Vec<_>>()
                });

                // The onboard URL is generated by the BCS server.
                let client = create_client(
                    &bcs_url,
                    &token,
                    bcs_cookie.as_deref(),
                    oauth_headers.as_ref(),
                );

                let server_url = client
                    .get_onboard_url(
                        &token,
                        &name,
                        summary.as_deref(),
                        Some(&skills_vec),
                        domains_vec.as_deref(),
                        scopes_vec.as_deref(),
                        None,
                    )
                    .await;

                let url = match server_url {
                    Ok(Some(url)) => url,
                    Ok(None) => {
                        // Server does not support onboard URL generation (404) or is
                        // unreachable. The URL must come from the server — fail clearly.
                        return Err(anyhow!(
                            "BCS server does not support onboard URL generation. \
                             Please upgrade the BCS server to a version that provides the \
                             onboard URL endpoint."
                        ));
                    }
                    Err(err) => {
                        // Server returned 400 (config error) or other failure → surface the error
                        return Err(err);
                    }
                };

                if url.len() > 2000 {
                    eprintln!(
                        "Warning: Registration URL is very long ({} chars). Some browsers may truncate it.",
                        url.len()
                    );
                }

                if cli.json {
                    println!("{}", json!({"url": url}));
                } else {
                    println!("Registration URL:");
                    println!("{}", url);
                }
            } else {
                // Direct API mode
                let token = get_token(token.as_deref())?;
                let client = create_client(
                    &bcs_url,
                    &token,
                    bcs_cookie.as_deref(),
                    oauth_headers.as_ref(),
                );

                let skills_vec = skills
                    .as_deref()
                    .map(parse_skills_input)
                    .unwrap_or_default();
                let domains_vec = domains
                    .as_deref()
                    .map(|d| d.split(',').map(|dom| dom.trim().to_string()).collect())
                    .unwrap_or_default();
                let scopes_vec = scopes
                    .as_deref()
                    .map(|s| s.split(',').map(|sc| sc.trim().to_string()).collect())
                    .unwrap_or_default();

                // Parse binding_channels JSON
                let binding_channels_parsed: Option<bcs_protocol::BindingChannels> =
                    binding_channels
                        .as_deref()
                        .map(|s| serde_json::from_str(s))
                        .transpose()
                        .map_err(|e| anyhow!("Invalid binding_channels JSON: {}", e))?;

                debug_request!(
                    debug,
                    "POST",
                    "/bots/onboard",
                    json!({
                        "name": &name,
                        "summary": &summary,
                        "skills": &skills_vec,
                        "domains": &domains_vec,
                        "scopes": &scopes_vec,
                        "binding_channels": &binding_channels_parsed
                    })
                );

                let result = client
                    .onboard(
                        &name,
                        summary.as_deref(),
                        Some(skills_vec),
                        Some(domains_vec),
                        Some(scopes_vec),
                        binding_channels_parsed,
                    )
                    .await?;

                debug_response!(
                    debug,
                    "200",
                    json!({
                        "bot_id": &result.bot_uuid,
                        "onboarded": result.onboarded,
                        "name": &result.name,
                        "binding_results": &result.binding_results,
                        "unbound": &result.unbound
                    })
                );

                if cli.json {
                    println!("{}", serde_json::to_string(&result)?);
                } else {
                    println!("✓ Bot onboarded to BCS network:");
                    println!("  Bot ID: {}", result.bot_uuid);
                    println!("  Name: {}", result.name);

                    // Show binding results
                    if !result.binding_results.is_empty() {
                        println!("  Binding results:");
                        for (channel, res) in &result.binding_results {
                            let status = res
                                .get("status")
                                .and_then(|s| s.as_str())
                                .unwrap_or("unknown");
                            if status == "success" {
                                println!("    ✓ {}: {}", channel, status);
                            } else {
                                let msg = res.get("message").and_then(|m| m.as_str()).unwrap_or("");
                                println!("    ✗ {}: {} - {}", channel, status, msg);
                            }
                        }
                    }

                    // Show unbound channels
                    if !result.unbound.is_empty() {
                        println!("  Unbound channels:");
                        for ub in &result.unbound {
                            println!("    - {}", ub);
                        }
                    }
                }
            }
        }

        Commands::List { token } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(debug, "GET", "/bots", json!({}));

            let bots = client.list_bots().await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "count": bots.len()
                })
            );

            println!("Bots in network ({}):", bots.len());
            for bot in bots {
                println!(
                    "  - {} ({})",
                    bot.bot_uuid,
                    bot.capabilities.name.as_deref().unwrap_or("unnamed")
                );
                if let Some(summary) = &bot.capabilities.summary {
                    println!("    {}", summary);
                }
            }
        }

        Commands::Get { token, bot_uuid } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(debug, "GET", &format!("/bots/{}", &bot_uuid), json!({}));

            let bot = client.get_bot(&bot_uuid).await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "bot_id": &bot.bot_uuid,
                    "capabilities": &bot.capabilities
                })
            );

            println!("Bot: {}", bot.bot_uuid);
            if let Some(name) = &bot.capabilities.name {
                println!("  Name: {}", name);
            }
            if let Some(summary) = &bot.capabilities.summary {
                println!("  Summary: {}", summary);
            }
            if !bot.capabilities.skills.is_empty() {
                println!(
                    "  Skills: {}",
                    bot.capabilities
                        .skills
                        .iter()
                        .map(|s| s.name.as_str())
                        .collect::<Vec<_>>()
                        .join(", ")
                );
            }
            if !bot.capabilities.domains.is_empty() {
                println!("  Domains: {}", bot.capabilities.domains.join(", "));
            }
            println!(
                "  Visibility: {}",
                if bot.capabilities.visibility.is_empty() {
                    "protected"
                } else {
                    &bot.capabilities.visibility
                }
            );
        }

        Commands::Discover {
            token,
            query,
            skills: _,
            visibility,
            collaborate_bot,
            organization_code,
            role,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(
                debug,
                "GET",
                "/bots/discover",
                json!({
                    "q": &query,
                    "visibility": &visibility,
                    "collaborate_bot": &collaborate_bot,
                    "organization_code": &organization_code,
                    "role": &role,
                })
            );

            let result = client
                .discover_bots_extended(
                    query.as_deref(),
                    visibility.as_deref(),
                    collaborate_bot.as_deref(),
                    organization_code.as_deref(),
                    role.as_deref(),
                )
                .await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "count": result.count
                })
            );

            println!("Discovered {} bots:", result.count);
            for bot in &result.bots {
                let name = bot.capabilities.name.as_deref().unwrap_or("unnamed");
                let vis = &bot.visibility;
                let friend_tag = match bot.is_friend {
                    Some(true) => " ★friend",
                    Some(false) => "",
                    None => "",
                };
                let provider_tag = bot
                    .provider_info
                    .as_ref()
                    .map(|provider| {
                        format!(
                            " provider={}/{}",
                            provider.provider_name, provider.provider_id
                        )
                    })
                    .unwrap_or_default();
                let agent_code_tag = bot
                    .agent_code
                    .as_ref()
                    .map(|agent_code| format!(" agent_code={agent_code}"))
                    .unwrap_or_default();
                println!(
                    "  - {} ({}) [{}]{}{}{}",
                    bot.bot_uuid, name, vis, friend_tag, provider_tag, agent_code_tag
                );
            }
        }

        Commands::UpdateStatus {
            token,
            status,
            summary,
            load,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            let dynamic_status = bcs_protocol::BotDynamicStatus {
                status,
                dynamic_summary: summary,
                load,
                updated_at: Some(
                    std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)?
                        .as_millis() as u64,
                ),
            };

            debug_request!(
                debug,
                "POST",
                "/bots/status",
                json!({
                    "status": &dynamic_status
                })
            );

            let result = client.update_status_with_token(dynamic_status).await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "updated": result.updated
                })
            );

            if result.updated {
                println!("✓ Status updated");
            } else {
                println!("✗ Failed to update status");
            }
        }

        Commands::RequestGroupHelp {
            token,
            topic,
            participants,
            driver,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            let suggested_participants: Option<Vec<String>> =
                participants.map(|p| p.split(',').map(|s| s.trim().to_string()).collect());

            skill_debug_request!(
                debug,
                "POST",
                "/groups/request",
                json!({
                    "topic": &topic,
                    "suggested_participants": &suggested_participants,
                    "driver": &driver
                })
            );

            let result = client
                .propose_group_chat_with_token(&topic, suggested_participants, driver.as_deref())
                .await?;

            skill_debug_response!(
                debug,
                "200",
                json!({
                    "mode": &result.mode,
                    "driver_bot": &result.driver_bot,
                    "participants": &result.participants,
                    "confirm_url": &result.confirm_url
                })
            );

            if cli.json {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                println!("Proposal created:");
                println!("  Mode: {}", result.mode);
                println!("  Driver: {}", result.driver_bot);
                println!("  Participants: {}", result.participants.join(", "));
                println!("  Confirm URL: {}", result.confirm_url);
                println!("  Message: {}", result.message);
            }
        }

        Commands::ConfirmGroupHelp { url } => {
            // Confirm URL contains its own token, so we don't need the auth token
            let mut client = BcsClient::new(&bcs_url);
            if let Some(ref cookie) = bcs_cookie {
                client.set_cookie(cookie);
            }

            skill_debug_request!(debug, "POST", &url, json!({}));

            let result = client.confirm_proposal(&url).await?;

            skill_debug_response!(
                debug,
                "200",
                json!({
                    "group_id": &result.group_id,
                    "mode": &result.mode,
                    "driver_bot": &result.driver_bot,
                    "participants": &result.participants,
                    "chat_url": &result.chat_url
                })
            );

            if cli.json {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                println!("Group created:");
                println!("  ID: {}", result.group_id);
                if let Some(mode) = &result.mode {
                    println!("  Mode: {}", mode);
                }
                println!("  Driver: {}", result.driver_bot);
                println!("  Participants: {}", result.participants.join(", "));
                if let Some(ref chat_url) = result.chat_url {
                    println!("  Chat URL: {}", chat_url);
                }
            }
        }

        Commands::CreateGroup {
            token,
            id: _,
            driver,
            participants,
            context,
            topic,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            // Format: bot_uuid (may contain colons, e.g. "20260412_347nf7bz:100005")
            // Comma-separated for multiple participants.
            // Consistent with request-group-help which also passes bot_uuid as-is.
            let participants: Vec<bcs_protocol::ParticipantInfo> = participants
                .split(',')
                .map(|p| bcs_protocol::ParticipantInfo {
                    bot_uuid: p.trim().to_string(),
                    role: None,
                })
                .collect();

            debug_request!(
                debug,
                "POST",
                "/groups",
                json!({
                    "driver_bot": &driver,
                    "participants": &participants
                })
            );

            let result = client
                .create_group_with_context(
                    &driver,
                    participants,
                    context.as_deref(),
                    topic.as_deref(),
                )
                .await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "id": &result.id,
                    "driver_bot": &result.driver_bot,
                    "participants": &result.participants
                })
            );

            // Surface the session the server auto-creates as part of group
            // creation (see commit ddd6ca7b4 — group_management always seeds
            // a "新会话"). Best-effort: a failed lookup must NOT unwind the
            // successful group creation.
            let auto_session: Option<serde_json::Value> = {
                debug_request!(
                    debug,
                    "GET",
                    &format!("/groups/{}/sessions?limit=1", &result.id),
                    json!({})
                );
                match client
                    .list_sessions(&result.id, None, None, None, None, Some(1))
                    .await
                {
                    Ok(v) => {
                        debug_response!(debug, "200", &v);
                        v.get("items")
                            .and_then(|x| x.as_array())
                            .and_then(|arr| arr.first())
                            .cloned()
                    }
                    Err(e) => {
                        eprintln!(
                            "Warning: group {} created but session lookup failed: {}",
                            result.id, e
                        );
                        None
                    }
                }
            };

            println!("Group created:");
            println!("  ID: {}", result.id);
            println!("  Driver: {}", result.driver_bot);
            println!("  Participants: {}", result.participants.join(", "));
            if let Some(chat_url) = &result.chat_url {
                println!("  Chat URL: {}", chat_url);
            }
            if let Some(sess) = auto_session {
                let sid = sess
                    .get("session_id")
                    .or_else(|| sess.get("id"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("?");
                println!("  Session: {}", sid);
            }
        }

        Commands::GetGroup { token, id } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(debug, "GET", &format!("/groups/{}", &id), json!({}));

            let group = client.get_group(&id).await?;

            debug_response!(debug, "200", &group);

            if cli.json {
                println!("{}", serde_json::to_string(&group)?);
            } else {
                println!("Group: {}", serde_json::to_string_pretty(&group)?);
            }
        }

        Commands::Fuse {
            token,
            group,
            question,
            participants,
            focus,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            let participants: Vec<String> = participants
                .split(',')
                .map(|s| s.trim().to_string())
                .collect();

            debug_request!(
                debug,
                "POST",
                &format!("/groups/{}/fuse", &group),
                json!({
                    "question": &question,
                    "participants": &participants,
                    "focus": &focus
                })
            );

            let result = client
                .fuse_context_with_focus(&group, &question, participants, focus.as_deref())
                .await?;

            debug_response!(
                debug,
                "200",
                json!({
                    "perspectives": &result.perspectives.len(),
                    "conflicts": &result.conflicts.len(),
                    "recommendation": &result.recommendation
                })
            );

            println!("Fusion result:");
            println!("{}", serde_json::to_string_pretty(&result)?);
        }

        Commands::ListGroups { token, mine } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            let (groups, current_bot_uuid) = if mine {
                let bot_uuid = resolve_my_bot_uuid()?;
                debug_request!(
                    debug,
                    "GET",
                    &format!("/bots/{}/groups", &bot_uuid),
                    json!({})
                );
                let groups = client.list_bot_groups(&bot_uuid).await?;
                (groups, Some(bot_uuid))
            } else {
                debug_request!(debug, "GET", "/groups", json!({}));
                (client.list_groups().await?, None)
            };

            debug_response!(
                debug,
                "200",
                json!({
                    "count": groups.len()
                })
            );

            if let Some(bot_uuid) = current_bot_uuid {
                println!("Groups for current bot {} ({}):", bot_uuid, groups.len());
            } else {
                println!("Groups ({}):", groups.len());
            }
            for group in groups {
                let id = group
                    .get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown");
                let mode = group
                    .get("mode")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown");
                let driver = group
                    .get("driver_bot")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown");
                println!("  - {} [{}] driver={}", id, mode, driver);
            }
        }

        Commands::AddMember {
            token,
            group,
            bot_uuid,
            role,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(
                debug,
                "POST",
                &format!("/groups/{}/members", &group),
                json!({
                    "bot_id": &bot_uuid,
                    "role": role.as_deref().unwrap_or("consultant")
                })
            );

            let result = client
                .add_group_member(&group, &bot_uuid, role.as_deref())
                .await?;

            debug_response!(debug, "200", &result);

            println!("✓ Member added to group:");
            println!("  Group: {}", group);
            println!("  Bot: {}", bot_uuid);
            println!("  Role: {}", role.as_deref().unwrap_or("consultant"));
        }

        Commands::Chat {
            token,
            bot_uuid,
            message,
            timeout_ms,
            session_id,
            tags,
            response_mode,
            poll_wait_ms,
            detach,
            organization_code,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            let effective_timeout_ms = timeout_ms.unwrap_or(if detach {
                60_000
            } else {
                1_800_000
            });

            debug_request!(
                debug,
                "POST",
                &format!("/bots/{}/chat-async", &bot_uuid),
                json!({
                    "message": &message,
                    "from": serde_json::Value::Null,
                    "timeout_ms": effective_timeout_ms,
                    "session_id": &session_id,
                    "tags": &tags,
                    "response_mode": &response_mode,
                    "poll_wait_ms": poll_wait_ms,
                    "detach": detach,
                    "organization_code": &organization_code,
                })
            );
            let result = if detach {
                client
                    .chat_polling_detach(
                        &bot_uuid,
                        &message,
                        None,
                        session_id.as_deref(),
                        &tags,
                        response_mode.as_deref(),
                        Some(effective_timeout_ms),
                        Some(poll_wait_ms),
                        organization_code.as_deref(),
                    )
                    .await?
            } else {
                client
                    .chat_polling(
                        &bot_uuid,
                        &message,
                        None,
                        session_id.as_deref(),
                        &tags,
                        response_mode.as_deref(),
                        Some(effective_timeout_ms),
                        Some(poll_wait_ms),
                        organization_code.as_deref(),
                    )
                    .await?
            };

            debug_response!(debug, "200", &result);

            if cli.json {
                println!("{}", serde_json::to_string(&result)?);
            } else if detach {
                println!("Message submitted to {}", bot_uuid);
                if let Some(rid) = result.get("run_id").and_then(|v| v.as_str()) {
                    println!("Run: {}", rid);
                }
                if let Some(sid) = result.get("session_id").and_then(|v| v.as_str()) {
                    println!("Session: {}", sid);
                }
                if let Some(state) = result.get("state").and_then(|v| v.as_str()) {
                    println!("State: {}", state);
                }
            } else {
                if let Some(response) = result.get("response") {
                    println!("Response from {}:", bot_uuid);
                    println!("{}", serde_json::to_string_pretty(response)?);
                } else {
                    println!("Result: {}", serde_json::to_string_pretty(&result)?);
                }
                if let Some(sid) = result.get("session_id").and_then(|v| v.as_str()) {
                    println!("Session: {}", sid);
                }
            }
        }

        Commands::GroupStatus {
            token,
            group,
            status,
            reason,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(
                debug,
                "PUT",
                &format!("/groups/{}/status", &group),
                json!({
                    "status": &status,
                    "reason": &reason
                })
            );

            let result = client
                .update_group_status(&group, &status, reason.as_deref())
                .await?;

            debug_response!(debug, "200", &result);

            if cli.json {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                println!("✓ Group status updated:");
                println!("  Group: {}", group);
                println!("  Status: {}", status);
                if let Some(r) = reason {
                    println!("  Reason: {}", r);
                }
            }
        }

        Commands::TerminateGroup { token, group } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            debug_request!(
                debug,
                "POST",
                &format!("/groups/{}/terminate", &group),
                json!({})
            );

            let result = client.terminate_group(&group).await?;

            debug_response!(debug, "200", &result);

            if cli.json {
                println!("{}", serde_json::to_string(&result)?);
            } else {
                println!("✓ Group terminated:");
                println!("  Group: {}", group);
                println!("  Status: completed");
            }
        }

        Commands::Friend { token, command } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            match command {
                FriendCommands::Request { bot_uuid } => {
                    debug_request!(
                        debug,
                        "POST",
                        "/friends/request",
                        json!({ "to_bot": &bot_uuid })
                    );

                    let result = client.send_friend_request(None, &bot_uuid).await?;

                    if cli.json {
                        println!(
                            "{}",
                            serde_json::to_string(&json!({
                                "success": result.success,
                                "data": result.data,
                                "message": result.message,
                            }))?
                        );
                    } else if result.success {
                        if let Some(ref msg) = result.message {
                            println!("✓ {}", msg);
                        } else {
                            println!("✓ Friend request sent to {}", bot_uuid);
                            if let Some(ref data) = result.data {
                                if let Some(id) = data.get("id").and_then(|v| v.as_str()) {
                                    println!("  Request ID: {}", id);
                                }
                            }
                        }
                    } else {
                        println!("✗ Failed: {}", result.error.unwrap_or_default());
                    }
                }

                FriendCommands::Accept { request_id } => {
                    debug_request!(
                        debug,
                        "POST",
                        &format!("/friends/requests/{}/accept", &request_id),
                        json!({})
                    );

                    let result = client.accept_friend_request(&request_id).await?;

                    if cli.json {
                        println!(
                            "{}",
                            serde_json::to_string(&json!({ "success": result.success }))?
                        );
                    } else if result.success {
                        println!("✓ Friend request accepted");
                    } else {
                        println!("✗ Failed: {}", result.error.unwrap_or_default());
                    }
                }

                FriendCommands::Reject { request_id } => {
                    debug_request!(
                        debug,
                        "POST",
                        &format!("/friends/requests/{}/reject", &request_id),
                        json!({})
                    );

                    let result = client.reject_friend_request(&request_id).await?;

                    if cli.json {
                        println!(
                            "{}",
                            serde_json::to_string(&json!({ "success": result.success }))?
                        );
                    } else if result.success {
                        println!("✓ Friend request rejected");
                    } else {
                        println!("✗ Failed: {}", result.error.unwrap_or_default());
                    }
                }

                FriendCommands::List { bot_uuid } => {
                    let my_bot_uuid = match bot_uuid {
                        Some(id) => id,
                        None => resolve_my_bot_uuid()?,
                    };

                    debug_request!(
                        debug,
                        "GET",
                        &format!("/bots/{}/friends", &my_bot_uuid),
                        json!({})
                    );

                    let result = client.list_friends(&my_bot_uuid).await?;

                    if cli.json {
                        println!("{}", serde_json::to_string(&result.data)?);
                    } else if let Some(data) = result.data {
                        if let Some(friends) = data.as_array() {
                            println!("Friends ({}):", friends.len());
                            for friend in friends {
                                let uuid = friend
                                    .get("bot_uuid")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("?");
                                let name = friend
                                    .get("name")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("unnamed");
                                let online = friend
                                    .get("is_online")
                                    .and_then(|v| v.as_bool())
                                    .unwrap_or(false);
                                let status_icon = if online { "🟢" } else { "⚪" };
                                println!("  {} {} ({})", status_icon, name, uuid);
                            }
                        } else {
                            println!("No friends found.");
                        }
                    }
                }

                FriendCommands::Requests { direction, status } => {
                    debug_request!(
                        debug,
                        "GET",
                        "/friends/requests",
                        json!({
                            "direction": &direction,
                            "status": &status
                        })
                    );

                    let result = client
                        .list_friend_requests(None, Some(&direction), status.as_deref())
                        .await?;

                    if cli.json {
                        println!("{}", serde_json::to_string(&result.data)?);
                    } else if let Some(data) = result.data {
                        if let Some(requests) = data.as_array() {
                            println!("Friend requests ({}):", requests.len());
                            for req in requests {
                                let id = req.get("id").and_then(|v| v.as_str()).unwrap_or("?");
                                let from =
                                    req.get("from_bot").and_then(|v| v.as_str()).unwrap_or("?");
                                let to = req.get("to_bot").and_then(|v| v.as_str()).unwrap_or("?");
                                let req_status =
                                    req.get("status").and_then(|v| v.as_str()).unwrap_or("?");
                                println!("  {} → {} [{}] (id: {})", from, to, req_status, id);
                            }
                        } else {
                            println!("No friend requests found.");
                        }
                    }
                }
            }
        }

        Commands::Channel { token, command } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            match &command {
                ChannelCommands::Bind { .. } => {
                    let payload = build_channel_bind_payload(&command)?;
                    debug_request!(
                        debug,
                        "POST",
                        "/channels/bindings",
                        redact_channel_bind_debug_payload(&payload)
                    );

                    let result = client.create_channel_binding(&payload).await?;

                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        println!("✓ Channel binding created");
                        if let Some(id) = result.get("id").and_then(|value| value.as_str()) {
                            println!("  ID: {}", id);
                        }
                        if let Some(account) =
                            result.get("account_ref").and_then(|value| value.as_str())
                        {
                            println!("  Account: {}", account);
                        }
                    }
                }

                ChannelCommands::List => {
                    debug_request!(debug, "GET", "/channels/bindings", json!({}));

                    let result = client.list_channel_bindings().await?;

                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else if let Some(items) =
                        result.get("items").and_then(|value| value.as_array())
                    {
                        println!("Channel bindings ({}):", items.len());
                        for item in items {
                            let id = item
                                .get("id")
                                .and_then(|value| value.as_str())
                                .unwrap_or("?");
                            let account = item
                                .get("account_ref")
                                .and_then(|value| value.as_str())
                                .unwrap_or("?");
                            let status = item
                                .get("status")
                                .and_then(|value| value.as_str())
                                .unwrap_or("?");
                            println!("  {} {} [{}]", id, account, status);
                        }
                    } else {
                        println!("{}", serde_json::to_string_pretty(&result)?);
                    }
                }

                ChannelCommands::Unbind { id } => {
                    debug_request!(
                        debug,
                        "DELETE",
                        &format!("/channels/bindings/{}", id),
                        json!({})
                    );

                    let result = client.delete_channel_binding(id).await?;

                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        println!("✓ Channel binding deleted: {}", id);
                    }
                }
            }
        }

        Commands::Visibility { token, command } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            match command {
                VisibilityCommands::Get { bot_uuid } => {
                    let my_bot_uuid = match bot_uuid {
                        Some(id) => id,
                        None => resolve_my_bot_uuid()?,
                    };

                    debug_request!(
                        debug,
                        "GET",
                        &format!("/bots/{}/visibility", &my_bot_uuid),
                        json!({})
                    );

                    let result = client.get_visibility(&my_bot_uuid).await?;

                    if cli.json {
                        println!(
                            "{}",
                            serde_json::to_string(&json!({
                                "success": result.success,
                                "data": result.data,
                            }))?
                        );
                    } else if let Some(data) = result.data {
                        let vis = data
                            .get("visibility")
                            .and_then(|v| v.as_str())
                            .unwrap_or("unknown");
                        println!("Visibility: {}", vis);
                    } else {
                        println!("✗ Failed: {}", result.error.unwrap_or_default());
                    }
                }

                VisibilityCommands::Set { value, bot_uuid } => {
                    let my_bot_uuid = match bot_uuid {
                        Some(id) => id,
                        None => resolve_my_bot_uuid()?,
                    };

                    debug_request!(
                        debug,
                        "PUT",
                        &format!("/bots/{}/visibility", &my_bot_uuid),
                        json!({
                            "visibility": &value
                        })
                    );

                    let result = client.set_visibility(&my_bot_uuid, &value).await?;

                    if cli.json {
                        println!(
                            "{}",
                            serde_json::to_string(&json!({
                                "success": result.success,
                                "data": result.data,
                            }))?
                        );
                    } else if result.success {
                        println!("✓ Visibility set to '{}'", value);
                    } else {
                        println!("✗ Failed: {}", result.error.unwrap_or_default());
                    }
                }
            }
        }

        Commands::Session { token, command } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            match command {
                SessionCommands::Create {
                    group,
                    title,
                    kind,
                    input,
                    meta,
                } => {
                    let input_json = input
                        .as_deref()
                        .map(serde_json::from_str::<serde_json::Value>)
                        .transpose()
                        .map_err(|e| anyhow!("Invalid --input JSON: {}", e))?;
                    let meta_json = meta
                        .as_deref()
                        .map(serde_json::from_str::<serde_json::Value>)
                        .transpose()
                        .map_err(|e| anyhow!("Invalid --meta JSON: {}", e))?;

                    debug_request!(
                        debug,
                        "POST",
                        &format!("/groups/{}/sessions", &group),
                        json!({
                            "session_title": &title,
                            "session_kind": &kind,
                            "input": &input_json,
                            "meta": &meta_json,
                        })
                    );

                    let result = client
                        .create_session(
                            &group,
                            title.as_deref(),
                            kind.as_deref(),
                            input_json.as_ref(),
                            meta_json.as_ref(),
                        )
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        let sid = result
                            .get("session_id")
                            .or_else(|| result.get("id"))
                            .and_then(|v| v.as_str())
                            .unwrap_or("?");
                        let kind = result
                            .get("session_kind")
                            .and_then(|v| v.as_str())
                            .unwrap_or("chat");
                        let status = result
                            .get("status")
                            .and_then(|v| v.as_str())
                            .unwrap_or("?");
                        println!("✓ Session created: {} (kind={}, status={})", sid, kind, status);
                    }
                }

                SessionCommands::List {
                    group,
                    status,
                    q,
                    participant,
                    offset,
                    limit,
                } => {
                    debug_request!(
                        debug,
                        "GET",
                        &format!("/groups/{}/sessions", &group),
                        json!({
                            "status": &status,
                            "q": &q,
                            "participant": &participant,
                            "offset": &offset,
                            "limit": &limit,
                        })
                    );

                    let result = client
                        .list_sessions(
                            &group,
                            status.as_deref(),
                            q.as_deref(),
                            participant.as_deref(),
                            offset,
                            limit,
                        )
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        let items = result
                            .get("items")
                            .and_then(|v| v.as_array())
                            .cloned()
                            .unwrap_or_default();
                        println!("Sessions in group {} ({}):", group, items.len());
                        for item in items {
                            let sid = item
                                .get("session_id")
                                .or_else(|| item.get("id"))
                                .and_then(|v| v.as_str())
                                .unwrap_or("?");
                            let st = item
                                .get("status")
                                .and_then(|v| v.as_str())
                                .unwrap_or("?");
                            let title = item
                                .get("session_title")
                                .and_then(|v| v.as_str())
                                .unwrap_or("");
                            println!("  - {} [{}] {}", sid, st, title);
                        }
                    }
                }

                SessionCommands::Get { session } => {
                    debug_request!(debug, "GET", &format!("/sessions/{}", &session), json!({}));
                    let result = client.get_session(&session).await?;
                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        println!("{}", serde_json::to_string_pretty(&result)?);
                    }
                }

                SessionCommands::Chat { session, message } => {
                    debug_request!(
                        debug,
                        "POST",
                        &format!("/sessions/{}/chat", &session),
                        json!({ "message": &message })
                    );

                    let result = client.session_chat(&session, &message).await?;

                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        let delivered = result
                            .get("delivered_count")
                            .and_then(|v| v.as_u64())
                            .unwrap_or(0);
                        let failed = result
                            .get("failed_count")
                            .and_then(|v| v.as_u64())
                            .unwrap_or(0);
                        println!("✓ Delivered to {} (failed {})", delivered, failed);
                        if let Some(mentions) =
                            result.get("mentions").and_then(|v| v.as_array())
                        {
                            if !mentions.is_empty() {
                                let names: Vec<String> = mentions
                                    .iter()
                                    .filter_map(|m| m.as_str().map(|s| s.to_string()))
                                    .collect();
                                if !names.is_empty() {
                                    println!("  @mentions: {}", names.join(", "));
                                }
                            }
                        }
                    }
                }

                SessionCommands::Messages {
                    session,
                    view_bot,
                    limit,
                    before,
                } => {
                    debug_request!(
                        debug,
                        "GET",
                        &format!("/sessions/{}/messages", &session),
                        json!({
                            "view_bot_id": &view_bot,
                            "limit": &limit,
                            "before": &before,
                        })
                    );

                    let result = client
                        .session_messages(&session, view_bot.as_deref(), limit, before)
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        let messages = result.as_array().cloned().unwrap_or_default();
                        println!("Messages in session {} ({}):", session, messages.len());
                        for msg in messages {
                            let ts = msg
                                .get("ts")
                                .or_else(|| msg.get("timestamp"))
                                .and_then(|v| v.as_u64())
                                .unwrap_or(0);
                            let sender = msg
                                .get("sender")
                                .or_else(|| msg.get("from"))
                                .and_then(|v| v.as_str())
                                .unwrap_or("?");
                            let content = msg
                                .get("content")
                                .or_else(|| msg.get("message"))
                                .and_then(|v| v.as_str())
                                .unwrap_or("");
                            // UTF-8 safe truncation per src/bcs/CLAUDE.md
                            let preview: &str = match content.char_indices().nth(80) {
                                Some((idx, _)) => &content[..idx],
                                None => content,
                            };
                            println!("  [{}] {}: {}", ts, sender, preview);
                        }
                    }
                }

                SessionCommands::Patch { session, title } => {
                    debug_request!(
                        debug,
                        "PATCH",
                        &format!("/sessions/{}", &session),
                        json!({ "session_title": &title })
                    );

                    let result = client.patch_session(&session, &title).await?;

                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        let sid = result
                            .get("session_id")
                            .or_else(|| result.get("id"))
                            .and_then(|v| v.as_str())
                            .unwrap_or("?");
                        let new_title = result
                            .get("session_title")
                            .and_then(|v| v.as_str())
                            .unwrap_or("?");
                        println!("✓ Patched: {} title=\"{}\"", sid, new_title);
                    }
                }

                SessionCommands::Complete {
                    session,
                    output,
                    error,
                } => {
                    let output_json = output
                        .as_deref()
                        .map(parse_json_arg)
                        .transpose()
                        .map_err(|e| anyhow!("--output: {}", e))?;

                    debug_request!(
                        debug,
                        "POST",
                        &format!("/sessions/{}/complete", &session),
                        json!({
                            "output": &output_json,
                            "error": &error,
                        })
                    );

                    let result = client
                        .complete_session(&session, output_json.as_ref(), error.as_deref())
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else if result.get("already_completed").and_then(|v| v.as_bool()) == Some(true)
                    {
                        println!("↺ Already completed: {}", session);
                    } else {
                        let status = result
                            .get("status")
                            .and_then(|v| v.as_str())
                            .unwrap_or("completed");
                        println!("✓ Completed: {} status={}", session, status);
                    }
                }

                SessionCommands::AddMember {
                    session,
                    bot_uuid,
                    role,
                } => {
                    debug_request!(
                        debug,
                        "POST",
                        &format!("/sessions/{}/members", &session),
                        json!({
                            "bot_uuid": &bot_uuid,
                            "role": &role,
                        })
                    );

                    let result = client
                        .add_session_member(&session, &bot_uuid, role.as_deref())
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        let effective_role = result
                            .get("participants")
                            .and_then(|v| v.as_array())
                            .and_then(|arr| {
                                arr.iter()
                                    .find(|p| p.get("bot_uuid").and_then(|v| v.as_str()) == Some(&bot_uuid))
                            })
                            .and_then(|p| p.get("role").and_then(|v| v.as_str()))
                            .unwrap_or("?");
                        println!(
                            "✓ Added member {} to {} (role={})",
                            bot_uuid, session, effective_role
                        );
                    }
                }

                SessionCommands::RemoveMember {
                    session,
                    bot_uuid,
                } => {
                    debug_request!(
                        debug,
                        "DELETE",
                        &format!("/sessions/{}/members/{}", &session, &bot_uuid),
                        json!({})
                    );

                    let result = client
                        .remove_session_member(&session, &bot_uuid)
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        println!("✓ Removed member {} from {}", bot_uuid, session);
                    }
                }

                SessionCommands::SetMemberMode {
                    session,
                    bot_uuid,
                    mode,
                } => {
                    debug_request!(
                        debug,
                        "PATCH",
                        &format!("/sessions/{}/members/{}", &session, &bot_uuid),
                        json!({ "mode": &mode })
                    );

                    let result = client
                        .set_session_member_mode(&session, &bot_uuid, &mode)
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        println!("✓ Mode set {}@{} -> {}", bot_uuid, session, mode);
                    }
                }

                SessionCommands::InviteLink {
                    session,
                    ttl_seconds,
                } => {
                    debug_request!(
                        debug,
                        "POST",
                        &format!("/sessions/{}/invite-link", &session),
                        json!({ "ttl_seconds": ttl_seconds })
                    );

                    let result = client
                        .create_session_invite_link(&session, ttl_seconds)
                        .await?;

                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        if let Some(link) = result.get("link").and_then(|v| v.as_str()) {
                            println!("✓ Invite link created:");
                            println!("  {}", link);
                            if let Some(expires_at) = result.get("expires_at").and_then(|v| v.as_u64()) {
                                println!("  Expires: {} (Unix ms)", expires_at);
                            }
                        } else {
                            println!("{}", serde_json::to_string_pretty(&result)?);
                        }
                    }
                }
            }
        }

        Commands::Service {
            token,
            command,
        } => {
            let token = get_token(token.as_deref())?;
            let client = create_client(
                &bcs_url,
                &token,
                bcs_cookie.as_deref(),
                oauth_headers.as_ref(),
            );

            match command {
                ServiceCommands::Invoke {
                    group,
                    input,
                    meta,
                    session_id,
                    baas_session_id,
                    caller_id,
                    title,
                    detach,
                    timeout_ms,
                } => {
                    let input_json = input
                        .as_deref()
                        .map(parse_json_arg)
                        .transpose()
                        .map_err(|e| anyhow!("--input: {}", e))?;
                    let meta_json = meta
                        .as_deref()
                        .map(parse_json_arg)
                        .transpose()
                        .map_err(|e| anyhow!("--meta: {}", e))?;
                    let meta_json = merge_baas_session_id_into_meta(
                        meta_json,
                        baas_session_id.as_deref(),
                    )
                    .map_err(|e| anyhow!("--baas-session-id: {}", e))?;

                    debug_request!(
                        debug,
                        "POST",
                        &format!("/services/{}/sessions", &group),
                        json!({
                            "session_id": &session_id,
                            "caller_id": &caller_id,
                            "session_title": &title,
                            "input": &input_json,
                            "meta": &meta_json,
                        })
                    );

                    let result = client
                        .service_invoke(
                            &group,
                            input_json.as_ref(),
                            session_id.as_deref(),
                            caller_id.as_deref(),
                            title.as_deref(),
                            meta_json.as_ref(),
                        )
                        .await?;

                    debug_response!(debug, "202", &result);

                    let sid = result
                        .get("session_id")
                        .and_then(|v| v.as_str())
                        .ok_or_else(|| anyhow!("Server response missing session_id: {}", result))?
                        .to_string();

                    if detach {
                        if cli.json {
                            println!("{}", serde_json::to_string(&result)?);
                        } else {
                            print_service_session_summary(&result, "Invocation submitted");
                        }
                    } else {
                        let budget = timeout_ms.unwrap_or(1_800_000);
                        let final_session = wait_for_service_completion(
                            &client, &group, &sid, budget,
                        )
                        .await?;
                        if cli.json {
                            println!("{}", serde_json::to_string(&final_session)?);
                        } else {
                            print_service_session_summary(&final_session, "Invocation completed");
                        }
                    }
                }

                ServiceCommands::Status { sid, group } => {
                    let (gid, sid_ref) = split_service_sid(&sid, group.as_deref())?;
                    debug_request!(
                        debug,
                        "GET",
                        &format!("/services/{}/sessions/{}", gid, sid_ref),
                        json!({})
                    );

                    let result = client.service_session_status(gid, sid_ref).await?;
                    debug_response!(debug, "200", &result);

                    if cli.json {
                        println!("{}", serde_json::to_string(&result)?);
                    } else {
                        print_service_session_summary(&result, "Service session");
                    }
                }

                ServiceCommands::Wait {
                    sid,
                    group,
                    timeout_ms,
                } => {
                    let (gid, sid_ref) = split_service_sid(&sid, group.as_deref())?;
                    let budget = timeout_ms.unwrap_or(1_800_000);
                    let final_session = wait_for_service_completion(
                        &client, gid, sid_ref, budget,
                    )
                    .await?;
                    if cli.json {
                        println!("{}", serde_json::to_string(&final_session)?);
                    } else {
                        print_service_session_summary(&final_session, "Service session");
                    }
                }
            }
        }
    }

    Ok(())
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use clap::{Parser, error::ErrorKind};
    use serial_test::serial;
    use std::io::Write;
    use tempfile::TempDir;

    // Helper to safely set env var
    #[allow(unsafe_code)]
    fn safe_set_var(key: &str, value: impl AsRef<std::ffi::OsStr>) {
        unsafe {
            std::env::set_var(key, value);
        }
    }

    // Helper to safely remove env var
    #[allow(unsafe_code)]
    fn safe_remove_var(key: &str) {
        unsafe {
            std::env::remove_var(key);
        }
    }

    fn write_session_file(temp_dir: &TempDir, value: serde_json::Value) {
        let session_dir = temp_dir.path().join(".bcs");
        std::fs::create_dir_all(&session_dir).unwrap();
        let session_file = session_dir.join("session.json");
        let mut file = std::fs::File::create(session_file).unwrap();
        file.write_all(serde_json::to_string_pretty(&value).unwrap().as_bytes())
            .unwrap();
    }

    #[test]
    fn test_structured_result_ok_serialization() {
        let result = StructuredResult {
            status: "ok".to_string(),
            message: None,
            network_env: None,
            auth_url: None,
            timeout_secs: None,
            log_file: Some("/tmp/bcs.log".to_string()),
        };
        let json = serde_json::to_string(&result).unwrap();
        assert!(json.contains("\"status\":\"ok\""));
        assert!(json.contains("\"log_file\":"));
    }

    #[test]
    fn test_structured_result_serialization() {
        let result = StructuredResult {
            status: "auth_timeout".to_string(),
            message: Some("timeout".to_string()),
            network_env: Some("office".to_string()),
            auth_url: None,
            timeout_secs: Some(120),
            log_file: Some("/tmp/bcs.log".to_string()),
        };
        let json = serde_json::to_string(&result).unwrap();
        assert!(json.contains("\"status\":\"auth_timeout\""));
        assert!(json.contains("\"network_env\":\"office\""));
        assert!(json.contains("\"timeout_secs\":120"));
    }

    #[test]
    fn test_normalize_bcs_api_url_from_ws_endpoint() {
        assert_eq!(
            normalize_bcs_api_url("ws://localhost:21000/ws/bot").as_deref(),
            Some("http://localhost:21000")
        );
        assert_eq!(
            normalize_bcs_api_url("wss://bcs-pre.example.com/ws/bot").as_deref(),
            Some("https://bcs-pre.example.com")
        );
    }

    #[test]
    fn test_normalize_bcs_api_url_keeps_http_base() {
        assert_eq!(
            normalize_bcs_api_url("https://bcs.example.com/").as_deref(),
            Some("https://bcs.example.com")
        );
    }

    #[test]
    fn test_normalize_bcs_ws_url_from_http_base() {
        assert_eq!(
            normalize_bcs_ws_url("http://localhost:21000").as_deref(),
            Some("ws://localhost:21000/ws/bot")
        );
    }

    #[test]
    fn test_normalize_bcs_ws_url_from_https_base() {
        assert_eq!(
            normalize_bcs_ws_url("https://bcs.example.com").as_deref(),
            Some("wss://bcs.example.com/ws/bot")
        );
    }

    #[test]
    fn test_normalize_bcs_ws_url_keeps_existing_ws_endpoints() {
        assert_eq!(
            normalize_bcs_ws_url("ws://localhost:21000/ws/bot").as_deref(),
            Some("ws://localhost:21000/ws/bot")
        );
        assert_eq!(
            normalize_bcs_ws_url("wss://bcs.example.com/ws/bot").as_deref(),
            Some("wss://bcs.example.com/ws/bot")
        );
    }

    /// Test token discovery priority: CLI arg > env var > session file
    #[test]
    fn test_token_discovery_priority_cli_first() {
        // CLI arg should take highest priority
        let explicit_token = "explicit-token-123";
        let result = discover_token(Some(explicit_token));
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), explicit_token);
    }

    /// Test token discovery from BCN_BOT_TOKEN environment variable
    #[test]
    #[serial]
    fn test_token_discovery_from_env_var() {
        let temp_dir = TempDir::new().unwrap();
        let data_dir = temp_dir.path().to_path_buf();

        // Save original env
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_token = std::env::var("BCN_BOT_TOKEN").ok();

        // Set test env vars
        safe_set_var("BOT_DATA_DIR", &data_dir);
        safe_set_var("BCN_BOT_TOKEN", "env-token-456");

        // No explicit token, should use env var
        let result = discover_token(None);

        // Restore env
        if let Some(orig) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", orig);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(orig) = original_token {
            safe_set_var("BCN_BOT_TOKEN", orig);
        } else {
            safe_remove_var("BCN_BOT_TOKEN");
        }

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "env-token-456");
    }

    /// Test token discovery from session file
    #[test]
    #[serial]
    fn test_token_discovery_from_session_file() {
        let temp_dir = TempDir::new().unwrap();
        let data_dir = temp_dir.path().to_path_buf();
        let bcs_dir = data_dir.join(".bcs");
        std::fs::create_dir_all(&bcs_dir).unwrap();

        // Write session file
        let session_file = bcs_dir.join("session.json");
        let session_content = json!({
            "bot_uuid": "bot-test-789",
            "token": "file-token-789",
            "bcs_url": "ws://localhost:21000/ws/bot"
        });
        let mut file = std::fs::File::create(&session_file).unwrap();
        file.write_all(
            serde_json::to_string_pretty(&session_content)
                .unwrap()
                .as_bytes(),
        )
        .unwrap();

        // Save original env
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_token = std::env::var("BCN_BOT_TOKEN").ok();

        // Set test data dir, clear BCN_BOT_TOKEN
        safe_set_var("BOT_DATA_DIR", &data_dir);
        safe_remove_var("BCN_BOT_TOKEN");

        // No explicit token, no env var, should use session file
        let result = discover_token(None);

        // Restore env
        if let Some(orig) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", orig);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(orig) = original_token {
            safe_set_var("BCN_BOT_TOKEN", orig);
        }

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "file-token-789");
    }

    /// Test token discovery priority: CLI arg overrides env var
    #[test]
    #[serial]
    fn test_token_cli_overrides_env() {
        let temp_dir = TempDir::new().unwrap();
        let data_dir = temp_dir.path().to_path_buf();

        // Save original env
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_token = std::env::var("BCN_BOT_TOKEN").ok();

        safe_set_var("BOT_DATA_DIR", &data_dir);
        safe_set_var("BCN_BOT_TOKEN", "env-token");

        // Explicit token should override env var
        let result = discover_token(Some("cli-token"));

        // Restore env
        if let Some(orig) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", orig);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(orig) = original_token {
            safe_set_var("BCN_BOT_TOKEN", orig);
        } else {
            safe_remove_var("BCN_BOT_TOKEN");
        }

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "cli-token");
    }

    /// Test token discovery priority: env var overrides session file
    #[test]
    #[serial]
    fn test_token_env_overrides_file() {
        let temp_dir = TempDir::new().unwrap();
        let data_dir = temp_dir.path().to_path_buf();
        let bcs_dir = data_dir.join(".bcs");
        std::fs::create_dir_all(&bcs_dir).unwrap();

        // Write session file
        let session_file = bcs_dir.join("session.json");
        let session_content = json!({
            "bot_id": "bot-test",
            "token": "file-token",
            "bcs_url": "ws://localhost:21000/ws/bot"
        });
        let mut file = std::fs::File::create(&session_file).unwrap();
        file.write_all(
            serde_json::to_string_pretty(&session_content)
                .unwrap()
                .as_bytes(),
        )
        .unwrap();

        // Save original env
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_token = std::env::var("BCN_BOT_TOKEN").ok();

        safe_set_var("BOT_DATA_DIR", &data_dir);
        safe_set_var("BCN_BOT_TOKEN", "env-token");

        // Env var should override session file
        let result = discover_token(None);

        // Restore env
        if let Some(orig) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", orig);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(orig) = original_token {
            safe_set_var("BCN_BOT_TOKEN", orig);
        } else {
            safe_remove_var("BCN_BOT_TOKEN");
        }

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "env-token");
    }

    /// Test token discovery returns an empty token when no source is available,
    /// allowing the CLI to proceed without authentication.
    #[test]
    #[serial]
    fn test_token_not_found_returns_empty() {
        let temp_dir = TempDir::new().unwrap();
        let data_dir = temp_dir.path().to_path_buf();

        // Save original env
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_token = std::env::var("BCN_BOT_TOKEN").ok();

        // Set empty environment (no token anywhere)
        safe_set_var("BOT_DATA_DIR", &data_dir);
        safe_remove_var("BCN_BOT_TOKEN");

        // Should succeed with an empty token (no auth)
        let result = discover_token(None);

        // Restore env
        if let Some(orig) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", orig);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(orig) = original_token {
            safe_set_var("BCN_BOT_TOKEN", orig);
        }

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "");
    }

    #[test]
    #[serial]
    fn test_resolve_bcs_url_from_session_file() {
        let temp_dir = TempDir::new().unwrap();
        write_session_file(
            &temp_dir,
            json!({
                "bot_uuid": null,
                "token": "session-token",
                "bcs_url": "ws://localhost:21000/ws/bot"
            }),
        );

        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_url = std::env::var("MOLTIS_BCS_URL").ok();
        let original_base_url = std::env::var("BCS_API_BASE_URL").ok();

        safe_set_var("BOT_DATA_DIR", temp_dir.path());
        safe_remove_var("MOLTIS_BCS_URL");
        safe_remove_var("BCS_API_BASE_URL");

        let cli = Cli {
            url: None,
            cookie: None,
            log_level: "info".to_string(),
            json: false,
            no_json: true,
            #[cfg(debug_assertions)]
            debug: false,
            command: Commands::Health,
        };
        let resolved = resolve_bcs_url(&cli).unwrap();

        if let Some(value) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", value);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(value) = original_url {
            safe_set_var("MOLTIS_BCS_URL", value);
        } else {
            safe_remove_var("MOLTIS_BCS_URL");
        }
        if let Some(value) = original_base_url {
            safe_set_var("BCS_API_BASE_URL", value);
        } else {
            safe_remove_var("BCS_API_BASE_URL");
        }

        assert_eq!(resolved, "http://localhost:21000");
    }

    #[test]
    #[serial]
    fn test_resolve_bcs_url_defaults_to_local() {
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_url = std::env::var("MOLTIS_BCS_URL").ok();
        let original_base_url = std::env::var("BCS_API_BASE_URL").ok();
        let original_agentclaw_env = std::env::var("AGENTCLAW_ENV").ok();

        safe_remove_var("BOT_DATA_DIR");
        safe_remove_var("MOLTIS_BCS_URL");
        safe_remove_var("BCS_API_BASE_URL");
        safe_remove_var("AGENTCLAW_ENV");

        let cli = Cli {
            url: None,
            cookie: None,
            log_level: "info".to_string(),
            json: false,
            no_json: true,
            #[cfg(debug_assertions)]
            debug: false,
            command: Commands::Health,
        };
        let result = resolve_bcs_url(&cli);

        if let Some(value) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", value);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(value) = original_url {
            safe_set_var("MOLTIS_BCS_URL", value);
        } else {
            safe_remove_var("MOLTIS_BCS_URL");
        }
        if let Some(value) = original_base_url {
            safe_set_var("BCS_API_BASE_URL", value);
        } else {
            safe_remove_var("BCS_API_BASE_URL");
        }
        if let Some(value) = original_agentclaw_env {
            safe_set_var("AGENTCLAW_ENV", value);
        } else {
            safe_remove_var("AGENTCLAW_ENV");
        }

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "http://127.0.0.1:21000");
    }

    #[test]
    #[serial]
    fn test_resolve_bcs_url_defaults_to_local_when_agentclaw_env_pre() {
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_url = std::env::var("MOLTIS_BCS_URL").ok();
        let original_base_url = std::env::var("BCS_API_BASE_URL").ok();
        let original_agentclaw_env = std::env::var("AGENTCLAW_ENV").ok();

        safe_remove_var("BOT_DATA_DIR");
        safe_remove_var("MOLTIS_BCS_URL");
        safe_remove_var("BCS_API_BASE_URL");
        safe_set_var("AGENTCLAW_ENV", "pre");

        let cli = Cli {
            url: None,
            cookie: None,
            log_level: "info".to_string(),
            json: false,
            no_json: true,
            #[cfg(debug_assertions)]
            debug: false,
            command: Commands::Health,
        };
        let result = resolve_bcs_url(&cli);

        if let Some(value) = original_data_dir {
            safe_set_var("BOT_DATA_DIR", value);
        } else {
            safe_remove_var("BOT_DATA_DIR");
        }
        if let Some(value) = original_url {
            safe_set_var("MOLTIS_BCS_URL", value);
        } else {
            safe_remove_var("MOLTIS_BCS_URL");
        }
        if let Some(value) = original_base_url {
            safe_set_var("BCS_API_BASE_URL", value);
        } else {
            safe_remove_var("BCS_API_BASE_URL");
        }
        if let Some(value) = original_agentclaw_env {
            safe_set_var("AGENTCLAW_ENV", value);
        } else {
            safe_remove_var("AGENTCLAW_ENV");
        }

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "http://127.0.0.1:21000");
    }

    #[test]
    fn test_classify_auth_error_message_timeout() {
        assert_eq!(
            classify_auth_error_message("OAuth2 authorization timed out locally after 2 minutes"),
            "auth_timeout"
        );
    }

    #[test]
    fn test_classify_auth_error_message_expired() {
        assert_eq!(
            classify_auth_error_message(
                "OAuth2 authorization expired on server. Please rerun the command."
            ),
            "auth_expired"
        );
    }

    #[test]
    fn test_classify_auth_error_message_request_failed() {
        assert_eq!(
            classify_auth_error_message("Invalid bots list response"),
            "request_failed"
        );
    }

    #[tokio::test]
    async fn test_detect_network_env_localhost() {
        let env = detect_network_env("http://localhost:21000", false).await;
        assert_eq!(env, NetworkEnv::Prod);
    }

    #[tokio::test]
    async fn test_detect_network_env_127() {
        let env = detect_network_env("http://127.0.0.1:21000", false).await;
        assert_eq!(env, NetworkEnv::Prod);
    }

    #[test]
    fn test_is_localhost_url() {
        assert!(is_localhost_url("http://localhost:21000"));
        assert!(is_localhost_url("https://127.0.0.1:8080"));
        assert!(is_localhost_url("http://127.0.0.1"));
        assert!(!is_localhost_url("https://bcs.example.com"));
        assert!(!is_localhost_url("http://10.0.0.1:21000"));
    }

    #[test]
    fn test_leave_command_is_not_available() {
        let err = match Cli::try_parse_from(["bcs-cli", "leave"]) {
            Ok(_) => panic!("expected leave command to be unavailable"),
            Err(err) => err,
        };

        assert_eq!(err.kind(), ErrorKind::InvalidSubcommand);
    }

    #[test]
    fn test_channel_bind_command_parses_defaults() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "channel",
            "bind",
            "--account",
            "robot_1",
            "--target-id",
            "group_1",
            "--robot-code",
            "robot_1",
            "--client-id",
            "client_id",
            "--client-secret",
            "secret",
        ])
        .unwrap();

        match cli.command {
            Commands::Channel {
                command:
                    ChannelCommands::Bind {
                        account,
                        target_kind,
                        target_id,
                        group_chat_scope,
                        visibility,
                        env,
                        send_mode,
                        message_type,
                        ..
                    },
                ..
            } => {
                assert_eq!(account, "robot_1");
                assert_eq!(target_kind, "group");
                assert_eq!(target_id, "group_1");
                assert_eq!(group_chat_scope, None);
                assert_eq!(visibility, "lead_only");
                assert_eq!(env, "dev");
                assert_eq!(send_mode, "normal");
                assert_eq!(message_type, "markdown");
            }
            _ => panic!("expected channel bind command"),
        }
    }

    #[test]
    fn test_channel_bind_payload_builds_group_dingtalk_normal() {
        let command = ChannelCommands::Bind {
            account: "robot_1".to_string(),
            target_kind: "group".to_string(),
            target_id: "group_1".to_string(),
            group_chat_scope: Some("conversation_shared".to_string()),
            visibility: "full_transcript".to_string(),
            env: "pre".to_string(),
            robot_code: "robot_1".to_string(),
            client_id: "client_id".to_string(),
            client_secret: "secret".to_string(),
            send_mode: "normal".to_string(),
            card_template_id: None,
            message_type: "text".to_string(),
        };

        let payload = build_channel_bind_payload(&command).unwrap();

        assert_eq!(
            payload,
            json!({
                "channel_type": "ding_talk",
                "account_ref": "robot_1",
                "target": { "group": { "group_id": "group_1" } },
                "group_chat_scope": "conversation_shared",
                "outbound_visibility": "full_transcript",
                "env": "pre",
                "config": {
                    "channel_type": "ding_talk",
                    "robot_code": "robot_1",
                    "client_id": "client_id",
                    "client_secret": "secret",
                    "send_mode": {
                        "mode": "normal",
                        "message_type": "text"
                    }
                }
            })
        );
    }

    #[test]
    fn test_channel_bind_payload_builds_bot_streaming_card() {
        let command = ChannelCommands::Bind {
            account: "robot_1".to_string(),
            target_kind: "bot".to_string(),
            target_id: "bot_1".to_string(),
            group_chat_scope: Some("per_sender".to_string()),
            visibility: "lead_only".to_string(),
            env: "dev".to_string(),
            robot_code: "robot_1".to_string(),
            client_id: "client_id".to_string(),
            client_secret: "secret".to_string(),
            send_mode: "streaming_card".to_string(),
            card_template_id: Some("card_tpl".to_string()),
            message_type: "markdown".to_string(),
        };

        let payload = build_channel_bind_payload(&command).unwrap();

        assert_eq!(payload["target"], json!({ "bot": { "bot_id": "bot_1" } }));
        assert_eq!(payload["group_chat_scope"], "per_sender");
        assert_eq!(
            payload["config"]["send_mode"],
            json!({
                "mode": "streaming_card",
                "card_template_id": "card_tpl",
                "fallback_message_type": "markdown"
            })
        );
    }

    #[test]
    fn test_channel_bind_payload_requires_card_template_for_streaming_card() {
        let command = ChannelCommands::Bind {
            account: "robot_1".to_string(),
            target_kind: "group".to_string(),
            target_id: "group_1".to_string(),
            group_chat_scope: None,
            visibility: "lead_only".to_string(),
            env: "dev".to_string(),
            robot_code: "robot_1".to_string(),
            client_id: "client_id".to_string(),
            client_secret: "secret".to_string(),
            send_mode: "streaming_card".to_string(),
            card_template_id: None,
            message_type: "markdown".to_string(),
        };

        let err = build_channel_bind_payload(&command).unwrap_err();
        assert!(err.to_string().contains("card-template-id"));
    }

    #[test]
    fn test_channel_bind_debug_payload_redacts_client_secret() {
        let command = ChannelCommands::Bind {
            account: "robot_1".to_string(),
            target_kind: "group".to_string(),
            target_id: "group_1".to_string(),
            group_chat_scope: None,
            visibility: "lead_only".to_string(),
            env: "dev".to_string(),
            robot_code: "robot_1".to_string(),
            client_id: "client_id".to_string(),
            client_secret: "secret".to_string(),
            send_mode: "normal".to_string(),
            card_template_id: None,
            message_type: "markdown".to_string(),
        };
        let payload = build_channel_bind_payload(&command).unwrap();

        let redacted = redact_channel_bind_debug_payload(&payload);

        assert_eq!(payload["config"]["client_secret"], "secret");
        assert_eq!(redacted["config"]["client_secret"], "<redacted>");
    }

    #[test]
    fn test_channel_list_command_parses() {
        let cli = Cli::try_parse_from(["bcs-cli", "channel", "list"]).unwrap();

        match cli.command {
            Commands::Channel {
                command: ChannelCommands::List,
                ..
            } => {}
            _ => panic!("expected channel list command"),
        }
    }

    #[test]
    fn test_channel_unbind_command_parses_id() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "channel",
            "unbind",
            "--id",
            "binding_1",
        ])
        .unwrap();

        match cli.command {
            Commands::Channel {
                command: ChannelCommands::Unbind { id },
                ..
            } => {
                assert_eq!(id, "binding_1");
            }
            _ => panic!("expected channel unbind command"),
        }
    }

    #[test]
    fn test_chat_command_timeout_ms_unset_by_default() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat {
                timeout_ms, detach, ..
            } => {
                assert_eq!(timeout_ms, None, "timeout_ms should be unset by default");
                assert!(!detach, "detach should default to false");
            }
            _ => panic!("expected chat command"),
        }
    }

    #[test]
    fn test_chat_command_defaults_to_after_last_tool_call_response_mode() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat { response_mode, .. } => {
                assert_eq!(response_mode.as_deref(), Some("after-last-tool-call"));
            }
            _ => panic!("expected chat command"),
        }
    }

    #[test]
    fn test_discover_command_accepts_organization_scope() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "discover",
            "--organization-code",
            "promo-2026",
            "--role",
            "traffic_analyst",
        ])
        .unwrap();

        match cli.command {
            Commands::Discover {
                organization_code,
                role,
                ..
            } => {
                assert_eq!(organization_code.as_deref(), Some("promo-2026"));
                assert_eq!(role.as_deref(), Some("traffic_analyst"));
            }
            _ => panic!("expected discover command"),
        }
    }

    #[test]
    fn test_chat_command_accepts_organization_code() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-b",
            "--message",
            "hello",
            "--organization-code",
            "promo-2026",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat { organization_code, .. } => {
                assert_eq!(organization_code.as_deref(), Some("promo-2026"));
            }
            _ => panic!("expected chat command"),
        }
    }

    #[test]
    fn test_chat_command_accepts_detach_flag() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
            "--detach",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat { detach, .. } => assert!(detach),
            _ => panic!("expected chat command"),
        }
    }

    #[test]
    fn test_chat_command_accepts_explicit_timeout_ms() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
            "--timeout-ms",
            "1200",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat { timeout_ms, .. } => assert_eq!(timeout_ms, Some(1_200)),
            _ => panic!("expected chat command"),
        }
    }

    #[test]
    fn test_chat_command_accepts_repeated_tags() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
            "--tag",
            "tag1",
            "--tag",
            "tag2",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat { tags, .. } => assert_eq!(tags, vec!["tag1", "tag2"]),
            _ => panic!("expected chat command"),
        }
    }

    #[test]
    fn test_chat_command_accepts_after_last_tool_call_response_mode() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
            "--response-mode",
            "after-last-tool-call",
        ])
        .unwrap();

        match cli.command {
            Commands::Chat { response_mode, .. } => {
                assert_eq!(response_mode.as_deref(), Some("after-last-tool-call"));
            }
            _ => panic!("expected chat command"),
        }
    }

    #[test]
    fn test_chat_command_rejects_zero_timeout_ms() {
        let err = match Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
            "--timeout-ms",
            "0",
        ]) {
            Ok(_) => panic!("expected parse failure"),
            Err(err) => err,
        };

        assert_eq!(err.kind(), ErrorKind::ValueValidation);
        let rendered = err.to_string();
        assert!(rendered.contains("--timeout-ms"));
        assert!(rendered.contains("86400000"));
    }

    #[test]
    fn test_chat_command_rejects_timeout_ms_over_limit() {
        let err = match Cli::try_parse_from([
            "bcs-cli",
            "chat",
            "--bot-uuid",
            "bot-123",
            "--message",
            "hello",
            "--timeout-ms",
            "86400001",
        ]) {
            Ok(_) => panic!("expected parse failure"),
            Err(err) => err,
        };

        assert_eq!(err.kind(), ErrorKind::ValueValidation);
        let rendered = err.to_string();
        assert!(rendered.contains("--timeout-ms"));
        assert!(rendered.contains("86400000"));
    }

    // ------------------------------------------------------------------
    // session subcommand parse tests
    // ------------------------------------------------------------------

    #[test]
    fn test_session_create_parses_minimal_args() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "create",
            "--group",
            "g-1",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command:
                    SessionCommands::Create {
                        group,
                        title,
                        kind,
                        input,
                        meta,
                    },
                ..
            } => {
                assert_eq!(group, "g-1");
                assert!(title.is_none());
                assert!(kind.is_none());
                assert!(input.is_none());
                assert!(meta.is_none());
            }
            _ => panic!("expected session create command"),
        }
    }

    #[test]
    fn test_session_list_accepts_filters() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "list",
            "--group",
            "g-1",
            "--status",
            "running",
            "-q",
            "tag",
            "--participant",
            "bot-a",
            "--offset",
            "10",
            "--limit",
            "50",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command:
                    SessionCommands::List {
                        group,
                        status,
                        q,
                        participant,
                        offset,
                        limit,
                    },
                ..
            } => {
                assert_eq!(group, "g-1");
                assert_eq!(status.as_deref(), Some("running"));
                assert_eq!(q.as_deref(), Some("tag"));
                assert_eq!(participant.as_deref(), Some("bot-a"));
                assert_eq!(offset, Some(10));
                assert_eq!(limit, Some(50));
            }
            _ => panic!("expected session list command"),
        }
    }

    #[test]
    fn test_session_get_requires_sid() {
        // Missing positional sid should fail.
        let err = match Cli::try_parse_from(["bcs-cli", "session", "get"]) {
            Ok(_) => panic!("expected parse failure"),
            Err(err) => err,
        };
        assert_eq!(err.kind(), ErrorKind::MissingRequiredArgument);

        // Provided sid round-trips.
        let cli =
            Cli::try_parse_from(["bcs-cli", "session", "get", "g-1:abcdef01"]).unwrap();
        match cli.command {
            Commands::Session {
                command: SessionCommands::Get { session },
                ..
            } => assert_eq!(session, "g-1:abcdef01"),
            _ => panic!("expected session get command"),
        }
    }

    #[test]
    fn test_session_chat_round_trips() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "chat",
            "--session",
            "g-1:00000001",
            "--message",
            "hello",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command: SessionCommands::Chat { session, message },
                ..
            } => {
                assert_eq!(session, "g-1:00000001");
                assert_eq!(message, "hello");
            }
            _ => panic!("expected session chat command"),
        }
    }

    #[test]
    fn test_session_messages_accepts_view_bot_and_limit() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "messages",
            "g-1:abcdef01",
            "--view-bot",
            "bot-a",
            "--limit",
            "100",
            "--before",
            "1700000000000",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command:
                    SessionCommands::Messages {
                        session,
                        view_bot,
                        limit,
                        before,
                    },
                ..
            } => {
                assert_eq!(session, "g-1:abcdef01");
                assert_eq!(view_bot.as_deref(), Some("bot-a"));
                assert_eq!(limit, Some(100));
                assert_eq!(before, Some(1_700_000_000_000));
            }
            _ => panic!("expected session messages command"),
        }
    }

    #[test]
    fn test_session_patch_parses_args() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "patch",
            "g-1:aabb0011",
            "--title",
            "new title",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command:
                    SessionCommands::Patch {
                        session,
                        title,
                    },
                ..
            } => {
                assert_eq!(session, "g-1:aabb0011");
                assert_eq!(title, "new title");
            }
            _ => panic!("expected session patch command"),
        }
    }

    #[test]
    fn test_session_complete_parses_args() {
        // with --output and --error
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "complete",
            "g-1:aabb0011",
            "--output",
            r#"{"summary":"ok"}"#,
            "--error",
            "timeout",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command:
                    SessionCommands::Complete {
                        session,
                        output,
                        error,
                    },
                ..
            } => {
                assert_eq!(session, "g-1:aabb0011");
                assert_eq!(output.as_deref(), Some(r#"{"summary":"ok"}"#));
                assert_eq!(error.as_deref(), Some("timeout"));
            }
            _ => panic!("expected session complete command"),
        }

        // minimal (no optional args)
        let cli2 = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "complete",
            "g-1:aabb0011",
        ])
        .unwrap();

        match cli2.command {
            Commands::Session {
                command:
                    SessionCommands::Complete {
                        output, error, ..
                    },
                ..
            } => {
                assert!(output.is_none());
                assert!(error.is_none());
            }
            _ => panic!("expected session complete command"),
        }
    }

    #[test]
    fn test_session_complete_output_parse_json_arg() {
        // parse_json_arg: JSON literal
        let val = parse_json_arg(r#"{"summary":"ok"}"#).unwrap();
        assert_eq!(val["summary"], "ok");

        // parse_json_arg: @file
        let dir = TempDir::new().unwrap();
        let file_path = dir.path().join("output.json");
        std::fs::write(&file_path, r#"{"result":42}"#).unwrap();
        let at_arg = format!("@{}", file_path.display());
        let val2 = parse_json_arg(&at_arg).unwrap();
        assert_eq!(val2["result"], 42);

        // parse_json_arg: bare @ is an error
        assert!(parse_json_arg("@").is_err());
    }

    #[test]
    fn test_session_add_member_parses_args() {
        // with --role
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "add-member",
            "g-1:aabb0011",
            "--bot-uuid",
            "bot-dba",
            "--role",
            "consultant",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command:
                    SessionCommands::AddMember {
                        session,
                        bot_uuid,
                        role,
                    },
                ..
            } => {
                assert_eq!(session, "g-1:aabb0011");
                assert_eq!(bot_uuid, "bot-dba");
                assert_eq!(role.as_deref(), Some("consultant"));
            }
            _ => panic!("expected session add-member command"),
        }

        // without --role
        let cli2 = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "add-member",
            "g-1:aabb0011",
            "--bot-uuid",
            "bot-dba",
        ])
        .unwrap();

        match cli2.command {
            Commands::Session {
                command: SessionCommands::AddMember { role, .. },
                ..
            } => {
                assert!(role.is_none());
            }
            _ => panic!("expected session add-member command"),
        }
    }

    #[test]
    fn test_session_remove_member_parses_args() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "remove-member",
            "g-1:aabb0011",
            "bot-dba",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command:
                    SessionCommands::RemoveMember {
                        session,
                        bot_uuid,
                    },
                ..
            } => {
                assert_eq!(session, "g-1:aabb0011");
                assert_eq!(bot_uuid, "bot-dba");
            }
            _ => panic!("expected session remove-member command"),
        }
    }

    #[test]
    fn test_session_set_member_mode_parses_args() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "set-member-mode",
            "g-1:aabb0011",
            "bot-dba",
            "--mode",
            "muted",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command:
                    SessionCommands::SetMemberMode {
                        session,
                        bot_uuid,
                        mode,
                    },
                ..
            } => {
                assert_eq!(session, "g-1:aabb0011");
                assert_eq!(bot_uuid, "bot-dba");
                assert_eq!(mode, "muted");
            }
            _ => panic!("expected session set-member-mode command"),
        }
    }

    #[test]
    fn test_session_invite_link_parses_args() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "session",
            "invite-link",
            "g-1:aabb0011",
            "--ttl-seconds",
            "3600",
        ])
        .unwrap();

        match cli.command {
            Commands::Session {
                command:
                    SessionCommands::InviteLink {
                        session,
                        ttl_seconds,
                    },
                ..
            } => {
                assert_eq!(session, "g-1:aabb0011");
                assert_eq!(ttl_seconds, Some(3600));
            }
            _ => panic!("expected session invite-link command"),
        }
    }

    // ------------------------------------------------------------------
    // service subcommand parse tests
    // ------------------------------------------------------------------

    #[test]
    fn test_service_invoke_parses_minimal_args() {
        let cli = Cli::try_parse_from([
            "bcs-cli", "service", "invoke", "--group", "g-1",
        ])
        .unwrap();

        match cli.command {
            Commands::Service {
                command:
                    ServiceCommands::Invoke {
                        group,
                        input,
                        meta,
                        session_id,
                        baas_session_id,
                        caller_id,
                        title,
                        detach,
                        timeout_ms,
                    },
                ..
            } => {
                assert_eq!(group, "g-1");
                assert!(input.is_none());
                assert!(meta.is_none());
                assert!(session_id.is_none());
                assert!(baas_session_id.is_none());
                assert!(caller_id.is_none());
                assert!(title.is_none());
                assert!(!detach);
                assert!(timeout_ms.is_none());
            }
            _ => panic!("expected service invoke command"),
        }
    }

    #[test]
    fn test_service_command_accepts_bot_token() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "service",
            "--token",
            "bot-token",
            "invoke",
            "--group",
            "g-1",
        ])
        .unwrap();

        match cli.command {
            Commands::Service { token, .. } => {
                assert_eq!(token.as_deref(), Some("bot-token"));
            }
            _ => panic!("expected service command"),
        }
    }

    #[test]
    fn test_service_invoke_parses_full_args() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "service",
            "invoke",
            "--group",
            "g-1",
            "--input",
            r#"{"q":"hi"}"#,
            "--meta",
            r#"{"trace":"abc"}"#,
            "--session-id",
            "g-1:00000001",
            "--baas-session-id",
            "agent:main:baas-session-1",
            "--caller-id",
            "client-a",
            "--title",
            "demo",
            "--detach",
            "--timeout-ms",
            "60000",
        ])
        .unwrap();

        match cli.command {
            Commands::Service {
                command:
                    ServiceCommands::Invoke {
                        group,
                        input,
                        meta,
                        session_id,
                        baas_session_id,
                        caller_id,
                        title,
                        detach,
                        timeout_ms,
                    },
                ..
            } => {
                assert_eq!(group, "g-1");
                assert_eq!(input.as_deref(), Some(r#"{"q":"hi"}"#));
                assert_eq!(meta.as_deref(), Some(r#"{"trace":"abc"}"#));
                assert_eq!(session_id.as_deref(), Some("g-1:00000001"));
                assert_eq!(baas_session_id.as_deref(), Some("agent:main:baas-session-1"));
                assert_eq!(caller_id.as_deref(), Some("client-a"));
                assert_eq!(title.as_deref(), Some("demo"));
                assert!(detach);
                assert_eq!(timeout_ms, Some(60_000));
            }
            _ => panic!("expected service invoke command"),
        }
    }

    #[test]
    fn test_service_invoke_rejects_zero_timeout_ms() {
        let err = match Cli::try_parse_from([
            "bcs-cli",
            "service",
            "invoke",
            "--group",
            "g-1",
            "--timeout-ms",
            "0",
        ]) {
            Ok(_) => panic!("expected parse failure"),
            Err(err) => err,
        };

        assert_eq!(err.kind(), ErrorKind::ValueValidation);
        let rendered = err.to_string();
        assert!(rendered.contains("--timeout-ms"));
        assert!(rendered.contains("86400000"));
    }

    #[test]
    fn test_service_invoke_baas_session_id_merges_into_meta() {
        let meta = Some(serde_json::json!({
            "trace": "abc",
            "callback_target": {
                "source": "cli"
            }
        }));

        let merged = merge_baas_session_id_into_meta(
            meta,
            Some("agent:main:baas-session-1"),
        )
        .unwrap()
        .expect("meta should exist after merge");

        assert_eq!(merged["trace"], "abc");
        assert_eq!(merged["callback_target"]["source"], "cli");
        assert_eq!(
            merged["callback_target"]["baas_session_id"],
            "agent:main:baas-session-1"
        );
    }

    #[test]
    fn test_service_invoke_baas_session_id_creates_meta_when_absent() {
        let merged = merge_baas_session_id_into_meta(None, Some("agent:main:baas-session-1"))
            .unwrap()
            .expect("meta should be created");

        assert_eq!(
            merged["callback_target"]["baas_session_id"],
            "agent:main:baas-session-1"
        );
    }

    #[test]
    fn test_service_status_parses_positional_sid() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "service",
            "status",
            "g-1:abcdef01",
        ])
        .unwrap();

        match cli.command {
            Commands::Service {
                command: ServiceCommands::Status { sid, group },
                ..
            } => {
                assert_eq!(sid, "g-1:abcdef01");
                assert!(group.is_none());
            }
            _ => panic!("expected service status command"),
        }
    }

    #[test]
    fn test_service_status_accepts_group_override() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "service",
            "status",
            "weird-sid",
            "--group",
            "g-explicit",
        ])
        .unwrap();

        match cli.command {
            Commands::Service {
                command: ServiceCommands::Status { sid, group },
                ..
            } => {
                assert_eq!(sid, "weird-sid");
                assert_eq!(group.as_deref(), Some("g-explicit"));
            }
            _ => panic!("expected service status command"),
        }
    }

    #[test]
    fn test_service_wait_parses_positional_sid_and_timeout() {
        let cli = Cli::try_parse_from([
            "bcs-cli",
            "service",
            "wait",
            "g-1:abcdef01",
            "--timeout-ms",
            "120000",
        ])
        .unwrap();

        match cli.command {
            Commands::Service {
                command:
                    ServiceCommands::Wait {
                        sid,
                        group,
                        timeout_ms,
                    },
                ..
            } => {
                assert_eq!(sid, "g-1:abcdef01");
                assert!(group.is_none());
                assert_eq!(timeout_ms, Some(120_000));
            }
            _ => panic!("expected service wait command"),
        }
    }

    // ------------------------------------------------------------------
    // service helper unit tests
    // ------------------------------------------------------------------

    #[test]
    fn test_service_session_summary_lines_include_state_machine_run() {
        let session = serde_json::json!({
            "session_id": "g-1:abcdef01",
            "group_id": "g-1",
            "status": "running",
            "state_machine_run_id": "run-1",
            "state_machine_run": {
                "run": {
                    "status": "running"
                }
            }
        });

        let lines = service_session_summary_lines(&session, "Invocation submitted");
        assert!(lines.iter().any(|line| line == "  StateRun: run-1"));
        assert!(lines.iter().any(|line| line == "  RunStatus: running"));
    }

    #[test]
    fn test_split_service_sid_recovers_group_from_colon() {
        let (gid, sid) = split_service_sid("g-1:abcdef01", None).unwrap();
        assert_eq!(gid, "g-1");
        assert_eq!(sid, "g-1:abcdef01");
    }

    #[test]
    fn test_split_service_sid_explicit_group_wins() {
        let (gid, sid) = split_service_sid("g-1:abcdef01", Some("g-other")).unwrap();
        assert_eq!(gid, "g-other");
        assert_eq!(sid, "g-1:abcdef01");
    }

    #[test]
    fn test_split_service_sid_rejects_unparseable_sid_without_group() {
        let err = split_service_sid("nocolonhere", None).unwrap_err();
        let msg = err.to_string();
        assert!(msg.contains("Cannot infer group"), "got: {}", msg);
        assert!(msg.contains("--group"), "got: {}", msg);
    }

    #[test]
    fn test_parse_json_arg_supports_literal() {
        let v = parse_json_arg(r#"{"k":"v","n":42}"#).unwrap();
        assert_eq!(v.get("k").and_then(|x| x.as_str()), Some("v"));
        assert_eq!(v.get("n").and_then(|x| x.as_i64()), Some(42));
    }

    #[test]
    fn test_parse_json_arg_reads_file_with_at_prefix() {
        let dir = TempDir::new().unwrap();
        let path = dir.path().join("payload.json");
        std::fs::write(&path, r#"{"hello":"world"}"#).unwrap();

        let arg = format!("@{}", path.display());
        let v = parse_json_arg(&arg).unwrap();
        assert_eq!(v.get("hello").and_then(|x| x.as_str()), Some("world"));
    }

    #[test]
    fn test_parse_json_arg_rejects_bare_at_sign() {
        let err = parse_json_arg("@").unwrap_err();
        assert!(err.to_string().contains("must be followed by a file path"));
    }
}
