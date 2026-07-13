//! HTTP helper used by the `bcs-cli` binaries.
//!
//! This is a tool-local client, not a public SDK. Shared wire DTOs live in
//! `bcs-protocol`.

use std::collections::HashMap;
use std::time::Duration;

use anyhow::{Context, Result, anyhow};
use tracing::{debug, info, warn};

use bcs_protocol::{
    BCS_CHAT_VERSION, BCS_CHAT_VERSION_HEADER, BindingChannels, BotCapabilities, BotConnectParams,
    BotConnectResponse, BotDynamicStatus, BotInfo, ChatRunCancelResponse, ChatRunState,
    ChatRunStatusResponse, ChatRunSubmitResponse, ConfirmProposalResponse, CreateGroupRequest,
    CreateGroupResponse, DiscoverBotsExtendedResponse, DiscoverBotsResponse, FriendApiResponse,
    FusionRequest, FusionResponse, JoinRequest, JoinResponse, OnboardRequest, OnboardResponse,
    ParticipantInfo, ProposalResponse, QueryBotEntry, QueryBotsRequest, SetVisibilityRequest,
    Skill, UpdateStatusRequest, UpdateStatusResponse,
};

// ============================================================================
// BCS Client
// ============================================================================

/// Default BCS URL if not configured.
pub const DEFAULT_BCS_URL: &str = "http://localhost:21000";

/// Client for interacting with the Bot Coordination Service.
#[derive(Debug, Clone)]
pub struct BcsClient {
    /// Base URL for the BCS.
    base_url: String,
    /// HTTP client.
    http_client: reqwest::Client,
    /// Optional Bearer token for authentication.
    token: Option<String>,
    /// Optional Cookie header for authentication (for remote BCS).
    cookie: Option<String>,
    /// Optional OAuth2 headers for office network authentication.
    oauth_headers: Option<HashMap<String, String>>,
    /// Optional client identity (e.g., "bcs-cli/0.3.0") for X-BCS-Client header.
    client_identity: Option<String>,
    /// Optional raw service key for `/services/*` routes. When set, it is sent
    /// as `X-BCS-Service-Key` for external caller attribution and replaces bot
    /// bearer / X-BCS-Bot-Token on the wire.
    service_key: Option<String>,
}

