//! client implementation.
use super::*;


// ============================================================================
// BCS Client
// ============================================================================

/// Default BCS URL if not configured.
pub const DEFAULT_BCS_URL: &str = "http://localhost:21000";



/// Build a reqwest HTTP client with explicit cross-host redirect policy.
///
/// Follows up to 10 redirects, but reqwest's default behavior of stripping
/// `Authorization` on cross-host redirects is preserved (this is the standard
/// reqwest behaviour when `Policy::custom` is used with `attempt.follow()`).
/// We explicitly construct the policy instead of relying on `Client::default()`
/// so the cross-host auth isolation is auditable.
pub(super) fn build_http_client() -> reqwest::Client {
    let policy = reqwest::redirect::Policy::custom(move |attempt| {
        if attempt.previous().len() >= 10 {
            attempt.error("too many redirects")
        } else {
            // cross-host or same-host: follow; reqwest strips Authorization on
            // cross-host hops by default.
            attempt.follow()
        }
    });
    reqwest::Client::builder()
        .timeout(Duration::from_secs(120))
        .redirect(policy)
        .build()
        .expect("build reqwest client")
}



#[derive(Debug, Deserialize)]
pub struct BotGroupListPage {
    pub items: Vec<serde_json::Value>,
    pub total: u64,
    pub offset: u64,
    pub limit: u64,
}



/// Inputs for creating a state-machine group from authoring YAML.
#[derive(Debug)]
pub struct CreateCustomGroupOptions {
    pub id: Option<String>,
    pub driver_bot: String,
    pub participant_bindings: BTreeMap<String, ParticipantBindingInfo>,
    pub definition_yaml: String,
    pub context: Option<String>,
    pub topic: Option<String>,
    pub auto_start_on_service_invocation: bool,
}



#[derive(Debug)]
pub struct RunSessionCollaborationOptions {
    pub session_id: String,
    pub participant_bindings: BTreeMap<String, ParticipantBindingInfo>,
    pub definition_yaml: String,
    pub input: serde_json::Value,
    pub opening_message: Option<serde_json::Value>,
}



#[derive(Debug, Deserialize)]
pub struct CurrentActorGroupListPage {
    pub actor_id: String,
    pub items: Vec<serde_json::Value>,
    pub total: u64,
    pub offset: u64,
    pub limit: u64,
}



/// Client for interacting with the Bot Coordination Service.
#[derive(Debug, Clone)]
pub struct BcsClient {
    /// Base URL for the BCS.
    pub(super) base_url: String,
    /// HTTP client.
    pub(super) http_client: reqwest::Client,
    /// Optional Bearer token for authentication.
    pub(super) token: Option<String>,
    /// Optional Cookie header for authentication (for remote BCS).
    pub(super) cookie: Option<String>,
    /// Optional OAuth2 headers for office network authentication.
    pub(super) oauth_headers: Option<HashMap<String, String>>,
    /// Optional client identity (e.g., "bcs-cli/0.3.0") for X-BCS-Client header.
    pub(super) client_identity: Option<String>,
    /// Optional raw service key for `/services/*` routes. When set, it is sent
    /// as `X-BCS-Service-Key` for external caller attribution and replaces bot
    /// bearer / X-BCS-Bot-Token on the wire.
    pub(super) service_key: Option<String>,
    /// W3C Trace Context inherited from the Bash tool process. The CLI is a
    /// transparent carrier and never creates its own span.
    pub(super) traceparent: Option<HeaderValue>,
}



// ============================================================================
// CLI-internal chat outcome types (NOT bcs-protocol wire DTOs)
// ============================================================================

