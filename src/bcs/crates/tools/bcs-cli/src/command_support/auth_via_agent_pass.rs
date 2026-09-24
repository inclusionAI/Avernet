//! command support implementation.
use super::*;


// disable agentpass, agentpass token should be auto injected into the http headers
pub(super) const AUTH_VIA_AGENT_PASS: bool = false;


pub(super) const DEFAULT_GROUP_BATCH_SIZE: u64 = 20;



#[derive(Debug, Serialize)]
pub(super) struct GroupListOutput {
    pub(super) items: Vec<serde_json::Value>,
    pub(super) offset: u64,
    pub(super) returned: u64,
    pub(super) total: u64,
    pub(super) has_more: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) next_offset: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) next_command: Option<String>,
}



#[derive(Debug, Serialize, Default)]
pub(super) struct StructuredResult {
    pub(super) status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) network_env: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) auth_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) timeout_secs: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) log_file: Option<String>,
}



pub(super) fn is_structured_mode(cli: &Cli) -> bool {
    // Default is structured (JSON) mode. --no-json disables it.
    // --json flag kept for backward compatibility (OpenClaw already passes it).
    !cli.no_json
}



pub(super) fn build_log_file_path() -> PathBuf {
    let dir = std::env::var("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
        .join(".bcs/logs");
    dir.join("bcs-cli.log")
}



pub(super) fn emit_structured_result(result: &StructuredResult) {
    println!(
        "{}",
        serde_json::to_string(result)
            .unwrap_or_else(|_| "{\"status\":\"request_failed\"}".to_string())
    );
}



pub(super) fn render_discover_result(
    result: &bcs_protocol::DiscoverBotsExtendedResponse,
    json_output: bool,
) -> Result<String> {
    if json_output {
        return Ok(serde_json::to_string_pretty(result)?);
    }

    let mut lines = vec![format!("Discovered {} bots:", result.count)];
    for bot in &result.bots {
        let name = bot.capabilities.name.as_deref().unwrap_or("unnamed");
        let vis = &bot.visibility;
        let friend_tag = match bot.is_friend {
            Some(true) => " ★friend",
            Some(false) | None => "",
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
        lines.push(format!(
            "  - {} ({}) [{}]{}{}{}",
            bot.bot_uuid, name, vis, friend_tag, provider_tag, agent_code_tag
        ));
    }

    Ok(lines.join("\n"))
}



pub(super) fn classify_auth_error_message(msg: &str) -> &'static str {
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
pub(super) fn resolve_oauth_credentials() -> (String, String) {
    let client_id = std::env::var("BCS_OAUTH_CLIENT_ID")
        .unwrap_or_else(|_| oauth::default_oauth_client_id().to_string());
    let client_secret = std::env::var("BCS_OAUTH_CLIENT_SECRET")
        .unwrap_or_else(|_| oauth::default_oauth_client_secret().to_string());
    (client_id, client_secret)
}



/// Get current environment from `AGENTCLAW_ENV` or `env` variable.
/// Priority: `AGENTCLAW_ENV` > `env` > `SERVER_ENV` chain > "dev" (default)
pub(super) fn get_current_env() -> String {
    std::env::var("AGENTCLAW_ENV")
        .or_else(|_| std::env::var("env"))
        .unwrap_or_else(|_| bcs_config::resolve_env_str())
}



// ============================================================================
// Token Discovery
// ============================================================================

/// Session info saved by BCN plugin (read-only for bcs-cli).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct SessionInfo {
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
pub(super) enum NetworkEnv {
    /// Office network - requires OAuth2 authentication through company gateway.
    Office,
    /// Production network - uses bot token only (default).
    Prod,
}



pub(super) fn session_file_path(data_dir: impl AsRef<Path>) -> PathBuf {
    data_dir.as_ref().join(".bcs").join("session.json")
}



pub(super) fn get_optional_session_file_path() -> Option<PathBuf> {
    std::env::var("BOT_DATA_DIR").ok().map(session_file_path)
}



pub(super) fn load_session_info_from_path(session_file: &Path) -> Result<Option<SessionInfo>> {
    if !session_file.exists() {
        return Ok(None);
    }

    let content = std::fs::read_to_string(session_file)
        .map_err(|e| anyhow!("Failed to read session file {:?}: {}", session_file, e))?;

    let session: SessionInfo = serde_json::from_str(&content)
        .map_err(|e| anyhow!("Failed to parse session file {:?}: {}", session_file, e))?;

    Ok(Some(session))
}



pub(super) fn load_optional_session_info() -> Result<Option<SessionInfo>> {
    let Some(session_file) = get_optional_session_file_path() else {
        return Ok(None);
    };
    load_session_info_from_path(&session_file)
}



pub(super) fn normalize_bcs_api_url(raw: &str) -> Option<String> {
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



pub(super) fn normalize_url_from_source(raw: String, source: &str) -> Result<String> {
    normalize_bcs_api_url(&raw)
        .ok_or_else(|| anyhow!("Invalid BCS API URL from {}: {}", source, raw))
}



pub(super) fn resolve_env_bcs_url() -> Result<Option<String>> {
    if let Ok(url) = std::env::var("BCS_API_BASE_URL") {
        return normalize_url_from_source(url, "BCS_API_BASE_URL").map(Some);
    }

    if let Ok(url) = std::env::var("MOLTIS_BCS_URL") {
        return normalize_url_from_source(url, "MOLTIS_BCS_URL").map(Some);
    }

    Ok(None)
}



pub(super) fn resolve_session_bcs_url() -> Result<Option<String>> {
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
pub(super) fn discover_token(explicit_token: Option<&str>) -> Result<String> {
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
pub(super) fn resolve_my_bot_uuid() -> Result<String> {
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
pub(super) fn parse_json_arg(raw: &str) -> Result<serde_json::Value> {
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



pub(super) fn build_panel_opening_message(
    component: Option<String>,
    params: Option<String>,
    tab_id: Option<String>,
    tab_title: Option<String>,
    tab_closable: Option<bool>,
) -> Result<Option<serde_json::Value>> {
    let Some(component) = component else {
        if params.is_some() || tab_id.is_some() || tab_title.is_some() || tab_closable.is_some() {
            return Err(anyhow!(
                "--panel-params and --panel-tab-* require --panel-component"
            ));
        }
        return Ok(None);
    };

    let mut opening_message = serde_json::Map::from_iter([
        ("type".to_string(), json!("panel")),
        ("component".to_string(), json!(component)),
    ]);
    if let Some(params) = params {
        let params = parse_json_arg(&params)?;
        if !params.is_object() {
            return Err(anyhow!("--panel-params must be a JSON object"));
        }
        opening_message.insert("params".to_string(), params);
    }

    let mut tab = serde_json::Map::new();
    if let Some(id) = tab_id {
        tab.insert("id".to_string(), json!(id));
    }
    if let Some(title) = tab_title {
        tab.insert("title".to_string(), json!(title));
    }
    if let Some(closable) = tab_closable {
        tab.insert("closable".to_string(), json!(closable));
    }
    if !tab.is_empty() {
        opening_message.insert("tab".to_string(), serde_json::Value::Object(tab));
    }

    Ok(Some(serde_json::Value::Object(opening_message)))
}



pub(super) fn merge_baas_session_id_into_meta(
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



/// Split a service session id at its canonical `{group_id}:<session_suffix>` boundary into its
/// `(group_id, session_id)` parts. `group_arg` always wins when supplied;
/// otherwise the colon split must succeed.
pub(super) fn split_service_sid<'a>(
    sid: &'a str,
    group_arg: Option<&'a str>,
) -> Result<(&'a str, &'a str)> {
    if let Some(group) = group_arg {
        return Ok((group, sid));
    }
    match sid.split_once(':') {
        Some((gid, _)) if !gid.is_empty() => Ok((gid, sid)),
        _ => Err(anyhow!(
            "Cannot infer group from session id '{}'. Expected '{{group}}:<session_suffix>' \
             or pass --group explicitly.",
            sid
        )),
    }
}



/// Poll a service-invocation session until it completes or the overall
/// budget runs out. Backoff: 500ms initial, doubles each iteration, capped
/// at 5_000ms. Returns the final session JSON, or an error wrapping the
/// last fetched payload when the timeout fires.
pub(super) async fn wait_for_service_completion(
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
pub(super) fn service_session_summary_lines(session: &serde_json::Value, header: &str) -> Vec<String> {
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



pub(super) fn print_service_session_summary(session: &serde_json::Value, header: &str) {
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



pub(super) const BCS_CLI_VERSION: &str = concat!(
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
pub(super) struct Cli {
    /// BCS API base URL (also reads BCS_API_BASE_URL/MOLTIS_BCS_URL)
    #[arg(short, long, env = "MOLTIS_BCS_URL")]
    pub(super) url: Option<String>,

    /// Cookie header for authentication (also reads BCS_COOKIE)
    #[arg(short, long, env = "BCS_COOKIE")]
    pub(super) cookie: Option<String>,

    /// Log level
    #[arg(short, long, default_value = "info")]
    pub(super) log_level: String,

    /// Output in JSON format (default: enabled). Use --no-json for interactive/human mode.
    #[arg(short, long)]
    pub(super) json: bool,

    /// Disable JSON output for interactive/human use
    #[arg(long)]
    pub(super) no_json: bool,

    /// Debug mode - print HTTP request/response details
    #[cfg(debug_assertions)]
    #[arg(short = 'D', long, env = "BCS_DEBUG", action = ArgAction::SetTrue)]
    pub(super) debug: bool,

    #[command(subcommand)]
    pub(super) command: Commands,
}



/// Parse skills input - supports JSON object array, JSON string array, and comma-separated format.
///
/// Examples:
/// - `[{"name":"sql","description":"SQL analysis"}]` → structured Skill objects
/// - `["sql","debug"]` → Skill objects with name only
/// - `"sql, debug"` → Skill objects with name only
pub(super) fn parse_skills_input(input: &str) -> Vec<bcs_protocol::Skill> {
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



pub(super) fn parse_custom_group_bindings(
    values: &[String],
) -> Result<BTreeMap<String, bcs_protocol::ParticipantBindingInfo>> {
    let mut bindings = BTreeMap::new();
    for value in values {
        let (raw_binding, raw_bot_id) = value.split_once('=').ok_or_else(|| {
            anyhow!("Invalid --binding '{}'; expected ROLE=BOT_UUID", value)
        })?;
        let binding = raw_binding.trim();
        let bot_id = raw_bot_id.trim();
        if binding.is_empty() || bot_id.is_empty() {
            return Err(anyhow!(
                "Invalid --binding '{}'; role and bot UUID must not be empty",
                value
            ));
        }
        if bindings.contains_key(binding) {
            return Err(anyhow!("Duplicate --binding role: {binding}"));
        }
        bindings.insert(
            binding.to_string(),
            bcs_protocol::ParticipantBindingInfo {
                source: "manual".to_string(),
                bot_ids: vec![bot_id.to_string()],
            },
        );
    }
    Ok(bindings)
}
