//! Shared library for BCS CLI tools.
//!
//! Contains token discovery, debug macros, and client helpers
//! shared between `bcs-cli` (bot-facing) and `bcs-admin` (developer-facing).

use std::path::{Path, PathBuf};

use anyhow::{Result, anyhow};
use clap::ArgAction;
use serde::{Deserialize, Serialize};
use tracing::{Level, debug, info};
use tracing_subscriber::FmtSubscriber;

mod client;
pub mod oauth;

pub use client::{
    BcsClient, BotGroupListPage, ChatAsyncError, ChatRunOutcome, CreateCustomGroupOptions,
    CurrentActorGroupListPage, RunSessionCollaborationOptions,
};

const COMPILED_PRE_BCS_URL: Option<&str> = option_env!("BCS_CLI_DEFAULT_PRE_URL");
const COMPILED_PROD_BCS_URL: Option<&str> = option_env!("BCS_CLI_DEFAULT_PROD_URL");

/// Get current environment from `AGENTCLAW_ENV` or `env` variable.
/// Priority: `AGENTCLAW_ENV` > `env` > `SERVER_ENV` chain > "dev" (default)
pub fn get_current_env() -> String {
    std::env::var("AGENTCLAW_ENV")
        .or_else(|_| std::env::var("env"))
        .unwrap_or_else(|_| bcs_config::resolve_env_str())
}

// ============================================================================
// Token Discovery
// ============================================================================

/// Session info saved by BCN plugin (read-only for bcs-cli).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionInfo {
    #[serde(default)]
    pub bot_uuid: Option<String>,
    pub token: String,
    #[serde(default)]
    pub bcs_url: Option<String>,
    #[serde(default)]
    pub api_base_url: Option<String>,
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

fn resolve_distribution_default_url_for_env(
    runtime_env: &str,
    pre_url: Option<&str>,
    prod_url: Option<&str>,
) -> Result<Option<String>> {
    let (pre_url, prod_url) = match (pre_url, prod_url) {
        (None, None) => return Ok(None),
        (Some(pre_url), Some(prod_url)) => (pre_url, prod_url),
        _ => {
            return Err(anyhow!(
                "BCS_CLI_DEFAULT_PRE_URL and BCS_CLI_DEFAULT_PROD_URL must both be configured"
            ));
        }
    };

    let is_pre = matches!(
        runtime_env.to_ascii_lowercase().as_str(),
        "pre" | "prepub"
    );
    let (url, source) = if is_pre {
        (pre_url, "compiled default (pre-release)")
    } else {
        (prod_url, "compiled default (production)")
    };

    normalize_url_from_source(url.to_string(), source).map(Some)
}

/// Resolve the distribution-specific URL compiled into this CLI build.
///
/// Public builds omit both compile-time values and return `None`. Internal
/// builds provide both values and select one using the runtime environment.
pub fn resolve_compiled_distribution_default_url() -> Result<Option<String>> {
    resolve_distribution_default_url_for_env(
        &get_current_env(),
        COMPILED_PRE_BCS_URL,
        COMPILED_PROD_BCS_URL,
    )
}

fn resolve_bcs_url_with_distribution_defaults(
    args: &GlobalArgs,
    pre_url: Option<&str>,
    prod_url: Option<&str>,
) -> Result<String> {
    if let Some(url) = args.url.as_ref() {
        return normalize_url_from_source(url.clone(), "--url");
    }

    if let Some(url) = resolve_env_bcs_url()? {
        return Ok(url);
    }

    if let Some(url) = resolve_session_bcs_url()? {
        return Ok(url);
    }

    if let Some(url) =
        resolve_distribution_default_url_for_env(&get_current_env(), pre_url, prod_url)?
    {
        return Ok(url);
    }

    let default_url = "http://127.0.0.1:21000";
    info!(
        "No BCS URL configured, defaulting to local BCS: {}",
        default_url
    );
    normalize_url_from_source(default_url.to_string(), "default (local)")
}