/// Structured result of a chat run poll flow, assembled by `bcs-cli` from
/// existing fields of [`ChatRunSubmitResponse`] / [`ChatRunStatusResponse`].
/// This is a CLI- internal type; it is NOT a `bcs-protocol` wire DTO and
/// introduces no new on-wire fields.
#[derive(Debug, Clone)]
pub struct ChatRunOutcome {
    pub delivery: Option<bcs_protocol::ChatRunDeliverySummary>,
    /// Did the run reach a success state?
    /// sync = `ChatRunState::Completed`; detach = `Running` or `Completed`.
    pub delivered: bool,
    /// Was the run created (`chat_async` returned a `run_id`)?
    /// False only on submit-level failure.
    pub submitted: bool,
    /// Run ID. `None` only on submit-level failure (no run was created).
    pub run_id: Option<String>,
    /// Session ID. `None` only on submit-level failure.
    pub session_id: Option<String>,
    /// Bot UUID. Filled from the submit/status response, or the CLI
    /// `--bot-uuid` arg on submit-level failure.
    pub bot_uuid: Option<String>,
    /// `running|completed|failed|cancelled|timeout|poll_error|
    ///  submit_failed|submit_indeterminate`
    pub state: String,
    /// Response content. Only meaningful for sync terminal runs.
    pub response_content: Option<String>,
    /// Error message (from server status, timeout, or poll error).
    pub error_message: Option<String>,
    /// Whether the server truncated `response.content`.
    pub content_truncated: bool,
}



/// Typed errors for `chat_async` submission. `main.rs` maps each variant to a
/// distinct [`ChatRunOutcome`] state so stdout is never empty and known IDs
/// are preserved.
#[derive(Debug)]
pub enum ChatAsyncError {
    /// `.send()` failed (connect refused/reset/client 10s timeout). A run
    /// MAY have been created server-side; its ID is unknown to the CLI.
    Transport(String),
    /// Server responded with a non-2xx status (it explicitly did not accept
    /// the run). No run was created.
    NotSuccessful { status: u16, body: String },
    /// Response body could not be deserialized as `ChatRunSubmitResponse`.
    InvalidResponse(String),

}

impl BcsClient {

///Create a new BCS client with the given base URL.
    pub fn new(base_url: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into(),
            http_client: build_http_client(),
            token: None,
            cookie: None,
            oauth_headers: None,
            client_identity: None,
            service_key: None,
            traceparent: None,
        }
    }

/// Create a client with a Bearer token for authentication.
    pub fn with_token(base_url: impl Into<String>, token: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into(),
            http_client: build_http_client(),
            token: Some(token.into()),
            cookie: None,
            oauth_headers: None,
            client_identity: None,
            service_key: None,
            traceparent: None,
        }
    }

/// Create a client with Bearer token and Cookie for authentication.
    pub fn with_token_and_cookie(
        base_url: impl Into<String>,
        token: impl Into<String>,
        cookie: impl Into<String>,
    ) -> Self {
        Self {
            base_url: base_url.into(),
            http_client: build_http_client(),
            token: Some(token.into()),
            cookie: Some(cookie.into()),
            oauth_headers: None,
            client_identity: None,
            service_key: None,
            traceparent: None,
        }
    }

/// Create a client with Bearer token and OAuth2 headers for office network authentication.
    pub fn with_token_and_oauth(
        base_url: impl Into<String>,
        token: impl Into<String>,
        oauth_headers: HashMap<String, String>,
    ) -> Self {
        Self {
            base_url: base_url.into(),
            http_client: build_http_client(),
            token: Some(token.into()),
            cookie: None,
            oauth_headers: Some(oauth_headers),
            client_identity: None,
            service_key: None,
            traceparent: None,
        }
    }

/// Create a client carrying a raw service key for `/services/*` routes.
    /// The key is sent as `X-BCS-Service-Key` on every request and supersedes
    /// any bot bearer token; the server hashes it via sha256 to look up the
    /// caller (`bcs-http/src/service_key.rs`).
    pub fn with_service_key(base_url: impl Into<String>, raw_key: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into(),
            http_client: build_http_client(),
            token: None,
            cookie: None,
            oauth_headers: None,
            client_identity: None,
            service_key: Some(raw_key.into()),
            traceparent: None,
        }
    }