impl BcsClient {
    ///Create a new BCS client with the given base URL.
    pub fn new(base_url: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into(),
            http_client: reqwest::Client::builder()
                .timeout(Duration::from_secs(120))
                .build()
                .unwrap_or_else(|_| reqwest::Client::new()),
            token: None,
            cookie: None,
            oauth_headers: None,
            client_identity: None,
            service_key: None,
        }
    }

    /// Create a client with a Bearer token for authentication.
    pub fn with_token(base_url: impl Into<String>, token: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into(),
            http_client: reqwest::Client::builder()
                .timeout(Duration::from_secs(120))
                .build()
                .unwrap_or_else(|_| reqwest::Client::new()),
            token: Some(token.into()),
            cookie: None,
            oauth_headers: None,
            client_identity: None,
            service_key: None,
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
            http_client: reqwest::Client::builder()
                .timeout(Duration::from_secs(120))
                .build()
                .unwrap_or_else(|_| reqwest::Client::new()),
            token: Some(token.into()),
            cookie: Some(cookie.into()),
            oauth_headers: None,
            client_identity: None,
            service_key: None,
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
            http_client: reqwest::Client::builder()
                .timeout(Duration::from_secs(120))
                .build()
                .unwrap_or_else(|_| reqwest::Client::new()),
            token: Some(token.into()),
            cookie: None,
            oauth_headers: Some(oauth_headers),
            client_identity: None,
            service_key: None,
        }
    }

    /// Create a client carrying a raw service key for `/services/*` routes.
    /// The key is sent as `X-BCS-Service-Key` on every request and supersedes
    /// any bot bearer token; the server hashes it via sha256 to look up the
    /// caller (`bcs-http/src/service_key.rs`).
    pub fn with_service_key(base_url: impl Into<String>, raw_key: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into(),
            http_client: reqwest::Client::builder()
                .timeout(Duration::from_secs(120))
                .build()
                .unwrap_or_else(|_| reqwest::Client::new()),
            token: None,
            cookie: None,
            oauth_headers: None,
            client_identity: None,
            service_key: Some(raw_key.into()),
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
    fn add_headers(&self, builder: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
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
    fn add_auth(&self, builder: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        self.add_headers(builder)
    }

    fn add_chat_headers(&self, builder: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        self.add_auth(builder)
            .header(BCS_CHAT_VERSION_HEADER, BCS_CHAT_VERSION)
    }

    // ========================================================================
    // Bot Lifecycle
    // ========================================================================

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

    // ========================================================================
    // Bot Discovery
    // ========================================================================

    /// Get a specific bot's info.
    pub async fn get_bot(&self, bot_id: &str) -> Result<BotInfo> {
        let url = format!("{}/bots/{}", self.base_url, bot_id);

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to get bot info")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Bot '{}' not found ({}): {}", bot_id, status, body));
        }

        // Server may return HTTP 200 with an error JSON body (e.g. {"error":"Bot not found","status":404}).
        // Parse as generic JSON first to detect this case.
        let body = response
            .text()
            .await
            .context("Failed to read response body")?;
        let json_value: serde_json::Value =
            serde_json::from_str(&body).context("Invalid JSON response")?;

        if let Some(error_msg) = json_value.get("error").and_then(|e| e.as_str()) {
            let status_code = json_value
                .get("status")
                .and_then(|s| s.as_u64())
                .unwrap_or(500);
            return Err(anyhow!(
                "Bot '{}' not found ({}): {}",
                bot_id,
                status_code,
                error_msg
            ));
        }

        let bot: BotInfo =
            serde_json::from_value(json_value).context("Invalid bot info response")?;

        Ok(bot)
    }

    /// List all registered bots.
    pub async fn list_bots(&self) -> Result<Vec<BotInfo>> {
        let url = format!("{}/bots?onboarded=true", self.base_url);

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to list bots")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("List bots failed ({}): {}", status, body));
        }

        // Server returns array directly: [{...}, {...}]
        let bots: Vec<BotInfo> = response
            .json()
            .await
            .context("Invalid bots list response")?;

        Ok(bots)
    }

    /// Discover bots by capability keywords.
    pub async fn discover_bots(&self, query: Option<&str>) -> Result<DiscoverBotsResponse> {
        let mut url = format!("{}/bots/discover", self.base_url);
        if let Some(q) = query {
            url.push_str(&format!("?q={}", urlencoding::encode(q)));
        }

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to discover bots")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Discover bots failed ({}): {}", status, body));
        }

        let result: DiscoverBotsResponse =
            response.json().await.context("Invalid discover response")?;

        Ok(result)
    }

    /// Discover bots with extended filtering (visibility, collaborate_bot).
    ///
    /// Returns `DiscoverBotsExtendedResponse` which includes `visibility` and
    /// `is_friend` fields per bot entry.
    ///
    /// **Important**: `collaborate_bot` is NOT a caller-identity parameter. It specifies
    /// a bot whose collaboration scope you want to view (public bots + that bot's friends).
    /// Do not pass a Private Bot's own UUID as `collaborate_bot` to mean "self search" —
    /// that will return an empty list because Private Bots cannot initiate collaboration.
    /// For plain directory search, omit `collaborate_bot` entirely.
    pub async fn discover_bots_extended(
        &self,
        query: Option<&str>,
        visibility: Option<&str>,
        collaborate_bot: Option<&str>,
        organization_code: Option<&str>,
        role: Option<&str>,
    ) -> Result<DiscoverBotsExtendedResponse> {
        let mut params: Vec<String> = Vec::new();
        if let Some(q) = query {
            params.push(format!("q={}", urlencoding::encode(q)));
        }
        if let Some(vis) = visibility {
            params.push(format!("visibility={}", urlencoding::encode(vis)));
        }
        if let Some(collab) = collaborate_bot {
            params.push(format!("collaborate_bot={}", urlencoding::encode(collab)));
        }
        if let Some(code) = organization_code.map(str::trim).filter(|code| !code.is_empty()) {
            params.push(format!("organization_code={}", urlencoding::encode(code)));
        }
        if let Some(role) = role.map(str::trim).filter(|role| !role.is_empty()) {
            params.push(format!("role={}", urlencoding::encode(role)));
        }

        let mut url = format!("{}/bots/discover", self.base_url);
        if !params.is_empty() {
            url.push('?');
            url.push_str(&params.join("&"));
        }

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to discover bots")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Discover bots failed ({}): {}", status, body));
        }

        let result: DiscoverBotsExtendedResponse =
            response.json().await.context("Invalid discover response")?;

        Ok(result)
    }

    /// Find bots by skills.
    pub async fn find_bots_by_skills(&self, skills: &[&str]) -> Result<Vec<BotInfo>> {
        let url = format!(
            "{}/bots/discover?skills={}",
            self.base_url,
            skills.join(",")
        );

        let response = self
            .http_client
            .get(&url)
            .send()
            .await
            .context("Failed to find bots by skills")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Find bots failed ({}): {}", status, body));
        }

        let result: DiscoverBotsResponse = response.json().await.context("Invalid response")?;

        Ok(result.bots)
    }

    // ========================================================================
    // Group Chat Proposals
    // ========================================================================

    /// Evaluate and propose a group chat using token authentication.
    /// The bot_uuid is derived from the token on the server side.
    pub async fn propose_group_chat_with_token(
        &self,
        topic: &str,
        suggested_participants: Option<Vec<String>>,
        suggested_driver: Option<&str>,
    ) -> Result<ProposalResponse> {
        let url = format!("{}/groups/request", self.base_url);

        let payload = serde_json::json!({
            "topic": topic,
            "suggested_participants": suggested_participants.unwrap_or_default(),
            "suggested_driver": suggested_driver,
        });

        debug!(
            topic = %topic,
            "Proposing group chat"
        );

        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to propose group chat")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Proposal failed ({}): {}", status, body));
        }

        // 先获取原始响应内容，便于调试
        let body = response
            .text()
            .await
            .context("Failed to read response body")?;
        eprintln!("[DEBUG] Response body: {}", body);

        let result: ProposalResponse = serde_json::from_str(&body)
            .with_context(|| format!("Invalid proposal response. Raw body: {}", body))?;

        Ok(result)
    }

    /// Confirm a proposal.
    pub async fn confirm_proposal(&self, confirm_url: &str) -> Result<ConfirmProposalResponse> {
        // Handle relative URLs by prepending http://
        let url = if confirm_url.starts_with("http://") || confirm_url.starts_with("https://") {
            confirm_url.to_string()
        } else {
            format!("http://{}", confirm_url)
        };

        let response = self
            .http_client
            .post(&url)
            .send()
            .await
            .context("Failed to confirm proposal")?;

        let status = response.status();
        let body = response.text().await.unwrap_or_default();

        if !status.is_success() {
            return Err(anyhow!("Confirmation failed ({}): {}", status, body));
        }

        // Server may return HTTP 200 with an error JSON body.
        let json_value: serde_json::Value = serde_json::from_str(&body).with_context(|| {
            format!(
                "Invalid confirmation response (not JSON): {}",
                &body[..body.len().min(200)]
            )
        })?;

        if let Some(error_msg) = json_value.get("error").and_then(|e| e.as_str()) {
            let error_status = json_value
                .get("status")
                .and_then(|s| s.as_u64())
                .unwrap_or(500);
            return Err(anyhow!(
                "Confirmation failed ({}): {}",
                error_status,
                error_msg
            ));
        }

        let result: ConfirmProposalResponse =
            serde_json::from_value(json_value).context("Invalid confirmation response")?;

        Ok(result)
    }

    // ========================================================================
    // Cross-Bot Communication
    // ========================================================================

    /// Invoke a method on another bot.
    ///
    /// # Deprecated
    /// This method bypasses BCS and makes direct HTTP calls to bots.
    /// Use `chat()` instead, which routes through BCS for proper mediation.
    #[deprecated(
        since = "0.3.0",
        note = "Use `chat()` instead. All bot communication must go through BCS for mediation."
    )]
    pub async fn invoke_bot(
        &self,
        _bot_id: &str,
        _method: &str,
        _params: serde_json::Value,
    ) -> Result<serde_json::Value> {
        anyhow::bail!(
            "invoke_bot is deprecated. All bot communication must go through BCS. Use `chat()` instead."
        )
    }

    const CHAT_TIMEOUT_MAX_MS: u64 = 300_000;
    // Keep the HTTP client alive slightly longer than the server-side wait window
    // so the caller sees the server's timeout result instead of an earlier client timeout.
    const CHAT_TIMEOUT_BUFFER_MS: u64 = 5_000;

    fn effective_chat_timeout_ms(timeout_ms: Option<u64>) -> u64 {
        timeout_ms
            .unwrap_or(Self::CHAT_TIMEOUT_MAX_MS)
            .min(Self::CHAT_TIMEOUT_MAX_MS)
    }

    fn chat_request_timeout(timeout_ms: Option<u64>) -> Duration {
        Duration::from_millis(
            Self::effective_chat_timeout_ms(timeout_ms)
                .saturating_add(Self::CHAT_TIMEOUT_BUFFER_MS),
        )
    }

    fn chat_payload(
        message: &str,
        from: Option<&str>,
        effective_timeout_ms: u64,
        session_id: Option<&str>,
        tags: &[String],
    ) -> serde_json::Value {
        let mut payload = serde_json::json!({
            "message": message,
            "from": from,
            "timeout_ms": effective_timeout_ms,
        });
        if let Some(sid) = session_id {
            if let Some(obj) = payload.as_object_mut() {
                obj.insert(
                    "session_id".to_string(),
                    serde_json::Value::String(sid.to_string()),
                );
            }
        }
        let tags = Self::normalize_tags(tags);
        if !tags.is_empty() {
            if let Some(obj) = payload.as_object_mut() {
                obj.insert("tags".to_string(), serde_json::json!(tags));
            }
        }
        payload
    }

    fn normalize_tags(tags: &[String]) -> Vec<String> {
        tags.iter()
            .map(|tag| tag.trim().to_string())
            .filter(|tag| !tag.is_empty())
            .collect()
    }

    fn chat_request_builder(
        &self,
        url: &str,
        payload: &serde_json::Value,
        timeout_ms: Option<u64>,
    ) -> reqwest::RequestBuilder {
        self.add_chat_headers(
            self.http_client
                .post(url)
                .json(payload)
                .timeout(Self::chat_request_timeout(timeout_ms)),
        )
    }

    /// Send a chat message to another bot via BCS routing.
    ///
    /// BCS looks up the target bot's URL and forwards the message.
    /// This ensures fresh URLs and centralized logging.
    pub async fn chat(
        &self,
        bot_id: &str,
        message: &str,
        from: Option<&str>,
        timeout_ms: Option<u64>,
    ) -> Result<serde_json::Value> {
        self.chat_with_session(bot_id, message, from, None, &[], timeout_ms)
            .await
    }

    /// Like [`Self::chat`] but lets the caller share a session across calls.
    pub async fn chat_with_session(
        &self,
        bot_id: &str,
        message: &str,
        from: Option<&str>,
        session_id: Option<&str>,
        tags: &[String],
        timeout_ms: Option<u64>,
    ) -> Result<serde_json::Value> {
        let effective_timeout_ms = Self::effective_chat_timeout_ms(timeout_ms);

        if let Some(requested_timeout_ms) = timeout_ms {
            if requested_timeout_ms > Self::CHAT_TIMEOUT_MAX_MS {
                warn!(
                    requested_timeout_ms = requested_timeout_ms,
                    effective_timeout_ms = effective_timeout_ms,
                    "Clamped chat timeout to configured maximum"
                );
            }
        }

        let url = format!("{}/bots/{}/chat", self.base_url, bot_id);

        let payload = Self::chat_payload(message, from, effective_timeout_ms, session_id, tags);

        debug!(
            bot_id = %bot_id,
            from = ?from,
            session_id = ?session_id,
            tags = ?Self::normalize_tags(tags),
            timeout_ms = effective_timeout_ms,
            message_len = message.len(),
            "Sending chat message via BCS"
        );

        let response = self
            .chat_request_builder(&url, &payload, timeout_ms)
            .send()
            .await
            .context("Failed to send chat message")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Chat failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response.json().await.context("Invalid chat response")?;

        Ok(result)
    }

    // ========================================================================
    // Async chat run flow (submit + long-poll + cancel)
    // ========================================================================

    /// Submit a chat run without waiting for the response. Returns immediately
    /// with a `run_id` that can be polled via [`Self::chat_run_status`].
    ///
    /// `run_timeout_ms` is the server-side wall-clock budget (pending +
    /// running) before the run is auto-expired. Pass `None` to accept the
    /// server default (30 min).
    pub async fn chat_async(
        &self,
        bot_id: &str,
        message: &str,
        from: Option<&str>,
        session_id: Option<&str>,
        tags: &[String],
        response_mode: Option<&str>,
        caller_wait_mode: Option<&str>,
        run_timeout_ms: Option<u64>,
        organization_code: Option<&str>,
    ) -> Result<ChatRunSubmitResponse> {
        let url = format!("{}/bots/{}/chat-async", self.base_url, bot_id);
        let payload = Self::chat_async_payload(
            message,
            from,
            session_id,
            tags,
            response_mode,
            caller_wait_mode,
            run_timeout_ms,
            organization_code,
        );

        let response = self
            .add_chat_headers(
                self.http_client
                    .post(&url)
                    .json(&payload)
                    .timeout(Duration::from_secs(10)),
            )
            .send()
            .await
            .context("Failed to submit chat_async")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("chat_async failed ({}): {}", status, body));
        }

        let submit: ChatRunSubmitResponse = response
            .json()
            .await
            .context("Invalid chat_async response")?;
        Ok(submit)
    }

    fn chat_async_payload(
        message: &str,
        from: Option<&str>,
        session_id: Option<&str>,
        tags: &[String],
        response_mode: Option<&str>,
        caller_wait_mode: Option<&str>,
        run_timeout_ms: Option<u64>,
        organization_code: Option<&str>,
    ) -> serde_json::Value {
        let mut payload = serde_json::json!({
            "message": message,
            "from": from,
        });
        if let Some(ms) = run_timeout_ms {
            payload
                .as_object_mut()
                .unwrap()
                .insert("timeout_ms".to_string(), serde_json::json!(ms));
        }
        if let Some(sid) = session_id {
            payload.as_object_mut().unwrap().insert(
                "session_id".to_string(),
                serde_json::Value::String(sid.to_string()),
            );
        }
        let tags = Self::normalize_tags(tags);
        if !tags.is_empty() {
            payload
                .as_object_mut()
                .unwrap()
                .insert("tags".to_string(), serde_json::json!(tags));
        }
        if let Some(mode) = response_mode {
            payload.as_object_mut().unwrap().insert(
                "response_mode".to_string(),
                serde_json::Value::String(mode.to_string()),
            );
        }
        if let Some(mode) = caller_wait_mode.map(str::trim).filter(|mode| !mode.is_empty()) {
            if let Some(object) = payload.as_object_mut() {
                object.insert(
                    "caller_wait_mode".to_string(),
                    serde_json::Value::String(mode.to_string()),
                );
            }
        }
        if let Some(code) = organization_code.map(str::trim).filter(|code| !code.is_empty()) {
            if let Some(object) = payload.as_object_mut() {
                object.insert(
                    "organization_code".to_string(),
                    serde_json::Value::String(code.to_string()),
                );
            }
        }
        payload
    }

    /// Fetch the current status of a chat run. If `wait_ms > 0`, the server
    /// will long-poll until the run's `version` advances past `since_version`,
    /// the run reaches a terminal state, or `wait_ms` elapses.
    pub async fn chat_run_status(
        &self,
        run_id: &str,
        since_version: Option<u64>,
        wait_ms: Option<u64>,
    ) -> Result<ChatRunStatusResponse> {
        let wait_ms = wait_ms.unwrap_or(0);
        let url = format!("{}/chat/runs/{}", self.base_url, run_id);
        let mut builder = self
            .http_client
            .get(&url)
            .timeout(Duration::from_millis(wait_ms.saturating_add(10_000)));
        builder = builder.query(&[
            ("wait_ms", wait_ms.to_string()),
            ("since_version", since_version.unwrap_or(0).to_string()),
        ]);
        let response = self
            .add_chat_headers(builder)
            .send()
            .await
            .context("Failed to poll chat run")?;
        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("chat_run_status failed ({}): {}", status, body));
        }
        response
            .json()
            .await
            .context("Invalid chat_run_status response")
    }

    /// Cancel a chat run. Idempotent: returns `cancelled=false` if the run was
    /// already terminal.
    pub async fn chat_run_cancel(&self, run_id: &str) -> Result<ChatRunCancelResponse> {
        let url = format!("{}/chat/runs/{}/cancel", self.base_url, run_id);
        let response = self
            .add_chat_headers(self.http_client.post(&url).timeout(Duration::from_secs(10)))
            .send()
            .await
            .context("Failed to cancel chat run")?;
        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("chat_run_cancel failed ({}): {}", status, body));
        }
        response
            .json()
            .await
            .context("Invalid chat_run_cancel response")
    }

    /// Submit a chat run and poll until it reaches a terminal state. Returns
    /// a JSON value shaped like the legacy [`Self::chat`] response plus a few
    /// additive fields (`run_id`, `state`, `session_id`, `error_message`).
    ///
    /// `overall_timeout_ms` caps the total wall-clock spent polling. On
    /// overflow the run is best-effort cancelled and an error is returned.
    /// `poll_wait_ms` controls each long-poll HTTP hop (default 15 s, capped
    /// at the server's configured max).
    pub async fn chat_polling(
        &self,
        bot_id: &str,
        message: &str,
        from: Option<&str>,
        session_id: Option<&str>,
        tags: &[String],
        response_mode: Option<&str>,
        overall_timeout_ms: Option<u64>,
        poll_wait_ms: Option<u64>,
        organization_code: Option<&str>,
    ) -> Result<serde_json::Value> {
        let overall_timeout = Duration::from_millis(overall_timeout_ms.unwrap_or(30 * 60 * 1_000));
        let poll_wait_ms = poll_wait_ms.unwrap_or(15_000);

        let submit = self
            .chat_async(
                bot_id,
                message,
                from,
                session_id,
                tags,
                response_mode,
                None,
                overall_timeout_ms,
                organization_code,
            )
            .await?;
        let run_id = submit.run_id.clone();
        let reported_session = submit.session_id.clone();
        debug!(
            run_id = %run_id,
            session_id = %reported_session,
            "chat_polling: submitted"
        );

        let deadline = tokio::time::Instant::now() + overall_timeout;
        let mut since_version: u64 = 0;
        loop {
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            if remaining.is_zero() {
                warn!(run_id = %run_id, "chat_polling: overall timeout, cancelling run");
                let _ = self.chat_run_cancel(&run_id).await;
                return Err(anyhow!(
                    "chat_polling: overall timeout after {} ms",
                    overall_timeout.as_millis()
                ));
            }
            let wait_ms = remaining.as_millis().min(poll_wait_ms as u128) as u64;
            let status = self
                .chat_run_status(&run_id, Some(since_version), Some(wait_ms))
                .await?;
            since_version = status.version;
            if status.is_terminal() {
                let delivered = matches!(status.state, ChatRunState::Completed);
                let state_str = status.state.as_str();
                if !delivered {
                    return Err(anyhow!(
                        "chat_polling: run {} ended in state {}: {}",
                        run_id,
                        state_str,
                        status
                            .error_message
                            .as_deref()
                            .unwrap_or("<no error message>"),
                    ));
                }
                return Ok(serde_json::json!({
                    "delivered": delivered,
                    "bot_uuid": status.bot_uuid,
                    "run_id": status.run_id,
                    "session_id": status.session_id,
                    "state": state_str,
                    "response": {"content": status.response.content},
                    "error_message": status.error_message,
                    "content_truncated": status.content_truncated,
                }));
            }
        }
    }

    /// Submit a chat run and detach once the server observes an acknowledgement
    /// state for the caller's wait mode. For chat schema v2, `submitted` means
    /// the Provider accepted the request but has not confirmed downstream
    /// delivery yet, so this waits until `running` (or a legacy `completed`).
    ///
    /// `overall_timeout_ms` caps the total wall-clock spent waiting for the
    /// detach acknowledgement. On overflow the run is left running on the
    /// server side and an error is returned.
    /// `poll_wait_ms` controls each long-poll HTTP hop (default 15 s).
    pub async fn chat_polling_detach(
        &self,
        bot_id: &str,
        message: &str,
        from: Option<&str>,
        session_id: Option<&str>,
        tags: &[String],
        response_mode: Option<&str>,
        overall_timeout_ms: Option<u64>,
        poll_wait_ms: Option<u64>,
        organization_code: Option<&str>,
    ) -> Result<serde_json::Value> {
        let overall_timeout = Duration::from_millis(overall_timeout_ms.unwrap_or(30 * 60 * 1_000));
        let poll_wait_ms = poll_wait_ms.unwrap_or(15_000);

        let submit = self
            .chat_async(
                bot_id,
                message,
                from,
                session_id,
                tags,
                response_mode,
                Some("detached"),
                overall_timeout_ms,
                organization_code,
            )
            .await?;
        let run_id = submit.run_id.clone();
        let reported_session = submit.session_id.clone();
        debug!(
            run_id = %run_id,
            session_id = %reported_session,
            "chat_polling_detach: submitted"
        );

        let deadline = tokio::time::Instant::now() + overall_timeout;
        let mut since_version: u64 = 0;
        loop {
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            if remaining.is_zero() {
                warn!(
                    run_id = %run_id,
                    "chat_polling_detach: overall timeout before first ack"
                );
                return Err(anyhow!(
                    "chat_polling_detach: timed out after {} ms waiting for detach ack",
                    overall_timeout.as_millis()
                ));
            }
            let wait_ms = remaining.as_millis().min(poll_wait_ms as u128) as u64;
            let status = self
                .chat_run_status(&run_id, Some(since_version), Some(wait_ms))
                .await?;

            let state_str = status.state.as_str();
            match status.state {
                ChatRunState::Completed | ChatRunState::Running => {
                    return Ok(serde_json::json!({
                        "submitted": true,
                        "bot_uuid": status.bot_uuid,
                        "run_id": status.run_id,
                        "session_id": status.session_id,
                        "state": state_str,
                    }));
                }
                ChatRunState::Failed | ChatRunState::Cancelled => {
                    return Err(anyhow!(
                        "chat_polling_detach: run {} ended in state {}: {}",
                        run_id,
                        state_str,
                        status
                            .error_message
                            .as_deref()
                            .unwrap_or("<no error message>"),
                    ));
                }
                ChatRunState::Pending | ChatRunState::Submitted | ChatRunState::Unknown => {
                    if status.is_terminal() {
                        return Err(anyhow!(
                            "chat_polling_detach: run {} ended in state {}: {}",
                            run_id,
                            state_str,
                            status
                                .error_message
                                .as_deref()
                                .unwrap_or("<no error message>"),
                        ));
                    }
                }
            }
            since_version = status.version;
        }
    }

    /// Send a message to a group via BCS routing.
    ///
    /// In Agent mode: Routes to @mentioned bot or driver.
    /// In Fusion mode: Broadcasts to all participants.
    pub async fn group_chat(
        &self,
        group_id: &str,
        message: &str,
        from: Option<&str>,
    ) -> Result<serde_json::Value> {
        let url = format!("{}/groups/{}/chat", self.base_url, group_id);

        let payload = serde_json::json!({
            "message": message,
            "from": from,
        });

        debug!(
            group_id = %group_id,
            from = ?from,
            message_len = message.len(),
            "Sending group message via BCS"
        );

        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to send group message")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Group chat failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid group chat response")?;

        Ok(result)
    }

    // ========================================================================
    // Group Management
    // ========================================================================

    /// Create a group (legacy method with mode parameter).
    #[deprecated(since = "0.5.0", note = "Use create_group_no_mode instead")]
    pub async fn create_group(
        &self,
        mode: &str,
        driver_bot: &str,
        participants: Vec<ParticipantInfo>,
    ) -> Result<CreateGroupResponse> {
        let url = format!("{}/groups", self.base_url);

        let payload = CreateGroupRequest {
            mode: Some(mode.to_string()),
            driver_bot: Some(driver_bot.to_string()),
            participants,
            participant_bindings: Default::default(),
            target_actor_id: None,
            id: None,
            label: None,
            routing_policy: None,
            context: None,
            topic: None,
            group_kind: None,
            service_spec: None,
            group_strategy: None,
            originator: None,
            collaboration_definition_yaml: None,
            auto_start_on_service_invocation: None,
            visibility: None,
        };

        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to create group")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Create group failed ({}): {}", status, body));
        }

        let result: CreateGroupResponse =
            response.json().await.context("Invalid group response")?;

        Ok(result)
    }

    /// Create a group without specifying mode (recommended).
    pub async fn create_group_no_mode(
        &self,
        driver_bot: &str,
        participants: Vec<ParticipantInfo>,
    ) -> Result<CreateGroupResponse> {
        self.create_group_with_context(driver_bot, participants, None, None).await
    }

    /// Create a group with optional context and topic.
    pub async fn create_group_with_context(
        &self,
        driver_bot: &str,
        participants: Vec<ParticipantInfo>,
        context: Option<&str>,
        topic: Option<&str>,
    ) -> Result<CreateGroupResponse> {
        let url = format!("{}/groups", self.base_url);

        let payload = CreateGroupRequest {
            mode: None,
            driver_bot: Some(driver_bot.to_string()),
            participants,
            participant_bindings: Default::default(),
            target_actor_id: None,
            id: None,
            label: None,
            routing_policy: None,
            context: context.map(|s| s.to_string()),
            topic: topic.map(|s| s.to_string()),
            group_kind: None,
            service_spec: None,
            group_strategy: None,
            originator: None,
            collaboration_definition_yaml: None,
            auto_start_on_service_invocation: None,
            visibility: None,
        };

        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to create group")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Create group failed ({}): {}", status, body));
        }

        let result: CreateGroupResponse =
            response.json().await.context("Invalid group response")?;

        Ok(result)
    }

    /// Get a group by ID.
    pub async fn get_group(&self, group_id: &str) -> Result<serde_json::Value> {
        let url = format!("{}/groups/{}", self.base_url, group_id);

        let response = self
            .http_client
            .get(&url)
            .send()
            .await
            .context("Failed to get group")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Get group failed ({}): {}", status, body));
        }

        let group: serde_json::Value = response.json().await.context("Invalid group response")?;

        Ok(group)
    }

    /// List all groups.
    pub async fn list_groups(&self) -> Result<Vec<serde_json::Value>> {
        let url = format!("{}/groups", self.base_url);

        let response = self
            .http_client
            .get(&url)
            .send()
            .await
            .context("Failed to list groups")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("List groups failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response.json().await.context("Invalid groups response")?;

        let items = result["items"].as_array().cloned().unwrap_or_default();

        Ok(items)
    }

    /// List groups that include a specific bot.
    pub async fn list_bot_groups(&self, bot_uuid: &str) -> Result<Vec<serde_json::Value>> {
        let url = format!(
            "{}/bots/{}/groups",
            self.base_url,
            urlencoding::encode(bot_uuid)
        );

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to list bot groups")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("List bot groups failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response.json().await.context("Invalid bot groups response")?;

        Ok(result["items"].as_array().cloned().unwrap_or_default())
    }

    /// Add a member to a group.
    pub async fn add_group_member(
        &self,
        group_id: &str,
        bot_id: &str,
        role: Option<&str>,
    ) -> Result<serde_json::Value> {
        let url = format!("{}/groups/{}/members", self.base_url, group_id);

        let payload = serde_json::json!({
            "bot_uuid": bot_id,
            "role": role.unwrap_or("consultant")
        });

        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to add group member")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Add member failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid add member response")?;

        Ok(result)
    }

    /// Update group status (coordinator/originator only).
    ///
    /// Only the group's originator or driver_bot can update the status.
    /// Valid statuses: active, completed, closed, inactive.
    pub async fn update_group_status(
        &self,
        group_id: &str,
        status: &str,
        reason: Option<&str>,
    ) -> Result<serde_json::Value> {
        let url = format!("{}/groups/{}/status", self.base_url, group_id);

        let payload = serde_json::json!({
            "status": status,
            "reason": reason,
        });

        debug!(
            group_id = %group_id,
            status = %status,
            reason = ?reason,
            "Updating group status"
        );

        let response = self
            .add_auth(self.http_client.put(&url).json(&payload))
            .send()
            .await
            .context("Failed to update group status")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Update group status failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid group status response")?;

        Ok(result)
    }

    /// Terminate a group session.
    ///
    /// Only the group's driver bot can terminate the group.
    /// Sets status to "completed" and broadcasts termination to participants.
    pub async fn terminate_group(&self, group_id: &str) -> Result<serde_json::Value> {
        let url = format!("{}/groups/{}/terminate", self.base_url, group_id);

        debug!(
            group_id = %group_id,
            "Terminating group"
        );

        let response = self
            .add_auth(self.http_client.post(&url))
            .send()
            .await
            .context("Failed to terminate group")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Terminate group failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid terminate group response")?;

        Ok(result)
    }

    /// Fuse contexts from group participants.
    pub async fn fuse_context(
        &self,
        group_id: &str,
        question: &str,
        participants: Vec<String>,
    ) -> Result<FusionResponse> {
        self.fuse_context_with_focus(group_id, question, participants, None)
            .await
    }

    /// Fuse contexts from group participants with optional focus area.
    pub async fn fuse_context_with_focus(
        &self,
        group_id: &str,
        question: &str,
        participants: Vec<String>,
        focus: Option<&str>,
    ) -> Result<FusionResponse> {
        let url = format!("{}/groups/{}/fuse", self.base_url, group_id);

        let payload = FusionRequest {
            question: question.to_string(),
            participants,
            focus: focus.map(String::from),
            session_id: Some(group_id.to_string()),
            fusion_mode: None,
        };

        let response = self
            .http_client
            .post(&url)
            .json(&payload)
            .send()
            .await
            .context("Failed to fuse context")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Fuse context failed ({}): {}", status, body));
        }

        let result: FusionResponse = response.json().await.context("Invalid fusion response")?;

        Ok(result)
    }
}

