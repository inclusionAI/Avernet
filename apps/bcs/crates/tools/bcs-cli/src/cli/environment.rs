use anyhow::Result;
use tracing::{debug, info};

use super::{CLI_LOG_TARGET, Cli};
use bcs_cli::BcsClient;

// Nothing below is redefined here: names shared with the library target come
// from `bcs_cli` (which owns `SessionInfo` and `get_current_env`) or from
// `super::shared_env` (the eight pure session/URL helpers). The `pub(super)`
// re-exports keep the existing bin call sites working. Reference the shared
// module as `super::shared_env`, never `crate::shared_env`: the internal overlay
// reuses `main.rs` through `#[path]`, where that path does not exist.
pub(super) use bcs_cli::{SessionInfo, get_current_env};
pub(super) use super::shared_env::{
    get_optional_session_file_path, load_optional_session_info, normalize_bcs_api_url,
};
use super::shared_env::{
    load_session_info_from_path, normalize_url_from_source, resolve_env_bcs_url,
    resolve_session_bcs_url,
};

// ============================================================================
// Network Environment and BCS URL Resolution
// ============================================================================

/// Network environment for BCS CLI.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum NetworkEnv {
    /// Office network - requires OAuth2 authentication through company gateway.
    Office,
    /// Production network - uses bot token only (default).
    Prod,
}

pub(super) fn normalize_bcs_ws_url(raw: &str) -> Option<String> {
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

pub(super) fn resolve_bcs_url(cli: &Cli) -> Result<String> {
    if let Some(url) = cli.url.as_ref() {
        return normalize_url_from_source(url.clone(), "--url");
    }

    if let Some(url) = resolve_env_bcs_url()? {
        return Ok(url);
    }

    if let Some(url) = resolve_session_bcs_url()? {
        return Ok(url);
    }

    if let Some(url) = bcs_cli::resolve_compiled_distribution_default_url()? {
        return Ok(url);
    }

    let default_url = "http://127.0.0.1:21000";
    info!(target: CLI_LOG_TARGET,
        "No BCS URL configured, defaulting to local BCS: {}",
        default_url
    );
    normalize_url_from_source(default_url.to_string(), "default (local)")
}

// ============================================================================
// Token Discovery
// ============================================================================

// Intentionally NOT shared with the library's `discover_token`: this variant
// carries CallerContext resolution, `BCN_AUTH_MODE=agentpass-subbots`, and the
// `CLI_LOG_TARGET` logging target. Unifying them is out of scope for the cleanup.

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
pub(super) fn discover_token(explicit_token: Option<&str>) -> Result<String> {
    let caller = bcs_cli::CallerContext::from_env_with_token(explicit_token)?;
    if std::env::var("BCN_AUTH_MODE").ok().as_deref() == Some("agentpass-subbots") {
        return Ok(caller.select_token(explicit_token, None, None).unwrap_or_default().to_string());
    }
    // 1. Use explicit token if provided
    if let Some(token) = explicit_token {
        if !token.is_empty() {
            return Ok(token.to_string());
        }
    }

    // 2. Check BCN_BOT_TOKEN environment variable (set by BCN plugin for child processes)
    if let Ok(token) = std::env::var("BCN_BOT_TOKEN") {
        if !token.is_empty() {
            debug!(target: CLI_LOG_TARGET, "Using BCN_BOT_TOKEN from environment");
            return Ok(token);
        }
    }

    // 3. Check session file in BOT_DATA_DIR (optional — missing dir or file is not an error)
    if let Some(session_file) = get_optional_session_file_path() {
        if let Some(session) = load_session_info_from_path(&session_file)? {
            if !session.token.is_empty() {
                debug!(target: CLI_LOG_TARGET, "Using token from session file");
                return Ok(session.token);
            }
        }
    }

    // No token found — return empty to allow unauthenticated requests.
    debug!(target: CLI_LOG_TARGET, "No token found, proceeding without authentication");
    Ok(String::new())
}

/// Get optional token from CLI argument.
pub(super) fn get_token(token_arg: Option<&str>) -> Result<String> {
    discover_token(token_arg)
}

/// Resolve the current bot's UUID from session file.
///
/// Priority:
/// 1. $BOT_DATA_DIR/.bcs/session.json `bot_uuid` field
///
/// This is used by friend/visibility commands that operate on "my" bot
/// without requiring the user to specify their own bot_uuid.
pub(super) async fn resolve_my_bot_uuid(client: &BcsClient) -> Result<String> {
    if let Some(actor) = bcs_cli::CallerContext::from_env_with_token(client.token())?.actor_id() { return Ok(actor.into()); }
    if let Ok(Some(session)) = load_optional_session_info() {
        if client.token().is_some_and(|token| !token.is_empty() && token == session.token) {
            if let Some(uuid) = session.bot_uuid.filter(|uuid| !uuid.is_empty()) { return Ok(uuid); }
        }
    }
    client.current_bot_uuid().await
}

/// Auto-detect network environment by probing the BCS URL.
///
/// Sends an unauthenticated GET to `$bcs_url/health`. If the Spanner gateway
/// intercepts with a login redirect (body contains "USER_NOT_LOGIN"),
/// we're on the office network and need OAuth2. Otherwise, assume prod.
/// Localhost URLs skip the probe entirely (always Prod).
pub(super) async fn detect_network_env(bcs_url: &str, structured_mode: bool) -> NetworkEnv {
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
pub(super) fn is_localhost_url(url: &str) -> bool {
    let without_scheme = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"))
        .unwrap_or(url);
    let host = without_scheme.split('/').next().unwrap_or("");
    let host = host.split(':').next().unwrap_or(host);
    host == "localhost" || host == "127.0.0.1" || host == "::1"
}