/// Set the raw service key used for `X-BCS-Service-Key`.
    pub fn set_service_key(&mut self, raw_key: impl Into<String>) {
        self.service_key = Some(raw_key.into());
    }

/// Set the Bearer token for authentication.
    pub fn set_token(&mut self, token: impl Into<String>) {
        self.token = Some(token.into());
    }

/// Set the Cookie header for authentication.
    pub fn set_cookie(&mut self, cookie: impl Into<String>) {
        self.cookie = Some(cookie.into());
    }

/// Set the client identity for X-BCS-Client header (e.g., "bcs-cli/0.3.0").
    pub fn set_client_identity(&mut self, identity: impl Into<String>) {
        self.client_identity = Some(identity.into());
    }

/// Configure the opaque Trace Context propagated only on business dispatch
    /// requests. The gateway owns W3C validation; polling and management
    /// requests intentionally omit it.
    pub fn set_traceparent(&mut self, value: &str) -> Result<()> {
        let header = HeaderValue::from_str(value).context("invalid TRACEPARENT header value")?;
        self.traceparent = Some(header);
        Ok(())
    }

/// Get the current token.
    pub fn token(&self) -> Option<&str> {
        self.token.as_deref()
    }

/// Get the current cookie.
    pub fn cookie(&self) -> Option<&str> {
        self.cookie.as_deref()
    }

/// Set OAuth2 headers for office network authentication.
    pub fn set_oauth_headers(&mut self, headers: HashMap<String, String>) {
        self.oauth_headers = Some(headers);
    }

/// Create a client using the MOLTIS_BCS_URL environment variable.
    pub fn from_env() -> Self {
        let url = std::env::var("MOLTIS_BCS_URL").unwrap_or_else(|_| DEFAULT_BCS_URL.to_string());
        Self::new(url)
    }

/// Get the base URL.
    pub fn base_url(&self) -> &str {
        &self.base_url
    }