// Friend DTOs moved to bcs-protocol (re-exported at top of this file).

// ============================================================================
// Friend API Methods on BcsClient
// ============================================================================

impl BcsClient {
    /// Send a friend request.
    pub async fn send_friend_request(
        &self,
        from_bot: Option<&str>,
        to_bot: &str,
    ) -> Result<FriendApiResponse> {
        let url = format!("{}/friends/request", self.base_url);
        let mut body = serde_json::json!({ "to_bot": to_bot });
        if let Some(from) = from_bot {
            body["from_bot"] = serde_json::json!(from);
        }

        let response = self
            .add_auth(self.http_client.post(&url))
            .json(&body)
            .send()
            .await
            .context("Failed to send friend request")?;
        let status = response.status();
        if !status.is_success() {
            let body: serde_json::Value = response.json().await.unwrap_or_default();
            let error_msg = body
                .get("error")
                .and_then(|e| e.as_str())
                .unwrap_or("unknown");
            anyhow::bail!(
                "Friend request failed (HTTP {}): {}",
                status.as_u16(),
                error_msg
            );
        }

        let result: FriendApiResponse = response
            .json()
            .await
            .context("Invalid friend request response")?;
        Ok(result)
    }

    /// List friend requests.
    pub async fn list_friend_requests(
        &self,
        bot_uuid: Option<&str>,
        direction: Option<&str>,
        status: Option<&str>,
    ) -> Result<FriendApiResponse> {
        let mut url = format!("{}/friends/requests", self.base_url);
        let mut params = Vec::new();
        if let Some(b) = bot_uuid {
            params.push(format!("bot_uuid={}", urlencoding::encode(b)));
        }
        if let Some(d) = direction {
            params.push(format!("direction={}", d));
        }
        if let Some(s) = status {
            params.push(format!("status={}", s));
        }
        if !params.is_empty() {
            url.push('?');
            url.push_str(&params.join("&"));
        }

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to list friend requests")?
            .error_for_status()
            .context("List friend requests failed")?;

        let result: FriendApiResponse = response
            .json()
            .await
            .context("Invalid list friend requests response")?;
        Ok(result)
    }