fn resolve_bcs_url(args: &GlobalArgs) -> Result<String> {
    resolve_bcs_url_with_distribution_defaults(
        args,
        COMPILED_PRE_BCS_URL,
        COMPILED_PROD_BCS_URL,
    )
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
pub fn discover_token(explicit_token: Option<&str>) -> Result<String> {
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
pub fn get_token(token_arg: Option<&str>) -> Result<String> {
    discover_token(token_arg)
}

// ============================================================================
// Debug Macros
// ============================================================================

/// Print HTTP request in debug mode
#[macro_export]
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
#[macro_export]
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

/// Skill→BCS interactive debug
#[macro_export]
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
#[macro_export]
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

// ============================================================================
// Client Helper
// ============================================================================

/// Create a BCS client with optional cookie.
pub fn create_client(bcs_url: &str, token: &str, cookie: Option<&str>) -> BcsClient {
    // An empty token means no bot token is available; build without Bearer auth.
    if token.is_empty() {
        let mut client = BcsClient::new(bcs_url);
        if let Some(cookie) = cookie {
            client.set_cookie(cookie);
        }
        client
    } else if let Some(cookie) = cookie {
        BcsClient::with_token_and_cookie(bcs_url, token, cookie)
    } else {
        BcsClient::with_token(bcs_url, token)
    }
}

// ============================================================================
// Shared CLI Global Args
// ============================================================================

/// Global CLI arguments shared by all BCS tools.
#[derive(clap::Args, Debug)]
pub struct GlobalArgs {
    /// BCS API base URL (also reads BCS_API_BASE_URL/MOLTIS_BCS_URL)
    #[arg(short, long, env = "MOLTIS_BCS_URL")]
    pub url: Option<String>,

    /// Cookie header for authentication (also reads BCS_COOKIE)
    #[arg(short, long, env = "BCS_COOKIE")]
    pub cookie: Option<String>,

    /// Log level
    #[arg(short, long, default_value = "info")]
    pub log_level: String,

    /// Output in JSON format
    #[arg(short, long)]
    pub json: bool,

    /// Debug mode - print HTTP request/response details
    #[arg(short = 'D', long, env = "BCS_DEBUG", action = ArgAction::SetTrue)]
    pub debug: bool,
}

/// Resolved runtime configuration from global args + env vars.
pub struct RuntimeConfig {
    pub bcs_url: String,
    pub bcs_cookie: Option<String>,
    pub debug: bool,
    pub json: bool,
}

/// Initialize logging and resolve configuration from global args.
pub fn init(args: &GlobalArgs) -> Result<RuntimeConfig> {
    // Setup logging
    let level = match args.log_level.to_lowercase().as_str() {
        "trace" => Level::TRACE,
        "debug" => Level::DEBUG,
        "info" => Level::INFO,
        "warn" => Level::WARN,
        "error" => Level::ERROR,
        _ => Level::INFO,
    };
    FmtSubscriber::builder()
        .with_max_level(level)
        .with_target(false)
        .compact()
        .init();

    // Get current environment (defaults to "dev")
    let env = get_current_env();

    // Determine BCS URL: CLI arg > env var > session.json > compiled default > local
    let bcs_url = resolve_bcs_url(args)?;

    // Determine cookie: CLI arg > env var
    let bcs_cookie = args
        .cookie
        .clone()
        .or_else(|| std::env::var("BCS_COOKIE").ok());

    // Debug mode
    let debug = args.debug || std::env::var("BCS_DEBUG").is_ok_and(|v| v == "true");
    if debug {
        eprintln!(
            "\x1b[2m[→BCS] Environment: {} | BCS URL: {}\x1b[0m",
            env, bcs_url
        );
        if bcs_cookie.is_some() {
            eprintln!("\x1b[2m[→BCS] Cookie: (set)\x1b[0m");
        }
    }

    Ok(RuntimeConfig {
        bcs_url,
        bcs_cookie,
        debug,
        json: args.json,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serial_test::serial;
    use std::io::Write;
    use tempfile::TempDir;

    #[allow(unsafe_code)]
    fn safe_set_var(key: &str, value: impl AsRef<std::ffi::OsStr>) {
        unsafe {
            std::env::set_var(key, value);
        }
    }

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
    #[serial]
    fn test_resolve_bcs_url_from_session_file() {
        let temp_dir = TempDir::new().unwrap();
        write_session_file(
            &temp_dir,
            serde_json::json!({
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

        let args = GlobalArgs {
            url: None,
            cookie: None,
            log_level: "info".to_string(),
            json: false,
            debug: false,
        };
        let resolved = resolve_bcs_url(&args).unwrap();

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
    fn test_distribution_default_selects_pre_for_pre_and_prepub() {
        let pre_url = Some("https://pre.example.com/");
        let prod_url = Some("https://prod.example.com/");

        assert_eq!(
            resolve_distribution_default_url_for_env("pre", pre_url, prod_url).unwrap(),
            Some("https://pre.example.com".to_string())
        );
        assert_eq!(
            resolve_distribution_default_url_for_env("PREPUB", pre_url, prod_url).unwrap(),
            Some("https://pre.example.com".to_string())
        );
    }

    #[test]
    fn test_distribution_default_selects_prod_for_other_or_missing_env() {
        let pre_url = Some("https://pre.example.com");
        let prod_url = Some("https://prod.example.com");

        for runtime_env in ["prod", "gray", "dev", "local", ""] {
            assert_eq!(
                resolve_distribution_default_url_for_env(runtime_env, pre_url, prod_url).unwrap(),
                Some("https://prod.example.com".to_string())
            );
        }
    }

    #[test]
    #[serial]
    fn test_runtime_environment_priority_matches_legacy_cli() {
        let keys = [
            "AGENTCLAW_ENV",
            "env",
            "SERVER_ENV",
            "REAL_SERVER_ENV",
            "ALIPAY_APP_ENV",
        ];
        let originals: Vec<_> = keys
            .iter()
            .map(|key| std::env::var(key).ok())
            .collect();

        safe_set_var("AGENTCLAW_ENV", "prod");
        safe_set_var("env", "prepub");
        safe_set_var("SERVER_ENV", "pre");
        assert_eq!(get_current_env(), "prod");

        safe_remove_var("AGENTCLAW_ENV");
        assert_eq!(get_current_env(), "prepub");

        safe_remove_var("env");
        safe_set_var("SERVER_ENV", "prepub");
        assert_eq!(get_current_env(), "pre");

        for (key, original) in keys.into_iter().zip(originals) {
            if let Some(value) = original {
                safe_set_var(key, value);
            } else {
                safe_remove_var(key);
            }
        }
    }

    #[test]
    fn test_distribution_default_is_absent_for_public_build() {
        assert_eq!(
            resolve_distribution_default_url_for_env("pre", None, None).unwrap(),
            None
        );
    }

    #[test]
    fn test_distribution_default_rejects_partial_compiled_configuration() {
        let pre_only = resolve_distribution_default_url_for_env(
            "pre",
            Some("https://pre.example.com"),
            None,
        )
        .unwrap_err()
        .to_string();
        let prod_only = resolve_distribution_default_url_for_env(
            "prod",
            None,
            Some("https://prod.example.com"),
        )
        .unwrap_err()
        .to_string();

        assert!(pre_only.contains("must both be configured"));
        assert!(prod_only.contains("must both be configured"));
    }

    #[test]
    #[serial]
    fn test_compiled_distribution_defaults_match_build_mode() {
        let requested_pre_url = std::env::var("BCS_CLI_DEFAULT_PRE_URL").ok();
        let requested_prod_url = std::env::var("BCS_CLI_DEFAULT_PROD_URL").ok();
        match (requested_pre_url.as_deref(), requested_prod_url.as_deref()) {
            (None, None) => {
                assert_eq!(COMPILED_PRE_BCS_URL, None);
                assert_eq!(COMPILED_PROD_BCS_URL, None);
            }
            (Some(pre_url), Some(prod_url)) => {
                assert_eq!(COMPILED_PRE_BCS_URL, Some(pre_url));
                assert_eq!(COMPILED_PROD_BCS_URL, Some(prod_url));
            }
            _ => panic!("build environment must provide both compiled BCS defaults"),
        }

        let original_agentclaw_env = std::env::var("AGENTCLAW_ENV").ok();
        safe_set_var("AGENTCLAW_ENV", "pre");
        let pre_result = resolve_compiled_distribution_default_url();

        safe_set_var("AGENTCLAW_ENV", "prod");
        let prod_result = resolve_compiled_distribution_default_url();

        if let Some(value) = original_agentclaw_env {
            safe_set_var("AGENTCLAW_ENV", value);
        } else {
            safe_remove_var("AGENTCLAW_ENV");
        }

        match (COMPILED_PRE_BCS_URL, COMPILED_PROD_BCS_URL) {
            (None, None) => {
                assert_eq!(pre_result.unwrap(), None);
                assert_eq!(prod_result.unwrap(), None);
            }
            (Some(pre_url), Some(prod_url)) => {
                assert_eq!(pre_result.unwrap(), normalize_bcs_api_url(pre_url));
                assert_eq!(prod_result.unwrap(), normalize_bcs_api_url(prod_url));
            }
            _ => panic!("compiled BCS defaults must both be configured"),
        }
    }

    #[test]
    #[serial]
    fn test_session_url_overrides_distribution_default() {
        let temp_dir = TempDir::new().unwrap();
        write_session_file(
            &temp_dir,
            serde_json::json!({
                "bot_uuid": null,
                "token": "session-token",
                "api_base_url": "https://session.example.com"
            }),
        );

        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_url = std::env::var("MOLTIS_BCS_URL").ok();
        let original_base_url = std::env::var("BCS_API_BASE_URL").ok();

        safe_set_var("BOT_DATA_DIR", temp_dir.path());
        safe_remove_var("MOLTIS_BCS_URL");
        safe_remove_var("BCS_API_BASE_URL");

        let args = GlobalArgs {
            url: None,
            cookie: None,
            log_level: "info".to_string(),
            json: false,
            debug: false,
        };
        let resolved = resolve_bcs_url_with_distribution_defaults(
            &args,
            Some("https://pre.example.com"),
            Some("https://prod.example.com"),
        )
        .unwrap();

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

        assert_eq!(resolved, "https://session.example.com");
    }

    #[test]
    #[serial]
    fn test_resolve_bcs_url_defaults_to_local() {
        let original_data_dir = std::env::var("BOT_DATA_DIR").ok();
        let original_url = std::env::var("MOLTIS_BCS_URL").ok();
        let original_base_url = std::env::var("BCS_API_BASE_URL").ok();

        safe_remove_var("BOT_DATA_DIR");
        safe_remove_var("MOLTIS_BCS_URL");
        safe_remove_var("BCS_API_BASE_URL");

        let args = GlobalArgs {
            url: None,
            cookie: None,
            log_level: "info".to_string(),
            json: false,
            debug: false,
        };
        let result = resolve_bcs_url_with_distribution_defaults(&args, None, None);

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

        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "http://127.0.0.1:21000");
    }
}