/// Health check for BCS.
    pub async fn health_check(&self) -> Result<bool> {
        let url = format!("{}/health", self.base_url);
        let response = self
            .add_headers(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to connect to BCS")?;

        Ok(response.status().is_success())
    }

/// Add authentication headers to a request builder.
    ///
    /// When OAuth2 headers are present (office network), the layout is:
    /// - `Authorization`: OAuth2 Bearer token (for Spanner gateway authentication)
    /// - `X-BCS-Bot-Token`: bot token (for BCS server bot identification)
    /// - `starpoint-data2` etc.: gateway device headers (passed through)
    /// - `User-Agent`: filtered out (SDK's custom UA breaks gateway routing)
    ///
    /// When no OAuth2 headers (prod), the layout is:
    /// - `Authorization: Bearer <bot_token>` (standard BCS auth)
    pub(super) fn add_headers(&self, builder: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        let has_oauth = self.oauth_headers.is_some();

        if has_oauth {
            let oauth_headers = self.oauth_headers.as_ref().expect("checked is_some above");
            let mut oauth_keys: Vec<&String> = oauth_headers.keys().collect();
            oauth_keys.sort();
            info!(
                "Preparing request with OAuth headers: oauth_keys={:?}, has_authorization={}, has_x_bcs_bot_token_from_sdk={}, bot_token_will_be_sent_via_x_bcs_bot_token=true",
                oauth_keys,
                oauth_headers
                    .keys()
                    .any(|k| k.eq_ignore_ascii_case("authorization")),
                oauth_headers
                    .keys()
                    .any(|k| k.eq_ignore_ascii_case("x-bcs-bot-token")),
            );
        }

        // Identity header. Service-key takes precedence: when set, send
        // `X-BCS-Service-Key` and skip the bot-token branches entirely
        // (the two are different identity spaces; never co-send).
        // - With OAuth (no svc-key): bot token goes to X-BCS-Bot-Token, OAuth takes Authorization
        // - Without OAuth (no svc-key): bot token goes to Authorization as usual
        let builder = if let Some(ref svc_key) = self.service_key {
            builder.header("X-BCS-Service-Key", svc_key.as_str())
        } else if let Some(ref token) = self.token {
            if has_oauth {
                builder.header("X-BCS-Bot-Token", token.as_str())
            } else {
                builder.bearer_auth(token)
            }
        } else {
            builder
        };

        let builder = if let Some(ref cookie) = self.cookie {
            builder.header("Cookie", cookie)
        } else {
            builder
        };

        // Inject OAuth2 headers for office network (Spanner gateway).
        // Skip User-Agent (SDK's custom UA breaks gateway routing).
        // Authorization from OAuth is kept (gateway needs it for user auth).
        let builder = if let Some(ref oauth_headers) = self.oauth_headers {
            let mut builder = builder;
            for (key, value) in oauth_headers {
                if key.eq_ignore_ascii_case("user-agent") {
                    continue;
                }
                builder = builder.header(key, value);
            }
            builder
        } else {
            builder
        };

        // Add X-BCS-Client header if client_identity is set.
        if let Some(ref identity) = self.client_identity {
            builder.header("X-BCS-Client", identity.as_str())
        } else {
            builder
        }
    }

/// Add Bearer token to a request builder if token is set.
    pub(super) fn add_auth(&self, builder: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        self.add_headers(builder)
    }

pub(super) fn add_chat_headers(&self, builder: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        self.add_auth(builder)
            .header(BCS_CHAT_VERSION_HEADER, BCS_CHAT_VERSION)
    }

pub(super) fn add_chat_dispatch_headers(
        &self,
        builder: reqwest::RequestBuilder,
        detach: bool,
        client_wait_timeout_ms: u64,
    ) -> reqwest::RequestBuilder {
        let builder = self
            .add_chat_headers(builder)
            .header("X-BCS-Client-Detach", detach.to_string())
            .header(
                "X-BCS-Client-Wait-Timeout-Ms",
                client_wait_timeout_ms.to_string(),
            );
        match &self.traceparent {
            Some(traceparent) => builder.header("traceparent", traceparent.clone()),
            None => builder,
        }
    }

/// Helper: check response status and parse JSON body.
    pub(super) async fn ensure_success(resp: reqwest::Response, label: &str) -> Result<serde_json::Value> {
        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(anyhow!("{} failed ({}): {}", label, status, body));
        }
        // 204 No Content carries no JSON body (e.g. DELETE returns 204). Return
        // Null instead of failing the `json()` parse — callers that print the
        // result render `null`/skip, and the best-effort cancel ignores it.
        if resp.status() == reqwest::StatusCode::NO_CONTENT {
            return Ok(serde_json::Value::Null);
        }
        resp.json()
            .await
            .with_context(|| format!("Invalid {} response", label))
    }

/// Helper: check response status only (no body parsing).
    pub(super) async fn ensure_success_status(resp: reqwest::Response, label: &str) -> Result<()> {
        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(anyhow!("{} failed ({}): {}", label, status, body));
        }
        Ok(())
    }

/// Join the BCS network (deprecated).
    ///
    /// # Deprecated
    /// This method is deprecated. Use WebSocket connection + `onboard` instead.
    /// The new flow is:
    /// 1. Connect to `/ws/bot` via WebSocket → BCS assigns bot_id and token
    /// 2. Call `onboard()` with Bearer token to register bot details
    #[deprecated(
        since = "0.4.0",
        note = "Use WebSocket connection + onboard instead. BCS no longer supports HTTP join."
    )]
    pub async fn join(
        &self,
        bot_id: &str,
        capabilities: Option<BotCapabilities>,
    ) -> Result<JoinResponse> {
        let url = format!("{}/bots/join", self.base_url);

        let payload = JoinRequest {
            bot_id: bot_id.to_string(),
            bot_name: None,
            engine_type: None,
            capabilities,
        };

        debug!(
            bot_id = %bot_id,
            url = %url,
            "Bot joining BCS network (deprecated)"
        );

        let response = self
            .http_client
            .post(&url)
            .json(&payload)
            .send()
            .await
            .context("Failed to send join request")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Join failed ({}): {}", status, body));
        }

        let result: JoinResponse = response.json().await.context("Invalid join response")?;

        info!(
            bot_id = %bot_id,
            "Bot joined BCS network successfully (deprecated)"
        );

        Ok(result)
    }