    /// Accept a friend request.
    pub async fn accept_friend_request(&self, request_id: &str) -> Result<FriendApiResponse> {
        let url = format!("{}/friends/requests/{}/accept", self.base_url, request_id);

        let response = self
            .add_auth(self.http_client.post(&url))
            .send()
            .await
            .context("Failed to accept friend request")?
            .error_for_status()
            .context("Accept friend request failed")?;

        let result: FriendApiResponse = response
            .json()
            .await
            .context("Invalid accept friend request response")?;
        Ok(result)
    }

    /// Reject a friend request.
    pub async fn reject_friend_request(&self, request_id: &str) -> Result<FriendApiResponse> {
        let url = format!("{}/friends/requests/{}/reject", self.base_url, request_id);

        let response = self
            .add_auth(self.http_client.post(&url))
            .send()
            .await
            .context("Failed to reject friend request")?
            .error_for_status()
            .context("Reject friend request failed")?;

        let result: FriendApiResponse = response
            .json()
            .await
            .context("Invalid reject friend request response")?;
        Ok(result)
    }

    /// List friends of a bot.
    pub async fn list_friends(&self, bot_id: &str) -> Result<FriendApiResponse> {
        let url = format!("{}/bots/{}/friends", self.base_url, bot_id);

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to list friends")?
            .error_for_status()
            .context("List friends failed")?;

        let result: FriendApiResponse = response
            .json()
            .await
            .context("Invalid list friends response")?;
        Ok(result)
    }

    /// Get bot visibility.
    pub async fn get_visibility(&self, bot_id: &str) -> Result<FriendApiResponse> {
        let url = format!("{}/bots/{}/visibility", self.base_url, bot_id);

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to get visibility")?
            .error_for_status()
            .context("Get visibility failed")?;

        let result: FriendApiResponse = response
            .json()
            .await
            .context("Invalid get visibility response")?;
        Ok(result)
    }

    /// Batch query bots by their UUIDs.
    /// Returns bot info including capabilities and visibility for each found bot.
    /// Bots that don't exist are silently excluded from the result.
    pub async fn query_bots(&self, bot_uuids: Vec<String>) -> Result<Vec<QueryBotEntry>> {
        let url = format!("{}/bots/query", self.base_url);
        let body = QueryBotsRequest { bot_uuids };

        let response = self
            .add_auth(self.http_client.post(&url))
            .json(&body)
            .send()
            .await
            .context("Failed to send query bots request")?;

        if !response.status().is_success() {
            let status = response.status();
            let text = response.text().await.unwrap_or_default();
            return Err(anyhow!("Query bots failed ({}): {}", status, text));
        }

        let entries: Vec<QueryBotEntry> = response
            .json()
            .await
            .context("Invalid query bots response")?;

        Ok(entries)
    }

    /// Set bot visibility.
    pub async fn set_visibility(
        &self,
        bot_id: &str,
        visibility: &str,
    ) -> Result<FriendApiResponse> {
        let url = format!("{}/bots/{}/visibility", self.base_url, bot_id);

        let response = self
            .add_auth(self.http_client.put(&url))
            .json(&SetVisibilityRequest {
                visibility: visibility.to_string(),
            })
            .send()
            .await
            .context("Failed to set visibility")?
            .error_for_status()
            .context("Set visibility failed")?;

        let result: FriendApiResponse = response
            .json()
            .await
            .context("Invalid set visibility response")?;
        Ok(result)
    }

    // ========================================================================
    // Sessions
    // ========================================================================

    /// Create or reactivate a session under a group.
    /// `POST /groups/{group_id}/sessions`
    pub async fn create_session(
        &self,
        group_id: &str,
        session_title: Option<&str>,
        session_kind: Option<&str>,
        input: Option<&serde_json::Value>,
        meta: Option<&serde_json::Value>,
    ) -> Result<serde_json::Value> {
        let url = format!("{}/groups/{}/sessions", self.base_url, group_id);
        let mut payload = serde_json::Map::new();
        if let Some(title) = session_title {
            payload.insert("session_title".to_string(), serde_json::json!(title));
        }
        if let Some(kind) = session_kind {
            payload.insert("session_kind".to_string(), serde_json::json!(kind));
        }
        if let Some(input) = input {
            payload.insert("input".to_string(), input.clone());
        }
        if let Some(meta) = meta {
            payload.insert("meta".to_string(), meta.clone());
        }

        let response = self
            .add_auth(self.http_client.post(&url).json(&serde_json::Value::Object(payload)))
            .send()
            .await
            .context("Failed to create session")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Create session failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid create session response")?;
        Ok(result)
    }