/// Connect a bot via HTTP (alternative to WebSocket bot.connect).
    ///
    /// This endpoint allows bots to connect via HTTP instead of WebSocket.
    /// Returns a session token for subsequent API calls.
    pub async fn connect(&self, params: BotConnectParams) -> Result<BotConnectResponse> {
        let url = format!("{}/bots/connect", self.base_url);

        debug!(
            token_present = params.token.is_some(),
            bot_id = ?params.bot_id,
            url = %url,
            "Bot connecting via HTTP"
        );

        let response = self
            .add_headers(self.http_client.post(&url))
            .json(&params)
            .send()
            .await
            .context("Failed to send connect request")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Connect failed ({}): {}", status, body));
        }

        let body = response
            .text()
            .await
            .context("Failed to read connect response body")?;
        let result: BotConnectResponse = match serde_json::from_str(&body) {
            Ok(r) => r,
            Err(e) => {
                // Check if this is a gateway error (e.g. USER_NOT_LOGIN)
                if let Ok(gw) = serde_json::from_str::<serde_json::Value>(&body) {
                    let error_code = gw.get("buserviceErrorCode").and_then(|v| v.as_str());
                    let biz_owner = gw.get("bizOwner").and_then(|v| v.as_str());
                    if let Some(code) = error_code {
                        let owner_hint = biz_owner
                            .map(|o| format!("若此问题持续出现，请联系负责人: {}", o))
                            .unwrap_or_default();
                        return Err(anyhow!("Gateway error [{}]{}", code, owner_hint));
                    }
                }
                return Err(anyhow!(e).context(format!("Invalid connect response: {}", body)));
            }
        };

        info!(
            bot_uuid = %result.bot_uuid,
            is_new = result.is_new,
            "Bot connected to BCS network via HTTP"
        );

        Ok(result)
    }

/// Fetch the onboard URL from the BCS server.
    ///
    /// The server generates the URL using its own `botchat_url` configuration,
    /// removing the need for the CLI to know the frontend base URL.
    ///
    /// Returns `Ok(Some(url))` on success, `Ok(None)` if the server does not
    /// support this endpoint (404) or is unreachable, and `Err` for server-side
    /// configuration errors (400) or other failures.
    pub async fn get_onboard_url(
        &self,
        token: &str,
        name: &str,
        summary: Option<&str>,
        skills: Option<&[Skill]>,
        domains: Option<&[String]>,
        scopes: Option<&[String]>,
        binding_channels: Option<&BindingChannels>,
    ) -> Result<Option<String>> {
        let mut url = format!(
            "{}/onboard/url?token={}&name={}",
            self.base_url,
            urlencoding::encode(token),
            urlencoding::encode(name),
        );

        if let Some(summary) = summary {
            if !summary.is_empty() {
                url.push_str(&format!("&summary={}", urlencoding::encode(summary)));
            }
        }

        if let Some(skills) = skills {
            if !skills.is_empty() {
                let skill_names: Vec<&str> = skills.iter().map(|s| s.name.as_str()).collect();
                url.push_str(&format!(
                    "&skills={}",
                    urlencoding::encode(&skill_names.join(","))
                ));
            }
        }

        if let Some(domains) = domains {
            if !domains.is_empty() {
                url.push_str(&format!(
                    "&domains={}",
                    urlencoding::encode(&domains.join(","))
                ));
            }
        }

        if let Some(scopes) = scopes {
            if !scopes.is_empty() {
                url.push_str(&format!(
                    "&scopes={}",
                    urlencoding::encode(&scopes.join(","))
                ));
            }
        }

        if let Some(channels) = binding_channels {
            if !channels.is_empty() {
                if let Ok(json) = serde_json::to_string(channels) {
                    url.push_str(&format!("&binding_channels={}", urlencoding::encode(&json)));
                }
            }
        }

        let response = match self.add_auth(self.http_client.get(&url)).send().await {
            Ok(resp) => resp,
            Err(_) => return Ok(None), // Connection failure → fallback
        };

        let status = response.status();

        if status == reqwest::StatusCode::NOT_FOUND {
            // Old server without this endpoint → fallback
            return Ok(None);
        }

        if status == reqwest::StatusCode::BAD_REQUEST {
            // Server config error (e.g. botchat_url not configured) → propagate
            let body = response.text().await.unwrap_or_default();
            anyhow::bail!("Server configuration error: {}", body);
        }

        if !status.is_success() {
            let body = response.text().await.unwrap_or_default();
            anyhow::bail!("Failed to get onboard URL ({}): {}", status, body);
        }

        let body: serde_json::Value = response
            .json()
            .await
            .context("Failed to parse onboard URL response")?;

        Ok(body
            .get("onboard_url")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string()))
    }