    /// List sessions under a group.
    /// `GET /groups/{group_id}/sessions`
    #[allow(clippy::too_many_arguments)]
    pub async fn list_sessions(
        &self,
        group_id: &str,
        status: Option<&str>,
        q: Option<&str>,
        participant: Option<&str>,
        offset: Option<u64>,
        limit: Option<u64>,
    ) -> Result<serde_json::Value> {
        let mut params: Vec<String> = Vec::new();
        if let Some(s) = status {
            params.push(format!("status={}", urlencoding::encode(s)));
        }
        if let Some(q) = q {
            params.push(format!("q={}", urlencoding::encode(q)));
        }
        if let Some(p) = participant {
            params.push(format!("participant={}", urlencoding::encode(p)));
        }
        if let Some(o) = offset {
            params.push(format!("offset={}", o));
        }
        if let Some(l) = limit {
            params.push(format!("limit={}", l));
        }

        let mut url = format!("{}/groups/{}/sessions", self.base_url, group_id);
        if !params.is_empty() {
            url.push('?');
            url.push_str(&params.join("&"));
        }

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to list sessions")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("List sessions failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid list sessions response")?;
        Ok(result)
    }

    /// Fetch a single session by id.
    /// `GET /sessions/{sid}`
    pub async fn get_session(&self, sid: &str) -> Result<serde_json::Value> {
        let url = format!("{}/sessions/{}", self.base_url, urlencoding::encode(sid));

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to get session")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Get session failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid get session response")?;
        Ok(result)
    }

    /// Send a chat message into a session.
    /// `POST /sessions/{sid}/chat`. Caller is resolved from the bearer token.
    pub async fn session_chat(
        &self,
        sid: &str,
        message: &str,
    ) -> Result<serde_json::Value> {
        let url = format!("{}/sessions/{}/chat", self.base_url, urlencoding::encode(sid));
        let payload = serde_json::json!({ "message": message });

        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to send session chat")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Session chat failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid session chat response")?;
        Ok(result)
    }

    /// Fetch message history for a session.
    /// `GET /sessions/{sid}/messages`
    pub async fn session_messages(
        &self,
        sid: &str,
        view_bot_id: Option<&str>,
        limit: Option<u64>,
        before: Option<u64>,
    ) -> Result<serde_json::Value> {
        let mut params: Vec<String> = Vec::new();
        if let Some(v) = view_bot_id {
            params.push(format!("view_bot_id={}", urlencoding::encode(v)));
        }
        if let Some(l) = limit {
            params.push(format!("limit={}", l));
        }
        if let Some(b) = before {
            params.push(format!("before={}", b));
        }

        let mut url = format!("{}/sessions/{}/messages", self.base_url, urlencoding::encode(sid));
        if !params.is_empty() {
            url.push('?');
            url.push_str(&params.join("&"));
        }

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to get session messages")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Get session messages failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid session messages response")?;
        Ok(result)
    }

    /// Update session title.
    /// `PATCH /sessions/{sid}`
    pub async fn patch_session(
        &self,
        sid: &str,
        title: &str,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}",
            self.base_url,
            urlencoding::encode(sid)
        );
        let payload = serde_json::json!({ "session_title": title });

        let response = self
            .add_auth(self.http_client.patch(&url).json(&payload))
            .send()
            .await
            .context("Failed to patch session")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Patch session failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid patch session response")?;
        Ok(result)
    }

    /// Complete a running chat session (driver-only).
    /// `POST /sessions/{sid}/complete`
    pub async fn complete_session(
        &self,
        sid: &str,
        output: Option<&serde_json::Value>,
        error: Option<&str>,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/complete",
            self.base_url,
            urlencoding::encode(sid)
        );
        let mut payload = serde_json::Map::new();
        if let Some(output) = output {
            payload.insert("output".to_string(), output.clone());
        }
        if let Some(error) = error {
            payload.insert("error".to_string(), serde_json::json!(error));
        }

        let response = self
            .add_auth(
                self.http_client
                    .post(&url)
                    .json(&serde_json::Value::Object(payload)),
            )
            .send()
            .await
            .context("Failed to complete session")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Complete session failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid complete session response")?;
        Ok(result)
    }

    /// Add a participant to a session.
    /// `POST /sessions/{sid}/members`
    pub async fn add_session_member(
        &self,
        sid: &str,
        bot_uuid: &str,
        role: Option<&str>,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/members",
            self.base_url,
            urlencoding::encode(sid)
        );
        let mut payload = serde_json::json!({ "bot_uuid": bot_uuid });
        if let Some(role) = role {
            payload["role"] = serde_json::json!(role);
        }

        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to add session member")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Add session member failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid add session member response")?;
        Ok(result)
    }

    /// Remove a participant from a session.
    /// `DELETE /sessions/{sid}/members/{bot_uuid}`
    pub async fn remove_session_member(
        &self,
        sid: &str,
        bot_uuid: &str,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/members/{}",
            self.base_url,
            urlencoding::encode(sid),
            urlencoding::encode(bot_uuid)
        );

        let response = self
            .add_auth(self.http_client.delete(&url))
            .send()
            .await
            .context("Failed to remove session member")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Remove session member failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid remove session member response")?;
        Ok(result)
    }

    /// Update a participant's mode in a session.
    /// `PATCH /sessions/{sid}/members/{bot_uuid}`
    pub async fn set_session_member_mode(
        &self,
        sid: &str,
        bot_uuid: &str,
        mode: &str,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/members/{}",
            self.base_url,
            urlencoding::encode(sid),
            urlencoding::encode(bot_uuid)
        );
        let payload = serde_json::json!({ "mode": mode });

        let response = self
            .add_auth(self.http_client.patch(&url).json(&payload))
            .send()
            .await
            .context("Failed to set session member mode")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!(
                "Set session member mode failed ({}): {}",
                status,
                body
            ));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid set member mode response")?;
        Ok(result)
    }

    /// Create an invite link for a session.
    /// `POST /sessions/{sid}/invite-link`
    pub async fn create_session_invite_link(
        &self,
        sid: &str,
        ttl_seconds: Option<u64>,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/invite-link",
            self.base_url,
            urlencoding::encode(sid)
        );
        let payload = if let Some(ttl) = ttl_seconds {
            serde_json::json!({ "ttl_seconds": ttl })
        } else {
            serde_json::json!({})
        };

        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to create session invite link")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!(
                "Create session invite link failed ({}): {}",
                status,
                body
            ));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid create invite link response")?;
        Ok(result)
    }

    // ========================================================================
    // Channel Bindings
    // ========================================================================

    /// Create a channel binding.
    /// `POST /channels/bindings`
    pub async fn create_channel_binding(
        &self,
        payload: &serde_json::Value,
    ) -> Result<serde_json::Value> {
        let url = format!("{}/channels/bindings", self.base_url);
        let response = self
            .add_auth(self.http_client.post(&url).json(payload))
            .send()
            .await
            .context("Failed to create channel binding")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Create channel binding failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid create channel binding response")?;
        Ok(result)
    }

    /// List channel bindings.
    /// `GET /channels/bindings`
    pub async fn list_channel_bindings(&self) -> Result<serde_json::Value> {
        let url = format!("{}/channels/bindings", self.base_url);
        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to list channel bindings")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("List channel bindings failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid list channel bindings response")?;
        Ok(result)
    }

    /// Delete a channel binding.
    /// `DELETE /channels/bindings/{id}`
    pub async fn delete_channel_binding(&self, id: &str) -> Result<serde_json::Value> {
        let url = format!(
            "{}/channels/bindings/{}",
            self.base_url,
            urlencoding::encode(id)
        );
        let response = self
            .add_auth(self.http_client.delete(&url))
            .send()
            .await
            .context("Failed to delete channel binding")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Delete channel binding failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid delete channel binding response")?;
        Ok(result)
    }

    // ========================================================================
    // Service Invocation
    // ========================================================================

    /// Kick off (or reactivate) a service_invocation session under a group.
    /// `POST /services/{group_id}/sessions`
    ///
    /// CLI callers normally send a bot token. `X-BCS-Service-Key` is still
    /// supported by the lower-level client for non-CLI external callers. The
    /// server returns 202 Accepted on success; any 2xx response carries the session JSON
    /// (`service_session_to_json` in `routes/services.rs`).
    pub async fn service_invoke(
        &self,
        group_id: &str,
        input: Option<&serde_json::Value>,
        session_id: Option<&str>,
        caller_id: Option<&str>,
        session_title: Option<&str>,
        meta: Option<&serde_json::Value>,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/services/{}/sessions",
            self.base_url,
            urlencoding::encode(group_id)
        );
        let mut payload = serde_json::Map::new();
        if let Some(sid) = session_id {
            payload.insert("session_id".to_string(), serde_json::json!(sid));
        }
        if let Some(cid) = caller_id {
            payload.insert("caller_id".to_string(), serde_json::json!(cid));
        }
        if let Some(input) = input {
            payload.insert("input".to_string(), input.clone());
        }
        if let Some(title) = session_title {
            payload.insert("session_title".to_string(), serde_json::json!(title));
        }
        if let Some(meta) = meta {
            payload.insert("meta".to_string(), meta.clone());
        }

        let response = self
            .add_auth(
                self.http_client
                    .post(&url)
                    .json(&serde_json::Value::Object(payload)),
            )
            .send()
            .await
            .context("Failed to send service invocation")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Service invoke failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid service invoke response")?;
        Ok(result)
    }

    /// Poll a service_invocation session once.
    /// `GET /services/{group_id}/sessions/{session_id}`
    pub async fn service_session_status(
        &self,
        group_id: &str,
        session_id: &str,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/services/{}/sessions/{}",
            self.base_url,
            urlencoding::encode(group_id),
            urlencoding::encode(session_id)
        );

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to fetch service session status")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Service session status failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid service session response")?;
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[allow(unsafe_code)]
    fn test_default_bcs_url() {
        // SAFETY: This is a test, we're removing an env var in a controlled manner
        unsafe {
            std::env::remove_var("MOLTIS_BCS_URL");
        }
        let client = BcsClient::from_env();
        assert_eq!(client.base_url(), DEFAULT_BCS_URL);
    }

    #[test]
    fn test_custom_bcs_url() {
        let client = BcsClient::new("http://custom:9000");
        assert_eq!(client.base_url(), "http://custom:9000");
    }

    #[test]
    fn test_with_token_and_oauth() {
        let mut headers = HashMap::new();
        headers.insert("X-Auth-Token".to_string(), "oauth-token-123".to_string());
        headers.insert("Cookie".to_string(), "session=abc".to_string());

        let client =
            BcsClient::with_token_and_oauth("http://localhost:21000", "bot-token", headers);
        assert_eq!(client.token(), Some("bot-token"));
        assert!(client.oauth_headers.is_some());
        let oauth = client.oauth_headers.as_ref().unwrap();
        assert_eq!(oauth.get("X-Auth-Token").unwrap(), "oauth-token-123");
    }

    #[test]
    fn test_set_oauth_headers() {
        let mut client = BcsClient::with_token("http://localhost:21000", "bot-token");
        assert!(client.oauth_headers.is_none());

        let mut headers = HashMap::new();
        headers.insert("X-Auth-Token".to_string(), "oauth-val".to_string());
        client.set_oauth_headers(headers);
        assert!(client.oauth_headers.is_some());
    }

    #[test]
    fn test_no_oauth_headers_by_default() {
        let client = BcsClient::new("http://localhost:21000");
        assert!(client.oauth_headers.is_none());

        let client = BcsClient::with_token("http://localhost:21000", "token");
        assert!(client.oauth_headers.is_none());

        let client = BcsClient::with_token_and_cookie("http://localhost:21000", "token", "cookie");
        assert!(client.oauth_headers.is_none());
    }

    #[test]
    fn test_oauth_headers_layout() {
        // Simulate SDK headers: Authorization, User-Agent, starpoint-data2
        let mut oauth_headers = HashMap::new();
        oauth_headers.insert(
            "Authorization".to_string(),
            "Bearer oauth-token".to_string(),
        );
        oauth_headers.insert(
            "User-Agent".to_string(),
            "agentClientSdk process/bcs-cli".to_string(),
        );
        oauth_headers.insert(
            "starpoint-data2".to_string(),
            "device-token-xyz".to_string(),
        );

        let client = BcsClient::with_token_and_oauth(
            "http://localhost:21000",
            "bot-token-abc",
            oauth_headers,
        );

        let req = client.http_client.get("http://localhost:21000/test");
        let req = client.add_headers(req);
        let built = req.build().unwrap();

        // OAuth Authorization should be the primary (for Spanner gateway)
        let auth = built
            .headers()
            .get("authorization")
            .unwrap()
            .to_str()
            .unwrap();
        assert_eq!(auth, "Bearer oauth-token");

        // Bot token should be in X-BCS-Bot-Token (for BCS server)
        let bot_token = built
            .headers()
            .get("x-bcs-bot-token")
            .unwrap()
            .to_str()
            .unwrap();
        assert_eq!(bot_token, "bot-token-abc");

        // starpoint-data2 should be passed through
        let device = built
            .headers()
            .get("starpoint-data2")
            .unwrap()
            .to_str()
            .unwrap();
        assert_eq!(device, "device-token-xyz");

        // User-Agent should NOT be the SDK's custom one
        let ua = built.headers().get("user-agent");
        if let Some(ua_val) = ua {
            assert!(!ua_val.to_str().unwrap().contains("agentClientSdk"));
        }
    }

    #[test]
    fn test_no_oauth_uses_standard_authorization() {
        // Without OAuth, bot token goes to Authorization as usual
        let client = BcsClient::with_token("http://localhost:21000", "bot-token-abc");

        let req = client.http_client.get("http://localhost:21000/test");
        let req = client.add_headers(req);
        let built = req.build().unwrap();

        let auth = built
            .headers()
            .get("authorization")
            .unwrap()
            .to_str()
            .unwrap();
        assert_eq!(auth, "Bearer bot-token-abc");
        assert!(built.headers().get("x-bcs-bot-token").is_none());
    }

    #[test]
    fn test_chat_headers_include_chat_version() {
        let client = BcsClient::new("http://localhost:21000");
        let request = client
            .add_chat_headers(client.http_client.get("http://localhost:21000/chat/runs/run-1"))
            .build()
            .unwrap();

        assert_eq!(
            request
                .headers()
                .get(BCS_CHAT_VERSION_HEADER)
                .and_then(|value| value.to_str().ok()),
            Some(BCS_CHAT_VERSION)
        );
    }

    #[test]
    fn test_chat_version_header_advertises_version_2() {
        assert_eq!(BCS_CHAT_VERSION, "2");
    }

    #[test]
    fn test_chat_run_status_deserializes_submitted_state() {
        let status: ChatRunStatusResponse = serde_json::from_value(serde_json::json!({
            "run_id": "run-1",
            "bot_uuid": "bot-target",
            "from_bot_id": "bot-source",
            "session_id": "session-1",
            "state": "submitted",
            "response": {"content": ""},
            "created_at_ms": 1,
            "updated_at_ms": 2,
            "expires_at_ms": 3,
            "version": 2,
            "is_terminal": false
        }))
        .unwrap();

        assert!(matches!(status.state, ChatRunState::Submitted));
        assert!(!status.is_terminal());
    }

    #[test]
    fn test_chat_run_status_deserializes_unknown_future_state() {
        let status: ChatRunStatusResponse = serde_json::from_value(serde_json::json!({
            "run_id": "run-1",
            "bot_uuid": "bot-target",
            "from_bot_id": "bot-source",
            "session_id": "session-1",
            "state": "provider_accepted",
            "response": {"content": ""},
            "created_at_ms": 1,
            "updated_at_ms": 2,
            "expires_at_ms": 3,
            "version": 2,
            "is_terminal": false
        }))
        .unwrap();

        assert!(matches!(status.state, ChatRunState::Unknown));
        assert!(!status.is_terminal());
    }

    #[tokio::test]
    async fn test_discover_bots_extended_includes_organization_scope_query() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        use std::sync::{Arc, Mutex};

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let request_line = Arc::new(Mutex::new(String::new()));
        let server_request_line = request_line.clone();

        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buf = [0_u8; 4096];
            let size = stream.read(&mut buf).unwrap_or(0);
            let request = String::from_utf8_lossy(&buf[..size]);
            let first_line = request.lines().next().unwrap_or_default().to_string();
            *server_request_line.lock().unwrap() = first_line;

            let body = serde_json::json!({"bots": [], "count": 0}).to_string();
            let response = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            stream.write_all(response.as_bytes()).unwrap();
            stream.flush().unwrap();
        });

        let client = BcsClient::new(format!("http://{}", addr));
        let result = client
            .discover_bots_extended(
                None,
                None,
                None,
                Some("promo 2026"),
                Some("traffic/analyst"),
            )
            .await
            .unwrap();

        server.join().unwrap();
        assert_eq!(result.count, 0);
        let line = request_line.lock().unwrap();
        assert!(line.contains("GET /bots/discover?"), "{line}");
        assert!(line.contains("organization_code=promo%202026"), "{line}");
        assert!(line.contains("role=traffic%2Fanalyst"), "{line}");
    }

    #[tokio::test]
    async fn test_chat_polling_detach_waits_through_submitted_until_running() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        use std::sync::{
            Arc,
            atomic::{AtomicBool, AtomicUsize, Ordering},
        };
        use std::time::{Duration as StdDuration, Instant};

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        // Keep the listener non-blocking so the worker can check the shutdown
        // flag between accepts; accepted streams are forced back to blocking
        // below so the request line can be read reliably.
        listener.set_nonblocking(true).unwrap();
        let addr = listener.local_addr().unwrap();
        let get_count = Arc::new(AtomicUsize::new(0));
        let served_get_count = get_count.clone();
        let shutdown = Arc::new(AtomicBool::new(false));
        let server_shutdown = shutdown.clone();

        let server = std::thread::spawn(move || {
            // Generous backstop: only meant to let the worker exit if the
            // client panics before signalling shutdown. A healthy
            // POST -> GET -> GET flow completes in milliseconds; this never
            // races a passing run.
            let deadline = Instant::now() + StdDuration::from_secs(30);
            while Instant::now() < deadline && !server_shutdown.load(Ordering::SeqCst) {
                let Ok((mut stream, _)) = listener.accept() else {
                    std::thread::sleep(StdDuration::from_millis(5));
                    continue;
                };
                // Force blocking reads on the accepted stream so a single
                // `read` cannot return 0/partial bytes before the request
                // line arrives. Read until at least the first line is whole.
                stream.set_nonblocking(false).ok();
                stream
                    .set_read_timeout(Some(StdDuration::from_secs(2)))
                    .ok();
                let mut buf = [0_u8; 4096];
                let mut size = 0;
                let request = loop {
                    match stream.read(&mut buf[size..]) {
                        Ok(0) | Err(_) => break String::from_utf8_lossy(&buf[..size]).into_owned(),
                        Ok(n) => {
                            size += n;
                            if buf[..size].contains(&b'\n') || size == buf.len() {
                                break String::from_utf8_lossy(&buf[..size]).into_owned();
                            }
                        }
                    }
                };
                let first_line = request.lines().next().unwrap_or_default();
                let body = if first_line.starts_with("POST ")
                    && first_line.contains("/bots/bot-target/chat-async")
                {
                    serde_json::json!({
                        "run_id": "run-1",
                        "bot_uuid": "bot-target",
                        "session_id": "session-1",
                        "status": "submitted",
                        "expires_at_ms": 9_999_999_u64
                    })
                } else if first_line.starts_with("GET ") && first_line.contains("/chat/runs/run-1") {
                    let count = served_get_count.fetch_add(1, Ordering::SeqCst) + 1;
                    let state = if count == 1 { "submitted" } else { "running" };
                    serde_json::json!({
                        "run_id": "run-1",
                        "bot_uuid": "bot-target",
                        "from_bot_id": "bot-source",
                        "session_id": "session-1",
                        "state": state,
                        "response": {"content": ""},
                        "created_at_ms": 1_u64,
                        "updated_at_ms": count as u64 + 1,
                        "expires_at_ms": 9_999_999_u64,
                        "version": count as u64 + 1,
                        "content_truncated": false,
                        "is_terminal": false
                    })
                } else {
                    serde_json::json!({"error": "unexpected request", "line": first_line})
                };
                let body = body.to_string();
                let response = format!(
                    "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                    body.len(),
                    body
                );
                // Tolerate write failures instead of panicking the worker; a
                // failed write just closes the connection and the client
                // surfaces the error, which is the intended failure mode.
                let _ = stream.write_all(response.as_bytes());
                let _ = stream.flush();
            }
        });

        let client = BcsClient::new(format!("http://{}", addr));
        let result = client
            .chat_polling_detach(
                "bot-target",
                "hello",
                None,
                None,
                &[],
                None,
                Some(2_000),
                Some(10),
                None,
            )
            .await;
        // Signal the worker to exit whether or not the call succeeded so the
        // join below never waits on the backstop deadline.
        shutdown.store(true, Ordering::SeqCst);
        let result = result.unwrap();

        server.join().unwrap();
        assert_eq!(get_count.load(Ordering::SeqCst), 2);
        assert_eq!(result["state"], "running");
    }

    #[test]
    fn test_non_chat_headers_omit_chat_version() {
        let client = BcsClient::new("http://localhost:21000");
        let request = client
            .add_headers(client.http_client.get("http://localhost:21000/health"))
            .build()
            .unwrap();

        assert!(request.headers().get(BCS_CHAT_VERSION_HEADER).is_none());
    }

    #[test]
    fn test_effective_chat_timeout_ms_defaults_and_clamps() {
        assert_eq!(BcsClient::effective_chat_timeout_ms(None), 300_000);
        assert_eq!(BcsClient::effective_chat_timeout_ms(Some(12_345)), 12_345);
        assert_eq!(BcsClient::effective_chat_timeout_ms(Some(500_000)), 300_000);
    }

    #[test]
    fn test_chat_request_timeout_adds_buffer() {
        assert_eq!(
            BcsClient::chat_request_timeout(Some(12_345)),
            Duration::from_millis(17_345)
        );
        assert_eq!(
            BcsClient::chat_request_timeout(Some(500_000)),
            Duration::from_millis(305_000)
        );
    }

    #[test]
    fn test_chat_payload_includes_timeout_ms() {
        let payload = BcsClient::chat_payload("hello", Some("bot_a"), 12_345, None, &[]);

        assert_eq!(payload["message"], serde_json::json!("hello"));
        assert_eq!(payload["from"], serde_json::json!("bot_a"));
        assert_eq!(payload["timeout_ms"], serde_json::json!(12_345));
        assert!(payload.get("session_id").is_none());
        assert!(payload.get("tags").is_none());
    }

    #[test]
    fn test_chat_payload_includes_session_id_when_present() {
        let payload = BcsClient::chat_payload("hi", None, 9_000, Some("sess-1"), &[]);
        assert_eq!(payload["session_id"], serde_json::json!("sess-1"));
    }

    #[test]
    fn test_chat_payload_includes_tags_when_present() {
        let tags = vec![" tag1 ".to_string(), "".to_string(), "tag2".to_string()];
        let payload = BcsClient::chat_payload("hi", None, 9_000, None, &tags);
        assert_eq!(payload["tags"], serde_json::json!(["tag1", "tag2"]));
    }

    #[test]
    fn test_chat_async_payload_includes_caller_wait_mode_when_present() {
        let payload = BcsClient::chat_async_payload(
            "hi",
            None,
            None,
            &[],
            Some("after-last-tool-call"),
            Some("detached"),
            Some(60_000),
            None,
        );

        assert_eq!(payload["caller_wait_mode"], serde_json::json!("detached"));
    }

    #[test]
    fn test_chat_async_payload_omits_blank_caller_wait_mode() {
        let payload = BcsClient::chat_async_payload(
            "hi",
            None,
            None,
            &[],
            None,
            Some("  "),
            None,
            None,
        );

        assert!(payload.get("caller_wait_mode").is_none());
    }

    #[test]
    fn test_chat_async_payload_includes_organization_code_when_present() {
        let payload = BcsClient::chat_async_payload(
            "hi",
            None,
            None,
            &[],
            None,
            None,
            None,
            Some(" promo-2026 "),
        );

        assert_eq!(payload["organization_code"], serde_json::json!("promo-2026"));
    }

    #[test]
    fn test_chat_async_payload_omits_blank_organization_code() {
        let payload = BcsClient::chat_async_payload(
            "hi",
            None,
            None,
            &[],
            None,
            None,
            None,
            Some("  "),
        );

        assert!(payload.get("organization_code").is_none());
    }

    #[test]
    fn test_chat_request_builder_applies_request_timeout() {
        let client = BcsClient::new("http://localhost:21000");
        let payload = BcsClient::chat_payload("hello", Some("bot_a"), 12_345, None, &[]);
        let request = client
            .chat_request_builder(
                "http://localhost:21000/bots/bot-123/chat",
                &payload,
                Some(12_345),
            )
            .build()
            .unwrap();

        assert_eq!(
            request.timeout().copied(),
            Some(Duration::from_millis(17_345))
        );
    }
}