/// Onboard a bot with detailed information after WebSocket connection.
    ///
    /// This is called after a bot has established a WebSocket connection and
    /// received a token from BCS. The token is used for Bearer authentication.
    pub async fn onboard(
        &self,
        name: &str,
        summary: Option<&str>,
        skills: Option<Vec<Skill>>,
        domains: Option<Vec<String>>,
        scopes: Option<Vec<String>>,
        binding_channels: Option<BindingChannels>,
    ) -> Result<OnboardResponse> {
        let url = format!("{}/bots/onboard", self.base_url);

        let payload = OnboardRequest {
            name: name.to_string(),
            summary: summary.map(String::from),
            skills: skills.unwrap_or_default(),
            domains: domains.unwrap_or_default(),
            scopes: scopes.unwrap_or_default(),
            binding_channels,
        };

        debug!(
            name = %name,
            summary = ?summary,
            url = %url,
            "Bot onboarding to BCS network"
        );

        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to send onboard request")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Onboard failed ({}): {}", status, body));
        }

        let result: OnboardResponse = response.json().await.context("Invalid onboard response")?;

        info!(
            bot_id = %result.bot_uuid,
            name = %name,
            "Bot onboarded to BCS network successfully"
        );

        Ok(result)
    }

/// Update a bot's dynamic status using token authentication.
    pub async fn update_status_with_token(
        &self,
        status: BotDynamicStatus,
    ) -> Result<UpdateStatusResponse> {
        let url = format!("{}/bots/status", self.base_url);

        // We need to get bot_uuid from the server response, so we pass it in the request
        // The server derives bot_uuid from the token
        let payload = UpdateStatusRequest {
            bot_uuid: String::new(), // Server will derive from token
            status,
        };

        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to send status update")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Status update failed ({}): {}", status, body));
        }

        let result: UpdateStatusResponse =
            response.json().await.context("Invalid status response")?;

        Ok(result)
    }

/// Update a bot's dynamic status (deprecated - use update_status_with_token).
    #[deprecated(since = "0.4.0", note = "Use update_status_with_token instead")]
    pub async fn update_status(&self, bot_id: &str, status: BotDynamicStatus) -> Result<bool> {
        let url = format!("{}/bots/status", self.base_url);

        let payload = UpdateStatusRequest {
            bot_uuid: bot_id.to_string(),
            status,
        };

        let response = self
            .http_client
            .post(&url)
            .json(&payload)
            .send()
            .await
            .context("Failed to send status update")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Status update failed ({}): {}", status, body));
        }

        let result: UpdateStatusResponse =
            response.json().await.context("Invalid status response")?;

        Ok(result.updated)
    }
}
